# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration model for the map1d branch-chain analysis."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Literal, TypeAlias

from pydantic import Field, PositiveFloat, field_validator, model_validator

from d3dplot.config.analysis import AnalysisConfigBase
from d3dplot.config.base import ConfigModel, Duration
from d3dplot.config.measurements import ProfileMeasurementsConfig

Map1DView: TypeAlias = Literal["profile", "space_time", "all_times"]
Map1DGrouping: TypeAlias = Literal["per_simulation", "all_simulations"]


def _default_views() -> list[Map1DView]:
    return ["profile"]


class Map1DRiverKmConfig(ConfigModel):
    """Optional river-km coordinate source for map1d chains."""

    file: Path = Field(description="River-km coordinate file.")
    tolerance_m: PositiveFloat = Field(
        default=500.0,
        description="Maximum nearest-point distance for chain coordinates.",
    )


class Map1DConfig(AnalysisConfigBase):
    """Extract quantities along configured one-dimensional branch chains."""

    analysis: Literal["map1d"] = Field(
        default="map1d",
        description="Discriminator identifying the map1d analysis.",
    )
    branches: list[list[str]] = Field(
        min_length=1,
        description="Ordered branch identifiers for each one-dimensional chain.",
    )
    branch_names: list[str] = Field(
        min_length=1,
        description="Stable output names for the configured branch chains.",
    )
    branch_titles: list[str] | None = Field(
        default=None,
        description="Optional display titles for the configured branch chains.",
    )
    views: list[Map1DView] = Field(
        default_factory=_default_views,
        min_length=1,
        description="map1d views to generate.",
    )
    grouping: Map1DGrouping = Field(
        default="per_simulation",
        description="Whether line views are grouped per simulation or together.",
    )
    river_km: Map1DRiverKmConfig | None = Field(
        default=None,
        description="Optional river-km file used for the horizontal coordinate.",
    )
    measurements: dict[str, ProfileMeasurementsConfig] = Field(
        default_factory=dict,
        description="Optional profile measurements keyed by variable name.",
    )
    measurements_time_tolerance: Duration = Field(
        default=timedelta(days=30),
        description="Maximum time difference for profile measurement overlays.",
    )

    @field_validator("branches")
    @classmethod
    def validate_branches(cls, value: list[list[str]]) -> list[list[str]]:
        if any(
            not chain or any(not branch or branch != branch.strip() for branch in chain)
            for chain in value
        ):
            raise ValueError("map1d branch chains must contain trimmed identifiers.")
        return value

    @field_validator("branch_names")
    @classmethod
    def validate_branch_names(cls, value: list[str]) -> list[str]:
        if any(not name or name != name.strip() for name in value):
            raise ValueError("map1d branch names must be non-empty and trimmed.")
        if len({name.casefold() for name in value}) != len(value):
            raise ValueError("map1d branch names must be unique.")
        return value

    @field_validator("branch_titles")
    @classmethod
    def validate_branch_titles(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and any(not title.strip() for title in value):
            raise ValueError("map1d branch titles must be non-empty when provided.")
        return value

    @model_validator(mode="after")
    def validate_branch_metadata(self) -> Map1DConfig:
        if len(self.branches) != len(self.branch_names):
            raise ValueError("map1d branches and branch_names must have equal lengths.")
        if self.branch_titles is not None and len(self.branch_titles) != len(
            self.branches
        ):
            raise ValueError(
                "map1d branch_titles and branches must have equal lengths."
            )
        return self
