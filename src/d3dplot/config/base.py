# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared configuration model and scalar types."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Annotated, Any, TypeAlias

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

_DURATION_PATTERN = re.compile(
    r"^\s*(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(?P<unit>[a-z]+)\s*$",
    re.IGNORECASE,
)
_DURATION_SECONDS: dict[str, float] = {
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
    "w": 604800,
    "week": 604800,
    "weeks": 604800,
}


def _parse_duration(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    match = _DURATION_PATTERN.fullmatch(value)
    if match is None:
        return value

    unit = match.group("unit").lower()
    seconds_per_unit = _DURATION_SECONDS.get(unit)
    if seconds_per_unit is None:
        return value
    return timedelta(seconds=float(match.group("value")) * seconds_per_unit)


Duration: TypeAlias = Annotated[timedelta, BeforeValidator(_parse_duration)]


FixedUTCOffset: TypeAlias = Annotated[
    str,
    Field(pattern=r"^[+-](?:(?:0\d|1[0-3]):[0-5]\d|14:00)$"),
]


class ConfigModel(BaseModel):
    """Base for strict, validated configuration models."""

    model_config = ConfigDict(
        extra="forbid",
        ser_json_timedelta="iso8601",
        validate_assignment=True,
        validate_default=True,
    )
