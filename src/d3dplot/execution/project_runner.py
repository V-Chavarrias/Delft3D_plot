# SPDX-License-Identifier: GPL-3.0-or-later
"""Native, multi-simulation orchestration for the process stage."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from d3dplot import __version__
from d3dplot.analyses.map1d.config import Map1DConfig
from d3dplot.analyses.map1d.process import Map1DProcessError
from d3dplot.analyses.map1d.stage import process_map1d_stage
from d3dplot.analyses.map2d.config import VELOCITY_COMPONENT_VARIABLES, Map2DConfig
from d3dplot.analyses.map2d.process import (
    Map2DProcessError,
    Map2DProcessResult,
    Map2DTaskProcessor,
)
from d3dplot.analyses.profile.config import ProfileConfig
from d3dplot.analyses.profile.stage import ProfileStageError, process_profile_analysis
from d3dplot.analyses.river_km_statistics.config import RiverKmStatisticsConfig
from d3dplot.analyses.river_km_statistics.process import (
    select_time_average_outputs,
)
from d3dplot.config.analysis import AnalysisConfigBase, ProcessingPolicy
from d3dplot.config.project import ProjectConfig, SimulationConfig
from d3dplot.config.selections import (
    DepthAverageSelection,
    ElevationSelection,
    LayersSelection,
)
from d3dplot.execution.cache import CacheFingerprintReadError
from d3dplot.execution.diagnostics import DiagnosticLog
from d3dplot.execution.orchestrator import run_process_stage
from d3dplot.execution.project import (
    ProjectRunResult,
    ProjectStageUnavailable,
)
from d3dplot.execution.runner import ExecutorName
from d3dplot.execution.tasks import (
    AnalysisTimeSelection,
    ProcessTask,
    plan_process_tasks,
    select_process_shard,
)
from d3dplot.simulation.discover import Simulation, discover_simulation
from d3dplot.simulation.inspection import (
    InspectionResult,
    inspect_simulation,
)
from d3dplot.simulation.time_axis import TimeAxis, TimeSelectionResult
from d3dplot.store import (
    OutputLayout,
    OutputWriteError,
    OutputWriteGuard,
    initialize_output_root,
)
from d3dplot.validate import ValidationReport, write_validation_report
from d3dplot.validate.report import ValidationIssue
from d3dplot.variables.context import QuantityRequest


@dataclass(frozen=True, slots=True)
class _PreparedSimulation:
    config: SimulationConfig
    layout: OutputLayout
    guard: OutputWriteGuard
    processor: Map2DTaskProcessor
    tasks: tuple[ProcessTask, ...]


def _quantity_requests(analysis: AnalysisConfigBase) -> tuple[QuantityRequest, ...]:
    vertical: Literal[
        "all",
        "top",
        "bottom",
        "depth_average",
        "elevation",
    ]
    if analysis.vertical == "all":
        layers = None
        vertical = "all"
        z_range = None
        elevation = None
    elif isinstance(analysis.vertical, LayersSelection):
        layers = tuple(analysis.vertical.layers)
        vertical = "all"
        z_range = None
        elevation = None
    elif analysis.vertical in {"top", "bottom"}:
        layers = None
        vertical = analysis.vertical
        z_range = None
        elevation = None
    elif isinstance(analysis.vertical, DepthAverageSelection):
        layers = None
        vertical = "depth_average"
        z_range = analysis.vertical.depth_average.z_range
        elevation = None
    elif isinstance(analysis.vertical, ElevationSelection):
        layers = None
        vertical = "elevation"
        z_range = None
        elevation = analysis.vertical.elevation
    else:
        raise ValueError("Unsupported vertical selection.")

    fractions = None if analysis.fractions == "all" else tuple(analysis.fractions)
    names = list(analysis.variables)
    if isinstance(analysis, Map2DConfig) and analysis.velocity_vectors != "none":
        names.extend(VELOCITY_COMPONENT_VARIABLES)
    return tuple(
        dict.fromkeys(
            QuantityRequest(
                requested_name=name,
                fractions=fractions,
                layers=layers,
                sum_fractions=analysis.sum_fractions,
                active_sediment_thickness_threshold_m=(
                    analysis.active_sediment_thickness_threshold_m
                ),
                vertical=vertical,
                z_range=z_range,
                elevation=elevation,
            )
            for name in names
        )
    )


def _record_issue(diagnostics: DiagnosticLog, issue: ValidationIssue) -> None:
    if issue.level == "error":
        diagnostics.error(f"{issue.code}: {issue.message}")
    elif issue.level == "warning":
        diagnostics.warning(f"{issue.code}: {issue.message}")
    else:
        diagnostics.info(f"{issue.code}: {issue.message}")


def _write_preflight_report(
    report: ValidationReport,
    layout: OutputLayout,
    project: ProjectConfig,
    diagnostics: DiagnosticLog,
) -> None:
    guard = initialize_output_root(
        layout,
        tool_version=__version__,
        forbidden_roots=project.output.forbidden_roots,
    )
    report_path = write_validation_report(report, layout, guard)
    diagnostics.info(f"Validation report written: {report_path}.")


def _inspection_for_simulation(
    project: ProjectConfig,
    simulation_config: SimulationConfig,
    layout: OutputLayout,
    diagnostics: DiagnosticLog,
) -> tuple[tuple[Simulation, InspectionResult] | None, tuple[str, ...]]:
    diagnostics.info(f"Starting simulation discovery: {simulation_config.path}.")
    simulation, discovery_report = discover_simulation(
        simulation_config.path,
        check_map_headers=project.check_map_headers,
        output_layout=layout,
    )
    diagnostics.info(
        "Simulation discovery finished: "
        f"found={simulation is not None}; issues={len(discovery_report.issues)}."
    )
    if simulation is None:
        _write_preflight_report(discovery_report, layout, project, diagnostics)
        return None, tuple(issue.code for issue in discovery_report.issues)

    diagnostics.info(
        f"Starting output inspection: simulation={simulation_config.label}."
    )
    inspection, inspection_report = inspect_simulation(
        simulation,
        layout,
        crs=project.crs,
        time_zone=(str(project.time_zone) if project.time_zone is not None else None),
        check_boundary_duplicates=project.check_boundary_duplicates,
        forbidden_roots=project.output.forbidden_roots,
    )
    diagnostics.record_validation_issues(inspection_report.issues)
    combined_report = ValidationReport(
        discovery_report.issues + inspection_report.issues
    )
    _write_preflight_report(combined_report, layout, project, diagnostics)
    diagnostics.info(
        "Output inspection finished: "
        f"valid={inspection_report.is_valid}; "
        f"issues={len(inspection_report.issues)}."
    )
    if inspection is None or not inspection_report.is_valid:
        return (
            None,
            tuple(issue.code for issue in combined_report.issues),
        )
    return (simulation, inspection), ()


def _selections_for_simulation(
    project: ProjectConfig,
    analyses: Sequence[tuple[int, AnalysisConfigBase]],
    inspection: InspectionResult,
    simulation_label: str,
    diagnostics: DiagnosticLog,
) -> tuple[list[AnalysisTimeSelection], dict[str, ProcessingPolicy]]:
    time_axis = TimeAxis.from_cache(inspection.times_path)
    diagnostics.info(f"Cached time axis loaded: entries={len(time_axis.entries)}.")
    time_zone = str(project.time_zone) if project.time_zone is not None else None
    selections: list[AnalysisTimeSelection] = []
    policies: dict[str, ProcessingPolicy] = {}

    for analysis_index, analysis in analyses:
        analysis_key = f"analysis-{analysis_index}-{analysis.analysis}"
        selection = time_axis.select(
            analysis.times,
            naive_time_zone=time_zone,
            duplicate_morphological_times=analysis.duplicate_morphological_times,
            on_corrupt_output=project.on_corrupt_output,
        )
        diagnostics.record_time_selection(
            analysis_key=analysis_key,
            simulation_label=simulation_label,
            requested=analysis.times.model_dump_json(),
            result=selection,
        )
        diagnostics.record_validation_issues(selection.issues)
        additional_selections: tuple[TimeSelectionResult, ...] = ()
        valid = selection.is_valid
        if isinstance(analysis, RiverKmStatisticsConfig):
            additional_selections = select_time_average_outputs(
                time_axis,
                analysis,
                on_corrupt_output=project.on_corrupt_output,
            )
            for average_index, (average, result) in enumerate(
                zip(analysis.time_averages, additional_selections, strict=True)
            ):
                diagnostics.record_time_selection(
                    analysis_key=f"{analysis_key}-average-{average_index}",
                    simulation_label=simulation_label,
                    requested=average.times.model_dump_json(),
                    result=result,
                )
                diagnostics.record_validation_issues(result.issues)
                valid = valid and result.is_valid
        if not valid:
            diagnostics.error(f"Invalid time selection for {analysis_key}.")
            continue

        selections.append(
            AnalysisTimeSelection(
                analysis_key=analysis_key,
                quantities=_quantity_requests(analysis),
                selection=selection,
                additional_selections=additional_selections,
            )
        )
        policies[analysis_key] = project.effective_reprocess(analysis)
    return selections, policies


def _process_profile_analyses(
    project: ProjectConfig,
    simulation_config: SimulationConfig,
    simulation: Simulation,
    inspection: InspectionResult,
    layout: OutputLayout,
    analyses: Sequence[tuple[int, ProfileConfig]],
    diagnostics: DiagnosticLog,
    output_paths: list[str],
    reused_paths: list[str],
    *,
    render_figures: bool,
) -> bool:
    """Process PRF analyses for one simulation; return True if any analysis failed."""
    guard = initialize_output_root(
        layout,
        tool_version=__version__,
        forbidden_roots=project.output.forbidden_roots,
    )
    time_axis = TimeAxis.from_cache(inspection.times_path)
    time_zone = str(project.time_zone) if project.time_zone is not None else None
    failed = False
    for analysis_index, analysis in analyses:
        analysis_key = f"analysis-{analysis_index}-{analysis.analysis}"
        selection = time_axis.select(
            analysis.times,
            naive_time_zone=time_zone,
            duplicate_morphological_times=analysis.duplicate_morphological_times,
            on_corrupt_output=project.on_corrupt_output,
        )
        diagnostics.record_time_selection(
            analysis_key=analysis_key,
            simulation_label=simulation_config.label,
            requested=analysis.times.model_dump_json(),
            result=selection,
        )
        diagnostics.record_validation_issues(selection.issues)
        if not selection.is_valid:
            diagnostics.error(f"Invalid time selection for {analysis_key}.")
            failed = True
            continue
        try:
            result = process_profile_analysis(
                project,
                simulation_config,
                simulation,
                inspection,
                analysis,
                analysis_index,
                selection.entries,
                layout,
                guard,
                render_figures=render_figures,
                diagnostic=diagnostics.info,
            )
        except (ProfileStageError, OSError, OutputWriteError, ValueError) as error:
            diagnostics.error(
                f"PRF processing failed for {simulation_config.label}/"
                f"{analysis_key}: {error}"
            )
            failed = True
            continue
        output_paths.extend(str(path) for path in result.output_paths)
        reused_paths.extend(str(path) for path in result.reused_paths)
        diagnostics.info(
            f"PRF processing finished: {analysis_key}; "
            f"figures={len(result.output_paths)}; "
            f"reused={len(result.reused_paths)}."
        )
    return failed


@dataclass(frozen=True, slots=True)
class ProfilePlotResult:
    """Figures written or reused by the PRF plot stage."""

    output_paths: tuple[Path, ...]
    reused_paths: tuple[Path, ...]


def plot_profile_analyses(
    project: ProjectConfig,
    *,
    analysis_name: str | None = None,
    simulation_label: str | None = None,
    diagnostics: DiagnosticLog | None,
) -> ProfilePlotResult:
    """Render PRF figures from the profile caches written by the process stage."""
    analyses = tuple(
        (index, analysis)
        for index, analysis in enumerate(project.analyses)
        if isinstance(analysis, ProfileConfig)
        and (analysis_name is None or analysis.analysis == analysis_name)
    )
    if not analyses:
        return ProfilePlotResult((), ())
    if diagnostics is None:
        raise ValueError("PRF plotting requires a diagnostics log.")
    output_paths: list[str] = []
    reused_paths: list[str] = []
    for simulation_config in project.simulations:
        if simulation_label is not None and simulation_config.label != simulation_label:
            continue
        layout = OutputLayout.for_simulation(simulation_config, project.output)
        inspected, _ = _inspection_for_simulation(
            project, simulation_config, layout, diagnostics
        )
        if inspected is None:
            raise ValueError(
                f"PRF plotting needs a valid output inspection for "
                f"{simulation_config.label}."
            )
        simulation, inspection = inspected
        if _process_profile_analyses(
            project,
            simulation_config,
            simulation,
            inspection,
            layout,
            analyses,
            diagnostics,
            output_paths,
            reused_paths,
            render_figures=True,
        ):
            raise ValueError("PRF plotting failed; see the diagnostics log.")
    return ProfilePlotResult(
        tuple(Path(path) for path in output_paths),
        tuple(Path(path) for path in reused_paths),
    )


def _planning_worker_count(
    executor: ExecutorName,
    workers: int | None,
) -> int:
    if executor == "serial":
        return 1
    return workers or os.cpu_count() or 1


def run_project(
    project: ProjectConfig,
    *,
    configuration_path: Path | None = None,
    executor: Literal["serial", "processes", "slurm"] | None = None,
    workers: int | None = None,
    shard: str | None = None,
    analysis_name: str | None = None,
) -> ProjectRunResult:
    """Process selected analyses for every configured simulation."""
    selected_analyses = tuple(
        (index, analysis)
        for index, analysis in enumerate(project.analyses)
        if analysis_name is None or analysis.analysis == analysis_name
    )
    if not selected_analyses:
        raise ValueError(f"No configured analysis matches {analysis_name!r}.")

    effective_executor = project.execution.executor if executor is None else executor
    if effective_executor == "slurm":
        raise ProjectStageUnavailable(
            "SLURM submission is not supported by the native process stage; "
            "use executor=serial or processes."
        )
    worker_count = project.execution.workers if workers is None else workers
    effective_shard = project.execution.shard if shard is None else shard
    map_analyses = tuple(
        item
        for item in selected_analyses
        if isinstance(item[1], (Map2DConfig, RiverKmStatisticsConfig))
    )
    map1d_analyses = tuple(
        item for item in selected_analyses if isinstance(item[1], Map1DConfig)
    )
    profile_analyses = tuple(
        (index, analysis)
        for index, analysis in selected_analyses
        if isinstance(analysis, ProfileConfig)
    )
    if effective_shard is not None and map1d_analyses:
        raise ProjectStageUnavailable(
            "map1d quantity processing does not support task sharding; "
            "remove map1d from the sharded project or run without --shard."
        )
    if effective_shard is not None and profile_analyses:
        raise ProjectStageUnavailable(
            "PRF profile processing does not support task sharding; "
            "remove PRF from the sharded project or run without --shard."
        )

    layouts = tuple(
        OutputLayout.for_simulation(simulation, project.output)
        for simulation in project.simulations
    )
    prepared_simulations: list[_PreparedSimulation] = []
    output_paths: list[str] = []
    reused_paths: list[str] = []
    failed_simulations: list[str] = []
    failure_messages: list[str] = []
    successful_tasks = 0
    failed_tasks = 0
    planner_workers = _planning_worker_count(effective_executor, worker_count)

    with DiagnosticLog.for_project(
        layouts,
        tool_version=__version__,
        command_line=tuple(sys.argv),
        forbidden_roots=project.output.forbidden_roots,
        configuration_path=configuration_path,
    ) as diagnostics:
        for simulation_config, layout in zip(
            project.simulations,
            layouts,
            strict=True,
        ):
            diagnostics.info(
                f"Starting project processing: simulation={simulation_config.label}."
            )
            simulation_failed = False

            if map1d_analyses:
                try:
                    map1d_result = process_map1d_stage(
                        project,
                        analysis_name=analysis_name,
                        simulation_label=simulation_config.label,
                        diagnostic=diagnostics.info,
                    )
                except (
                    Map1DProcessError,
                    OSError,
                    OutputWriteError,
                    ValueError,
                ) as error:
                    diagnostics.error(
                        f"map1d processing failed for {simulation_config.label}: "
                        f"{error}"
                    )
                    simulation_failed = True
                else:
                    output_paths.extend(str(path) for path in map1d_result.cache_paths)
                    reused_paths.extend(
                        str(path) for path in map1d_result.reused_cache_paths
                    )
                    diagnostics.info(
                        f"map1d processing finished: "
                        f"built={len(map1d_result.cache_paths)}; "
                        f"reused={len(map1d_result.reused_cache_paths)}."
                    )

            if not map_analyses and not profile_analyses:
                if simulation_failed:
                    failed_simulations.append(simulation_config.label)
                continue

            try:
                inspected, preflight_messages = _inspection_for_simulation(
                    project,
                    simulation_config,
                    layout,
                    diagnostics,
                )
                failure_messages.extend(preflight_messages)
            except (OSError, OutputWriteError, ValueError) as error:
                diagnostics.error(
                    f"Preflight failed for {simulation_config.label}: {error}"
                )
                failed_simulations.append(simulation_config.label)
                continue
            if inspected is None:
                failed_simulations.append(simulation_config.label)
                continue
            simulation, inspection = inspected

            if profile_analyses and _process_profile_analyses(
                project,
                simulation_config,
                simulation,
                inspection,
                layout,
                profile_analyses,
                diagnostics,
                output_paths,
                reused_paths,
                render_figures=False,
            ):
                simulation_failed = True

            if not map_analyses:
                if simulation_failed:
                    failed_simulations.append(simulation_config.label)
                continue

            try:
                selections, policies = _selections_for_simulation(
                    project,
                    map_analyses,
                    inspection,
                    simulation_config.label,
                    diagnostics,
                )
            except (OSError, ValueError) as error:
                diagnostics.error(
                    f"Time selection failed for {simulation_config.label}: {error}"
                )
                failed_simulations.append(simulation_config.label)
                continue
            if len(selections) != len(map_analyses):
                simulation_failed = True

            guard = initialize_output_root(
                layout,
                tool_version=__version__,
                forbidden_roots=project.output.forbidden_roots,
            )
            map2d_keys = tuple(
                f"analysis-{index}-{analysis.analysis}"
                for index, analysis in map_analyses
                if isinstance(analysis, Map2DConfig)
            )
            river_km_configs = {
                f"analysis-{index}-{analysis.analysis}": analysis
                for index, analysis in map_analyses
                if isinstance(analysis, RiverKmStatisticsConfig)
            }
            try:
                processor = Map2DTaskProcessor(
                    simulation,
                    simulation_config.label,
                    inspection,
                    layout,
                    guard,
                    analysis_keys=map2d_keys,
                    river_km_analyses=river_km_configs,
                    model_crs=project.crs,
                    dtype=project.output.dtype,
                    on_corrupt_output=project.on_corrupt_output,
                )
            except ValueError as error:
                diagnostics.error(
                    f"Task processor setup failed for "
                    f"{simulation_config.label}: {error}"
                )
                failed_simulations.append(simulation_config.label)
                continue

            cache_selections: list[AnalysisTimeSelection] = []
            for selection in selections:
                try:
                    cached_selection = processor.attach_output_cache_states(
                        (selection,),
                        {selection.analysis_key: policies[selection.analysis_key]},
                    )[0]
                except (
                    CacheFingerprintReadError,
                    Map2DProcessError,
                    OSError,
                    OutputWriteError,
                    ValueError,
                ) as error:
                    diagnostics.error(
                        f"Could not plan caches for "
                        f"{simulation_config.label}/{selection.analysis_key}: {error}"
                    )
                    simulation_failed = True
                    continue
                cache_selections.append(cached_selection)
                for state in cached_selection.output_cache:
                    decision = state.decision
                    if decision.action == "reuse":
                        reused_paths.append(state.output_path)
                    if decision.warning is not None:
                        diagnostics.warning(
                            f"{selection.analysis_key}: {decision.warning}"
                        )

            processor.finish_cache_planning()
            choices, issues = processor.cache_planning_diagnostics()
            for choice in choices:
                diagnostics.info(choice)
            for issue in issues:
                _record_issue(diagnostics, issue)

            try:
                tasks = plan_process_tasks(
                    simulation_config.label,
                    cache_selections,
                    workers=planner_workers,
                    max_block_times=project.execution.process_block_times,
                )
            except ValueError as error:
                diagnostics.error(
                    f"Task planning failed for {simulation_config.label}: {error}"
                )
                failed_simulations.append(simulation_config.label)
                continue
            processor.release_planning_resources()
            diagnostics.info(
                f"Process task plan ready: simulation={simulation_config.label}; "
                f"tasks={len(tasks)}."
            )
            prepared_simulations.append(
                _PreparedSimulation(
                    config=simulation_config,
                    layout=layout,
                    guard=guard,
                    processor=processor,
                    tasks=tasks,
                )
            )
            if simulation_failed and simulation_config.label not in failed_simulations:
                failed_simulations.append(simulation_config.label)

        all_tasks = tuple(
            task for prepared in prepared_simulations for task in prepared.tasks
        )
        selected_tasks = select_process_shard(all_tasks, effective_shard)
        tasks_by_simulation: dict[str, list[ProcessTask]] = {
            prepared.config.label: [] for prepared in prepared_simulations
        }
        for task in selected_tasks:
            tasks_by_simulation[task.simulation_label].append(task)
        if effective_shard is not None:
            diagnostics.info(
                f"Selected process shard {effective_shard}: "
                f"{len(selected_tasks)} of {len(all_tasks)} task(s)."
            )

        for prepared in prepared_simulations:
            tasks = tuple(tasks_by_simulation[prepared.config.label])
            diagnostics.info(
                f"Starting quantity-cache processing: "
                f"simulation={prepared.config.label}; tasks={len(tasks)}."
            )
            try:
                summary = run_process_stage(
                    tasks,
                    prepared.processor,
                    executor=effective_executor,
                    workers=worker_count,
                    diagnostics=diagnostics,
                    summary_path=prepared.layout.tables / "run-summary.csv",
                    guard=prepared.guard,
                )
            except (OSError, OutputWriteError, ValueError) as error:
                diagnostics.error(
                    f"Quantity-cache processing failed for "
                    f"{prepared.config.label}: {error}"
                )
                if prepared.config.label not in failed_simulations:
                    failed_simulations.append(prepared.config.label)
                continue

            successful_tasks += summary.succeeded_count
            failed_tasks += summary.failed_count
            for result in summary.outcomes:
                if isinstance(result.value, Map2DProcessResult):
                    output_paths.extend(str(path) for path in result.value.output_paths)
            diagnostics.info(
                f"Quantity-cache processing finished: "
                f"simulation={prepared.config.label}; "
                f"succeeded={summary.succeeded_count}; "
                f"failed={summary.failed_count}."
            )
            if summary.exit_code and prepared.config.label not in failed_simulations:
                failed_simulations.append(prepared.config.label)

        if failed_simulations or failed_tasks:
            diagnostics.error(
                "d3dplot stopped: project processing completed with failures "
                f"(simulations={len(failed_simulations)}; tasks={failed_tasks})."
            )

    failed_simulations = list(dict.fromkeys(failed_simulations))
    return ProjectRunResult(
        output_paths=tuple(dict.fromkeys(output_paths)),
        reused_paths=tuple(dict.fromkeys(reused_paths)),
        exit_code=1 if failed_simulations or failed_tasks else 0,
        succeeded_tasks=successful_tasks,
        failed_tasks=failed_tasks,
        failed_simulations=tuple(failed_simulations),
        failure_messages=tuple(dict.fromkeys(failure_messages)),
    )
