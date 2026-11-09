# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for reading PRF line topology from the inspection grid file."""

from __future__ import annotations

from pathlib import Path

import netCDF4
import numpy as np
import pytest

from d3dplot.simulation import discover_simulation, inspect_simulation
from d3dplot.store import OutputLayout
from d3dplot.variables.line_crossings_cache import (
    LineInputError,
    read_line_topology,
)
from d3dplot.variables.line_sampling import LineTopology


def _write_grid(path: Path, *, include_node_y: bool = True) -> None:
    with netCDF4.Dataset(path, mode="w") as dataset:
        dataset.createDimension("mesh2d_nNodes", 6)
        dataset.createDimension("mesh2d_nFaces", 2)
        dataset.createDimension("mesh2d_nMax_face_nodes", 4)
        node_x = dataset.createVariable("mesh2d_node_x", "f8", ("mesh2d_nNodes",))
        node_x[:] = [0.0, 1.0, 2.0, 0.0, 1.0, 2.0]
        if include_node_y:
            node_y = dataset.createVariable("mesh2d_node_y", "f8", ("mesh2d_nNodes",))
            node_y[:] = [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]
        face_nodes = dataset.createVariable(
            "mesh2d_face_nodes",
            "i8",
            ("mesh2d_nFaces", "mesh2d_nMax_face_nodes"),
            fill_value=-999,
        )
        face_nodes.start_index = np.int32(1)
        # One quad and one triangle, one-based, with the grid file's fill value.
        face_nodes[:] = [[1, 2, 5, 4], [2, 3, 6, -999]]


def test_reads_one_based_connectivity_with_fill(tmp_path: Path) -> None:
    grid_path = tmp_path / "grid.nc"
    _write_grid(grid_path)

    topology = read_line_topology(grid_path, is_geographic=False)

    np.testing.assert_array_equal(topology.face_nodes, [[0, 1, 4, 3], [1, 2, 5, -1]])
    np.testing.assert_array_equal(topology.node_x, [0.0, 1.0, 2.0, 0.0, 1.0, 2.0])
    np.testing.assert_array_equal(topology.node_y, [0.0, 0.0, 0.0, 1.0, 1.0, 1.0])
    assert topology.n_faces == 2
    assert topology.is_geographic is False


def test_geographic_flag_is_part_of_the_fingerprint(tmp_path: Path) -> None:
    grid_path = tmp_path / "grid.nc"
    _write_grid(grid_path)

    planar = read_line_topology(grid_path, is_geographic=False)
    geographic = read_line_topology(grid_path, is_geographic=True)

    assert geographic.is_geographic is True
    assert planar.fingerprint != geographic.fingerprint


def test_missing_node_coordinates_are_reported(tmp_path: Path) -> None:
    grid_path = tmp_path / "grid.nc"
    _write_grid(grid_path, include_node_y=False)

    with pytest.raises(LineInputError, match="mesh2d_node_y"):
        read_line_topology(grid_path, is_geographic=False)


@pytest.mark.integration
def test_inspection_topology_reproduces_grid_face_areas(
    synthetic_case: Path,
    tmp_path: Path,
) -> None:
    simulation, discovery_report = discover_simulation(
        synthetic_case / "simulations" / "r001"
    )
    assert simulation is not None, discovery_report.to_text()
    result, report = inspect_simulation(
        simulation,
        OutputLayout(tmp_path / "d3dplot"),
        crs=None,
        forbidden_roots=(),
    )
    assert result is not None, report.to_text()

    grid_path = result.grid_paths[0]
    topology = read_line_topology(grid_path, is_geographic=False)
    with netCDF4.Dataset(grid_path, mode="r") as dataset:
        expected_areas = np.asarray(
            dataset.variables["mesh2d_flowelem_ba"][:], dtype=np.float64
        )

    assert topology.n_faces == expected_areas.size
    # Areas agree to about 1e-7 relative; a wrong index base would be far off.
    np.testing.assert_allclose(
        _polygon_areas(topology), expected_areas, rtol=1e-6, atol=0.0
    )


def _polygon_areas(topology: LineTopology) -> np.ndarray:
    areas = np.empty(topology.n_faces, dtype=np.float64)
    for index, nodes in enumerate(topology.face_nodes):
        valid = nodes[nodes >= 0]
        x = topology.node_x[valid]
        y = topology.node_y[valid]
        areas[index] = 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))
    return areas
