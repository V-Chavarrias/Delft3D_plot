# SPDX-License-Identifier: GPL-3.0-or-later
"""Read and merge raw face- or edge-centred map variables."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, Literal, Protocol, TypeAlias, cast

from d3dplot.simulation.discover import Simulation
from d3dplot.simulation.partitions import _NETCDF4_READ_LOCK
from d3dplot.simulation.subrun import SubRun
from d3dplot.validate.report import ValidationIssue
from d3dplot.variables.registry import VariableKind, VariableLocation, VariableSpec

_Array: TypeAlias = Any
np: Any = import_module("numpy")


class _NetCDFVariable(Protocol):
    dimensions: tuple[str, ...]
    shape: tuple[int, ...]

    def __getitem__(self, key: object) -> object: ...

    def ncattrs(self) -> list[str]: ...

    def getncattr(self, name: str) -> object: ...


class _NetCDFDataset(Protocol):
    variables: Mapping[str, _NetCDFVariable]
    dimensions: Mapping[str, object]


class _NetCDF4Module(Protocol):
    def Dataset(
        self,
        path: Path,
        *,
        mode: Literal["r"],
    ) -> AbstractContextManager[_NetCDFDataset]: ...


@dataclass(frozen=True, slots=True)
class _PlanMapping:
    n_faces: int
    n_edges: int
    partition_offset: _Array
    partition_n_owned: _Array
    owned_local: _Array
    global_pos: _Array
    edge_partition_offset: _Array
    edge_partition_n_owned: _Array
    edge_owned_local: _Array
    edge_global_pos: _Array
    edge_orientation: _Array


@dataclass(frozen=True, slots=True)
class MapReadResult:
    """Canonical global values, edge orientation, and runtime quality warnings."""

    values: _Array
    edge_orientation: _Array | None
    issues: tuple[ValidationIssue, ...]


class MapReaderError(RuntimeError):
    """A map read failed with one stable validation issue."""

    def __init__(self, issue: ValidationIssue) -> None:
        self.issue = issue
        super().__init__(issue.message)


class MapReader:
    """Read each raw variable from partition files and scatter it globally."""

    def __init__(
        self,
        simulation: Simulation,
        merge_plan_path: Path,
        *,
        on_corrupt_output: Literal["mask", "error", "skip"] = "mask",
    ) -> None:
        self._simulation = simulation
        self._on_corrupt_output = on_corrupt_output
        self._plans_by_subrun = _load_plan_mappings(
            simulation,
            merge_plan_path,
        )

    def read(
        self,
        spec: VariableSpec,
        *,
        subrun_index: int,
        local_index: int,
        corrupt_partitions: Sequence[int] = (),
    ) -> MapReadResult:
        """Read one raw variable at one map index in canonical dimension order."""
        if spec.kind is not VariableKind.RAW:
            raise ValueError("MapReader accepts raw VariableSpec instances only.")
        if spec.location is VariableLocation.LINE:
            raise MapReaderError(
                _issue(
                    "V-VAR-006",
                    "error",
                    spec.name,
                    "Line quantities cannot be read from a whole-grid map file.",
                    "Request a grid quantity or use a line-sampling analysis.",
                )
            )

        subrun = _find_subrun(self._simulation, subrun_index)
        plan = self._plans_by_subrun.get(subrun_index)
        if plan is None:
            raise MapReaderError(
                _issue(
                    "V-OUT-020",
                    "error",
                    self._simulation.root,
                    f"No merge plan is assigned to sub-run {subrun_index}.",
                    "Run inspection again to rebuild the merge plan.",
                )
            )

        corrupt = set(corrupt_partitions)
        invalid_corrupt = corrupt - set(range(subrun.partition_count))
        if invalid_corrupt:
            raise MapReaderError(
                _issue(
                    "V-OUT-030",
                    "error",
                    subrun.folder,
                    (
                        "Corrupt-partition metadata references unknown partition "
                        f"indices: {sorted(invalid_corrupt)}."
                    ),
                    "Rebuild the time-quality cache with a fresh inspection.",
                )
            )
        if corrupt and self._on_corrupt_output != "mask":
            level: Literal["error", "warning"] = (
                "error" if self._on_corrupt_output == "error" else "warning"
            )
            action = (
                "The configured policy is 'error'."
                if self._on_corrupt_output == "error"
                else (
                    "The configured policy is 'skip'; remove this output "
                    "from selection."
                )
            )
            raise MapReaderError(
                _issue(
                    "V-TIME-011",
                    level,
                    subrun.folder,
                    (
                        f"Sub-run {subrun_index}, map index {local_index} "
                        f"contains corrupt partition(s) {sorted(corrupt)}. {action}"
                    ),
                    "Choose a non-corrupt output or change on_corrupt_output.",
                )
            )

        location = (
            VariableLocation.FACE
            if spec.location is VariableLocation.FACE
            else VariableLocation.EDGE
        )
        location_dimension = (
            "mesh2d_nFaces" if location is VariableLocation.FACE else "mesh2d_nEdges"
        )
        expected_count = (
            plan.n_faces if location is VariableLocation.FACE else plan.n_edges
        )
        output: _Array | None = None
        selected_name: str | None = None
        issues: list[ValidationIssue] = []
        orientations = (
            _edge_orientation_by_global(plan)
            if location is VariableLocation.EDGE
            else None
        )

        for partition, path in enumerate(subrun.map_files):
            local_indices, global_positions = _partition_positions(
                plan,
                location,
                partition,
            )
            try:
                with _NETCDF4_READ_LOCK:
                    with _netcdf4_module().Dataset(path, mode="r") as dataset:
                        if selected_name is None:
                            selected_name = next(
                                (
                                    candidate
                                    for candidate in spec.netcdf_candidates
                                    if candidate in dataset.variables
                                ),
                                None,
                            )
                        if selected_name is None:
                            raise MapReaderError(
                                _issue(
                                    "V-VAR-002",
                                    "error",
                                    path,
                                    (
                                        f"None of the NetCDF candidates for "
                                        f"{spec.name!r} were found: "
                                        f"{', '.join(spec.netcdf_candidates)}."
                                    ),
                                    "Restore the required variable in every map "
                                    "partition.",
                                )
                            )
                        variable = dataset.variables.get(selected_name)
                        if variable is None:
                            raise MapReaderError(
                                _issue(
                                    "V-VAR-002",
                                    "error",
                                    path,
                                    (
                                        f"Variable {selected_name!r} for "
                                        f"{spec.name!r} is missing."
                                    ),
                                    "Restore the required variable in every map "
                                    "partition.",
                                )
                            )

                        dimensions = variable.dimensions
                        expected_dimensions = (
                            location_dimension,
                            *spec.extra_dims,
                        )
                        time_dimensions = dimensions.count("time")
                        non_time_dimensions = tuple(
                            dimension for dimension in dimensions if dimension != "time"
                        )
                        if (
                            time_dimensions > 1
                            or len(non_time_dimensions) != len(expected_dimensions)
                            or set(non_time_dimensions) != set(expected_dimensions)
                        ):
                            raise MapReaderError(
                                _issue(
                                    "V-VAR-004",
                                    "error",
                                    path,
                                    (
                                        f"Variable {selected_name!r} has dimensions "
                                        f"{dimensions}; expected optional 'time' and "
                                        f"{expected_dimensions}."
                                    ),
                                    "Correct the VariableSpec or restore the "
                                    "expected map variable.",
                                )
                            )
                        shape_by_dimension = dict(
                            zip(dimensions, variable.shape, strict=True)
                        )
                        local_count = shape_by_dimension[location_dimension]
                        if local_indices.size and (
                            int(local_indices.min()) < 0
                            or int(local_indices.max()) >= local_count
                        ):
                            raise MapReaderError(
                                _issue(
                                    "V-OUT-020",
                                    "error",
                                    path,
                                    (
                                        f"The merge plan indexes outside the local "
                                        f"{location.value} dimension."
                                    ),
                                    "Rebuild the merge plan for the current "
                                    "output topology.",
                                )
                            )
                        if "time" in dimensions:
                            time_count = shape_by_dimension["time"]
                            if local_index < 0 or local_index >= time_count:
                                raise MapReaderError(
                                    _issue(
                                        "V-OUT-030",
                                        "error",
                                        path,
                                        (
                                            f"Map index {local_index} is outside "
                                            f"the time dimension of {selected_name!r}."
                                        ),
                                        "Rebuild the time-quality cache and "
                                        "restore complete output.",
                                    )
                                )

                        extra_shape = tuple(
                            shape_by_dimension[dimension]
                            for dimension in spec.extra_dims
                        )
                        if output is None:
                            output = np.full(
                                (expected_count, *extra_shape),
                                np.nan,
                                dtype=np.float64,
                            )
                        elif output.shape[1:] != extra_shape:
                            raise MapReaderError(
                                _issue(
                                    "V-VAR-004",
                                    "error",
                                    path,
                                    (
                                        f"Variable {selected_name!r} has extra "
                                        "dimension sizes that differ between "
                                        "partitions."
                                    ),
                                    "Restore consistent extra dimensions across "
                                    "partitions.",
                                )
                            )

                        if partition in corrupt:
                            issues.append(
                                _issue(
                                    "V-TIME-011",
                                    "warning",
                                    path,
                                    (
                                        f"Masked {local_indices.size} owned "
                                        f"{location.value}s in corrupt partition "
                                        f"{partition} at map index {local_index}."
                                    ),
                                    "The corrupt output was retained with NaN values.",
                                )
                            )
                            continue

                        values, invalid_count = _read_partition_values(
                            variable,
                            selected_name,
                            spec,
                            location_dimension,
                            local_indices,
                            local_index,
                            path,
                        )
                        if values.shape[0] != global_positions.size:
                            raise MapReaderError(
                                _issue(
                                    "V-OUT-020",
                                    "error",
                                    path,
                                    "The selected local values do not match the "
                                    "merge plan.",
                                    "Rebuild the merge plan for the current "
                                    "output topology.",
                                )
                            )
                        output[global_positions] = values
                        if invalid_count:
                            issues.append(
                                _issue(
                                    "V-OUT-031",
                                    "warning",
                                    path,
                                    (
                                        f"Masked {invalid_count} implausible value(s) "
                                        f"in {selected_name!r} at map index "
                                        f"{local_index}."
                                    ),
                                    "Non-finite values and magnitudes above "
                                    "1e30 are treated as missing.",
                                )
                            )
            except MapReaderError:
                raise
            except (
                EOFError,
                IndexError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as error:
                raise MapReaderError(
                    _issue(
                        "V-OUT-002",
                        "error",
                        path,
                        f"Could not read {spec.name!r} from the map file: {error}",
                        "Restore or regenerate the affected map output.",
                    )
                ) from error

        if output is None:
            raise MapReaderError(
                _issue(
                    "V-VAR-002",
                    "error",
                    subrun.folder,
                    f"Variable {spec.name!r} was not found in any map partition.",
                    "Restore the required variable in the simulation output.",
                )
            )
        return MapReadResult(
            values=output,
            edge_orientation=orientations,
            issues=tuple(issues),
        )


def _load_plan_mappings(
    simulation: Simulation,
    merge_plan_path: Path,
) -> dict[int, _PlanMapping]:
    try:
        with _NETCDF4_READ_LOCK:
            with _netcdf4_module().Dataset(merge_plan_path, mode="r") as dataset:
                variables = dataset.variables
                required = {
                    "subrun_index",
                    "plan_of_subrun",
                    "plan_n_faces",
                    "plan_n_edges",
                    "partition_offset",
                    "partition_n_owned",
                    "owned_local",
                    "global_pos",
                    "edge_partition_offset",
                    "edge_partition_n_owned",
                    "edge_owned_local",
                    "edge_global_pos",
                    "edge_orientation",
                }
                missing = required - set(variables)
                if missing:
                    raise ValueError(
                        "merge plan is missing variable(s): "
                        + ", ".join(sorted(missing))
                    )

                subrun_indices = np.asarray(
                    variables["subrun_index"][:], dtype=np.int64
                )
                plans = np.asarray(variables["plan_of_subrun"][:], dtype=np.int64)
                plan_n_faces = np.asarray(variables["plan_n_faces"][:], dtype=np.int64)
                plan_n_edges = np.asarray(variables["plan_n_edges"][:], dtype=np.int64)
                arrays = {
                    name: np.asarray(variables[name][:])
                    for name in (
                        "partition_offset",
                        "partition_n_owned",
                        "owned_local",
                        "global_pos",
                        "edge_partition_offset",
                        "edge_partition_n_owned",
                        "edge_owned_local",
                        "edge_global_pos",
                        "edge_orientation",
                    )
                }
    except (
        EOFError,
        IndexError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as error:
        raise MapReaderError(
            _issue(
                "V-OUT-020",
                "error",
                merge_plan_path,
                f"Could not load the face/edge merge plan: {error}",
                "Run inspection again to rebuild the merge plan.",
            )
        ) from error

    mappings: dict[int, _PlanMapping] = {}
    for position, subrun_index in enumerate(subrun_indices):
        plan_index = int(plans[position])
        if plan_index < 0 or plan_index >= plan_n_faces.size:
            raise MapReaderError(
                _issue(
                    "V-OUT-020",
                    "error",
                    merge_plan_path,
                    f"Invalid plan index for sub-run {int(subrun_index)}.",
                    "Run inspection again to rebuild the merge plan.",
                )
            )
        mappings[int(subrun_index)] = _PlanMapping(
            n_faces=int(plan_n_faces[plan_index]),
            n_edges=int(plan_n_edges[plan_index]),
            partition_offset=arrays["partition_offset"][plan_index],
            partition_n_owned=arrays["partition_n_owned"][plan_index],
            owned_local=arrays["owned_local"][plan_index],
            global_pos=arrays["global_pos"][plan_index],
            edge_partition_offset=arrays["edge_partition_offset"][plan_index],
            edge_partition_n_owned=arrays["edge_partition_n_owned"][plan_index],
            edge_owned_local=arrays["edge_owned_local"][plan_index],
            edge_global_pos=arrays["edge_global_pos"][plan_index],
            edge_orientation=arrays["edge_orientation"][plan_index],
        )

    expected_subruns = {subrun.index for subrun in simulation.subruns}
    if set(mappings) != expected_subruns:
        raise MapReaderError(
            _issue(
                "V-OUT-020",
                "error",
                merge_plan_path,
                "Merge-plan sub-run coverage does not match the simulation.",
                "Run inspection again to rebuild the merge plan.",
            )
        )
    return mappings


def _partition_positions(
    plan: _PlanMapping,
    location: VariableLocation,
    partition: int,
) -> tuple[_Array, _Array]:
    if location is VariableLocation.FACE:
        offsets = plan.partition_offset
        counts = plan.partition_n_owned
        local = plan.owned_local
        global_positions = plan.global_pos
    else:
        offsets = plan.edge_partition_offset
        counts = plan.edge_partition_n_owned
        local = plan.edge_owned_local
        global_positions = plan.edge_global_pos
    if partition < 0 or partition >= counts.size:
        raise ValueError(f"Invalid partition index {partition}.")
    start = int(offsets[partition])
    stop = start + int(counts[partition])
    return local[start:stop], global_positions[start:stop]


def _edge_orientation_by_global(plan: _PlanMapping) -> _Array:
    result = np.empty(plan.n_edges, dtype=np.int8)
    result[plan.edge_global_pos[: plan.n_edges]] = plan.edge_orientation[: plan.n_edges]
    return result


def _read_partition_values(
    variable: _NetCDFVariable,
    variable_name: str,
    spec: VariableSpec,
    location_dimension: str,
    local_indices: _Array,
    local_index: int,
    location: Path,
) -> tuple[_Array, int]:
    dimensions = variable.dimensions
    indexer = tuple(
        local_index if dimension == "time" else slice(None) for dimension in dimensions
    )
    raw = np.ma.asarray(variable[indexer])
    if not np.issubdtype(raw.dtype, np.number):
        raise MapReaderError(
            _issue(
                "V-VAR-004",
                "error",
                location,
                f"Variable {variable_name!r} must contain numeric values.",
                "Restore numeric values or use a compatible VariableSpec.",
            )
        )

    mask = np.asarray(np.ma.getmaskarray(raw), dtype=np.bool_).copy()
    data = np.asarray(np.ma.getdata(raw))
    available_attributes = {name.casefold(): name for name in variable.ncattrs()}
    for missing_name in ("_FillValue", "missing_value"):
        actual_name = available_attributes.get(missing_name.casefold())
        if actual_name is None:
            continue
        missing_values = np.asarray(variable.getncattr(actual_name)).reshape(-1)
        for missing_value in missing_values:
            try:
                missing_numeric = float(missing_value)
            except (TypeError, ValueError):
                continue
            if np.isnan(missing_numeric):
                mask |= np.isnan(data)
            else:
                mask |= data == missing_value

    dimensions_without_time = tuple(
        dimension for dimension in dimensions if dimension != "time"
    )
    selected = np.take(
        raw, local_indices, axis=dimensions_without_time.index(location_dimension)
    )
    selected_mask = np.take(
        mask, local_indices, axis=dimensions_without_time.index(location_dimension)
    )
    remaining_dimensions = list(dimensions_without_time)
    canonical_dimensions = [location_dimension, *spec.extra_dims]
    permutation = [
        remaining_dimensions.index(dimension) for dimension in canonical_dimensions
    ]
    if permutation != list(range(len(permutation))):
        selected = np.transpose(selected, axes=permutation)
        selected_mask = np.transpose(selected_mask, axes=permutation)

    values = np.asarray(np.ma.getdata(selected), dtype=np.float64)
    invalid = ~np.isfinite(values) | (np.abs(values) > 1e30)
    invalid_count = int(np.count_nonzero(~selected_mask & invalid))
    values = values.copy()
    values[selected_mask | invalid] = np.nan
    return values, invalid_count


def _find_subrun(simulation: Simulation, index: int) -> SubRun:
    for subrun in simulation.subruns:
        if subrun.index == index:
            return subrun
    raise MapReaderError(
        _issue(
            "V-OUT-020",
            "error",
            simulation.root,
            f"Unknown sub-run {index}.",
            "Use a sub-run index from the discovered simulation.",
        )
    )


def _issue(
    code: str,
    level: Literal["error", "warning"],
    location: str | Path,
    message: str,
    hint: str,
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        level=level,
        location=str(location),
        message=message,
        hint=hint,
    )


def _netcdf4_module() -> _NetCDF4Module:
    os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")
    return cast(_NetCDF4Module, import_module("netCDF4"))
