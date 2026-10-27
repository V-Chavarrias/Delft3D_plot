# SPDX-License-Identifier: GPL-3.0-or-later
"""Plot map2d figures from guarded, persisted quantity caches."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import netCDF4
from PIL import Image, UnidentifiedImageError

from d3dplot import __version__
from d3dplot.analyses.map2d.config import Map2DConfig
from d3dplot.analyses.map2d.plot import (
    Map2DPlotError,
    Map2DPlotFrame,
    Map2DPlotSource,
    plot_map2d_group,
)
from d3dplot.analyses.map2d.process import map2d_selection_label
from d3dplot.config.project import ProjectConfig, SimulationConfig
from d3dplot.config.selections import LayersSelection
from d3dplot.simulation.partitions import _NETCDF4_READ_LOCK
from d3dplot.store import (
    OutputLayout,
    OutputWriteError,
    OutputWriteGuard,
    initialize_output_root,
)
from d3dplot.store.layout import OUTPUT_MARKER_NAME
from d3dplot.variables.context import QuantityRequest
from d3dplot.variables.registry import get_variable_spec

DiagnosticSink = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class ReplotTarget:
    """Stable identity for a single figure recorded in PNG metadata."""

    analysis_index: int
    simulation_label: str
    variable: str
    comparison: str
    time: datetime
    extent_index: int
    color_index: int


@dataclass(frozen=True, slots=True)
class Map2DPlotStageResult:
    """Outputs produced or reused by a project-level map2d plot stage."""

    output_paths: tuple[Path, ...]
    reused_paths: tuple[Path, ...]
    movie_paths: tuple[Path, ...]
    reused_movie_paths: tuple[Path, ...]
    messages: tuple[str, ...]


def read_replot_metadata(figure_path: str | Path) -> tuple[Path, ReplotTarget]:
    """Read and validate the guarded provenance selector embedded in a PNG."""
    try:
        path = Path(figure_path).expanduser().resolve(strict=True)
    except OSError as error:
        raise Map2DPlotError(
            "V-PLOT-005",
            f"Could not resolve replot figure {figure_path}: {error}",
        ) from error
    figure_root = _marked_child_root(path, "figures")
    if path.suffix.casefold() != ".png":
        raise Map2DPlotError("V-PLOT-005", f"Replot requires a PNG figure: {path}.")
    try:
        with Image.open(path) as image:
            if image.format != "PNG":
                raise Map2DPlotError(
                    "V-PLOT-005",
                    f"Figure is not a PNG image: {path}.",
                )
            metadata = dict(image.info)
    except Map2DPlotError:
        raise
    except (OSError, UnidentifiedImageError) as error:
        raise Map2DPlotError(
            "V-PLOT-005",
            f"Could not read PNG metadata from {path}: {error}",
        ) from error

    provenance_value = metadata.get("d3dplot_provenance")
    if not isinstance(provenance_value, str) or not provenance_value:
        raise Map2DPlotError(
            "V-PLOT-005",
            f"Figure has no d3dplot provenance metadata: {path}.",
        )
    try:
        provenance_path = Path(provenance_value).expanduser().resolve(strict=True)
    except OSError as error:
        raise Map2DPlotError(
            "V-PLOT-005",
            f"Could not resolve figure provenance {provenance_value!r}: {error}",
        ) from error
    _marked_child_root(provenance_path, "config")
    if not provenance_path.is_file():
        raise Map2DPlotError(
            "V-PLOT-005",
            f"Resolved configuration is not a regular file: {provenance_path}.",
        )

    key_value = metadata.get("d3dplot_replot_key")
    if not isinstance(key_value, str):
        raise Map2DPlotError(
            "V-PLOT-005",
            f"Figure has no d3dplot replot selector: {path}.",
        )
    try:
        key = json.loads(key_value)
        target = _parse_replot_target(key)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise Map2DPlotError(
            "V-PLOT-005",
            f"Figure has invalid d3dplot replot metadata: {error}",
        ) from error

    if not _is_within(path, figure_root / "figures"):
        raise OutputWriteError(f"Figure path is outside the marked output root: {path}")
    return provenance_path, target


def plot_map2d_stage(
    project: ProjectConfig,
    *,
    provenance_path: Path | None = None,
    analysis_name: str | None = None,
    simulation_label: str | None = None,
    variable: str | None = None,
    fresh_figures: bool = False,
    target: ReplotTarget | None = None,
    target_path: Path | None = None,
    diagnostic: DiagnosticSink | None = None,
) -> Map2DPlotStageResult:
    """Render configured map2d analyses from existing processed cache files."""
    if (target is None) != (target_path is None):
        raise ValueError("A replot target and target path must be provided together.")

    simulations = {item.label: item for item in project.simulations}
    if simulation_label is not None and simulation_label not in simulations:
        raise ValueError(f"Configured simulation label not found: {simulation_label}")
    reference = _reference_simulation(project)
    analysis_indices: tuple[int, ...]
    selected_simulations: tuple[SimulationConfig, ...]
    if target is not None:
        if target.analysis_index >= len(project.analyses):
            raise Map2DPlotError(
                "V-PLOT-005",
                "Figure metadata refers to an analysis no longer present in its "
                "resolved configuration.",
            )
        if target.simulation_label not in simulations:
            raise Map2DPlotError(
                "V-PLOT-005",
                "Figure metadata refers to an unknown configured simulation.",
            )
        analysis_indices = (target.analysis_index,)
        selected_simulations = (simulations[target.simulation_label],)
    else:
        analysis_indices = tuple(range(len(project.analyses)))
        selected_simulations = (
            tuple(project.simulations)
            if simulation_label is None
            else (simulations[simulation_label],)
        )

    if analysis_name is not None:
        analysis_indices = tuple(
            index
            for index in analysis_indices
            if project.analyses[index].analysis == analysis_name
        )
        if not analysis_indices:
            raise ValueError(f"Configured analysis not found: {analysis_name}")

    if target is not None and target_path is not None:
        only_output_path = target_path.expanduser().resolve(strict=False)
    else:
        only_output_path = None

    outputs: list[Path] = []
    reused: list[Path] = []
    movies: list[Path] = []
    reused_movies: list[Path] = []
    messages: list[str] = []
    source_cache: dict[tuple[str, str, str], Map2DPlotSource] = {}

    def get_source(
        simulation: SimulationConfig,
        analysis: Map2DConfig,
        requested_variable: str,
    ) -> Map2DPlotSource:
        cache_key = (
            simulation.label,
            requested_variable.casefold(),
            _selection(analysis, requested_variable),
        )
        if cache_key not in source_cache:
            source_cache[cache_key] = _load_map2d_source(
                project,
                simulation,
                analysis,
                requested_variable,
            )
        return source_cache[cache_key]

    for analysis_index in analysis_indices:
        configured_analysis = project.analyses[analysis_index]
        if not isinstance(configured_analysis, Map2DConfig):
            raise ValueError(
                f"Plotting for analysis {configured_analysis.analysis!r} is not "
                "implemented yet."
            )
        analysis = configured_analysis
        if target is not None and target.variable.casefold() not in {
            name.casefold() for name in analysis.variables
        }:
            raise Map2DPlotError(
                "V-PLOT-005",
                "Figure metadata variable is not present in the resolved analysis.",
            )

        variables = (
            (target.variable,)
            if target is not None
            else tuple(
                name
                for name in analysis.variables
                if variable is None
                or name.casefold() == variable.casefold()
                or get_variable_spec(name).name.casefold() == variable.casefold()
            )
        )
        if variable is not None and not variables:
            raise ValueError(f"Configured map2d variable not found: {variable}")

        for requested_variable in variables:
            current_simulations = selected_simulations
            if target is not None:
                target_matches = (
                    target.analysis_index == analysis_index
                    and target.variable.casefold() == requested_variable.casefold()
                )
                if not target_matches:
                    continue
            for comparison in analysis.comparisons:
                if target is not None and comparison != target.comparison:
                    continue
                requires_reference = comparison.startswith("difference_simulation")
                if requires_reference:
                    if not analysis.include_reference_in_differences and any(
                        item.label == reference.label for item in current_simulations
                    ):
                        plot_simulations = tuple(
                            item
                            for item in current_simulations
                            if item.label != reference.label
                        )
                    else:
                        plot_simulations = current_simulations
                else:
                    plot_simulations = current_simulations

                for simulation in plot_simulations:
                    source = get_source(simulation, analysis, requested_variable)
                    reference_source = (
                        get_source(reference, analysis, requested_variable)
                        if requires_reference
                        else None
                    )
                    if (
                        target is not None
                        and simulation.label != target.simulation_label
                    ):
                        continue
                    result = plot_map2d_group(
                        project,
                        analysis,
                        source,
                        requested_variable,
                        comparison,
                        reference=reference_source,
                        diagnostic=diagnostic,
                        fresh_figures=fresh_figures or target is not None,
                        analysis_index=analysis_index,
                        provenance_path=provenance_path,
                        only_output_path=only_output_path,
                    )
                    outputs.extend(result.output_paths)
                    reused.extend(result.reused_paths)
                    movies.extend(result.movie_paths)
                    reused_movies.extend(result.reused_movie_paths)
                    messages.extend(result.messages)

    if target is not None and target_path is not None:
        resolved_target = target_path.expanduser().resolve(strict=False)
        if resolved_target not in outputs:
            raise Map2DPlotError(
                "V-PLOT-005",
                f"Could not regenerate the figure recorded by {target_path}.",
            )

    return Map2DPlotStageResult(
        tuple(outputs),
        tuple(reused),
        tuple(movies),
        tuple(reused_movies),
        tuple(messages),
    )


def _load_map2d_source(
    project: ProjectConfig,
    simulation: SimulationConfig,
    analysis: Map2DConfig,
    variable: str,
) -> Map2DPlotSource:
    layout = OutputLayout.for_simulation(simulation, project.output)
    guard = initialize_output_root(
        layout,
        tool_version=__version__,
        forbidden_roots=project.output.forbidden_roots,
    )
    selection = _selection(analysis, variable)
    variable_directory = quote(variable.casefold(), safe="-_.")
    cache_directory = layout.cache_data / "map2d" / variable_directory / selection
    cache_paths = tuple(sorted(cache_directory.glob("*.nc")))
    if not cache_paths:
        raise Map2DPlotError(
            "V-PLOT-002",
            f"No processed map2d cache files found for {simulation.label!r}, "
            f"{variable!r}, selection {selection!r}: {cache_directory}.",
        )

    expected_variable = get_variable_spec(variable).name
    frames: list[Map2DPlotFrame] = []
    grid_paths: set[Path] = set()
    for cache_path in cache_paths:
        frame_time, cache_variable, cache_selection, grid_path = _read_cache_metadata(
            cache_path,
            layout,
        )
        if cache_variable.casefold() != expected_variable.casefold():
            raise Map2DPlotError(
                "V-PLOT-002",
                f"Cache variable {cache_variable!r} does not match "
                f"{expected_variable!r}: {cache_path}.",
            )
        if cache_selection != selection:
            raise Map2DPlotError(
                "V-PLOT-002",
                f"Cache selection {cache_selection!r} does not match "
                f"{selection!r}: {cache_path}.",
            )
        grid_paths.add(grid_path)
        frames.append(
            Map2DPlotFrame(
                match_key=frame_time.isoformat(),
                time=frame_time,
                path=cache_path,
            )
        )
    if len(grid_paths) != 1:
        raise Map2DPlotError(
            "V-PLOT-002",
            f"Map2d cache group spans {len(grid_paths)} inspected grids for "
            f"{simulation.label!r}, {variable!r}; plot each topology separately.",
        )
    frames.sort(key=lambda frame: frame.time)
    return Map2DPlotSource(
        label=simulation.label,
        layout=layout,
        guard=guard,
        grid_path=next(iter(grid_paths)),
        frames=tuple(frames),
    )


def _read_cache_metadata(
    path: Path,
    layout: OutputLayout,
) -> tuple[datetime, str, str, Path]:
    resolved_path = path.resolve(strict=True)
    if not _is_within(resolved_path, layout.root):
        raise OutputWriteError(f"Cache path is outside the marked output root: {path}")
    try:
        with _NETCDF4_READ_LOCK:
            with netCDF4.Dataset(resolved_path, mode="r") as dataset:
                time_value = getattr(dataset, "time", None)
                variable_value = getattr(dataset, "variable", None)
                selection_value = getattr(dataset, "selection", None)
                grid_file_value = getattr(dataset, "grid_file", None)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise Map2DPlotError(
            "V-PLOT-002",
            f"Could not read map2d cache metadata {path}: {error}",
        ) from error

    if not isinstance(time_value, str) or not time_value:
        raise Map2DPlotError(
            "V-PLOT-002",
            f"Map2d cache has no valid time value: {path}.",
        )
    if not isinstance(variable_value, str) or not variable_value:
        raise Map2DPlotError(
            "V-PLOT-002",
            f"Map2d cache has no valid variable value: {path}.",
        )
    if not isinstance(selection_value, str) or not selection_value:
        raise Map2DPlotError(
            "V-PLOT-002",
            f"Map2d cache has no valid selection value: {path}.",
        )
    if not isinstance(grid_file_value, str) or not grid_file_value:
        raise Map2DPlotError(
            "V-PLOT-002",
            f"Map2d cache has no valid grid-file value: {path}.",
        )
    try:
        frame_time = datetime.fromisoformat(time_value)
    except ValueError as error:
        raise Map2DPlotError(
            "V-PLOT-002",
            f"Map2d cache has an invalid time value: {path}.",
        ) from error
    if frame_time.tzinfo is None or frame_time.utcoffset() is None:
        raise Map2DPlotError(
            "V-PLOT-002",
            f"Map2d cache time must include a UTC offset: {path}.",
        )

    try:
        grid_path = (resolved_path.parent / grid_file_value).resolve(strict=True)
    except OSError as error:
        raise Map2DPlotError(
            "V-PLOT-002",
            f"Could not resolve inspected grid for cache {path}: {error}",
        ) from error
    if not _is_within(grid_path, layout.root):
        raise OutputWriteError(
            f"Inspected grid path is outside the marked output root: {grid_path}"
        )
    return frame_time, variable_value, selection_value, grid_path


def _selection(analysis: Map2DConfig, variable: str) -> str:
    fractions = (
        tuple(analysis.fractions) if isinstance(analysis.fractions, list) else None
    )
    vertical = analysis.vertical
    layers = tuple(vertical.layers) if isinstance(vertical, LayersSelection) else None
    request = QuantityRequest(
        requested_name=variable,
        fractions=fractions,
        layers=layers,
        sum_fractions=analysis.sum_fractions,
        active_sediment_thickness_threshold_m=(
            analysis.active_sediment_thickness_threshold_m
        ),
    )
    return map2d_selection_label(request)


def _reference_simulation(project: ProjectConfig) -> SimulationConfig:
    reference = project.reference_simulation
    if isinstance(reference, str):
        try:
            return next(item for item in project.simulations if item.label == reference)
        except StopIteration as error:
            raise ValueError(
                f"Configured reference simulation not found: {reference}"
            ) from error
    return project.simulations[reference]


def _parse_replot_target(value: object) -> ReplotTarget:
    if not isinstance(value, Mapping):
        raise ValueError("replot selector must be a mapping")
    analysis_index = _non_negative_index(value.get("analysis_index"), "analysis_index")
    simulation_label = _required_text(value.get("simulation_label"), "simulation_label")
    variable = _required_text(value.get("variable"), "variable")
    comparison = _required_text(value.get("comparison"), "comparison")
    time_value = _required_text(value.get("time"), "time")
    try:
        frame_time = datetime.fromisoformat(time_value)
    except ValueError as error:
        raise ValueError("replot selector time is not ISO 8601") from error
    if frame_time.tzinfo is None or frame_time.utcoffset() is None:
        raise ValueError("replot selector time must include a UTC offset")
    if comparison not in {
        "value",
        "difference_time",
        "difference_simulation",
        "difference_simulation_time",
        "difference_simulation_relative",
    }:
        raise ValueError(f"unsupported replot comparison: {comparison!r}")
    return ReplotTarget(
        analysis_index=analysis_index,
        simulation_label=simulation_label,
        variable=variable,
        comparison=comparison,
        time=frame_time,
        extent_index=_non_negative_index(value.get("extent_index"), "extent_index"),
        color_index=_non_negative_index(value.get("color_index"), "color_index"),
    )


def _non_negative_index(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value


def _marked_child_root(path: Path, child_name: str) -> Path:
    for root in (path.parent, *path.parents):
        marker = root / OUTPUT_MARKER_NAME
        if marker.is_symlink() or not marker.is_file():
            continue
        if not _is_within(path, root / child_name):
            continue
        try:
            OutputWriteGuard(root).validate_target(path)
        except OutputWriteError:
            continue
        return root
    raise Map2DPlotError(
        "V-PLOT-005",
        f"Path is not beneath a marked d3dplot/{child_name} directory: {path}.",
    )


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
