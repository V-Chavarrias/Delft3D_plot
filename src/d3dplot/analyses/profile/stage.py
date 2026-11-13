# SPDX-License-Identifier: GPL-3.0-or-later
"""PRF stage: sample quantities along polylines and render profile views."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from itertools import groupby
from pathlib import Path
from typing import Any
from urllib.parse import quote

import numpy as np
from matplotlib import dates as mdates
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from pyproj import CRS
from pyproj.exceptions import CRSError

from d3dplot.analyses.profile.config import ProfileConfig, ProfileLineConfig
from d3dplot.analyses.profile.process import (
    ProfileLine,
    ProfileProcessError,
    ProfileSelection,
    ProfileTimeReader,
    prepare_profile_line,
    profile_interfaces_at_time,
    profile_values_at_time,
    select_polyline,
)
from d3dplot.config.project import ProjectConfig, SimulationConfig
from d3dplot.config.selections import (
    DepthAverageSelection,
    ElevationSelection,
    LayersSelection,
)
from d3dplot.config.selections import VerticalSelection as VerticalConfig
from d3dplot.execution.cache import CacheFingerprintReadError, read_cache_fingerprints
from d3dplot.paths import resolve_path_preserving_drive
from d3dplot.plotting.finalize import FigureContext, close_figure, finalize_figure
from d3dplot.plotting.time import format_time_label
from d3dplot.plotting.views.line import LineView
from d3dplot.plotting.views.section import SectionView
from d3dplot.plotting.views.space_time import SpaceTimeView
from d3dplot.plotting.writers import get_figure_writer
from d3dplot.simulation.discover import Simulation
from d3dplot.simulation.inspection import InspectionResult
from d3dplot.simulation.map_reader import MapReader, MapReaderError
from d3dplot.simulation.subrun import SubRun
from d3dplot.simulation.time_axis import TimeEntry
from d3dplot.store import (
    CACHE_SCHEMA_VERSION,
    OutputLayout,
    OutputWriteGuard,
    fingerprint_artifact,
    fingerprint_small_input,
)
from d3dplot.store.paths import OutputArea, build_quantity_output_path
from d3dplot.variables.context import QuantityContext
from d3dplot.variables.engine import QuantityEngine, QuantityEngineError
from d3dplot.variables.geometry import GridGeometry
from d3dplot.variables.line_crossings_cache import (
    LineInputError,
    Polyline,
    read_line_topology,
    read_pli_polylines,
)
from d3dplot.variables.line_sampling import LineTopology
from d3dplot.variables.registry import UnknownVariableError, get_variable_spec

DiagnosticSink = Callable[[str], None]

_PROFILE_INPUT_ALGORITHM_VERSION = "1"


class ProfileStageError(ValueError):
    """A PRF analysis cannot be processed or rendered for one simulation."""


@dataclass(frozen=True, slots=True)
class ProfileStageResult:
    """Figures written and reused by the PRF stage for one simulation."""

    output_paths: tuple[Path, ...] = ()
    reused_paths: tuple[Path, ...] = ()


def profile_selections(analysis: ProfileConfig) -> tuple[ProfileSelection, ...]:
    """Expand configured variables, vertical and fractions into sampling selections.

    An explicit fraction list without sum_fractions gives one selection per
    fraction, each summed over that single fraction, so every series is one-dimensional.
    """
    vertical = _vertical_fields(analysis.vertical)
    selections: list[ProfileSelection] = []
    for name in analysis.variables:
        try:
            spec = get_variable_spec(name)
        except UnknownVariableError as error:
            raise ProfileStageError(str(error)) from error
        if not isinstance(analysis.fractions, str) and "nSedTot" not in spec.extra_dims:
            raise ProfileStageError(
                f"PRF variable {name!r} has no sediment fraction dimension; remove "
                "its fractions."
            )
        if isinstance(analysis.fractions, str):
            if "nSedTot" in spec.extra_dims and not analysis.sum_fractions:
                raise ProfileStageError(
                    f"PRF variable {name!r} has sediment fractions; give explicit "
                    "fractions or set sum_fractions: true."
                )
            selections.append(
                ProfileSelection(
                    variable=name,
                    fractions=None,
                    sum_fractions=analysis.sum_fractions,
                    **vertical,
                )
            )
        elif analysis.sum_fractions:
            selections.append(
                ProfileSelection(
                    variable=name,
                    fractions=tuple(analysis.fractions),
                    sum_fractions=True,
                    **vertical,
                )
            )
        else:
            selections.extend(
                ProfileSelection(
                    variable=name,
                    fractions=(index,),
                    sum_fractions=True,
                    **vertical,
                )
                for index in analysis.fractions
            )
    return tuple(selections)


def _vertical_fields(vertical: VerticalConfig) -> dict[str, Any]:
    """Map a configured vertical selection to ProfileSelection fields."""
    if isinstance(vertical, str):
        return {"vertical": vertical}
    if isinstance(vertical, LayersSelection):
        return {"vertical": "all", "layers": tuple(vertical.layers)}
    if isinstance(vertical, DepthAverageSelection):
        return {
            "vertical": "depth_average",
            "z_range": vertical.depth_average.z_range,
        }
    if isinstance(vertical, ElevationSelection):
        return {"vertical": "elevation", "elevation": float(vertical.elevation)}
    raise ProfileStageError("Unsupported PRF vertical selection.")


def process_profile_analysis(
    project: ProjectConfig,
    simulation_config: SimulationConfig,
    simulation: Simulation,
    inspection: InspectionResult,
    analysis: ProfileConfig,
    analysis_index: int,
    entries: Sequence[TimeEntry],
    layout: OutputLayout,
    guard: OutputWriteGuard,
    *,
    render_figures: bool,
    diagnostic: DiagnosticSink | None = None,
) -> ProfileStageResult:
    """Build PRF profile caches for the selected times and optionally render figures.

    Entries are visited in time order, and each sub-run's output is opened once.
    """
    selections = profile_selections(analysis)
    run = _ProfileRun(
        project,
        simulation_config,
        analysis,
        analysis_index,
        selections,
        layout,
        guard,
        render_figures=render_figures,
        diagnostic=diagnostic,
    )
    try:
        run.process(simulation, inspection, entries)
    except (
        CacheFingerprintReadError,
        CRSError,
        LineInputError,
        MapReaderError,
        ProfileProcessError,
        QuantityEngineError,
    ) as error:
        raise ProfileStageError(str(error)) from error
    finally:
        run.close()
    return run.result()


class _ProfileRun:
    """State of one PRF run for one simulation: lines, fingerprints and figures."""

    def __init__(
        self,
        project: ProjectConfig,
        simulation_config: SimulationConfig,
        analysis: ProfileConfig,
        analysis_index: int,
        selections: tuple[ProfileSelection, ...],
        layout: OutputLayout,
        guard: OutputWriteGuard,
        *,
        render_figures: bool,
        diagnostic: DiagnosticSink | None,
    ) -> None:
        self._project = project
        self._simulation_label = simulation_config.label
        self._analysis = analysis
        self._analysis_index = analysis_index
        self._selections = selections
        self._layout = layout
        self._guard = guard
        self._render = render_figures
        self._diagnostic = diagnostic
        self._style = project.effective_figure(analysis)
        self._hooks = project.effective_hooks(analysis)
        self._replot = project.effective_replot(analysis)
        self._is_geographic = _is_geographic(project.crs)
        self._polylines: dict[Path, tuple[Polyline, ...]] = {}
        self._topology: dict[int, LineTopology] = {}
        self._geometry: dict[int, GridGeometry] = {}
        self._lines: dict[tuple[int, str], ProfileLine] = {}
        self._input_fingerprints: dict[int, str] = {}
        self._line_views: dict[tuple[str, int], LineView] = {}
        self._section_views: dict[tuple[str, int], SectionView] = {}
        self._space_time_values: dict[tuple[str, int], dict[datetime, np.ndarray]] = {}
        self._space_time_lines: dict[tuple[str, int], ProfileLine] = {}
        self._outputs: list[Path] = []
        self._reused: list[Path] = []

    def result(self) -> ProfileStageResult:
        return ProfileStageResult(tuple(self._outputs), tuple(self._reused))

    def close(self) -> None:
        for line_view in self._line_views.values():
            close_figure(line_view.figure)
        for section_view in self._section_views.values():
            close_figure(section_view.figure)
        self._line_views.clear()
        self._section_views.clear()

    def process(
        self,
        simulation: Simulation,
        inspection: InspectionResult,
        entries: Sequence[TimeEntry],
    ) -> None:
        reader = MapReader(
            simulation,
            inspection.merge_plan_path,
            on_corrupt_output=self._project.on_corrupt_output,
        )
        engine = QuantityEngine()
        subruns = {subrun.index: subrun for subrun in simulation.subruns}
        ordered = sorted(entries, key=lambda entry: (entry.subrun, entry.time))
        for subrun_index, group in groupby(ordered, key=lambda entry: entry.subrun):
            group_entries = tuple(group)
            plan_index = reader.plan_index(subrun_index)
            subrun = subruns[subrun_index]
            grid_path = self._grid_path(inspection, plan_index)
            input_fingerprint = self._input_fingerprint(
                inspection, subrun, plan_index, grid_path
            )
            lines = [
                self._line(inspection, plan_index, line_config)
                for line_config in self._analysis.lines
            ]
            base_context = QuantityContext.from_model_input(
                subrun.model_input,
                grid_geometry=self._grid_geometry(plan_index, grid_path),
            )
            with reader.session(subrun_index) as map_session:
                for entry in group_entries:
                    time_reader = ProfileTimeReader(
                        engine,
                        map_session,
                        entry,
                        replace(base_context, current_time=entry.time),
                    )
                    for line in lines:
                        self._process_line_time(
                            time_reader, line, input_fingerprint, entry
                        )
        if self._render:
            self._finish_space_time()

    def _process_line_time(
        self,
        time_reader: ProfileTimeReader,
        line: ProfileLine,
        input_fingerprint: str,
        entry: TimeEntry,
    ) -> None:
        values_by_selection = [
            profile_values_at_time(
                reader=time_reader,
                line=line,
                selection=selection,
                layout=self._layout,
                guard=self._guard,
                input_fingerprint=input_fingerprint,
                diagnostic=self._diagnostic,
            )
            for selection in self._selections
        ]
        interfaces: np.ndarray | None = None
        if "section" in self._analysis.views:
            interfaces = profile_interfaces_at_time(
                reader=time_reader,
                line=line,
                layout=self._layout,
                guard=self._guard,
                input_fingerprint=input_fingerprint,
                diagnostic=self._diagnostic,
            )
        if not self._render:
            return
        for index, (selection, values) in enumerate(
            zip(self._selections, values_by_selection, strict=True)
        ):
            if "profile" in self._analysis.views:
                self._render_profile(
                    entry, line, index, selection, values, input_fingerprint
                )
            if "section" in self._analysis.views and interfaces is not None:
                self._render_section(
                    entry, line, index, selection, values, interfaces, input_fingerprint
                )
            if "space_time" in self._analysis.views:
                key = (line.name, index)
                known = self._space_time_lines.setdefault(key, line)
                if known is not line and known.n_crossings != line.n_crossings:
                    raise ProfileStageError(
                        f"PRF line {line.name!r} has different crossings in different "
                        "merge plans; space-time views need one plan per line."
                    )
                self._space_time_values.setdefault(key, {})[entry.time] = (
                    _one_dimensional(values, line, selection)
                )

    def _render_profile(
        self,
        entry: TimeEntry,
        line: ProfileLine,
        index: int,
        selection: ProfileSelection,
        values: np.ndarray,
        input_fingerprint: str,
    ) -> None:
        path = self._figure_path(line, index, selection, "profile", entry.time)
        if self._reuse(path):
            return
        series, labels = _series(values, selection)
        title = f"{line.name} - {selection.variable} - {self._time_label(entry)}"
        key = (line.name, index)
        view = self._line_views.get(key)
        if view is None:
            view = LineView.create(
                line.distance_m,
                series,
                labels,
                display="lines",
                figure_config=self._style,
                x_label="Distance along line (m)",
                y_label=selection.variable,
                title=title,
            )
            self._line_views[key] = view
        else:
            view.update(line.distance_m, series, title=title)
        self._finish(
            view.figure,
            view.axes,
            path,
            selection.variable,
            "profile",
            entry.time,
            input_fingerprint,
            close=False,
        )

    def _render_section(
        self,
        entry: TimeEntry,
        line: ProfileLine,
        index: int,
        selection: ProfileSelection,
        values: np.ndarray,
        interfaces: np.ndarray,
        input_fingerprint: str,
    ) -> None:
        path = self._figure_path(line, index, selection, "section", entry.time)
        if self._reuse(path):
            return
        if values.ndim != 2:
            raise ProfileStageError(
                f"PRF section view needs layer-resolved values; {selection.variable!r} "
                "gave one value per crossing."
            )
        title = f"{line.name} - {selection.variable} - {self._time_label(entry)}"
        key = (line.name, index)
        view = self._section_views.get(key)
        if view is None:
            crossings = line.plan.crossings
            view = SectionView.create(
                crossings.distance_start_m,
                crossings.distance_stop_m,
                interfaces,
                values,
                colormap="viridis",
                colorbar_label=selection.variable,
                x_label="Distance along line (m)",
                y_label="Elevation (m)",
                title=title,
            )
            self._section_views[key] = view
        else:
            view.update(interfaces, values, title=title, limits=None)
        self._finish(
            view.figure,
            view.axes,
            path,
            selection.variable,
            "section",
            entry.time,
            input_fingerprint,
            close=False,
        )

    def _finish_space_time(self) -> None:
        for (line_name, index), by_time in self._space_time_values.items():
            line = self._space_time_lines[(line_name, index)]
            selection = self._selections[index]
            path = self._figure_path(line, index, selection, "space_time", None)
            if self._reuse(path):
                continue
            times = sorted(by_time)
            if len(times) < 2:
                raise ProfileStageError(
                    "PRF space-time views need at least two times for every line."
                )
            data = np.vstack([by_time[time] for time in times])
            time_centres = np.asarray(
                [mdates.date2num(time) for time in times],  # type: ignore[no-untyped-call]
                dtype=np.float64,
            )
            view = SpaceTimeView.create(
                _centre_edges(line.distance_m),
                _centre_edges(time_centres),
                data,
                figure_config=self._style,
                colormap="viridis",
                colorbar_label=selection.variable,
                x_label="Distance along line (m)",
                y_label="Time",
                title=f"{line.name} - {selection.variable}",
            )
            locator = mdates.AutoDateLocator()  # type: ignore[no-untyped-call]
            view.axes.yaxis.set_major_locator(locator)
            view.axes.yaxis.set_major_formatter(
                mdates.ConciseDateFormatter(locator)  # type: ignore[no-untyped-call]
            )
            self._finish(
                view.figure,
                view.axes,
                path,
                selection.variable,
                "space_time",
                times[-1],
                self._combined_input_fingerprint(),
                close=True,
            )

    def _finish(
        self,
        figure: Figure,
        axes: Axes,
        path: Path,
        variable: str,
        view_name: str,
        time: datetime,
        input_fingerprint: str,
        *,
        close: bool,
    ) -> None:
        context = FigureContext(
            analysis="profile",
            view=view_name,
            comparison="value",
            variable=variable,
            simulation=self._simulation_label,
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
            language=self._project.language,
            output_path=path,
        )
        finalize_figure(
            figure,
            context,
            self._style,
            self._hooks,
            get_figure_writer("png"),
            metadata={"input_fingerprint": input_fingerprint},
            guard=self._guard,
            close=close,
        )
        self._outputs.append(path)

    def _reuse(self, path: Path) -> bool:
        if path.exists() and self._replot == "never":
            self._reused.append(path)
            return True
        return False

    def _time_label(self, entry: TimeEntry) -> str:
        return format_time_label(entry.time, self._style.time_format)

    def _figure_path(
        self,
        line: ProfileLine,
        index: int,
        selection: ProfileSelection,
        view_name: str,
        time: datetime | None,
    ) -> Path:
        filename = "all_times.png" if time is None else f"{time:%Y%m%dT%H%M%S}.png"
        return build_quantity_output_path(
            self._layout,
            OutputArea.FIGURES,
            get_variable_spec(selection.variable).name,
            prefix=(f"analysis-{self._analysis_index}-profile", self._simulation_label),
            suffix=(
                line.name,
                quote(view_name, safe="-_."),
                selection.label(),
            ),
            filename=filename,
        )

    def _combined_input_fingerprint(self) -> str:
        return ";".join(sorted(set(self._input_fingerprints.values())))

    def _grid_path(self, inspection: InspectionResult, plan_index: int) -> Path:
        if plan_index >= len(inspection.grid_paths):
            raise ProfileStageError(
                f"No inspected grid exists for merge plan {plan_index}; run "
                "inspection again."
            )
        return Path(inspection.grid_paths[plan_index])

    def _grid_geometry(self, plan_index: int, grid_path: Path) -> GridGeometry:
        geometry = self._geometry.get(plan_index)
        if geometry is None:
            geometry = GridGeometry.from_path(grid_path)
            self._geometry[plan_index] = geometry
        return geometry

    def _line(
        self,
        inspection: InspectionResult,
        plan_index: int,
        config: ProfileLineConfig,
    ) -> ProfileLine:
        key = (plan_index, config.name)
        line = self._lines.get(key)
        if line is not None:
            return line
        topology = self._topology.get(plan_index)
        if topology is None:
            topology = read_line_topology(
                self._grid_path(inspection, plan_index),
                is_geographic=self._is_geographic,
            )
            self._topology[plan_index] = topology
        polylines = self._polylines.get(config.file)
        if polylines is None:
            polylines = read_pli_polylines(config.file)
            self._polylines[config.file] = polylines
        polyline = select_polyline(
            polylines,
            line_name=config.name,
            polyline_name=config.polyline,
        )
        line = prepare_profile_line(
            config.name,
            polyline.xy,
            topology,
            self._layout,
            self._guard,
            diagnostic=self._diagnostic,
        )
        self._lines[key] = line
        return line

    def _input_fingerprint(
        self,
        inspection: InspectionResult,
        subrun: SubRun,
        plan_index: int,
        grid_path: Path,
    ) -> str:
        cached = self._input_fingerprints.get(subrun.index)
        if cached is not None:
            return cached
        inputs: list[str] = []
        inputs.extend(
            fingerprint_small_input(path)
            for path in (
                subrun.mdu.mdu_path,
                subrun.mdu.mor_file,
                subrun.mdu.sed_file,
            )
            if path is not None
        )
        cache_paths = (inspection.merge_plan_path, grid_path, inspection.times_path)
        cache_fingerprints = read_cache_fingerprints(cache_paths)
        for path in cache_paths:
            value = cache_fingerprints[resolve_path_preserving_drive(path)]
            if value is None:
                raise ProfileStageError(
                    f"Inspected cache input {path} has no fingerprint; run inspection "
                    "again."
                )
            inputs.append(value)
        fingerprint = fingerprint_artifact(
            kind="profile_inputs",
            schema_version=CACHE_SCHEMA_VERSION,
            tool_version="d3dplot",
            algorithm_version=_PROFILE_INPUT_ALGORITHM_VERSION,
            parameters={"plan_index": plan_index},
            inputs=inputs,
        )
        self._input_fingerprints[subrun.index] = fingerprint
        return fingerprint


def _is_geographic(crs: str | None) -> bool:
    if crs is None:
        return False
    return bool(CRS.from_user_input(crs).is_geographic)


def _series(
    values: np.ndarray,
    selection: ProfileSelection,
) -> tuple[np.ndarray, tuple[str, ...]]:
    """Return (series, points) data and one label per series."""
    if values.ndim == 1:
        return values[np.newaxis, :], (selection.variable,)
    columns = values.reshape(values.shape[0], -1).T
    labels = tuple(
        f"{selection.variable} layer {k + 1}" for k in range(columns.shape[0])
    )
    return columns, labels


def _one_dimensional(
    values: np.ndarray,
    line: ProfileLine,
    selection: ProfileSelection,
) -> np.ndarray:
    if values.ndim != 1:
        raise ProfileStageError(
            f"PRF space-time view needs one value per crossing; {selection.variable!r} "
            f"on line {line.name!r} is layer-resolved. Use top, bottom, "
            "depth_average or elevation."
        )
    return values


def _centre_edges(values: np.ndarray) -> np.ndarray:
    centres = np.asarray(values, dtype=np.float64)
    if centres.ndim != 1 or centres.size < 2 or np.any(np.diff(centres) <= 0):
        raise ProfileStageError("PRF centre coordinates must be strictly increasing.")
    inner = (centres[:-1] + centres[1:]) / 2.0
    return np.concatenate(
        (
            [centres[0] - (inner[0] - centres[0])],
            inner,
            [centres[-1] + (centres[-1] - inner[-1])],
        )
    )


__all__ = [
    "ProfileStageError",
    "ProfileStageResult",
    "process_profile_analysis",
    "profile_selections",
]
