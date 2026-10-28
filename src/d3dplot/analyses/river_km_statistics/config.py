# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration model for river-kilometre statistics."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Literal, TypeAlias

from pydantic import Field, PositiveFloat, field_validator

from d3dplot.config.analysis import AnalysisConfigBase
from d3dplot.config.base import ConfigModel, Duration
from d3dplot.config.figure import MapViewConfig
from d3dplot.config.measurements import ProfileMeasurementsConfig
from d3dplot.config.selections import TimesConfig

DisplayMode: TypeAlias = Literal["auto", "lines", "stacked_area"]
Statistic: TypeAlias = Literal[
    "mean",
    "std",
    "max",
    "min",
    "count",
    "sum",
    "sum_per_length",
    "mean_area_weighted",
    "bed_slope_streamwise",
]
View: TypeAlias = Literal[
    "profile",
    "profile_all_times",
    "space_time",
    "time_series",
    "cumulative",
]


class ConcaveHullConfig(ConfigModel):
    """Settings for generating a concave hull from polygon parts."""

    ratio: float = Field(
        gt=0,
        le=1,
        description="Concave-hull ratio between zero and one.",
    )


class RegionConfig(ConfigModel):
    """Region polygon and optional measurements."""

    name: str = Field(min_length=1, description="Unique region name.")
    polygon: Path = Field(description="Region polygon input.")
    measurements: Path | ProfileMeasurementsConfig | None = Field(
        default=None,
        description="Optional river-km profile measurement file or settings.",
    )
    concave_hull: ConcaveHullConfig | None = Field(
        default=None,
        description="Optional concave-hull construction settings.",
    )


class RegionDifferenceConfig(ConfigModel):
    """Difference between two river-km regions."""

    regions: tuple[str, str] = Field(
        description="Names of the two regions to subtract, in order.",
    )
    measurements: Path | ProfileMeasurementsConfig | None = Field(
        default=None,
        description="Optional measurements for the region difference.",
    )


class KmRangeConfig(ConfigModel):
    """Generate a named river-km set from a start, end, and step."""

    start: float = Field(description="First river-km value.")
    end: float = Field(description="Last river-km value.")
    step: PositiveFloat = Field(description="Distance between river-km values.")


KmSetRange: TypeAlias = list[float] | KmRangeConfig


class RiverKmConfig(ConfigModel):
    """River-km geometry input and named km sets."""

    file: Path = Field(description="River-km file used to build segment geometry.")
    sets: dict[str, KmSetRange] = Field(
        default_factory=dict,
        description="Named km sets, each a list or a start/end/step range.",
    )
    track: str | None = Field(default=None, description="Optional river track name.")
    branches: list[str] = Field(
        default_factory=list,
        description=(
            "Optional generic-provider branch labels: one branch broadcast to all "
            "km centres, or one branch per centre. Rijntakken and Maas branches "
            "are resolved by their providers."
        ),
    )
    half_width_m: PositiveFloat = Field(
        default=1000.0,
        description="Half-width of the river corridor in metres.",
    )
    allow_invalid_segments: bool = Field(
        default=False,
        description=(
            "Allow invalid segment polygons and non-shared segment overlaps; "
            "the lower segment index remains the deterministic assignment."
        ),
    )


class TimeAverageConfig(ConfigModel):
    """Times used to calculate a named time average."""

    name: str = Field(min_length=1, description="Name of the time-average result.")
    times: TimesConfig = Field(
        description="Nested time selection, axis, and tolerance.",
    )


def _default_statistics() -> list[Statistic]:
    return ["mean_area_weighted"]


def _default_views() -> list[View]:
    return ["profile"]


class RiverKmStatisticsConfig(AnalysisConfigBase):
    """Compute statistics along river-km segments for one or more regions."""

    analysis: Literal["river_km_statistics"] = Field(
        default="river_km_statistics",
        description="Discriminator identifying this analysis.",
    )
    regions: list[RegionConfig] = Field(
        min_length=1,
        description="Region polygons and optional measurement files.",
    )
    region_groups: list[list[str]] = Field(
        default_factory=list,
        description="Named region groupings plotted together.",
    )
    region_differences: list[RegionDifferenceConfig] = Field(
        default_factory=list,
        description="Differences between pairs of regions.",
    )
    river_km: RiverKmConfig = Field(description="River-km geometry and segment sets.")
    statistics: list[Statistic] = Field(
        default_factory=_default_statistics,
        min_length=1,
        description="Statistics calculated for every segment.",
    )
    views: list[View] = Field(
        default_factory=_default_views,
        min_length=1,
        description="River-km plots to generate.",
    )
    time_series_locations_km: list[float] = Field(
        default_factory=list,
        description="River-km locations for time-series views.",
    )
    multiply_by_width: Literal["none", "flow", "morphological"] = Field(
        default="none",
        description="Optional width multiplication applied to segment statistics.",
    )
    time_averages: list[TimeAverageConfig] = Field(
        default_factory=list,
        description="Named time ranges to average.",
    )
    diagnostics: list[Literal["segment_map", "segment_membership"]] = Field(
        default_factory=list,
        description="Diagnostic segment maps to generate.",
    )
    display: DisplayMode | dict[str, DisplayMode] = Field(
        default="auto",
        description="Line or stacked-area rendering, optionally keyed by variable.",
    )
    map: MapViewConfig = Field(
        default_factory=MapViewConfig,
        description="Shared map settings for diagnostic maps.",
    )
    measurements_time_tolerance: Duration = Field(
        default=timedelta(days=30),
        description="Maximum time difference when matching profile measurements.",
    )
    measurements_km_tolerance: PositiveFloat = Field(
        default=1.0,
        description="Maximum river-km distance when matching measurements.",
    )
    limits_include_measurements: bool = Field(
        default=False,
        description="Include measurement values when calculating axis limits.",
    )
    measurements_label: str = Field(
        default="Measurements",
        description="Legend label used for profile measurements.",
    )
    grouping: Literal["per_simulation", "all_simulations"] = Field(
        default="per_simulation",
        description="How simulations are grouped in figures.",
    )

    @field_validator("measurements_time_tolerance")
    @classmethod
    def validate_measurements_time_tolerance(cls, value: timedelta) -> timedelta:
        if value < timedelta(0):
            raise ValueError("measurement time tolerance cannot be negative")
        return value
