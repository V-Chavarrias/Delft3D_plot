# SPDX-License-Identifier: GPL-3.0-or-later
"""Read sediment fractions from D-Flow FM ``.sed`` files."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from d3dplot.model_input.ini import (
    optional_float,
    optional_text,
    parse_ini_file,
    section_mappings,
)
from d3dplot.paths import resolve_path_preserving_drive


@dataclass(frozen=True, slots=True)
class SedimentFraction:
    """Properties of one sediment fraction."""

    name: str | None
    sediment_type: str | None
    diameter_m: float | None
    minimum_diameter_m: float | None
    maximum_diameter_m: float | None
    solid_density_kg_m3: float | None


@dataclass(frozen=True, slots=True)
class SedimentInfo:
    """Sediment fractions parsed from a ``.sed`` file."""

    fractions: tuple[SedimentFraction, ...]


def read_sediment(path: Path) -> SedimentInfo:
    """Read each repeated ``[Sediment]`` section with HYDROLIB-core."""
    sed_path = resolve_path_preserving_drive(path)
    document = parse_ini_file(sed_path)
    sections = section_mappings(
        document.get("sediment"),
        path=sed_path,
        section_name="Sediment",
    )
    fractions = tuple(
        _read_fraction(section, path=sed_path, index=index)
        for index, section in enumerate(sections, start=1)
    )
    return SedimentInfo(fractions)


def _read_fraction(
    section: Mapping[str, object],
    *,
    path: Path,
    index: int,
) -> SedimentFraction:
    prefix = f"[Sediment] fraction {index}"
    return SedimentFraction(
        name=optional_text(
            section.get("name"),
            path=path,
            field_name=f"{prefix}.Name",
        ),
        sediment_type=optional_text(
            section.get("sedtyp"),
            path=path,
            field_name=f"{prefix}.SedTyp",
        ),
        diameter_m=optional_float(
            section.get("seddia"),
            path=path,
            field_name=f"{prefix}.SedDia",
        ),
        minimum_diameter_m=optional_float(
            section.get("sedmindia"),
            path=path,
            field_name=f"{prefix}.SedMinDia",
        ),
        maximum_diameter_m=optional_float(
            section.get("sedmaxdia"),
            path=path,
            field_name=f"{prefix}.SedMaxDia",
        ),
        solid_density_kg_m3=optional_float(
            section.get("rhosol"),
            path=path,
            field_name=f"{prefix}.RhoSol",
        ),
    )
