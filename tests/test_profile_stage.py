# SPDX-License-Identifier: GPL-3.0-or-later
"""End-to-end tests for the PRF profile stage on the synthetic SMT case."""

from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from d3dplot import __version__
from d3dplot.analyses.profile import process as profile_process
from d3dplot.analyses.profile.config import ProfileConfig
from d3dplot.analyses.profile.stage import ProfileStageError, profile_selections
from d3dplot.config.project import ProjectConfig
from d3dplot.execution.diagnostics import DiagnosticLog
from d3dplot.execution.project_runner import (
    ProfilePlotResult,
    plot_profile_analyses,
    run_project,
)
from d3dplot.store import OutputLayout


def _profile_config(**options: Any) -> ProfileConfig:
    return ProfileConfig.model_validate(
        {
            "analysis": "profile",
            "lines": [{"name": "section", "file": "section.pli"}],
            **options,
        }
    )


@pytest.mark.unit
def test_profile_selections_keep_a_non_sediment_variable_whole() -> None:
    selections = profile_selections(_profile_config(variables=["water_level"]))

    assert len(selections) == 1
    assert selections[0].variable == "water_level"
    assert selections[0].fractions is None
    assert selections[0].sum_fractions is False


@pytest.mark.unit
def test_profile_selections_split_explicit_fractions_into_one_series_each() -> None:
    selections = profile_selections(
        _profile_config(variables=["layer_fraction"], fractions=[1, 3])
    )

    assert [selection.fractions for selection in selections] == [(1,), (3,)]
    assert all(selection.sum_fractions for selection in selections)


@pytest.mark.unit
def test_profile_selections_sum_explicit_fractions_into_one_selection() -> None:
    selections = profile_selections(
        _profile_config(
            variables=["layer_fraction"], fractions=[1, 3], sum_fractions=True
        )
    )

    assert len(selections) == 1
    assert selections[0].fractions == (1, 3)
    assert selections[0].sum_fractions is True


@pytest.mark.unit
def test_profile_selections_reject_unsummed_fractions_of_a_sediment_variable() -> None:
    with pytest.raises(ProfileStageError, match="sediment fractions"):
        profile_selections(_profile_config(variables=["layer_fraction"]))


@pytest.mark.unit
def test_profile_selections_reject_fractions_for_a_non_sediment_variable() -> None:
    with pytest.raises(ProfileStageError, match="no sediment fraction dimension"):
        profile_selections(_profile_config(variables=["water_level"], fractions=[1]))


def _synthetic_centreline_pli(tmp_path: Path) -> Path:
    """Write a cross-section .pli that crosses the synthetic channel at mid-length."""
    tools_directory = str(
        Path(__file__).resolve().parents[1] / "tools" / "synthetic_smt"
    )
    if tools_directory not in sys.path:
        sys.path.insert(0, tools_directory)

    mesh = import_module("mesh")
    params = import_module("params")

    centreline = mesh.make_centreline(params.Params())
    x, y = centreline.xy(5_000.0, np.array([-300.0, 300.0]))
    path = tmp_path / "section.pli"
    lines = [
        "section",
        "   2   2",
        f"  {x[0]:.3f}  {y[0]:.3f}",
        f"  {x[1]:.3f}  {y[1]:.3f}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _project(
    synthetic_case: Path,
    output_root: Path,
    pli_path: Path,
    analysis: dict[str, Any],
) -> ProjectConfig:
    return ProjectConfig.model_validate(
        {
            "simulations": [
                {
                    "path": str(synthetic_case / "simulations" / "r001"),
                    "label": "r001",
                }
            ],
            "output": {"root": str(output_root)},
            "execution": {"executor": "serial"},
            "analyses": [
                {
                    "analysis": "profile",
                    "variables": ["water_level"],
                    "lines": [{"name": "section", "file": str(pli_path)}],
                    **analysis,
                }
            ],
        }
    )


def _plot_profiles(project: ProjectConfig) -> ProfilePlotResult:
    layouts = [
        OutputLayout.for_simulation(simulation, project.output)
        for simulation in project.simulations
    ]
    with DiagnosticLog.for_project(
        layouts,
        tool_version=__version__,
        command_line=("pytest",),
        forbidden_roots=project.output.forbidden_roots,
    ) as diagnostics:
        return plot_profile_analyses(project, diagnostics=diagnostics)


@pytest.mark.integration
def test_profile_process_writes_caches_without_figures(
    synthetic_case: Path,
    tmp_path: Path,
) -> None:
    pli_path = _synthetic_centreline_pli(tmp_path)
    output_root = tmp_path / "output"
    project = _project(synthetic_case, output_root, pli_path, {"views": ["profile"]})

    result = run_project(project, executor="serial")

    assert result.failed_simulations == ()
    assert result.output_paths == ()
    assert list(output_root.rglob("*.png")) == []


@pytest.mark.integration
def test_profile_plot_renders_figures_from_the_process_caches(
    synthetic_case: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pli_path = _synthetic_centreline_pli(tmp_path)
    project = _project(
        synthetic_case,
        tmp_path / "output",
        pli_path,
        {"views": ["profile"]},
    )
    run_project(project, executor="serial")

    def fail_on_sub_run_read(*args: object, **kwargs: object) -> None:
        raise AssertionError("the plot stage should read the profile caches only")

    monkeypatch.setattr(profile_process, "read_profile_values", fail_on_sub_run_read)
    result = _plot_profiles(project)

    figures = [path for path in result.output_paths if path.suffix == ".png"]
    assert figures, "the plot stage should render at least one figure"
    assert all(path.is_file() for path in result.output_paths)


@pytest.mark.integration
def test_profile_bed_level_shows_the_channel_and_space_time_view(
    synthetic_case: Path,
    tmp_path: Path,
) -> None:
    pli_path = _synthetic_centreline_pli(tmp_path)
    project = _project(
        synthetic_case,
        tmp_path / "output",
        pli_path,
        {"variables": ["bed_level"], "views": ["profile", "space_time"]},
    )

    run_result = run_project(project, executor="serial")
    result = _plot_profiles(project)

    assert run_result.failed_simulations == ()
    profile_figures = [
        path
        for path in result.output_paths
        if "profile" in path.parts and path.suffix == ".png"
    ]
    space_time_figures = [
        path
        for path in result.output_paths
        if "space_time" in path.parts and path.suffix == ".png"
    ]
    assert profile_figures
    assert len(space_time_figures) == 1
    assert all(path.is_file() for path in result.output_paths)


@pytest.mark.integration
def test_second_profile_plot_with_replot_never_reuses_figures(
    synthetic_case: Path,
    tmp_path: Path,
) -> None:
    pli_path = _synthetic_centreline_pli(tmp_path)
    output_root = tmp_path / "output"
    project = _project(
        synthetic_case,
        output_root,
        pli_path,
        {"views": ["profile"], "replot": "never"},
    )

    run_project(project, executor="serial")
    first = _plot_profiles(project)
    second_run = run_project(project, executor="serial")
    second = _plot_profiles(project)

    assert first.output_paths
    assert second_run.failed_simulations == ()
    assert second_run.output_paths == ()
    assert second.output_paths == ()
    assert set(second.reused_paths) == set(first.output_paths)
