# SPDX-License-Identifier: GPL-3.0-or-later
"""Process PRF profile quantities: sampling along polylines, per-time caches (D118)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypeAlias

import netCDF4
import numpy as np

from d3dplot.simulation.time_axis import TimeEntry
from d3dplot.simulation.time_utils import format_hydrodynamic_timestamp
from d3dplot.store import (
    CACHE_SCHEMA_VERSION,
    OutputLayout,
    OutputWriteGuard,
    atomic_path,
    fingerprint_artifact,
)
from d3dplot.store.paths import OutputArea, build_quantity_output_path
from d3dplot.variables.context import QuantityContext, QuantityRequest
from d3dplot.variables.engine import QuantityEngine, QuantityReaderSession
from d3dplot.variables.line_crossings_cache import Polyline, prepare_line_plan
from d3dplot.variables.line_sampling import LinePlan, LineSampling, LineTopology
from d3dplot.variables.registry import get_variable_spec

VerticalSelection: TypeAlias = Literal[
    "all", "top", "bottom", "depth_average", "elevation"
]

_PROFILE_CACHE_ALGORITHM_VERSION = "1"


class ProfileProcessError(ValueError):
    """A profile line, selection or cached profile slice cannot be processed."""


@dataclass(frozen=True, slots=True)
class ProfileSelection:
    """Vertical and layer selection of one requested quantity along a line."""

    variable: str
    layers: tuple[int, ...] | None = None
    vertical: VerticalSelection = "all"
    elevation: float | None = None
    z_range: tuple[float, float] | None = None

    def parameters(self) -> dict[str, Any]:
        """Return the canonical description used in fingerprints and file names."""
        return {
            "variable": get_variable_spec(self.variable).name,
            "layers": None if self.layers is None else list(self.layers),
            "vertical": self.vertical,
            "elevation": self.elevation,
            "z_range": None if self.z_range is None else list(self.z_range),
        }

    def label(self) -> str:
        """Return a short stable label that separates selections in file names."""
        text = json.dumps(self.parameters(), sort_keys=True).encode()
        return hashlib.sha256(text).hexdigest()[:12]


@dataclass(frozen=True, eq=False)
class ProfileLine:
    """One polyline with its sampling and prepared crossings."""

    name: str
    sampling: LineSampling
    plan: LinePlan

    @property
    def n_crossings(self) -> int:
        return int(self.plan.crossings.face_index.size)

    @property
    def distance_m(self) -> np.ndarray:
        """Distance along the polyline at the middle of each crossing."""
        return self.plan.crossings.distance_m


def select_polyline(
    polylines: Sequence[Polyline],
    *,
    line_name: str,
    polyline_name: str | None,
) -> Polyline:
    """Return the configured polyline object, or the only one in the file."""
    if polyline_name is None:
        if len(polylines) != 1:
            names = ", ".join(repr(polyline.name) for polyline in polylines)
            raise ProfileProcessError(
                f"Profile line {line_name!r} needs a polyline name; the file holds "
                f"{names}."
            )
        return polylines[0]
    for polyline in polylines:
        if polyline.name.casefold() == polyline_name.casefold():
            return polyline
    raise ProfileProcessError(
        f"Profile line {line_name!r}: polyline {polyline_name!r} is not in the file."
    )


def prepare_profile_line(
    name: str,
    polyline_xy: np.ndarray,
    topology: LineTopology,
    layout: OutputLayout,
    guard: OutputWriteGuard,
    *,
    diagnostic: Callable[[str], None] | None = None,
) -> ProfileLine:
    """Compute or reuse the crossings of one line, once per task."""
    sampling = LineSampling(polyline_xy, topology)
    plan = prepare_line_plan(sampling, layout, guard, diagnostic=diagnostic)
    return ProfileLine(name=name, sampling=sampling, plan=plan)


def read_profile_values(
    engine: QuantityEngine,
    session: QuantityReaderSession,
    entry: TimeEntry,
    line: ProfileLine,
    selection: ProfileSelection,
    context: QuantityContext,
) -> np.ndarray:
    """Sample one quantity along a line at one time, keeping any layer axis."""
    engine.register_sampling_plan(line.sampling, line.plan)
    quantity = engine.at_time(session, entry, context)
    values = np.asarray(
        quantity.get(
            QuantityRequest(
                requested_name=selection.variable,
                layers=selection.layers,
                vertical=selection.vertical,
                elevation=selection.elevation,
                z_range=selection.z_range,
                sampling=line.sampling,
            )
        ),
        dtype=np.float64,
    )
    if values.ndim < 1 or values.shape[0] != line.n_crossings:
        raise ProfileProcessError(
            f"Profile quantity {selection.variable!r} must give one value per crossing "
            f"of line {line.name!r} ({line.n_crossings}), "
            f"received shape {values.shape}."
        )
    return values


def profile_cache_fingerprint(
    *,
    input_fingerprint: str,
    selection: ProfileSelection,
    line: ProfileLine,
    time: datetime,
) -> str:
    """Build the identity of one persisted profile slice."""
    return fingerprint_artifact(
        kind="profile",
        schema_version=CACHE_SCHEMA_VERSION,
        tool_version="d3dplot",
        algorithm_version=_PROFILE_CACHE_ALGORITHM_VERSION,
        parameters={
            **selection.parameters(),
            "line": line.name,
        },
        inputs=[input_fingerprint, line.sampling.fingerprint()],
        time=time.isoformat(),
    )


def profile_cache_path(
    layout: OutputLayout,
    *,
    selection: ProfileSelection,
    line_name: str,
    time: datetime,
) -> Path:
    """Return one guarded per-time profile cache path."""
    filename = format_hydrodynamic_timestamp(time)
    return build_quantity_output_path(
        layout,
        OutputArea.CACHE_DATA,
        get_variable_spec(selection.variable).name,
        prefix=("profile",),
        suffix=(line_name, selection.label()),
        filename=f"{filename}.nc",
    )


def write_profile_cache(
    path: Path,
    guard: OutputWriteGuard,
    *,
    values: np.ndarray,
    line: ProfileLine,
    selection: ProfileSelection,
    time: datetime,
    fingerprint: str,
) -> None:
    """Write one atomic, fingerprinted profile slice (float32 on disk, D26)."""
    data = np.asarray(values, dtype=np.float64)
    if data.ndim not in (1, 2) or data.shape[0] != line.n_crossings:
        raise ProfileProcessError(
            "Profile cache values must have one row per crossing of the line."
        )
    with atomic_path(path, guard) as temporary:
        with netCDF4.Dataset(temporary, mode="w", format="NETCDF4") as dataset:
            dataset.createDimension("crossing", data.shape[0])
            distance = dataset.createVariable("distance_m", "f8", ("crossing",))
            distance[:] = line.distance_m
            if data.ndim == 1:
                dimensions: tuple[str, ...] = ("crossing",)
            else:
                dataset.createDimension("layer", data.shape[1])
                dimensions = ("crossing", "layer")
            values_variable = dataset.createVariable("values", "f4", dimensions)
            values_variable[:] = data.astype(np.float32)
            dataset.setncattr("d3dplot_fingerprint", fingerprint)
            dataset.setncattr("variable", selection.variable)
            dataset.setncattr("line", line.name)
            dataset.setncattr("time", time.isoformat())
            dataset.setncattr("selection", json.dumps(selection.parameters()))


def read_profile_cache(
    path: Path,
    *,
    expected_fingerprint: str,
    expected_crossings: int,
) -> np.ndarray:
    """Read and verify one persisted profile slice."""
    try:
        with netCDF4.Dataset(path, mode="r") as dataset:
            if dataset.getncattr("d3dplot_fingerprint") != expected_fingerprint:
                raise ProfileProcessError(f"Profile cache fingerprint changed: {path}.")
            values = np.asarray(dataset.variables["values"][:], dtype=np.float64)
    except ProfileProcessError:
        raise
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise ProfileProcessError(
            f"Could not read profile cache {path}: {error}"
        ) from error
    if values.ndim < 1 or values.shape[0] != expected_crossings:
        raise ProfileProcessError(f"Profile cache has invalid crossing count: {path}.")
    return values


def profile_values_at_time(
    *,
    engine: QuantityEngine,
    session: QuantityReaderSession,
    entry: TimeEntry,
    line: ProfileLine,
    selection: ProfileSelection,
    layout: OutputLayout,
    guard: OutputWriteGuard,
    input_fingerprint: str,
    context: QuantityContext | None = None,
    diagnostic: Callable[[str], None] | None = None,
) -> np.ndarray:
    """Return one sampled slice, reusing its cache file when the fingerprint matches."""
    fingerprint = profile_cache_fingerprint(
        input_fingerprint=input_fingerprint,
        selection=selection,
        line=line,
        time=entry.time,
    )
    path = profile_cache_path(
        layout,
        selection=selection,
        line_name=line.name,
        time=entry.time,
    )
    if path.is_file():
        if diagnostic is not None:
            diagnostic(f"Reused profile cache {path.name}.")
        return read_profile_cache(
            path,
            expected_fingerprint=fingerprint,
            expected_crossings=line.n_crossings,
        )
    values = read_profile_values(
        engine,
        session,
        entry,
        line,
        selection,
        QuantityContext() if context is None else context,
    )
    write_profile_cache(
        path,
        guard,
        values=values,
        line=line,
        selection=selection,
        time=entry.time,
        fingerprint=fingerprint,
    )
    return values


__all__ = [
    "ProfileLine",
    "ProfileProcessError",
    "ProfileSelection",
    "VerticalSelection",
    "prepare_profile_line",
    "profile_cache_fingerprint",
    "profile_cache_path",
    "profile_values_at_time",
    "read_profile_cache",
    "read_profile_values",
    "select_polyline",
    "write_profile_cache",
]
