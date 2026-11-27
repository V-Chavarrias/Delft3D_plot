# SPDX-License-Identifier: GPL-3.0-or-later
"""Axis and colour limit rows of profile figures, shared by rendering and validation."""

from __future__ import annotations

from typing import Any, TypeAlias

# (lower, upper) limits; None for a bound means automatic.
LimitRow: TypeAlias = tuple[float | None, float | None] | None


def limit_rows(
    configured: object,
    variable: str,
    comparison: str,
    *,
    option: str,
) -> list[LimitRow]:
    """Resolve limit rows for one variable and comparison; one figure per row.

    A null row means automatic limits. A variable or comparison missing from the
    dictionary form also means automatic limits.
    """
    if configured is None:
        return [None]
    if isinstance(configured, list):
        return _limit_pairs(configured)
    if isinstance(configured, dict):
        variable_limits = configured.get(variable)
        if variable_limits is None:
            return [None]
        if isinstance(variable_limits, dict):
            comparison_limits = variable_limits.get(comparison)
            if comparison_limits is None:
                return [None]
            if isinstance(comparison_limits, list) and comparison_limits:
                return _limit_pairs(comparison_limits)
    raise ValueError(
        f"Unsupported PRF {option} for variable {variable!r} and comparison "
        f"{comparison!r}."
    )


def paired_limits(
    y_rows: list[LimitRow],
    colour_rows: list[LimitRow],
) -> list[tuple[LimitRow, LimitRow]]:
    """Pair elevation and colour limits; one row applies to every figure."""
    if len(y_rows) == 1:
        y_rows = y_rows * len(colour_rows)
    if len(colour_rows) == 1:
        colour_rows = colour_rows * len(y_rows)
    if len(y_rows) != len(colour_rows):
        raise ValueError(
            "PRF section y_limits and color_limits need the same number of rows, or "
            f"one row for every figure; got {len(y_rows)} and {len(colour_rows)}."
        )
    return list(zip(y_rows, colour_rows, strict=True))


def _limit_pairs(rows: list[Any]) -> list[LimitRow]:
    return [None if row is None else (row[0], row[1]) for row in rows]
