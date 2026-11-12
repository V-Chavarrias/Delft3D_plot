# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the native D-WAQ substance (.sub) reader."""

from __future__ import annotations

from pathlib import Path

import pytest

from d3dplot.model_input.errors import ModelInputError
from d3dplot.model_input.substance import read_substance


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "case.sub"
    path.write_text(text, encoding="utf-8")
    return path


def test_reads_positive_rcdectr_rates_and_ignores_other_parameters(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path,
        "substance 'NO3' active\n"
        "  description 'nitrate'\n"
        "  concentration-unit 'g/m3'\n"
        "end-substance\n"
        "parameter 'RcDecTR1' description 'decay' unit '1/s' value 1.0d-6 "
        "end-parameter\n"
        "parameter 'RcDecTR2' description 'decay' unit '1/s' value 2.5e-7 "
        "end-parameter\n"
        "parameter 'Other' description 'x' unit 'y' value 3 end-parameter\n",
    )

    info = read_substance(path)

    assert info.decay_rates_s_1 == pytest.approx((1.0e-6, 2.5e-7))


def test_keywords_are_case_insensitive(tmp_path: Path) -> None:
    path = _write(tmp_path, "PARAMETER RcDecTR1 VALUE 4.0 END-PARAMETER\n")

    assert read_substance(path).decay_rates_s_1 == (4.0,)


def test_comments_are_dropped_and_quoted_values_unquoted(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "# header comment\n"
        "parameter 'RcDecTR1' # inline comment\n"
        "  description 'a # b'\n"
        "  value 5.0\n"
        "end-parameter\n",
    )

    assert read_substance(path).decay_rates_s_1 == (5.0,)


def test_keywords_inside_other_blocks_are_not_read(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "output 'out'\n"
        "  parameter 'RcDecTR9' value 9.0 end-parameter\n"
        "end-output\n"
        "active-processes\n"
        "  name 'DECAY' 'x' 'y'\n"
        "end-active-processes\n"
        "parameter RcDecTR1 value 1.0 end-parameter\n",
    )

    assert read_substance(path).decay_rates_s_1 == (1.0,)


def test_file_without_rcdectr_gives_no_rates(tmp_path: Path) -> None:
    path = _write(tmp_path, "parameter 'Other' value 3 end-parameter\n")

    assert read_substance(path).decay_rates_s_1 == ()


def test_rejects_non_positive_rate(tmp_path: Path) -> None:
    path = _write(tmp_path, "parameter RcDecTR1 value 0.0 end-parameter\n")

    with pytest.raises(ModelInputError, match="must be positive"):
        read_substance(path)


def test_rejects_non_numeric_parameter(tmp_path: Path) -> None:
    path = _write(tmp_path, "parameter RcDecTR1 value abc end-parameter\n")

    with pytest.raises(ModelInputError, match="is not numeric"):
        read_substance(path)


def test_rejects_parameter_without_value(tmp_path: Path) -> None:
    path = _write(
        tmp_path, "parameter RcDecTR1 description 'x' unit 'y' end-parameter\n"
    )

    with pytest.raises(ModelInputError, match="has no value"):
        read_substance(path)


def test_rejects_parameter_without_name(tmp_path: Path) -> None:
    path = _write(tmp_path, "parameter\n")

    with pytest.raises(ModelInputError, match="has no name"):
        read_substance(path)


def test_missing_file_raises_model_input_error(tmp_path: Path) -> None:
    with pytest.raises(ModelInputError, match="Could not read the substance file"):
        read_substance(tmp_path / "missing.sub")
