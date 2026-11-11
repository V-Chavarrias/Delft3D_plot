# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for map1d branch-chain geometry and raw reads."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import netCDF4
import numpy as np
import pytest

from d3dplot.analyses.map1d.plot import (
    comparison_values,
    create_all_times_view,
    create_profile_view,
    create_space_time_view,
)
from d3dplot.analyses.map1d.process import (
    Map1DGrid,
    Map1DMapSession,
    map1d_cache_fingerprint,
    map1d_cache_path,
    read_map1d_cache,
    read_map1d_quantity_series,
    read_map1d_river_km_coordinates,
    write_map1d_cache,
)
from d3dplot.analyses.map1d.stage import _axis_limit_pairs, _figure_path
from d3dplot.config import load_config
from d3dplot.config.figure import FigureConfig
from d3dplot.config.project import SimulationConfig
from d3dplot.simulation.time_utils import format_hydrodynamic_timestamp
from d3dplot.store import OutputLayout, initialize_output_root
from d3dplot.variables.registry import get_variable_spec


def _write_map1d_map(path: Path) -> None:
    with netCDF4.Dataset(path, mode="w") as dataset:
        dataset.createDimension("branch", 2)
        dataset.createDimension("branch_name", 8)
        dataset.createDimension("node", 5)
        dataset.createDimension("edge", 3)
        dataset.createDimension("time", 2)
        branch_id = dataset.createVariable(
            "network1d_branch_id", "S1", ("branch", "branch_name")
        )
        branch_id[:] = np.asarray(
            [
                [b"A", b"", b"", b"", b"", b"", b"", b""],
                [b"B", b"", b"", b"", b"", b"", b"", b""],
            ],
            dtype="S1",
        )
        lengths = dataset.createVariable("network1d_edge_length", "f8", ("branch",))
        lengths[:] = [20.0, 30.0]
        for name, values in {
            "mesh1d_node_branch": ("i4", [0, 0, 0, 1, 1]),
            "mesh1d_node_offset": ("f8", [0.0, 10.0, 20.0, 0.0, 15.0]),
            "mesh1d_node_x": ("f8", [0.0, 10.0, 20.0, 20.0, 35.0]),
            "mesh1d_node_y": ("f8", [0.0, 0.0, 0.0, 0.0, 0.0]),
            "mesh1d_edge_branch": ("i4", [0, 0, 1]),
            "mesh1d_edge_offset": ("f8", [5.0, 15.0, 7.5]),
            "mesh1d_edge_x": ("f8", [5.0, 15.0, 27.5]),
            "mesh1d_edge_y": ("f8", [0.0, 0.0, 0.0]),
        }.items():
            dimension = "node" if "node" in name else "edge"
            variable = dataset.createVariable(name, values[0], (dimension,))
            variable[:] = values[1]
        depth = dataset.createVariable("mesh1d_waterdepth", "f8", ("time", "node"))
        depth[:] = [[1, 2, 3, 4, 5], [6, 7, 8, 9, 10]]
        bed_level = dataset.createVariable("mesh1d_flowelem_bl", "f8", ("node",))
        bed_level[:] = [100, 101, 102, 103, 104]
        discharge = dataset.createVariable("mesh1d_q1", "f8", ("time", "edge"))
        discharge[:] = [[10, 20, 30], [40, 50, 60]]


@pytest.mark.unit
def test_map1d_branch_chain_preserves_order_and_offsets(tmp_path: Path) -> None:
    path = tmp_path / "map1d_map.nc"
    _write_map1d_map(path)

    grid = Map1DGrid.from_map(path)
    chain = grid.chain("ab", ["A", "B"])

    np.testing.assert_array_equal(chain.indices, [0, 1, 2, 4])
    np.testing.assert_allclose(chain.offsets_m, [0, 10, 20, 35])
    np.testing.assert_allclose(chain.x, [0, 10, 20, 35])


@pytest.mark.unit
def test_map1d_map_session_reads_alias_quantity_at_time(tmp_path: Path) -> None:
    path = tmp_path / "map1d_map.nc"
    _write_map1d_map(path)

    with netCDF4.Dataset(path, mode="r") as dataset:
        session = Map1DMapSession(
            dataset,
            time_index=1,
            indices=np.asarray([0, 2, 4], dtype=np.int64),
        )
        result = session.read(
            get_variable_spec("h"),
            local_index=1,
        )

    np.testing.assert_allclose(result.values, [6, 8, 10])


@pytest.mark.unit
def test_map1d_quantity_series_uses_quantity_engine(tmp_path: Path) -> None:
    path = tmp_path / "map1d_map.nc"
    _write_map1d_map(path)
    grid = Map1DGrid.from_map(path)
    chain = grid.chain("a", ["A"])

    values = read_map1d_quantity_series(path, "h", chain, (0, 1))

    np.testing.assert_allclose(values, [[1, 2, 3], [6, 7, 8]])


@pytest.mark.unit
def test_map1d_quantity_series_reads_raw_edge_variable(tmp_path: Path) -> None:
    path = tmp_path / "map1d_map.nc"
    _write_map1d_map(path)
    grid = Map1DGrid.from_map(path)
    chain = grid.chain("a", ["A"], location="edge")

    values = read_map1d_quantity_series(path, "mesh1d_q1", chain, (0, 1))

    np.testing.assert_allclose(values, [[10, 20], [40, 50]])


@pytest.mark.unit
def test_map1d_quantity_series_reads_bed_level_over_time(tmp_path: Path) -> None:
    path = tmp_path / "map1d_map.nc"
    _write_map1d_map(path)
    grid = Map1DGrid.from_map(path)
    chain = grid.chain("a", ["A"])

    values = read_map1d_quantity_series(path, "bed_level", chain, (0, 1))

    np.testing.assert_allclose(values, [[100, 101, 102], [100, 101, 102]])


@pytest.mark.unit
def test_map1d_river_km_coordinates_use_nearest_branch_points(tmp_path: Path) -> None:
    path = tmp_path / "river_km.csv"
    path.write_text(
        "x,y,label,km\n0,0,river_A,10\n10,0,river_A,11\n20,0,river_A,12\n",
        encoding="utf-8",
    )
    map_path = tmp_path / "map1d_map.nc"
    _write_map1d_map(map_path)
    chain = Map1DGrid.from_map(map_path).chain("a", ["A"])

    coordinates = read_map1d_river_km_coordinates(
        chain,
        path,
        tolerance_m=0.1,
    )

    np.testing.assert_allclose(coordinates, [10, 11, 12])


@pytest.mark.unit
def test_map1d_yaml_testcase_loads() -> None:
    config = load_config(
        Path(__file__).parents[1] / "examples" / "map1d_riverlab_r003.yml"
    )

    analysis = config.analyses[0]
    assert analysis.analysis == "map1d"
    assert analysis.variables == ["h", "bed_level"]
    assert analysis.views == ["space_time"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("variable", "canonical_name"),
    (("h", "water_depth"), ("bed_level", "bed_level")),
)
def test_map1d_output_paths_use_canonical_variable_names(
    tmp_path: Path,
    variable: str,
    canonical_name: str,
) -> None:
    layout = OutputLayout(tmp_path / "output")
    simulation = SimulationConfig(path=tmp_path, label="RTC")
    time = datetime(2021, 1, 1, tzinfo=UTC)

    figure_path = _figure_path(
        layout,
        simulation,
        0,
        variable,
        "w",
        "space_time",
        "value",
        time,
    )
    cache_path = map1d_cache_path(
        layout,
        variable=variable,
        chain_name="w",
        time=time,
    )

    assert figure_path.parents[3].name == canonical_name
    assert figure_path.name == "20210101T000000_l01.png"
    assert cache_path.parent.parent.name == canonical_name


@pytest.mark.unit
def test_map1d_axis_limit_sets_are_pairwise() -> None:
    limits = _axis_limit_pairs(
        FigureConfig(
            x_limits=[(0, 10), (20, 30)],
            y_limits=[(-1, 1), (-2, 2)],
        ),
        "bed_level",
        "value",
    )

    assert limits == (((0, 10), (-1, 1)), ((20, 30), (-2, 2)))


@pytest.mark.unit
def test_cache_timestamp_format_omits_zero_fraction() -> None:
    assert (
        format_hydrodynamic_timestamp(datetime(2021, 1, 1, tzinfo=UTC))
        == "20210101T000000"
    )
    assert (
        format_hydrodynamic_timestamp(datetime(2021, 1, 1, 0, 0, 0, 418435, tzinfo=UTC))
        == "20210101T000000.418435"
    )


@pytest.mark.unit
def test_map1d_plot_views_use_shared_templates(tmp_path: Path) -> None:
    path = tmp_path / "map1d_map.nc"
    _write_map1d_map(path)
    grid = Map1DGrid.from_map(path)
    chain = grid.chain("a", ["A"])
    values = np.asarray([[1, 2, 3], [4, 5, 6]], dtype=np.float64)
    times = (
        datetime(2021, 1, 1, tzinfo=UTC),
        datetime(2021, 1, 2, tzinfo=UTC),
    )
    style = FigureConfig()

    profile = create_profile_view(
        chain, values, variable="h", figure_config=style, title="h"
    )
    all_times = create_all_times_view(
        chain, values, times, figure_config=style, title="h"
    )
    space_time = create_space_time_view(
        chain, values, times, figure_config=style, title="h"
    )

    profile.update(chain.offsets_m, values[1], title="updated")
    profile.add_overlay(chain.offsets_m, values[0], "Measurements")
    assert len(profile.axes.lines) == 2
    assert len(all_times.artists) == 2
    assert space_time.mesh.get_array().size == len(chain.indices) * len(times)
    assert space_time.axes.yaxis.get_major_formatter().__class__.__name__ == (
        "ConciseDateFormatter"
    )
    np.testing.assert_allclose(
        comparison_values(values, "difference_time"),
        [[0, 0, 0], [3, 3, 3]],
    )
    reference = values - 1
    np.testing.assert_allclose(
        comparison_values(values, "difference_simulation", reference_values=reference),
        np.ones_like(values),
    )
    np.testing.assert_allclose(
        comparison_values(
            values,
            "difference_simulation_time",
            reference_values=reference,
        ),
        np.zeros_like(values),
    )
    relative = comparison_values(
        values,
        "difference_simulation_relative",
        reference_values=reference,
    )
    assert np.isnan(relative[0, 0])
    np.testing.assert_allclose(relative[1:], 100.0 / reference[1:])


@pytest.mark.unit
def test_map1d_cache_round_trip_is_guarded(tmp_path: Path) -> None:
    path = tmp_path / "map1d_map.nc"
    _write_map1d_map(path)
    grid = Map1DGrid.from_map(path)
    chain = grid.chain("a", ["A"])
    layout = OutputLayout(tmp_path / "output")
    guard = initialize_output_root(layout, tool_version="test", forbidden_roots=())
    cache_path = map1d_cache_path(
        layout,
        variable="h",
        chain_name=chain.name,
        time=datetime(2021, 1, 1, tzinfo=UTC),
    )
    time = datetime(2021, 1, 1, tzinfo=UTC)
    assert map1d_cache_fingerprint(
        input_fingerprint="input",
        variable="h",
        chain=chain,
        time=time,
    ) == map1d_cache_fingerprint(
        input_fingerprint="input",
        variable="water_depth",
        chain=chain,
        time=time,
    )
    write_map1d_cache(
        cache_path,
        guard,
        values=np.asarray([1.0, 2.0, 3.0]),
        chain=chain,
        variable="h",
        time=time,
        fingerprint="fingerprint",
    )

    values = read_map1d_cache(
        cache_path,
        expected_fingerprint="fingerprint",
        expected_size=3,
    )

    np.testing.assert_allclose(values, [1.0, 2.0, 3.0])
