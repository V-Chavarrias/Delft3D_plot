# SPDX-License-Identifier: GPL-3.0-or-later
"""Native project-stage dispatch shared by CLI workflows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from d3dplot.analyses.registry import is_builtin_analysis
from d3dplot.config.project import ProjectConfig


class ProjectStageUnavailable(RuntimeError):
    """A requested project stage is not supported by this d3dplot version."""


@dataclass(frozen=True, slots=True)
class ProjectRunResult:
    """Normalized result returned by a native project stage."""

    output_paths: tuple[str, ...]
    reused_paths: tuple[str, ...]
    exit_code: int = 0
    succeeded_tasks: int = 0
    failed_tasks: int = 0
    failed_simulations: tuple[str, ...] = ()
    failure_messages: tuple[str, ...] = ()


def ensure_executable_analyses(
    project: ProjectConfig,
    *,
    analysis_name: str | None = None,
) -> None:
    """Reject config-only plugin analyses before a runtime stage starts."""
    selected = tuple(
        analysis
        for analysis in project.analyses
        if analysis_name is None or analysis.analysis == analysis_name
    )
    if not selected:
        raise ValueError(f"No configured analysis matches {analysis_name!r}.")
    unsupported = tuple(
        analysis.analysis
        for analysis in selected
        if not is_builtin_analysis(analysis.analysis)
    )
    if unsupported:
        names = ", ".join(sorted(set(unsupported)))
        raise ProjectStageUnavailable(
            f"Analysis plugin(s) {names} are config-only: d3dplot validates and "
            "exports their models, but process, run, and plot cannot execute them."
        )


def run_native_project(
    project: ProjectConfig,
    *,
    configuration_path: Path | None = None,
    analysis_name: str | None = None,
    executor: Literal["serial", "processes", "slurm"] | None = None,
    workers: int | None = None,
    shard: str | None = None,
) -> ProjectRunResult:
    """Run the native process stage for registered built-in analyses."""
    ensure_executable_analyses(project, analysis_name=analysis_name)
    from d3dplot.execution.project_runner import run_project

    return run_project(
        project,
        configuration_path=configuration_path,
        analysis_name=analysis_name,
        executor=executor,
        workers=workers,
        shard=shard,
    )
