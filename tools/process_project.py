# SPDX-License-Identifier: GPL-3.0-or-later
"""Process and plot selected quantities from a single-simulation project YAML."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from d3dplot import __version__
from d3dplot.analyses.map2d.config import Map2DConfig
from d3dplot.analyses.map2d.process import (
    Map2DProcessResult,
    Map2DTaskProcessor,
)
from d3dplot.analyses.river_km_statistics.config import RiverKmStatisticsConfig
from d3dplot.config.analysis import AnalysisConfigBase
from d3dplot.config.io import load_config
from d3dplot.config.selections import LayersSelection
from d3dplot.execution.diagnostics import DiagnosticLog
from d3dplot.execution.orchestrator import run_process_stage
from d3dplot.execution.tasks import AnalysisTimeSelection, plan_process_tasks
from d3dplot.simulation.discover import discover_simulation
from d3dplot.simulation.inspection import inspect_simulation
from d3dplot.simulation.time_axis import TimeAxis, TimeSelectionResult
from d3dplot.store import OutputLayout, initialize_output_root
from d3dplot.variables.context import QuantityRequest


def _quantity_requests(
    analysis: AnalysisConfigBase,
) -> tuple[QuantityRequest, ...]:
    vertical = analysis.vertical
    if vertical == "all":
        layers = None
    elif isinstance(vertical, LayersSelection):
        layers = tuple(vertical.layers)
    else:
        raise ValueError(
            "This processing helper supports vertical: all or explicit layers."
        )

    fractions = None if analysis.fractions == "all" else tuple(analysis.fractions)
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
            )
            for name in analysis.variables
        )
    )


def _process_project(configuration_path: Path, *, serial: bool) -> int:
    project = load_config(configuration_path)
    if len(project.simulations) != 1:
        raise ValueError(
            "This local processing helper currently requires exactly one "
            "configured simulation."
        )

    simulation_config = project.simulations[0]
    layout = OutputLayout.for_simulation(simulation_config, project.output)
    with DiagnosticLog.for_project(
        (layout,),
        tool_version=__version__,
        command_line=tuple(sys.argv),
        forbidden_roots=project.output.forbidden_roots,
        configuration_path=configuration_path,
    ) as diagnostics:
        simulation, discovery_report = discover_simulation(simulation_config.path)
        diagnostics.record_validation_issues(discovery_report.issues)
        if simulation is None:
            raise ValueError(discovery_report.to_text())

        inspection, inspection_report = inspect_simulation(
            simulation,
            layout,
            crs=project.crs,
            time_zone=(
                str(project.time_zone) if project.time_zone is not None else None
            ),
            check_boundary_duplicates=project.check_boundary_duplicates,
            forbidden_roots=project.output.forbidden_roots,
        )
        diagnostics.record_validation_issues(inspection_report.issues)
        if inspection is None or not inspection_report.is_valid:
            raise ValueError(inspection_report.to_text())

        time_axis = TimeAxis.from_cache(inspection.times_path)
        time_zone = str(project.time_zone) if project.time_zone is not None else None
        selections: list[AnalysisTimeSelection] = []
        selection_records: list[
            tuple[str, AnalysisConfigBase, TimeSelectionResult]
        ] = []
        map2d_keys: list[str] = []
        river_km_analyses: dict[str, RiverKmStatisticsConfig] = {}

        for index, analysis in enumerate(project.analyses):
            analysis_key = f"analysis-{index}-{analysis.analysis}"
            selection = time_axis.select(
                analysis.times,
                naive_time_zone=time_zone,
                duplicate_morphological_times=(analysis.duplicate_morphological_times),
                on_corrupt_output=project.on_corrupt_output,
            )
            selections.append(
                AnalysisTimeSelection(
                    analysis_key=analysis_key,
                    quantities=_quantity_requests(analysis),
                    selection=selection,
                )
            )
            selection_records.append((analysis_key, analysis, selection))

            if isinstance(analysis, Map2DConfig):
                map2d_keys.append(analysis_key)
            elif isinstance(analysis, RiverKmStatisticsConfig):
                river_km_analyses[analysis_key] = analysis
            else:
                raise TypeError(
                    f"Unsupported analysis type: {type(analysis).__name__}."
                )

        executor = "serial" if serial else project.execution.executor
        if executor == "slurm":
            raise ValueError(
                "SLURM submission is not supported by this local processing helper."
            )
        workers = (
            1
            if executor == "serial"
            else project.execution.workers or os.cpu_count() or 1
        )

        guard = initialize_output_root(
            layout,
            tool_version=__version__,
            forbidden_roots=project.output.forbidden_roots,
        )
        processor = Map2DTaskProcessor(
            simulation,
            simulation_config.label,
            inspection,
            layout,
            guard,
            analysis_keys=tuple(map2d_keys),
            river_km_analyses=river_km_analyses,
            model_crs=project.crs,
            dtype=project.output.dtype,
            on_corrupt_output=project.on_corrupt_output,
        )

        for analysis_key, selected_analysis, selection in selection_records:
            diagnostics.record_time_selection(
                analysis_key=analysis_key,
                simulation_label=simulation_config.label,
                requested=selected_analysis.times.model_dump_json(),
                result=selection,
            )
            if not selection.is_valid:
                raise ValueError(
                    f"Invalid time selection for {analysis_key}: "
                    + "; ".join(issue.message for issue in selection.issues)
                )
        tasks = plan_process_tasks(
            simulation_config.label,
            selections,
            workers=workers,
            max_block_times=project.execution.process_block_times,
            shard=project.execution.shard,
        )
        if not tasks:
            raise ValueError("The configured selections produced no process tasks.")
        diagnostics.warning(
            "This test helper rebuilds all selected quantity caches; it does not "
            "apply the project's reprocess cache policy."
        )
        summary = run_process_stage(
            tasks,
            processor,
            executor=executor,
            workers=project.execution.workers,
            diagnostics=diagnostics,
            summary_path=layout.tables / "run-summary.csv",
            guard=guard,
        )

    output_count = sum(
        len(result.value.output_paths)
        for result in summary.outcomes
        if isinstance(result.value, Map2DProcessResult)
    )
    print(
        f"Processing finished: {summary.succeeded_count} task(s) succeeded, "
        f"{summary.failed_count} failed; {output_count} cache file(s) written."
    )
    print(f"Output root: {layout.root}")
    print(f"Run summary: {layout.tables / 'run-summary.csv'}")
    print(f"Diagnostics: {layout.logs}")
    if summary.exit_code:
        return summary.exit_code

    plot_command = [
        sys.executable,
        "-m",
        "d3dplot",
        "plot",
        str(configuration_path),
    ]
    return subprocess.run(plot_command, check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Process configured quantities and create figures for one simulation. "
            "All selected quantity caches are rebuilt."
        )
    )
    parser.add_argument(
        "configuration",
        type=Path,
        help="YAML or JSON project configuration.",
    )
    parser.add_argument(
        "--serial",
        action="store_true",
        help="Run tasks serially instead of using the configured executor.",
    )
    arguments = parser.parse_args()
    configuration_path = arguments.configuration.expanduser().resolve(strict=True)
    return _process_project(configuration_path, serial=arguments.serial)


if __name__ == "__main__":
    raise SystemExit(main())
