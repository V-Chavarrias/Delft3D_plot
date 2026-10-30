# SPDX-License-Identifier: GPL-3.0-or-later
"""Build deterministic river-km segment geometry."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import import_module
from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray

from d3dplot.river_km.file import RiverKmPoint
from d3dplot.river_km.providers import RiverKmProvider

FloatArray = NDArray[np.float64]
_shapely_geometry: Any = import_module("shapely.geometry")


class RiverKmGeometryError(ValueError):
    """River-km segment geometry cannot be constructed safely."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "V-RKM-005",
        geometry: RiverKmSegmentGeometry | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.geometry = geometry


@dataclass(frozen=True, slots=True)
class RiverKmSegmentGeometry:
    """Coordinates and segment polygons derived from a river-km point file."""

    provider: str
    centres_km: FloatArray
    edges_km: FloatArray
    segment_lengths_km: FloatArray
    centre_branches: tuple[str, ...]
    edge_branches: tuple[str, ...]
    centres_xy: FloatArray
    edges_xy: FloatArray
    angles_rad: FloatArray
    left_cut_lines: FloatArray
    right_cut_lines: FloatArray
    polygons: FloatArray
    invalid_segment_indices: tuple[int, ...]
    removed_centres_km: tuple[float, ...]


def _validated_centres(centres_km: ArrayLike) -> FloatArray:
    try:
        centres = np.asarray(centres_km, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise RiverKmGeometryError(
            f"River-km centres must be a one-dimensional numeric sequence: {error}"
        ) from error
    if (
        centres.ndim != 1
        or len(centres) < 2
        or not np.isfinite(centres).all()
        or (np.diff(centres) <= 0).any()
    ):
        raise RiverKmGeometryError(
            "River-km centres must contain at least two finite, strictly "
            "increasing, distinct values."
        )
    return centres


def centres_to_edges(centres_km: ArrayLike) -> tuple[FloatArray, FloatArray]:
    """Return Matlab-style centre edges and the associated segment lengths."""
    centres = _validated_centres(centres_km)
    edges = np.concatenate(
        (
            [centres[0] - (centres[1] - centres[0]) / 2],
            (centres[:-1] + centres[1:]) / 2,
            [centres[-1] + (centres[-1] - centres[-2]) / 2],
        )
    )
    return edges, np.diff(edges)


def rebuild_edges(
    centres_km: Sequence[float], segment_lengths_km: Sequence[float]
) -> FloatArray:
    """Rebuild cut edges while retaining original per-centre segment lengths."""
    centres = np.asarray(centres_km, dtype=np.float64)
    lengths = np.asarray(segment_lengths_km, dtype=np.float64)
    if (
        centres.ndim != 1
        or lengths.ndim != 1
        or len(centres) != len(lengths)
        or len(centres) < 2
        or not np.isfinite(centres).all()
        or not np.isfinite(lengths).all()
        or (lengths <= 0).any()
    ):
        raise RiverKmGeometryError("Cannot rebuild edges from invalid centres/lengths.")
    return np.concatenate((centres - lengths / 2, [centres[-1] + lengths[-1] / 2]))


def correct_bend_cutoff(
    km: float,
    centre_km: float,
    branch: str,
    provider: RiverKmProvider,
) -> tuple[float, bool]:
    """Apply the provider's cutoff mapping to one edge coordinate."""
    tolerance = float(provider.definition.cutoff_tolerance_km)
    for lower, upper in provider.cutoff_intervals(branch):
        if lower + tolerance < km < upper - tolerance:
            if km > centre_km:
                return upper + (km - lower) - provider.ds_pol_km, True
            return lower + (km - upper) + provider.ds_pol_km, True
    return km, False


def _inside_bend_cutoff(km: float, branch: str, provider: RiverKmProvider) -> bool:
    tolerance = float(provider.definition.cutoff_tolerance_km)
    return any(
        lower + tolerance < km < upper - tolerance
        for lower, upper in provider.cutoff_intervals(branch)
    )


def polyline_angles(coordinates: FloatArray) -> FloatArray:
    """Calculate Matlab-compatible centred polyline directions in radians."""
    if (
        coordinates.ndim != 2
        or coordinates.shape[1] != 2
        or coordinates.shape[0] < 2
        or not np.isfinite(coordinates).all()
    ):
        raise RiverKmGeometryError(
            "Polyline coordinates must be finite and have shape (n>=2, 2)."
        )
    if coordinates.shape[0] == 2:
        direction = math.atan2(
            coordinates[1, 1] - coordinates[0, 1],
            coordinates[1, 0] - coordinates[0, 0],
        )
        return np.full(2, direction, dtype=np.float64)
    interior = np.arctan2(
        coordinates[2:, 1] - coordinates[:-2, 1],
        coordinates[2:, 0] - coordinates[:-2, 0],
    )
    return np.concatenate(([interior[0]], interior, [interior[-1]]))


def _expand_branches(
    branches: Sequence[str] | None, count: int
) -> tuple[str, ...] | None:
    if branches is None or not branches:
        return None
    if len(branches) == 1:
        branch = branches[0].strip()
        if branch:
            return (branch,) * count
    if len(branches) == count and all(branch.strip() for branch in branches):
        return tuple(branch.strip() for branch in branches)
    raise RiverKmGeometryError(
        "Generic river-km branches must contain one broadcast branch or one "
        "non-empty branch per requested centre.",
        code="V-RKM-002",
    )


def build_segment_geometry(
    points: Sequence[RiverKmPoint],
    centres_km: Sequence[float],
    *,
    track: str | None = None,
    branches: Sequence[str] | None = None,
    half_width_m: float = 1000.0,
    allow_invalid_segments: bool = False,
) -> RiverKmSegmentGeometry:
    """Build edges, cut lines, and segment polygons for one km set."""
    centres = _validated_centres(centres_km)
    if not math.isfinite(half_width_m) or half_width_m <= 0:
        raise RiverKmGeometryError("half_width_m must be positive and finite.")
    provider = RiverKmProvider.from_points(points)
    explicit_branches = (
        _expand_branches(branches, len(centres)) if provider.name == "generic" else None
    )
    centre_branches = tuple(
        provider.branch_for_km(
            float(km),
            track=track,
            explicit_branch=(
                explicit_branches[index] if explicit_branches is not None else None
            ),
        )
        for index, km in enumerate(centres)
    )
    original_edges, original_lengths = centres_to_edges(centres)

    keep = np.asarray(
        [
            not _inside_bend_cutoff(float(km), branch, provider)
            for km, branch in zip(centres, centre_branches, strict=True)
        ],
        dtype=bool,
    )
    removed_centres = tuple(float(km) for km in centres[~keep])
    retained_centres = centres[keep]
    retained_lengths = original_lengths[keep]
    retained_branches = tuple(
        branch
        for branch, retained in zip(centre_branches, keep, strict=True)
        if retained
    )
    if len(retained_centres) < 2:
        raise RiverKmGeometryError(
            "Bend-cutoff filtering left fewer than two river-km centres."
        )

    edges = rebuild_edges(retained_centres, retained_lengths)
    if provider.name == "generic":
        edge_explicit: tuple[str | None, ...] = tuple(
            retained_branches[min(index, len(retained_branches) - 1)]
            for index in range(len(edges))
        )
    else:
        edge_explicit = (None,) * len(edges)
    edge_branches = tuple(
        provider.branch_for_km(
            float(km),
            track=track,
            explicit_branch=edge_explicit[index],
        )
        for index, km in enumerate(edges)
    )

    corrected_edges = edges.copy()
    for index, (km, branch) in enumerate(zip(edges, edge_branches, strict=True)):
        reference_centre = retained_centres[min(index, len(retained_centres) - 1)]
        corrected_edges[index], _ = correct_bend_cutoff(
            float(km), float(reference_centre), branch, provider
        )

    centres_xy = np.asarray(
        [
            provider.coordinates_at(
                float(km),
                branch,
                allow_extrapolation=True,
            )
            for km, branch in zip(retained_centres, retained_branches, strict=True)
        ],
        dtype=np.float64,
    )
    edges_xy = np.asarray(
        [
            provider.coordinates_at(
                float(km),
                branch,
                allow_extrapolation=True,
            )
            for km, branch in zip(corrected_edges, edge_branches, strict=True)
        ],
        dtype=np.float64,
    )
    angles = polyline_angles(edges_xy)
    normal_x = -np.sin(angles)
    normal_y = np.cos(angles)
    offset = np.column_stack((normal_x, normal_y)) * half_width_m
    left = edges_xy + offset
    right = edges_xy - offset
    polygons = np.stack(
        [
            np.asarray(
                [left[index], left[index + 1], right[index + 1], right[index]],
                dtype=np.float64,
            )
            for index in range(len(retained_centres))
        ]
    )
    invalid_segment_indices = tuple(
        index
        for index, coordinates in enumerate(polygons)
        if not _shapely_geometry.Polygon(coordinates).is_valid
    )
    result = RiverKmSegmentGeometry(
        provider=provider.name,
        centres_km=retained_centres,
        edges_km=corrected_edges,
        segment_lengths_km=retained_lengths,
        centre_branches=retained_branches,
        edge_branches=edge_branches,
        centres_xy=centres_xy,
        edges_xy=edges_xy,
        angles_rad=angles,
        left_cut_lines=left,
        right_cut_lines=right,
        polygons=polygons,
        invalid_segment_indices=invalid_segment_indices,
        removed_centres_km=removed_centres,
    )
    if invalid_segment_indices and not allow_invalid_segments:
        index = invalid_segment_indices[0]
        raise RiverKmGeometryError(
            f"River-km segment {index} is an invalid or self-intersecting polygon.",
            code="V-SMB-010",
            geometry=result,
        )
    return result
