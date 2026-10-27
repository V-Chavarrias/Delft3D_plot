# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for cache-only map2d plotting."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import netCDF4
import numpy as np
import pytest
import rasterio
import xarray as xr
import xugrid as xu
from PIL import Image
from rasterio.transform import from_origin

from _input_snapshot import assert_inputs_unchanged, snapshot_inputs
from d3dplot import __version__
from d3dplot.analyses.map2d.config import Map2DConfig
from d3dplot.analyses.map2d.plot import (
    Map2DPlotError,
    Map2DPlotFrame,
    Map2DPlotSource,
    plot_map2d_group,
)
from d3dplot.config.project import ProjectConfig
from d3dplot.plotting.views.decorations import _clip_line
from d3dplot.store import (
    OutputLayout,
    OutputWriteGuard,
    atomic_path,
    initialize_output_root,
)

_HOOK_LINE_COUNTS: list[int] = []
_HOOK_VALUES: list[tuple[float, ...]] = []
_HOOK_LIMITS: list[tuple[float, float]] = []
_HOOK_AXES_COUNTS: list[int] = []
_HOOK_MAPPABLE_ZORDERS: list[float] = []
_HOOK_RIVER_LABELS: list[tuple[str, ...]] = []
_HOOK_MAP_TEXTS: list[tuple[str, ...]] = []
_HOOK_MEASUREMENT_AXES: list[bool] = []
_HOOK_SHARED_MEASUREMENT_LIMITS: list[tuple[float, float, float, float] | None] = []


def _capture_plot_state(figure: Any, context: Any) -> None:
    axes = context.axes["model"]
    values = np.asarray(context.data["values"], dtype=np.float64)
    mappable = axes.collections[0] if axes.collections else axes.images[0]
    _HOOK_LINE_COUNTS.append(len(axes.lines) + 1)
    _HOOK_AXES_COUNTS.append(len(figure.axes))
    _HOOK_VALUES.append(tuple(float(value) for value in values))
    _HOOK_LIMITS.append(tuple(float(value) for value in mappable.get_clim()))
    _HOOK_MAPPABLE_ZORDERS.append(float(mappable.get_zorder()))
    mappable.set_zorder(10.0)
    axes.plot([0.0, 1.0], [0.0, 1.0], color="red")
    axes.set_title("Hook title")
    figure.add_axes([0.01, 0.01, 0.01, 0.01])


def _capture_map_texts(figure: Any, context: Any) -> None:
    axes = context.axes["model"]
    _HOOK_RIVER_LABELS.append(tuple(point.label for point in context.data["river_km"]))
    _HOOK_MAP_TEXTS.append(tuple(text.get_text() for text in axes.texts))


def _capture_measurement_axes(figure: Any, context: Any) -> None:
    has_measurements = "measurements" in context.axes
    _HOOK_MEASUREMENT_AXES.append(has_measurements)
    if not has_measurements:
        _HOOK_SHARED_MEASUREMENT_LIMITS.append(None)
        return
    model_axes = context.axes["model"]
    measurement_axes = context.axes["measurements"]
    model_mappable = (
        model_axes.collections[0] if model_axes.collections else model_axes.images[0]
    )
    measurement_mappable = (
        measurement_axes.collections[0]
        if measurement_axes.collections
        else measurement_axes.images[0]
    )
    model_limits = model_mappable.get_clim()
    measurement_limits = measurement_mappable.get_clim()
    _HOOK_SHARED_MEASUREMENT_LIMITS.append(
        (
            float(model_limits[0]),
            float(model_limits[1]),
            float(measurement_limits[0]),
            float(measurement_limits[1]),
        )
    )


def _project_and_source(
    tmp_path: Path,
    *,
    values: tuple[tuple[float, ...], ...],
    render: str = "polygons",
    map_overrides: dict[str, Any] | None = None,
    figure_overrides: dict[str, Any] | None = None,
    project_overrides: dict[str, Any] | None = None,
    measurement_overrides: dict[str, Any] | None = None,
    movie_overrides: dict[str, Any] | None = None,
    hooks: list[str] | None = None,
    with_hook: bool = False,
    grid_faces: int = 2,
) -> tuple[ProjectConfig, Map2DConfig, Map2DPlotSource]:
    layout = OutputLayout(tmp_path / "output")
    guard = initialize_output_root(
        layout,
        tool_version=__version__,
        forbidden_roots=(),
    )
    grid_path = _write_grid(layout, guard, grid_faces, "grid.nc")
    frames = tuple(
        Map2DPlotFrame(
            match_key=f"time-{index}",
            time=datetime(2021, 1, index + 1, tzinfo=UTC),
            path=_write_frame(layout, guard, index, frame_values),
        )
        for index, frame_values in enumerate(values)
    )
    map_config: dict[str, Any] = {"render": render}
    if map_overrides is not None:
        map_config.update(map_overrides)
    analysis_config: dict[str, Any] = {
        "analysis": "map2d",
        "variables": ["water_depth"],
        "map": map_config,
    }
    if measurement_overrides is not None:
        analysis_config["measurements"] = measurement_overrides
    if movie_overrides is not None:
        analysis_config["movie"] = movie_overrides
    if figure_overrides is not None:
        analysis_config["figure"] = figure_overrides
    project_data: dict[str, Any] = {
        "simulations": [{"path": str(tmp_path), "label": "current"}],
        "output": {"root": str(layout.root)},
        "analyses": [analysis_config],
        "figure": {"size_cm": [8.0, 5.0], "dpi": 80},
    }
    if with_hook:
        project_data["hooks"] = [f"{__name__}:_capture_plot_state"]
    if hooks is not None:
        project_data["hooks"] = hooks
    if project_overrides is not None:
        project_data.update(project_overrides)
    project = ProjectConfig.model_validate(project_data)
    analysis = project.analyses[0]
    assert isinstance(analysis, Map2DConfig)
    source = Map2DPlotSource(
        label="current",
        layout=layout,
        guard=guard,
        grid_path=grid_path,
        frames=frames,
    )
    return project, analysis, source


def _write_grid(
    layout: OutputLayout,
    guard: OutputWriteGuard,
    n_faces: int,
    filename: str,
) -> Path:
    node_x = np.array([0.0, 1.0, 1.0, 0.0, 2.0, 2.0])[: 4 if n_faces == 1 else 6]
    node_y = np.array([0.0, 0.0, 1.0, 1.0, 0.0, 1.0])[: 4 if n_faces == 1 else 6]
    faces = np.array([[0, 1, 2, 3], [1, 4, 5, 2]], dtype=np.int64)[:n_faces]
    grid = xu.Ugrid2d(
        node_x=node_x,
        node_y=node_y,
        fill_value=-1,
        face_node_connectivity=faces,
    )
    dataset = xu.UgridDataset(
        xr.Dataset(attrs={"d3dplot_fingerprint": f"grid-{filename}"}),
        grid,
    )
    path = layout.cache_geometry / filename
    with atomic_path(path, guard) as temporary:
        dataset.ugrid.to_netcdf(temporary)
    dataset.close()
    return path


def _write_frame(
    layout: OutputLayout,
    guard: OutputWriteGuard,
    index: int,
    values: tuple[float, ...],
) -> Path:
    path = layout.cache_data / "plot-fixtures" / f"frame-{index}.nc"
    with atomic_path(path, guard) as temporary:
        with netCDF4.Dataset(temporary, mode="w") as dataset:
            dataset.createDimension("face", len(values))
            stored = dataset.createVariable("values", "f8", ("face",))
            stored[:] = values
            dataset.d3dplot_fingerprint = f"frame-{index}-fingerprint"
            dataset.variable = "water_depth"
    return path


def _write_ldb(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "L001\n     2     2\n       -1.000        0.500\n        3.000        0.500\n",
        encoding="utf-8",
    )
    return path


def _pixels(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGBA")).copy()


@pytest.mark.unit
@pytest.mark.parametrize("render", ["polygons", "raster"])
def test_plot_map2d_is_pixel_identical_when_fresh(
    tmp_path: Path,
    render: str,
) -> None:
    project, analysis, source = _project_and_source(
        tmp_path,
        values=((1.0, 2.0), (3.0, 4.0)),
        render=render,
    )
    first = plot_map2d_group(
        project,
        analysis,
        source,
        "water_depth",
        "value",
        fresh_figures=True,
    )
    first_pixels = {path: _pixels(path) for path in first.output_paths}
    second = plot_map2d_group(
        project,
        analysis,
        source,
        "water_depth",
        "value",
        fresh_figures=True,
    )
    assert second.output_paths == first.output_paths
    for path in second.output_paths:
        with Image.open(path) as image:
            np.testing.assert_array_equal(
                np.asarray(image.convert("RGBA")),
                first_pixels[path],
            )
            assert image.info["figure_fingerprint"]


@pytest.mark.unit
def test_plot_map2d_uses_effective_figure_settings_and_rebuilds_for_hooks(
    tmp_path: Path,
) -> None:
    _HOOK_LINE_COUNTS.clear()
    _HOOK_VALUES.clear()
    _HOOK_LIMITS.clear()
    _HOOK_AXES_COUNTS.clear()
    _HOOK_MAPPABLE_ZORDERS.clear()
    project, analysis, source = _project_and_source(
        tmp_path,
        values=((1.0, 2.0), (3.0, 4.0), (5.0, 6.0)),
        map_overrides={
            "extents": [(0.1, 0.9, 0.1, 0.9)],
            "color_limits_mode": {"days_since": "2021-01-01"},
        },
        figure_overrides={"display_scale": 2.0},
        with_hook=True,
    )
    assert project.effective_figure(analysis).hooks_mode == "rebuild"

    result = plot_map2d_group(
        project,
        analysis,
        source,
        "water_depth",
        "difference_time",
        fresh_figures=True,
    )
    assert len(result.output_paths) == 4
    assert _HOOK_LINE_COUNTS == [1, 1, 1, 1]
    assert _HOOK_AXES_COUNTS == [2, 2, 2, 2]
    assert _HOOK_VALUES == [(4.0, 4.0), (4.0, 4.0), (8.0, 8.0), (8.0, 8.0)]
    assert _HOOK_LIMITS == [(0.0, 2.0), (0.0, 2.0), (0.0, 4.0), (0.0, 4.0)]
    assert _HOOK_MAPPABLE_ZORDERS == [1.0, 1.0, 1.0, 1.0]
    extent_cache_times = {
        path: path.stat().st_mtime_ns
        for path in source.layout.cache_geometry.glob("extent_*.nc")
    }
    assert len(extent_cache_times) == 2

    first_run_pixels = {path: _pixels(path) for path in result.output_paths}
    second = plot_map2d_group(
        project,
        analysis,
        source,
        "water_depth",
        "difference_time",
        fresh_figures=True,
    )
    for path in second.output_paths:
        with Image.open(path) as image:
            np.testing.assert_array_equal(
                np.asarray(image.convert("RGBA")),
                first_run_pixels[path],
            )
    assert {
        path: path.stat().st_mtime_ns for path in extent_cache_times
    } == extent_cache_times
    cached = plot_map2d_group(
        project,
        analysis,
        source,
        "water_depth",
        "difference_time",
    )
    assert set(cached.reused_paths) == set(result.output_paths)

    with netCDF4.Dataset(source.frames[0].path, mode="a") as baseline:
        baseline.variables["values"][:] = [2.0, 3.0]
        baseline.d3dplot_fingerprint = "updated-baseline-fingerprint"
    rebuilt = plot_map2d_group(
        project,
        analysis,
        source,
        "water_depth",
        "difference_time",
    )
    assert rebuilt.reused_paths == ()
    assert len(rebuilt.output_paths) == 4


@pytest.mark.unit
def test_plot_map2d_regrids_simulation_time_difference(
    tmp_path: Path,
) -> None:
    project, analysis, source = _project_and_source(
        tmp_path,
        values=((2.0, 4.0), (3.0, 4.0)),
        grid_faces=2,
        with_hook=True,
    )
    reference_grid = _write_grid(source.layout, source.guard, 1, "reference-grid.nc")
    reference_frames = tuple(
        Map2DPlotFrame(
            match_key=frame.match_key,
            time=frame.time,
            path=_write_frame(
                source.layout,
                source.guard,
                index + 10,
                (1.0,),
            ),
        )
        for index, frame in enumerate(source.frames)
    )
    reference = Map2DPlotSource(
        label="reference",
        layout=source.layout,
        guard=source.guard,
        grid_path=reference_grid,
        frames=reference_frames,
    )
    _HOOK_VALUES.clear()

    result = plot_map2d_group(
        project,
        analysis,
        source,
        "water_depth",
        "difference_simulation_time",
        reference=reference,
        fresh_figures=True,
    )
    assert len(result.output_paths) == 1
    assert _HOOK_VALUES == [(1.0,)]
    reused_regridder = plot_map2d_group(
        project,
        analysis,
        source,
        "water_depth",
        "difference_simulation_time",
        reference=reference,
        fresh_figures=True,
    )
    assert reused_regridder.output_paths == result.output_paths
    assert _HOOK_VALUES[-1] == (1.0,)


@pytest.mark.unit
def test_plot_map2d_reports_empty_extents_and_invalid_face_count(
    tmp_path: Path,
) -> None:
    project, analysis, source = _project_and_source(
        tmp_path / "empty-extent",
        values=((1.0, 2.0),),
        map_overrides={"extents": [(3.0, 4.0, 3.0, 4.0)]},
    )
    messages: list[str] = []
    result = plot_map2d_group(
        project,
        analysis,
        source,
        "water_depth",
        "value",
        diagnostic=messages.append,
    )
    assert len(result.output_paths) == 1
    assert any("V-PLOT-001" in message for message in messages)

    project, analysis, source = _project_and_source(
        tmp_path / "invalid-cache",
        values=((1.0,),),
    )
    with pytest.raises(Map2DPlotError) as error:
        plot_map2d_group(
            project,
            analysis,
            source,
            "water_depth",
            "value",
        )
    assert error.value.code == "V-PLOT-002"


@pytest.mark.unit
def test_map2d_land_and_independent_river_km_overlays_are_cached_and_read_only(
    tmp_path: Path,
) -> None:
    land_path = _write_ldb(tmp_path / "inputs" / "boundary.ldb")
    labels_path = tmp_path / "inputs" / "labels.csv"
    labels_path.write_text(
        "x,y,label,km\n1.0,0.5,13.00_LABEL,13.000\n10.0,10.0,99.00_OUTSIDE,99.000\n",
        encoding="utf-8",
    )
    windows_path = tmp_path / "inputs" / "windows.csv"
    windows_path.write_text(
        "x,y,label,km\n1.0,0.5,12.00_WINDOW,12.000\n",
        encoding="utf-8",
    )
    inputs = (land_path, labels_path, windows_path)
    input_snapshot = snapshot_inputs(inputs)
    _HOOK_RIVER_LABELS.clear()
    _HOOK_MAP_TEXTS.clear()

    project, analysis, source = _project_and_source(
        tmp_path,
        values=((1.0, 2.0),),
        map_overrides={
            "land_boundaries": [land_path],
            "river_km_labels": {"file": labels_path, "color": "magenta"},
            "extents_along_river_km": {
                "file": windows_path,
                "window_x_m": 0.5,
                "window_y_m": 0.5,
            },
        },
        hooks=[f"{__name__}:_capture_map_texts"],
    )
    diagnostics: list[str] = []
    first = plot_map2d_group(
        project,
        analysis,
        source,
        "water_depth",
        "value",
        diagnostic=diagnostics.append,
    )

    cache_files = tuple(source.layout.cache_inputs.glob("ldb_*.nc"))
    assert len(first.output_paths) == 2
    assert len(cache_files) == 1
    assert "Cached land boundary boundary.ldb." in diagnostics
    assert _HOOK_RIVER_LABELS == [("13.00_LABEL",), ("13.00_LABEL",)]
    assert all("12.00_WINDOW" not in texts for texts in _HOOK_MAP_TEXTS)
    cache_mtime = cache_files[0].stat().st_mtime_ns

    diagnostics.clear()
    second = plot_map2d_group(
        project,
        analysis,
        source,
        "water_depth",
        "value",
        diagnostic=diagnostics.append,
    )
    assert set(second.reused_paths) == set(first.output_paths)
    assert "Reused land-boundary cache for boundary.ldb." in diagnostics
    assert cache_files[0].stat().st_mtime_ns == cache_mtime
    assert_inputs_unchanged(input_snapshot, inputs)

    segments = _clip_line(
        np.array([-1.0, 2.0]),
        np.array([0.5, 0.5]),
        (0.0, 1.0, 0.0, 1.0),
    )
    assert len(segments) == 1
    np.testing.assert_allclose(segments[0], [[0.0, 0.5], [1.0, 0.5]])


@pytest.mark.unit
def test_map2d_tiles_use_persistent_cache_and_provider_attribution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tile_module = pytest.importorskip("contextily.tile")
    calls: list[str] = []

    def fake_fetch(tile_url: str, *args: Any, **kwargs: Any) -> np.ndarray:
        calls.append(tile_url)
        return np.full((256, 256, 3), 127, dtype=np.uint8)

    monkeypatch.setattr(tile_module, "_fetch_tile", fake_fetch)
    tile_cache = tmp_path / "tile-cache"
    project, analysis, source = _project_and_source(
        tmp_path / "plot",
        values=((1.0, 2.0),),
        map_overrides={"background_tiles": {"provider": "Esri.WorldImagery"}},
        project_overrides={
            "crs": "EPSG:3857",
            "tiles_cache_dir": tile_cache,
        },
        hooks=[f"{__name__}:_capture_map_texts"],
    )
    _HOOK_MAP_TEXTS.clear()
    diagnostics: list[str] = []
    first = plot_map2d_group(
        project,
        analysis,
        source,
        "water_depth",
        "value",
        diagnostic=diagnostics.append,
    )
    first_fetch_count = len(calls)

    assert first.output_paths
    assert first_fetch_count > 0
    assert any(tile_cache.rglob("output.pkl"))
    assert any("Esri" in text for texts in _HOOK_MAP_TEXTS for text in texts)

    second = plot_map2d_group(
        project,
        analysis,
        source,
        "water_depth",
        "value",
    )
    assert set(second.reused_paths) == set(first.output_paths)
    assert len(calls) == first_fetch_count


@pytest.mark.unit
def test_map2d_tiles_report_offline_fallback_and_missing_optional_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tile_module = pytest.importorskip("contextily.tile")
    requests = pytest.importorskip("requests")

    def offline_fetch(*args: Any, **kwargs: Any) -> np.ndarray:
        raise requests.exceptions.ConnectionError("offline test")

    monkeypatch.setattr(tile_module, "_fetch_tile", offline_fetch)
    project, analysis, source = _project_and_source(
        tmp_path / "offline",
        values=((1.0, 2.0),),
        map_overrides={"background_tiles": {}},
        project_overrides={
            "crs": "EPSG:3857",
            "tiles_cache_dir": tmp_path / "offline-cache",
        },
    )
    diagnostics: list[str] = []
    result = plot_map2d_group(
        project,
        analysis,
        source,
        "water_depth",
        "value",
        diagnostic=diagnostics.append,
    )
    assert result.output_paths
    assert any("V-ENV-002" in message for message in diagnostics)

    from d3dplot.plotting.views import decorations

    original_import = decorations.import_module

    def without_contextily(name: str) -> Any:
        if name == "contextily":
            raise ImportError("contextily intentionally hidden in test")
        return original_import(name)

    monkeypatch.setattr(decorations, "import_module", without_contextily)
    project, analysis, source = _project_and_source(
        tmp_path / "missing-extra",
        values=((1.0, 2.0),),
        map_overrides={"background_tiles": {}},
        project_overrides={"crs": "EPSG:3857"},
    )
    with pytest.raises(Map2DPlotError) as error:
        plot_map2d_group(
            project,
            analysis,
            source,
            "water_depth",
            "value",
        )
    assert error.value.code == "V-ENV-001"


@pytest.mark.unit
def test_map2d_invalid_overlay_inputs_have_stable_validation_codes(
    tmp_path: Path,
) -> None:
    project, analysis, source = _project_and_source(
        tmp_path / "invalid-land",
        values=((1.0, 2.0),),
        map_overrides={"land_boundaries": [tmp_path / "missing.ldb"]},
    )
    with pytest.raises(Map2DPlotError) as error:
        plot_map2d_group(project, analysis, source, "water_depth", "value")
    assert error.value.code == "V-LDB-001"

    invalid_labels = tmp_path / "invalid-labels.csv"
    invalid_labels.write_text("invalid\n", encoding="utf-8")
    project, analysis, source = _project_and_source(
        tmp_path / "invalid-river-km",
        values=((1.0, 2.0),),
        map_overrides={"river_km_labels": {"file": invalid_labels}},
    )
    with pytest.raises(Map2DPlotError) as error:
        plot_map2d_group(project, analysis, source, "water_depth", "value")
    assert error.value.code == "V-RKM-001"


@pytest.mark.unit
def test_measurement_panel_is_hook_visible_and_omitted_for_simulation_differences(
    tmp_path: Path,
) -> None:
    _HOOK_MEASUREMENT_AXES.clear()
    _HOOK_SHARED_MEASUREMENT_LIMITS.clear()
    measurement_dir = tmp_path / "measurement-input"
    measurement_dir.mkdir()
    raster_path = measurement_dir / "measurements.tif"
    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        height=1,
        width=2,
        count=1,
        dtype="float32",
        crs="EPSG:28992",
        transform=from_origin(0.0, 1.0, 1.0, 1.0),
        nodata=-9999.0,
    ) as dataset:
        dataset.write(np.array([[4.0, 8.0]], dtype=np.float32), 1)
    catalogue = measurement_dir / "measurements.csv"
    catalogue.write_text(
        "time,file,factor\n2021-01-01T00:00:00Z,measurements.tif,1\n",
        encoding="utf-8",
    )
    project, analysis, source = _project_and_source(
        tmp_path / "plot",
        values=((1.0, 2.0), (3.0, 4.0)),
        project_overrides={"crs": "EPSG:28992"},
        measurement_overrides={
            "water_depth": {
                "catalogue": catalogue,
                "time_tolerance": "2 days",
                "spatial_margin_m": 0,
                "color_limits_from_measurements": True,
            }
        },
        hooks=[f"{__name__}:_capture_measurement_axes"],
    )

    measured = plot_map2d_group(
        project,
        analysis,
        source,
        "water_depth",
        "value",
        fresh_figures=True,
    )
    assert len(measured.output_paths) == 2
    assert _HOOK_MEASUREMENT_AXES == [True, True]
    assert _HOOK_SHARED_MEASUREMENT_LIMITS == [
        (4.0, 8.0, 4.0, 8.0),
        (4.0, 8.0, 4.0, 8.0),
    ]
    first_pixels = {path: _pixels(path) for path in measured.output_paths}
    measured_again = plot_map2d_group(
        project,
        analysis,
        source,
        "water_depth",
        "value",
        fresh_figures=True,
    )
    assert measured_again.output_paths == measured.output_paths
    for path in measured_again.output_paths:
        np.testing.assert_array_equal(_pixels(path), first_pixels[path])

    simulation_difference = plot_map2d_group(
        project,
        analysis,
        source,
        "water_depth",
        "difference_simulation",
        reference=source,
        fresh_figures=True,
    )
    assert len(simulation_difference.output_paths) == 2
    assert _HOOK_MEASUREMENT_AXES == [True, True, True, True, False, False]
    assert _HOOK_SHARED_MEASUREMENT_LIMITS[-2:] == [None, None]
    assert any(
        "omitted for simulation comparison" in message
        for message in (simulation_difference.messages)
    )


@pytest.mark.unit
def test_plot_map2d_writes_movie_from_all_group_frames(
    tmp_path: Path,
) -> None:
    pytest.importorskip("imageio")
    pytest.importorskip("imageio_ffmpeg")
    project, analysis, source = _project_and_source(
        tmp_path,
        values=((1.0, 2.0), (3.0, 4.0)),
        movie_overrides={"duration_s": 0.5},
    )

    result = plot_map2d_group(
        project,
        analysis,
        source,
        "water_depth",
        "value",
        fresh_figures=True,
    )

    assert len(result.output_paths) == 2
    assert len(result.movie_paths) == 1
    assert result.movie_paths[0].is_file()
    assert result.movie_paths[0].with_suffix(".mp4.json").is_file()
    assert any("Saved movie" in message for message in result.messages)
    import imageio.v2 as imageio

    with imageio.get_reader(result.movie_paths[0]) as reader:
        assert reader.count_frames() == 2
    reused = plot_map2d_group(
        project,
        analysis,
        source,
        "water_depth",
        "value",
    )
    assert reused.reused_movie_paths == result.movie_paths
