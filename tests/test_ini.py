# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the native Deltares INI reader."""

from __future__ import annotations

from pathlib import Path

import pytest

from d3dplot.model_input.errors import ModelInputError
from d3dplot.model_input.ini import parse_ini_file


def _write(tmp_path: Path, text: str, name: str = "file.ini") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_comments_blank_lines_and_preamble_are_ignored(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "# header comment\n"
        "orphan = 1\n"
        "\n"
        "[general]\n"
        "# section comment\n"
        "a = 2\n"
        "no equals here\n",
    )

    document = parse_ini_file(path)

    assert document == {
        "general": {
            "_header": "general",
            "datablock": None,
            "a": "2",
            "comments": {"a": None},
        }
    }


def test_value_and_comment_forms(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "[s]\n"
        "plain = value\n"
        "simple = value # a comment\n"
        "quoted = #quoted value#\n"
        "quoted_comment = #quoted value#  # note\n"
        "only_comment = # just a note\n"
        "empty =\n"
        "blank_value = 10   [-]   comment\n",
    )

    section = parse_ini_file(path)["s"]

    assert isinstance(section, dict)
    assert section["plain"] == "value"
    assert section["simple"] == "value"
    assert section["quoted"] == "#quoted value#"
    assert section["quoted_comment"] == "#quoted value#"
    assert section["only_comment"] is None
    assert section["empty"] is None
    assert section["blank_value"] == "10   [-]   comment"
    assert section["comments"] == {
        "plain": None,
        "simple": "a comment",
        "quoted": None,
        "quoted_comment": "note",
        "only_comment": "just a note",
        "empty": None,
        "blank_value": None,
    }


def test_keys_are_normalised(tmp_path: Path) -> None:
    path = _write(tmp_path, "[Sec-Name]\n1 Key-Name = 1\n")

    document = parse_ini_file(path)

    assert "sec_name" not in document
    assert "secname" in document
    section = document["secname"]
    assert isinstance(section, dict)
    assert section["one_keyname"] == "1"


def test_repeated_keys_and_sections_become_lists(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "[item]\nvalue = 1\nvalue = 2\n[item]\nvalue = 3\n[other]\n",
    )

    document = parse_ini_file(path)

    items = document["item"]
    assert isinstance(items, list)
    assert len(items) == 2
    assert items[0]["value"] == ["1", "2"]
    assert items[1]["value"] == "3"
    assert document["other"] == {
        "_header": "other",
        "datablock": None,
        "comments": {},
    }


def test_latin1_fallback_for_non_utf8_bytes(tmp_path: Path) -> None:
    path = tmp_path / "latin.mdu"
    path.write_bytes(b"[general]\r\ntemp = 20\xb0C\r\n")

    section = parse_ini_file(path)["general"]

    assert isinstance(section, dict)
    assert section["temp"] == "20\u00b0C"


def test_utf8_bom_does_not_hide_first_section(tmp_path: Path) -> None:
    path = tmp_path / "bom.mdu"
    path.write_bytes(b"\xef\xbb\xbf[general]\na = 1\n")

    section = parse_ini_file(path)["general"]

    assert isinstance(section, dict)
    assert section["a"] == "1"


def test_missing_file_raises_model_input_error(tmp_path: Path) -> None:
    with pytest.raises(ModelInputError, match="Could not parse the INI file"):
        parse_ini_file(tmp_path / "missing.mdu")
