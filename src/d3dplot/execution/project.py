# SPDX-License-Identifier: GPL-3.0-or-later
"""Native project-stage dispatch shared by CLI workflows."""

from __future__ import annotations

from dataclasses import dataclass

from d3dplot.analyses.m1d.config import M1DConfig
from d3dplot.analyses.m1d.stage import M1DStageResult, plot_m1d_stage
from d3dplot.config.project import ProjectConfig


class ProjectStageUnavailable(RuntimeError):
    """The native project runner does not yet support a configured analysis."""


@dataclass(frozen=True, slots=True)
class ProjectRunResult:
    """Normalized result returned by a native project stage."""

    output_paths: tuple[str, ...]
    reused_paths: tuple[str, ...]


def run_native_project(project: ProjectConfig) -> ProjectRunResult:
    """Run the native stage path for analyses supported by the orchestrator."""
    if not project.analyses or not all(
        isinstance(analysis, M1DConfig) for analysis in project.analyses
    ):
        raise ProjectStageUnavailable(
            "The native project runner currently supports M1D-only projects."
        )
    result: M1DStageResult = plot_m1d_stage(project)
    return ProjectRunResult(
        output_paths=tuple(str(path) for path in result.output_paths),
        reused_paths=tuple(str(path) for path in result.reused_paths),
    )
