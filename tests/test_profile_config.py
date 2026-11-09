# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the PRF profile configuration model."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from d3dplot.analyses.profile.config import ProfileConfig, ProfileLineConfig


def _line(name: str, polyline: str | None = None) -> ProfileLineConfig:
    return ProfileLineConfig(name=name, file=Path("profiles.pli"), polyline=polyline)


def _config(**overrides: object) -> ProfileConfig:
    values: dict[str, object] = {
        "variables": ["mesh2d_s1"],
        "lines": [_line("a")],
    }
    values.update(overrides)
    return ProfileConfig(**values)  # type: ignore[arg-type]


@pytest.mark.unit
def test_profile_config_defaults_to_profile_view() -> None:
    config = _config()

    assert config.analysis == "profile"
    assert config.views == ["profile"]


@pytest.mark.unit
def test_profile_config_requires_at_least_one_line() -> None:
    with pytest.raises(ValidationError):
        _config(lines=[])


@pytest.mark.unit
def test_profile_config_rejects_duplicate_line_names_ignoring_case() -> None:
    with pytest.raises(ValidationError, match="must be unique"):
        _config(lines=[_line("Dike"), _line("dike")])


@pytest.mark.unit
def test_profile_config_rejects_unknown_view() -> None:
    with pytest.raises(ValidationError):
        _config(views=["map"])


@pytest.mark.unit
def test_profile_line_names_and_polyline_names_must_be_trimmed() -> None:
    with pytest.raises(ValidationError, match="trimmed"):
        _line(" a ")
    with pytest.raises(ValidationError, match="trimmed"):
        _line("a", polyline="")
