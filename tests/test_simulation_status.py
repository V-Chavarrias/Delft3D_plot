# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for D-Flow FM and SMT simulation-status detection."""

from __future__ import annotations

import csv
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import netCDF4
import pytest

from d3dplot.model_input.mdu import MduInfo
from d3dplot.simulation import (
    SimulationState,
    detect_simulation_status,
    discover_simulation,
    dump_simulation_status_csv,
    dump_simulation_status_json,
    inspect_simulation_status,
    write_simulation_status_csv,
    write_simulation_status_json,
)
from d3dplot.simulation.status import detect_subrun_status


@pytest.mark.integration
@pytest.mark.parametrize(
    ("relative_path", "expected"),
    [
        ("simulations/r001", SimulationState.FINISHED),
        ("simulations/r002", SimulationState.FINISHED),
        ("simulations/variants/crashed", SimulationState.RUNNING),
        ("simulations/variants/corrupt_partition", SimulationState.FINISHED),
        ("simulations/variants/non_partitioned", SimulationState.FINISHED),
        ("simulations/variants/running", SimulationState.RUNNING),
        ("simulations/fm_partitioned", SimulationState.FINISHED),
        ("simulations/fm_single", SimulationState.FINISHED),
    ],
)
def test_status_for_synthetic_variants(
    synthetic_case: Path,
    relative_path: str,
    expected: SimulationState,
) -> None:
    simulation, report = discover_simulation(synthetic_case / relative_path)

    assert simulation is not None
    assert report.is_valid
    assert detect_simulation_status(simulation).state is expected


@pytest.mark.integration
def test_old_incomplete_output_is_stopped(synthetic_case: Path) -> None:
    simulation, report = discover_simulation(
        synthetic_case / "simulations" / "variants" / "crashed"
    )
    assert simulation is not None
    assert report.is_valid
    last_modified = max(
        path.stat().st_mtime
        for subrun in simulation.subruns
        for path in subrun.map_files
    )
    now = datetime.fromtimestamp(last_modified, tz=UTC) + timedelta(hours=2)

    status = detect_simulation_status(simulation, now=now)

    assert status.state is SimulationState.STOPPED
    assert status.subruns[-1].state is SimulationState.STOPPED


@pytest.mark.unit
def test_empty_map_list_is_not_started(tmp_path: Path) -> None:
    status = detect_subrun_status(
        0,
        _mdu(tmp_path, tstop=3600),
        (),
    )

    assert status.state is SimulationState.NOT_STARTED
    assert status.last_time is None


@pytest.mark.unit
def test_path_status_reports_not_started_when_map_output_is_absent(
    tmp_path: Path,
) -> None:
    (tmp_path / "dflowfm.mdu").write_text(
        "[Time]\nRefDate = 20200101\n[Output]\nOutputDir = results\n",
        encoding="utf-8",
    )

    status, report = inspect_simulation_status(tmp_path)

    assert status is not None
    assert status.state is SimulationState.NOT_STARTED
    assert status.subruns[0].state is SimulationState.NOT_STARTED
    assert [issue.code for issue in report.errors] == ["V-OUT-001"]


@pytest.mark.unit
def test_all_partition_dia_files_must_report_finished(tmp_path: Path) -> None:
    map_files = (
        _write_map(tmp_path / "run_0000_map.nc", time_value=3600),
        _write_map(tmp_path / "run_0001_map.nc", time_value=3600),
    )
    (tmp_path / "run_0000.dia").write_text(
        "** INFO   : Computation finished at: 11:00:00, 01-01-2021\n",
        encoding="utf-8",
    )
    mdu = _mdu(tmp_path, tstop=3600)

    status = detect_subrun_status(0, mdu, map_files)

    assert status.state is SimulationState.RUNNING

    (tmp_path / "run_0001.dia").write_text(
        "** INFO   : Computation finished at: 11:00:00, 01-01-2021\n",
        encoding="utf-8",
    )
    status = detect_subrun_status(0, mdu, map_files)

    assert status.state is SimulationState.FINISHED


@pytest.mark.unit
def test_controlled_exit_is_interrupted(tmp_path: Path) -> None:
    map_file = _write_map(tmp_path / "run_map.nc", time_value=3600)
    (tmp_path / "run.dia").write_text(
        "*** ERROR Flow exited abnormally\n",
        encoding="utf-8",
    )

    status = detect_subrun_status(
        0,
        _mdu(tmp_path, tstop=7200),
        (map_file,),
    )

    assert status.state is SimulationState.INTERRUPTED


@pytest.mark.unit
def test_status_reads_only_the_last_four_kib_of_dia(tmp_path: Path) -> None:
    map_file = _write_map(tmp_path / "run_map.nc", time_value=3600)
    (tmp_path / "run.dia").write_bytes(
        b"** INFO   : Computation finished\n" + b"x" * 5000 + b"\n"
    )

    status = detect_subrun_status(
        0,
        _mdu(tmp_path, tstop=3600),
        (map_file,),
    )

    assert status.state is SimulationState.RUNNING


@pytest.mark.unit
def test_elapsed_map_time_is_compared_in_seconds(tmp_path: Path) -> None:
    map_file = _write_map(
        tmp_path / "run_map.nc",
        time_value=60,
        units="minutes since 2021-01-01 00:00:00 +00:00",
    )
    os.utime(map_file, (1, 1))
    now = datetime.fromtimestamp(7201, tz=UTC)

    status = detect_subrun_status(
        0,
        _mdu(tmp_path, tstop=7200),
        (map_file,),
        now=now,
    )

    assert status.state is SimulationState.STOPPED
    assert status.last_time == "2021-01-01T01:00:00+00:00"


@pytest.mark.unit
def test_numeric_stop_uses_elapsed_time_when_cf_origin_differs(
    tmp_path: Path,
) -> None:
    map_file = _write_map(
        tmp_path / "run_map.nc",
        time_value=120,
        units="minutes since 2020-12-31 23:00:00 +00:00",
    )
    os.utime(map_file, (1, 1))
    now = datetime.fromtimestamp(7201, tz=UTC)

    status = detect_subrun_status(
        0,
        _mdu(tmp_path, tstop=7200),
        (map_file,),
        now=now,
    )

    assert status.state is SimulationState.RUNNING
    assert status.last_time == "2021-01-01T01:00:00+00:00"


@pytest.mark.integration
def test_status_serializers_and_guarded_writers(synthetic_case: Path) -> None:
    simulation, report = discover_simulation(
        synthetic_case / "simulations" / "fm_single"
    )
    assert simulation is not None
    assert report.is_valid
    status = detect_simulation_status(simulation, label="single")

    json_data = json.loads(dump_simulation_status_json(status))
    assert json_data["label"] == "single"
    assert json_data["state"] == "finished"
    assert json_data["subruns"][0]["state"] == "finished"
    assert json_data["tool_version"]

    csv_rows = list(csv.DictReader(dump_simulation_status_csv([status]).splitlines()))
    assert csv_rows[0]["label"] == "single"
    assert csv_rows[0]["state"] == "finished"
    assert csv_rows[0]["n_subruns"] == "1"

    writes: list[tuple[Path, str]] = []

    def capture(path: Path, text: str) -> None:
        writes.append((path, text))

    write_simulation_status_json(status, "cache/status.json", capture)
    write_simulation_status_csv([status], "tables/status.csv", capture)

    assert [path for path, _ in writes] == [
        Path("cache/status.json"),
        Path("tables/status.csv"),
    ]


def _mdu(tmp_path: Path, *, tstop: float) -> MduInfo:
    return MduInfo(
        mdu_path=tmp_path / "run.mdu",
        refdate=20210101,
        tunit="S",
        tstart=0.0,
        tstop=tstop,
        tzone=0.0,
        output_dir=tmp_path,
        map_file=Path("run_map.nc"),
        net_file=None,
        mor_file=None,
        sed_file=None,
        gravity=9.81,
        water_density=1000.0,
    )


def _write_map(
    path: Path,
    *,
    time_value: float,
    units: str = "seconds since 2021-01-01 00:00:00 +00:00",
) -> Path:
    with netCDF4.Dataset(path, mode="w") as dataset:
        dataset.createDimension("time", 1)
        time_variable = dataset.createVariable("time", "f8", ("time",))
        time_variable.units = units
        time_variable.calendar = "standard"
        time_variable[:] = [time_value]
        morft_variable = dataset.createVariable("morft", "f8", ("time",))
        morft_variable.units = units
        morft_variable[:] = [time_value]
        dataset.setncattr("source", "D-Flow FM 1.2.184 (synthetic)")
    return path
