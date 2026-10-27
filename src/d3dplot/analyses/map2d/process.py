# SPDX-License-Identifier: GPL-3.0-or-later
"""Process map2d quantities into the guarded NetCDF data cache."""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC
from importlib import import_module
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from d3dplot import __version__
from d3dplot.execution.cache import CacheFingerprintReadError, read_cache_fingerprints
from d3dplot.execution.runner import TaskDiagnostic, TaskOutputReport
from d3dplot.execution.tasks import ProcessTask
from d3dplot.simulation.discover import Simulation
from d3dplot.simulation.inspection import InspectionResult
from d3dplot.simulation.map_reader import MapReader
from d3dplot.simulation.subrun import SubRun
from d3dplot.simulation.time_axis import TimeAxis, TimeEntry
from d3dplot.store import (
    CACHE_SCHEMA_VERSION,
    OutputLayout,
    OutputWriteGuard,
    atomic_path,
    canonical_json,
    fingerprint_artifact,
    fingerprint_model_output,
    fingerprint_small_input,
)
from d3dplot.validate.report import ValidationIssue
from d3dplot.variables.context import QuantityContext, QuantityRequest
from d3dplot.variables.engine import QuantityEngine
from d3dplot.variables.geometry import GridGeometry
from d3dplot.variables.registry import (
    UnknownVariableError,
    VariableLocation,
    get_variable_spec,
)

_Array = Any
np: Any = import_module("numpy")
_logger = logging.getLogger(__name__)

_MAP2D_ALGORITHM_VERSION = "1"
_CF_TIME_UNITS = "seconds since 1970-01-01 00:00:00 +00:00"


class Map2DProcessError(RuntimeError):
    """A map2d output could not be built with a stable validation issue."""

    def __init__(self, issue: ValidationIssue) -> None:
        self.issue = issue
        super().__init__(issue.message)


@dataclass(frozen=True, slots=True)
class Map2DProcessResult(TaskOutputReport):
    """Files and user-visible quantity-engine choices produced by one task."""

    output_paths: tuple[Path, ...]
    choices: tuple[str, ...]
    issues: tuple[ValidationIssue, ...]


class Map2DTaskProcessor:
    """Spawn-safe worker that reuses inspected inputs across process tasks."""

    def __init__(
        self,
        simulation: Simulation,
        simulation_label: str,
        inspection: InspectionResult,
        layout: OutputLayout,
        guard: OutputWriteGuard,
        *,
        analysis_keys: Sequence[str],
        dtype: Literal["float32", "float64"] = "float32",
        on_corrupt_output: Literal["mask", "error", "skip"] = "mask",
    ) -> None:
        if not simulation_label or simulation_label != simulation_label.strip():
            raise ValueError("simulation_label must be non-empty and trimmed.")
        if not analysis_keys or any(
            not key or key != key.strip() for key in analysis_keys
        ):
            raise ValueError("At least one trimmed map2d analysis key is required.")
        if len(set(analysis_keys)) != len(analysis_keys):
            raise ValueError("Map2d analysis keys must be unique.")
        if dtype not in {"float32", "float64"}:
            raise ValueError(f"Unsupported output dtype: {dtype!r}.")
        if on_corrupt_output not in {"mask", "error", "skip"}:
            raise ValueError(
                f"Unsupported corrupt-output policy: {on_corrupt_output!r}."
            )
        if layout.root != guard.root:
            raise ValueError("Output layout and write guard must use the same root.")

        self._simulation = simulation
        self._simulation_label = simulation_label
        self._inspection = inspection
        self._layout = layout
        self._guard = guard
        self._analysis_keys = frozenset(analysis_keys)
        self._dtype = dtype
        self._on_corrupt_output = on_corrupt_output
        self._reader: MapReader | None = None
        self._engine: QuantityEngine | None = None
        self._time_axis: TimeAxis | None = None
        self._geometry: dict[int, GridGeometry] = {}
        self._input_fingerprints: dict[int, tuple[str, ...]] = {}

    def __call__(self, task: ProcessTask) -> Map2DProcessResult:
        """Compute only map2d work assigned to this process task."""
        if task.simulation_label != self._simulation_label:
            raise ValueError(
                f"Task simulation {task.simulation_label!r} does not match "
                f"{self._simulation_label!r}."
            )
        subruns = {subrun.index: subrun for subrun in self._simulation.subruns}
        if task.subrun_index not in subruns:
            raise ValueError(f"Unknown sub-run index {task.subrun_index}.")

        selected_times = tuple(
            (
                time_work,
                tuple(
                    work
                    for work in time_work.analyses
                    if work.analysis_key in self._analysis_keys
                ),
            )
            for time_work in task.times
        )
        selected_times = tuple(item for item in selected_times if item[1])
        if not selected_times:
            return Map2DProcessResult(
                diagnostics=(),
                output_paths=(),
                choices=(),
                issues=(),
            )

        self._ensure_initialized()
        reader = self._reader
        engine = self._engine
        time_axis = self._time_axis
        if reader is None or engine is None or time_axis is None:
            raise RuntimeError("Map2d task processor failed to initialize.")

        subrun = subruns[task.subrun_index]
        plan_index = reader.plan_index(task.subrun_index)
        if plan_index >= len(self._inspection.grid_paths):
            raise Map2DProcessError(
                ValidationIssue(
                    code="V-OUT-020",
                    level="error",
                    location=str(self._inspection.merge_plan_path),
                    message=f"No inspected grid exists for merge plan {plan_index}.",
                    hint="Run inspection again to rebuild the grid cache.",
                )
            )
        grid_path = self._inspection.grid_paths[plan_index]
        grid_geometry = self._grid_geometry(plan_index, grid_path)
        input_fingerprints = self._fingerprints(subrun, grid_path)

        output_paths: list[Path] = []
        choices: list[str] = []
        issues: list[ValidationIssue] = []
        with reader.session(task.subrun_index) as map_session:
            for time_work, analysis_work in selected_times:
                entry = time_work.entry
                requests = tuple(
                    dict.fromkeys(
                        request for work in analysis_work for request in work.quantities
                    )
                )
                previous = _previous_entry(time_axis.entries, entry)
                previous_water_level = None
                needs_rate = any(_is_water_level_rate(request) for request in requests)
                if (
                    needs_rate
                    and previous is not None
                    and not map_session.has_variable(get_variable_spec("mesh2d_s0"))
                ):
                    previous_context = replace(
                        QuantityContext.from_model_input(
                            subrun.model_input,
                            grid_geometry=grid_geometry,
                        ),
                        current_time=previous.time,
                    )
                    previous_session = engine.at_time(
                        map_session,
                        previous,
                        previous_context,
                    )
                    previous_water_level = previous_session.get("water_level")
                    issues.extend(previous_session.issues)
                    choices.extend(previous_session.choices)

                context = replace(
                    QuantityContext.from_model_input(
                        subrun.model_input,
                        grid_geometry=grid_geometry,
                    ),
                    current_time=entry.time,
                    previous_time=previous.time if previous is not None else None,
                    previous_water_level=previous_water_level,
                )
                quantity_session = engine.at_time(map_session, entry, context)
                for request in requests:
                    spec = quantity_session.resolve_spec(request)
                    if spec.location is not VariableLocation.FACE:
                        raise Map2DProcessError(
                            ValidationIssue(
                                code="V-VAR-004",
                                level="error",
                                location=request.requested_name,
                                message=(
                                    f"Map2d quantity {request.requested_name!r} is "
                                    f"{spec.location.value}-located; face values are "
                                    "required."
                                ),
                                hint=(
                                    "Request a face-located quantity or use an "
                                    "analysis designed for this grid location."
                                ),
                            )
                        )
                    values = quantity_session.get(request)
                    values = _face_values(values, grid_geometry.n_faces, request)
                    selection = _selection_label(spec.name, request)
                    output_path = map2d_output_path(self._layout, request, entry)
                    parameters = _fingerprint_parameters(
                        spec.name,
                        spec.version,
                        request,
                        selection,
                        self._dtype,
                        self._on_corrupt_output,
                        entry,
                        plan_index,
                    )
                    fingerprint = fingerprint_artifact(
                        kind="map2d_data",
                        schema_version=CACHE_SCHEMA_VERSION,
                        tool_version=__version__,
                        algorithm_version=_MAP2D_ALGORITHM_VERSION,
                        parameters=parameters,
                        inputs=input_fingerprints,
                        time=entry.time.astimezone(UTC).isoformat(),
                    )
                    _write_data_file(
                        output_path,
                        self._guard,
                        values,
                        dtype=self._dtype,
                        units=spec.units,
                        long_name=spec.name,
                        fingerprint=fingerprint,
                        parameters=parameters,
                        entry=entry,
                        plan_index=plan_index,
                        grid_path=grid_path,
                        variable=spec.name,
                        selection=selection,
                    )
                    output_paths.append(output_path)

                choices.extend(quantity_session.choices)
                issues.extend(quantity_session.issues)

        for choice in choices:
            _logger.info(
                "Map2d %s %s: %s",
                task.simulation_label,
                task.key,
                choice,
            )
        for issue in issues:
            _logger.log(
                (
                    logging.ERROR
                    if issue.level == "error"
                    else logging.WARNING
                    if issue.level == "warning"
                    else logging.INFO
                ),
                "%s: %s",
                issue.code,
                issue.message,
            )
        task_diagnostics = [
            TaskDiagnostic("info", f"Map2d {task.key}: {choice}") for choice in choices
        ]
        task_diagnostics.extend(
            TaskDiagnostic(
                issue.level if issue.level in {"error", "warning"} else "info",
                f"{issue.code}: {issue.message}",
            )
            for issue in issues
        )
        task_diagnostics.extend(
            TaskDiagnostic("info", f"Created map2d cache file: {path}")
            for path in output_paths
        )
        return Map2DProcessResult(
            diagnostics=tuple(task_diagnostics),
            output_paths=tuple(output_paths),
            choices=tuple(choices),
            issues=tuple(issues),
        )

    def _ensure_initialized(self) -> None:
        if self._reader is None:
            self._reader = MapReader(
                self._simulation,
                self._inspection.merge_plan_path,
                on_corrupt_output=self._on_corrupt_output,
            )
        if self._engine is None:
            self._engine = QuantityEngine()
        if self._time_axis is None:
            self._time_axis = TimeAxis.from_cache(self._inspection.times_path)

    def _grid_geometry(self, plan_index: int, path: Path) -> GridGeometry:
        geometry = self._geometry.get(plan_index)
        if geometry is None:
            geometry = GridGeometry.from_path(path)
            self._geometry[plan_index] = geometry
        return geometry

    def _fingerprints(self, subrun: SubRun, grid_path: Path) -> tuple[str, ...]:
        cached = self._input_fingerprints.get(subrun.index)
        if cached is not None:
            return cached

        fingerprints = [fingerprint_model_output(path) for path in subrun.map_files]
        model_input_paths = (
            subrun.mdu.mdu_path,
            subrun.mdu.mor_file,
            subrun.mdu.sed_file,
        )
        fingerprints.extend(
            fingerprint_small_input(path)
            for path in model_input_paths
            if path is not None
        )
        try:
            cache_fingerprints = read_cache_fingerprints(
                (
                    self._inspection.merge_plan_path,
                    grid_path,
                    self._inspection.times_path,
                )
            )
        except CacheFingerprintReadError as error:
            raise Map2DProcessError(
                ValidationIssue(
                    code="V-OUT-020",
                    level="error",
                    location=str(grid_path),
                    message=f"Could not fingerprint inspected cache inputs: {error}",
                    hint="Run inspection again to rebuild the grid and merge plan.",
                )
            ) from error
        for path in (
            self._inspection.merge_plan_path,
            grid_path,
            self._inspection.times_path,
        ):
            value = cache_fingerprints[path.resolve(strict=False)]
            if value is None:
                raise Map2DProcessError(
                    ValidationIssue(
                        code="V-OUT-020",
                        level="error",
                        location=str(path),
                        message="An inspected cache input has no fingerprint.",
                        hint="Run inspection again to rebuild the affected cache.",
                    )
                )
            fingerprints.append(value)

        result = tuple(fingerprints)
        self._input_fingerprints[subrun.index] = result
        return result


def map2d_output_path(
    layout: OutputLayout,
    request: QuantityRequest,
    entry: TimeEntry,
) -> Path:
    """Return the stable data-cache path for one quantity selection and time."""
    variable = quote(request.requested_name.casefold(), safe="-_.")
    selection = _selection_label(_request_variable_name(request), request)
    timestamp = entry.time.astimezone(UTC)
    filename = timestamp.strftime("%Y%m%dT%H%M%S")
    if timestamp.microsecond:
        filename += f".{timestamp.microsecond:06d}"
    return layout.cache_data / "map2d" / variable / selection / f"{filename}.nc"


def _selection_label(variable: str, request: QuantityRequest) -> str:
    parts: list[str] = []
    if request.fractions is not None:
        parts.append("fraction-" + "-".join(str(index) for index in request.fractions))
    if request.layers is not None:
        parts.append("layer-" + "-".join(str(index) for index in request.layers))
    if request.sum_fractions:
        parts.append("sum-fractions")
    if variable == "morphological_active_area":
        threshold = format(request.active_sediment_thickness_threshold_m, ".8g")
        parts.append(f"active-thickness-{threshold.replace('.', 'p')}m")
    return "-".join(parts) or "all"


def _fingerprint_parameters(
    variable: str,
    quantity_version: str | None,
    request: QuantityRequest,
    selection: str,
    dtype: str,
    on_corrupt_output: str,
    entry: TimeEntry,
    plan_index: int,
) -> dict[str, object]:
    request_parameters: dict[str, object] = {
        "fractions": list(request.fractions) if request.fractions is not None else None,
        "layers": list(request.layers) if request.layers is not None else None,
        "sum_fractions": request.sum_fractions,
    }
    if variable == "morphological_active_area":
        request_parameters["active_sediment_thickness_threshold_m"] = (
            request.active_sediment_thickness_threshold_m
        )
    return {
        "variable": variable,
        "quantity_version": quantity_version,
        "requested_name": request.requested_name.casefold(),
        "selection": selection,
        "request": request_parameters,
        "dtype": dtype,
        "on_corrupt_output": on_corrupt_output,
        "corrupt_partitions": list(entry.corrupt_partitions),
        "subrun": entry.subrun,
        "local_index": entry.local_index,
        "morphological_time": (
            entry.morph_time.isoformat() if entry.morph_time is not None else None
        ),
        "plan": plan_index,
    }


def _face_values(values: _Array, n_faces: int, request: QuantityRequest) -> _Array:
    result = np.asarray(values, dtype=np.float64)
    if result.ndim > 1 and all(size == 1 for size in result.shape[1:]):
        result = result.reshape(result.shape[0])
    if result.ndim != 1 or result.shape[0] != n_faces:
        code = "V-VAR-005" if result.ndim > 1 else "V-VAR-004"
        raise Map2DProcessError(
            ValidationIssue(
                code=code,
                level="error",
                location=request.requested_name,
                message=(
                    f"Map2d quantity {request.requested_name!r} has shape "
                    f"{result.shape}; expected one value per face."
                ),
                hint=("Select or reduce extra dimensions before writing map2d data."),
            )
        )
    return result


def _previous_entry(
    entries: Sequence[TimeEntry],
    current: TimeEntry,
) -> TimeEntry | None:
    previous = (
        entry
        for entry in entries
        if entry.subrun == current.subrun and entry.local_index < current.local_index
    )
    return max(previous, key=lambda entry: entry.local_index, default=None)


def _is_water_level_rate(request: QuantityRequest) -> bool:
    try:
        return get_variable_spec(request.requested_name).name == "water_level_rate"
    except UnknownVariableError:
        return False


def _request_variable_name(request: QuantityRequest) -> str:
    try:
        return get_variable_spec(request.requested_name).name
    except UnknownVariableError:
        return request.requested_name.casefold()


def _write_data_file(
    path: Path,
    guard: OutputWriteGuard,
    values: _Array,
    *,
    dtype: Literal["float32", "float64"],
    units: str,
    long_name: str,
    fingerprint: str,
    parameters: dict[str, object],
    entry: TimeEntry,
    plan_index: int,
    grid_path: Path,
    variable: str,
    selection: str,
) -> None:
    netcdf4 = import_module("netCDF4")
    stored_values = np.asarray(values, dtype=np.dtype(dtype))
    relative_grid_path = os.path.relpath(grid_path, path.parent).replace("\\", "/")
    current_time = entry.time.astimezone(UTC)
    morph_time = (
        entry.morph_time.astimezone(UTC).isoformat()
        if entry.morph_time is not None
        else ""
    )
    attributes: dict[str, object] = {
        "Conventions": "CF-1.10",
        "d3dplot_fingerprint": fingerprint,
        "d3dplot_parameters": canonical_json(parameters),
        "d3dplot_version": __version__,
        "d3dplot_cache_schema": CACHE_SCHEMA_VERSION,
        "time": current_time.isoformat(),
        "time_utc": current_time.timestamp(),
        "time_utc_units": _CF_TIME_UNITS,
        "time_utc_calendar": "proleptic_gregorian",
        "morph_time": morph_time,
        "subrun": entry.subrun,
        "local_index": entry.local_index,
        "variable": variable,
        "selection": selection,
        "grid_file": relative_grid_path,
        "plan": plan_index,
    }
    with atomic_path(path, guard) as temporary:
        with netcdf4.Dataset(temporary, mode="w", format="NETCDF4") as dataset:
            dataset.createDimension("face", stored_values.size)
            variable_data = dataset.createVariable(
                "values",
                np.dtype(dtype),
                ("face",),
                zlib=True,
                complevel=4,
                fill_value=np.nan,
            )
            variable_data[:] = stored_values
            variable_data.setncattr("units", units)
            variable_data.setncattr("long_name", long_name)
            for name, value in attributes.items():
                dataset.setncattr(name, value)


__all__ = [
    "Map2DProcessError",
    "Map2DProcessResult",
    "Map2DTaskProcessor",
    "map2d_output_path",
]
