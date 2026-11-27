# SPDX-License-Identifier: GPL-3.0-or-later
"""Colormap convention shared by all figures, selected by comparison kind."""

from __future__ import annotations

from collections.abc import Mapping

VALUE_COLORMAP = "turbo"
TIME_DIFFERENCE_COLORMAP = "RdYlBu"
SIMULATION_DIFFERENCE_COLORMAP = "RdYlGn"


def default_colormap(comparison: str) -> str:
    """Return the convention colormap for a value or difference comparison."""
    if comparison == "difference_time":
        return TIME_DIFFERENCE_COLORMAP
    if comparison.startswith("difference_simulation"):
        return SIMULATION_DIFFERENCE_COLORMAP
    return VALUE_COLORMAP


def resolve_colormap(
    comparison: str,
    configured: str | Mapping[str, str] | None = None,
    variable: str | None = None,
) -> str:
    """Return the colormap of a figure, honouring an explicit configuration.

    A configured mapping is keyed by variable (case-insensitive); a variable
    without an entry falls back to the convention colormap.
    """
    if configured is None:
        return default_colormap(comparison)
    if isinstance(configured, str):
        return configured
    if variable is not None:
        for name, colormap in configured.items():
            if name.casefold() == variable.casefold():
                return colormap
    return default_colormap(comparison)
