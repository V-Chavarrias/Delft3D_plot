# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration model for the PRF profile analysis (reading along polylines, D118)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal, TypeAlias

from pydantic import Field, field_validator, model_validator

from d3dplot.config.analysis import AnalysisConfigBase
from d3dplot.config.base import ConfigModel

ProfileView: TypeAlias = Literal["profile", "space_time", "section"]

_SUPPORTED_COMPARISONS = frozenset(
    {"value", "difference_time", "difference_simulation"}
)


class ProfileLineConfig(ConfigModel):
    """One polyline along which quantities are sampled.

    Give the geometry either as a ``file`` (.pli) or inline as ``coordinates``.
    """

    name: str = Field(description="Stable output name of the line.")
    file: Path | None = Field(
        default=None,
        description="Polyline (.pli) file in model coordinates.",
    )
    polyline: str | None = Field(
        default=None,
        description=(
            "Name of the polyline object inside the file. Required when the file "
            "contains more than one polyline. Only valid with file."
        ),
    )
    coordinates: list[tuple[float, float]] | None = Field(
        default=None,
        min_length=2,
        description="Inline (x, y) vertices in model coordinates, in order.",
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

    @field_validator("coordinates")
    @classmethod
    def validate_coordinates(
        cls, value: list[tuple[float, float]] | None
    ) -> list[tuple[float, float]] | None:
        if value is not None and not all(
            math.isfinite(x) and math.isfinite(y) for x, y in value
        ):
            raise ValueError("Profile line coordinates must be finite.")
        return value

    @model_validator(mode="after")
    def validate_geometry_source(self) -> ProfileLineConfig:
        if (self.file is None) == (self.coordinates is None):
            raise ValueError(
                f"Profile line {self.name!r} needs exactly one of file or coordinates."
            )
        if self.coordinates is not None and self.polyline is not None:
            raise ValueError(
                f"Profile line {self.name!r}: polyline applies only to file."
            )
        return self


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
        unsupported = [
            comparison
            for comparison in self.comparisons
            if comparison not in _SUPPORTED_COMPARISONS
        ]
        if unsupported:
            raise ValueError(
                "Profile analysis supports the 'value', 'difference_time' and "
                f"'difference_simulation' comparisons; got {unsupported}."
            )
        has_differences = any(comparison != "value" for comparison in self.comparisons)
        if has_differences and list(self.views) != ["profile"]:
            raise ValueError(
                "Difference comparisons are supported only for the 'profile' view."
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
