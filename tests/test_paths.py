# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for separator handling of paths written in model files (D88, WP R.9)."""

from __future__ import annotations

from pathlib import Path

import pytest

from d3dplot.model_input.mdu import MduInfo
from d3dplot.paths import normalise_separators


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("..\\model\\grid_net.nc", "../model/grid_net.nc"),
        ("../model/grid_net.nc", "../model/grid_net.nc"),
        ("sub\\dir\\a.mdu", "sub/dir/a.mdu"),
    ],
)
def test_posix_separator_converts_backslashes(text: str, expected: str) -> None:
    assert normalise_separators(text, separator="/") == expected


@pytest.mark.parametrize("text", ["..\\model\\grid_net.nc", "../model/grid_net.nc"])
def test_windows_separator_keeps_text(text: str) -> None:
    assert normalise_separators(text, separator="\\") == text


@pytest.mark.parametrize(
    "net_file",
    ["..\\grid\\grid_net.nc", "../grid/grid_net.nc"],
)
def test_mdu_net_file_with_either_separator_resolves(
    tmp_path: Path, net_file: str
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    mdu_path = run_dir / "model.mdu"
    mdu_path.write_text(
        f"[geometry]\nNetFile = {net_file}\n",
        encoding="utf-8",
    )

    info = MduInfo.from_path(mdu_path)

    assert info.net_file is not None
    assert info.net_file.resolve() == (tmp_path / "grid" / "grid_net.nc").resolve()
