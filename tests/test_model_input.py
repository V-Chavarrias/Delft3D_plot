# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for read-only D-Flow FM model-input adapters."""

from __future__ import annotations

from pathlib import Path

import pytest

from d3dplot.model_input import MduInfo, read_model_input, resolve_dimr_mdu
from d3dplot.validate import validate_model_input


@pytest.mark.unit
def test_mdu_info_reads_values_and_resolves_input_references(
    tmp_path: Path,
) -> None:
    mdu_path = tmp_path / "sample.mdu"
    mdu_path.write_text(
        "[Time]\n"
        "RefDate = 20200203\n"
        "Tzone = 1\n"
        "Tunit = M\n"
        "TStart = 1.5\n"
        "TStop = 2.5\n"
        "[Geometry]\n"
        "NetFile = mesh.nc\n"
        "[Output]\n"
        "OutputDir = results\n"
        "MapFile = custom_map.nc\n"
        "[Physics]\n"
        "Ag = 9.813\n"
        "Rhomean = 998.2\n",
        encoding="utf-8",
    )

    info = MduInfo.from_path(mdu_path)

    assert info.mdu_path == mdu_path.resolve()
    assert (info.refdate, info.tunit, info.tstart, info.tstop, info.tzone) == (
        20200203,
        "M",
        1.5,
        2.5,
        1.0,
    )
    assert info.tzone_explicit
    assert info.output_dir == (tmp_path / "results").resolve()
    assert info.map_file == Path("custom_map.nc")
    assert info.net_file == (tmp_path / "mesh.nc").resolve()
    assert info.mor_file is None
    assert info.sed_file is None
    assert info.gravity == pytest.approx(9.813)
    assert info.water_density == pytest.approx(998.2)
    assert not (tmp_path / "mesh.nc").exists()


@pytest.mark.unit
def test_mdu_info_uses_kernel_defaults_and_datetime_overrides(
    tmp_path: Path,
) -> None:
    mdu_path = tmp_path / "defaults.mdu"
    mdu_path.write_text(
        "[Time]\n"
        "RefDate = 20200101\n"
        "Tunit = S\n"
        "TStart = 12\n"
        "TStop = 24\n"
        "StartDateTime = 20200203040506\n"
        "StopDateTime = 20200204050607\n",
        encoding="utf-8",
    )

    info = MduInfo.from_path(mdu_path)

    assert info.tstart == "20200203040506"
    assert info.tstop == "20200204050607"
    assert not info.tzone_explicit
    assert info.output_dir == (tmp_path / "DFM_OUTPUT_defaults").resolve()
    assert info.map_file == Path("defaults_map.nc")
    assert info.gravity == pytest.approx(9.81)
    assert info.water_density == pytest.approx(1000.0)


@pytest.mark.integration
def test_generated_synthetic_mdu_mor_and_sed_are_parsed(
    synthetic_case: Path,
) -> None:
    mdu_path = synthetic_case / "simulations" / "r001" / "output" / "0" / "dflowfm.mdu"

    model_input = read_model_input(mdu_path)

    assert model_input.mdu.mor_file == (mdu_path.parent / "mor.mor").resolve()
    assert (
        model_input.mdu.sed_file
        == (synthetic_case / "simulations" / "r001" / "sed" / "synthetic.sed").resolve()
    )
    assert model_input.morphology is not None
    assert model_input.morphology.morfac == pytest.approx(10.0)
    assert model_input.morphology.morstt == pytest.approx(86400.0)
    assert model_input.sediment is not None
    assert [fraction.diameter_m for fraction in model_input.sediment.fractions] == [
        0.0005,
        0.002,
        0.008,
    ]
    assert [fraction.name for fraction in model_input.sediment.fractions] == [
        "#Sediment1#",
        "#Sediment2#",
        "#Sediment3#",
    ]
    assert all(
        fraction.sediment_type == "bedload"
        for fraction in model_input.sediment.fractions
    )
    assert [
        fraction.solid_density_kg_m3 for fraction in model_input.sediment.fractions
    ] == pytest.approx([2650.0, 2700.0, 2650.0])


@pytest.mark.unit
def test_sediment_reader_supports_diameter_ranges(tmp_path: Path) -> None:
    mdu_path = tmp_path / "ranges.mdu"
    mdu_path.write_text("[Time]\nRefDate = 20200101\n", encoding="utf-8")
    sed_path = tmp_path / "ranges.sed"
    sed_path.write_text(
        "[Sediment]\n"
        "Name = sand\n"
        "SedTyp = bedload\n"
        "SedMinDia = 0.0001\n"
        "SedMaxDia = 0.001\n"
        "RhoSol = 2600\n",
        encoding="utf-8",
    )
    mdu_text = mdu_path.read_text(encoding="utf-8")
    mdu_path.write_text(
        f"{mdu_text}[Sediment]\nSedFile = {sed_path.name}\n",
        encoding="utf-8",
    )

    model_input = read_model_input(mdu_path)

    assert model_input.sediment is not None
    assert len(model_input.sediment.fractions) == 1
    fraction = model_input.sediment.fractions[0]
    assert fraction.name == "sand"
    assert fraction.sediment_type == "bedload"
    assert fraction.diameter_m is None
    assert fraction.minimum_diameter_m == pytest.approx(0.0001)
    assert fraction.maximum_diameter_m == pytest.approx(0.001)
    assert fraction.solid_density_kg_m3 == pytest.approx(2600.0)


@pytest.mark.unit
def test_v_mdu_001_lists_unknown_keywords_and_sections(tmp_path: Path) -> None:
    mdu_path = tmp_path / "unknown.mdu"
    mdu_path.write_text(
        "[Time]\n"
        "RefDate = 20200101\n"
        "UnlistedTimeKeyword = 1\n"
        "[FutureSection]\n"
        "UnlistedSectionKeyword = 1\n",
        encoding="utf-8",
    )

    model_input, report = validate_model_input(mdu_path)

    assert model_input is None
    assert [issue.code for issue in report.errors] == ["V-MDU-001"]
    message = report.errors[0].message.casefold()
    assert "unlistedtimekeyword" in message
    assert "futuresection" in message
    assert "unlistedsectionkeyword" in message
    assert report.errors[0].hint is not None


@pytest.mark.unit
def test_v_mdu_001_reports_malformed_referenced_sediment_file(
    tmp_path: Path,
) -> None:
    mdu_path = tmp_path / "malformed.mdu"
    mdu_path.write_text(
        "[Time]\nRefDate = 20200101\n[Sediment]\nSedFile = invalid.sed\n",
        encoding="utf-8",
    )
    (tmp_path / "invalid.sed").write_text(
        "[Sediment]\nSedDia = not-a-number\n",
        encoding="utf-8",
    )

    model_input, report = validate_model_input(mdu_path)

    assert model_input is None
    assert [issue.code for issue in report.errors] == ["V-MDU-001"]
    assert "SedDia" in report.errors[0].message


@pytest.mark.unit
def test_dimr_resolution_uses_working_directory_without_loading_mdu(
    tmp_path: Path,
) -> None:
    working_dir = tmp_path / "run"
    working_dir.mkdir()
    mdu_path = working_dir / "component.mdu"
    mdu_path.write_text(
        "[Time]\nRefDate = 20200101\nUnknownKeyword = 1\n",
        encoding="utf-8",
    )
    dimr_path = tmp_path / "dimr_config.xml"
    dimr_path.write_text(
        '<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n'
        '<dimrConfig xmlns="http://schemas.deltares.nl/dimr" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:schemaLocation="http://schemas.deltares.nl/dimr '
        'http://content.oss.deltares.nl/schemas/dimr-1.2.xsd">\n'
        "  <documentation><fileVersion>1.2</fileVersion>"
        "<createdBy>test</createdBy>"
        "<creationDate>2026-01-01T00:00:00</creationDate></documentation>\n"
        '  <control><parallel><start name="fm" /></parallel></control>\n'
        '<component name="fm"><library>dflowfm</library>'
        "<workingDir>run</workingDir><inputFile>component.mdu</inputFile>"
        "<mpiCommunicator>DFM_COMM_DFMWORLD</mpiCommunicator></component>\n"
        "</dimrConfig>\n",
        encoding="utf-8",
    )

    assert resolve_dimr_mdu(dimr_path) == mdu_path.resolve()
    model_input, report = validate_model_input(mdu_path)
    assert model_input is None
    assert [issue.code for issue in report.errors] == ["V-MDU-001"]
