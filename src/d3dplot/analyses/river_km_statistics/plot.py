# SPDX-License-Identifier: GPL-3.0-or-later
"""Plot river-kilometre statistics and diagnostic maps from cached data."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import blake2b
from importlib import import_module
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from matplotlib.collections import LineCollection, PolyCollection
from PIL import Image, UnidentifiedImageError

from d3dplot import __version__
from d3dplot.analyses.river_km_statistics.config import (
    RiverKmStatisticsConfig,
    TimeAverageConfig,
)
from d3dplot.analyses.river_km_statistics.process import (
    PreparedRegionSegments,
    RiverKmStatisticsError,
    bed_slope_streamwise,
    compute_cumulative,
    compute_time_average,
    difference_regions,
    difference_simulation_relative,
    read_prepared_region_segments,
    select_time_average_outputs,
)
from d3dplot.config.analysis import Comparison
from d3dplot.config.figure import AxisLimit, FigureConfig
from d3dplot.config.project import ProjectConfig, SimulationConfig
from d3dplot.config.selections import LayersSelection
from d3dplot.execution.cache import decide_cache, read_cache_fingerprints
from d3dplot.labels import label
from d3dplot.plotting.finalize import (
    FigureContext,
    FigureHookError,
    FigureSnapshot,
    HookSpec,
    close_figure,
    finalize_figure,
)
from d3dplot.plotting.views.decorations import (
    MapDecorations,
    MapDecorationsError,
)
from d3dplot.plotting.views.extents import (
    ExtentSet,
    ExtentSetError,
    ExtentWindow,
    create_extent_window,
    open_ugrid,
)
from d3dplot.plotting.views.line import LineView
from d3dplot.plotting.views.map import MapView, MapViewError, face_polygons
from d3dplot.plotting.views.space_time import SpaceTimeView
from d3dplot.plotting.writers import get_figure_writer
from d3dplot.simulation.partitions import _NETCDF4_READ_LOCK
from d3dplot.simulation.time_axis import TimeAxis, TimeSelectionResult
from d3dplot.store import (
    CACHE_SCHEMA_VERSION,
    OutputLayout,
    OutputWriteError,
    OutputWriteGuard,
    canonical_json,
    fingerprint_artifact,
    initialize_output_root,
)
from d3dplot.store.layout import is_path_within
from d3dplot.variables.context import QuantityRequest
from d3dplot.variables.registry import get_variable_spec
from d3dplot.variables.selection import quantity_selection_label

_Array = Any
np: Any = import_module("numpy")
netcdf4: Any = import_module("netCDF4")
mdates: Any = import_module("matplotlib.dates")
_DIFFERENCE_COMPARISONS = frozenset(
    {
        "difference_simulation",
        "difference_simulation_time",
        "difference_simulation_relative",
    }
)


class RiverKmStatisticsPlotError(RuntimeError):
    """Cached river-kilometre data cannot be plotted safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class StatisticCacheFrame:
    """One validated per-time river-kilometre statistics cache."""

    time: datetime
    morph_time: datetime | None
    path: Path
    fingerprint: str
    grid_path: Path
    km_centres: _Array
    km_edge_start: _Array
    km_edge_end: _Array
    statistics: tuple[str, ...]
    component_names: tuple[str, ...]
    values: _Array
    segment_width_m: _Array | None


@dataclass(frozen=True, slots=True)
class RiverKmStatisticsPlotStageResult:
    """Figure outputs produced or reused by a river-kilometre plot stage."""

    output_paths: tuple[Path, ...]
    reused_paths: tuple[Path, ...]
    messages: tuple[str, ...]
    movie_paths: tuple[Path, ...] = ()
    reused_movie_paths: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class _PlotSource:
    """One simulation/region series participating in a combined figure."""

    label: str
    simulation: SimulationConfig
    frames: tuple[StatisticCacheFrame, ...]
    reference_frames: tuple[StatisticCacheFrame, ...] | None = None
    partner_frames: tuple[StatisticCacheFrame, ...] | None = None
    reference_partner_frames: tuple[StatisticCacheFrame, ...] | None = None


DiagnosticSink = Callable[[str], None]


def _quantity_request(
    analysis: RiverKmStatisticsConfig,
    variable: str,
) -> QuantityRequest:
    fractions = (
        tuple(analysis.fractions) if isinstance(analysis.fractions, list) else None
    )
    layers = (
        tuple(analysis.vertical.layers)
        if isinstance(analysis.vertical, LayersSelection)
        else None
    )
    return QuantityRequest(
        requested_name=variable,
        fractions=fractions,
        layers=layers,
        sum_fractions=analysis.sum_fractions,
        active_sediment_thickness_threshold_m=(
            analysis.active_sediment_thickness_threshold_m
        ),
    )


def load_statistic_cache_frames(
    layout: OutputLayout,
    analysis: RiverKmStatisticsConfig,
    region_name: str,
    km_set_name: str,
    variable: str,
) -> tuple[StatisticCacheFrame, ...]:
    """Load validated per-time caches for one configured region and quantity."""
    spec = get_variable_spec(variable)
    request = _quantity_request(analysis, variable)
    selection = quantity_selection_label(request, variable_name=spec.name)
    cache_directory = (
        layout.cache_data
        / "river_km_statistics"
        / quote(region_name, safe="-_.")
        / quote(km_set_name, safe="-_.")
        / quote(spec.name.casefold(), safe="-_.")
        / selection
    )
    cache_paths = tuple(sorted(cache_directory.glob("*.nc")))
    if not cache_paths:
        raise RiverKmStatisticsPlotError(
            "V-PLOT-002",
            f"No river-kilometre statistics caches found for "
            f"{region_name!r}/{km_set_name!r}/{spec.name!r}: {cache_directory}.",
        )

    frames = tuple(
        _read_statistic_cache_frame(
            path,
            layout,
            expected_variable=spec.name,
            expected_region=region_name,
            expected_km_set=km_set_name,
            expected_selection=selection,
        )
        for path in cache_paths
    )
    frames = tuple(sorted(frames, key=lambda frame: frame.time))
    if len({frame.time for frame in frames}) != len(frames):
        raise RiverKmStatisticsPlotError(
            "V-PLOT-002",
            f"Statistics cache contains duplicate times for {region_name!r}/"
            f"{km_set_name!r}/{spec.name!r}.",
        )
    if len({frame.grid_path for frame in frames}) != 1:
        raise RiverKmStatisticsPlotError(
            "V-PLOT-002",
            f"Statistics caches span multiple inspected grids for "
            f"{region_name!r}/{km_set_name!r}/{spec.name!r}.",
        )
    return frames


def _read_statistic_cache_frame(
    path: Path,
    layout: OutputLayout,
    *,
    expected_variable: str,
    expected_region: str,
    expected_km_set: str,
    expected_selection: str,
) -> StatisticCacheFrame:
    resolved = path.resolve(strict=True)
    if not is_path_within(resolved, layout.root):
        raise OutputWriteError(
            f"Statistics cache is outside the marked output root: {resolved}"
        )
    try:
        with (
            _NETCDF4_READ_LOCK,
            netcdf4.Dataset(resolved, mode="r") as dataset,
        ):
            attributes = set(dataset.ncattrs())
            required_attributes = {
                "d3dplot_fingerprint",
                "time",
                "variable",
                "selection",
                "region",
                "km_set",
                "grid_file",
            }
            missing_attributes = required_attributes - attributes
            if missing_attributes:
                raise ValueError(
                    "cache is missing attribute(s): "
                    + ", ".join(sorted(missing_attributes))
                )
            fingerprint = _attribute_text(dataset, "d3dplot_fingerprint")
            time_value = _parse_cache_time(_attribute_text(dataset, "time"))
            variable_name = _attribute_text(dataset, "variable")
            selection = _attribute_text(dataset, "selection")
            region_name = _attribute_text(dataset, "region")
            km_set_name = _attribute_text(dataset, "km_set")
            grid_value = _attribute_text(dataset, "grid_file")
            morph_time_value = _attribute_text(dataset, "morph_time", optional=True)
            morph_time = (
                _parse_cache_time(morph_time_value) if morph_time_value else None
            )
            if (
                variable_name.casefold() != expected_variable.casefold()
                or selection != expected_selection
                or region_name != expected_region
                or km_set_name != expected_km_set
            ):
                raise ValueError("cache metadata does not match the requested group")
            required_variables = {
                "values",
                "statistic",
                "km_centre",
                "km_edge_start",
                "km_edge_end",
            }
            missing_variables = required_variables - set(dataset.variables)
            if missing_variables:
                raise ValueError(
                    "cache is missing variable(s): "
                    + ", ".join(sorted(missing_variables))
                )
            values = np.ma.filled(dataset.variables["values"][:], np.nan)
            values = np.asarray(values, dtype=np.float64)
            statistics = tuple(
                _decode_text(value) for value in dataset.variables["statistic"][:]
            )
            km_centres = np.asarray(dataset.variables["km_centre"][:], dtype=np.float64)
            edge_start = np.asarray(
                dataset.variables["km_edge_start"][:],
                dtype=np.float64,
            )
            edge_end = np.asarray(
                dataset.variables["km_edge_end"][:],
                dtype=np.float64,
            )
            component_names: tuple[str, ...] = ()
            if "component_name" in dataset.variables:
                component_names = tuple(
                    _decode_text(value)
                    for value in dataset.variables["component_name"][:]
                )
            segment_width = (
                np.asarray(dataset.variables["segment_width_m"][:], dtype=np.float64)
                if "segment_width_m" in dataset.variables
                else None
            )
    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as error:
        raise RiverKmStatisticsPlotError(
            "V-PLOT-002",
            f"Could not read river-kilometre statistics cache {resolved}: {error}.",
        ) from error

    if (
        not fingerprint
        or values.ndim not in {2, 3}
        or values.shape[0] != km_centres.size
        or values.shape[1] != len(statistics)
        or edge_start.shape != km_centres.shape
        or edge_end.shape != km_centres.shape
        or (values.ndim == 3 and values.shape[2] != len(component_names))
        or (values.ndim == 2 and component_names)
        or (segment_width is not None and segment_width.shape != km_centres.shape)
        or not np.isfinite(km_centres).all()
        or not np.isfinite(edge_start).all()
        or not np.isfinite(edge_end).all()
    ):
        raise RiverKmStatisticsPlotError(
            "V-PLOT-002",
            f"River-kilometre statistics cache has invalid shapes or coordinates: "
            f"{resolved}.",
        )
    grid_path = (resolved.parent / grid_value).resolve(strict=True)
    if not is_path_within(grid_path, layout.root) or not grid_path.is_file():
        raise OutputWriteError(
            f"Inspected grid cache is outside the marked output root: {grid_path}"
        )
    return StatisticCacheFrame(
        time=time_value,
        morph_time=morph_time,
        path=resolved,
        fingerprint=fingerprint,
        grid_path=grid_path,
        km_centres=km_centres,
        km_edge_start=edge_start,
        km_edge_end=edge_end,
        statistics=statistics,
        component_names=component_names,
        values=values,
        segment_width_m=segment_width,
    )


def _attribute_text(dataset: Any, name: str, *, optional: bool = False) -> str:
    if name not in dataset.ncattrs():
        if optional:
            return ""
        raise ValueError(f"cache has no {name!r} attribute")
    return _decode_text(dataset.getncattr(name))


def _decode_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _parse_cache_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"cache time is not ISO 8601: {value!r}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"cache time is not timezone-aware: {value!r}")
    return parsed.astimezone(UTC)


def statistic_values(
    frame: StatisticCacheFrame,
    statistic: str,
    analysis: RiverKmStatisticsConfig,
) -> _Array:
    """Return one statistic, applying plot-stage width or slope operations."""
    if statistic == "bed_slope_streamwise":
        if "mean" not in frame.statistics:
            raise RiverKmStatisticsPlotError(
                "V-SMB-014",
                "bed_slope_streamwise requires the per-segment mean statistic.",
            )
        result = bed_slope_streamwise(
            frame.values[:, frame.statistics.index("mean")],
            frame.km_centres,
        )
    else:
        if statistic not in frame.statistics:
            raise RiverKmStatisticsPlotError(
                "V-PLOT-002",
                f"Statistic {statistic!r} is missing from {frame.path}.",
            )
        result = np.asarray(frame.values[:, frame.statistics.index(statistic)])
    if analysis.multiply_by_width != "none" and statistic in {
        "mean",
        "mean_area_weighted",
    }:
        if frame.segment_width_m is None:
            raise RiverKmStatisticsPlotError(
                "V-PLOT-002",
                f"Width multiplication was requested but {frame.path} has no "
                "segment_width_m coordinate.",
            )
        width = frame.segment_width_m
        result = result * (width if result.ndim == 1 else width[:, np.newaxis])
    return np.asarray(result, dtype=np.float64)


def _png_fingerprint(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        with Image.open(path) as image:
            value = image.info.get("figure_fingerprint")
    except (OSError, UnidentifiedImageError):
        return None
    return value if isinstance(value, str) else None


def _configuration_fingerprint(
    project: ProjectConfig,
    analysis: RiverKmStatisticsConfig,
) -> str:
    return blake2b(
        canonical_json(
            {
                "project": project.model_dump(mode="json"),
                "analysis": analysis.model_dump(mode="json"),
            }
        ).encode("utf-8"),
        digest_size=32,
    ).hexdigest()


def _hook_fingerprints(hooks: Sequence[HookSpec]) -> tuple[str, ...]:
    from d3dplot.analyses.map2d.plot import _hook_fingerprints as map_hook_hashes

    return map_hook_hashes(hooks)


def _statistic_label(statistic: str, project: ProjectConfig) -> str:
    return label(
        statistic,
        project.language,
        "quantity",
        extra_dictionaries=project.labels.extra_dictionaries,
    ).text


def _value_label(
    variable: str,
    statistic: str,
    project: ProjectConfig,
) -> tuple[str, str]:
    value = label(
        variable,
        project.language,
        "value",
        datum=project.labels.vertical_datum,
        extra_dictionaries=project.labels.extra_dictionaries,
    )
    statistic_text = _statistic_label(statistic, project)
    return f"{statistic_text} — {value.quantity}", value.unit


def _resolve_limits(
    values: AxisLimit
    | list[AxisLimit]
    | Mapping[str, Mapping[str, Sequence[AxisLimit]]]
    | None,
    variable: str,
    comparison: str,
) -> tuple[AxisLimit | None, ...]:
    if values is None:
        return (None,)
    if isinstance(values, Mapping):
        selected = next(
            (
                inner
                for key, inner in values.items()
                if key.casefold() == variable.casefold()
            ),
            {},
        )
        limits = next(
            (
                pairs
                for key, pairs in selected.items()
                if key.casefold() == comparison.casefold()
            ),
            (),
        )
        return tuple(limits) if limits else (None,)
    if isinstance(values, tuple):
        if len(values) != 2 or not all(
            item is None or isinstance(item, (int, float)) for item in values
        ):
            raise ValueError("Axis limits must contain a lower and upper value.")
        return (values,)
    if isinstance(values, list):
        return tuple(values) or (None,)
    return tuple(values) or (None,)


def plot_river_km_statistics_stage(
    project: ProjectConfig,
    *,
    simulation_label: str | None = None,
    variable: str | None = None,
    analysis_name: str | None = None,
    fresh_figures: bool = False,
    provenance_path: Path | None = None,
    diagnostic: DiagnosticSink | None = None,
) -> RiverKmStatisticsPlotStageResult:
    """Render configured river-kilometre views from persisted data caches."""
    simulations = {simulation.label: simulation for simulation in project.simulations}
    if simulation_label is not None and simulation_label not in simulations:
        raise ValueError(f"Configured simulation label not found: {simulation_label}")

    outputs: list[Path] = []
    reused: list[Path] = []
    messages: list[str] = []

    def report(message: str) -> None:
        messages.append(message)
        if diagnostic is not None:
            diagnostic(message)

    selected_simulations = (
        tuple(project.simulations)
        if simulation_label is None
        else (simulations[simulation_label],)
    )
    for analysis_index, configured in enumerate(project.analyses):
        if not isinstance(configured, RiverKmStatisticsConfig):
            continue
        if analysis_name is not None and configured.analysis != analysis_name:
            continue
        active_config = configured
        selected_variables = tuple(
            name
            for name in configured.variables
            if variable is None
            or name.casefold() == variable.casefold()
            or get_variable_spec(name).name.casefold() == variable.casefold()
        )
        if variable is not None and not selected_variables:
            continue

        layouts: dict[str, OutputLayout] = {}
        guards: dict[str, OutputWriteGuard] = {}
        frame_caches: dict[
            str,
            dict[tuple[str, str, str], tuple[StatisticCacheFrame, ...]],
        ] = {}

        def layout_and_guard(
            simulation: SimulationConfig,
            *,
            layouts: dict[str, OutputLayout] = layouts,
            guards: dict[str, OutputWriteGuard] = guards,
        ) -> tuple[OutputLayout, OutputWriteGuard]:
            if simulation.label not in layouts:
                layout = OutputLayout.for_simulation(simulation, project.output)
                guard = initialize_output_root(
                    layout,
                    tool_version=__version__,
                    forbidden_roots=project.output.forbidden_roots,
                )
                layouts[simulation.label] = layout
                guards[simulation.label] = guard
            return layouts[simulation.label], guards[simulation.label]

        def frames_for(
            simulation: SimulationConfig,
            region_name: str,
            km_set_name: str,
            requested_variable: str,
            *,
            frame_caches: dict[
                str,
                dict[tuple[str, str, str], tuple[StatisticCacheFrame, ...]],
            ] = frame_caches,
            configured: RiverKmStatisticsConfig = active_config,
            layout_and_guard: Callable[
                [SimulationConfig], tuple[OutputLayout, OutputWriteGuard]
            ] = layout_and_guard,
        ) -> tuple[StatisticCacheFrame, ...]:
            key = (
                region_name,
                km_set_name,
                get_variable_spec(requested_variable).name.casefold(),
            )
            cache = frame_caches.setdefault(simulation.label, {})
            if key not in cache:
                layout, _ = layout_and_guard(simulation)
                cache[key] = load_statistic_cache_frames(
                    layout,
                    configured,
                    region_name,
                    km_set_name,
                    requested_variable,
                )
            return cache[key]

        has_simulation_comparison = any(
            comparison in _DIFFERENCE_COMPARISONS
            for comparison in configured.comparisons
        )
        reference = _reference_simulation(project)

        for simulation in selected_simulations:
            layout, guard = layout_and_guard(simulation)
            for requested_variable in selected_variables:
                for region in configured.regions:
                    for km_set_name in configured.river_km.sets:
                        frames_for(
                            simulation, region.name, km_set_name, requested_variable
                        )

            if configured.diagnostics and selected_variables:
                first_variable = selected_variables[0]
                for region in configured.regions:
                    for km_set_name in configured.river_km.sets:
                        frames = frames_for(
                            simulation,
                            region.name,
                            km_set_name,
                            first_variable,
                        )
                        prepared = read_prepared_region_segments(
                            layout,
                            guard,
                            region.name,
                            km_set_name,
                        )
                        for issue in prepared.issues:
                            report(f"{issue.code}: {issue.message}")
                        output_paths, reused_paths = _plot_diagnostics(
                            project,
                            configured,
                            simulation,
                            layout,
                            guard,
                            prepared,
                            frames[0],
                            analysis_index=analysis_index,
                            provenance_path=provenance_path,
                            fresh_figures=fresh_figures,
                            report=report,
                        )
                        outputs.extend(output_paths)
                        reused.extend(reused_paths)

            if configured.grouping == "per_simulation":
                time_axis: TimeAxis | None = None
                average_selections: tuple[TimeSelectionResult, ...] = ()
                if configured.time_averages and "profile" in configured.views:
                    try:
                        time_axis = TimeAxis.from_cache(layout.cache / "times.nc")
                    except ValueError as error:
                        raise RiverKmStatisticsPlotError(
                            "V-PLOT-002",
                            f"Could not load inspected time axis for "
                            f"{simulation.label!r}: {error}",
                        ) from error
                    average_selections = select_time_average_outputs(
                        time_axis,
                        configured,
                        on_corrupt_output=project.on_corrupt_output,
                    )

                for requested_variable in selected_variables:
                    for region in configured.regions:
                        for km_set_name in configured.river_km.sets:
                            frames = frames_for(
                                simulation,
                                region.name,
                                km_set_name,
                                requested_variable,
                            )
                            reference_frames = (
                                frames_for(
                                    reference,
                                    region.name,
                                    km_set_name,
                                    requested_variable,
                                )
                                if has_simulation_comparison
                                else None
                            )
                            output_paths, reused_paths = _plot_region_views(
                                project,
                                configured,
                                simulation,
                                layout,
                                guard,
                                region.name,
                                km_set_name,
                                requested_variable,
                                frames,
                                reference_frames=reference_frames,
                                analysis_index=analysis_index,
                                provenance_path=provenance_path,
                                fresh_figures=fresh_figures,
                                report=report,
                            )
                            outputs.extend(output_paths)
                            reused.extend(reused_paths)
                            if time_axis is not None:
                                for average, selection in zip(
                                    configured.time_averages,
                                    average_selections,
                                    strict=True,
                                ):
                                    average_frames, average_times = (
                                        _select_time_average_frames(
                                            average,
                                            selection,
                                            frames,
                                            report,
                                        )
                                    )
                                    output_paths, reused_paths = (
                                        _plot_time_average_profiles(
                                            project,
                                            configured,
                                            simulation,
                                            layout,
                                            guard,
                                            region.name,
                                            km_set_name,
                                            requested_variable,
                                            average,
                                            average_frames,
                                            average_times,
                                            analysis_index=analysis_index,
                                            provenance_path=provenance_path,
                                            fresh_figures=fresh_figures,
                                            report=report,
                                        )
                                    )
                                    outputs.extend(output_paths)
                                    reused.extend(reused_paths)

                for region_group in configured.region_groups:
                    region_names = tuple(region_group)
                    for requested_variable in selected_variables:
                        for km_set_name in configured.river_km.sets:
                            sources = tuple(
                                _PlotSource(
                                    label=region_name,
                                    simulation=simulation,
                                    frames=frames_for(
                                        simulation,
                                        region_name,
                                        km_set_name,
                                        requested_variable,
                                    ),
                                    reference_frames=(
                                        frames_for(
                                            reference,
                                            region_name,
                                            km_set_name,
                                            requested_variable,
                                        )
                                        if has_simulation_comparison
                                        else None
                                    ),
                                )
                                for region_name in region_names
                            )
                            output_paths, reused_paths = _plot_grouped_sources(
                                project,
                                configured,
                                simulation,
                                layout,
                                guard,
                                simulation.label,
                                "regions_" + "_".join(region_names),
                                km_set_name,
                                requested_variable,
                                sources,
                                analysis_index=analysis_index,
                                provenance_path=provenance_path,
                                fresh_figures=fresh_figures,
                                report=report,
                            )
                            outputs.extend(output_paths)
                            reused.extend(reused_paths)

                for difference in configured.region_differences:
                    left_name, right_name = difference.regions
                    for requested_variable in selected_variables:
                        for km_set_name in configured.river_km.sets:
                            sources = (
                                _PlotSource(
                                    label=f"{left_name} - {right_name}",
                                    simulation=simulation,
                                    frames=frames_for(
                                        simulation,
                                        left_name,
                                        km_set_name,
                                        requested_variable,
                                    ),
                                    reference_frames=(
                                        frames_for(
                                            reference,
                                            left_name,
                                            km_set_name,
                                            requested_variable,
                                        )
                                        if has_simulation_comparison
                                        else None
                                    ),
                                    partner_frames=frames_for(
                                        simulation,
                                        right_name,
                                        km_set_name,
                                        requested_variable,
                                    ),
                                    reference_partner_frames=(
                                        frames_for(
                                            reference,
                                            right_name,
                                            km_set_name,
                                            requested_variable,
                                        )
                                        if has_simulation_comparison
                                        else None
                                    ),
                                ),
                            )
                            output_paths, reused_paths = _plot_grouped_sources(
                                project,
                                configured,
                                simulation,
                                layout,
                                guard,
                                simulation.label,
                                f"{left_name}_minus_{right_name}",
                                km_set_name,
                                requested_variable,
                                sources,
                                analysis_index=analysis_index,
                                provenance_path=provenance_path,
                                fresh_figures=fresh_figures,
                                report=report,
                            )
                            outputs.extend(output_paths)
                            reused.extend(reused_paths)

        if configured.grouping == "all_simulations" and selected_simulations:
            output_simulation = reference
            output_layout, output_guard = layout_and_guard(output_simulation)
            for requested_variable in selected_variables:
                for region in configured.regions:
                    for km_set_name in configured.river_km.sets:
                        sources = tuple(
                            _PlotSource(
                                label=simulation.label,
                                simulation=simulation,
                                frames=frames_for(
                                    simulation,
                                    region.name,
                                    km_set_name,
                                    requested_variable,
                                ),
                                reference_frames=(
                                    frames_for(
                                        reference,
                                        region.name,
                                        km_set_name,
                                        requested_variable,
                                    )
                                    if has_simulation_comparison
                                    else None
                                ),
                            )
                            for simulation in selected_simulations
                        )
                        output_paths, reused_paths = _plot_grouped_sources(
                            project,
                            configured,
                            output_simulation,
                            output_layout,
                            output_guard,
                            "all_simulations",
                            region.name,
                            km_set_name,
                            requested_variable,
                            sources,
                            analysis_index=analysis_index,
                            provenance_path=provenance_path,
                            fresh_figures=fresh_figures,
                            report=report,
                        )
                        outputs.extend(output_paths)
                        reused.extend(reused_paths)

                for region_group in configured.region_groups:
                    region_names = tuple(region_group)
                    for km_set_name in configured.river_km.sets:
                        for requested_variable in selected_variables:
                            sources = tuple(
                                _PlotSource(
                                    label=f"{simulation.label}/{region_name}",
                                    simulation=simulation,
                                    frames=frames_for(
                                        simulation,
                                        region_name,
                                        km_set_name,
                                        requested_variable,
                                    ),
                                    reference_frames=(
                                        frames_for(
                                            reference,
                                            region_name,
                                            km_set_name,
                                            requested_variable,
                                        )
                                        if has_simulation_comparison
                                        else None
                                    ),
                                )
                                for simulation in selected_simulations
                                for region_name in region_names
                            )
                            output_paths, reused_paths = _plot_grouped_sources(
                                project,
                                configured,
                                output_simulation,
                                output_layout,
                                output_guard,
                                "all_simulations",
                                "regions_" + "_".join(region_names),
                                km_set_name,
                                requested_variable,
                                sources,
                                analysis_index=analysis_index,
                                provenance_path=provenance_path,
                                fresh_figures=fresh_figures,
                                report=report,
                            )
                            outputs.extend(output_paths)
                            reused.extend(reused_paths)

                for difference in configured.region_differences:
                    left_name, right_name = difference.regions
                    for km_set_name in configured.river_km.sets:
                        for requested_variable in selected_variables:
                            sources = tuple(
                                _PlotSource(
                                    label=(
                                        f"{simulation.label}: "
                                        f"{left_name} - {right_name}"
                                    ),
                                    simulation=simulation,
                                    frames=frames_for(
                                        simulation,
                                        left_name,
                                        km_set_name,
                                        requested_variable,
                                    ),
                                    reference_frames=(
                                        frames_for(
                                            reference,
                                            left_name,
                                            km_set_name,
                                            requested_variable,
                                        )
                                        if has_simulation_comparison
                                        else None
                                    ),
                                    partner_frames=frames_for(
                                        simulation,
                                        right_name,
                                        km_set_name,
                                        requested_variable,
                                    ),
                                    reference_partner_frames=(
                                        frames_for(
                                            reference,
                                            right_name,
                                            km_set_name,
                                            requested_variable,
                                        )
                                        if has_simulation_comparison
                                        else None
                                    ),
                                )
                                for simulation in selected_simulations
                            )
                            output_paths, reused_paths = _plot_grouped_sources(
                                project,
                                configured,
                                output_simulation,
                                output_layout,
                                output_guard,
                                "all_simulations",
                                f"{left_name}_minus_{right_name}",
                                km_set_name,
                                requested_variable,
                                sources,
                                analysis_index=analysis_index,
                                provenance_path=provenance_path,
                                fresh_figures=fresh_figures,
                                report=report,
                            )
                            outputs.extend(output_paths)
                            reused.extend(reused_paths)

    return RiverKmStatisticsPlotStageResult(
        tuple(outputs),
        tuple(reused),
        tuple(messages),
    )


def _plot_diagnostics(
    project: ProjectConfig,
    analysis: RiverKmStatisticsConfig,
    simulation: SimulationConfig,
    layout: OutputLayout,
    guard: OutputWriteGuard,
    prepared: PreparedRegionSegments,
    frame: StatisticCacheFrame,
    *,
    analysis_index: int,
    provenance_path: Path | None,
    fresh_figures: bool,
    report: DiagnosticSink,
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    grid_dataset = None
    active_view: MapView | None = None
    outputs: list[Path] = []
    reused: list[Path] = []
    figure_config = project.effective_figure(analysis)
    hooks = project.effective_hooks(analysis)
    hook_fingerprints = _hook_fingerprints(hooks)
    style_fingerprint = _configuration_fingerprint(project, analysis)
    try:
        grid_dataset = open_ugrid(frame.grid_path)
        grid = grid_dataset.grid
        face_count = int(grid.n_face)
        if prepared.assignment.segment.shape != (face_count,):
            raise RiverKmStatisticsPlotError(
                "V-PLOT-002",
                "Prepared segment assignment does not match the inspected grid.",
            )
        decorations = MapDecorations.prepare(
            project,
            analysis.map,
            figure_config,
            getattr(grid, "crs", None),
            layout,
            guard,
            diagnostic=report,
        )
        extent_set = ExtentSet.create(
            grid,
            frame.grid_path,
            layout,
            guard,
            analysis.map,
        )
        decorations.prepare_backgrounds(
            tuple(
                window.bounds for window in extent_set.windows if window.faces.size > 0
            )
        )
        segment_values = np.full(face_count, np.nan, dtype=np.float64)
        assigned = prepared.assignment.segment >= 0
        segment_values[assigned] = prepared.geometry.centres_km[
            prepared.assignment.segment[assigned]
        ]
        if "segment_map" in analysis.diagnostics:
            values = segment_values.copy()
            for window in extent_set.windows:
                if window.faces.size == 0:
                    report(
                        f"V-PLOT-001: diagnostic extent {window.name!r} has no "
                        "visible faces and was skipped."
                    )
                    continue
                title = _diagnostic_title(
                    "segment_map",
                    prepared.region_name,
                    prepared.km_set_name,
                    project,
                )
                output_path = _diagnostic_path(
                    layout,
                    project.series,
                    simulation.label,
                    prepared,
                    "segment_map",
                    window.name,
                )
                fingerprint = _diagnostic_fingerprint(
                    project,
                    analysis,
                    prepared,
                    frame,
                    style_fingerprint,
                    hook_fingerprints,
                    decorations.fingerprint_for(window.bounds),
                    diagnostic="segment_map",
                    bounds=window.bounds,
                )
                if _reuse_diagnostic(
                    output_path,
                    fingerprint,
                    project,
                    analysis,
                    fresh_figures,
                    reused,
                    report,
                ):
                    continue
                active_view = MapView.create(
                    grid,
                    window,
                    values,
                    map_config=analysis.map,
                    figure_config=figure_config,
                    colormap=_map_colormap(analysis, "rkm"),
                    colorbar_label=label(
                        "rkm",
                        project.language,
                        "value",
                        scale=0.001,
                        extra_dictionaries=project.labels.extra_dictionaries,
                    ).text,
                    initial_limits=_map_limits(analysis, "rkm"),
                    decorations=decorations,
                )
                _draw_region_outline(active_view.axes, prepared)
                _draw_cut_lines(active_view.axes, prepared)
                issue_faces = np.unique(
                    np.concatenate(
                        (
                            prepared.assignment.overlap_face_indices,
                            prepared.assignment.uncovered_face_indices,
                        )
                    )
                )
                _draw_issue_faces(active_view.axes, grid, issue_faces)
                context = _map_context(
                    project,
                    "segment_map",
                    "rkm",
                    simulation.label,
                    frame.time,
                    window,
                    active_view,
                    values,
                    prepared,
                    output_path,
                    title,
                )
                _finalize_output(
                    active_view.figure,
                    context,
                    figure_config,
                    hooks,
                    guard,
                    fingerprint,
                    output_path,
                    provenance_path,
                    task_key=(
                        f"plot|{simulation.label}|segment_map|"
                        f"{prepared.region_name}|{prepared.km_set_name}|"
                        f"{window.index}"
                    ),
                )
                outputs.append(output_path)
                report(f"Saved diagnostic map {output_path}.")
                active_view = None

        if "segment_membership" in analysis.diagnostics:
            for segment_index, centre_km in enumerate(prepared.geometry.centres_km):
                polygon = np.asarray(prepared.geometry.polygons[segment_index])
                coordinates = polygon[np.isfinite(polygon).all(axis=1)]
                if coordinates.shape[0] < 3:
                    report(
                        f"V-SMB-010: segment {centre_km:g} km has no drawable "
                        "polygon and its membership figure was skipped."
                    )
                    continue
                xmin, ymin = np.min(coordinates, axis=0)
                xmax, ymax = np.max(coordinates, axis=0)
                margin = 0.1 * max(float(xmax - xmin), float(ymax - ymin))
                bounds = (
                    float(xmin - margin),
                    float(xmax + margin),
                    float(ymin - margin),
                    float(ymax + margin),
                )
                decorations.prepare_backgrounds((bounds,))
                window = create_extent_window(
                    grid,
                    frame.grid_path,
                    layout,
                    guard,
                    index=len(extent_set.windows) + segment_index,
                    name=f"segment_{segment_index + 1}",
                    bounds=bounds,
                )
                if window.faces.size == 0:
                    report(
                        f"V-PLOT-001: segment-membership extent for {centre_km:g} "
                        "km contains no visible faces and was skipped."
                    )
                    continue
                values = np.full(face_count, np.nan, dtype=np.float64)
                values[prepared.assignment.segment == segment_index] = 1.0
                title = _segment_membership_title(
                    prepared.region_name,
                    prepared.km_set_name,
                    float(centre_km),
                    project,
                )
                output_path = _diagnostic_path(
                    layout,
                    project.series,
                    simulation.label,
                    prepared,
                    "segment_membership",
                    f"km_{centre_km:g}",
                )
                fingerprint = _diagnostic_fingerprint(
                    project,
                    analysis,
                    prepared,
                    frame,
                    style_fingerprint,
                    hook_fingerprints,
                    decorations.fingerprint_for(window.bounds),
                    diagnostic="segment_membership",
                    bounds=window.bounds,
                    segment_index=segment_index,
                )
                if _reuse_diagnostic(
                    output_path,
                    fingerprint,
                    project,
                    analysis,
                    fresh_figures,
                    reused,
                    report,
                ):
                    continue
                active_view = MapView.create(
                    grid,
                    window,
                    values,
                    map_config=analysis.map,
                    figure_config=figure_config,
                    colormap=_map_colormap(analysis, "segment_membership"),
                    colorbar_label=_diagnostic_label("segment_membership", project),
                    initial_limits=(0.0, 1.0),
                    decorations=decorations,
                )
                _draw_region_outline(active_view.axes, prepared)
                _draw_segment_outline(active_view.axes, polygon)
                context = _map_context(
                    project,
                    "segment_membership",
                    "segment_membership",
                    simulation.label,
                    frame.time,
                    window,
                    active_view,
                    values,
                    prepared,
                    output_path,
                    title,
                )
                _finalize_output(
                    active_view.figure,
                    context,
                    figure_config,
                    hooks,
                    guard,
                    fingerprint,
                    output_path,
                    provenance_path,
                    task_key=(
                        f"plot|{simulation.label}|segment_membership|"
                        f"{prepared.region_name}|{prepared.km_set_name}|"
                        f"{segment_index}"
                    ),
                )
                outputs.append(output_path)
                report(f"Saved diagnostic map {output_path}.")
                active_view = None
    except MapDecorationsError as error:
        raise RiverKmStatisticsPlotError(error.code, str(error)) from error
    except ExtentSetError as error:
        raise RiverKmStatisticsPlotError(error.code, str(error)) from error
    except (MapViewError, RiverKmStatisticsError) as error:
        code = getattr(error, "code", "V-PLOT-002")
        raise RiverKmStatisticsPlotError(code, str(error)) from error
    finally:
        if active_view is not None:
            close_figure(active_view.figure)
        if grid_dataset is not None:
            grid_dataset.close()
    return tuple(outputs), tuple(reused)


def _model_crs(grid: Any, configured_crs: str | None) -> str | None:
    grid_crs = getattr(grid, "crs", None)
    if grid_crs is None or str(grid_crs).casefold() in {"epsg:0", "0"}:
        return configured_crs
    to_string = getattr(grid_crs, "to_string", None)
    return str(to_string()) if callable(to_string) else str(grid_crs)


def _diagnostic_title(
    diagnostic: str,
    region_name: str,
    km_set_name: str,
    project: ProjectConfig,
) -> str:
    return f"{_diagnostic_label(diagnostic, project)} — {region_name} — {km_set_name}"


def _segment_membership_title(
    region_name: str,
    km_set_name: str,
    centre_km: float,
    project: ProjectConfig,
) -> str:
    diagnostic = _diagnostic_label("segment_membership", project)
    river_km = label(
        "rkm",
        project.language,
        "value",
        scale=0.001,
        extra_dictionaries=project.labels.extra_dictionaries,
    )
    return (
        f"{diagnostic} — {region_name} — {km_set_name} — {centre_km:g} {river_km.unit}"
    )


def _diagnostic_label(name: str, project: ProjectConfig) -> str:
    return label(
        name,
        project.language,
        "quantity",
        extra_dictionaries=project.labels.extra_dictionaries,
    ).text


def _diagnostic_path(
    layout: OutputLayout,
    series: str,
    subject: str,
    prepared: PreparedRegionSegments,
    diagnostic: str,
    suffix: str,
) -> Path:
    return (
        layout.figures
        / quote(series, safe="-_.")
        / quote(subject, safe="-_.")
        / "river_km_statistics"
        / quote(prepared.region_name, safe="-_.")
        / quote(prepared.km_set_name, safe="-_.")
        / "diagnostics"
        / f"{diagnostic}_{quote(suffix, safe='-_.')}.png"
    )


def _diagnostic_fingerprint(
    project: ProjectConfig,
    analysis: RiverKmStatisticsConfig,
    prepared: PreparedRegionSegments,
    frame: StatisticCacheFrame,
    style_fingerprint: str,
    hook_fingerprints: Sequence[str],
    decorations_fingerprint: str,
    *,
    diagnostic: str,
    bounds: Sequence[float],
    segment_index: int | None = None,
) -> str:
    grid_fingerprints = read_cache_fingerprints((frame.grid_path,))
    grid_fingerprint = grid_fingerprints[frame.grid_path.resolve(strict=False)]
    if grid_fingerprint is None:
        raise RiverKmStatisticsPlotError(
            "V-PLOT-002",
            f"Inspected grid cache has no fingerprint: {frame.grid_path}.",
        )
    return fingerprint_artifact(
        kind="figure",
        schema_version=CACHE_SCHEMA_VERSION,
        tool_version=__version__,
        algorithm_version="river-km-diagnostic-1",
        parameters={
            "analysis": analysis.model_dump(mode="json"),
            "bounds": list(bounds),
            "diagnostic": diagnostic,
            "figure": project.effective_figure(analysis).model_dump(mode="json"),
            "map": analysis.map.model_dump(mode="json"),
            "region": prepared.region_name,
            "km_set": prepared.km_set_name,
            "segment_index": segment_index,
        },
        inputs=(
            style_fingerprint,
            *hook_fingerprints,
            prepared.fingerprint,
            grid_fingerprint,
            decorations_fingerprint,
        ),
    )


def _reuse_diagnostic(
    output_path: Path,
    fingerprint: str,
    project: ProjectConfig,
    analysis: RiverKmStatisticsConfig,
    fresh_figures: bool,
    reused: list[Path],
    report: DiagnosticSink,
) -> bool:
    decision = decide_cache(
        exists=output_path.is_file(),
        existing_fingerprint=_png_fingerprint(output_path),
        expected_fingerprint=fingerprint,
        policy="always" if fresh_figures else project.effective_replot(analysis),
    )
    if decision.action == "reuse":
        reused.append(output_path)
        report(f"Reused diagnostic map {output_path}.")
        if decision.warning is not None:
            report(f"WARNING: {decision.warning}")
        return True
    return False


def _map_colormap(analysis: RiverKmStatisticsConfig, variable: str) -> str:
    configured = analysis.map.colormap
    if isinstance(configured, str):
        return configured
    return next(
        (
            value
            for key, value in configured.items()
            if key.casefold() == variable.casefold()
        ),
        "viridis",
    )


def _map_limits(
    analysis: RiverKmStatisticsConfig,
    variable: str,
) -> AxisLimit | None:
    configured = analysis.map.color_limits
    if configured is None:
        return None
    variable_limits = next(
        (
            value
            for key, value in configured.items()
            if key.casefold() == variable.casefold()
        ),
        {},
    )
    comparison_limits = next(
        (value for key, value in variable_limits.items() if key.casefold() == "value"),
        (),
    )
    return comparison_limits[0] if comparison_limits else None


def _draw_region_outline(axes: Any, prepared: PreparedRegionSegments) -> None:
    lines: list[_Array] = []
    geometry = prepared.region.geometry
    polygons = (geometry,) if geometry.geom_type == "Polygon" else geometry.geoms
    for polygon in polygons:
        lines.append(np.asarray(polygon.exterior.coords, dtype=np.float64))
        lines.extend(
            np.asarray(interior.coords, dtype=np.float64)
            for interior in polygon.interiors
        )
    if lines:
        axes.add_collection(
            LineCollection(lines, colors="black", linewidths=1.1, zorder=7),
            autolim=False,
        )


def _draw_cut_lines(axes: Any, prepared: PreparedRegionSegments) -> None:
    left = np.asarray(prepared.geometry.left_cut_lines, dtype=np.float64).reshape(-1, 2)
    right = np.asarray(prepared.geometry.right_cut_lines, dtype=np.float64).reshape(
        -1, 2
    )
    if left.shape != right.shape:
        raise RiverKmStatisticsPlotError(
            "V-PLOT-002",
            "Prepared river-km cut-line boundaries have different shapes.",
        )
    lines = np.stack((left, right), axis=1)
    if lines.size:
        axes.add_collection(
            LineCollection(lines, colors="black", linewidths=0.55, zorder=6),
            autolim=False,
        )


def _draw_issue_faces(axes: Any, grid: Any, indices: _Array) -> None:
    if indices.size == 0:
        return
    polygons = face_polygons(grid, indices)
    axes.add_collection(
        PolyCollection(
            polygons,
            facecolors="none",
            edgecolors="#d62728",
            linewidths=0.65,
            hatch="///",
            zorder=8,
        ),
        autolim=False,
    )


def _draw_segment_outline(axes: Any, polygon: _Array) -> None:
    coordinates = np.asarray(polygon, dtype=np.float64)
    coordinates = coordinates[np.isfinite(coordinates).all(axis=1)]
    if coordinates.shape[0] >= 3:
        if not np.allclose(coordinates[0], coordinates[-1]):
            coordinates = np.vstack((coordinates, coordinates[0]))
        axes.add_collection(
            LineCollection([coordinates], colors="black", linewidths=0.8, zorder=8),
            autolim=False,
        )


def _map_context(
    project: ProjectConfig,
    view_name: str,
    variable: str,
    subject: str,
    time: datetime,
    window: ExtentWindow,
    view: MapView,
    values: _Array,
    prepared: PreparedRegionSegments,
    output_path: Path,
    title: str,
) -> FigureContext:
    return FigureContext(
        analysis="river_km_statistics",
        view=view_name,
        comparison="value",
        variable=variable,
        simulation=subject,
        time=time,
        extent=window.bounds,
        axes={"model": view.axes, "colorbar": view.colorbar.ax},
        data={
            "values": values,
            "face_indices": window.faces,
            "km_centres": prepared.geometry.centres_km,
            "segment_indices": prepared.assignment.segment,
            "region": prepared.region.geometry,
            "time": time,
        },
        labels={"title": title},
        language=project.language,
        output_path=output_path,
    )


def _finalize_output(
    figure: Any,
    context: FigureContext,
    figure_config: FigureConfig,
    hooks: Sequence[HookSpec],
    guard: OutputWriteGuard,
    fingerprint: str,
    output_path: Path,
    provenance_path: Path | None,
    close: bool = True,
    *,
    task_key: str,
) -> None:
    metadata = {
        "config_hash": fingerprint,
        "figure_fingerprint": fingerprint,
        "task_key": task_key,
    }
    if provenance_path is not None:
        metadata["d3dplot_provenance"] = str(provenance_path.resolve(strict=False))
    snapshot = (
        FigureSnapshot.capture(figure)
        if hooks and figure_config.hooks_mode == "per_image"
        else None
    )
    try:
        finalize_figure(
            figure,
            context,
            figure_config,
            hooks,
            get_figure_writer("png"),
            metadata=metadata,
            guard=guard,
            close=False,
        )
    except FigureHookError as error:
        raise RiverKmStatisticsPlotError("V-HOOK-001", str(error)) from error
    finally:
        if snapshot is not None:
            snapshot.restore()
        if close:
            close_figure(figure)


def _select_time_average_frames(
    average: TimeAverageConfig,
    selection: TimeSelectionResult,
    frames: tuple[StatisticCacheFrame, ...],
    report: DiagnosticSink,
) -> tuple[tuple[StatisticCacheFrame, ...], tuple[datetime, ...]]:
    for issue in selection.issues:
        report(f"{issue.code}: {issue.message}")
    if not selection.is_valid:
        issue = next(issue for issue in selection.issues if issue.level == "error")
        raise RiverKmStatisticsPlotError(issue.code, issue.message)
    if len(selection.entries) < 2:
        raise RiverKmStatisticsPlotError(
            "V-SMB-014",
            f"Time average {average.name!r} requires at least two selected outputs.",
        )
    frame_by_time = {frame.time: frame for frame in frames}
    missing = tuple(
        entry.time for entry in selection.entries if entry.time not in frame_by_time
    )
    if missing:
        missing_text = ", ".join(item.isoformat() for item in missing)
        raise RiverKmStatisticsPlotError(
            "V-PLOT-002",
            f"Time average {average.name!r} is missing processed statistics for "
            f"{missing_text}; process the requested time-average outputs first.",
        )
    selected_frames = tuple(frame_by_time[entry.time] for entry in selection.entries)
    average_times = tuple(
        entry.morph_time if average.times.axis == "morphological" else entry.time
        for entry in selection.entries
    )
    if any(instant is None for instant in average_times):
        raise RiverKmStatisticsPlotError(
            "V-TIME-012",
            f"Time average {average.name!r} selected an output without a "
            "morphological time.",
        )
    resolved_times = tuple(instant for instant in average_times if instant is not None)
    for duplicate in selection.duplicates:
        report(
            f"Resolved duplicate {average.times.axis} time "
            f"{duplicate.chosen.time.isoformat()} to output index "
            f"{duplicate.chosen.time_index + 1} for time average {average.name!r}."
        )
    report(
        f"Time average {average.name!r} selected "
        f"{len(selected_frames)} output(s) on the {average.times.axis} axis."
    )
    return selected_frames, resolved_times


def _plot_time_average_profiles(
    project: ProjectConfig,
    analysis: RiverKmStatisticsConfig,
    simulation: SimulationConfig,
    layout: OutputLayout,
    guard: OutputWriteGuard,
    region_name: str,
    km_set_name: str,
    variable: str,
    average: TimeAverageConfig,
    frames: tuple[StatisticCacheFrame, ...],
    average_times: tuple[datetime, ...],
    *,
    analysis_index: int,
    provenance_path: Path | None,
    fresh_figures: bool,
    report: DiagnosticSink,
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    figure_config = project.effective_figure(analysis)
    hooks = project.effective_hooks(analysis)
    statistic_sources = tuple(
        tuple(statistic_values(frame, statistic, analysis) for frame in frames)
        for statistic in analysis.statistics
    )
    source_fingerprints = tuple(frame.fingerprint for frame in frames)
    x_limits = _resolve_limits(figure_config.x_limits, variable, "value")
    y_limits = _resolve_limits(figure_config.y_limits, variable, "value")
    limit_pairs = _paired_limits(x_limits, y_limits)
    outputs: list[Path] = []
    reused: list[Path] = []
    for statistic, series in zip(
        analysis.statistics,
        statistic_sources,
        strict=True,
    ):
        averaged_values = compute_time_average(np.stack(series), average_times)
        base_statistic_text, unit_text = _value_label(variable, statistic, project)
        for average_statistic, data in averaged_values.items():
            component_count = data.shape[1] if data.ndim == 2 else 1
            component_names = frames[0].component_names or ("",)
            if len(component_names) != component_count:
                raise RiverKmStatisticsPlotError(
                    "V-PLOT-002",
                    "Component labels do not match time-averaged statistic values.",
                )
            labels = tuple(
                name or _statistic_label(statistic, project) for name in component_names
            )
            values = np.asarray(data, dtype=np.float64) * figure_config.display_scale
            y_values = values[np.newaxis, :] if values.ndim == 1 else values.T
            display_mode = _display_mode(analysis, variable)
            average_title = (
                f"{average.name} — {_statistic_label(average_statistic, project)} "
                f"of {base_statistic_text} — {region_name} — {km_set_name}"
            )
            average_view_name = (
                f"time_average_{quote(average.name, safe='-_.')}_{average_statistic}"
            )
            for limit_index, (x_limit, y_limit) in enumerate(limit_pairs):
                output_path = _line_path(
                    layout,
                    project.series,
                    simulation.label,
                    region_name,
                    km_set_name,
                    variable,
                    average_view_name,
                    statistic,
                    "value",
                    None,
                    limit_index,
                )
                fingerprint = _line_fingerprint(
                    project,
                    analysis,
                    statistic,
                    "value",
                    variable,
                    "time_average",
                    region_name,
                    km_set_name,
                    frames[0],
                    source_fingerprints,
                    x_limit,
                    y_limit,
                    extra={
                        "average": average.model_dump(mode="json"),
                        "average_statistic": average_statistic,
                        "times": [instant.isoformat() for instant in average_times],
                    },
                )
                if _reuse_figure(
                    output_path,
                    fingerprint,
                    project,
                    analysis,
                    fresh_figures,
                    reused,
                    report,
                ):
                    continue
                view = LineView.create(
                    frames[0].km_centres,
                    y_values,
                    labels,
                    display=display_mode,
                    figure_config=figure_config,
                    x_label=label(
                        "rkm",
                        project.language,
                        "value",
                        scale=0.001,
                        extra_dictionaries=project.labels.extra_dictionaries,
                    ).text,
                    y_label=_axis_value_label(
                        (
                            f"{average.name} — "
                            f"{_statistic_label(average_statistic, project)} of "
                            f"{base_statistic_text}"
                        ),
                        variable,
                        unit_text,
                        figure_config.display_scale,
                    ),
                    title=average_title,
                    x_limits=x_limit,
                    y_limits=y_limit,
                )
                context = _line_context(
                    project,
                    analysis,
                    "time_average",
                    variable,
                    "value",
                    simulation.label,
                    average_times[-1],
                    frames[0].km_centres,
                    y_values,
                    output_path,
                    average_title,
                    x_limit,
                    y_limit,
                    axes=view.axes,
                    extra_data={
                        "time_average": average.name,
                        "average_statistic": average_statistic,
                        "source_statistic": statistic,
                        "times": average_times,
                    },
                )
                _finalize_output(
                    view.figure,
                    context,
                    figure_config,
                    hooks,
                    guard,
                    fingerprint,
                    output_path,
                    provenance_path,
                    task_key=(
                        f"plot|{simulation.label}|{variable}|time_average|"
                        f"{average.name}|{statistic}|{average_statistic}|"
                        f"{region_name}|{km_set_name}|{limit_index}"
                    ),
                )
                outputs.append(output_path)
                report(f"Saved time-average figure {output_path}.")
    return tuple(outputs), tuple(reused)


def _plot_region_views(
    project: ProjectConfig,
    analysis: RiverKmStatisticsConfig,
    simulation: SimulationConfig,
    layout: OutputLayout,
    guard: OutputWriteGuard,
    region_name: str,
    km_set_name: str,
    variable: str,
    frames: tuple[StatisticCacheFrame, ...],
    *,
    reference_frames: tuple[StatisticCacheFrame, ...] | None,
    analysis_index: int,
    provenance_path: Path | None,
    fresh_figures: bool,
    report: DiagnosticSink,
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    outputs: list[Path] = []
    reused: list[Path] = []
    if not frames:
        return (), ()
    for statistic in analysis.statistics:
        current_values = tuple(
            statistic_values(frame, statistic, analysis) for frame in frames
        )
        for comparison in analysis.comparisons:
            compared = _comparison_values(
                simulation,
                project,
                analysis,
                frames,
                current_values,
                reference_frames,
                statistic,
                comparison,
            )
            if not compared:
                continue
            times = tuple(item[0] for item in compared)
            values = tuple(item[1] for item in compared)
            fingerprints = tuple(item[2] for item in compared)
            results = _plot_comparison_views(
                project,
                analysis,
                simulation,
                layout,
                guard,
                region_name,
                km_set_name,
                variable,
                statistic,
                comparison,
                frames,
                times,
                values,
                fingerprints,
                analysis_index=analysis_index,
                provenance_path=provenance_path,
                fresh_figures=fresh_figures,
                report=report,
            )
            outputs.extend(results[0])
            reused.extend(results[1])
    return tuple(outputs), tuple(reused)


def _plot_comparison_views(
    project: ProjectConfig,
    analysis: RiverKmStatisticsConfig,
    simulation: SimulationConfig,
    layout: OutputLayout,
    guard: OutputWriteGuard,
    region_name: str,
    km_set_name: str,
    variable: str,
    statistic: str,
    comparison: Comparison,
    frames: tuple[StatisticCacheFrame, ...],
    times: tuple[StatisticCacheFrame, ...],
    values: tuple[_Array, ...],
    fingerprints: tuple[tuple[str, ...], ...],
    *,
    analysis_index: int,
    provenance_path: Path | None,
    fresh_figures: bool,
    report: DiagnosticSink,
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    outputs: list[Path] = []
    reused: list[Path] = []
    for view_name in analysis.views:
        if view_name == "profile":
            results = _plot_profiles(
                project,
                analysis,
                simulation,
                layout,
                guard,
                region_name,
                km_set_name,
                variable,
                statistic,
                comparison,
                frames,
                times,
                values,
                fingerprints,
                view_name,
                analysis_index,
                provenance_path,
                fresh_figures,
                report,
            )
        elif view_name == "profile_all_times":
            results = _plot_profile_all_times(
                project,
                analysis,
                simulation,
                layout,
                guard,
                region_name,
                km_set_name,
                variable,
                statistic,
                comparison,
                frames,
                times,
                values,
                fingerprints,
                analysis_index,
                provenance_path,
                fresh_figures,
                report,
            )
        elif view_name == "space_time":
            results = _plot_space_time(
                project,
                analysis,
                simulation,
                layout,
                guard,
                region_name,
                km_set_name,
                variable,
                statistic,
                comparison,
                frames,
                times,
                values,
                fingerprints,
                analysis_index,
                provenance_path,
                fresh_figures,
                report,
            )
        elif view_name == "time_series":
            results = _plot_time_series(
                project,
                analysis,
                simulation,
                layout,
                guard,
                region_name,
                km_set_name,
                variable,
                statistic,
                comparison,
                frames,
                times,
                values,
                fingerprints,
                analysis_index,
                provenance_path,
                fresh_figures,
                report,
            )
        elif view_name == "cumulative":
            results = _plot_cumulative(
                project,
                analysis,
                simulation,
                layout,
                guard,
                region_name,
                km_set_name,
                variable,
                statistic,
                comparison,
                frames,
                times,
                values,
                fingerprints,
                analysis_index,
                provenance_path,
                fresh_figures,
                report,
            )
        else:
            raise RiverKmStatisticsPlotError(
                "V-CFG-001",
                f"Unsupported river-kilometre view {view_name!r}.",
            )
        outputs.extend(results[0])
        reused.extend(results[1])
    return tuple(outputs), tuple(reused)


def _comparison_values(
    simulation: SimulationConfig,
    project: ProjectConfig,
    analysis: RiverKmStatisticsConfig,
    frames: tuple[StatisticCacheFrame, ...],
    current: tuple[_Array, ...],
    reference_frames: tuple[StatisticCacheFrame, ...] | None,
    statistic: str,
    comparison: Comparison,
    *,
    reference_values: tuple[_Array, ...] | None = None,
) -> tuple[tuple[StatisticCacheFrame, _Array, tuple[str, ...]], ...]:
    reference = _reference_simulation(project)
    if comparison in _DIFFERENCE_COMPARISONS and reference_frames is None:
        raise RiverKmStatisticsPlotError(
            "V-PLOT-002",
            f"Comparison {comparison!r} requires reference statistics caches.",
        )
    if (
        comparison in _DIFFERENCE_COMPARISONS
        and simulation.label == reference.label
        and not analysis.include_reference_in_differences
    ):
        return ()

    reference_by_time = (
        {frame.time: frame for frame in reference_frames}
        if reference_frames is not None
        else {}
    )
    reference_values_by_time = (
        {
            frame.time: value
            for frame, value in zip(
                reference_frames or (),
                reference_values,
                strict=True,
            )
        }
        if reference_values is not None
        else {}
    )
    current_by_time = {
        frame.time: value for frame, value in zip(frames, current, strict=True)
    }
    common_times = (
        tuple(time for time in current_by_time if time in reference_by_time)
        if comparison in _DIFFERENCE_COMPARISONS
        else tuple(current_by_time)
    )
    if comparison in _DIFFERENCE_COMPARISONS and not common_times:
        raise RiverKmStatisticsPlotError(
            "V-SMB-014",
            "No matching simulation times exist for the configured comparison.",
        )

    result: list[tuple[StatisticCacheFrame, _Array, tuple[str, ...]]] = []
    base_time = common_times[0] if common_times else None
    base_current: _Array | None = None
    base_reference: _Array | None = None
    if comparison in {"difference_time", "difference_simulation_time"}:
        if base_time is None:
            base_time = frames[0].time
        base_current = current_by_time[base_time]
        if comparison == "difference_simulation_time":
            reference_frame = reference_by_time[base_time]
            base_reference = (
                reference_values_by_time[base_time]
                if base_time in reference_values_by_time
                else statistic_values(reference_frame, statistic, analysis)
            )

    for time in common_times:
        frame = next(item for item in frames if item.time == time)
        values = current_by_time[time]
        fingerprints = [frame.fingerprint]
        if comparison == "difference_time":
            if time == base_time and not analysis.include_initial_time_difference:
                continue
            if base_current is None:
                raise AssertionError("Time-difference baseline was not resolved.")
            values = values - base_current
        elif comparison in _DIFFERENCE_COMPARISONS:
            reference_frame = reference_by_time[time]
            reference_data = (
                reference_values_by_time[time]
                if time in reference_values_by_time
                else statistic_values(reference_frame, statistic, analysis)
            )
            if values.shape != reference_data.shape or not np.allclose(
                frame.km_centres,
                reference_frame.km_centres,
                rtol=0,
                atol=1e-8,
            ):
                raise RiverKmStatisticsPlotError(
                    "V-SMB-012",
                    "Simulation comparison requires matching river-kilometre "
                    "centres and component dimensions.",
                )
            fingerprints.append(reference_frame.fingerprint)
            values = values - reference_data
            if comparison == "difference_simulation_time":
                if time == base_time and not analysis.include_initial_time_difference:
                    continue
                if base_current is None or base_reference is None:
                    raise AssertionError("Simulation-time baseline was not resolved.")
                values = values - (base_current - base_reference)
            elif comparison == "difference_simulation_relative":
                values = difference_simulation_relative(
                    current_by_time[time],
                    reference_data,
                )
        result.append(
            (frame, np.asarray(values, dtype=np.float64), tuple(fingerprints))
        )
    return tuple(result)


def _source_statistic_values(
    frames: tuple[StatisticCacheFrame, ...],
    partner_frames: tuple[StatisticCacheFrame, ...] | None,
    statistic: str,
    analysis: RiverKmStatisticsConfig,
) -> tuple[tuple[StatisticCacheFrame, ...], tuple[_Array, ...]]:
    left_values = tuple(
        statistic_values(frame, statistic, analysis) for frame in frames
    )
    if partner_frames is None:
        return frames, left_values
    right_by_time = {frame.time: frame for frame in partner_frames}
    aligned_frames: list[StatisticCacheFrame] = []
    differences: list[_Array] = []
    for frame, left in zip(frames, left_values, strict=True):
        right_frame = right_by_time.get(frame.time)
        if right_frame is None:
            continue
        right = statistic_values(right_frame, statistic, analysis)
        if left.shape != right.shape or not np.allclose(
            frame.km_centres,
            right_frame.km_centres,
            rtol=0,
            atol=1e-8,
        ):
            raise RiverKmStatisticsPlotError(
                "V-SMB-012",
                "Region differences require matching river-kilometre centres "
                "and component dimensions.",
            )
        aligned_frames.append(frame)
        differences.append(difference_regions(left, right))
    if not aligned_frames:
        raise RiverKmStatisticsPlotError(
            "V-SMB-012",
            "Region differences require at least one matching output time.",
        )
    return tuple(aligned_frames), tuple(differences)


def _plot_grouped_sources(
    project: ProjectConfig,
    analysis: RiverKmStatisticsConfig,
    output_simulation: SimulationConfig,
    layout: OutputLayout,
    guard: OutputWriteGuard,
    subject: str,
    region_name: str,
    km_set_name: str,
    variable: str,
    sources: tuple[_PlotSource, ...],
    *,
    analysis_index: int,
    provenance_path: Path | None,
    fresh_figures: bool,
    report: DiagnosticSink,
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    outputs: list[Path] = []
    reused: list[Path] = []
    render_simulation = output_simulation.model_copy(update={"label": subject})
    for statistic in analysis.statistics:
        for comparison in analysis.comparisons:
            results_by_source: list[
                tuple[
                    _PlotSource,
                    dict[
                        datetime,
                        tuple[StatisticCacheFrame, _Array, tuple[str, ...]],
                    ],
                ]
            ] = []
            for source in sources:
                source_frames, source_values = _source_statistic_values(
                    source.frames,
                    source.partner_frames,
                    statistic,
                    analysis,
                )
                reference_values: tuple[_Array, ...] | None = None
                reference_frames = source.reference_frames
                if source.reference_frames is not None and (
                    source.reference_partner_frames is not None
                ):
                    reference_frames, reference_values = _source_statistic_values(
                        source.reference_frames,
                        source.reference_partner_frames,
                        statistic,
                        analysis,
                    )
                compared = _comparison_values(
                    source.simulation,
                    project,
                    analysis,
                    source_frames,
                    source_values,
                    reference_frames,
                    statistic,
                    comparison,
                    reference_values=reference_values,
                )
                if compared:
                    results_by_source.append(
                        (source, {item[0].time: item for item in compared})
                    )
            if not results_by_source:
                continue
            common_times = set.intersection(
                *(set(results) for _, results in results_by_source)
            )
            if not common_times:
                report(
                    f"V-SMB-014: no common output times for grouped figure "
                    f"{region_name!r}/{km_set_name!r}/{comparison!r}."
                )
                continue
            ordered_times = tuple(sorted(common_times))
            if any(set(results) != common_times for _, results in results_by_source):
                report(
                    f"Grouped figure {region_name!r} uses "
                    f"{len(ordered_times)} common output time(s)."
                )
            component_labels: list[str] = []
            for source, source_results in results_by_source:
                first_values = source_results[ordered_times[0]][1]
                count = first_values.shape[1] if first_values.ndim == 2 else 1
                component_names = source_results[ordered_times[0]][
                    0
                ].component_names or ("",)
                if len(component_names) != count:
                    raise RiverKmStatisticsPlotError(
                        "V-PLOT-002",
                        "Component labels do not match grouped statistic values.",
                    )
                for component_name in component_names:
                    name = component_name or _statistic_label(statistic, project)
                    component_labels.append(
                        f"{source.label} — {name}"
                        if len(results_by_source) > 1
                        else component_name
                    )

            composite_frames: list[StatisticCacheFrame] = []
            composite_values: list[_Array] = []
            composite_fingerprints: list[tuple[str, ...]] = []
            for time in ordered_times:
                frame_pieces: list[_Array] = []
                source_fingerprints: list[str] = []
                base_frame: StatisticCacheFrame | None = None
                for _, source_results in results_by_source:
                    frame, values, fingerprints = source_results[time]
                    if base_frame is None:
                        base_frame = frame
                    elif values.shape[
                        0
                    ] != base_frame.km_centres.size or not np.allclose(
                        frame.km_centres,
                        base_frame.km_centres,
                        rtol=0,
                        atol=1e-8,
                    ):
                        raise RiverKmStatisticsPlotError(
                            "V-SMB-012",
                            "Grouped figures require matching river-kilometre centres.",
                        )
                    frame_pieces.append(
                        values[:, np.newaxis] if values.ndim == 1 else values
                    )
                    source_fingerprints.extend(fingerprints)
                if base_frame is None:
                    raise AssertionError("Grouped results have no source frame.")
                composite_frames.append(
                    replace(
                        base_frame,
                        component_names=tuple(component_labels),
                    )
                )
                composite_values.append(np.concatenate(frame_pieces, axis=1))
                composite_fingerprints.append(tuple(source_fingerprints))

            render_analysis = analysis
            if len(results_by_source) > 1 or any(
                source.partner_frames is not None for source, _ in results_by_source
            ):
                render_analysis = analysis.model_copy(update={"display": "lines"})
            plotted, cached = _plot_comparison_views(
                project,
                render_analysis,
                render_simulation,
                layout,
                guard,
                region_name,
                km_set_name,
                variable,
                statistic,
                comparison,
                tuple(composite_frames),
                tuple(composite_frames),
                tuple(composite_values),
                tuple(composite_fingerprints),
                analysis_index=analysis_index,
                provenance_path=provenance_path,
                fresh_figures=fresh_figures,
                report=report,
            )
            outputs.extend(plotted)
            reused.extend(cached)
    return tuple(outputs), tuple(reused)


def _plot_profiles(
    project: ProjectConfig,
    analysis: RiverKmStatisticsConfig,
    simulation: SimulationConfig,
    layout: OutputLayout,
    guard: OutputWriteGuard,
    region_name: str,
    km_set_name: str,
    variable: str,
    statistic: str,
    comparison: Comparison,
    frames: tuple[StatisticCacheFrame, ...],
    times: tuple[StatisticCacheFrame, ...],
    values: tuple[_Array, ...],
    fingerprints: tuple[tuple[str, ...], ...],
    view_name: str,
    analysis_index: int,
    provenance_path: Path | None,
    fresh_figures: bool,
    report: DiagnosticSink,
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    outputs: list[Path] = []
    reused: list[Path] = []
    figure_config = project.effective_figure(analysis)
    statistic_text, unit_text = _value_label(variable, statistic, project)
    scale = figure_config.display_scale
    y_limits = _resolve_limits(
        figure_config.y_limits,
        variable,
        comparison,
    )
    x_limits = _resolve_limits(
        figure_config.x_limits,
        variable,
        comparison,
    )
    limit_pairs = _paired_limits(x_limits, y_limits)
    for frame, raw_values, source_fingerprints in zip(
        times, values, fingerprints, strict=True
    ):
        component_count = raw_values.shape[1] if raw_values.ndim == 2 else 1
        component_labels = frame.component_names or ("",)
        if len(component_labels) != component_count:
            raise RiverKmStatisticsPlotError(
                "V-PLOT-002",
                "Component labels do not match cached statistics dimensions.",
            )
        display_mode = _display_mode(analysis, variable)
        if comparison != "value" and display_mode == "stacked_area":
            display_mode = "lines"
        y_values = np.asarray(raw_values, dtype=np.float64) * scale
        if y_values.ndim == 1:
            y_values = y_values[np.newaxis, :]
        else:
            y_values = y_values.T
        line_labels = tuple(
            name or _statistic_label(statistic, project) for name in component_labels
        )
        title = _figure_title(
            project,
            statistic_text,
            region_name,
            km_set_name,
            comparison,
            frame.time,
        )
        for limit_index, (x_limit, y_limit) in enumerate(limit_pairs):
            output_path = _line_path(
                layout,
                project.series,
                simulation.label,
                region_name,
                km_set_name,
                variable,
                view_name,
                statistic,
                comparison,
                frame.time,
                limit_index,
            )
            limits_fingerprint = fingerprint_artifact(
                kind="figure",
                schema_version=CACHE_SCHEMA_VERSION,
                tool_version=__version__,
                algorithm_version="river-km-lines-1",
                parameters={
                    "analysis": analysis.model_dump(mode="json"),
                    "comparison": comparison,
                    "display": display_mode,
                    "figure": figure_config.model_dump(mode="json"),
                    "km_set": km_set_name,
                    "region": region_name,
                    "statistic": statistic,
                    "time": frame.time.isoformat(),
                    "variable": variable,
                    "view": view_name,
                    "x_limits": x_limit,
                    "y_limits": y_limit,
                },
                inputs=(
                    _configuration_fingerprint(project, analysis),
                    *_hook_fingerprints(project.effective_hooks(analysis)),
                    *source_fingerprints,
                ),
                time=frame.time.isoformat(),
            )
            if _reuse_figure(
                output_path,
                limits_fingerprint,
                project,
                analysis,
                fresh_figures,
                reused,
                report,
            ):
                continue
            view = LineView.create(
                frame.km_centres,
                y_values,
                line_labels,
                display=display_mode,
                figure_config=figure_config,
                x_label=label(
                    "rkm",
                    project.language,
                    "value",
                    scale=0.001,
                    extra_dictionaries=project.labels.extra_dictionaries,
                ).text,
                y_label=_axis_value_label(statistic_text, variable, unit_text, scale),
                title=title,
                x_limits=x_limit,
                y_limits=y_limit,
            )
            context = _line_context(
                project,
                analysis,
                view_name,
                variable,
                comparison,
                simulation.label,
                frame.time,
                frame.km_centres,
                y_values,
                output_path,
                title,
                x_limit,
                y_limit,
                axes=view.axes,
            )
            _finalize_output(
                view.figure,
                context,
                figure_config,
                project.effective_hooks(analysis),
                guard,
                limits_fingerprint,
                output_path,
                provenance_path,
                task_key=(
                    f"plot|{simulation.label}|{variable}|{view_name}|"
                    f"{comparison}|{region_name}|{km_set_name}|"
                    f"{frame.time.isoformat()}|{limit_index}"
                ),
            )
            outputs.append(output_path)
            report(f"Saved figure {output_path}.")
    return tuple(outputs), tuple(reused)


def _plot_profile_all_times(
    project: ProjectConfig,
    analysis: RiverKmStatisticsConfig,
    simulation: SimulationConfig,
    layout: OutputLayout,
    guard: OutputWriteGuard,
    region_name: str,
    km_set_name: str,
    variable: str,
    statistic: str,
    comparison: Comparison,
    frames: tuple[StatisticCacheFrame, ...],
    times: tuple[StatisticCacheFrame, ...],
    values: tuple[_Array, ...],
    fingerprints: tuple[tuple[str, ...], ...],
    analysis_index: int,
    provenance_path: Path | None,
    fresh_figures: bool,
    report: DiagnosticSink,
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    if not times:
        return (), ()
    values_by_line: list[_Array] = []
    line_labels: list[str] = []
    source_fingerprints: list[str] = []
    for frame, data, frame_fingerprints in zip(
        times, values, fingerprints, strict=True
    ):
        component_values = data[:, np.newaxis] if data.ndim == 1 else data
        for component_index, component_name in enumerate(
            frame.component_names or ("",)
        ):
            values_by_line.append(component_values[:, component_index])
            line_labels.append(
                frame.time.isoformat()
                + (f" — {component_name}" if component_name else "")
            )
        source_fingerprints.extend(frame_fingerprints)
    first = times[0]
    figure_config = project.effective_figure(analysis)
    statistic_text, unit_text = _value_label(variable, statistic, project)
    scaled = np.asarray(values_by_line, dtype=np.float64) * figure_config.display_scale
    title = _figure_title(
        project,
        statistic_text,
        region_name,
        km_set_name,
        comparison,
        None,
    )
    x_limits = _resolve_limits(figure_config.x_limits, variable, comparison)
    y_limits = _resolve_limits(figure_config.y_limits, variable, comparison)
    outputs: list[Path] = []
    reused: list[Path] = []
    for limit_index, (x_limit, y_limit) in enumerate(
        _paired_limits(x_limits, y_limits)
    ):
        output_path = _line_path(
            layout,
            project.series,
            simulation.label,
            region_name,
            km_set_name,
            variable,
            "profile_all_times",
            statistic,
            comparison,
            None,
            limit_index,
        )
        fingerprint = _line_fingerprint(
            project,
            analysis,
            statistic,
            comparison,
            variable,
            "profile_all_times",
            region_name,
            km_set_name,
            first,
            source_fingerprints,
            x_limit,
            y_limit,
            extra={"times": [frame.time.isoformat() for frame in times]},
        )
        if _reuse_figure(
            output_path,
            fingerprint,
            project,
            analysis,
            fresh_figures,
            reused,
            report,
        ):
            continue
        view = LineView.create(
            first.km_centres,
            scaled,
            tuple(line_labels),
            display="lines",
            figure_config=figure_config,
            x_label=label(
                "rkm",
                project.language,
                "value",
                scale=0.001,
                extra_dictionaries=project.labels.extra_dictionaries,
            ).text,
            y_label=_axis_value_label(
                statistic_text,
                variable,
                unit_text,
                figure_config.display_scale,
            ),
            title=title,
            x_limits=x_limit,
            y_limits=y_limit,
        )
        context = _line_context(
            project,
            analysis,
            "profile_all_times",
            variable,
            comparison,
            simulation.label,
            times[-1].time,
            first.km_centres,
            scaled,
            output_path,
            title,
            x_limit,
            y_limit,
            axes=view.axes,
            extra_data={"times": tuple(frame.time for frame in times)},
        )
        _finalize_output(
            view.figure,
            context,
            figure_config,
            project.effective_hooks(analysis),
            guard,
            fingerprint,
            output_path,
            provenance_path,
            task_key=(
                f"plot|{simulation.label}|{variable}|profile_all_times|"
                f"{comparison}|{region_name}|{km_set_name}|{limit_index}"
            ),
        )
        outputs.append(output_path)
        report(f"Saved figure {output_path}.")
    return tuple(outputs), tuple(reused)


def _plot_space_time(
    project: ProjectConfig,
    analysis: RiverKmStatisticsConfig,
    simulation: SimulationConfig,
    layout: OutputLayout,
    guard: OutputWriteGuard,
    region_name: str,
    km_set_name: str,
    variable: str,
    statistic: str,
    comparison: Comparison,
    frames: tuple[StatisticCacheFrame, ...],
    times: tuple[StatisticCacheFrame, ...],
    values: tuple[_Array, ...],
    fingerprints: tuple[tuple[str, ...], ...],
    analysis_index: int,
    provenance_path: Path | None,
    fresh_figures: bool,
    report: DiagnosticSink,
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    if len(times) < 2:
        raise RiverKmStatisticsPlotError(
            "V-SMB-014",
            "The space_time view requires at least two selected output times.",
        )
    first = times[0]
    km_edges = _km_edges(first)
    time_centres = np.asarray(
        [mdates.date2num(frame.time) for frame in times],
        dtype=np.float64,
    )
    time_edges = _centre_edges(time_centres)
    component_count = values[0].shape[1] if values[0].ndim == 2 else 1
    components = first.component_names or ("",)
    if len(components) != component_count:
        raise RiverKmStatisticsPlotError(
            "V-PLOT-002",
            "Component labels do not match cached statistics dimensions.",
        )
    statistic_text, unit_text = _value_label(variable, statistic, project)
    figure_config = project.effective_figure(analysis)
    output_paths: list[Path] = []
    reused: list[Path] = []
    for component_index, component_name in enumerate(components):
        matrix = np.stack(
            [data if data.ndim == 1 else data[:, component_index] for data in values],
            axis=0,
        )
        matrix = matrix * figure_config.display_scale
        title = _figure_title(
            project,
            statistic_text,
            region_name,
            km_set_name,
            comparison,
            None,
            component_name=component_name,
        )
        output_path = _line_path(
            layout,
            project.series,
            simulation.label,
            region_name,
            km_set_name,
            variable,
            "space_time",
            statistic,
            comparison,
            None,
            component_index,
        )
        fingerprint = _line_fingerprint(
            project,
            analysis,
            statistic,
            comparison,
            variable,
            "space_time",
            region_name,
            km_set_name,
            first,
            tuple(value for item in fingerprints for value in item),
            None,
            None,
            extra={
                "component": component_name,
                "times": [frame.time.isoformat() for frame in times],
            },
        )
        if _reuse_figure(
            output_path,
            fingerprint,
            project,
            analysis,
            fresh_figures,
            reused,
            report,
        ):
            continue
        view = SpaceTimeView.create(
            km_edges,
            time_edges,
            matrix,
            figure_config=figure_config,
            colormap=_map_colormap(analysis, variable),
            colorbar_label=_axis_value_label(
                statistic_text,
                variable,
                unit_text,
                figure_config.display_scale,
            ),
            x_label=label(
                "rkm",
                project.language,
                "value",
                scale=0.001,
                extra_dictionaries=project.labels.extra_dictionaries,
            ).text,
            y_label=label(
                "time",
                project.language,
                "quantity",
                extra_dictionaries=project.labels.extra_dictionaries,
            ).text,
            title=title,
            limits=_map_limits(analysis, variable),
        )
        view.axes.yaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d", tz=UTC))
        view.figure.tight_layout()
        context = FigureContext(
            analysis="river_km_statistics",
            view="space_time",
            comparison=comparison,
            variable=variable,
            simulation=simulation.label,
            time=times[-1].time,
            extent=(
                float(km_edges[0]),
                float(km_edges[-1]),
                float(time_edges[0]),
                float(time_edges[-1]),
            ),
            axes={"model": view.axes, "colorbar": view.colorbar.ax},
            data={
                "values": matrix,
                "km_edges": km_edges,
                "times": tuple(frame.time for frame in times),
            },
            labels={"title": title, "value": statistic_text},
            language=project.language,
            output_path=output_path,
        )
        _finalize_output(
            view.figure,
            context,
            figure_config,
            project.effective_hooks(analysis),
            guard,
            fingerprint,
            output_path,
            provenance_path,
            task_key=(
                f"plot|{simulation.label}|{variable}|space_time|{comparison}|"
                f"{region_name}|{km_set_name}|{component_index}"
            ),
        )
        output_paths.append(output_path)
        report(f"Saved figure {output_path}.")
    return tuple(output_paths), tuple(reused)


def _plot_time_series(
    project: ProjectConfig,
    analysis: RiverKmStatisticsConfig,
    simulation: SimulationConfig,
    layout: OutputLayout,
    guard: OutputWriteGuard,
    region_name: str,
    km_set_name: str,
    variable: str,
    statistic: str,
    comparison: Comparison,
    frames: tuple[StatisticCacheFrame, ...],
    times: tuple[StatisticCacheFrame, ...],
    values: tuple[_Array, ...],
    fingerprints: tuple[tuple[str, ...], ...],
    analysis_index: int,
    provenance_path: Path | None,
    fresh_figures: bool,
    report: DiagnosticSink,
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    if not analysis.time_series_locations_km:
        report("No time_series_locations_km configured; time_series view skipped.")
        return (), ()
    first = times[0]
    figure_config = project.effective_figure(analysis)
    statistic_text, unit_text = _value_label(variable, statistic, project)
    time_values = np.asarray(
        [mdates.date2num(frame.time) for frame in times],
        dtype=np.float64,
    )
    output_paths: list[Path] = []
    reused: list[Path] = []
    component_labels = first.component_names or ("",)
    for location_index, location_km in enumerate(analysis.time_series_locations_km):
        segment_index = int(np.argmin(np.abs(first.km_centres - location_km)))
        chosen_km = float(first.km_centres[segment_index])
        component_count = values[0].shape[1] if values[0].ndim == 2 else 1
        lines: list[_Array] = []
        line_labels: list[str] = []
        for component_index in range(component_count):
            lines.append(
                np.asarray(
                    [
                        data[segment_index]
                        if data.ndim == 1
                        else data[segment_index, component_index]
                        for data in values
                    ],
                    dtype=np.float64,
                )
            )
            component_name = component_labels[component_index]
            line_labels.append(component_name or _statistic_label(statistic, project))
        y_values = np.stack(lines, axis=0) * figure_config.display_scale
        title = (
            f"{_statistic_label(statistic, project)} — "
            f"{region_name} — {km_set_name} — {chosen_km:g} km"
        )
        output_path = _line_path(
            layout,
            project.series,
            simulation.label,
            region_name,
            km_set_name,
            variable,
            "time_series",
            statistic,
            comparison,
            None,
            location_index,
        )
        fingerprint = _line_fingerprint(
            project,
            analysis,
            statistic,
            comparison,
            variable,
            "time_series",
            region_name,
            km_set_name,
            first,
            tuple(value for item in fingerprints for value in item),
            None,
            None,
            extra={
                "component_labels": component_labels,
                "location_km": location_km,
                "selected_km": chosen_km,
                "times": [frame.time.isoformat() for frame in times],
            },
        )
        if _reuse_figure(
            output_path,
            fingerprint,
            project,
            analysis,
            fresh_figures,
            reused,
            report,
        ):
            continue
        view = LineView.create(
            time_values,
            y_values,
            tuple(line_labels),
            display="lines",
            figure_config=figure_config,
            x_label=label(
                "time",
                project.language,
                "quantity",
                extra_dictionaries=project.labels.extra_dictionaries,
            ).text,
            y_label=_axis_value_label(
                statistic_text,
                variable,
                unit_text,
                figure_config.display_scale,
            ),
            title=title,
        )
        view.axes.xaxis_date()
        view.axes.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d", tz=UTC))
        view.figure.autofmt_xdate()
        context = _line_context(
            project,
            analysis,
            "time_series",
            variable,
            comparison,
            simulation.label,
            times[-1].time,
            time_values,
            y_values,
            output_path,
            title,
            None,
            None,
            axes=view.axes,
            extra_data={
                "km": chosen_km,
                "requested_km": float(location_km),
                "times": tuple(frame.time for frame in times),
            },
        )
        _finalize_output(
            view.figure,
            context,
            figure_config,
            project.effective_hooks(analysis),
            guard,
            fingerprint,
            output_path,
            provenance_path,
            task_key=(
                f"plot|{simulation.label}|{variable}|time_series|{comparison}|"
                f"{region_name}|{km_set_name}|{chosen_km:g}"
            ),
        )
        output_paths.append(output_path)
        report(f"Saved figure {output_path}.")
    return tuple(output_paths), tuple(reused)


def _plot_cumulative(
    project: ProjectConfig,
    analysis: RiverKmStatisticsConfig,
    simulation: SimulationConfig,
    layout: OutputLayout,
    guard: OutputWriteGuard,
    region_name: str,
    km_set_name: str,
    variable: str,
    statistic: str,
    comparison: Comparison,
    frames: tuple[StatisticCacheFrame, ...],
    times: tuple[StatisticCacheFrame, ...],
    values: tuple[_Array, ...],
    fingerprints: tuple[tuple[str, ...], ...],
    analysis_index: int,
    provenance_path: Path | None,
    fresh_figures: bool,
    report: DiagnosticSink,
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    cumulative = compute_cumulative(
        np.stack(values, axis=0),
        tuple(frame.time for frame in times),
    )
    cumulative_values = tuple(cumulative[index] for index in range(cumulative.shape[0]))
    return _plot_profiles(
        project,
        analysis,
        simulation,
        layout,
        guard,
        region_name,
        km_set_name,
        variable,
        statistic,
        comparison,
        frames,
        times,
        cumulative_values,
        fingerprints,
        "cumulative",
        analysis_index,
        provenance_path,
        fresh_figures,
        report,
    )


def _figure_title(
    project: ProjectConfig,
    statistic_text: str,
    region_name: str,
    km_set_name: str,
    comparison: str,
    time: datetime | None,
    *,
    component_name: str | None = None,
) -> str:
    parts = [statistic_text, region_name, km_set_name]
    if comparison != "value":
        parts.append(comparison.replace("_", " "))
    if time is not None:
        parts.append(time.isoformat())
    if component_name:
        parts.append(component_name)
    return " — ".join(parts)


def _display_mode(
    analysis: RiverKmStatisticsConfig,
    variable: str,
) -> Literal["lines", "stacked_area"]:
    configured = analysis.display
    if isinstance(configured, Mapping):
        mode = next(
            (
                value
                for name, value in configured.items()
                if name.casefold() == variable.casefold()
            ),
            "auto",
        )
    else:
        mode = configured
    if mode == "auto":
        return "stacked_area" if get_variable_spec(variable).extra_dims else "lines"
    return "lines" if mode == "lines" else "stacked_area"


def _axis_value_label(
    statistic_text: str,
    variable: str,
    unit_text: str,
    scale: float,
) -> str:
    scale_text = f" × {scale:g}" if scale != 1.0 else ""
    unit = f" [{unit_text}{scale_text}]" if unit_text else ""
    return f"{statistic_text} — {variable}{unit}"


def _paired_limits(
    x_limits: tuple[AxisLimit | None, ...],
    y_limits: tuple[AxisLimit | None, ...],
) -> tuple[tuple[AxisLimit | None, AxisLimit | None], ...]:
    count = max(len(x_limits), len(y_limits))
    if len(x_limits) not in {1, count} or len(y_limits) not in {1, count}:
        raise RiverKmStatisticsPlotError(
            "V-CFG-004",
            "The number of x_limits and y_limits rows must match.",
        )
    return tuple(
        (
            x_limits[0] if len(x_limits) == 1 else x_limits[index],
            y_limits[0] if len(y_limits) == 1 else y_limits[index],
        )
        for index in range(count)
    )


def _line_path(
    layout: OutputLayout,
    series: str,
    subject: str,
    region_name: str,
    km_set_name: str,
    variable: str,
    view_name: str,
    statistic: str,
    comparison: str,
    time: datetime | None,
    limit_index: int,
) -> Path:
    time_text = (
        time.astimezone(UTC).strftime("%Y%m%dT%H%M%S")
        if time is not None
        else "all_times"
    )
    return (
        layout.figures
        / quote(series, safe="-_.")
        / quote(subject, safe="-_.")
        / "river_km_statistics"
        / quote(region_name, safe="-_.")
        / quote(km_set_name, safe="-_.")
        / quote(variable.casefold(), safe="-_.")
        / f"{view_name}_{statistic}_{comparison}_{time_text}_l{limit_index + 1}.png"
    )


def _reuse_figure(
    output_path: Path,
    fingerprint: str,
    project: ProjectConfig,
    analysis: RiverKmStatisticsConfig,
    fresh_figures: bool,
    reused: list[Path],
    report: DiagnosticSink,
) -> bool:
    decision = decide_cache(
        exists=output_path.is_file(),
        existing_fingerprint=_png_fingerprint(output_path),
        expected_fingerprint=fingerprint,
        policy="always" if fresh_figures else project.effective_replot(analysis),
    )
    if decision.action == "reuse":
        reused.append(output_path)
        report(f"Reused figure {output_path}.")
        if decision.warning is not None:
            report(f"WARNING: {decision.warning}")
        return True
    return False


def _line_fingerprint(
    project: ProjectConfig,
    analysis: RiverKmStatisticsConfig,
    statistic: str,
    comparison: str,
    variable: str,
    view_name: str,
    region_name: str,
    km_set_name: str,
    first: StatisticCacheFrame,
    source_fingerprints: Sequence[str],
    x_limits: AxisLimit | None,
    y_limits: AxisLimit | None,
    *,
    extra: Mapping[str, object] | None = None,
) -> str:
    parameters: dict[str, object] = {
        "analysis": analysis.model_dump(mode="json"),
        "comparison": comparison,
        "figure": project.effective_figure(analysis).model_dump(mode="json"),
        "km_set": km_set_name,
        "region": region_name,
        "statistic": statistic,
        "variable": variable,
        "view": view_name,
        "x_limits": x_limits,
        "y_limits": y_limits,
    }
    if extra is not None:
        parameters.update(extra)
    return fingerprint_artifact(
        kind="figure",
        schema_version=CACHE_SCHEMA_VERSION,
        tool_version=__version__,
        algorithm_version="river-km-plot-1",
        parameters=parameters,
        inputs=(
            _configuration_fingerprint(project, analysis),
            *_hook_fingerprints(project.effective_hooks(analysis)),
            first.fingerprint,
            *source_fingerprints,
        ),
    )


def _line_context(
    project: ProjectConfig,
    analysis: RiverKmStatisticsConfig,
    view_name: str,
    variable: str,
    comparison: str,
    subject: str,
    time: datetime,
    x: _Array,
    values: _Array,
    output_path: Path,
    title: str,
    x_limits: AxisLimit | None,
    y_limits: AxisLimit | None,
    *,
    axes: Any,
    extra_data: Mapping[str, object] | None = None,
) -> FigureContext:
    finite_x = np.asarray(x, dtype=np.float64)
    finite_y = np.asarray(values, dtype=np.float64)
    finite_y = finite_y[np.isfinite(finite_y)]
    x_extent = (
        float(x_limits[0])
        if x_limits and x_limits[0] is not None
        else float(finite_x.min()),
        float(x_limits[1])
        if x_limits and x_limits[1] is not None
        else float(finite_x.max()),
    )
    if finite_y.size:
        y_extent = (
            float(y_limits[0])
            if y_limits and y_limits[0] is not None
            else float(finite_y.min()),
            float(y_limits[1])
            if y_limits and y_limits[1] is not None
            else float(finite_y.max()),
        )
    else:
        y_extent = (0.0, 1.0)
    data: dict[str, object] = {"x": x, "values": values, "time": time}
    if extra_data is not None:
        data.update(extra_data)
    return FigureContext(
        analysis="river_km_statistics",
        view=view_name,
        comparison=comparison,
        variable=variable,
        simulation=subject,
        time=time,
        extent=(x_extent[0], x_extent[1], y_extent[0], y_extent[1]),
        axes={"model": axes},
        data=data,
        labels={"title": title},
        language=project.language,
        output_path=output_path,
    )


def _km_edges(frame: StatisticCacheFrame) -> _Array:
    if frame.km_edge_start.size == 0:
        raise RiverKmStatisticsPlotError(
            "V-PLOT-002",
            f"Statistics cache has no segment coordinates: {frame.path}.",
        )
    if frame.km_edge_start.size > 1 and not np.allclose(
        frame.km_edge_end[:-1],
        frame.km_edge_start[1:],
        rtol=0,
        atol=1e-8,
    ):
        raise RiverKmStatisticsPlotError(
            "V-PLOT-002",
            f"Segment edges are discontinuous in {frame.path}.",
        )
    return np.concatenate((frame.km_edge_start[:1], frame.km_edge_end))


def _centre_edges(centres: _Array) -> _Array:
    values = np.asarray(centres, dtype=np.float64)
    if values.ndim != 1 or values.size < 2 or np.any(np.diff(values) <= 0):
        raise RiverKmStatisticsPlotError(
            "V-SMB-014",
            "The space_time view requires at least two strictly increasing times.",
        )
    midpoints = 0.5 * (values[1:] + values[:-1])
    return np.concatenate(
        (
            [values[0] - (midpoints[0] - values[0])],
            midpoints,
            [values[-1] + (values[-1] - midpoints[-1])],
        )
    )


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
