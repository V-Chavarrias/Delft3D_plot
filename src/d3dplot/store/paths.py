# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared path construction for analysis outputs."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from urllib.parse import quote

from d3dplot.store.layout import OutputLayout
from d3dplot.variables.registry import canonical_variable_name


class OutputArea(StrEnum):
    """Standard output trees used by analysis path builders."""

    CACHE_DATA = "cache_data"
    FIGURES = "figures"


def _quantity_path_segment(name: str) -> str:
    return quote(canonical_variable_name(name), safe="-_.")


def build_output_path(
    layout: OutputLayout,
    area: OutputArea,
    *parts: str | Path,
) -> Path:
    """Build a path beneath a standard output tree."""
    if area == OutputArea.CACHE_DATA:
        root = layout.cache_data
    elif area == OutputArea.FIGURES:
        root = layout.figures
    else:
        raise ValueError(f"Unsupported output area: {area!r}.")
    return root.joinpath(*parts)


def build_quantity_output_directory(
    layout: OutputLayout,
    area: OutputArea,
    variable: str,
    *,
    prefix: Sequence[str | Path] = (),
    suffix: Sequence[str | Path] = (),
) -> Path:
    """Build a quantity directory using its canonical registry name."""
    return build_output_path(
        layout,
        area,
        *prefix,
        _quantity_path_segment(variable),
        *suffix,
    )


def build_quantity_output_path(
    layout: OutputLayout,
    area: OutputArea,
    variable: str,
    *,
    prefix: Sequence[str | Path] = (),
    suffix: Sequence[str | Path] = (),
    filename: str | Path,
) -> Path:
    """Build a quantity file path using its canonical registry name."""
    return build_output_path(
        layout,
        area,
        *prefix,
        _quantity_path_segment(variable),
        *suffix,
        filename,
    )
