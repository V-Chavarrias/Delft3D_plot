# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for output topology inspection and merge-plan caches."""

from __future__ import annotations

import csv
import math
from contextlib import redirect_stdout
from datetime import UTC, datetime
from importlib import import_module
from io import StringIO
from pathlib import Path
from shutil import copytree

import netCDF4
import numpy as np
import pytest

from d3dplot.simulation import (
    Simulation,
    TimeAxis,
    discover_simulation,
    inspect_simulation,
)
from d3dplot.store import OutputLayout


@pytest.mark.integration
def test_inspection_matches_dfm_tools_and_reuses_cache(
    synthetic_case: Path,
    tmp_path: Path,
) -> None:
    simulation = _discover(synthetic_case / "simulations" / "r001")
    layout = OutputLayout(tmp_path / "d3dplot")

    result, report = inspect_simulation(
        simulation,
        layout,
        crs=None,
        forbidden_roots=(),
    )

    assert result is not None, report.to_text()
    assert report.is_valid
    assert "V-OUT-033" not in {issue.code for issue in report.warnings}
    assert result.reference_subrun == 0
    assert result.plan_count == 1
    assert result.merge_plan_path.is_file()
    assert result.times_path.is_file()
    assert result.grid_paths[0].is_file()
    _assert_time_axis_matches_expected(synthetic_case, result.times_path)
    _assert_values_match_dfm_tools(simulation, result.merge_plan_path)

    reused, reused_report = inspect_simulation(
        simulation,
        layout,
        crs=None,
        forbidden_roots=(),
    )

    assert reused is not None
    assert reused_report.is_valid
    assert reused.cache_reused


@pytest.mark.integration
def test_coordinate_matching_is_reported_for_plain_fm(
    synthetic_case: Path,
    tmp_path: Path,
) -> None:
    simulation = _discover(synthetic_case / "simulations" / "fm_single")

    result, report = inspect_simulation(
        simulation,
        OutputLayout(tmp_path / "d3dplot"),
        crs=None,
        forbidden_roots=(),
    )

    assert result is not None, report.to_text()
    assert result.reference_subrun == 0
    assert "V-OUT-023" in {issue.code for issue in report.warnings}
    with netCDF4.Dataset(result.merge_plan_path) as dataset:
        assert dataset.getncattr("matching") == "coordinates"


@pytest.mark.integration
def test_corrupt_partition_uses_first_time_consistent_reference(
    synthetic_case: Path,
    tmp_path: Path,
) -> None:
    simulation = _discover(
        synthetic_case / "simulations" / "variants" / "corrupt_partition"
    )

    result, report = inspect_simulation(
        simulation,
        OutputLayout(tmp_path / "d3dplot"),
        crs=None,
        forbidden_roots=(),
    )

    assert result is not None
    assert result.reference_subrun == 1
    assert "V-OUT-030" in {issue.code for issue in report.warnings}
    time_axis = TimeAxis.from_cache(result.times_path)
    corrupt_entries = [entry for entry in time_axis.entries if entry.corrupt_partitions]
    assert corrupt_entries
    assert all(1 in entry.corrupt_partitions for entry in corrupt_entries)


@pytest.mark.integration
def test_configured_time_zone_conflict_uses_map_offset(
    synthetic_case: Path,
    tmp_path: Path,
) -> None:
    simulation = _discover(synthetic_case / "simulations" / "r001")

    result, report = inspect_simulation(
        simulation,
        OutputLayout(tmp_path / "d3dplot"),
        crs=None,
        forbidden_roots=(),
        time_zone="+02:00",
    )

    assert result is not None, report.to_text()
    assert "V-TIME-002" in {issue.code for issue in report.warnings}
    assert TimeAxis.from_cache(result.times_path).source_utc_offset == "+01:00"


@pytest.mark.integration
def test_restart_boundary_morphological_mismatch_is_rejected(
    synthetic_case: Path,
    tmp_path: Path,
) -> None:
    simulation_path = tmp_path / "boundary_morph_mismatch"
    copytree(synthetic_case / "simulations" / "r001", simulation_path)
    simulation = _discover(simulation_path)
    for map_file in simulation.subruns[1].map_files:
        with netCDF4.Dataset(map_file, mode="r+") as dataset:
            morph_time = dataset.variables["morft"]
            morph_time[0] = float(morph_time[0]) + 2.0

    result, report = inspect_simulation(
        _discover(simulation_path),
        OutputLayout(tmp_path / "output" / "d3dplot"),
        crs=None,
        forbidden_roots=(),
    )

    assert result is None
    assert "V-OUT-032" in {issue.code for issue in report.errors}


@pytest.mark.integration
def test_restart_boundary_bed_level_mismatch_is_reported(
    synthetic_case: Path,
    tmp_path: Path,
) -> None:
    simulation_path = tmp_path / "boundary_bed_mismatch"
    copytree(synthetic_case / "simulations" / "r001", simulation_path)
    simulation = _discover(simulation_path)
    map_file = simulation.subruns[1].map_files[0]
    with netCDF4.Dataset(map_file, mode="r+") as dataset:
        domains = np.asarray(
            dataset.variables["mesh2d_flowelem_domain"][:],
            dtype=np.int64,
        )
        bed_level = dataset.variables["mesh2d_mor_bl"]
        boundary_values = np.asarray(bed_level[0], dtype=np.float64)
        local_face = next(
            index
            for index, (domain, value) in enumerate(
                zip(domains, boundary_values, strict=True)
            )
            if domain == 0 and math.isfinite(float(value))
        )
        bed_level[0, local_face] = boundary_values[local_face] + 0.1

    result, report = inspect_simulation(
        _discover(simulation_path),
        OutputLayout(tmp_path / "output" / "d3dplot"),
        crs=None,
        forbidden_roots=(),
    )

    assert result is not None, report.to_text()
    assert "V-OUT-033" in {issue.code for issue in report.warnings}


@pytest.mark.integration
def test_invalid_owned_global_number_is_rejected(
    synthetic_case: Path,
    tmp_path: Path,
) -> None:
    simulation_path = tmp_path / "invalid_identity"
    copytree(synthetic_case / "simulations" / "r001", simulation_path)
    map_file = simulation_path / "output" / "0" / "results" / "dflowfm_0000_map.nc"
    with netCDF4.Dataset(map_file, mode="r+") as dataset:
        domains = dataset.variables["mesh2d_flowelem_domain"][:]
        owned_face = next(
            index for index, domain in enumerate(domains) if int(domain) == 0
        )
        dataset.variables["mesh2d_flowelem_globalnr"][owned_face] = 0

    simulation = _discover(simulation_path)
    result, report = inspect_simulation(
        simulation,
        OutputLayout(tmp_path / "output" / "d3dplot"),
        crs=None,
        forbidden_roots=(),
    )

    assert result is None
    assert "V-OUT-022" in {issue.code for issue in report.errors}


@pytest.mark.integration
def test_changed_topology_creates_a_second_merge_plan(
    synthetic_case: Path,
    tmp_path: Path,
) -> None:
    simulation_path = tmp_path / "changed_topology"
    copytree(synthetic_case / "simulations" / "r001", simulation_path)
    changed_map = simulation_path / "output" / "1" / "results" / "dflowfm_0000_map.nc"
    with netCDF4.Dataset(changed_map, mode="r+") as dataset:
        domains = dataset.variables["mesh2d_flowelem_domain"][:]
        ghost_face = next(
            index for index, domain in enumerate(domains) if int(domain) != 0
        )
        global_numbers = dataset.variables["mesh2d_flowelem_globalnr"]
        global_numbers[ghost_face] = int(global_numbers[ghost_face]) + 10_000_000

    simulation = _discover(simulation_path)
    result, report = inspect_simulation(
        simulation,
        OutputLayout(tmp_path / "output" / "d3dplot"),
        crs=None,
        forbidden_roots=(),
    )

    assert result is not None
    assert result.plan_count == 2
    assert "V-OUT-021" in {issue.code for issue in report.warnings}


def _discover(path: Path) -> Simulation:
    simulation, report = discover_simulation(path)

    assert simulation is not None
    assert report.is_valid
    return simulation


def _assert_time_axis_matches_expected(
    synthetic_case: Path,
    time_cache_path: Path,
) -> None:
    with (synthetic_case / "expected" / "time_axis.csv").open(
        encoding="utf-8",
        newline="",
    ) as stream:
        expected = list(csv.DictReader(stream))

    time_axis = TimeAxis.from_cache(time_cache_path)
    assert len(expected) == len(time_axis.entries) == 9
    assert time_axis.source_utc_offset == "+01:00"
    assert time_axis.morphology_available
    for entry, row in zip(time_axis.entries, expected, strict=True):
        assert entry.time_index == int(row["time_index"])
        assert entry.subrun == int(row["subrun"])
        assert entry.local_index == int(row["local_index"])
        assert entry.time == datetime.fromisoformat(row["time"]).astimezone(UTC)
        assert entry.morph_time == datetime.fromisoformat(row["morph_time"]).astimezone(
            UTC
        )
        assert not entry.corrupt_partitions


def _assert_values_match_dfm_tools(
    simulation: Simulation,
    merge_plan_path: Path,
) -> None:
    numpy = import_module("numpy")
    subrun = simulation.subruns[0]
    with netCDF4.Dataset(merge_plan_path) as plan:
        offsets = [int(value) for value in plan.variables["partition_offset"][0]]
        counts = [int(value) for value in plan.variables["partition_n_owned"][0]]
        owned_local = [int(value) for value in plan.variables["owned_local"][0]]
        global_positions = [int(value) for value in plan.variables["global_pos"][0]]
        n_faces = int(plan.getncattr("n_faces"))

    scattered = numpy.full(n_faces, numpy.nan, dtype=numpy.float64)
    for partition, map_file in enumerate(subrun.map_files):
        start = offsets[partition]
        stop = start + counts[partition]
        local_faces = owned_local[start:stop]
        merged_faces = global_positions[start:stop]
        with netCDF4.Dataset(map_file) as dataset:
            variable = dataset.variables["mesh2d_s1"]
            values = numpy.asarray(
                numpy.ma.filled(variable[0, local_faces], numpy.nan),
                dtype=numpy.float64,
            )
        scattered[merged_faces] = values

    helper = import_module("dfm_tools.xugrid_helpers")
    pattern = str(subrun.map_files[0].parent / "dflowfm_*_map.nc")
    with redirect_stdout(StringIO()):
        merged = helper.open_partitioned_dataset(
            pattern,
            drop_variables=["morft"],
            decode_times=False,
        )
    try:
        reference = numpy.asarray(
            merged.obj["mesh2d_s1"].isel(time=0).values,
            dtype=numpy.float64,
        )
    finally:
        merged.close()
    numpy.testing.assert_allclose(
        scattered,
        reference,
        rtol=0,
        atol=0,
        equal_nan=True,
    )
