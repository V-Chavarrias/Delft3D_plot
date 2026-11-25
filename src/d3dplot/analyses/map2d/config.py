# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration model for the map2d analysis."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, PositiveFloat, model_validator

from d3dplot.config.analysis import AnalysisConfigBase
from d3dplot.config.base import ConfigModel, reject_unimplemented_fields
from d3dplot.config.figure import MapViewConfig
from d3dplot.config.measurements import MapMeasurementsConfig

VELOCITY_COMPONENT_VARIABLES: tuple[str, str] = ("mesh2d_ucx", "mesh2d_ucy")


class MovieConfig(ConfigModel):
    """Movie export settings for map frames."""

    duration_s: PositiveFloat | None = Field(
        default=None,
        description=(
            "Duration of each movie frame in seconds. Defaults to 1.0 when neither "
            "duration_s nor total_duration_s is set."
        ),
    )
    total_duration_s: PositiveFloat | None = Field(
        default=None,
        description=(
            "Total movie duration in seconds; each frame gets an equal share. "
            "Mutually exclusive with duration_s."
        ),
    )

    @model_validator(mode="after")
    def validate_duration(self) -> MovieConfig:
        if self.duration_s is not None and self.total_duration_s is not None:
            raise ValueError(
                "Set either movie duration_s or total_duration_s, not both."
            )
        return self

    def frame_duration_s(self, frame_count: int) -> float:
        """Return the duration of each frame of a movie with ``frame_count`` frames."""
        if self.total_duration_s is not None:
            return self.total_duration_s / frame_count
        return self.duration_s if self.duration_s is not None else 1.0


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
        description="Not implemented in v1; setting it is rejected.",
    )
    view_3d: bool = Field(
        default=False,
        description="Not implemented in v1; setting it is rejected.",
    )
    velocity_vectors: Literal["none", "scaled", "normalized"] = Field(
        default="none",
        description="Velocity-vector overlay style.",
    )
    mask_range: dict[str, tuple[float | None, float | None]] = Field(
        default_factory=dict,
        description="Optional value ranges to mask, keyed by variable.",
    )

    @model_validator(mode="after")
    def reject_unimplemented(self) -> Map2DConfig:
        reject_unimplemented_fields(self, ["export", "view_3d"])
        return self
