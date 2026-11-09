# SPDX-License-Identifier: GPL-3.0-or-later
"""Polyline input and persisted line-crossing indices for PRF sampling (D55, D116)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import netCDF4
import numpy as np

from d3dplot import __version__
from d3dplot.store import (
    CACHE_SCHEMA_VERSION,
    OutputLayout,
    OutputWriteGuard,
    atomic_path,
    fingerprint_artifact,
)
from d3dplot.variables.geometry import (
    GridGeometryError,
    _read_connectivity,
    _read_float_array,
)
from d3dplot.variables.line_sampling import (
    LineCrossings,
    LinePlan,
    LineSampling,
    LineTopology,
    compute_line_crossings,
)

_CROSSINGS_ALGORITHM_VERSION = "1"


class LineInputError(ValueError):
    """A polyline file or a cached line-crossing index cannot be used."""


@dataclass(frozen=True, slots=True)
class Polyline:
    """One named polyline of a .pli file, in model coordinates."""

    name: str
    xy: np.ndarray


def read_pli_polylines(path: str | Path) -> tuple[Polyline, ...]:
    """Read every polyline object of a .pli file with HYDROLIB-core."""
    source = Path(path)
    try:
        from hydrolib.core.dflowfm import PolyFile  # type: ignore[import-untyped]

        objects = PolyFile(source).objects
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise LineInputError(
            f"Could not read polyline file {source}: {error}"
        ) from error

    polylines: list[Polyline] = []
    for index, poly_object in enumerate(objects):
        name = (poly_object.metadata.name or "").strip() or f"line_{index + 1}"
        xy = np.array(
            [(float(point.x), float(point.y)) for point in poly_object.points],
            dtype=np.float64,
        ).reshape(-1, 2)
        if xy.shape[0] < 2:
            raise LineInputError(
                f"{source}: polyline {name!r} needs at least two points."
            )
        if not np.all(np.isfinite(xy)):
            raise LineInputError(f"{source}: polyline {name!r} has non-finite points.")
        polylines.append(Polyline(name=name, xy=xy))
    if not polylines:
        raise LineInputError(f"{source} contains no polyline.")
    return tuple(polylines)


def read_line_topology(
    grid_path: str | Path,
    *,
    is_geographic: bool,
) -> LineTopology:
    """Read the merged node coordinates and face connectivity of an inspection grid.

    The caller supplies ``is_geographic`` from the CRS used for inspection, because
    the grid file does not record it reliably. Face-node indices are returned
    zero-based with ``-1`` as fill, as in the grid geometry reader.
    """
    from d3dplot.simulation.partitions import _NETCDF4_READ_LOCK

    source = Path(grid_path)
    try:
        with _NETCDF4_READ_LOCK:
            with netCDF4.Dataset(source, mode="r") as dataset:
                required = ("mesh2d_node_x", "mesh2d_node_y", "mesh2d_face_nodes")
                missing = [name for name in required if name not in dataset.variables]
                if missing:
                    raise LineInputError(
                        f"{source} lacks {', '.join(missing)}; re-run inspection."
                    )
                node_x = _read_float_array(dataset.variables["mesh2d_node_x"])
                node_y = _read_float_array(dataset.variables["mesh2d_node_y"])
                face_nodes = _read_connectivity(dataset.variables["mesh2d_face_nodes"])
        return LineTopology(
            node_x=node_x,
            node_y=node_y,
            face_nodes=face_nodes,
            is_geographic=is_geographic,
        )
    except LineInputError:
        raise
    except (
        GridGeometryError,
        IndexError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as error:
        raise LineInputError(
            f"Could not read line topology from {source}: {error}"
        ) from error


def line_crossings_fingerprint(sampling: LineSampling) -> str:
    """Return the identity of the crossing indices for one polyline and topology."""
    return fingerprint_artifact(
        kind="line_crossings",
        schema_version=CACHE_SCHEMA_VERSION,
        tool_version=__version__,
        algorithm_version=_CROSSINGS_ALGORITHM_VERSION,
        parameters={},
        inputs=[sampling.fingerprint()],
    )


def line_crossings_cache_path(layout: OutputLayout, fingerprint: str) -> Path:
    """Return the guarded geometry-cache path of one crossing-index file."""
    return layout.cache_geometry / f"line_crossings_{fingerprint[:24]}.nc"


def write_line_crossings_cache(
    path: Path,
    guard: OutputWriteGuard,
    crossings: LineCrossings,
    *,
    fingerprint: str,
) -> None:
    """Write one atomic, fingerprinted set of line-crossing indices."""
    with atomic_path(path, guard) as temporary:
        with netCDF4.Dataset(temporary, mode="w", format="NETCDF4") as dataset:
            dataset.createDimension("crossing", crossings.face_index.size)
            dataset.createDimension("component", 2)
            face_index = dataset.createVariable("face_index", "i8", ("crossing",))
            face_index[:] = crossings.face_index
            for name, array in (
                ("distance_start_m", crossings.distance_start_m),
                ("distance_stop_m", crossings.distance_stop_m),
            ):
                variable_object = dataset.createVariable(name, "f8", ("crossing",))
                variable_object[:] = array
            direction = dataset.createVariable(
                "direction_xy", "f8", ("crossing", "component")
            )
            direction[:, :] = crossings.direction_xy
            dataset.setncattr("d3dplot_fingerprint", fingerprint)


def read_line_crossings_cache(
    path: Path,
    *,
    expected_fingerprint: str,
) -> LineCrossings:
    """Read and verify one persisted set of line-crossing indices."""
    try:
        with netCDF4.Dataset(path, mode="r") as dataset:
            if dataset.getncattr("d3dplot_fingerprint") != expected_fingerprint:
                raise LineInputError(
                    f"Line crossings cache fingerprint changed: {path}."
                )
            face_index = np.asarray(dataset.variables["face_index"][:], dtype=np.int64)
            distance_start = np.asarray(
                dataset.variables["distance_start_m"][:], dtype=np.float64
            )
            distance_stop = np.asarray(
                dataset.variables["distance_stop_m"][:], dtype=np.float64
            )
            direction = np.asarray(
                dataset.variables["direction_xy"][:], dtype=np.float64
            )
    except LineInputError:
        raise
    except (
        AttributeError,
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as error:
        raise LineInputError(
            f"Could not read line crossings cache {path}: {error}"
        ) from error

    count = face_index.size
    if (
        count == 0
        or distance_start.shape != (count,)
        or distance_stop.shape != (count,)
        or direction.shape != (count, 2)
    ):
        raise LineInputError(f"Line crossings cache has invalid shapes: {path}.")
    return LineCrossings(
        face_index=face_index,
        distance_start_m=distance_start,
        distance_stop_m=distance_stop,
        direction_xy=direction,
    )


def prepare_line_plan(
    sampling: LineSampling,
    layout: OutputLayout,
    guard: OutputWriteGuard,
    *,
    diagnostic: Callable[[str], None] | None = None,
) -> LinePlan:
    """Load the persisted crossings of a polyline, or compute and persist them once."""
    fingerprint = line_crossings_fingerprint(sampling)
    path = line_crossings_cache_path(layout, fingerprint)
    if path.is_file():
        crossings = read_line_crossings_cache(path, expected_fingerprint=fingerprint)
        if diagnostic is not None:
            diagnostic(f"Reused line crossings cache {path.name}.")
    else:
        crossings = compute_line_crossings(sampling.topology, sampling.polyline_xy)
        write_line_crossings_cache(path, guard, crossings, fingerprint=fingerprint)
        if diagnostic is not None:
            diagnostic(f"Built line crossings cache {path.name}.")
    return LinePlan(fingerprint=sampling.fingerprint(), crossings=crossings)


__all__ = [
    "LineInputError",
    "Polyline",
    "line_crossings_cache_path",
    "line_crossings_fingerprint",
    "prepare_line_plan",
    "read_line_crossings_cache",
    "read_pli_polylines",
    "write_line_crossings_cache",
]
