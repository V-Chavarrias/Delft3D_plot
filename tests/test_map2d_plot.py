# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for cache-only map2d plotting."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import netCDF4
import numpy as np
import pytest
import xarray as xr
import xugrid as xu
from PIL import Image

from d3dplot import __version__
from d3dplot.analyses.map2d.config import Map2DConfig
from d3dplot.analyses.map2d.plot import (
    Map2DPlotError,
    Map2DPlotFrame,
    Map2DPlotSource,
    plot_map2d_group,
)
from d3dplot.config.project import ProjectConfig
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


def _project_and_source(
    tmp_path: Path,
    *,
    values: tuple[tuple[float, ...], ...],
    render: str = "polygons",
    map_overrides: dict[str, Any] | None = None,
    figure_overrides: dict[str, Any] | None = None,
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
