# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the native Tekal polyline reader."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from d3dplot.model_input.errors import ModelInputError
from d3dplot.model_input.polyline import read_polyline_file


def _write(tmp_path: Path, text: str, name: str = "lines.pli") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_reads_every_block_with_description_lines(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "* first description\n"
        "river_axis\n"
        "3 2\n"
        "0.0 0.0\n"
        "1.0 0.5\n"
        "2.0 1.0\n"
        "\n"
        "* second description\n"
        "bank\n"
        "2 2\n"
        "5 5\n"
        "6 6\n",
    )

    blocks = read_polyline_file(path)

    assert [block.name for block in blocks] == ["river_axis", "bank"]
    np.testing.assert_array_equal(
        blocks[0].xy, np.array([[0.0, 0.0], [1.0, 0.5], [2.0, 1.0]])
    )
    np.testing.assert_array_equal(blocks[1].xy, np.array([[5.0, 5.0], [6.0, 6.0]]))
    assert blocks[0].xy.dtype == np.float64


def test_pliz_keeps_x_and_y_and_drops_z(tmp_path: Path) -> None:
    path = _write(tmp_path, "shore\n2 3\n1.0 2.0 -3.0\n4.0 5.0 -6.0\n", "shore.pliz")

    blocks = read_polyline_file(path)

    np.testing.assert_array_equal(blocks[0].xy, np.array([[1.0, 2.0], [4.0, 5.0]]))


def test_empty_file_gives_no_blocks(tmp_path: Path) -> None:
    assert read_polyline_file(_write(tmp_path, "\n\n")) == ()


def test_utf8_bom_is_not_part_of_the_first_name(tmp_path: Path) -> None:
    path = tmp_path / "bom.ldb"
    path.write_bytes("\ufeffcoast\n1 2\n0 0 1\n".encode("utf-8"))

    blocks = read_polyline_file(path)

    assert blocks[0].name == "coast"


def test_non_numeric_coordinate_reports_the_line(tmp_path: Path) -> None:
    path = _write(tmp_path, "bank\n2 2\n0 0\nx y\n")

    with pytest.raises(ModelInputError, match="line 4"):
        read_polyline_file(path)


def test_truncated_block_is_an_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "bank\n3 2\n0 0\n1 1\n")

    with pytest.raises(ModelInputError, match="end of file"):
        read_polyline_file(path)


@pytest.mark.parametrize("dimensions", ["0 2", "2 0", "two 2", "2"])
def test_invalid_rows_columns_line_is_an_error(tmp_path: Path, dimensions: str) -> None:
    path = _write(tmp_path, f"bank\n{dimensions}\n0 0\n1 1\n")

    with pytest.raises(ModelInputError, match="rows columns"):
        read_polyline_file(path)


def test_name_followed_by_name_is_an_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "bank\n* description after a name\n2 2\n0 0\n1 1\n")

    with pytest.raises(ModelInputError, match="rows columns"):
        read_polyline_file(path)


def test_missing_file_raises_model_input_error(tmp_path: Path) -> None:
    with pytest.raises(ModelInputError, match="Could not read polyline file"):
        read_polyline_file(tmp_path / "missing.pli")
