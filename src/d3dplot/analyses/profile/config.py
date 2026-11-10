# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration model for the PRF profile analysis (reading along polylines, D118)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, TypeAlias

from pydantic import Field, field_validator, model_validator

from d3dplot.config.analysis import AnalysisConfigBase
from d3dplot.config.base import ConfigModel

ProfileView: TypeAlias = Literal["profile", "space_time", "section"]


class ProfileLineConfig(ConfigModel):
    """One polyline along which quantities are sampled."""

    name: str = Field(description="Stable output name of the line.")
    file: Path = Field(description="Polyline (.pli) file in model coordinates.")
    polyline: str | None = Field(
        default=None,
        description=(
            "Name of the polyline object inside the file. Required when the file "
            "contains more than one polyline."
        ),
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("Profile line names must be non-empty and trimmed.")
        return value

    @field_validator("polyline")
    @classmethod
    def validate_polyline(cls, value: str | None) -> str | None:
        if value is not None and (not value or value != value.strip()):
            raise ValueError("Profile polyline names must be non-empty and trimmed.")
        return value


class ProfileConfig(AnalysisConfigBase):
    """Sample quantities along configured polylines (PRF)."""

    analysis: Literal["profile"] = Field(
        default="profile",
        description="Discriminator identifying the profile analysis.",
    )
    lines: list[ProfileLineConfig] = Field(
        min_length=1,
        description="Polylines along which the quantities are sampled.",
    )
    views: list[ProfileView] = Field(
        default=["profile"],
        min_length=1,
        description=(
            "Profile views to generate: distance plots per time, space-time plots, "
            "or vertical sections for layer-resolved quantities."
        ),
    )

    @model_validator(mode="after")
    def validate_lines(self) -> ProfileConfig:
        names = [line.name.casefold() for line in self.lines]
        if len(set(names)) != len(names):
            raise ValueError("Profile line names must be unique.")
        return self

    @model_validator(mode="after")
    def validate_profile_options(self) -> ProfileConfig:
        if self.comparisons != ["value"]:
            raise ValueError(
                "Profile analysis supports only the 'value' comparison for now."
            )
        if "section" in self.views and self.vertical != "all":
            raise ValueError("The 'section' view requires vertical: all.")
        single_fraction_views = {"space_time", "section"} & set(self.views)
        if (
            single_fraction_views
            and not self.sum_fractions
            and isinstance(self.fractions, list)
            and len(self.fractions) > 1
        ):
            raise ValueError(
                "The 'space_time' and 'section' views need one fraction or "
                "sum_fractions: true."
            )
        return self


__all__ = ["ProfileConfig", "ProfileLineConfig", "ProfileView"]
