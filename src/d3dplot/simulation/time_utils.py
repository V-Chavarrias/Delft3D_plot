# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared CF-time and fixed-offset helpers."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta, timezone

_CF_TIME_UNITS = re.compile(
    r"^\s*(?P<unit>[A-Za-z]+)\s+since\s+(?P<origin>.+?)\s*$",
    re.IGNORECASE,
)
_UTC_OFFSET = re.compile(r"^(?P<sign>[+-])(?P<hour>\d{2}):(?P<minute>\d{2})$")
_TIME_UNIT_SECONDS = {
    "s": 1.0,
    "sec": 1.0,
    "secs": 1.0,
    "second": 1.0,
    "seconds": 1.0,
    "min": 60.0,
    "mins": 60.0,
    "minute": 60.0,
    "minutes": 60.0,
    "h": 3600.0,
    "hr": 3600.0,
    "hrs": 3600.0,
    "hour": 3600.0,
    "hours": 3600.0,
    "d": 86400.0,
    "day": 86400.0,
    "days": 86400.0,
    "ms": 0.001,
    "millisecond": 0.001,
    "milliseconds": 0.001,
    "us": 0.000001,
    "microsecond": 0.000001,
    "microseconds": 0.000001,
}


def decode_cf_time(
    value: float,
    units: str,
    calendar: str,
    fallback_offset_hours: float,
) -> datetime | None:
    """Decode one numeric CF time into an aware UTC datetime."""
    match = _CF_TIME_UNITS.fullmatch(units)
    if match is None:
        return None
    seconds_per_unit = _TIME_UNIT_SECONDS.get(match.group("unit").casefold())
    if seconds_per_unit is None:
        return None
    if calendar.casefold() not in {"standard", "gregorian", "proleptic_gregorian"}:
        return None
    if not math.isfinite(value):
        return None

    origin_text = match.group("origin").strip().replace("Z", "+00:00")
    try:
        origin = datetime.fromisoformat(origin_text)
    except ValueError:
        return None
    if origin.tzinfo is None:
        origin = origin.replace(tzinfo=timezone_from_hours(fallback_offset_hours))
    try:
        decoded = origin + timedelta(seconds=value * seconds_per_unit)
    except OverflowError:
        return None
    return decoded.astimezone(UTC)


def cf_time_units_with_origin(
    units: str | None,
    reference_units: str | None,
) -> str | None:
    """Attach a reference CF origin to a supported standalone time unit."""
    if units is None:
        return None
    normalized_units = units.strip()
    if _CF_TIME_UNITS.fullmatch(normalized_units) is not None:
        return normalized_units
    if normalized_units.casefold() not in _TIME_UNIT_SECONDS:
        return None
    if reference_units is None:
        return None
    reference_match = _CF_TIME_UNITS.fullmatch(reference_units)
    if reference_match is None:
        return None
    origin = reference_match.group("origin").strip()
    return f"{normalized_units} since {origin}"


def time_unit_seconds(units: str | None) -> float | None:
    """Return the seconds per CF time unit or standalone time-unit token."""
    if units is None:
        return None
    match = _CF_TIME_UNITS.fullmatch(units)
    unit = match.group("unit") if match is not None else units.strip()
    return _TIME_UNIT_SECONDS.get(unit.casefold())


def timezone_from_hours(offset_hours: float) -> timezone:
    """Create a fixed timezone from an MDU offset expressed in hours."""
    if not math.isfinite(offset_hours) or abs(offset_hours) > 14:
        raise ValueError(f"UTC offset is outside the supported range: {offset_hours}")
    try:
        return timezone(timedelta(hours=offset_hours))
    except ValueError as error:
        raise ValueError(f"Invalid UTC offset: {offset_hours}") from error


def timezone_from_offset(offset: str) -> timezone:
    """Parse a configured fixed UTC offset such as ``+01:30``."""
    match = _UTC_OFFSET.fullmatch(offset)
    if match is None:
        raise ValueError(f"Invalid fixed UTC offset: {offset!r}")
    hours = int(match.group("hour"))
    minutes = int(match.group("minute"))
    if hours > 14 or minutes > 59 or (hours == 14 and minutes != 0):
        raise ValueError(f"UTC offset is outside the supported range: {offset!r}")
    seconds = (hours * 60 + minutes) * 60
    if match.group("sign") == "-":
        seconds = -seconds
    return timezone(timedelta(seconds=seconds))


def timezone_from_cf_units(units: str | None) -> timezone | None:
    """Return an explicit fixed offset encoded in CF time units, if present."""
    if units is None:
        return None
    match = _CF_TIME_UNITS.fullmatch(units)
    if match is None:
        return None
    origin_text = match.group("origin").strip().replace("Z", "+00:00")
    try:
        origin = datetime.fromisoformat(origin_text)
    except ValueError:
        return None
    if origin.tzinfo is None:
        return None
    offset = origin.utcoffset()
    if offset is None:
        return None
    return timezone(offset)


def offset_text(zone: timezone) -> str:
    """Format a fixed timezone as a signed ``HH:MM`` offset."""
    offset = zone.utcoffset(None)
    if offset is None:
        raise ValueError("A fixed timezone offset is required.")
    seconds = int(offset.total_seconds())
    if seconds % 60 != 0 or abs(seconds) > 14 * 60 * 60:
        raise ValueError(f"Unsupported timezone offset: {offset}")
    sign = "+" if seconds >= 0 else "-"
    hours, remainder = divmod(abs(seconds), 3600)
    minutes = remainder // 60
    return f"{sign}{hours:02d}:{minutes:02d}"
