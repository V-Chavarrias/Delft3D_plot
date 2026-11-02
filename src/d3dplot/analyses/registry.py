# SPDX-License-Identifier: GPL-3.0-or-later
"""Built-in analysis registry used by configuration and command workflows."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import TypeAlias

from pydantic import BaseModel

from d3dplot.analyses.m1d.config import M1DConfig
from d3dplot.analyses.map2d.config import Map2DConfig
from d3dplot.analyses.river_km_statistics.config import RiverKmStatisticsConfig

AnalysisConfigType: TypeAlias = type[BaseModel]

_BUILTIN_ANALYSES: Mapping[str, AnalysisConfigType] = MappingProxyType(
    {
        "m1d": M1DConfig,
        "map2d": Map2DConfig,
        "river_km_statistics": RiverKmStatisticsConfig,
    }
)


def builtin_analysis_configs() -> Mapping[str, AnalysisConfigType]:
    """Return the immutable built-in analysis discriminator registry."""
    return _BUILTIN_ANALYSES


def builtin_analysis_names() -> tuple[str, ...]:
    """Return built-in analysis names in stable sorted order."""
    return tuple(sorted(_BUILTIN_ANALYSES))


def is_builtin_analysis(name: str) -> bool:
    """Return whether a discriminator names a built-in analysis."""
    return name in _BUILTIN_ANALYSES
