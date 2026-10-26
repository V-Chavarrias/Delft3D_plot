# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for direct face and edge map-file reads."""

from __future__ import annotations

from pathlib import Path

import netCDF4
import numpy as np
import pytest

from d3dplot.simulation import Simulation, discover_simulation, inspect_simulation
from d3dplot.simulation.map_reader import MapReader, MapReaderError
from d3dplot.store import OutputLayout
from d3dplot.variables import (
    VariableKind,
    VariableLocation,
    VariableSpec,
    get_variable_spec,
)


@pytest.mark.integration
def test_reader_scatters_face_and_edge_variables(
    synthetic_case: Path,
    tmp_path: Path,
) -> None:
    simulation = _discover(synthetic_case / "simulations" / "r001")
    inspection, report = inspect_simulation(
        simulation,
        OutputLayout(tmp_path / "d3dplot"),
        forbidden_roots=(),
    )
    assert inspection is not None, report.to_text()

    reader = MapReader(simulation, inspection.merge_plan_path)
    face = reader.read(
        get_variable_spec("water_level"),
        subrun_index=0,
        local_index=0,
    )
    assert face.values.shape == (6406,)
    assert not face.issues
    assert face.edge_orientation is None

    edge = reader.read(
        VariableSpec(
            name="edge_x",
            kind=VariableKind.RAW,
            aliases=(),
            netcdf_candidates=("mesh2d_edge_x",),
            location=VariableLocation.EDGE,
            extra_dims=(),
            units="m",
            label_key="edge_x",
        ),
        subrun_index=0,
        local_index=0,
    )
    assert edge.values.shape == (13222,)
    assert not edge.issues
    assert edge.edge_orientation is not None
    assert set(np.unique(edge.edge_orientation)) <= {-1, 1}
    np.testing.assert_allclose(
        edge.values,
        _merged_edge_x(inspection.grid_paths[0]),
        rtol=0,
        atol=1e-12,
    )


@pytest.mark.integration
def test_reader_transposes_extra_dimensions_and_rejects_unexpected_dimensions(
    synthetic_case: Path,
    tmp_path: Path,
) -> None:
    simulation = _discover(synthetic_case / "simulations" / "r001")
    inspection, report = inspect_simulation(
        simulation,
        OutputLayout(tmp_path / "d3dplot"),
        forbidden_roots=(),
    )
    assert inspection is not None, report.to_text()
    reader = MapReader(simulation, inspection.merge_plan_path)

    layer_fraction = reader.read(
        get_variable_spec("layer_fraction"),
        subrun_index=0,
        local_index=0,
    )
    assert layer_fraction.values.shape == (6406, 2, 3)
    assert not layer_fraction.issues

    invalid_spec = VariableSpec(
        name="invalid_layer_fraction",
        kind=VariableKind.RAW,
        aliases=(),
        netcdf_candidates=("mesh2d_lyrfrac",),
        location=VariableLocation.FACE,
        extra_dims=(),
        units="1",
        label_key="invalid_layer_fraction",
    )
    with pytest.raises(MapReaderError) as error:
        reader.read(
            invalid_spec,
            subrun_index=0,
            local_index=0,
        )
    assert error.value.issue.code == "V-VAR-004"


@pytest.mark.integration
def test_reader_masks_corrupt_partitions_and_reports_implausible_values(
    synthetic_case: Path,
    tmp_path: Path,
) -> None:
    corrupt_simulation = _discover(
        synthetic_case / "simulations" / "variants" / "corrupt_partition"
    )
    inspection, report = inspect_simulation(
        corrupt_simulation,
        OutputLayout(tmp_path / "corrupt" / "d3dplot"),
        forbidden_roots=(),
    )
    assert inspection is not None, report.to_text()
    reader = MapReader(corrupt_simulation, inspection.merge_plan_path)
    masked = reader.read(
        get_variable_spec("water_level"),
        subrun_index=0,
        local_index=1,
        corrupt_partitions=(1,),
    )
    assert "V-TIME-011" in {issue.code for issue in masked.issues}
    assert np.isnan(masked.values).any()
    assert np.isfinite(masked.values).any()

    ordinary_simulation = _discover(synthetic_case / "simulations" / "r001")
    ordinary_inspection, ordinary_report = inspect_simulation(
        ordinary_simulation,
        OutputLayout(tmp_path / "ordinary" / "d3dplot"),
        forbidden_roots=(),
    )
    assert ordinary_inspection is not None, ordinary_report.to_text()
    ordinary_reader = MapReader(
        ordinary_simulation,
        ordinary_inspection.merge_plan_path,
    )
    with netCDF4.Dataset(ordinary_simulation.subruns[2].map_files[0]) as dataset:
        last_index = len(dataset.dimensions["time"]) - 1
    bed_level = ordinary_reader.read(
        get_variable_spec("bed_level"),
        subrun_index=2,
        local_index=last_index,
    )
    assert "V-OUT-031" in {issue.code for issue in bed_level.issues}
    assert np.isnan(bed_level.values).any()


def _discover(path: Path) -> Simulation:
    simulation, report = discover_simulation(path)
    assert simulation is not None
    assert report.is_valid
    return simulation


def _merged_edge_x(grid_path: Path) -> np.ndarray:
    with netCDF4.Dataset(grid_path) as dataset:
        node_x = np.asarray(dataset.variables["mesh2d_node_x"][:], dtype=np.float64)
        edge_variable = dataset.variables["mesh2d_edge_nodes"]
        edge_nodes = np.asarray(edge_variable[:], dtype=np.int64)
        start_index = (
            int(edge_variable.getncattr("start_index"))
            if "start_index" in edge_variable.ncattrs()
            else 0
        )
    edge_nodes -= start_index
    return (node_x[edge_nodes[:, 0]] + node_x[edge_nodes[:, 1]]) / 2
