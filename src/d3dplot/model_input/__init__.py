# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only adapters for D-Flow FM model input."""

from d3dplot.model_input.dimr import resolve_dimr_mdu
from d3dplot.model_input.errors import ModelInputError
from d3dplot.model_input.mdu import MduInfo
from d3dplot.model_input.morphology import MorphologyInfo, read_morphology
from d3dplot.model_input.reader import ModelInput, read_model_input
from d3dplot.model_input.sediment import SedimentFraction, SedimentInfo, read_sediment

__all__ = [
    "MduInfo",
    "ModelInput",
    "ModelInputError",
    "MorphologyInfo",
    "SedimentFraction",
    "SedimentInfo",
    "read_model_input",
    "read_morphology",
    "read_sediment",
    "resolve_dimr_mdu",
]
