# SPDX-License-Identifier: GPL-3.0-or-later
"""Read morphology values from D-Flow FM ``.mor`` files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from d3dplot.model_input.errors import ModelInputError
from d3dplot.model_input.ini import optional_float, parse_ini_file, section_mappings


@dataclass(frozen=True, slots=True)
class MorphologyInfo:
    """Morphology scale factor and start time read from a ``.mor`` file."""

    morfac: float | None
    morstt: float | None


def read_morphology(path: Path) -> MorphologyInfo:
    """Read ``MorFac`` and ``MorStt`` with HYDROLIB-core's INI parser."""
    mor_path = path.resolve(strict=False)
    document = parse_ini_file(mor_path)
    sections = section_mappings(
        document.get("morphology"),
        path=mor_path,
        section_name="Morphology",
    )
    if len(sections) != 1:
        raise ModelInputError(
            mor_path,
            f"Expected one [Morphology] section, found {len(sections)}.",
            "Check the morphology file for a single [Morphology] section.",
        )

    section = sections[0]
    return MorphologyInfo(
        morfac=optional_float(
            section.get("morfac"),
            path=mor_path,
            field_name="[Morphology].MorFac",
        ),
        morstt=optional_float(
            section.get("morstt"),
            path=mor_path,
            field_name="[Morphology].MorStt",
        ),
    )
