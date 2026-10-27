# SPDX-License-Identifier: GPL-3.0-or-later
"""Time, vertical, and fraction selection configuration."""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from numbers import Real
from typing import Literal, TypeAlias

from pydantic import Field, NonNegativeInt, PositiveInt, field_validator

from d3dplot.config.base import ConfigModel, Duration

TimeInstant: TypeAlias = datetime | date
TimeAxis: TypeAlias = Literal["hydrodynamic", "morphological"]


class IndicesSelection(ConfigModel):
    """Select outputs by one-based index, matching Matlab time selectors."""

    indices: list[PositiveInt] = Field(
        min_length=1,
        description="One-based indices of model outputs to select.",
    )
    include_last: bool = Field(
        default=False,
        description="Also select the last output, matching positive Inf in Matlab.",
    )


class TimeRange(ConfigModel):
    """Select outputs in an inclusive time range at a fixed interval."""

    start: TimeInstant = Field(description="First time in the inclusive range.")
    end: TimeInstant = Field(description="Last time in the inclusive range.")
    step: Duration = Field(description="Interval between requested times.")

    @field_validator("step")
    @classmethod
    def validate_step(cls, value: timedelta) -> timedelta:
        if value <= timedelta(0):
            raise ValueError("time range step must be positive")
        return value

    @field_validator("start", "end")
    @classmethod
    def normalize_date(cls, value: TimeInstant) -> datetime:
        if isinstance(value, date) and not isinstance(value, datetime):
            return datetime.combine(value, time.min)
        return value


TimeSelection: TypeAlias = (
    Literal["all", "last"] | list[TimeInstant] | IndicesSelection | TimeRange
)


class TimesConfig(ConfigModel):
    """Select model outputs using one time axis and matching tolerance."""

    at: TimeSelection = Field(
        default="all",
        description=(
            "Outputs to select: all/NaN, last/Inf, one-based indices, explicit times, "
            "or a range."
        ),
    )
    axis: TimeAxis = Field(
        default="hydrodynamic",
        description="Time coordinate used for selection and matching.",
    )
    tolerance: Duration | None = Field(
        default=None,
        description="Maximum distance from an explicit requested time.",
    )

    @field_validator("at", mode="before")
    @classmethod
    def normalize_matlab_time_selection(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().casefold()
            if normalized in {"nan", ".nan"}:
                return "all"
            if normalized in {
                "inf",
                "+inf",
                "infinity",
                "+infinity",
                ".inf",
                "+.inf",
            }:
                return "last"
            if normalized in {"-inf", "-infinity", "-.inf"}:
                raise ValueError("times.at accepts positive Inf, not negative Inf")

        if isinstance(value, Real) and not isinstance(value, bool):
            numeric_value = float(value)
            if math.isnan(numeric_value):
                return "all"
            if numeric_value == math.inf:
                return "last"
            if numeric_value == -math.inf:
                raise ValueError("times.at accepts positive Inf, not negative Inf")

        if isinstance(value, int) and not isinstance(value, bool):
            return {"indices": [value]}

        if isinstance(value, list):
            indices: list[int] = []
            include_last = False
            has_nan = False
            has_other = False
            has_negative_infinity = False
            for item in value:
                if isinstance(item, str):
                    normalized = item.strip().casefold()
                    if normalized in {"nan", ".nan"}:
                        has_nan = True
                    elif normalized in {
                        "inf",
                        "+inf",
                        "infinity",
                        "+infinity",
                        ".inf",
                        "+.inf",
                    }:
                        include_last = True
                    elif normalized in {"-inf", "-infinity", "-.inf"}:
                        has_negative_infinity = True
                    else:
                        has_other = True
                    continue
                if isinstance(item, Real) and not isinstance(item, bool):
                    numeric_item = float(item)
                    if math.isnan(numeric_item):
                        has_nan = True
                    elif numeric_item == math.inf:
                        include_last = True
                    elif numeric_item == -math.inf:
                        has_negative_infinity = True
                    elif numeric_item.is_integer():
                        indices.append(int(numeric_item))
                    else:
                        has_other = True
                    continue
                has_other = True

            if has_nan:
                return "all"
            if has_negative_infinity:
                raise ValueError("times.at accepts positive Inf, not negative Inf")
            if (indices or include_last) and has_other:
                raise ValueError(
                    "Do not mix integer indices or Inf with explicit times."
                )
            if indices or include_last:
                if not indices:
                    return "last"
                return {"indices": indices, "include_last": include_last}

        return value

    @field_validator("at")
    @classmethod
    def normalize_dates_in_list(cls, value: TimeSelection) -> TimeSelection:
        if not isinstance(value, list):
            return value
        return [
            datetime.combine(item, time.min)
            if isinstance(item, date) and not isinstance(item, datetime)
            else item
            for item in value
        ]

    @field_validator("tolerance")
    @classmethod
    def validate_tolerance(cls, value: timedelta | None) -> timedelta | None:
        if value is not None and value < timedelta(0):
            raise ValueError("time tolerance cannot be negative")
        return value


class LayersSelection(ConfigModel):
    """Select explicit zero-based vertical layer indices."""

    layers: list[NonNegativeInt] = Field(
        min_length=1,
        description="Zero-based vertical layer indices.",
    )


class DepthAverageConfig(ConfigModel):
    """Configure an optional elevation range for a depth average."""

    z_range: tuple[float, float] | None = Field(
        default=None,
        description="Optional lower and upper elevations in model length units.",
    )


class DepthAverageSelection(ConfigModel):
    """Request a thickness-weighted vertical average."""

    depth_average: DepthAverageConfig = Field(
        default_factory=DepthAverageConfig,
        description="Depth-average settings, optionally limited to an elevation range.",
    )


class ElevationSelection(ConfigModel):
    """Interpolate a vertical profile at one elevation."""

    elevation: float = Field(description="Elevation in model length units.")


VerticalSelection: TypeAlias = (
    Literal["top", "bottom", "all"]
    | LayersSelection
    | DepthAverageSelection
    | ElevationSelection
)
FractionSelection: TypeAlias = Literal["all"] | list[PositiveInt]

Times: TypeAlias = TimesConfig
Vertical: TypeAlias = VerticalSelection
Fractions: TypeAlias = FractionSelection
