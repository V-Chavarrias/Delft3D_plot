# SPDX-License-Identifier: GPL-3.0-or-later
"""Region polygon loading and deterministic face-to-segment assignment."""

from __future__ import annotations

import math
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray

from d3dplot.river_km.geometry import RiverKmSegmentGeometry

BooleanArray = NDArray[np.bool_]
IndexArray = NDArray[np.int64]
IntArray = NDArray[np.int32]
_geopandas: Any = import_module("geopandas")
_pyogrio_errors: Any = import_module("pyogrio.errors")
_pyproj_exceptions: Any = import_module("pyproj.exceptions")
_shapely: Any = import_module("shapely")
_shapely_geometry: Any = import_module("shapely.geometry")


class RegionPolygonError(ValueError):
    """Region polygon input is unreadable, invalid, or has an unusable CRS."""

    def __init__(self, message: str, *, code: str = "V-SMB-001") -> None:
        super().__init__(message)
        self.code = code


class RegionAssignmentError(ValueError):
    """Region membership or segment assignment violates its geometry contract."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RegionPolygon:
    """A unioned region geometry, including the CRS used for its coordinates."""

    geometry: Any
    source_crs: str
    crs: str


@dataclass(frozen=True, slots=True)
class RegionSegmentAssignment:
    """Boundary-inclusive region membership and deterministic segment indices."""

    region_inside: BooleanArray
    segment: IntArray
    uncovered_face_indices: IndexArray
    overlap_face_indices: IndexArray
    shared_boundary_face_indices: IndexArray
    invalid_segment_indices: tuple[int, ...]


def _polygon_parts(geometry: Any) -> list[Any]:
    if geometry.geom_type == "Polygon":
        return [geometry]
    if geometry.geom_type == "MultiPolygon":
        return list(geometry.geoms)
    raise RegionPolygonError(
        f"Region geometry must contain only Polygon or MultiPolygon features, "
        f"not {geometry.geom_type}."
    )


def read_region_polygon(
    path: Path,
    *,
    target_crs: str | None = None,
    concave_hull_ratio: float | None = None,
) -> RegionPolygon:
    """Read, union, and optionally transform or hull polygon features."""
    path = Path(path)
    if not path.is_file():
        raise RegionPolygonError(f"Region polygon file does not exist: {path}.")
    try:
        frame = _geopandas.read_file(path, engine="pyogrio")
    except (
        OSError,
        ValueError,
        TypeError,
        _pyogrio_errors.DataSourceError,
    ) as error:
        raise RegionPolygonError(
            f"Could not read region polygon file {path}: {error}"
        ) from error
    if frame.empty:
        raise RegionPolygonError(f"Region polygon file is empty: {path}.")
    if frame.crs is None:
        raise RegionPolygonError(
            f"Region polygon file has no source CRS: {path}.",
            code="V-SMB-002",
        )
    source_crs = frame.crs.to_string()
    if target_crs is not None:
        try:
            frame = frame.to_crs(target_crs)
        except (
            ValueError,
            TypeError,
            _pyproj_exceptions.CRSError,
            _pyproj_exceptions.ProjError,
        ) as error:
            raise RegionPolygonError(
                f"Could not transform region polygon {path} to {target_crs}: {error}",
                code="V-SMB-002",
            ) from error
    if concave_hull_ratio is not None and (
        not math.isfinite(concave_hull_ratio)
        or concave_hull_ratio <= 0
        or concave_hull_ratio > 1
    ):
        raise RegionPolygonError(
            "concave_hull_ratio must be greater than zero and at most one."
        )

    geometries = [
        geometry
        for geometry in frame.geometry
        if geometry is not None and not geometry.is_empty
    ]
    if not geometries:
        raise RegionPolygonError(f"Region polygon file has no geometry: {path}.")
    for geometry in geometries:
        _polygon_parts(geometry)
        if not geometry.is_valid:
            raise RegionPolygonError(
                f"Region polygon file contains invalid geometry: {path}."
            )
    try:
        geometry = _shapely.union_all(geometries)
    except _shapely.errors.GEOSException as error:
        raise RegionPolygonError(
            f"Could not union the region polygons in {path}: {error}"
        ) from error
    if geometry.is_empty or not geometry.is_valid:
        raise RegionPolygonError(f"Region polygon union is empty or invalid: {path}.")
    _polygon_parts(geometry)

    if concave_hull_ratio is not None:
        coordinates = np.unique(_shapely.get_coordinates(geometry), axis=0)
        if len(coordinates) < 3:
            raise RegionPolygonError(
                "Region polygon has too few unique coordinates for a concave "
                f"hull: {path}."
            )
        points = _shapely_geometry.MultiPoint(coordinates)
        try:
            geometry = _shapely.concave_hull(
                points,
                ratio=concave_hull_ratio,
            )
        except _shapely.errors.GEOSException as error:
            raise RegionPolygonError(
                f"Could not build a concave hull for {path}: {error}"
            ) from error
        if (
            geometry.is_empty
            or geometry.geom_type != "Polygon"
            or not geometry.is_valid
        ):
            raise RegionPolygonError(f"Concave hull for {path} is empty or invalid.")

    return RegionPolygon(
        geometry=geometry,
        source_crs=source_crs,
        crs=frame.crs.to_string(),
    )


def _face_coordinates(
    x: ArrayLike, y: ArrayLike
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    try:
        face_x = np.asarray(x, dtype=np.float64)
        face_y = np.asarray(y, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise ValueError("Face-centre coordinates must be numeric arrays.") from error
    if (
        face_x.ndim != 1
        or face_y.ndim != 1
        or face_x.shape != face_y.shape
        or not np.isfinite(face_x).all()
        or not np.isfinite(face_y).all()
    ):
        raise ValueError(
            "Face-centre coordinate arrays must be one-dimensional, finite, "
            "and have equal lengths."
        )
    return face_x, face_y


def _within_km_range(
    x: NDArray[np.float64],
    y: NDArray[np.float64],
    edges_xy: NDArray[np.float64],
) -> BooleanArray:
    if (
        edges_xy.ndim != 2
        or edges_xy.shape[1] != 2
        or len(edges_xy) < 2
        or not np.isfinite(edges_xy).all()
    ):
        raise ValueError("River-km edge coordinates must have shape (n>=2, 2).")
    start_direction = edges_xy[1] - edges_xy[0]
    end_direction = edges_xy[-1] - edges_xy[-2]
    if np.linalg.norm(start_direction) == 0 or np.linalg.norm(end_direction) == 0:
        raise ValueError("River-km endpoint directions must be non-zero.")
    from_start = np.column_stack((x - edges_xy[0, 0], y - edges_xy[0, 1]))
    from_end = np.column_stack((x - edges_xy[-1, 0], y - edges_xy[-1, 1]))
    return np.asarray(
        (from_start @ start_direction >= 0) & (from_end @ end_direction <= 0),
        dtype=np.bool_,
    )


def assign_faces_to_segments(
    face_x: ArrayLike,
    face_y: ArrayLike,
    region: RegionPolygon,
    geometry: RiverKmSegmentGeometry,
    *,
    allow_invalid_segments: bool = False,
) -> RegionSegmentAssignment:
    """Assign region faces to polygons, resolving shared-cut ties to lower indices."""
    x, y = _face_coordinates(face_x, face_y)
    if (
        region.geometry.is_empty
        or region.geometry.geom_type not in {"Polygon", "MultiPolygon"}
        or not region.geometry.is_valid
    ):
        raise RegionAssignmentError(
            "Region geometry must be a non-empty valid Polygon or MultiPolygon.",
            code="V-SMB-001",
        )
    if geometry.polygons.ndim != 3 or geometry.polygons.shape[1:] != (4, 2):
        raise ValueError("Segment polygon coordinates must have shape (n, 4, 2).")
    within_km_range = _within_km_range(x, y, geometry.edges_xy)

    polygons = [
        _shapely_geometry.Polygon(coordinates) for coordinates in geometry.polygons
    ]
    invalid_segments = tuple(
        index for index, polygon in enumerate(polygons) if not polygon.is_valid
    )
    if invalid_segments and not allow_invalid_segments:
        raise RegionAssignmentError(
            f"Invalid river-km segment polygons at indices {invalid_segments}.",
            code="V-SMB-010",
        )

    try:
        region_inside = np.asarray(
            _shapely.intersects_xy(region.geometry, x, y),
            dtype=np.bool_,
        )
        segment_indices = np.full(x.shape, -1, dtype=np.int32)
        nonshared_overlaps: set[int] = set()
        shared_boundaries: set[int] = set()
        for index, polygon in enumerate(polygons):
            in_segment = np.asarray(
                _shapely.intersects_xy(polygon, x, y),
                dtype=np.bool_,
            )
            in_segment &= region_inside
            overlap = in_segment & (segment_indices >= 0)
            if overlap.any():
                shared = np.zeros(x.shape, dtype=np.bool_)
                if index > 0:
                    shared_cut = _shapely_geometry.LineString(
                        [
                            geometry.left_cut_lines[index],
                            geometry.right_cut_lines[index],
                        ]
                    )
                    on_shared_cut = np.asarray(
                        _shapely.intersects_xy(shared_cut, x, y),
                        dtype=np.bool_,
                    )
                    shared = overlap & (segment_indices == index - 1) & on_shared_cut
                shared_boundaries.update(
                    int(face_index) for face_index in np.flatnonzero(shared)
                )
                nonshared = overlap & ~shared
                overlap_indices = np.flatnonzero(nonshared)
                if overlap_indices.size and not allow_invalid_segments:
                    raise RegionAssignmentError(
                        f"{len(overlap_indices)} face(s) are assigned to multiple "
                        f"segments; first face index is {int(overlap_indices[0])}.",
                        code="V-SMB-013",
                    )
                nonshared_overlaps.update(
                    int(face_index) for face_index in overlap_indices
                )
            unassigned = in_segment & (segment_indices < 0)
            segment_indices[unassigned] = index
    except _shapely.errors.GEOSException as error:
        raise RegionAssignmentError(
            f"Could not evaluate region or segment geometry: {error}",
            code="V-SMB-010",
        ) from error

    return RegionSegmentAssignment(
        region_inside=region_inside,
        segment=segment_indices,
        uncovered_face_indices=np.flatnonzero(
            region_inside & within_km_range & (segment_indices < 0)
        ),
        overlap_face_indices=np.asarray(sorted(nonshared_overlaps), dtype=np.int64),
        shared_boundary_face_indices=np.asarray(
            sorted(shared_boundaries), dtype=np.int64
        ),
        invalid_segment_indices=invalid_segments,
    )
