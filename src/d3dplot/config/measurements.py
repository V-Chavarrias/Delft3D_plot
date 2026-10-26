# SPDX-License-Identifier: GPL-3.0-or-later
"""Map and profile measurement configuration."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Literal

from pydantic import Field, NonNegativeFloat, field_validator

from d3dplot.config.base import ConfigModel, Duration, FixedUTCOffset


class PolygonFilters(ConfigModel):
    """Field selection and filtering rules for polygon measurements."""

    preset: Literal["rws_vakken"] | None = Field(
        default=None,
        description="Named polygon-filter preset.",
    )
    value_field: str | None = Field(default=None, description="Polygon value field.")
    count_field: str | None = Field(
        default=None, description="Polygon coverage-count field."
    )
    area_field: str | None = Field(default=None, description="Polygon area field.")
    min_coverage: float = Field(
        default=0.99,
        ge=0,
        le=1,
        description="Minimum count-to-area coverage ratio.",
    )
    location_field: str | None = Field(
        default=None,
        description="Field containing left/right location codes.",
    )
    allowed_locations: list[int] = Field(
        default_factory=lambda: [-3, -2, -1, 0, 1, 2, 3],
        description="Accepted location codes.",
    )
    id_field: str | None = Field(
        default=None, description="Field used to match polygon IDs."
    )


class MapMeasurementsConfig(ConfigModel):
    """Measurements overlaid on a map analysis."""

    catalogue: Path = Field(description="CSV or YAML measurement catalogue.")
    time_tolerance: Duration = Field(
        default=timedelta(days=30),
        description="Maximum time difference between model and measurement data.",
    )
    spatial_margin_m: NonNegativeFloat = Field(
        default=1000.0,
        description="Bounding-box margin around the map extent, in metres.",
    )
    color_limits_from_measurements: bool = Field(
        default=False,
        description="Use measurement values when calculating automatic color limits.",
    )
    edge_color: str = Field(
        default="black", description="Polygon measurement edge color."
    )
    polygon_filters: PolygonFilters | None = Field(
        default=None,
        description="Optional filters for polygon measurement inputs.",
    )
    layout: Literal["side_by_side"] = Field(
        default="side_by_side",
        description="Map and measurements panel arrangement.",
    )
    mosaic: Literal["first", "last", "mean"] = Field(
        default="first",
        description="How to combine measurement tiles that overlap.",
    )

    @field_validator("time_tolerance")
    @classmethod
    def validate_time_tolerance(cls, value: timedelta) -> timedelta:
        if value < timedelta(0):
            raise ValueError("measurement time tolerance cannot be negative")
        return value


class ProfileMeasurementsConfig(ConfigModel):
    """Optional settings for a river-km profile measurement file."""

    file: Path = Field(
        description="Profile measurements in NetCDF or Matlab v5/v7 format."
    )
    time_zone: FixedUTCOffset | None = Field(
        default=None,
        description="Fixed UTC offset for naive measurement times.",
    )
