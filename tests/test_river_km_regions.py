# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for region geometry and river-km segment assignment."""

from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import Polygon, box

from d3dplot.river_km.file import RiverKmPoint, read_river_km_points
from d3dplot.river_km.geometry import build_segment_geometry
from d3dplot.river_km.regions import (
    RegionAssignmentError,
    RegionPolygon,
    RegionPolygonError,
    assign_faces_to_segments,
    read_region_polygon,
)


def _two_segments():
    points = tuple(
        RiverKmPoint(
            x=1000.0 * km,
            y=0.0,
            label=f"{km}_SY",
            km=float(km),
            km_text=str(km),
        )
        for km in range(3)
    )
    return build_segment_geometry(points, [0.5, 1.5], half_width_m=10.0)


def _region(geometry: Polygon) -> RegionPolygon:
    return RegionPolygon(geometry, "EPSG:28992", "EPSG:28992")


@pytest.mark.unit
def test_region_loading_unions_parts_preserves_holes_and_transforms_crs(
    tmp_path: Path,
) -> None:
    polygon_with_hole = Polygon(
        [(0, 0), (1000, 0), (1000, 1000), (0, 1000)],
        holes=[[(200, 200), (800, 200), (800, 800), (200, 800)]],
    )
    island = Polygon([(2000, 0), (2500, 0), (2500, 500), (2000, 500)])
    path = tmp_path / "regions.shp"
    gpd.GeoDataFrame(
        {"name": ["mainland", "island"]},
        geometry=[polygon_with_hole, island],
        crs="EPSG:3857",
    ).to_file(path)

    region = read_region_polygon(path, target_crs="EPSG:32631")

    assert region.source_crs == "EPSG:3857"
    assert region.crs == "EPSG:32631"
    assert region.geometry.geom_type == "MultiPolygon"
    assert sum(len(part.interiors) for part in region.geometry.geoms) == 1


@pytest.mark.unit
def test_optional_concave_hull_merges_polygon_parts(tmp_path: Path) -> None:
    path = tmp_path / "parts.shp"
    gpd.GeoDataFrame(
        {"name": ["west", "east"]},
        geometry=[
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            Polygon([(3, 0), (4, 0), (4, 1), (3, 1)]),
        ],
        crs="EPSG:28992",
    ).to_file(path)

    region = read_region_polygon(path, concave_hull_ratio=1.0)

    assert region.geometry.geom_type == "Polygon"
    assert region.geometry.area == pytest.approx(4.0)


@pytest.mark.unit
def test_region_loading_reports_unreadable_and_missing_crs(tmp_path: Path) -> None:
    with pytest.raises(RegionPolygonError) as unreadable:
        read_region_polygon(tmp_path / "missing.shp")
    assert unreadable.value.code == "V-SMB-001"

    path = tmp_path / "no_crs.shp"
    with pytest.warns(UserWarning, match="crs.*not provided"):
        gpd.GeoDataFrame(
            {"name": ["region"]},
            geometry=[box(0, 0, 1, 1)],
        ).to_file(path)
    with pytest.raises(RegionPolygonError) as missing_crs:
        read_region_polygon(path)
    assert missing_crs.value.code == "V-SMB-002"


@pytest.mark.unit
def test_membership_includes_boundaries_preserves_holes_and_ties_lower() -> None:
    region_geometry = Polygon(
        [(0, -5), (2000, -5), (2000, 5), (0, 5)],
        holes=[[(1200, -1), (1400, -1), (1400, 1), (1200, 1)]],
    )

    result = assign_faces_to_segments(
        [0, 1000, 1200, 1300, 1500, 2100],
        [0, 0, 0, 0, 0, 0],
        _region(region_geometry),
        _two_segments(),
    )

    np.testing.assert_array_equal(
        result.region_inside,
        [True, True, True, False, True, False],
    )
    np.testing.assert_array_equal(result.segment, [0, 0, 1, -1, 1, -1])
    np.testing.assert_array_equal(result.shared_boundary_face_indices, [1])
    assert result.overlap_face_indices.size == 0
    assert result.uncovered_face_indices.size == 0


@pytest.mark.unit
def test_nonshared_segment_overlap_fails_or_is_reported_with_override() -> None:
    segments = _two_segments()
    overlapping_polygons = np.asarray(
        [
            [[0, -10], [1000, -10], [1000, 10], [0, 10]],
            [[800, -10], [1800, -10], [1800, 10], [800, 10]],
        ],
        dtype=np.float64,
    )
    overlapping_segments = replace(segments, polygons=overlapping_polygons)
    region = _region(box(0, -10, 1800, 10))

    with pytest.raises(RegionAssignmentError) as error:
        assign_faces_to_segments([900], [0], region, overlapping_segments)
    assert error.value.code == "V-SMB-013"

    result = assign_faces_to_segments(
        [900],
        [0],
        region,
        overlapping_segments,
        allow_invalid_segments=True,
    )
    np.testing.assert_array_equal(result.segment, [0])
    np.testing.assert_array_equal(result.overlap_face_indices, [0])


@pytest.mark.unit
def test_uncovered_region_faces_are_returned_for_diagnostics() -> None:
    result = assign_faces_to_segments(
        [1000, 2500],
        [15, 0],
        _region(box(0, -20, 3000, 20)),
        _two_segments(),
    )

    np.testing.assert_array_equal(result.uncovered_face_indices, [0])


@pytest.mark.integration
def test_synthetic_segment_assignment_matches_cells_per_segment_250m(
    synthetic_case: Path,
) -> None:
    from expected import EXACT_REGIONS, SEGMENT_LENGTH
    from mesh import make_centreline, make_mesh
    from params import Params

    params = Params()
    mesh = make_mesh(params, make_centreline(params))
    n_segments = int(round(params.length / SEGMENT_LENGTH))
    centres_km = params.km0 + (np.arange(n_segments, dtype=np.float64) + 0.5) * (
        SEGMENT_LENGTH / 1000.0
    )
    # This still contains every synthetic region but avoids self-intersections
    # where the synthetic meander radius is smaller than the default half-width.
    segments = build_segment_geometry(
        read_river_km_points(synthetic_case / "inputs" / "rkm" / "rkm.csv"),
        centres_km,
        half_width_m=750.0,
    )

    with (synthetic_case / "expected" / "cells_per_segment_250m.csv").open(
        newline="", encoding="utf-8"
    ) as file:
        expected_rows = {
            (row["region"], int(row["segment"])): [
                int(global_number) - 1 for global_number in row["globalnr"].split()
            ]
            for row in csv.DictReader(file)
        }

    for region_name in EXACT_REGIONS:
        region = read_region_polygon(
            synthetic_case / "inputs" / "regions" / f"{region_name}.shp",
            target_crs="EPSG:28992",
        )
        assignment = assign_faces_to_segments(
            mesh.face_x,
            mesh.face_y,
            region,
            segments,
        )
        for segment_index in range(n_segments):
            assigned_faces = np.flatnonzero(assignment.segment == segment_index)
            assert (
                assigned_faces.tolist() == expected_rows[(region_name, segment_index)]
            )
