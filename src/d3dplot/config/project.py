# SPDX-License-Identifier: GPL-3.0-or-later
"""Project-level configuration models."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, PositiveInt, StrictInt, StrictStr, field_validator

from d3dplot.analyses.map2d.config import Map2DConfig
from d3dplot.analyses.river_km_statistics.config import RiverKmStatisticsConfig
from d3dplot.config.analysis import AnalysisConfigBase, ProcessingPolicy
from d3dplot.config.base import ConfigModel, FixedUTCOffset
from d3dplot.config.figure import FigureConfig

AnalysisConfig: TypeAlias = Annotated[
    Map2DConfig | RiverKmStatisticsConfig,
    Field(discriminator="analysis"),
]


class SimulationConfig(ConfigModel):
    """One model simulation and its optional output-directory override."""

    path: Path = Field(
        description="Simulation folder containing model inputs and outputs."
    )
    label: str = Field(
        min_length=1, description="Unique project label for this simulation."
    )
    output_dir: Path | None = Field(
        default=None,
        description="Optional base directory for this simulation's d3dplot output.",
    )


class OutputConfig(ConfigModel):
    """Project output root, forbidden roots, and cache precision."""

    root: Path | None = Field(
        default=None,
        description="Optional shared root for generated d3dplot output.",
    )
    forbidden_roots: list[Path] = Field(
        default_factory=list,
        description="Directories that generated output must never be placed inside.",
    )
    dtype: Literal["float32", "float64"] = Field(
        default="float32",
        description="Floating-point type used for cached values.",
    )


class PathsConfig(ConfigModel):
    """Cross-platform path mappings applied to configured paths."""

    mappings: dict[str, str] = Field(
        default_factory=lambda: {"p:\\": "/p/"},
        description="Case-insensitive path prefixes mapped between operating systems.",
    )


class SlurmConfig(ConfigModel):
    """SLURM job-script defaults."""

    nodes: PositiveInt = Field(default=1, description="Number of SLURM nodes.")
    tasks_per_node: PositiveInt = Field(default=4, description="Tasks per node.")
    partition: str = Field(default="4vcpu", description="SLURM partition name.")
    time_limit: str = Field(default="12:00:00", description="SLURM wall-clock limit.")
    job_name: str | None = Field(
        default=None,
        description="Optional job name; otherwise derived from the stage.",
    )


class ExecutionConfig(ConfigModel):
    """Task executor and parallel-work settings."""

    executor: Literal["serial", "processes", "slurm"] = Field(
        default="processes",
        description="Execution backend.",
    )
    workers: PositiveInt | None = Field(
        default=None,
        description="Worker count; null selects a value from available resources.",
    )
    shard: str | None = Field(
        default=None,
        description="Optional zero-based shard in the form index/count.",
    )
    process_block_times: PositiveInt = Field(
        default=50,
        description="Maximum model output times processed in one task block.",
    )
    slurm: SlurmConfig = Field(
        default_factory=SlurmConfig,
        description="SLURM job-script defaults.",
    )

    @field_validator("shard")
    @classmethod
    def validate_shard(cls, value: str | None) -> str | None:
        if value is None:
            return None

        match = re.fullmatch(r"(0|[1-9]\d*)/([1-9]\d*)", value)
        if match is None:
            raise ValueError("shard must use the form index/count")
        index, count = (int(part) for part in match.groups())
        if index >= count:
            raise ValueError("shard index must be less than its count")
        return value


class LabelsConfig(ConfigModel):
    """Label dictionaries and vertical-datum settings."""

    vertical_datum: str = Field(
        default="+NAP",
        description="Vertical datum shown in figure labels.",
    )
    extra_dictionaries: list[Path] = Field(
        default_factory=list,
        description="Additional translation dictionaries.",
    )


class ProjectConfig(ConfigModel):
    """Validated project settings shared by analyses and output stages."""

    simulations: list[SimulationConfig] = Field(
        min_length=1,
        description="Model simulations included in this project.",
    )
    reference_simulation: StrictStr | StrictInt = Field(
        default=0,
        description=(
            "Reference simulation label or zero-based index; defaults to the first."
        ),
    )
    crs: str | None = Field(
        default=None,
        description="Coordinate reference system used when model files have no CRS.",
    )
    time_zone: FixedUTCOffset | None = Field(
        default=None,
        description="Fixed UTC offset used when model time units do not provide one.",
    )
    language: Literal["en", "nl", "es"] = Field(
        default="en",
        description="Language used for generated figure labels.",
    )
    series: str = Field(
        default="01",
        description="Figure-series subfolder name.",
    )
    output: OutputConfig = Field(
        default_factory=OutputConfig,
        description="Output location and cache settings.",
    )
    paths: PathsConfig = Field(
        default_factory=PathsConfig,
        description="Cross-platform path mappings.",
    )
    execution: ExecutionConfig = Field(
        default_factory=ExecutionConfig,
        description="Executor and parallel-work settings.",
    )
    labels: LabelsConfig = Field(
        default_factory=LabelsConfig,
        description="Label dictionaries and vertical datum.",
    )
    figure: FigureConfig = Field(
        default_factory=FigureConfig,
        description="Default figure style inherited by analyses.",
    )
    hooks: list[str] = Field(
        default_factory=list,
        description="Importable module:function hooks applied to every figure.",
    )
    tiles_cache_dir: Path | None = Field(
        default=None,
        description="Optional persistent cache directory for background tiles.",
    )
    reprocess: ProcessingPolicy = Field(
        default="if_changed",
        description="Policy for reusing or rebuilding cached analysis data.",
    )
    replot: ProcessingPolicy = Field(
        default="if_changed",
        description="Policy for reusing or rebuilding figures.",
    )
    on_corrupt_output: Literal["mask", "error", "skip"] = Field(
        default="mask",
        description="Handling of corrupt model output.",
    )
    analyses: list[AnalysisConfig] = Field(
        min_length=1,
        description="Built-in analyses to run.",
    )

    def effective_figure(self, analysis: AnalysisConfigBase) -> FigureConfig:
        """Merge an analysis's explicit figure fields over project defaults."""
        analysis_values = analysis.model_dump(mode="python", exclude_unset=True)
        figure_override = analysis_values.get("figure", {})
        figure_values = self.figure.model_dump(mode="python")
        if isinstance(figure_override, dict):
            figure_values.update(figure_override)
        return FigureConfig.model_validate(figure_values)

    def effective_hooks(self, analysis: AnalysisConfigBase) -> list[str]:
        """Combine project and analysis hooks using the lower-level replacement rule."""
        if analysis.hooks_replace:
            return list(analysis.hooks)
        return [*self.hooks, *analysis.hooks]

    def effective_reprocess(self, analysis: AnalysisConfigBase) -> ProcessingPolicy:
        """Resolve an analysis-level reprocess override against the project default."""
        return analysis.reprocess or self.reprocess

    def effective_replot(self, analysis: AnalysisConfigBase) -> ProcessingPolicy:
        """Resolve an analysis-level replot override against the project default."""
        return analysis.replot or self.replot
