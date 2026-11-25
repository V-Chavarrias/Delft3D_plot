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
def test_profile_config_accepts_time_and_simulation_differences() -> None:
    config = _config(comparisons=["value", "difference_time", "difference_simulation"])

    assert config.comparisons == ["value", "difference_time", "difference_simulation"]


@pytest.mark.unit
def test_profile_config_rejects_unsupported_difference_comparisons() -> None:
    with pytest.raises(ValidationError, match="difference_simulation_time"):
        _config(comparisons=["difference_simulation_time"])


@pytest.mark.unit
def test_profile_config_rejects_differences_outside_profile_view() -> None:
    with pytest.raises(ValidationError, match="'profile' view"):
        _config(views=["section"], comparisons=["value", "difference_time"])


@pytest.mark.unit
def test_section_view_requires_all_layers() -> None:
    with pytest.raises(ValidationError, match="vertical: all"):
        _config(views=["section"], vertical="top")


@pytest.mark.unit
def test_space_time_and_section_reject_several_fractions_without_sum() -> None:
    with pytest.raises(ValidationError, match="one fraction or sum_fractions"):
        _config(views=["space_time"], fractions=[1, 2])
    with pytest.raises(ValidationError, match="one fraction or sum_fractions"):
        _config(views=["section"], fractions=[1, 2])


@pytest.mark.unit
def test_space_time_accepts_several_fractions_when_summed() -> None:
    config = _config(views=["space_time"], fractions=[1, 2], sum_fractions=True)

    assert config.views == ["space_time"]


@pytest.mark.unit
def test_profile_view_accepts_several_fractions_as_separate_series() -> None:
    config = _config(views=["profile"], fractions=[1, 2])

    assert config.fractions == [1, 2]


@pytest.mark.unit
def test_profile_line_needs_exactly_one_geometry_source() -> None:
    inline = ProfileLineConfig(name="a", coordinates=[(0, 34), (7000, 34)])
    assert inline.file is None
    with pytest.raises(ValidationError, match="exactly one of file or coordinates"):
        ProfileLineConfig(name="a")
    with pytest.raises(ValidationError, match="exactly one of file or coordinates"):
        ProfileLineConfig(name="a", file=Path("a.pli"), coordinates=[(0, 0), (1, 1)])
    with pytest.raises(ValidationError, match="applies only to file"):
        ProfileLineConfig(name="a", coordinates=[(0, 0), (1, 1)], polyline="p")


@pytest.mark.unit
def test_profile_inline_coordinates_need_two_finite_points() -> None:
    with pytest.raises(ValidationError):
        ProfileLineConfig(name="a", coordinates=[(0, 0)])
    with pytest.raises(ValidationError, match="finite"):
        ProfileLineConfig(name="a", coordinates=[(0, 0), (float("nan"), 1)])


@pytest.mark.unit
def test_profile_line_names_and_polyline_names_must_be_trimmed() -> None:
    with pytest.raises(ValidationError, match="trimmed"):
        _line(" a ")
    with pytest.raises(ValidationError, match="trimmed"):
        _line("a", polyline="")
