# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for plain-FM and SMT simulation discovery."""

from __future__ import annotations

import os
from pathlib import Path

import netCDF4
import pytest

from d3dplot.simulation import SimulationKind, discover_simulation


@pytest.mark.integration
@pytest.mark.parametrize(
    (
        "relative_path",
        "expected_kind",
        "expected_partition_counts",
        "expected_issue",
    ),
    [
        ("simulations/r001", SimulationKind.SMT, (3, 3, 3, 3), None),
        ("simulations/r002", SimulationKind.SMT, (3, 3, 3, 3), None),
        (
            "simulations/variants/crashed",
            SimulationKind.SMT,
            (3, 3, 3, 3),
            None,
        ),
        (
            "simulations/variants/corrupt_partition",
            SimulationKind.SMT,
            (3, 3, 3, 3),
            None,
        ),
        (
            "simulations/variants/non_partitioned",
            SimulationKind.SMT,
            (1, 1, 1, 1),
            None,
        ),
        (
            "simulations/variants/running",
            SimulationKind.SMT,
            (3, 3, 3, 3),
            "V-SIM-011",
        ),
        ("simulations/fm_partitioned", SimulationKind.FM, (3,), None),
        ("simulations/fm_single", SimulationKind.FM, (1,), None),
        ("simulations/variants/gap", SimulationKind.SMT, None, "V-SIM-010"),
    ],
)
def test_discovery_classifies_synthetic_variants(
    synthetic_case: Path,
    relative_path: str,
    expected_kind: SimulationKind,
    expected_partition_counts: tuple[int, ...] | None,
    expected_issue: str | None,
) -> None:
    simulation_path = synthetic_case / relative_path

    simulation, report = discover_simulation(simulation_path)

    if expected_issue == "V-SIM-010":
        assert simulation is None
        assert [issue.code for issue in report.errors] == ["V-SIM-010"]
        return

    assert simulation is not None
    assert report.is_valid
    assert simulation.kind is expected_kind
    assert tuple(subrun.partition_count for subrun in simulation.subruns) == (
        expected_partition_counts
    )
    assert tuple(subrun.index for subrun in simulation.subruns) == tuple(
        range(len(simulation.subruns))
    )
    assert all(
        subrun.model_input.morphology is not None for subrun in simulation.subruns
    )
    assert all(subrun.model_input.sediment is not None for subrun in simulation.subruns)
    assert [issue.code for issue in report.warnings] == (
        ["V-SIM-011"] if expected_issue == "V-SIM-011" else []
    )


@pytest.mark.integration
def test_smt_reads_model_input_only_from_first_subrun(
    synthetic_case: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import d3dplot.simulation.subrun as subrun_module

    original = subrun_module.validate_model_input
    read_paths: list[Path] = []

    def counted(path: Path) -> object:
        read_paths.append(path)
        return original(path)

    monkeypatch.setattr(subrun_module, "validate_model_input", counted)
    simulation, report = discover_simulation(synthetic_case / "simulations" / "r001")

    assert simulation is not None
    assert report.is_valid
    assert len(read_paths) == 1
    assert simulation.subruns[0].mdu.mdu_path == read_paths[0]
    assert simulation.subruns[1].mdu.mdu_path != read_paths[0]


def test_missing_simulation_reports_v_sim_001(tmp_path: Path) -> None:
    simulation, report = discover_simulation(tmp_path / "missing")

    assert simulation is None
    assert [issue.code for issue in report.errors] == ["V-SIM-001"]


@pytest.mark.unit
@pytest.mark.skipif(os.name != "nt", reason="Windows mapped-drive path behavior")
def test_missing_mapped_drive_simulation_keeps_drive_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = Path(r"P:\missing-simulation")
    unc_path = Path(r"\\directory.intra\PROJECT\missing-simulation")
    path_type = type(path)
    monkeypatch.setattr(
        path_type,
        "resolve",
        lambda self, strict=False: unc_path,
    )
    monkeypatch.setattr(path_type, "is_dir", lambda self: False)

    simulation, report = discover_simulation(path)

    assert simulation is None
    assert [issue.code for issue in report.errors] == ["V-SIM-001"]
    assert report.errors[0].location == str(path)


def test_multiple_candidate_mdu_files_ignore_partition_mdu(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.mdu").write_text("[Time]\nRefDate = 20200101\n", encoding="utf-8")
    (tmp_path / "b.mdu").write_text("[Time]\nRefDate = 20200101\n", encoding="utf-8")
    (tmp_path / "a_0000.mdu").write_text(
        "[Time]\nRefDate = 20200101\n",
        encoding="utf-8",
    )

    simulation, report = discover_simulation(tmp_path)

    assert simulation is None
    assert [issue.code for issue in report.errors] == ["V-SIM-002"]


def test_missing_map_files_report_v_out_001(tmp_path: Path) -> None:
    _write_mdu(tmp_path)

    simulation, report = discover_simulation(tmp_path)

    assert simulation is None
    assert [issue.code for issue in report.errors] == ["V-OUT-001"]


def test_unreadable_map_file_reports_v_out_002(tmp_path: Path) -> None:
    _write_mdu(tmp_path)
    output = tmp_path / "results"
    output.mkdir()
    (output / "dflowfm_map.nc").write_text("not a NetCDF file", encoding="utf-8")

    simulation, report = discover_simulation(tmp_path, check_map_headers=True)

    assert simulation is None
    assert [issue.code for issue in report.errors] == ["V-OUT-002"]


def test_unreadable_map_file_is_not_opened_by_default(tmp_path: Path) -> None:
    _write_mdu(tmp_path)
    output = tmp_path / "results"
    output.mkdir()
    (output / "dflowfm_map.nc").write_text("not a NetCDF file", encoding="utf-8")

    simulation, report = discover_simulation(tmp_path)

    assert simulation is not None
    assert report.is_valid


def test_classic_netcdf_map_file_is_readable(tmp_path: Path) -> None:
    _write_mdu(tmp_path)
    _write_map(
        tmp_path / "results" / "dflowfm_map.nc",
        netcdf_format="NETCDF3_CLASSIC",
    )

    simulation, report = discover_simulation(tmp_path, check_map_headers=True)

    assert simulation is not None
    assert report.is_valid


def test_64bit_offset_netcdf_map_file_is_readable(tmp_path: Path) -> None:
    _write_mdu(tmp_path)
    _write_map(
        tmp_path / "results" / "dflowfm_map.nc",
        netcdf_format="NETCDF3_64BIT_OFFSET",
    )

    simulation, report = discover_simulation(tmp_path, check_map_headers=True)

    assert simulation is not None
    assert report.is_valid


def test_truncated_classic_netcdf_map_file_reports_v_out_002(
    tmp_path: Path,
) -> None:
    _write_mdu(tmp_path)
    output = tmp_path / "results"
    output.mkdir()

    map_file = output / "dflowfm_map.nc"
    for truncated_header in (b"CDF\x01", b"CDF\x01\x00\x00\x00\x03"):
        map_file.write_bytes(truncated_header)
        simulation, report = discover_simulation(tmp_path, check_map_headers=True)

        assert simulation is None
        assert [issue.code for issue in report.errors] == ["V-OUT-002"]


def test_nonconsecutive_partition_files_report_v_out_001(tmp_path: Path) -> None:
    _write_mdu(tmp_path)
    for index in (0, 2):
        _write_map(tmp_path / "results" / f"dflowfm_{index:04d}_map.nc")

    simulation, report = discover_simulation(tmp_path)

    assert simulation is None
    assert [issue.code for issue in report.errors] == ["V-OUT-001"]


def test_partition_count_must_match_between_smt_subruns(tmp_path: Path) -> None:
    (tmp_path / "smt.yml").write_text("model: dflowfm\n", encoding="utf-8")
    for index, partition_count in ((0, 1), (1, 2)):
        subrun = tmp_path / "output" / str(index)
        _write_mdu(subrun)
        for partition in range(partition_count):
            _write_map(subrun / "results" / f"dflowfm_{partition:04d}_map.nc")

    simulation, report = discover_simulation(tmp_path)

    assert simulation is None
    assert [issue.code for issue in report.errors] == ["V-OUT-003"]


def test_parallel_smt_discovery_preserves_subrun_order(tmp_path: Path) -> None:
    (tmp_path / "smt.yml").write_text("model: dflowfm\n", encoding="utf-8")
    for index in range(8):
        subrun = tmp_path / "output" / str(index)
        _write_mdu(subrun)
        _write_map(subrun / "results" / "dflowfm_map.nc")

    simulation, report = discover_simulation(tmp_path)

    assert simulation is not None
    assert report.is_valid
    assert [subrun.index for subrun in simulation.subruns] == list(range(8))


def test_dimr_selects_fm_component_not_first_component(tmp_path: Path) -> None:
    flow_dir = tmp_path / "flow"
    _write_mdu(flow_dir)
    _write_map(flow_dir / "results" / "dflowfm_map.nc")
    (tmp_path / "decoy.mdu").write_text(
        "[Time]\nRefDate = 20200101\n",
        encoding="utf-8",
    )
    (tmp_path / "dimr_config.xml").write_text(
        _dimr_document(
            '<component name="rtc"><library>FBCTools_BMI</library>'
            "<workingDir>.</workingDir><inputFile>rtc.xml</inputFile>"
            "<mpiCommunicator>DFM_COMM_DFMWORLD</mpiCommunicator></component>"
            '<component name="fm"><library>dflowfm</library>'
            "<workingDir>flow</workingDir><inputFile>dflowfm.mdu</inputFile>"
            "<mpiCommunicator>DFM_COMM_DFMWORLD</mpiCommunicator></component>"
        ),
        encoding="utf-8",
    )

    simulation, report = discover_simulation(tmp_path)

    assert report.is_valid
    assert simulation is not None
    assert simulation.kind is SimulationKind.FM
    assert len(simulation.subruns) == 1
    assert simulation.subruns[0].mdu.mdu_path == (flow_dir / "dflowfm.mdu").resolve()


def _write_mdu(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "dflowfm.mdu").write_text(
        "[Time]\nRefDate = 20200101\n[Output]\nOutputDir = results\n",
        encoding="utf-8",
    )


def _write_map(path: Path, *, netcdf_format: str = "NETCDF4") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with netCDF4.Dataset(path, mode="w", format=netcdf_format) as dataset:
        dataset.createDimension("time", 1)


def _dimr_document(components: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n'
        '<dimrConfig xmlns="http://schemas.deltares.nl/dimr" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:schemaLocation="http://schemas.deltares.nl/dimr '
        'http://content.oss.deltares.nl/schemas/dimr-1.2.xsd">\n'
        "<documentation><fileVersion>1.2</fileVersion>"
        "<createdBy>test</createdBy>"
        "<creationDate>2026-10-26T00:00:00</creationDate></documentation>\n"
        '<control><start name="rtc" /></control>\n'
        f"{components}\n"
        "</dimrConfig>\n"
    )
