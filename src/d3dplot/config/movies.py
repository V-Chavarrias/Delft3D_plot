# SPDX-License-Identifier: GPL-3.0-or-later
"""Movie export configuration shared by the map and profile analyses."""

from __future__ import annotations

from pydantic import Field, PositiveFloat, model_validator

from d3dplot.config.base import ConfigModel


class MovieConfig(ConfigModel):
    """Movie export settings for figure frames."""

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
