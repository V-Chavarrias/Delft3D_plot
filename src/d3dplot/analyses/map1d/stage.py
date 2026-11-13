# SPDX-License-Identifier: GPL-3.0-or-later
"""map1d plotting stage for one-dimensional D-Flow FM map files."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import netCDF4
import numpy as np
from matplotlib.axes import Axes
from matplotlib.figure import Figure

from d3dplot import __version__
from d3dplot.analyses.map1d.config import Map1DConfig
from d3dplot.analyses.map1d.plot import (
    comparison_values,
    create_all_times_view,
    create_profile_view,
    create_space_time_view,
)
from d3dplot.analyses.map1d.process import (
    Map1DChain,
    Map1DGrid,
    Map1DProcessError,
    map1d_cache_fingerprint,
    map1d_cache_path,
    map1d_input_fingerprint,
    read_map1d_cache,
    read_map1d_quantity_series,
    read_map1d_river_km_coordinates,
    write_map1d_cache,
)
from d3dplot.analyses.river_km_statistics.measurements import (
    ProfileMeasurement,
    load_profile_measurements,
    match_profile_measurement,
)
from d3dplot.config.figure import FigureConfig
from d3dplot.config.project import ProjectConfig, SimulationConfig
from d3dplot.plotting.finalize import FigureContext, finalize_figure
from d3dplot.plotting.time import format_time_label
from d3dplot.plotting.writers import get_figure_writer
from d3dplot.simulation.discover import SimulationKind, discover_simulation
from d3dplot.simulation.time_utils import decode_cf_time
from d3dplot.store import (
    OutputLayout,
    OutputWriteGuard,
    initialize_output_root,
)
from d3dplot.store.paths import OutputArea, build_quantity_output_path
from d3dplot.variables.registry import UnknownVariableError, get_variable_spec

DiagnosticSink = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class Map1DStageResult:
    """Figures and processed quantity caches produced by the map1d stage."""

    output_paths: tuple[Path, ...]
    reused_paths: tuple[Path, ...]
    messages: tuple[str, ...]
    cache_paths: tuple[Path, ...] = ()
    reused_cache_paths: tuple[Path, ...] = ()


def plot_map1d_stage(
    project: ProjectConfig,
    *,
    analysis_name: str | None = None,
    simulation_label: str | None = None,
    diagnostic: DiagnosticSink | None = None,
) -> Map1DStageResult:
    """Render configured map1d views from one-dimensional map output."""
    return _run_map1d_stage(
        project,
        analysis_name=analysis_name,
        simulation_label=simulation_label,
        diagnostic=diagnostic,
        render_figures=True,
    )


def process_map1d_stage(
    project: ProjectConfig,
    *,
    analysis_name: str | None = None,
    simulation_label: str | None = None,
    diagnostic: DiagnosticSink | None = None,
) -> Map1DStageResult:
    """Build configured map1d quantity caches without rendering figures."""
    return _run_map1d_stage(
        project,
        analysis_name=analysis_name,
        simulation_label=simulation_label,
        diagnostic=diagnostic,
        render_figures=False,
    )


def _run_map1d_stage(
    project: ProjectConfig,
    *,
    analysis_name: str | None,
    simulation_label: str | None,
    diagnostic: DiagnosticSink | None,
    render_figures: bool,
) -> Map1DStageResult:
    outputs: list[Path] = []
    reused: list[Path] = []
    messages: list[str] = []
    cache_paths: list[Path] = []
    reused_cache_paths: list[Path] = []
    for analysis_index, configured in enumerate(project.analyses):
        if not isinstance(configured, Map1DConfig):
            continue
        if analysis_name is not None and analysis_name != configured.analysis:
            continue
        for simulation in project.simulations:
            if simulation_label is not None and simulation.label != simulation_label:
                continue
            result = _plot_analysis(
                project,
                simulation,
                configured,
                analysis_index,
                diagnostic=diagnostic,
                render_figures=render_figures,
            )
            outputs.extend(result.output_paths)
            reused.extend(result.reused_paths)
            messages.extend(result.messages)
            cache_paths.extend(result.cache_paths)
            reused_cache_paths.extend(result.reused_cache_paths)
    return Map1DStageResult(
        tuple(outputs),
        tuple(reused),
        tuple(messages),
        tuple(cache_paths),
        tuple(reused_cache_paths),
    )


def _plot_analysis(
    project: ProjectConfig,
    simulation: SimulationConfig,
    analysis: Map1DConfig,
    analysis_index: int,
    *,
    diagnostic: DiagnosticSink | None,
    render_figures: bool,
) -> Map1DStageResult:
    map_path, mdu_path, fallback_message = _map_path_for_simulation(simulation)
    grid = Map1DGrid.from_map(map_path)
    times = _read_times(map_path)
    indices = _select_time_indices(times, analysis)
    selected_times = tuple(times[index] for index in indices)
    layout = OutputLayout.for_simulation(simulation, project.output)
    guard = initialize_output_root(
        layout,
        tool_version=__version__,
        forbidden_roots=project.output.forbidden_roots,
    )
    style = project.effective_figure(analysis)
    hooks = project.effective_hooks(analysis)
    input_fingerprint = map1d_input_fingerprint(map_path=map_path, mdu_path=mdu_path)
    fingerprint = input_fingerprint
    reference = _reference_simulation(project) if render_figures else simulation
    outputs: list[Path] = []
    reused: list[Path] = []
    messages: list[str] = []
    cache_paths: list[Path] = []
    reused_cache_paths: list[Path] = []
    if fallback_message is not None:
        messages.append(fallback_message)
    measurements: dict[str, ProfileMeasurement] = {}
    if render_figures:
        for measurement_variable, settings in analysis.measurements.items():
            loaded = load_profile_measurements(
                settings.file,
                [measurement_variable],
                time_zone=(
                    settings.time_zone
                    or (
                        str(project.time_zone)
                        if project.time_zone is not None
                        else None
                    )
                ),
                layout=layout,
                guard=guard,
                diagnostic=messages.append,
                skip_missing=analysis.on_missing_input == "skip",
            )
            measurements.update(loaded)

    for variable in analysis.variables:
        location = _location_for_variable(map_path, variable)
        for chain, chain_name, chain_title in zip(
            (
                grid.chain(name, branches, location=location)
                for name, branches in zip(
                    analysis.branch_names,
                    analysis.branches,
                    strict=True,
                )
            ),
            analysis.branch_names,
            analysis.branch_titles or analysis.branch_names,
            strict=True,
        ):
            x_values = None
            x_label = "Distance (m)"
            if analysis.river_km is not None and render_figures:
                x_values = read_map1d_river_km_coordinates(
                    chain,
                    analysis.river_km.file,
                    tolerance_m=analysis.river_km.tolerance_m,
                )
                x_label = "River km"
            values, cache_message = _read_or_process_series(
                map_path,
                layout,
                guard,
                variable,
                chain,
                indices,
                selected_times,
                input_fingerprint,
                project.effective_reprocess(analysis),
            )
            if cache_message is not None:
                messages.append(cache_message)
            time_cache_paths = tuple(
                map1d_cache_path(
                    layout,
                    variable=variable,
                    chain_name=chain.name,
                    time=time,
                )
                for time in selected_times
            )
            if cache_message is not None and cache_message.startswith(
                "Reused map1d cache:"
            ):
                reused_cache_paths.extend(time_cache_paths)
            else:
                cache_paths.extend(time_cache_paths)
            if not render_figures:
                messages.append(
                    f"map1d processed {simulation.label}/{chain_name}/{variable}."
                )
                if diagnostic is not None:
                    diagnostic(messages[-1])
                continue
            reference_values: np.ndarray | None = None
            if any(
                comparison
                in {
                    "difference_simulation",
                    "difference_simulation_time",
                    "difference_simulation_relative",
                }
                for comparison in analysis.comparisons
            ):
                if simulation.label == reference.label:
                    if not analysis.include_reference_in_differences:
                        continue
                    reference_values = values
                else:
                    reference_path, reference_mdu, reference_fallback = (
                        _map_path_for_simulation(reference)
                    )
                    if reference_fallback is not None:
                        messages.append(reference_fallback)
                    reference_grid = Map1DGrid.from_map(reference_path)
                    reference_times = _read_times(reference_path)
                    reference_indices = _match_time_indices(
                        selected_times,
                        reference_times,
                    )
                    reference_location = _location_for_variable(
                        reference_path,
                        variable,
                    )
                    reference_chain = reference_grid.chain(
                        chain_name,
                        analysis.branches[analysis.branch_names.index(chain_name)],
                        location=reference_location,
                    )
                    if not np.allclose(
                        chain.offsets_m,
                        reference_chain.offsets_m,
                        equal_nan=False,
                    ):
                        raise Map1DProcessError(
                            f"map1d comparison chain coordinates differ for "
                            f"{chain_name!r}."
                        )
                    reference_layout = OutputLayout.for_simulation(
                        reference,
                        project.output,
                    )
                    reference_guard = initialize_output_root(
                        reference_layout,
                        tool_version=__version__,
                        forbidden_roots=project.output.forbidden_roots,
                    )
                    reference_values, reference_message = _read_or_process_series(
                        reference_path,
                        reference_layout,
                        reference_guard,
                        variable,
                        reference_chain,
                        reference_indices,
                        selected_times,
                        map1d_input_fingerprint(
                            map_path=reference_path, mdu_path=reference_mdu
                        ),
                        project.reprocess,
                    )
                    if reference_message is not None:
                        messages.append(reference_message)
            for comparison in analysis.comparisons:
                if comparison not in {
                    "value",
                    "difference_time",
                    "difference_simulation",
                    "difference_simulation_time",
                    "difference_simulation_relative",
                }:
                    continue
                compared = comparison_values(
                    values,
                    comparison,
                    reference_values=reference_values,
                    reference_initial_values=(
                        None if reference_values is None else reference_values[0]
                    ),
                )
                for limit_index, (x_limits, y_limits) in enumerate(
                    _axis_limit_pairs(style, variable, comparison)
                ):
                    _render_map1d_views(
                        project=project,
                        analysis=analysis,
                        simulation=simulation,
                        analysis_index=analysis_index,
                        variable=variable,
                        chain=chain,
                        chain_name=chain_name,
                        chain_title=chain_title,
                        selected_times=selected_times,
                        compared=compared,
                        comparison=comparison,
                        view_names=analysis.views,
                        x_values=x_values,
                        x_label=x_label,
                        x_limits=x_limits,
                        y_limits=y_limits,
                        limit_index=limit_index,
                        measurements=measurements,
                        layout=layout,
                        guard=guard,
                        fingerprint=fingerprint,
                        hooks=hooks,
                        outputs=outputs,
                        reused=reused,
                    )
            messages.append(
                f"map1d processed {simulation.label}/{chain_name}/{variable}."
            )
            if diagnostic is not None:
                diagnostic(messages[-1])
    return Map1DStageResult(
        tuple(outputs),
        tuple(reused),
        tuple(messages),
        tuple(cache_paths),
        tuple(reused_cache_paths),
    )


def _render_map1d_views(
    *,
    project: ProjectConfig,
    analysis: Map1DConfig,
    simulation: SimulationConfig,
    analysis_index: int,
    variable: str,
    chain: Map1DChain,
    chain_name: str,
    chain_title: str,
    selected_times: tuple[datetime, ...],
    compared: np.ndarray,
    comparison: str,
    view_names: Sequence[str],
    x_values: np.ndarray | None,
    x_label: str,
    x_limits: tuple[float | None, float | None] | None,
    y_limits: tuple[float | None, float | None] | None,
    limit_index: int,
    measurements: dict[str, ProfileMeasurement],
    layout: OutputLayout,
    guard: OutputWriteGuard,
    fingerprint: str,
    hooks: list[str],
    outputs: list[Path],
    reused: list[Path],
) -> None:
    for view_name in view_names:
        if view_name == "profile":
            for time, row in zip(selected_times, compared, strict=True):
                output_path = _figure_path(
                    layout,
                    simulation,
                    analysis_index,
                    variable,
                    chain_name,
                    view_name,
                    comparison,
                    time,
                    limit_index=limit_index,
                )
                if output_path.exists() and project.replot == "never":
                    reused.append(output_path)
                    continue
                time_label = format_time_label(
                    time,
                    project.effective_figure(analysis).time_format,
                )
                view = create_profile_view(
                    chain,
                    row,
                    variable=variable,
                    figure_config=project.effective_figure(analysis),
                    title=f"{chain_title} - {time_label}",
                    x_label=x_label,
                    x_limits=x_limits,
                    y_limits=y_limits,
                    x_values=x_values,
                )
                measurement = measurements.get(variable.casefold())
                if measurement is not None and x_values is not None:
                    match = match_profile_measurement(
                        measurement,
                        "val_mean",
                        time,
                        analysis.measurements_time_tolerance,
                    )
                    if match is not None:
                        view.add_overlay(match.river_km, match.values, "Measurements")
                _finalize(
                    view.figure,
                    view.axes,
                    project,
                    analysis,
                    simulation,
                    variable,
                    comparison,
                    view_name,
                    time,
                    output_path,
                    fingerprint,
                    guard,
                    hooks,
                )
                outputs.append(output_path)
        elif view_name == "all_times":
            output_path = _figure_path(
                layout,
                simulation,
                analysis_index,
                variable,
                chain_name,
                view_name,
                comparison,
                selected_times[0],
                limit_index=limit_index,
            )
            if output_path.exists() and project.replot == "never":
                reused.append(output_path)
                continue
            all_times_view = create_all_times_view(
                chain,
                compared,
                selected_times,
                figure_config=project.effective_figure(analysis),
                title=chain_title,
                x_label=x_label,
                x_limits=x_limits,
                y_limits=y_limits,
                x_values=x_values,
            )
            _finalize(
                all_times_view.figure,
                all_times_view.axes,
                project,
                analysis,
                simulation,
                variable,
                comparison,
                view_name,
                selected_times[0],
                output_path,
                fingerprint,
                guard,
                hooks,
            )
            outputs.append(output_path)
        elif view_name == "space_time":
            output_path = _figure_path(
                layout,
                simulation,
                analysis_index,
                variable,
                chain_name,
                view_name,
                comparison,
                selected_times[0],
                limit_index=limit_index,
            )
            if output_path.exists() and project.replot == "never":
                reused.append(output_path)
                continue
            space_time_view = create_space_time_view(
                chain,
                compared,
                selected_times,
                figure_config=project.effective_figure(analysis),
                title=chain_title,
                x_label=x_label,
                y_label="Date/time",
                limits=y_limits,
                x_values=x_values,
            )
            _finalize(
                space_time_view.figure,
                space_time_view.axes,
                project,
                analysis,
                simulation,
                variable,
                comparison,
                view_name,
                selected_times[0],
                output_path,
                fingerprint,
                guard,
                hooks,
            )
            outputs.append(output_path)


def _read_or_process_series(
    map_path: Path,
    layout: OutputLayout,
    guard: OutputWriteGuard,
    variable: str,
    chain: Map1DChain,
    indices: tuple[int, ...],
    times: tuple[datetime, ...],
    input_fingerprint: str,
    policy: str,
) -> tuple[np.ndarray, str | None]:
    cache_paths = tuple(
        map1d_cache_path(
            layout,
            variable=variable,
            chain_name=chain.name,
            time=time,
        )
        for time in times
    )
    expected = tuple(
        map1d_cache_fingerprint(
            input_fingerprint=input_fingerprint,
            variable=variable,
            chain=chain,
            time=time,
        )
        for time in times
    )
    if policy not in {"if_changed", "never", "always"}:
        raise Map1DProcessError(f"Unsupported map1d reprocess policy: {policy!r}.")
    cache_exists = all(path.is_file() for path in cache_paths)
    if policy == "never" and not cache_exists:
        raise Map1DProcessError(
            f"map1d cache is incomplete for {variable}/{chain.name} and "
            "reprocess is 'never'."
        )
    if cache_exists and policy != "always":
        try:
            values = np.stack(
                tuple(
                    read_map1d_cache(
                        path,
                        expected_fingerprint=fingerprint,
                        expected_size=chain.indices.size,
                    )
                    for path, fingerprint in zip(cache_paths, expected, strict=True)
                ),
                axis=0,
            )
            return values, f"Reused map1d cache: {variable}/{chain.name}."
        except Map1DProcessError:
            pass

    values = read_map1d_quantity_series(map_path, variable, chain, indices)
    for row, path, time, fingerprint in zip(
        values,
        cache_paths,
        times,
        expected,
        strict=True,
    ):
        write_map1d_cache(
            path,
            guard,
            values=row,
            chain=chain,
            variable=variable,
            time=time,
            fingerprint=fingerprint,
        )
    return values, f"Built map1d cache: {variable}/{chain.name}."


def _read_times(path: Path) -> tuple[datetime, ...]:
    with netCDF4.Dataset(path, mode="r") as dataset:
        if "time" not in dataset.variables:
            raise Map1DProcessError(f"map1d map has no time variable: {path}.")
        variable = dataset.variables["time"]
        units = str(getattr(variable, "units", ""))
        calendar = str(getattr(variable, "calendar", "standard"))
        values = np.asarray(variable[:], dtype=np.float64)
    decoded = tuple(decode_cf_time(value, units, calendar, 0.0) for value in values)
    if any(value is None for value in decoded):
        raise Map1DProcessError(f"map1d map contains undecodable time values: {path}.")
    return tuple(value for value in decoded if value is not None)


def _map_path_for_simulation(
    simulation: SimulationConfig,
) -> tuple[Path, Path | None, str | None]:
    """Resolve the single 1D map and its MDU, with an explicit discovery fallback."""
    discovered, report = discover_simulation(simulation.path)
    if discovered is not None and discovered.kind is SimulationKind.D3D4:
        raise Map1DProcessError(
            "map1d supports D-Flow FM simulations only; "
            f"{simulation.label!r} is a D3D4 run."
        )
    map_owner: dict[Path, Path | None] = {}
    if discovered is not None and report.is_valid:
        map_owner = {
            path: subrun.mdu.mdu_path
            for subrun in discovered.subruns
            for path in subrun.map_files
        }
        map_files = tuple(map_owner)
        fallback_message = None
    else:
        map_files = tuple(sorted(simulation.path.rglob("*_map.nc")))
        fallback_message = (
            "map1d used the single discovered map file because general simulation "
            "discovery reported model-input issues: "
            + "; ".join(issue.code for issue in report.issues)
        )
    if len(map_files) != 1:
        raise Map1DProcessError(
            "map1d currently requires exactly one unpartitioned map file; "
            f"found {len(map_files)} for {simulation.label!r}."
        )
    return map_files[0], map_owner.get(map_files[0]), fallback_message


def _reference_simulation(project: ProjectConfig) -> SimulationConfig:
    reference = project.reference_simulation
    if isinstance(reference, str):
        for simulation in project.simulations:
            if simulation.label == reference:
                return simulation
        raise Map1DProcessError(f"Unknown reference simulation {reference!r}.")
    try:
        return project.simulations[reference]
    except IndexError as error:
        raise Map1DProcessError(
            f"Reference simulation index {reference} is outside the configured list."
        ) from error


def _axis_limit_pairs(
    style: FigureConfig,
    variable: str,
    comparison: str,
) -> tuple[
    tuple[
        tuple[float | None, float | None] | None,
        tuple[float | None, float | None] | None,
    ],
    ...,
]:
    """Resolve all applicable x/y limit pairs, broadcasting singleton lists."""
    x_configured = style.x_limits
    y_configured = style.y_limits
    x_values = _axis_limit_values(x_configured, variable, comparison)
    y_values = _axis_limit_values(y_configured, variable, comparison)
    count = max(len(x_values), len(y_values))
    if len(x_values) not in {1, count} or len(y_values) not in {1, count}:
        raise Map1DProcessError(
            "map1d x_limits and y_limits must have equal row counts or one "
            "must contain a single broadcast row."
        )
    return tuple(
        (
            x_values[index if len(x_values) > 1 else 0],
            y_values[index if len(y_values) > 1 else 0],
        )
        for index in range(count)
    )


def _axis_limit_values(
    configured: object,
    variable: str,
    comparison: str,
) -> list[tuple[float | None, float | None] | None]:
    """Resolve a list of axis-limit pairs."""
    if configured is None:
        return [None]
    if isinstance(configured, list):
        if not configured:
            return [None]
        return [(pair[0], pair[1]) for pair in configured]
    if isinstance(configured, dict):
        variable_limits = configured.get(variable)
        if isinstance(variable_limits, dict):
            comparison_limits = variable_limits.get(comparison)
            if isinstance(comparison_limits, list) and comparison_limits:
                return [(pair[0], pair[1]) for pair in comparison_limits]
    raise Map1DProcessError(
        f"Unsupported map1d axis limits for variable {variable!r} and "
        f"comparison {comparison!r}."
    )


def _match_time_indices(
    requested: tuple[datetime, ...],
    candidates: tuple[datetime, ...],
) -> tuple[int, ...]:
    if not candidates:
        raise Map1DProcessError("Reference map1d simulation has no time outputs.")
    indices = tuple(
        min(
            range(len(candidates)),
            key=lambda index: abs(candidates[index] - requested_time),
        )
        for requested_time in requested
    )
    return indices


def _select_time_indices(
    times: tuple[datetime, ...],
    analysis: Map1DConfig,
) -> tuple[int, ...]:
    selected = analysis.times.at
    if selected == "all":
        return tuple(range(len(times)))
    if selected == "last":
        return (len(times) - 1,)
    if hasattr(selected, "indices"):
        indices = tuple(index - 1 for index in selected.indices)
        if any(index < 0 or index >= len(times) for index in indices):
            raise Map1DProcessError(
                "map1d time index is outside the available outputs."
            )
        return indices
    if isinstance(selected, list):
        result: list[int] = []
        for requested in selected:
            if not isinstance(requested, datetime):
                raise Map1DProcessError(
                    "map1d explicit time selection must use datetimes or indices."
                )
            result.append(
                min(range(len(times)), key=lambda index: abs(times[index] - requested))
            )
        return tuple(dict.fromkeys(result))
    raise Map1DProcessError("map1d time range selection is not implemented yet.")


def _location_for_variable(path: Path, variable: str) -> str:
    try:
        candidates = get_variable_spec(variable).netcdf_candidates
    except UnknownVariableError:
        candidates = (variable,)
    with netCDF4.Dataset(path, mode="r") as dataset:
        edge_candidates = {
            "mesh1d_q1",
            "mesh1d_q1_main",
            "mesh1d_mor_width_u",
            "mesh1d_sbn",
            "mesh1d_sbt",
        }
        if any(
            candidate in edge_candidates and candidate in dataset.variables
            for candidate in candidates
        ):
            return "edge"
    return "node"


def _figure_path(
    layout: OutputLayout,
    simulation: SimulationConfig,
    analysis_index: int,
    variable: str,
    chain_name: str,
    view: str,
    comparison: str,
    time: datetime,
    *,
    limit_index: int = 0,
) -> Path:
    filename = f"{time:%Y%m%dT%H%M%S}_l{limit_index + 1:02d}.png"
    return build_quantity_output_path(
        layout,
        OutputArea.FIGURES,
        variable,
        prefix=(f"analysis-{analysis_index}-map1d", simulation.label),
        suffix=(
            chain_name,
            quote(comparison, safe="-_."),
            quote(view, safe="-_."),
        ),
        filename=filename,
    )


def _finalize(
    figure: Figure,
    axes: Axes,
    project: ProjectConfig,
    analysis: Map1DConfig,
    simulation: SimulationConfig,
    variable: str,
    comparison: str,
    view_name: str,
    time: datetime,
    output_path: Path,
    fingerprint: str,
    guard: OutputWriteGuard,
    hooks: list[str],
) -> None:
    context = FigureContext(
        analysis="map1d",
        view=view_name,
        comparison=comparison,
        variable=variable,
        simulation=simulation.label,
        time=time,
        extent=(
            float(axes.get_xlim()[0]),
            float(axes.get_xlim()[1]),
            float(axes.get_ylim()[0]),
            float(axes.get_ylim()[1]),
        ),
        axes={"model": axes},
        data={},
        labels={"variable": variable},
        language=project.language,
        output_path=output_path,
    )
    finalize_figure(
        figure,
        context,
        project.effective_figure(analysis),
        hooks,
        get_figure_writer("png"),
        metadata={"figure_fingerprint": fingerprint},
        guard=guard,
    )
