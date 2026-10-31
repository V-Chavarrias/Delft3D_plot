# SPDX-License-Identifier: GPL-3.0-or-later
"""Plot map2d values from the persistent per-time data cache."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import blake2b
from importlib import import_module
from pathlib import Path
from typing import Any
from urllib.parse import quote

import netCDF4
from PIL import Image, UnidentifiedImageError

from d3dplot import __version__
from d3dplot.analyses.map2d.config import Map2DConfig
from d3dplot.config.analysis import Comparison
from d3dplot.config.figure import DaysSinceColorLimits
from d3dplot.config.project import ProjectConfig
from d3dplot.execution.cache import (
    CacheFingerprintReadError,
    decide_cache,
    read_cache_fingerprints,
)
from d3dplot.labels import label
from d3dplot.measurements.catalogue import (
    MapMeasurementCatalogue,
    MapMeasurementError,
    inspect_map_catalogue,
)
from d3dplot.measurements.map2d import (
    PreparedMapMeasurement,
    measurement_range,
    prepare_map_measurement,
    scale_map_measurement,
)
from d3dplot.plotting.finalize import (
    FigureContext,
    FigureHookError,
    FigureSnapshot,
    HookSpec,
    close_figure,
    finalize_figure,
)
from d3dplot.plotting.movies import MovieWriterError, write_mp4
from d3dplot.plotting.time import format_time_label
from d3dplot.plotting.views.decorations import MapDecorations, MapDecorationsError
from d3dplot.plotting.views.extents import (
    ExtentSet,
    ExtentSetError,
    UgridDataset,
    open_ugrid,
)
from d3dplot.plotting.views.map import MapView, MapViewError
from d3dplot.plotting.writers import get_figure_writer
from d3dplot.simulation.partitions import _NETCDF4_READ_LOCK
from d3dplot.store import (
    CACHE_SCHEMA_VERSION,
    OutputLayout,
    OutputWriteGuard,
    atomic_path,
    canonical_json,
    fingerprint_artifact,
    fingerprint_small_input,
)
from d3dplot.variables.registry import get_variable_spec

_Array = Any
np: Any = import_module("numpy")
xr: Any = import_module("xarray")
xu: Any = import_module("xugrid")
_FloatArray = Any
_Comparison = Comparison
_DIFFERENCE_COMPARISONS = frozenset(
    {
        "difference_simulation",
        "difference_simulation_time",
        "difference_simulation_relative",
    }
)


class Map2DPlotError(RuntimeError):
    """Map2d cache data cannot be plotted safely."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Map2DPlotFrame:
    """One processed map cache file and its stable matching key."""

    match_key: str
    time: datetime
    path: Path
    morph_time: datetime | None = None

    def __post_init__(self) -> None:
        if not self.match_key or self.match_key != self.match_key.strip():
            raise ValueError("Map2DPlotFrame.match_key must be non-empty and trimmed.")
        if self.time.tzinfo is None or self.time.utcoffset() is None:
            raise ValueError("Map2DPlotFrame.time must be time-zone-aware.")
        if self.morph_time is not None and (
            self.morph_time.tzinfo is None or self.morph_time.utcoffset() is None
        ):
            raise ValueError("Map2DPlotFrame.morph_time must be time-zone-aware.")


@dataclass(frozen=True, slots=True)
class Map2DPlotSource:
    """A simulation's map caches and inspected grid."""

    label: str
    layout: OutputLayout
    guard: OutputWriteGuard
    grid_path: Path
    frames: tuple[Map2DPlotFrame, ...]
    model_time_zone: str | None = None

    def __post_init__(self) -> None:
        if not self.label or self.label != self.label.strip():
            raise ValueError("Map2DPlotSource.label must be non-empty and trimmed.")
        if self.layout.root != self.guard.root:
            raise ValueError("Map2DPlotSource layout and guard must share a root.")
        if not self.frames:
            raise ValueError("Map2DPlotSource needs at least one processed frame.")
        keys = [frame.match_key for frame in self.frames]
        if len(set(keys)) != len(keys):
            raise ValueError("Map2DPlotSource frame match keys must be unique.")


@dataclass(frozen=True, slots=True)
class Map2DPlotResult:
    """Figures and movies written or reused by one map2d plot group."""

    output_paths: tuple[Path, ...]
    reused_paths: tuple[Path, ...]
    movie_paths: tuple[Path, ...]
    reused_movie_paths: tuple[Path, ...]
    messages: tuple[str, ...]


DiagnosticSink = Callable[[str], None]


def plot_map2d_group(
    project: ProjectConfig,
    analysis: Map2DConfig,
    source: Map2DPlotSource,
    variable: str,
    comparison: Comparison,
    *,
    reference: Map2DPlotSource | None = None,
    diagnostic: DiagnosticSink | None = None,
    fresh_figures: bool = False,
    analysis_index: int = 0,
    provenance_path: Path | None = None,
    only_output_path: Path | None = None,
) -> Map2DPlotResult:
    """Plot one variable/comparison group without reopening model output."""
    if isinstance(analysis_index, bool) or analysis_index < 0:
        raise ValueError("analysis_index cannot be negative.")
    target_output_path = (
        only_output_path.resolve(strict=False) if only_output_path is not None else None
    )
    if provenance_path is not None and not provenance_path.is_file():
        raise FileNotFoundError(
            f"Resolved configuration does not exist: {provenance_path}"
        )

    spec = get_variable_spec(variable)
    if spec.location.value != "face":
        raise Map2DPlotError(
            "V-PLOT-003",
            f"map2d cannot plot {spec.location.value}-located variable {variable!r}.",
        )
    uses_reference = comparison in _DIFFERENCE_COMPARISONS
    if uses_reference and reference is None:
        raise Map2DPlotError(
            "V-PLOT-003",
            f"Comparison {comparison!r} requires a reference simulation.",
        )
    if not uses_reference and reference is not None:
        raise ValueError("A reference source is only valid for simulation comparisons.")

    target_source = reference if uses_reference else source
    if target_source is None:
        raise AssertionError("Simulation comparisons require a reference source.")
    figure_config = project.effective_figure(analysis)
    hooks = project.effective_hooks(analysis)
    hook_fingerprints = _hook_fingerprints(hooks)
    output_subject = (
        f"{source.label}_vs_{target_source.label}" if uses_reference else source.label
    )
    output_layout = target_source.layout
    output_guard = target_source.guard
    measurement_settings = next(
        (
            settings
            for key, settings in analysis.measurements.items()
            if key.casefold() == variable.casefold()
        ),
        None,
    )
    show_measurements = measurement_settings is not None and comparison in {
        "value",
        "difference_time",
    }
    messages: list[str] = []
    written: list[Path] = []
    reused: list[Path] = []
    movies: list[Path] = []
    reused_movies: list[Path] = []
    movie_frames: dict[tuple[int, int], list[tuple[datetime, Path]]] = {}

    def report(message: str) -> None:
        messages.append(message)
        if diagnostic is not None:
            diagnostic(message)

    current_dataset: UgridDataset | None = None
    reference_dataset: UgridDataset | None = None
    view: MapView | None = None
    try:
        if measurement_settings is not None and not show_measurements:
            report(
                f"Measurements for {variable!r} are omitted for simulation "
                f"comparison {comparison!r}."
            )
        current_dataset = open_ugrid(source.grid_path)
        reference_dataset = (
            current_dataset
            if target_source is source
            else open_ugrid(target_source.grid_path)
        )
        current_grid = current_dataset.grid
        target_grid = reference_dataset.grid
        model_crs = _model_crs(target_grid, project.crs)
        current_grid_fingerprint = _grid_fingerprint(source.grid_path)
        target_grid_fingerprint = _grid_fingerprint(target_source.grid_path)
        extent_set = ExtentSet.create(
            target_grid,
            target_source.grid_path,
            output_layout,
            output_guard,
            analysis.map,
        )
        decorations = MapDecorations.prepare(
            project,
            analysis.map,
            figure_config,
            getattr(target_grid, "crs", None),
            output_layout,
            output_guard,
            diagnostic=report,
        )
        decorations.prepare_backgrounds(
            tuple(
                window.bounds for window in extent_set.windows if window.faces.size > 0
            )
        )
        regridder, regrid_fingerprint = _regridder_for(
            current_grid,
            target_grid,
            current_grid_fingerprint,
            target_grid_fingerprint,
            output_layout,
            output_guard,
        )
        reference_frames = (
            _match_reference_frames(source.frames, reference.frames)
            if uses_reference and reference is not None
            else {}
        )
        color_limits = _color_limit_sets(analysis, variable, comparison)
        style_hash = _config_fingerprint(project, analysis)
        measurement_catalogue: MapMeasurementCatalogue | None = None
        if show_measurements and measurement_settings is not None:
            time_zone = (
                measurement_settings.time_zone
                or source.model_time_zone
                or project.time_zone
            )
            measurement_catalogue = inspect_map_catalogue(
                measurement_settings.catalogue,
                project.paths,
                output_layout,
                output_guard,
                time_zone=time_zone,
                diagnostic=report,
            )
        measurement_baselines: dict[int, PreparedMapMeasurement | None] = {}
        if (
            show_measurements
            and comparison == "difference_time"
            and measurement_catalogue is not None
            and measurement_settings is not None
        ):
            for window in extent_set.windows:
                if window.faces.size == 0:
                    continue
                measurement_baselines[window.index] = prepare_map_measurement(
                    measurement_catalogue,
                    measurement_settings,
                    source.frames[0].time,
                    window.bounds,
                    model_crs,
                    output_layout,
                    output_guard,
                    diagnostic=report,
                )

        first_current: _FloatArray | None = None
        first_current_fingerprint: str | None = None
        first_reference: _FloatArray | None = None
        first_reference_fingerprint: str | None = None
        for frame_index, frame in enumerate(source.frames):
            current_values, current_fingerprint = _read_frame(frame, variable)
            if current_values.size != current_grid.n_face:
                raise Map2DPlotError(
                    "V-PLOT-002",
                    f"Map2d cache has {current_values.size} values for "
                    f"{current_grid.n_face} grid faces: {frame.path}.",
                )
            if first_current is None:
                first_current = current_values
                first_current_fingerprint = current_fingerprint
            paired_reference: Map2DPlotFrame | None = None
            reference_values: _FloatArray | None = None
            reference_fingerprint: str | None = None
            if uses_reference:
                paired_reference = reference_frames[frame.match_key]
                reference_values, reference_fingerprint = _read_frame(
                    paired_reference,
                    variable,
                )
                if reference_values.size != target_grid.n_face:
                    raise Map2DPlotError(
                        "V-PLOT-002",
                        f"Reference cache has {reference_values.size} values for "
                        f"{target_grid.n_face} grid faces: {paired_reference.path}.",
                    )
                if first_reference is None:
                    first_reference = reference_values
                    first_reference_fingerprint = reference_fingerprint

            map_values = _comparison_values(
                comparison,
                current_values,
                first_current,
                reference_values,
                first_reference,
                regridder,
                current_grid,
            )
            if comparison in {"difference_time", "difference_simulation_time"}:
                if frame_index == 0 and not analysis.include_initial_time_difference:
                    report(f"Initial zero-difference frame omitted for {variable!r}.")
                    continue
            output_time = (
                paired_reference.time if paired_reference is not None else frame.time
            )
            display_frame = paired_reference if paired_reference is not None else frame
            display_time = (
                display_frame.morph_time
                if analysis.times.axis == "morphological"
                and display_frame.morph_time is not None
                else output_time
            )
            scale = figure_config.display_scale
            if scale != 1.0:
                map_values = map_values * scale
            time_text = format_time_label(display_time, figure_config.time_format)
            label_kind = (
                "percentage"
                if comparison == "difference_simulation_relative"
                else ("difference" if comparison != "value" else "value")
            )
            quantity_label = label(
                variable,
                project.language,
                "quantity",
                datum=project.labels.vertical_datum,
                extra_dictionaries=project.labels.extra_dictionaries,
            )
            value_label = label(
                variable,
                project.language,
                label_kind,
                datum=project.labels.vertical_datum,
                extra_dictionaries=project.labels.extra_dictionaries,
            )
            unit_label = value_label.unit
            title = time_text
            colorbar_label = value_label.text
            if scale != 1.0:
                unit_label = f"{unit_label} × {scale:g}"
                colorbar_label = (
                    f"{value_label.quantity} [{value_label.unit} × {scale:g}]"
                )
            x_axis_label = label(
                "x_coord",
                project.language,
                "value",
                extra_dictionaries=project.labels.extra_dictionaries,
            ).text
            y_axis_label = label(
                "y_coord",
                project.language,
                "value",
                extra_dictionaries=project.labels.extra_dictionaries,
            ).text

            for window in extent_set.windows:
                if window.faces.size == 0:
                    report(
                        f"V-PLOT-001: extent {window.name!r} contains no visible "
                        "faces; it was skipped."
                    )
                    continue
                measurement: PreparedMapMeasurement | None = None
                if (
                    show_measurements
                    and measurement_catalogue is not None
                    and measurement_settings is not None
                ):
                    if comparison == "difference_time":
                        baseline = measurement_baselines.get(window.index)
                        if baseline is None:
                            report(
                                f"V-MEA-004: measurement difference omitted at "
                                f"{output_time.isoformat()} because no baseline "
                                "measurement was selected."
                            )
                        else:
                            measurement = prepare_map_measurement(
                                measurement_catalogue,
                                measurement_settings,
                                output_time,
                                window.bounds,
                                model_crs,
                                output_layout,
                                output_guard,
                                comparison="difference_time",
                                baseline=baseline,
                                diagnostic=report,
                            )
                    else:
                        measurement = prepare_map_measurement(
                            measurement_catalogue,
                            measurement_settings,
                            output_time,
                            window.bounds,
                            model_crs,
                            output_layout,
                            output_guard,
                            diagnostic=report,
                        )
                scaled_measurement = scale_map_measurement(measurement, scale)
                measurement_limits = measurement_range(measurement)
                for color_index, configured_limits in enumerate(color_limits):
                    limits = _limits_for_time(
                        configured_limits,
                        analysis.map.color_limits_mode,
                        output_time,
                    )
                    if (
                        measurement_settings is not None
                        and measurement_settings.color_limits_from_measurements
                        and measurement_limits is not None
                    ):
                        if limits is None:
                            limits = measurement_limits
                        else:
                            lower, upper = limits
                            limits = (
                                measurement_limits[0] if lower is None else lower,
                                measurement_limits[1] if upper is None else upper,
                            )
                    limits = _scaled_limits(limits, scale)
                    output_path = _figure_path(
                        output_layout,
                        project.series,
                        output_subject,
                        variable,
                        comparison,
                        output_time,
                        color_index,
                        window.index,
                    )
                    if (
                        target_output_path is not None
                        and output_path.resolve(strict=False) != target_output_path
                    ):
                        continue
                    if analysis.movie is not None and target_output_path is None:
                        movie_frames.setdefault((window.index, color_index), []).append(
                            (output_time, output_path)
                        )
                    data_fingerprints = [current_fingerprint]
                    if reference_fingerprint is not None:
                        data_fingerprints.append(reference_fingerprint)
                    if (
                        comparison in {"difference_time", "difference_simulation_time"}
                        and first_current_fingerprint is not None
                    ):
                        data_fingerprints.append(first_current_fingerprint)
                    if (
                        comparison == "difference_simulation_time"
                        and first_reference_fingerprint is not None
                    ):
                        data_fingerprints.append(first_reference_fingerprint)
                    if regrid_fingerprint is not None:
                        data_fingerprints.append(regrid_fingerprint)
                    if measurement is not None:
                        data_fingerprints.append(measurement.fingerprint)
                    fingerprint = fingerprint_artifact(
                        kind="figure",
                        schema_version=CACHE_SCHEMA_VERSION,
                        tool_version=__version__,
                        algorithm_version="map2d-plot-2",
                        parameters={
                            "analysis": analysis.model_dump(mode="json"),
                            "comparison": comparison,
                            "extent": list(window.bounds),
                            "extent_fingerprint": window.fingerprint,
                            "figure": figure_config.model_dump(mode="json"),
                            "hooks": hook_fingerprints,
                            "map": analysis.map.model_dump(mode="json"),
                            "subject": output_subject,
                            "variable": variable,
                            "color_limits": limits,
                            "measurement_fingerprint": (
                                measurement.fingerprint
                                if measurement is not None
                                else None
                            ),
                        },
                        inputs=(
                            style_hash,
                            *data_fingerprints,
                            target_grid_fingerprint,
                            decorations.fingerprint_for(window.bounds),
                        ),
                        time=output_time.isoformat(),
                    )
                    policy = (
                        "always"
                        if fresh_figures
                        else project.effective_replot(analysis)
                    )
                    decision = decide_cache(
                        exists=output_path.is_file(),
                        existing_fingerprint=_png_fingerprint(output_path),
                        expected_fingerprint=fingerprint,
                        policy=policy,
                    )
                    if decision.action == "reuse":
                        reused.append(output_path)
                        report(f"Reused figure {output_path}.")
                        if decision.warning is not None:
                            report(f"WARNING: {decision.warning}")
                        continue

                    if (
                        view is None
                        or view.window.fingerprint != window.fingerprint
                        or (hooks and figure_config.hooks_mode == "rebuild")
                    ):
                        if view is not None:
                            close_figure(view.figure)
                        view = MapView.create(
                            target_grid,
                            window,
                            map_values,
                            map_config=analysis.map,
                            figure_config=figure_config,
                            colormap=_colormap_for(analysis, variable, comparison),
                            colorbar_label=colorbar_label,
                            x_label=x_axis_label,
                            y_label=y_axis_label,
                            initial_limits=limits,
                            decorations=decorations,
                            measurement_panel=show_measurements,
                        )
                    view.update(map_values, limits=limits)
                    view.axes.set_title(title)
                    measurement_label = label(
                        "measurement",
                        project.language,
                        "quantity",
                    )
                    measurement_title = measurement_label.text
                    if scaled_measurement is not None:
                        measurement_title = (
                            f"{measurement_label.text} — "
                            f"{scaled_measurement.mean_time.isoformat()}"
                        )
                    panel_limits = limits
                    if panel_limits is None:
                        panel_clim = view.mappable.get_clim()
                        panel_limits = (float(panel_clim[0]), float(panel_clim[1]))
                    view.update_measurements(
                        scaled_measurement,
                        title=measurement_title,
                        limits=panel_limits,
                        edge_color=(
                            measurement_settings.edge_color
                            if measurement_settings is not None
                            else "black"
                        ),
                        aspect=analysis.map.aspect,
                    )
                    axes_context: dict[str, Any] = {
                        "model": view.axes,
                        "colorbar": view.colorbar.ax,
                    }
                    if view.measurement_axes is not None:
                        axes_context["measurements"] = view.measurement_axes
                    context = FigureContext(
                        analysis="map2d",
                        view="map",
                        comparison=comparison,
                        variable=variable,
                        simulation=output_subject,
                        time=output_time,
                        extent=window.bounds,
                        axes=axes_context,
                        data={
                            "values": map_values,
                            "face_indices": window.faces,
                            "river_km": decorations.visible_river_km(window.bounds),
                            "time": output_time,
                            "measurements": scaled_measurement,
                        },
                        labels={
                            "quantity": quantity_label.quantity,
                            "unit": unit_label,
                            "title": title,
                        },
                        language=project.language,
                        output_path=output_path,
                    )
                    metadata = {
                        "config_hash": style_hash,
                        "figure_fingerprint": fingerprint,
                        "task_key": (
                            f"plot|{output_subject}|{variable}|{comparison}|"
                            f"{time_text}|extent={window.index}|color={color_index}"
                        ),
                        "d3dplot_replot_key": canonical_json(
                            {
                                "analysis_index": analysis_index,
                                "simulation_label": source.label,
                                "variable": variable,
                                "comparison": comparison,
                                "time": output_time.isoformat(),
                                "extent_index": window.index,
                                "color_index": color_index,
                            }
                        ),
                    }
                    if provenance_path is not None:
                        metadata["d3dplot_provenance"] = str(
                            provenance_path.resolve(strict=False)
                        )
                    snapshot = (
                        FigureSnapshot.capture(view.figure)
                        if hooks and figure_config.hooks_mode == "per_image"
                        else None
                    )
                    try:
                        finalize_figure(
                            view.figure,
                            context,
                            figure_config,
                            hooks,
                            get_figure_writer("png"),
                            metadata=metadata,
                            guard=output_guard,
                            close=False,
                        )
                    except FigureHookError as error:
                        raise Map2DPlotError("V-HOOK-001", str(error)) from error
                    finally:
                        if snapshot is not None:
                            snapshot.restore()
                    written.append(output_path)
                    report(f"Saved figure {output_path}.")
        if view is not None:
            close_figure(view.figure)
            view = None
        if analysis.movie is not None and target_output_path is None:
            for (extent_index, color_index), ordered_frames in sorted(
                movie_frames.items()
            ):
                frame_paths = tuple(
                    path for _, path in sorted(ordered_frames, key=lambda item: item[0])
                )
                frame_fingerprints = tuple(
                    _png_fingerprint(path) for path in frame_paths
                )
                if any(value is None for value in frame_fingerprints):
                    raise Map2DPlotError(
                        "V-PLOT-002",
                        "A movie frame is missing a valid figure fingerprint.",
                    )
                fingerprint = fingerprint_artifact(
                    kind="map2d_movie",
                    schema_version=CACHE_SCHEMA_VERSION,
                    tool_version=__version__,
                    algorithm_version="map2d-movie-1",
                    parameters={
                        "comparison": comparison,
                        "duration_s": analysis.movie.duration_s,
                        "extent_index": extent_index,
                        "subject": output_subject,
                        "variable": variable,
                        "color_index": color_index,
                    },
                    inputs=tuple(
                        value for value in frame_fingerprints if value is not None
                    ),
                )
                movie_path = _movie_path(
                    output_layout,
                    project.series,
                    output_subject,
                    variable,
                    comparison,
                    color_index,
                    extent_index,
                )
                try:
                    was_reused = write_mp4(
                        frame_paths,
                        movie_path,
                        analysis.movie.duration_s,
                        fingerprint,
                        output_guard,
                    )
                except MovieWriterError as error:
                    raise Map2DPlotError(error.code, str(error)) from error
                if was_reused:
                    reused_movies.append(movie_path)
                    report(f"Reused movie {movie_path}.")
                else:
                    movies.append(movie_path)
                    report(f"Saved movie {movie_path}.")
    except MapDecorationsError as error:
        raise Map2DPlotError(error.code, str(error)) from error
    except ExtentSetError as error:
        raise Map2DPlotError(error.code, str(error)) from error
    except MapMeasurementError as error:
        raise Map2DPlotError(error.code, str(error)) from error
    except MovieWriterError as error:
        raise Map2DPlotError(error.code, str(error)) from error
    except (CacheFingerprintReadError, MapViewError) as error:
        raise Map2DPlotError("V-PLOT-002", str(error)) from error
    finally:
        if view is not None:
            close_figure(view.figure)
        if current_dataset is not None:
            current_dataset.close()
        if reference_dataset is not None and reference_dataset is not current_dataset:
            reference_dataset.close()
    return Map2DPlotResult(
        tuple(written),
        tuple(reused),
        tuple(movies),
        tuple(reused_movies),
        tuple(messages),
    )


def _read_frame(frame: Map2DPlotFrame, variable: str) -> tuple[_FloatArray, str]:
    try:
        with _NETCDF4_READ_LOCK:
            with netCDF4.Dataset(frame.path, mode="r") as dataset:
                if "values" not in dataset.variables:
                    raise Map2DPlotError(
                        "V-PLOT-002",
                        f"Map2d cache has no 'values' variable: {frame.path}.",
                    )
                values = np.asarray(dataset.variables["values"][:], dtype=np.float64)
                fingerprint = getattr(dataset, "d3dplot_fingerprint", None)
                cache_variable = getattr(dataset, "variable", None)
    except Map2DPlotError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise Map2DPlotError(
            "V-PLOT-002",
            f"Could not read map2d cache {frame.path}: {error}",
        ) from error
    if values.ndim != 1:
        raise Map2DPlotError(
            "V-PLOT-002",
            f"Map2d cache values must be one-dimensional: {frame.path}.",
        )
    if not isinstance(fingerprint, str) or not fingerprint:
        raise Map2DPlotError(
            "V-PLOT-002",
            f"Map2d cache has no valid fingerprint: {frame.path}.",
        )
    if (
        not isinstance(cache_variable, str)
        or cache_variable.casefold() != variable.casefold()
    ):
        raise Map2DPlotError(
            "V-PLOT-002",
            f"Map2d cache variable {cache_variable!r} does not match {variable!r}.",
        )
    return values, fingerprint


def _match_reference_frames(
    current: Sequence[Map2DPlotFrame],
    reference: Sequence[Map2DPlotFrame],
) -> Mapping[str, Map2DPlotFrame]:
    reference_by_key = {frame.match_key: frame for frame in reference}
    missing = [
        frame.match_key for frame in current if frame.match_key not in reference_by_key
    ]
    if missing:
        raise Map2DPlotError(
            "V-PLOT-003",
            "Reference simulation is missing matched map times: " + ", ".join(missing),
        )
    return reference_by_key


def _comparison_values(
    comparison: _Comparison,
    current: _FloatArray,
    first_current: _FloatArray,
    reference: _FloatArray | None,
    first_reference: _FloatArray | None,
    regridder: xu.CentroidLocatorRegridder | None,
    source_grid: xu.Ugrid2d,
) -> _FloatArray:
    if comparison == "value":
        return current.copy()
    if comparison == "difference_time":
        if first_current is None:
            raise Map2DPlotError(
                "V-PLOT-003",
                "difference_time requires a first current frame.",
            )
        return current - first_current
    if reference is None:
        raise Map2DPlotError(
            "V-PLOT-003",
            f"Comparison {comparison!r} requires reference values.",
        )
    current_on_reference = _regrid_values(current, source_grid, regridder)
    if current_on_reference.shape != reference.shape:
        raise Map2DPlotError(
            "V-PLOT-002",
            "Current and reference map caches have incompatible face counts.",
        )
    if comparison == "difference_simulation":
        return current_on_reference - reference
    if comparison == "difference_simulation_relative":
        result = np.full(reference.shape, np.nan, dtype=np.float64)
        valid = np.isfinite(reference) & (reference != 0.0)
        np.divide(
            current_on_reference - reference,
            reference,
            out=result,
            where=valid,
        )
        result[valid] *= 100.0
        return result
    if first_reference is None:
        raise Map2DPlotError(
            "V-PLOT-003",
            "difference_simulation_time requires a first reference frame.",
        )
    if first_current is None or first_reference.shape != reference.shape:
        raise Map2DPlotError(
            "V-PLOT-002",
            "Initial current and reference map caches have incompatible face counts.",
        )
    initial_current = _regrid_values(first_current, source_grid, regridder)
    return (current_on_reference - initial_current) - (reference - first_reference)


def _regrid_values(
    values: _FloatArray,
    source_grid: xu.Ugrid2d,
    regridder: xu.CentroidLocatorRegridder | None,
) -> _FloatArray:
    if regridder is None:
        return values.copy()
    source = xr.DataArray(values, dims=(source_grid.face_dimension,))
    result = regridder.regrid_dataarray(source, (source_grid.face_dimension,))
    return np.asarray(result.values, dtype=np.float64).reshape(-1)


def _regridder_for(
    source_grid: xu.Ugrid2d,
    target_grid: xu.Ugrid2d,
    source_fingerprint: str,
    target_fingerprint: str,
    layout: OutputLayout,
    guard: OutputWriteGuard,
) -> tuple[xu.CentroidLocatorRegridder | None, str | None]:
    if source_fingerprint == target_fingerprint:
        if source_grid.n_face != target_grid.n_face:
            raise Map2DPlotError(
                "V-PLOT-003",
                "Equal grid fingerprints have different face counts.",
            )
        return None, None
    fingerprint = fingerprint_artifact(
        kind="centroid_regridder",
        schema_version=CACHE_SCHEMA_VERSION,
        tool_version=__version__,
        algorithm_version="1",
        parameters={"method": "centroid_locator"},
        inputs=(source_fingerprint, target_fingerprint),
    )
    path = layout.cache_geometry / f"regrid_{fingerprint[:24]}.nc"
    if path.is_file():
        with xr.open_dataset(path) as stored:
            if stored.attrs.get("d3dplot_fingerprint") == fingerprint:
                return xu.CentroidLocatorRegridder.from_dataset(
                    stored.load()
                ), fingerprint
    regridder = xu.CentroidLocatorRegridder(source_grid, target_grid)
    dataset = regridder.to_dataset()
    dataset.attrs["d3dplot_fingerprint"] = fingerprint
    try:
        with atomic_path(path, guard) as temporary:
            dataset.to_netcdf(temporary)
    finally:
        dataset.close()
    return regridder, fingerprint


def _grid_fingerprint(path: Path) -> str:
    resolved = path.resolve(strict=False)
    fingerprint = read_cache_fingerprints((resolved,))[resolved]
    if fingerprint is None:
        raise Map2DPlotError(
            "V-PLOT-002",
            f"Inspected grid cache has no fingerprint: {path}.",
        )
    return fingerprint


def _color_limit_sets(
    analysis: Map2DConfig,
    variable: str,
    comparison: _Comparison,
) -> tuple[tuple[float | None, float | None] | None, ...]:
    if analysis.map.color_limits is None:
        return (None,)
    variable_limits = next(
        (
            value
            for key, value in analysis.map.color_limits.items()
            if key.casefold() == variable.casefold()
        ),
        None,
    )
    if variable_limits is None:
        return (None,)
    comparison_limits = next(
        (
            value
            for key, value in variable_limits.items()
            if key.casefold() == comparison.casefold()
        ),
        None,
    )
    if comparison_limits is None:
        return (None,)
    if not comparison_limits:
        raise Map2DPlotError(
            "V-PLOT-003",
            f"Color-limit configuration for {variable!r}/{comparison!r} is empty.",
        )
    return tuple(
        None if limits is None else (limits[0], limits[1])
        for limits in comparison_limits
    )


def _limits_for_time(
    configured_limits: tuple[float | None, float | None] | None,
    mode: str | DaysSinceColorLimits,
    output_time: datetime,
) -> tuple[float | None, float | None] | None:
    if not isinstance(mode, DaysSinceColorLimits):
        return configured_limits
    reference_time = datetime.combine(
        mode.days_since,
        datetime.min.time(),
        tzinfo=UTC,
    )
    elapsed_days = max(
        (output_time.astimezone(UTC) - reference_time).total_seconds() / 86_400.0,
        0.0,
    )
    return 0.0, elapsed_days


def _scaled_limits(
    limits: tuple[float | None, float | None] | None,
    scale: float,
) -> tuple[float | None, float | None] | None:
    if limits is None or scale == 1.0:
        return limits
    lower, upper = limits
    if scale < 0:
        return (
            None if upper is None else upper * scale,
            None if lower is None else lower * scale,
        )
    return (
        None if lower is None else lower * scale,
        None if upper is None else upper * scale,
    )


def _colormap_for(
    analysis: Map2DConfig,
    variable: str,
    comparison: Comparison,
) -> str:
    configured = analysis.map.colormap
    if (
        comparison in {"difference_time", "difference_simulation_time"}
        and "colormap" not in analysis.map.model_fields_set
    ):
        return "RdYlBu"
    if isinstance(configured, str):
        return configured
    return next(
        (
            colormap
            for name, colormap in configured.items()
            if name.casefold() == variable.casefold()
        ),
        "viridis",
    )


def _model_crs(grid: Any, configured_crs: str | None) -> str | None:
    grid_crs = getattr(grid, "crs", None)
    if grid_crs is None or str(grid_crs).casefold() in {"epsg:0", "0"}:
        return configured_crs
    to_string = getattr(grid_crs, "to_string", None)
    return str(to_string()) if callable(to_string) else str(grid_crs)


def _figure_path(
    layout: OutputLayout,
    series: str,
    subject: str,
    variable: str,
    comparison: _Comparison,
    time: datetime,
    color_index: int,
    extent_index: int,
) -> Path:
    safe_variable = quote(variable.casefold(), safe="-_.")
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    if time.microsecond:
        timestamp += f".{time.microsecond:06d}"
    filename = (
        f"{safe_variable}_{comparison}_{timestamp}_c{color_index + 1}"
        f"_e{extent_index + 1}.png"
    )
    path = layout.figures / series / "map2d" / comparison
    if "_simulation" in comparison or subject == "all_simulations":
        path /= quote(subject, safe="-_.")
    return path / safe_variable / filename


def _movie_path(
    layout: OutputLayout,
    series: str,
    subject: str,
    variable: str,
    comparison: _Comparison,
    color_index: int,
    extent_index: int,
) -> Path:
    safe_variable = quote(variable.casefold(), safe="-_.")
    filename = (
        f"{safe_variable}_{comparison}_c{color_index + 1}_e{extent_index + 1}.mp4"
    )
    path = layout.figures / series / "map2d" / comparison
    if "_simulation" in comparison or subject == "all_simulations":
        path /= quote(subject, safe="-_.")
    return path / filename


def _config_fingerprint(project: ProjectConfig, analysis: Map2DConfig) -> str:
    payload = {
        "project": project.model_dump(mode="json"),
        "analysis": analysis.model_dump(mode="json"),
    }
    return blake2b(canonical_json(payload).encode("utf-8"), digest_size=32).hexdigest()


def _png_fingerprint(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        with Image.open(path) as image:
            value = image.info.get("figure_fingerprint")
    except (OSError, UnidentifiedImageError):
        return None
    return value if isinstance(value, str) else None


def _hook_fingerprints(hooks: Sequence[HookSpec]) -> tuple[str, ...]:
    result: list[str] = []
    for hook in hooks:
        if isinstance(hook, str):
            if hook.count(":") != 1:
                raise Map2DPlotError(
                    "V-HOOK-001",
                    f"Hook {hook!r} must use the 'module:function' form.",
                )
            module_name, function_name = hook.split(":", maxsplit=1)
            try:
                module = __import__(module_name, fromlist=[function_name])
            except (ImportError, ValueError) as error:
                raise Map2DPlotError(
                    "V-HOOK-001",
                    f"Could not import figure hook {hook!r}: {error}",
                ) from error
            function = getattr(module, function_name, None)
            if not callable(function):
                raise Map2DPlotError(
                    "V-HOOK-001",
                    f"Configured figure hook {hook!r} is not callable.",
                )
            source_path = inspect.getsourcefile(function)
        else:
            source_path = inspect.getsourcefile(hook)
        if source_path is None:
            raise Map2DPlotError(
                "V-HOOK-001",
                f"Could not locate source for figure hook {hook!r}.",
            )
        try:
            result.append(fingerprint_small_input(Path(source_path)))
        except OSError as error:
            raise Map2DPlotError(
                "V-HOOK-001",
                f"Could not fingerprint figure hook {hook!r}: {error}",
            ) from error
    return tuple(result)
