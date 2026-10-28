# SPDX-License-Identifier: GPL-3.0-or-later
"""Map windows and fingerprinted visible-face subsets."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol, cast

import netCDF4

from d3dplot import __version__
from d3dplot.config.figure import Extent, MapViewConfig
from d3dplot.execution.cache import read_cache_fingerprints
from d3dplot.river_km.file import RiverKmFileError, read_river_km_points
from d3dplot.store import (
    CACHE_SCHEMA_VERSION,
    OutputLayout,
    OutputWriteGuard,
    atomic_path,
    canonical_json,
    fingerprint_artifact,
)

_Array = Any
np: Any = import_module("numpy")
xu: Any = import_module("xugrid")


class ExtentSetError(RuntimeError):
    """Configured map windows could not be resolved."""

    def __init__(self, message: str, *, code: str = "V-PLOT-002") -> None:
        super().__init__(message)
        self.code = code


class UgridGrid(Protocol):
    @property
    def bounds(self) -> tuple[float, float, float, float]: ...

    @property
    def face_node_connectivity(self) -> _Array: ...

    @property
    def node_coordinates(self) -> _Array: ...

    @property
    def fill_value(self) -> int: ...

    @property
    def n_face(self) -> int: ...

    @property
    def face_dimension(self) -> str: ...

    def rasterize(
        self,
        resolution: float,
        bounds: tuple[float, float, float, float],
    ) -> tuple[_Array, _Array, _Array]: ...


class UgridDataset(Protocol):
    @property
    def grid(self) -> UgridGrid: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ExtentWindow:
    """One named map window and the grid faces that intersect it."""

    index: int
    name: str
    bounds: Extent
    faces: _Array
    fingerprint: str


@dataclass(frozen=True, slots=True)
class ExtentSet:
    """Full-model, explicit, and river-km map windows."""

    windows: tuple[ExtentWindow, ...]

    @classmethod
    def create(
        cls,
        grid: UgridGrid,
        grid_path: Path,
        layout: OutputLayout,
        guard: OutputWriteGuard,
        config: MapViewConfig,
    ) -> ExtentSet:
        grid_bounds = grid.bounds
        windows: list[tuple[str, Extent, bool]] = [
            (
                "full_model",
                (grid_bounds[0], grid_bounds[2], grid_bounds[1], grid_bounds[3]),
                True,
            )
        ]
        if config.extents is not None:
            windows.extend(
                (f"extent_{index + 1}", bounds, False)
                for index, bounds in enumerate(config.extents)
            )
        if config.extents_along_river_km is not None:
            windows.extend(
                _river_windows(
                    config.extents_along_river_km.file,
                    config.extents_along_river_km.window_x_m,
                    config.extents_along_river_km.window_y_m,
                )
            )

        grid_fingerprint = read_cache_fingerprints((grid_path,))[
            grid_path.resolve(strict=False)
        ]
        if grid_fingerprint is None:
            raise ExtentSetError(
                f"Inspected grid cache has no fingerprint: {grid_path}."
            )
        faces_by_window = _visible_faces(grid)
        result: list[ExtentWindow] = []
        for index, (name, bounds, is_full) in enumerate(windows):
            faces = (
                np.arange(grid.n_face, dtype=np.int32)
                if is_full
                else _faces_in_bounds(faces_by_window, bounds)
            )
            fingerprint = fingerprint_artifact(
                kind="visible_faces",
                schema_version=CACHE_SCHEMA_VERSION,
                tool_version=__version__,
                algorithm_version="1",
                parameters={
                    "bounds": list(bounds),
                    "name": name,
                    "n_faces": int(grid.n_face),
                },
                inputs=(grid_fingerprint,),
            )
            cache_path = layout.cache_geometry / f"extent_{fingerprint[:24]}.nc"
            faces = _load_or_write_faces(cache_path, faces, fingerprint, bounds, guard)
            result.append(
                ExtentWindow(
                    index=index,
                    name=name,
                    bounds=bounds,
                    faces=faces,
                    fingerprint=fingerprint,
                )
            )
        return cls(windows=tuple(result))


def create_extent_window(
    grid: UgridGrid,
    grid_path: Path,
    layout: OutputLayout,
    guard: OutputWriteGuard,
    *,
    index: int,
    name: str,
    bounds: Extent,
) -> ExtentWindow:
    """Create or reuse one cached face subset for caller-defined bounds."""
    xmin, xmax, ymin, ymax = bounds
    if xmin >= xmax or ymin >= ymax:
        raise ExtentSetError(f"Extent {name!r} must have increasing bounds.")
    grid_fingerprint = read_cache_fingerprints((grid_path,))[
        grid_path.resolve(strict=False)
    ]
    if grid_fingerprint is None:
        raise ExtentSetError(f"Inspected grid cache has no fingerprint: {grid_path}.")
    fingerprint = fingerprint_artifact(
        kind="visible_faces",
        schema_version=CACHE_SCHEMA_VERSION,
        tool_version=__version__,
        algorithm_version="1",
        parameters={
            "bounds": list(bounds),
            "name": name,
            "n_faces": int(grid.n_face),
        },
        inputs=(grid_fingerprint,),
    )
    cache_path = layout.cache_geometry / f"extent_{fingerprint[:24]}.nc"
    faces = _faces_in_bounds(_visible_faces(grid), bounds)
    cached_faces = _load_or_write_faces(cache_path, faces, fingerprint, bounds, guard)
    return ExtentWindow(index, name, bounds, cached_faces, fingerprint)


def _visible_faces(
    grid: UgridGrid,
) -> tuple[_Array, _Array, _Array, _Array]:
    connectivity = np.asarray(grid.face_node_connectivity, dtype=np.int64)
    if connectivity.ndim != 2 or connectivity.shape[0] != grid.n_face:
        raise ExtentSetError("Grid face-node connectivity has an invalid shape.")
    node_coordinates = np.asarray(grid.node_coordinates, dtype=np.float64)
    try:
        valid_nodes = valid_face_nodes(connectivity, grid.fill_value)
    except ValueError as error:
        raise ExtentSetError(str(error)) from error
    counts = np.count_nonzero(valid_nodes, axis=1)
    if np.any(counts < 3):
        raise ExtentSetError("Grid face-node connectivity contains invalid faces.")
    if np.any(connectivity[valid_nodes] >= node_coordinates.shape[0]):
        raise ExtentSetError("Grid face-node connectivity indexes beyond the nodes.")
    safe_indices = np.where(valid_nodes, connectivity, 0)
    face_nodes = node_coordinates[safe_indices]
    face_min_x = np.min(np.where(valid_nodes, face_nodes[:, :, 0], np.inf), axis=1)
    face_max_x = np.max(np.where(valid_nodes, face_nodes[:, :, 0], -np.inf), axis=1)
    face_min_y = np.min(np.where(valid_nodes, face_nodes[:, :, 1], np.inf), axis=1)
    face_max_y = np.max(np.where(valid_nodes, face_nodes[:, :, 1], -np.inf), axis=1)
    return face_min_x, face_max_x, face_min_y, face_max_y


def valid_face_nodes(connectivity: _Array, fill_value: int) -> _Array:
    """Mask both the declared fill value and inspection's normalized -1 padding."""
    values = np.asarray(connectivity, dtype=np.int64)
    padding = (values == fill_value) | (values == -1)
    if np.any((values < 0) & ~padding):
        raise ValueError("Grid face-node connectivity contains invalid node indices.")
    return ~padding


def _faces_in_bounds(
    face_bounds: tuple[_Array, _Array, _Array, _Array],
    extent: Extent,
) -> _Array:
    xmin, xmax, ymin, ymax = extent
    face_min_x, face_max_x, face_min_y, face_max_y = face_bounds
    selected = (
        (face_max_x >= xmin)
        & (face_min_x <= xmax)
        & (face_max_y >= ymin)
        & (face_min_y <= ymax)
    )
    return np.flatnonzero(selected).astype(np.int32, copy=False)


def _river_windows(
    path: Path,
    half_width_x: float,
    half_width_y: float,
) -> list[tuple[str, Extent, bool]]:
    try:
        points = read_river_km_points(path)
    except RiverKmFileError as error:
        raise ExtentSetError(str(error), code="V-RKM-001") from error
    return [
        (
            f"river_km_{point.label}_{point.km_text}",
            (
                point.x - half_width_x,
                point.x + half_width_x,
                point.y - half_width_y,
                point.y + half_width_y,
            ),
            False,
        )
        for point in points
    ]


def _load_or_write_faces(
    path: Path,
    faces: _Array,
    fingerprint: str,
    bounds: Extent,
    guard: OutputWriteGuard,
) -> _Array:
    if path.is_file():
        with netCDF4.Dataset(path, mode="r") as dataset:
            stored_fingerprint = getattr(dataset, "d3dplot_fingerprint", None)
            if stored_fingerprint == fingerprint and "faces" in dataset.variables:
                cached = np.asarray(dataset.variables["faces"][:], dtype=np.int64)
                if cached.ndim == 1 and np.all(cached >= 0):
                    return cached.astype(np.int32, copy=False)

    with atomic_path(path, guard) as temporary:
        with netCDF4.Dataset(temporary, mode="w") as dataset:
            dataset.createDimension("face_index", int(faces.size))
            values = dataset.createVariable("faces", "i4", ("face_index",))
            values[:] = faces
            dataset.d3dplot_fingerprint = fingerprint
            dataset.d3dplot_parameters = canonical_json(
                {"bounds": list(bounds), "visible_face_count": int(faces.size)}
            )
    return faces


def open_ugrid(path: Path) -> UgridDataset:
    """Open one inspected UGRID cache without loading model output."""
    try:
        return cast(UgridDataset, xu.open_dataset(path))
    except (OSError, ValueError, TypeError) as error:
        raise ExtentSetError(
            f"Could not read inspected UGRID cache {path}: {error}"
        ) from error
