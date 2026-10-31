# SPDX-License-Identifier: GPL-3.0-or-later
"""Formatting helpers for time labels in figures."""

from __future__ import annotations

import re
from datetime import datetime

_TIME_FORMAT_TOKENS = {
    "yyyy": "%Y",
    "yy": "%y",
    "HH": "%H",
    "MM": "%M",
    "mm": "%m",
    "dd": "%d",
    "ss": "%S",
}
_TIME_FORMAT_PATTERN = re.compile(
    "|".join(sorted(_TIME_FORMAT_TOKENS, key=len, reverse=True))
)


def format_time_label(value: datetime, time_format: str) -> str:
    """Format a time with d3dplot tokens or Python strftime directives."""
    if "%" in time_format:
        return value.strftime(time_format)
    return value.strftime(
        _TIME_FORMAT_PATTERN.sub(
            lambda match: _TIME_FORMAT_TOKENS[match.group(0)],
            time_format,
        )
    )
