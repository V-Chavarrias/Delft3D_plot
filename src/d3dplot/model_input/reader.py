# SPDX-License-Identifier: GPL-3.0-or-later
"""Read the MDU and any referenced morphology and sediment files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from d3dplot.model_input.mdu import MduInfo
from d3dplot.model_input.morphology import MorphologyInfo, read_morphology
from d3dplot.model_input.sediment import SedimentInfo, read_sediment


@dataclass(frozen=True, slots=True)
class ModelInput:
    """Parsed, read-only input metadata for one D-Flow FM simulation."""

    mdu: MduInfo
    morphology: MorphologyInfo | None
    sediment: SedimentInfo | None


def read_model_input(path: Path) -> ModelInput:
    """Read an MDU and the ``.mor``/``.sed`` files it references."""
    mdu = MduInfo.from_path(path)
    morphology = read_morphology(mdu.mor_file) if mdu.mor_file is not None else None
    sediment = read_sediment(mdu.sed_file) if mdu.sed_file is not None else None
    return ModelInput(mdu=mdu, morphology=morphology, sediment=sediment)
