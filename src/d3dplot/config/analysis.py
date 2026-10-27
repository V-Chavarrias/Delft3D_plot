# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared analysis configuration fields."""

from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import Field

from d3dplot.config.base import ConfigModel
from d3dplot.config.figure import FigureConfig
from d3dplot.config.selections import FractionSelection, TimesConfig, VerticalSelection

Comparison: TypeAlias = Literal[
    "value",
    "difference_time",
    "difference_simulation",
    "difference_simulation_time",
    "difference_simulation_relative",
]
ProcessingPolicy: TypeAlias = Literal["if_changed", "never", "always"]


def _default_comparisons() -> list[Comparison]:
    return ["value"]


class AnalysisConfigBase(ConfigModel):
    """Fields shared by built-in analyses."""

    variables: list[str] = Field(
        min_length=1,
        description="Canonical, aliased, or raw NetCDF quantity names.",
    )
    times: TimesConfig = Field(
        default_factory=TimesConfig,
        description="Requested outputs, time axis, and matching tolerance.",
    )
    active_sediment_thickness_threshold_m: float = Field(
        default=0.001,
        ge=0.0,
        description=(
            "Minimum total sediment thickness for morphological_active_area (ba_mor)."
        ),
    )
    time_matching: Literal["reference", "own"] = Field(
        default="reference",
        description="Match other simulations to the reference times or use their own.",
    )
    reprocess: ProcessingPolicy | None = Field(
        default=None,
        description="Analysis-level override for the project reprocess policy.",
    )
    replot: ProcessingPolicy | None = Field(
        default=None,
        description="Analysis-level override for the project replot policy.",
    )
    duplicate_morphological_times: Literal["last", "first"] = Field(
        default="last",
        description="Output to keep when morphological times are duplicated.",
    )
    comparisons: list[Comparison] = Field(
        default_factory=_default_comparisons,
        min_length=1,
        description="Comparisons to calculate from cached values.",
    )
    vertical: VerticalSelection = Field(
        default="all",
        description="Vertical layer selection or reduction.",
    )
    fractions: FractionSelection = Field(
        default="all",
        description="Sediment fractions to select.",
    )
    sum_fractions: bool = Field(
        default=False,
        description=(
            "Sum selected fractions instead of retaining a component dimension."
        ),
    )
    figure: FigureConfig = Field(
        default_factory=FigureConfig,
        description="Analysis-level figure settings that override project defaults.",
    )
    hooks: list[str] = Field(
        default_factory=list,
        description="Importable module:function hooks for this analysis.",
    )
    hooks_replace: bool = Field(
        default=False,
        description="Replace project hooks instead of extending them.",
    )
    on_missing_input: Literal["error", "skip"] = Field(
        default="error",
        description="Behavior when a configured optional input is missing.",
    )
    include_initial_time_difference: bool = Field(
        default=False,
        description="Include the zero difference at the first selected time.",
    )
    include_reference_in_differences: bool = Field(
        default=False,
        description="Include the reference simulation in difference figures.",
    )
    legend_labels: dict[str, str] = Field(
        default_factory=dict,
        description="Analysis-level legend label replacements.",
    )
