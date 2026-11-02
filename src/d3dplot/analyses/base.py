# SPDX-License-Identifier: GPL-3.0-or-later
"""Public config-only analysis plugin contract."""

from __future__ import annotations

from dataclasses import dataclass

from d3dplot.config.analysis import AnalysisConfigBase


@dataclass(frozen=True, slots=True)
class AnalysisPlugin:
    """Entry-point value for an analysis that extends project configuration."""

    config_model: type[AnalysisConfigBase]
