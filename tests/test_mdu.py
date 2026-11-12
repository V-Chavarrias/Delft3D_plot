# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the native MDU reader."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from d3dplot.model_input.errors import ModelInputError
from d3dplot.model_input.ini import normalise_key
from d3dplot.model_input.mdu import MduInfo
from d3dplot.model_input.mdu_keywords import is_known_section, keyword_spec

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
R209_KEYWORDS = (
    ("sediment", "dredgeFile", "standard"),
    ("sediment", "morCFL", "standard"),
    ("sediment", "DzbDtMax", "standard"),
    ("sediment", "morphoPol", "standard"),
    ("sediment", "inMorphoPol", "standard"),
    ("geometry", "circumcenterMethod", "deprecated"),
    ("geometry", "circumcenterTolerance", "deprecated"),
    ("output", "ncCompression", "standard"),
    ("output", "wriMap_trachytopes", "standard"),
    ("time", "tStartTlfsmo", "standard"),
    ("output", "wrirst_bnd", "research"),
    ("output", "writedfminterpretedvalues", "research"),
    ("restart", "rstignorebl", "research"),
)


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "run.mdu"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_apply_when_sections_are_missing(tmp_path: Path) -> None:
    info = MduInfo.from_path(_write(tmp_path, "[General]\nProgram = D-Flow FM\n"))

    assert info.refdate == 20200101
    assert info.tunit == "S"
    assert info.tstart == 0.0
    assert info.tstop == 86400.0
    assert info.tzone == 0.0
    assert info.tzone_explicit is False
    assert info.gravity == 9.81
    assert info.water_density == 1000.0
    assert info.net_file is None
    assert info.mor_file is None
    assert info.sed_file is None
    assert info.substance_file is None
    assert info.output_dir == tmp_path / "DFM_OUTPUT_run"
    assert info.map_file == Path("run_map.nc")


def test_known_keywords_are_read(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "[time]\n"
        "refdate = 20130101\n"
        "tunit = M\n"
        "tstart = 60.0\n"
        "tstop = 3600\n"
        "tzone = 1\n"
        "[output]\n"
        "outputdir = out\n"
        "mapfile = my_map.nc\n"
        "[geometry]\n"
        "netfile = grid_net.nc\n"
        "[sediment]\n"
        "morfile = run.mor\n"
        "sedfile = run.sed\n"
        "[physics]\n"
        "ag = 9.80665\n"
        "rhomean = 1025\n"
        "[processes]\n"
        "substancefile = run.sub\n",
    )

    info = MduInfo.from_path(path)

    assert info.refdate == 20130101
    assert info.tunit == "M"
    assert info.tstart == 60.0
    assert info.tstop == 3600.0
    assert info.tzone == 1.0
    assert info.tzone_explicit is True
    assert info.output_dir == tmp_path / "out"
    assert info.map_file == Path("my_map.nc")
    assert info.net_file == tmp_path / "grid_net.nc"
    assert info.mor_file == tmp_path / "run.mor"
    assert info.sed_file == tmp_path / "run.sed"
    assert info.gravity == 9.80665
    assert info.water_density == 1025.0
    assert info.substance_file == tmp_path / "run.sub"


def test_empty_output_dir_uses_default(tmp_path: Path) -> None:
    info = MduInfo.from_path(_write(tmp_path, "[output]\noutputdir =\nmapfile =\n"))

    assert info.output_dir == tmp_path / "DFM_OUTPUT_run"
    assert info.map_file == Path("run_map.nc")


def test_datetime_placeholder_is_kept_as_written(tmp_path: Path) -> None:
    info = MduInfo.from_path(
        _write(tmp_path, "[time]\nstartdatetime = yyyymmddhhmmss\n")
    )

    assert info.tstart == "yyyymmddhhmmss"


def test_datetime_keywords_override_numeric_times(tmp_path: Path) -> None:
    info = MduInfo.from_path(
        _write(
            tmp_path,
            "[time]\nstartdatetime = 20200115\nstopdatetime = 20200116120000\n",
        )
    )

    assert info.tstart == "20200115"
    assert info.tstop == "20200116120000"


def test_unknown_sections_and_keywords_are_accepted(tmp_path: Path) -> None:
    info = MduInfo.from_path(
        _write(
            tmp_path,
            "[model]\nautostart = 0\nversion = 1\n"
            "[time]\nrefdate = 20100101\nobsolete_keyword = 5\n"
            "[custom_section]\nanything = yes\n",
        )
    )

    assert info.refdate == 20100101


def test_utf8_bom_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "run.mdu"
    path.write_bytes(b"\xef\xbb\xbf[time]\nrefdate = 20150101\n")

    assert MduInfo.from_path(path).refdate == 20150101


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[time]\nrefdate = 2015x\n", "refdate"),
        ("[time]\ntstart = soon\n", "tstart"),
        ("[time]\nstartdatetime = 2020-01-15\n", "startdatetime"),
        ("[time]\nstopdatetime = 20201340\n", "stopdatetime"),
        ("[physics]\nag = nan\n", "ag"),
    ],
)
def test_unparseable_known_values_are_errors(
    tmp_path: Path, text: str, message: str
) -> None:
    with pytest.raises(ModelInputError, match=message):
        MduInfo.from_path(_write(tmp_path, text))


def test_duplicated_section_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ModelInputError, match=r"\[time\]"):
        MduInfo.from_path(_write(tmp_path, "[time]\nrefdate = 2015\n[time]\n"))


@pytest.mark.parametrize(("section", "key", "status"), R209_KEYWORDS)
def test_r209_keywords_are_listed_with_their_status(
    section: str, key: str, status: str
) -> None:
    spec = keyword_spec(section, key)

    assert spec is not None
    assert spec.status == status


def test_r209_keywords_are_accepted_and_deprecated_ones_noted(tmp_path: Path) -> None:
    lines_by_section: dict[str, list[str]] = {}
    for section, key, _ in R209_KEYWORDS:
        lines_by_section.setdefault(section, []).append(f"{key} = 1")
    text = "".join(
        f"[{section}]\n" + "\n".join(lines) + "\n"
        for section, lines in lines_by_section.items()
    )

    info = MduInfo.from_path(_write(tmp_path, text))

    assert not any("Unknown" in note for note in info.keyword_notes)
    # Keys are reported in the normalised (lower-case) form the parser keeps.
    for key in ("circumcentermethod", "circumcentertolerance"):
        assert any(key in note and "deprecated" in note for note in info.keyword_notes)
    assert len(info.keyword_notes) == 2


def test_unknown_keyword_is_accepted_with_a_note(tmp_path: Path) -> None:
    info = MduInfo.from_path(_write(tmp_path, "[general]\nnotARealKey = 1\n"))

    assert info.keyword_notes == (
        "Unknown MDU keyword [general] notarealkey is accepted and not used.",
    )


def test_unknown_section_is_accepted_with_one_note(tmp_path: Path) -> None:
    info = MduInfo.from_path(_write(tmp_path, "[madeUpSection]\nfoo = 1\n"))

    assert info.keyword_notes == (
        "Unknown MDU section [madeUpSection] is accepted and not checked.",
    )


def test_section_and_keyword_names_ignore_case_spaces_and_dashes() -> None:
    assert is_known_section("External Forcing")
    assert is_known_section("external_forcing")
    assert keyword_spec("Time", "REFDATE") is not None
    assert keyword_spec("time", "ref-date") is not None


@pytest.mark.parametrize(
    "name", ["1D2DLinkFile", "External Forcing", "wriMap-trachytopes", "DzbDtMax"]
)
def test_generator_normalisation_matches_the_reader(name: str) -> None:
    assert _load_generator().normalise_name(name) == normalise_key(name)


@pytest.mark.requireslocaldata
def test_generated_keyword_module_matches_the_pinned_specification() -> None:
    spec_path = REPOSITORY_ROOT / "data_local" / "spec" / "mdu.json"
    if not spec_path.exists():
        pytest.skip("The pinned MDU specification is not in data_local/spec.")

    generator = _load_generator()

    assert generator.main(["--spec", str(spec_path), "--check"]) == 0


def _load_generator() -> ModuleType:
    path = REPOSITORY_ROOT / "tools" / "generate_mdu_keywords.py"
    spec = importlib.util.spec_from_file_location("generate_mdu_keywords", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
