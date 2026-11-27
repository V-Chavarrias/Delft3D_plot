# SPDX-License-Identifier: GPL-3.0-or-later
"""HIS station time-series stage for D-Flow FM HIS files and D3D4 trih files (D132)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import TypeAlias
from urllib.parse import quote

import matplotlib as mpl
import matplotlib.dates as mdates
import netCDF4
import numpy as np
from matplotlib.axes import Axes
from matplotlib.colors import to_hex
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

from d3dplot import __version__
from d3dplot.analyses.his.config import HisConfig
from d3dplot.analyses.his.godin import godin_filter
from d3dplot.analyses.his.measurements import (
    HisMeasurement,
    HisMeasurementCatalogue,
    read_his_measurements,
)
from d3dplot.config.figure import FigureConfig
from d3dplot.config.project import ProjectConfig, SimulationConfig
from d3dplot.config.selections import LayersSelection
from d3dplot.plotting.colormaps import default_colormap
from d3dplot.plotting.figure_names import (
    axis_label,
    comparison_label_kind,
    figure_file_path,
)
from d3dplot.plotting.finalize import FigureContext, finalize_figure
from d3dplot.plotting.views.line import LineView
from d3dplot.plotting.views.space_time import SpaceTimeView
from d3dplot.plotting.writers import get_figure_writer
from d3dplot.river_km.file import (
    RiverKmFileError,
    RiverKmPoint,
    read_river_km_points,
)
from d3dplot.river_km.geometry import RiverKmGeometryError, centres_to_edges
from d3dplot.simulation.his import (
    FmHisFile,
    HisError,
    HisReader,
    HisSeries,
    TrihHisFile,
    check_layer,
    locate_his_file,
    open_his_reader,
    resolve_station,
)
from d3dplot.simulation.time_utils import figure_timezone, offset_text
from d3dplot.store import (
    CACHE_SCHEMA_VERSION,
    OutputLayout,
    OutputWriteGuard,
    atomic_path,
    fingerprint_artifact,
    fingerprint_model_output,
    initialize_output_root,
)
from d3dplot.store.paths import OutputArea, build_quantity_output_path
from d3dplot.variables.registry import UnknownVariableError, get_variable_spec

HisFile: TypeAlias = FmHisFile | TrihHisFile

_ALGORITHM_VERSION = "2"
_CACHE_POLICIES = frozenset({"if_changed", "never", "always"})
_UNSAFE_FILE_CHARACTERS = re.compile(r"[^\w.-]+")
_LINEWIDTH = 1.0
_RAW_LINEWIDTH = 0.5
_MEASURED_COLOUR = "black"


class HisStageError(ValueError):
    """A HIS analysis cannot be processed or plotted."""


@dataclass(frozen=True, slots=True)
class HisStageResult:
    """Figures and station caches produced by the HIS stage."""

    output_paths: tuple[Path, ...]
    reused_paths: tuple[Path, ...]
    messages: tuple[str, ...]
    cache_paths: tuple[Path, ...] = ()
    reused_cache_paths: tuple[Path, ...] = ()


@dataclass(slots=True)
class _Run:
    outputs: list[Path] = field(default_factory=list)
    reused: list[Path] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    cache_paths: list[Path] = field(default_factory=list)
    reused_cache_paths: list[Path] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Series:
    simulation: SimulationConfig
    variable: str
    station_name: str
    time_seconds: np.ndarray
    values: np.ndarray
    fingerprint: str
    layer: int
    layered: bool
    position: tuple[float, float] | None = None


@dataclass(frozen=True, slots=True)
class _Line:
    label: str
    time_seconds: np.ndarray
    values: np.ndarray
    color: str | None = None
    linewidth: float = _LINEWIDTH


def plot_his_stage(
    project: ProjectConfig,
    *,
    analysis_name: str | None = None,
    simulation_label: str | None = None,
) -> HisStageResult:
    """Render configured HIS figures and build their station caches."""
    return _run_stage(
        project,
        analysis_name=analysis_name,
        simulation_label=simulation_label,
        render_figures=True,
    )


def process_his_stage(
    project: ProjectConfig,
    *,
    analysis_name: str | None = None,
    simulation_label: str | None = None,
) -> HisStageResult:
    """Build configured HIS station caches without rendering figures."""
    return _run_stage(
        project,
        analysis_name=analysis_name,
        simulation_label=simulation_label,
        render_figures=False,
    )


def _run_stage(
    project: ProjectConfig,
    *,
    analysis_name: str | None,
    simulation_label: str | None,
    render_figures: bool,
) -> HisStageResult:
    run = _Run()
    for configured in project.analyses:
        if not isinstance(configured, HisConfig):
            continue
        if analysis_name is not None and analysis_name != configured.analysis:
            continue
        _run_analysis(
            project,
            configured,
            run,
            simulation_label=simulation_label,
            render_figures=render_figures,
        )
    return HisStageResult(
        tuple(run.outputs),
        tuple(run.reused),
        tuple(run.messages),
        tuple(run.cache_paths),
        tuple(run.reused_cache_paths),
    )


def _run_analysis(
    project: ProjectConfig,
    analysis: HisConfig,
    run: _Run,
    *,
    simulation_label: str | None,
    render_figures: bool,
) -> None:
    selected = [
        simulation
        for simulation in project.simulations
        if simulation_label is None or simulation.label == simulation_label
    ]
    if not selected:
        return
    reference = _reference_simulation(project) if render_figures else None
    sources = list(selected)
    if reference is not None and all(s.label != reference.label for s in selected):
        sources.append(reference)
    located = {s.label: _locate(s, analysis, run) for s in sources}

    zone: timezone | None = None
    if reference is not None:
        reference_file = located[reference.label]
        if reference_file is None:
            run.messages.append(
                "HIS figures skipped: the reference simulation has no HIS file."
            )
            return
        zone = _display_zone(project, reference_file)

    targets = {s.label: _target(project, s) for s in selected}
    series = _collect_series(project, analysis, selected, located, targets, run)
    if reference is not None and zone is not None:
        _plot(
            project,
            analysis,
            run,
            selected=selected,
            reference=reference,
            series=series,
            zone=zone,
            targets=targets,
        )


def _reference_simulation(project: ProjectConfig) -> SimulationConfig:
    reference = project.reference_simulation
    if isinstance(reference, str):
        for simulation in project.simulations:
            if simulation.label == reference:
                return simulation
        raise HisStageError(f"Unknown reference simulation {reference!r}.")
    try:
        return project.simulations[reference]
    except IndexError as error:
        raise HisStageError(
            f"Reference simulation index {reference} is outside the configured list."
        ) from error


def _locate(
    simulation: SimulationConfig,
    analysis: HisConfig,
    run: _Run,
) -> HisFile | None:
    try:
        return locate_his_file(simulation.path)
    except HisError as error:
        if analysis.on_missing_input == "skip":
            run.messages.append(f"Skipped HIS for {simulation.label!r}: {error}")
            return None
        raise HisStageError(str(error)) from error


def _display_zone(project: ProjectConfig, reference_file: HisFile) -> timezone:
    with open_his_reader(reference_file) as reader:
        offset = offset_text(reader.zone)
    return figure_timezone(
        own_offset=offset,
        reference_offset=offset,
        explicit_time_zone=(
            None if project.time_zone is None else str(project.time_zone)
        ),
        compares_simulations=True,
    )


def _target(
    project: ProjectConfig,
    simulation: SimulationConfig,
) -> tuple[OutputLayout, OutputWriteGuard]:
    layout = OutputLayout.for_simulation(simulation, project.output)
    guard = initialize_output_root(
        layout,
        tool_version=__version__,
        forbidden_roots=project.output.forbidden_roots,
    )
    return layout, guard


def _collect_series(
    project: ProjectConfig,
    analysis: HisConfig,
    selected: Sequence[SimulationConfig],
    located: dict[str, HisFile | None],
    targets: dict[str, tuple[OutputLayout, OutputWriteGuard]],
    run: _Run,
) -> list[_Series]:
    policy = project.effective_reprocess(analysis)
    if policy not in _CACHE_POLICIES:
        raise HisStageError(f"Unsupported HIS reprocess policy: {policy!r}.")
    series: list[_Series] = []
    for simulation in selected:
        his_file = located[simulation.label]
        if his_file is None:
            continue
        layout, guard = targets[simulation.label]
        input_fingerprints = [
            fingerprint_model_output(path) for path in _input_paths(his_file)
        ]
        with open_his_reader(his_file) as reader:
            for station in analysis.stations:
                index = resolve_station(reader.station_names, station)
                station_name = reader.station_names[index]
                position = (
                    _station_position(reader, index)
                    if "x_t" in analysis.views
                    else None
                )
                for variable in analysis.variables:
                    count, layers = _layers(analysis, reader, variable)
                    for layer in layers:
                        series.append(
                            _series_for(
                                layout=layout,
                                guard=guard,
                                simulation=simulation,
                                reader=reader,
                                index=index,
                                station_name=station_name,
                                variable=variable,
                                layer=layer,
                                layered=count > 1,
                                input_fingerprints=input_fingerprints,
                                policy=policy,
                                run=run,
                                position=position,
                            )
                        )
    return series


def _station_position(reader: HisReader, index: int) -> tuple[float, float]:
    try:
        return reader.station_xy(index)
    except HisError as error:
        raise HisStageError(str(error)) from error


def _layers(
    analysis: HisConfig,
    reader: HisReader,
    variable: str,
) -> tuple[int, list[int]]:
    count = reader.layer_count(variable)
    if not isinstance(analysis.vertical, LayersSelection):
        return count, list(range(1, count + 1))
    try:
        for layer in analysis.vertical.layers:
            check_layer(variable, layer, count)
    except HisError as error:
        raise HisStageError(str(error)) from error
    return count, list(analysis.vertical.layers)


def _input_paths(his_file: HisFile) -> tuple[Path, ...]:
    if isinstance(his_file, TrihHisFile):
        return (his_file.data_path, his_file.def_path, his_file.mdf_path)
    return (his_file.data_path,)


def _series_for(
    *,
    layout: OutputLayout,
    guard: OutputWriteGuard,
    simulation: SimulationConfig,
    reader: HisReader,
    index: int,
    station_name: str,
    variable: str,
    layer: int,
    layered: bool,
    input_fingerprints: list[str],
    policy: str,
    run: _Run,
    position: tuple[float, float] | None,
) -> _Series:
    # The one-based index keeps names that sanitise to the same text apart.
    # Only layered variables carry the layer in the cache name and parameters, so
    # single-layer caches written before layers were supported stay valid.
    layer_text = f"_l{layer:03d}" if layered else ""
    parameters: dict[str, object] = {
        "station": station_name,
        "station_index": index + 1,
        "variable": variable,
    }
    if layered:
        parameters["layer"] = layer
    cache_path = build_quantity_output_path(
        layout,
        OutputArea.CACHE_DATA,
        variable,
        prefix=("his",),
        suffix=(),
        filename=(
            f"{index + 1:05d}_{_UNSAFE_FILE_CHARACTERS.sub('_', station_name)}"
            f"{layer_text}.nc"
        ),
    )
    fingerprint = fingerprint_artifact(
        kind="his",
        schema_version=CACHE_SCHEMA_VERSION,
        tool_version=__version__,
        algorithm_version=_ALGORITHM_VERSION,
        parameters=parameters,
        inputs=input_fingerprints,
    )
    if policy != "always" and cache_path.is_file():
        cached = _read_cache(cache_path, fingerprint)
        if cached is not None:
            run.reused_cache_paths.append(cache_path)
            run.messages.append(
                f"Reused HIS cache: {variable}/{station_name} layer {layer} for "
                f"{simulation.label}."
            )
            return _Series(
                simulation=simulation,
                variable=variable,
                station_name=station_name,
                time_seconds=cached[0],
                values=cached[1],
                fingerprint=fingerprint,
                layer=layer,
                layered=layered,
                position=position,
            )
    if policy == "never":
        raise HisStageError(
            f"HIS cache is missing or stale for {variable}/{station_name} layer "
            f"{layer} of {simulation.label!r} and reprocess is 'never'."
        )
    his_series = _read_his_series(reader, index, variable, layer)
    _write_cache(
        cache_path,
        guard,
        series=his_series,
        station=station_name,
        variable=variable,
        fingerprint=fingerprint,
    )
    run.cache_paths.append(cache_path)
    run.messages.append(
        f"Built HIS cache: {variable}/{station_name} layer {layer} for "
        f"{simulation.label}."
    )
    times, values = _cache_arrays(his_series)
    return _Series(
        simulation=simulation,
        variable=variable,
        station_name=station_name,
        time_seconds=times,
        values=values,
        fingerprint=fingerprint,
        layer=layer,
        layered=layered,
        position=position,
    )


def _read_his_series(
    reader: HisReader,
    index: int,
    variable: str,
    layer: int,
) -> HisSeries:
    return reader.read(variable, index, layer=layer)


def _cache_arrays(series: HisSeries) -> tuple[np.ndarray, np.ndarray]:
    # Values are float32 on disk (D26); round-trip so fresh and cached runs agree.
    times = np.asarray(series.time_seconds, dtype=np.float64)
    values = np.asarray(series.values, dtype=np.float32).astype(np.float64)
    return times, values


def _read_cache(
    path: Path,
    fingerprint: str,
) -> tuple[np.ndarray, np.ndarray] | None:
    try:
        with netCDF4.Dataset(path, mode="r") as dataset:
            if str(dataset.getncattr("d3dplot_fingerprint")) != fingerprint:
                return None
            times = np.asarray(dataset.variables["time_seconds"][:], dtype=np.float64)
            values = np.asarray(
                np.ma.filled(dataset.variables["values"][:], np.nan),
                dtype=np.float64,
            )
    except (OSError, KeyError, AttributeError, RuntimeError):
        return None
    return times, values


def _write_cache(
    path: Path,
    guard: OutputWriteGuard,
    *,
    series: HisSeries,
    station: str,
    variable: str,
    fingerprint: str,
) -> None:
    with atomic_path(path, guard) as temporary:
        with netCDF4.Dataset(temporary, mode="w", format="NETCDF4") as dataset:
            dataset.createDimension("time", int(series.time_seconds.size))
            times = dataset.createVariable("time_seconds", "f8", ("time",))
            times.long_name = "UTC epoch seconds"
            times[:] = series.time_seconds
            values = dataset.createVariable("values", "f4", ("time",))
            values[:] = series.values
            dataset.setncattr("d3dplot_fingerprint", fingerprint)
            dataset.setncattr("station", station)
            dataset.setncattr("variable", variable)


def _plot(
    project: ProjectConfig,
    analysis: HisConfig,
    run: _Run,
    *,
    selected: Sequence[SimulationConfig],
    reference: SimulationConfig,
    series: list[_Series],
    zone: timezone,
    targets: dict[str, tuple[OutputLayout, OutputWriteGuard]],
) -> None:
    style = project.effective_figure(analysis)
    hooks = project.effective_hooks(analysis)
    replot = project.effective_replot(analysis)
    reference_selected = any(s.label == reference.label for s in selected)
    if "difference_simulation" in analysis.comparisons and not reference_selected:
        run.messages.append(
            "HIS differences skipped: the reference simulation is not selected."
        )
    catalogue = _measurement_catalogue(project, analysis)
    if "all_stations" in analysis.views:
        _plot_all_stations(
            project,
            analysis,
            run,
            series=series,
            zone=zone,
            targets=targets,
            catalogue=catalogue,
            style=style,
            hooks=hooks,
            replot=replot,
        )
    if "convergence" in analysis.views:
        if reference_selected:
            _plot_convergence(
                project,
                analysis,
                run,
                series=series,
                reference=reference,
                zone=zone,
                targets=targets,
                style=style,
                hooks=hooks,
                replot=replot,
            )
        else:
            run.messages.append(
                "HIS convergence skipped: the reference simulation is not selected."
            )
    if "x_t" in analysis.views:
        _plot_x_t(
            project,
            analysis,
            run,
            series=series,
            zone=zone,
            targets=targets,
            style=style,
            hooks=hooks,
            replot=replot,
        )
    if "line" not in analysis.views:
        return
    groups: dict[tuple[str, str, int], list[_Series]] = {}
    for item in series:
        groups.setdefault((item.variable, item.station_name, item.layer), []).append(
            item
        )
    for (variable, station_name, layer), members in groups.items():
        folder = quote(station_name, safe="-_.")
        layer_folders = _layer_folders(members[0])
        filtered = _filtered_members(analysis, members, run)
        if "value" in analysis.comparisons:
            _plot_values(
                project,
                analysis,
                run,
                selected=selected,
                members=members,
                filtered=filtered,
                variable=variable,
                station_name=station_name,
                layer=layer,
                folders=[folder, *layer_folders],
                catalogue=catalogue,
                style=style,
                hooks=hooks,
                replot=replot,
                zone=zone,
                targets=targets,
            )
        if "difference_simulation" in analysis.comparisons and reference_selected:
            _plot_differences(
                project,
                analysis,
                run,
                members=[item for item in filtered if item is not None],
                reference=reference,
                variable=variable,
                station_name=station_name,
                layer=layer,
                folders=[folder, *layer_folders],
                style=style,
                hooks=hooks,
                replot=replot,
                zone=zone,
                targets=targets,
            )


def _layer_folders(item: _Series) -> list[str]:
    return [f"layer-{item.layer}"] if item.layered else []


def _measurement_catalogue(
    project: ProjectConfig,
    analysis: HisConfig,
) -> HisMeasurementCatalogue | None:
    config = analysis.measurements
    if config is None:
        return None
    zone = config.time_zone or project.time_zone
    try:
        return read_his_measurements(
            config.catalogue,
            project.paths,
            time_zone=None if zone is None else str(zone),
        )
    except HisError as error:
        raise HisStageError(str(error)) from error


def _filtered_members(
    analysis: HisConfig,
    members: list[_Series],
    run: _Run,
) -> list[_Series | None]:
    """Return each member filtered, or None when the filter leaves nothing.

    Without a filter the members are returned unchanged.
    """
    if analysis.filter == "none":
        return list(members)
    filtered: list[_Series | None] = []
    for member in members:
        try:
            times, values = godin_filter(member.time_seconds, member.values)
        except ValueError as error:
            raise HisStageError(
                f"HIS filter failed for {member.variable}/{member.station_name} of "
                f"{member.simulation.label!r}: {error}"
            ) from error
        if times.size == 0:
            run.messages.append(
                f"HIS filter left no values for {member.variable}/"
                f"{member.station_name} of {member.simulation.label!r}: the series "
                "is shorter than the filter window."
            )
            filtered.append(None)
            continue
        filtered.append(replace(member, time_seconds=times, values=values))
    return filtered


def _palette() -> list[str]:
    return [
        to_hex(colour) for colour in mpl.rcParams["axes.prop_cycle"].by_key()["color"]
    ]


def _palette_colours(count: int) -> list[str]:
    palette = _palette()
    return [palette[index % len(palette)] for index in range(count)]


def _simulation_colour(project: ProjectConfig, label: str) -> str:
    # The colour follows the simulation's position in the project, so it is the same
    # in every figure.
    index = next(
        i
        for i, simulation in enumerate(project.simulations)
        if simulation.label == label
    )
    return _palette_colours(index + 1)[index]


def _value_lines(
    analysis: HisConfig,
    members: list[_Series],
    filtered: list[_Series | None],
    labels: list[str],
    colours: list[str],
) -> list[_Line]:
    """Raw and filtered lines per member, with the same colour for both."""
    lines: list[_Line] = []
    for member, filtered_member, label, colour in zip(
        members, filtered, labels, colours, strict=True
    ):
        if analysis.filter != "none":
            lines.append(
                _Line(
                    f"{label} (raw)",
                    member.time_seconds,
                    member.values,
                    colour,
                    _RAW_LINEWIDTH,
                )
            )
        if filtered_member is not None:
            lines.append(
                _Line(
                    label,
                    filtered_member.time_seconds,
                    filtered_member.values,
                    colour,
                    _LINEWIDTH,
                )
            )
    return lines


def _measured_line(measurement: HisMeasurement, label: str) -> _Line:
    return _Line(
        f"{label} (measured)",
        measurement.time_seconds,
        measurement.values,
        _MEASURED_COLOUR,
        _LINEWIDTH,
    )


def _comparison_folder(
    comparison: str,
    analysis: HisConfig,
    *,
    measured: bool,
) -> str:
    # The filter and the measured overlay change the figure, so they get their own
    # folder. A figure with replot: never would otherwise reuse the plain one.
    parts = [comparison]
    if analysis.filter != "none":
        parts.append(analysis.filter)
    if measured:
        parts.append("measured")
    return "_".join(parts)


def _plot_values(
    project: ProjectConfig,
    analysis: HisConfig,
    run: _Run,
    *,
    selected: Sequence[SimulationConfig],
    members: list[_Series],
    filtered: list[_Series | None],
    variable: str,
    station_name: str,
    layer: int,
    folders: list[str],
    catalogue: HisMeasurementCatalogue | None,
    style: FigureConfig,
    hooks: list[str],
    replot: str,
    zone: timezone,
    targets: dict[str, tuple[OutputLayout, OutputWriteGuard]],
) -> None:
    labels = [
        analysis.legend_labels.get(member.simulation.label, member.simulation.label)
        for member in members
    ]
    colours = [
        _simulation_colour(project, member.simulation.label) for member in members
    ]
    lines = _value_lines(analysis, members, filtered, labels, colours)
    measurement = (
        None if catalogue is None else catalogue.find(station_name, variable, layer)
    )
    if measurement is not None:
        lines.append(_measured_line(measurement, station_name))
    if not lines:
        return
    inputs = [member.fingerprint for member in members]
    if measurement is not None and catalogue is not None:
        inputs.append(catalogue.fingerprint)
    fingerprint = _figure_fingerprint(
        analysis,
        comparison="value",
        variable=variable,
        stations=[station_name],
        layer=layer,
        style=style,
        zone=zone,
        inputs=inputs,
    )
    folder = _comparison_folder("value", analysis, measured=measurement is not None)
    title = f"{station_name}: {variable}"
    y_label = _y_label(project, variable, "value")
    # The same overlay goes into every selected simulation's folder.
    for simulation in selected:
        layout, guard = targets[simulation.label]
        path = figure_file_path(
            layout,
            prefix=("his",),
            variable=variable,
            folders=[*folders, folder],
            time=None,
            zone=zone,
        )
        _write_figure(
            project,
            run,
            path=path,
            lines=lines,
            title=title,
            y_label=y_label,
            zone=zone,
            comparison="value",
            variable=variable,
            simulation_label=simulation.label,
            style=style,
            hooks=hooks,
            guard=guard,
            replot=replot,
            fingerprint=fingerprint,
        )


def _plot_all_stations(
    project: ProjectConfig,
    analysis: HisConfig,
    run: _Run,
    *,
    series: list[_Series],
    zone: timezone,
    targets: dict[str, tuple[OutputLayout, OutputWriteGuard]],
    catalogue: HisMeasurementCatalogue | None,
    style: FigureConfig,
    hooks: list[str],
    replot: str,
) -> None:
    # One line per station for each simulation, variable and layer, in the
    # simulation's own folder. Stations keep their configured order.
    groups: dict[tuple[str, str, int], list[_Series]] = {}
    for item in series:
        key = (item.simulation.label, item.variable, item.layer)
        groups.setdefault(key, []).append(item)
    for (simulation_label, variable, layer), members in groups.items():
        layer_folders = _layer_folders(members[0])
        filtered = _filtered_members(analysis, members, run)
        labels = [member.station_name for member in members]
        colours = _palette_colours(len(members))
        lines = _value_lines(analysis, members, filtered, labels, colours)
        measured = False
        if catalogue is not None:
            for member in members:
                measurement = catalogue.find(member.station_name, variable, layer)
                if measurement is not None:
                    lines.append(_measured_line(measurement, member.station_name))
                    measured = True
        if not lines:
            continue
        inputs = [member.fingerprint for member in members]
        if measured and catalogue is not None:
            inputs.append(catalogue.fingerprint)
        fingerprint = _figure_fingerprint(
            analysis,
            comparison="value",
            variable=variable,
            stations=labels,
            layer=layer,
            style=style,
            zone=zone,
            inputs=inputs,
        )
        layout, guard = targets[simulation_label]
        path = figure_file_path(
            layout,
            prefix=("his",),
            variable=variable,
            folders=[
                "all_stations",
                *layer_folders,
                _comparison_folder("value", analysis, measured=measured),
            ],
            time=None,
            zone=zone,
        )
        _write_figure(
            project,
            run,
            path=path,
            lines=lines,
            title=f"{simulation_label}: {variable}",
            y_label=_y_label(project, variable, "value"),
            zone=zone,
            comparison="value",
            variable=variable,
            simulation_label=simulation_label,
            style=style,
            hooks=hooks,
            guard=guard,
            replot=replot,
            fingerprint=fingerprint,
        )


def _plot_differences(
    project: ProjectConfig,
    analysis: HisConfig,
    run: _Run,
    *,
    members: list[_Series],
    reference: SimulationConfig,
    variable: str,
    station_name: str,
    layer: int,
    folders: list[str],
    style: FigureConfig,
    hooks: list[str],
    replot: str,
    zone: timezone,
    targets: dict[str, tuple[OutputLayout, OutputWriteGuard]],
) -> None:
    reference_series = next(
        (member for member in members if member.simulation.label == reference.label),
        None,
    )
    if reference_series is None:
        run.messages.append(
            f"HIS differences skipped for {variable}/{station_name}: the reference "
            "simulation has no series for this station."
        )
        return
    tolerance_s = _tolerance_seconds(analysis)
    label_of = analysis.legend_labels.get
    differences: list[_Line] = []
    used = [reference_series]
    for member in members:
        if member is reference_series:
            continue
        matched_values, matched = _interpolate_to_times(
            reference_series.time_seconds,
            member.time_seconds,
            member.values,
            tolerance_s,
        )
        if matched == 0:
            run.messages.append(
                f"HIS difference {member.simulation.label} minus {reference.label} "
                f"skipped for {variable}/{station_name}: no reference time inside "
                "the compared series or within tolerance."
            )
            continue
        run.messages.append(
            f"HIS difference {member.simulation.label} minus {reference.label} for "
            f"{variable}/{station_name}: matched {matched} of "
            f"{reference_series.time_seconds.size} reference times."
        )
        differences.append(
            _Line(
                label_of(member.simulation.label, member.simulation.label),
                reference_series.time_seconds,
                matched_values - reference_series.values,
            )
        )
        used.append(member)
    if not differences:
        return
    if analysis.include_reference_in_differences:
        differences.append(
            _Line(
                label_of(reference.label, reference.label),
                reference_series.time_seconds,
                np.zeros_like(reference_series.values),
            )
        )
    fingerprint = _figure_fingerprint(
        analysis,
        comparison="difference_simulation",
        variable=variable,
        stations=[station_name],
        layer=layer,
        style=style,
        zone=zone,
        inputs=[item.fingerprint for item in used],
    )
    layout, guard = targets[reference.label]
    path = figure_file_path(
        layout,
        prefix=("his",),
        variable=variable,
        folders=[
            *folders,
            _comparison_folder("difference_simulation", analysis, measured=False),
        ],
        time=None,
        zone=zone,
    )
    _write_figure(
        project,
        run,
        path=path,
        lines=differences,
        title=f"{station_name}: {variable} difference",
        y_label=_y_label(project, variable, "difference_simulation"),
        zone=zone,
        comparison="difference_simulation",
        variable=variable,
        simulation_label=reference.label,
        style=style,
        hooks=hooks,
        guard=guard,
        replot=replot,
        fingerprint=fingerprint,
    )


def _interpolate_to_times(
    target_seconds: np.ndarray,
    source_seconds: np.ndarray,
    source_values: np.ndarray,
    tolerance_s: float | None,
) -> tuple[np.ndarray, int]:
    """Linearly interpolate the source series onto the target times.

    Non-finite source samples are dropped first, so they are bridged by
    interpolation. Targets outside the span of the valid samples are NaN. With a
    tolerance, a target farther than it from the nearest valid sample is NaN too.
    """
    valid = np.isfinite(source_values)
    times = source_seconds[valid]
    values = source_values[valid]
    if times.size == 0:
        return np.full(target_seconds.shape, np.nan), 0
    order = np.argsort(times, kind="stable")
    times = times[order]
    values = values[order]
    usable = (target_seconds >= times[0]) & (target_seconds <= times[-1])
    if tolerance_s is not None:
        right = np.clip(np.searchsorted(times, target_seconds), 0, times.size - 1)
        left = np.clip(right - 1, 0, times.size - 1)
        nearest_gap = np.minimum(
            np.abs(target_seconds - times[left]), np.abs(times[right] - target_seconds)
        )
        usable &= nearest_gap <= tolerance_s
    interpolated = np.interp(target_seconds, times, values)
    matched_values = np.where(usable, interpolated, np.nan)
    return matched_values, int(np.count_nonzero(usable))


def _tolerance_seconds(analysis: HisConfig) -> float | None:
    tolerance = analysis.times.tolerance
    if tolerance is None:
        return None
    return tolerance.total_seconds()


def _figure_fingerprint(
    analysis: HisConfig,
    *,
    comparison: str,
    variable: str,
    stations: Sequence[str],
    layer: int,
    style: FigureConfig,
    zone: timezone,
    inputs: list[str],
    options: Mapping[str, object] | None = None,
) -> str:
    parameters: dict[str, object] = {
        "comparison": comparison,
        "variable": variable,
        "stations": list(stations),
        "layer": layer,
        "filter": analysis.filter,
        "zone": offset_text(zone),
        "legend_labels": dict(analysis.legend_labels),
        "include_reference_in_differences": (analysis.include_reference_in_differences),
        "figure": style.model_dump(mode="json"),
    }
    # Only the new views add options, so existing figure fingerprints do not change.
    if options is not None:
        parameters["options"] = dict(options)
    return fingerprint_artifact(
        kind="his_figure",
        schema_version=CACHE_SCHEMA_VERSION,
        tool_version=__version__,
        algorithm_version=_ALGORITHM_VERSION,
        parameters=parameters,
        inputs=inputs,
    )


def _y_label(project: ProjectConfig, variable: str, comparison: str) -> str:
    try:
        label_key = get_variable_spec(variable).label_key
    except UnknownVariableError:
        return variable
    return axis_label(
        label_key,
        project.language,
        datum=project.labels.vertical_datum,
        extra_dictionaries=project.labels.extra_dictionaries,
        kind=comparison_label_kind(comparison),
    )


def _write_figure(
    project: ProjectConfig,
    run: _Run,
    *,
    path: Path,
    lines: list[_Line],
    title: str,
    y_label: str,
    zone: timezone,
    comparison: str,
    variable: str,
    simulation_label: str,
    style: FigureConfig,
    hooks: list[str],
    guard: OutputWriteGuard,
    replot: str,
    fingerprint: str,
) -> None:
    if _reuse_existing(run, path, replot):
        return
    view = _line_figure(lines, title=title, y_label=y_label, zone=zone, style=style)
    first_time = min(float(np.min(line.time_seconds)) for line in lines)
    _finalize(
        project,
        run,
        figure=view.figure,
        axes=view.axes,
        path=path,
        view_name="line",
        comparison=comparison,
        variable=variable,
        simulation_label=simulation_label,
        time_seconds=first_time,
        style=style,
        hooks=hooks,
        guard=guard,
        fingerprint=fingerprint,
    )


def _reuse_existing(run: _Run, path: Path, replot: str) -> bool:
    if replot == "never" and path.is_file():
        run.reused.append(path)
        return True
    return False


def _finalize(
    project: ProjectConfig,
    run: _Run,
    *,
    figure: Figure,
    axes: Axes,
    path: Path,
    view_name: str,
    comparison: str,
    variable: str,
    simulation_label: str,
    time_seconds: float,
    style: FigureConfig,
    hooks: list[str],
    guard: OutputWriteGuard,
    fingerprint: str,
) -> None:
    xmin, xmax = axes.get_xlim()
    ymin, ymax = axes.get_ylim()
    context = FigureContext(
        analysis="his",
        view=view_name,
        comparison=comparison,
        variable=variable,
        simulation=simulation_label,
        time=datetime.fromtimestamp(time_seconds, tz=UTC),
        extent=(float(xmin), float(xmax), float(ymin), float(ymax)),
        axes={"model": axes},
        data={},
        labels={"variable": variable},
        language=project.language,
        output_path=path,
    )
    finalize_figure(
        figure,
        context,
        style,
        hooks,
        get_figure_writer("png"),
        metadata={"figure_fingerprint": fingerprint},
        guard=guard,
    )
    run.outputs.append(path)


def _plot_convergence(
    project: ProjectConfig,
    analysis: HisConfig,
    run: _Run,
    *,
    series: list[_Series],
    reference: SimulationConfig,
    zone: timezone,
    targets: dict[str, tuple[OutputLayout, OutputWriteGuard]],
    style: FigureConfig,
    hooks: list[str],
    replot: str,
) -> None:
    # One figure per variable and layer in the reference simulation's folder. Each
    # simulation is one line of standard deviations per station, markers only.
    window_s = analysis.convergence_time.total_seconds()
    layout, guard = targets[reference.label]
    groups: dict[tuple[str, int], list[_Series]] = {}
    for item in series:
        groups.setdefault((item.variable, item.layer), []).append(item)
    for (variable, layer), members in groups.items():
        names: list[str] = []
        by_simulation: dict[str, dict[str, _Series]] = {}
        for member in members:
            if member.station_name not in names:
                names.append(member.station_name)
            by_simulation.setdefault(member.simulation.label, {})[
                member.station_name
            ] = member
        path = figure_file_path(
            layout,
            prefix=("his",),
            variable=variable,
            folders=["convergence", *_layer_folders(members[0])],
            time=None,
            zone=zone,
        )
        if _reuse_existing(run, path, replot):
            continue
        labels = tuple(by_simulation)
        rows = np.asarray(
            [
                [
                    _window_std(by_simulation[label][name], window_s)
                    if name in by_simulation[label]
                    else np.nan
                    for name in names
                ]
                for label in labels
            ],
            dtype=np.float64,
        )
        view = LineView.create(
            np.arange(1, len(names) + 1, dtype=np.float64),
            rows,
            labels,
            display="lines",
            figure_config=style,
            x_label="Station",
            y_label=f"Standard deviation: {_y_label(project, variable, 'value')}",
            title=f"Convergence: {variable}",
        )
        drawn: list[Line2D] = [
            artist for artist in view.artists if isinstance(artist, Line2D)
        ]
        for artist in drawn:
            artist.set_linestyle("none")
            artist.set_marker("o")
        view.axes.set_xticks(np.arange(1, len(names) + 1, dtype=np.float64))
        view.axes.set_xticklabels(names, rotation=45, ha="right")
        view.axes.legend()
        view.figure.tight_layout()
        fingerprint = _figure_fingerprint(
            analysis,
            comparison="value",
            variable=variable,
            stations=names,
            layer=layer,
            style=style,
            zone=zone,
            inputs=[member.fingerprint for member in members],
            options={"view": "convergence", "convergence_time_s": window_s},
        )
        _finalize(
            project,
            run,
            figure=view.figure,
            axes=view.axes,
            path=path,
            view_name="convergence",
            comparison="value",
            variable=variable,
            simulation_label=reference.label,
            time_seconds=min(float(np.min(m.time_seconds)) for m in members),
            style=style,
            hooks=hooks,
            guard=guard,
            fingerprint=fingerprint,
        )


def _window_std(item: _Series, window_s: float) -> float:
    # Standard deviation of the raw finite values in the window before the final
    # sample. NaN when the window holds fewer than two values.
    times = item.time_seconds
    if times.size == 0:
        return float("nan")
    start = float(np.max(times)) - window_s
    window = item.values[(times >= start) & np.isfinite(item.values)]
    if window.size < 2:
        return float("nan")
    return float(np.std(window, ddof=1))


def _plot_x_t(
    project: ProjectConfig,
    analysis: HisConfig,
    run: _Run,
    *,
    series: list[_Series],
    zone: timezone,
    targets: dict[str, tuple[OutputLayout, OutputWriteGuard]],
    style: FigureConfig,
    hooks: list[str],
    replot: str,
) -> None:
    # One river-km by time figure per simulation, variable and layer, in the
    # simulation's own folder. Stations are placed at their nearest river-km point.
    if analysis.river_km_file is None:
        raise HisStageError("HIS x_t needs river_km_file.")
    points = _river_km_points(analysis.river_km_file)
    river_fingerprint = fingerprint_model_output(
        analysis.river_km_file, hash_content=True
    )
    km_of = _station_river_km(series, points, run)
    groups: dict[tuple[str, str, int], list[_Series]] = {}
    for item in series:
        key = (item.simulation.label, item.variable, item.layer)
        groups.setdefault(key, []).append(item)
    for (simulation_label, variable, layer), members in groups.items():
        ordered = sorted(
            members, key=lambda m: km_of[(simulation_label, m.station_name)]
        )
        times = ordered[0].time_seconds
        skipped = (
            f"HIS x_t skipped for {variable} layer {layer} of {simulation_label!r}"
        )
        if len(ordered) < 2 or times.size < 2:
            run.messages.append(
                f"{skipped}: it needs at least two stations and two times."
            )
            continue
        if any(not np.array_equal(m.time_seconds, times) for m in ordered):
            run.messages.append(f"{skipped}: the stations have different times.")
            continue
        layout, guard = targets[simulation_label]
        path = figure_file_path(
            layout,
            prefix=("his",),
            variable=variable,
            folders=["x_t", *_layer_folders(ordered[0])],
            time=None,
            zone=zone,
        )
        if _reuse_existing(run, path, replot):
            continue
        try:
            km_edges, _ = centres_to_edges(
                [km_of[(simulation_label, m.station_name)] for m in ordered]
            )
            time_edges, _ = centres_to_edges(_display_dates(times, zone))
        except RiverKmGeometryError as error:
            raise HisStageError(
                f"HIS x_t needs distinct, increasing river km and times: {error}"
            ) from error
        values = np.column_stack([m.values for m in ordered])
        view = SpaceTimeView.create(
            km_edges,
            time_edges,
            values,
            figure_config=style,
            colormap=default_colormap("value"),
            colorbar_label=_y_label(project, variable, "value"),
            x_label="River km",
            y_label="Time",
            title=f"{simulation_label}: {variable}",
        )
        locator = mdates.AutoDateLocator()  # type: ignore[no-untyped-call]
        view.axes.yaxis.set_major_locator(locator)
        view.axes.yaxis.set_major_formatter(
            mdates.ConciseDateFormatter(locator)  # type: ignore[no-untyped-call]
        )
        view.figure.tight_layout()
        fingerprint = _figure_fingerprint(
            analysis,
            comparison="value",
            variable=variable,
            stations=[m.station_name for m in ordered],
            layer=layer,
            style=style,
            zone=zone,
            inputs=[*(m.fingerprint for m in ordered), river_fingerprint],
            options={"view": "x_t"},
        )
        _finalize(
            project,
            run,
            figure=view.figure,
            axes=view.axes,
            path=path,
            view_name="x_t",
            comparison="value",
            variable=variable,
            simulation_label=simulation_label,
            time_seconds=float(times[0]),
            style=style,
            hooks=hooks,
            guard=guard,
            fingerprint=fingerprint,
        )


def _river_km_points(path: Path) -> tuple[RiverKmPoint, ...]:
    try:
        points = read_river_km_points(path)
    except RiverKmFileError as error:
        raise HisStageError(str(error)) from error
    if not points:
        raise HisStageError(f"River-km file {path} contains no points.")
    return points


def _station_river_km(
    series: list[_Series],
    points: tuple[RiverKmPoint, ...],
    run: _Run,
) -> dict[tuple[str, str], float]:
    # Each station is placed at the river km of its nearest point. The distance is
    # logged because it bounds the accuracy of the placement.
    km_of: dict[tuple[str, str], float] = {}
    xy = np.asarray([(point.x, point.y) for point in points], dtype=np.float64)
    for item in series:
        key = (item.simulation.label, item.station_name)
        if key in km_of:
            continue
        if item.position is None:
            raise HisStageError(f"No position for HIS station {item.station_name!r}.")
        distances = np.hypot(xy[:, 0] - item.position[0], xy[:, 1] - item.position[1])
        nearest = int(np.argmin(distances))
        km_of[key] = points[nearest].km
        run.messages.append(
            f"HIS x_t: {item.station_name!r} of {item.simulation.label!r} placed at "
            f"river km {points[nearest].km:g}, {float(distances[nearest]):.4g} model "
            "units from its nearest river-km point."
        )
    return km_of


def _line_figure(
    lines: list[_Line],
    *,
    title: str,
    y_label: str,
    zone: timezone,
    style: FigureConfig,
) -> LineView:
    # Each line has its own time axis, so the first line sets the template and the
    # others are added as overlays. Styling is applied once all lines exist, because
    # the legend copies the artist properties when it is created.
    first, *others = lines
    view = LineView.create(
        _display_dates(first.time_seconds, zone),
        first.values[np.newaxis, :],
        (first.label,),
        display="lines",
        figure_config=style,
        x_label="Time",
        y_label=y_label,
        title=title,
    )
    locator = mdates.AutoDateLocator()  # type: ignore[no-untyped-call]
    view.axes.xaxis.set_major_locator(locator)
    view.axes.xaxis.set_major_formatter(
        mdates.ConciseDateFormatter(locator)  # type: ignore[no-untyped-call]
    )
    drawn: list[Line2D] = [
        artist for artist in view.artists if isinstance(artist, Line2D)
    ]
    for line in others:
        drawn.append(
            view.add_overlay(
                _display_dates(line.time_seconds, zone),
                line.values,
                line.label,
            )
        )
    for line, artist in zip(lines, drawn, strict=True):
        if line.color is not None:
            artist.set_color(line.color)
        artist.set_linewidth(line.linewidth)
    view.axes.legend()
    return view


def _display_dates(time_seconds: np.ndarray, zone: timezone) -> np.ndarray:
    # Matplotlib converts aware datetimes to UTC, so pass the naive wall-clock time
    # in the display zone to keep the axis in that zone.
    wall_clock = [
        datetime.fromtimestamp(float(seconds), tz=zone).replace(tzinfo=None)
        for seconds in time_seconds
    ]
    dates = mdates.date2num(wall_clock)  # type: ignore[no-untyped-call]
    return np.asarray(dates, dtype=np.float64)
