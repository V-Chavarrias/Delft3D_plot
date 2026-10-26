# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration for simulation-status detection."""

from __future__ import annotations

from datetime import timedelta

from pydantic import Field, field_validator

from d3dplot.config.base import ConfigModel, Duration

DEFAULT_STATUS_STALE_AFTER = timedelta(hours=1)


class StatusConfig(ConfigModel):
    """Policies used when detecting model-run status."""

    stale_after: Duration = Field(
        default=DEFAULT_STATUS_STALE_AFTER,
        description=(
            "Minimum age of unchanged output files before an incomplete run is "
            "reported as stopped."
        ),
    )

    @field_validator("stale_after")
    @classmethod
    def validate_stale_after(cls, value: timedelta) -> timedelta:
        """Require a non-negative stale interval."""
        if value < timedelta(0):
            raise ValueError("stale_after must be non-negative")
        return value
