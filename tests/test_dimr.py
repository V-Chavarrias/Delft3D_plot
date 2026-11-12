# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the native DIMR reader that resolves the D-Flow FM MDU."""

from __future__ import annotations

from pathlib import Path

import pytest

from d3dplot.model_input.dimr import (
    DimrResolutionError,
    MultipleDflowFMComponentsError,
    NoDflowFMComponentError,
    resolve_dimr_mdu,
)

_NAMESPACE = 'xmlns="http://schemas.deltares.nl/dimr"'


def _document(body: str, *, namespace: str = _NAMESPACE) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n'
        f"<dimrConfig {namespace}>\n"
        "<documentation><fileVersion>1.2</fileVersion>"
        "<createdBy>test</createdBy>"
        "<creationDate>27-Jan-2023 17:21:58</creationDate></documentation>\n"
        f"{body}\n"
        "</dimrConfig>\n"
    )


def _fm_component(
    name: str = "fm",
    *,
    working_dir: str | None = "flow",
    input_file: str | None = "dflowfm.mdu",
) -> str:
    parts = [f'<component name="{name}"><library>dflowfm</library>']
    if working_dir is not None:
        parts.append(f"<workingDir>{working_dir}</workingDir>")
    if input_file is not None:
        parts.append(f"<inputFile>{input_file}</inputFile>")
    parts.append("<mpiCommunicator>DFM_COMM_DFMWORLD</mpiCommunicator></component>")
    return "".join(parts)


def _write_dimr(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "dimr_config.xml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.unit
def test_resolves_fm_mdu_relative_to_working_directory(tmp_path: Path) -> None:
    dimr_path = _write_dimr(
        tmp_path,
        _document(
            '<control><start name="fm" /></control>\n' + _fm_component(),
        ),
    )

    assert resolve_dimr_mdu(dimr_path) == (tmp_path / "flow" / "dflowfm.mdu").resolve()


@pytest.mark.unit
def test_missing_working_directory_resolves_next_to_dimr_file(tmp_path: Path) -> None:
    dimr_path = _write_dimr(
        tmp_path,
        _document(_fm_component(working_dir=None)),
    )

    assert resolve_dimr_mdu(dimr_path) == (tmp_path / "dflowfm.mdu").resolve()


@pytest.mark.unit
def test_absolute_input_file_is_returned_unchanged(tmp_path: Path) -> None:
    mdu_path = (tmp_path / "elsewhere" / "run.mdu").resolve()
    dimr_path = _write_dimr(
        tmp_path,
        _document(_fm_component(input_file=str(mdu_path))),
    )

    assert resolve_dimr_mdu(dimr_path) == mdu_path


@pytest.mark.unit
def test_document_without_namespace_resolves(tmp_path: Path) -> None:
    dimr_path = _write_dimr(
        tmp_path,
        _document(_fm_component(), namespace=""),
    )

    assert resolve_dimr_mdu(dimr_path) == (tmp_path / "flow" / "dflowfm.mdu").resolve()


@pytest.mark.unit
def test_obsolete_and_unknown_elements_are_ignored(tmp_path: Path) -> None:
    body = (
        '<control><parallel><start name="fm" />'
        "<startGroup><time>0 1 2</time></startGroup></parallel></control>\n"
        "<unknownElement><nested>1</nested></unknownElement>\n" + _fm_component()
    )
    dimr_path = _write_dimr(tmp_path, _document(body))

    assert resolve_dimr_mdu(dimr_path) == (tmp_path / "flow" / "dflowfm.mdu").resolve()


@pytest.mark.unit
def test_non_fm_component_without_input_file_is_ignored(tmp_path: Path) -> None:
    body = (
        '<component name="rtc"><library>FBCTools_BMI</library>'
        "<workingDir>.</workingDir></component>\n" + _fm_component()
    )
    dimr_path = _write_dimr(tmp_path, _document(body))

    assert resolve_dimr_mdu(dimr_path) == (tmp_path / "flow" / "dflowfm.mdu").resolve()


@pytest.mark.unit
def test_fm_component_without_input_file_raises(tmp_path: Path) -> None:
    dimr_path = _write_dimr(
        tmp_path,
        _document(_fm_component(name="fm", input_file=None)),
    )

    with pytest.raises(DimrResolutionError, match="does not reference an input file"):
        resolve_dimr_mdu(dimr_path)


@pytest.mark.unit
def test_without_fm_component_raises(tmp_path: Path) -> None:
    body = (
        '<component name="rtc"><library>FBCTools_BMI</library>'
        "<workingDir>.</workingDir><inputFile>rtc.xml</inputFile></component>"
    )
    dimr_path = _write_dimr(tmp_path, _document(body))

    with pytest.raises(NoDflowFMComponentError):
        resolve_dimr_mdu(dimr_path)


@pytest.mark.unit
def test_several_fm_components_raise_with_names(tmp_path: Path) -> None:
    body = _fm_component("fm_a", working_dir="a") + _fm_component(
        "fm_b", working_dir="b"
    )
    dimr_path = _write_dimr(tmp_path, _document(body))

    with pytest.raises(MultipleDflowFMComponentsError, match="fm_a, fm_b"):
        resolve_dimr_mdu(dimr_path)


@pytest.mark.unit
def test_malformed_xml_raises_dimr_resolution_error(tmp_path: Path) -> None:
    dimr_path = _write_dimr(tmp_path, "<dimrConfig><component>")

    with pytest.raises(DimrResolutionError, match="not well-formed XML"):
        resolve_dimr_mdu(dimr_path)
