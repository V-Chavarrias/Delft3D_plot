# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for river-km providers and segment geometry."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from d3dplot.river_km.file import (
    RiverKmFileError,
    RiverKmPoint,
    read_river_km_points,
)
from d3dplot.river_km.geometry import (
    RiverKmGeometryError,
    build_segment_geometry,
    centres_to_edges,
    correct_bend_cutoff,
    rebuild_edges,
)
from d3dplot.river_km.providers import RiverKmProvider, RiverKmProviderError


def _points(*branches: str) -> tuple[RiverKmPoint, ...]:
    return tuple(
        RiverKmPoint(
            x=float(index),
            y=0.0,
            label=branch,
            km=0.0,
            km_text="0",
        )
        for index, branch in enumerate(branches)
    )


def _axis_points(branch: str, km_values: range) -> tuple[RiverKmPoint, ...]:
    return tuple(
        RiverKmPoint(
            x=1000.0 * km,
            y=0.0,
            label=f"{km}_{branch}",
            km=float(km),
            km_text=str(km),
        )
        for km in km_values
    )


def test_reader_extracts_branch_after_first_underscore(tmp_path: Path) -> None:
    path = tmp_path / "river.csv"
    path.write_text(
        "X,Y,MODELKM,KM\n1,2,3.00_MA,3.0\n4,5,WA,4.0\n",
        encoding="utf-8",
    )

    points = read_river_km_points(path)

    assert [point.branch for point in points] == ["MA", "WA"]
    assert points[0].km_text == "3.0"


def test_reader_rejects_header_without_four_columns(tmp_path: Path) -> None:
    path = tmp_path / "invalid.csv"
    path.write_text("X,Y\n1,2,A,3\n", encoding="utf-8")

    with pytest.raises(RiverKmFileError, match="header needs"):
        read_river_km_points(path)


@pytest.mark.parametrize(
    ("km", "expected"),
    [
        (960.16, "WA"),
        (960.15, "WA"),
        (952.85, "WA"),
        (867.00, "BR"),
        (852.90, "Rhein"),
    ],
)
def test_wa_track_uses_available_rijntakken_branches(km: float, expected: str) -> None:
    provider = RiverKmProvider.from_points(_points("WA", "BO", "NI", "BR", "Rhein"))

    assert provider.name == "rijntakken"
    assert provider.branch_for_km(km, track="wa") == expected


def test_wa_track_uses_matlab_default_waal_branch() -> None:
    provider = RiverKmProvider.from_points(_points("WA"))

    assert provider.branch_for_km(960.2, track="WA") == "WA"


def test_rijntakken_track_selects_provider_before_file_tags() -> None:
    provider = RiverKmProvider.from_points(_points("OTHER"), track="WA")

    assert provider.name == "rijntakken"
    assert provider.branch_for_km(900.0, track="WA") == "WA"


@pytest.mark.parametrize(
    ("track", "km", "expected"),
    [
        ("IJ", 878.06, "IJ"),
        ("IJ", 878.05, "PK"),
        ("IJ", 867.05, "BR"),
        ("IJ", 852.9, "Rhein"),
        ("LE", 929.36, "LE"),
        ("NR", 929.35, "NR"),
        ("NR", 878.08, "PK"),
        ("PK", 867.05, "PK"),
    ],
)
def test_other_rijntakken_track_tables(track: str, km: float, expected: str) -> None:
    provider = RiverKmProvider.from_points(
        _points("IJ", "PK", "BR", "Rhein", "LE", "NR")
    )

    assert provider.branch_for_km(km, track=track) == expected


def test_rijntakken_requires_a_track_and_checks_pk_range() -> None:
    provider = RiverKmProvider.from_points(_points("PK"))

    with pytest.raises(RiverKmProviderError) as missing_track:
        provider.branch_for_km(870.0)
    assert missing_track.value.code == "V-RKM-002"

    with pytest.raises(RiverKmProviderError) as outside_range:
        provider.branch_for_km(867.049, track="PK")
    assert outside_range.value.code == "V-RKM-004"


def test_maas_branch_switch_uses_configured_track() -> None:
    with_bm = RiverKmProvider.from_points(_points("ma", "bm"))
    without_bm = RiverKmProvider.from_points(_points("MA"))
    forced = RiverKmProvider.from_points(_points("OTHER"), track="MA")

    assert with_bm.name == "maas"
    assert with_bm.branch_for_km(230.0) == "MA"
    assert with_bm.branch_for_km(230.25) == "BM"
    assert without_bm.branch_for_km(230.25) == "BM"
    assert forced.name == "maas"
    assert forced.branch_for_km(230.25) == "BM"


def test_mixed_recognized_river_tags_are_rejected() -> None:
    with pytest.raises(RiverKmProviderError) as error:
        RiverKmProvider.from_points(_points("MA", "WA"))

    assert error.value.code == "V-RKM-002"


def test_generic_multiple_branches_need_an_explicit_choice() -> None:
    provider = RiverKmProvider.from_points(_points("NORTH", "SOUTH"))

    with pytest.raises(RiverKmProviderError) as error:
        provider.branch_for_km(1.0)

    assert error.value.code == "V-RKM-002"
    assert provider.branch_for_km(1.0, explicit_branch="south") == "south"


def test_duplicate_km_is_rejected_after_case_and_alias_normalization() -> None:
    points = (
        RiverKmPoint(0.0, 0.0, "0_RH", 1.0, "1"),
        RiverKmPoint(1.0, 0.0, "1_rhein", 1.0, "1"),
    )

    with pytest.raises(RiverKmProviderError) as error:
        RiverKmProvider.from_points(points)

    assert error.value.code == "V-RKM-003"


def test_branch_coordinates_interpolate_without_extrapolation() -> None:
    points = (
        RiverKmPoint(0.0, 0.0, "A", 0.0, "0"),
        RiverKmPoint(20.0, 10.0, "A", 2.0, "2"),
    )
    provider = RiverKmProvider.from_points(points)

    assert provider.coordinates_at(1.0, "a") == (10.0, 5.0)
    with pytest.raises(RiverKmProviderError) as error:
        provider.coordinates_at(2.01, "A")
    assert error.value.code == "V-RKM-004"


def test_branch_coordinates_can_match_matlab_endpoint_extrapolation() -> None:
    points = (
        RiverKmPoint(0.0, 0.0, "A", 0.0, "0"),
        RiverKmPoint(20.0, 10.0, "A", 2.0, "2"),
    )
    provider = RiverKmProvider.from_points(points)

    np.testing.assert_allclose(
        provider.coordinates_at(2.01, "A", allow_extrapolation=True),
        (20.1, 10.05),
    )


@given(
    st.lists(
        st.floats(
            min_value=0.1, max_value=100.0, allow_nan=False, allow_infinity=False
        ),
        min_size=1,
        max_size=8,
    )
)
def test_irregular_centres_keep_matlab_edge_lengths(spacings: list[float]) -> None:
    centres = np.concatenate(([0.0], np.cumsum(spacings)))

    original_edges, lengths = centres_to_edges(centres)
    rebuilt = rebuild_edges(centres, lengths)

    assert original_edges.shape == (len(centres) + 1,)
    np.testing.assert_allclose(lengths, np.diff(original_edges))
    np.testing.assert_allclose(rebuilt[:-1], centres - lengths / 2)
    assert rebuilt[-1] == pytest.approx(centres[-1] + lengths[-1] / 2)


def test_cutoff_edge_mapping_and_tolerance() -> None:
    provider = RiverKmProvider.from_points(_axis_points("IJ", range(880, 912)))

    mapped_right, right_cut = correct_bend_cutoff(894.0, 893.0, "IJ", provider)
    mapped_left, left_cut = correct_bend_cutoff(894.0, 895.0, "IJ", provider)
    boundary, boundary_cut = correct_bend_cutoff(891.91, 891.0, "IJ", provider)

    assert right_cut and mapped_right == pytest.approx(898.0)
    assert left_cut and mapped_left == pytest.approx(890.0)
    assert not boundary_cut and boundary == pytest.approx(891.91)


def test_cutoff_filtered_geometry_matches_hand_computed_coordinates() -> None:
    geometry = build_segment_geometry(
        _axis_points("IJ", range(880, 916)),
        [890.0, 894.0, 898.0, 906.0, 911.0],
        track="IJ",
        half_width_m=10.0,
    )

    np.testing.assert_allclose(geometry.centres_km, [890.0, 898.0, 911.0])
    np.testing.assert_allclose(geometry.segment_lengths_km, [4.0, 6.0, 5.0])
    np.testing.assert_allclose(geometry.edges_km, [888.0, 891.0, 903.9, 913.5])
    assert geometry.removed_centres_km == (894.0, 906.0)


def test_straight_generic_geometry_has_expected_cut_lines_and_polygons() -> None:
    geometry = build_segment_geometry(
        _axis_points("SY", range(4)),
        [0.5, 1.5, 2.5],
        branches=["SY"],
        half_width_m=10.0,
    )

    np.testing.assert_allclose(geometry.centres_xy, [[500, 0], [1500, 0], [2500, 0]])
    np.testing.assert_allclose(
        geometry.edges_xy, [[0, 0], [1000, 0], [2000, 0], [3000, 0]]
    )
    np.testing.assert_allclose(geometry.left_cut_lines[:, 1], 10.0)
    np.testing.assert_allclose(geometry.right_cut_lines[:, 1], -10.0)
    np.testing.assert_allclose(
        geometry.polygons[0],
        [[0, 10], [1000, 10], [1000, -10], [0, -10]],
    )


def test_generic_per_centre_branches_choose_the_edge_axis() -> None:
    points = (
        *_axis_points("A", range(3)),
        *(
            RiverKmPoint(
                x=1000.0 * km + 100.0,
                y=0.0,
                label=f"{km}_B",
                km=float(km),
                km_text=str(km),
            )
            for km in range(2, 5)
        ),
    )
    geometry = build_segment_geometry(
        points,
        [0.5, 1.5, 2.5],
        branches=["A", "A", "B"],
        half_width_m=10.0,
    )

    assert geometry.centre_branches == ("A", "A", "B")
    assert geometry.edge_branches == ("A", "A", "B", "B")
    np.testing.assert_allclose(geometry.edges_xy[:, 0], [0, 1000, 2100, 3100])


def test_invalid_centre_sets_are_rejected() -> None:
    for centres in ([1.0], [1.0, 1.0], [2.0, 1.0], [0.0, math.inf]):
        with pytest.raises(RiverKmGeometryError) as error:
            centres_to_edges(centres)
        assert error.value.code == "V-RKM-005"


def test_self_intersecting_segment_is_rejected() -> None:
    points = tuple(
        RiverKmPoint(
            x=float(x),
            y=float(y),
            label="A",
            km=float(km),
            km_text=str(km),
        )
        for km, (x, y) in enumerate(((0, 0), (0, 1), (0, 0), (0, 1)))
    )

    with pytest.raises(RiverKmGeometryError) as error:
        build_segment_geometry(
            points, [0.5, 1.5, 2.5], branches=["A"], half_width_m=10.0
        )

    assert error.value.code == "V-SMB-010"
    assert error.value.geometry is not None

    permitted = build_segment_geometry(
        points,
        [0.5, 1.5, 2.5],
        branches=["A"],
        half_width_m=10.0,
        allow_invalid_segments=True,
    )
    assert permitted.invalid_segment_indices


def test_invalid_segment_diagnostic_writes_figure(tmp_path: Path) -> None:
    from d3dplot.analyses.river_km_statistics.diagnostic import (
        write_invalid_segment_diagnostic,
    )
    from d3dplot.config.figure import FigureConfig
    from d3dplot.store import OutputLayout, initialize_output_root

    points = tuple(
        RiverKmPoint(
            x=float(x),
            y=float(y),
            label="A",
            km=float(km),
            km_text=str(km),
        )
        for km, (x, y) in enumerate(((0, 0), (0, 1), (0, 0), (0, 1)))
    )
    with pytest.raises(RiverKmGeometryError) as error:
        build_segment_geometry(
            points, [0.5, 1.5, 2.5], branches=["A"], half_width_m=10.0
        )

    geometry = error.value.geometry
    assert geometry is not None
    layout = OutputLayout(tmp_path / "d3dplot")
    guard = initialize_output_root(layout, tool_version="test")
    output = write_invalid_segment_diagnostic(
        geometry,
        segment_index=geometry.invalid_segment_indices[0],
        region_name="region",
        km_set_name="250m",
        layout=layout,
        guard=guard,
        style=FigureConfig(),
        fingerprint="test",
    )

    assert output.is_file()
