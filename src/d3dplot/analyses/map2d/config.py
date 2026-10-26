# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration model for the map2d analysis."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, PositiveFloat

from d3dplot.config.analysis import AnalysisConfigBase
from d3dplot.config.base import ConfigModel
from d3dplot.config.figure import MapViewConfig
from d3dplot.config.measurements import MapMeasurementsConfig


class MovieConfig(ConfigModel):
    """Movie export settings for map frames."""

    duration_s: PositiveFloat = Field(
        default=1.0,
        description="Duration of each movie frame in seconds.",
    )


class Map2DConfig(AnalysisConfigBase):
    """Map values of one or more variables at selected model times."""

    analysis: Literal["map2d"] = Field(
        default="map2d",
        description="Discriminator identifying the map2d analysis.",
    )
    map: MapViewConfig = Field(
        default_factory=MapViewConfig,
        description="Shared map-view settings, including map.render.",
    )
    measurements: dict[str, MapMeasurementsConfig] = Field(
        default_factory=dict,
        description="Measurement settings keyed by variable name.",
    )
    movie: MovieConfig | None = Field(
        default=None,
        description="Optional movie output settings.",
    )
    export: list[Literal["shapefile", "netcdf"]] = Field(
        default_factory=list,
        description="Additional data export formats.",
    )
    view_3d: bool = Field(default=False, description="Show a 3D map view.")
    velocity_vectors: Literal["none", "scaled", "normalized"] = Field(
        default="none",
        description="Velocity-vector overlay style.",
    )
    mask_range: dict[str, tuple[float | None, float | None]] = Field(
        default_factory=dict,
        description="Optional value ranges to mask, keyed by variable.",
    )
