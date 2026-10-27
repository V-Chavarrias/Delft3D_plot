# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for cache-backed plot-stage provenance and one-figure replay."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import netCDF4
import numpy as np
import pytest
import xarray as xr
import xugrid as xu
import yaml
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from typer.testing import CliRunner

from d3dplot import __version__
from d3dplot.analyses.map2d.plot import Map2DPlotError
from d3dplot.cli import app
from d3dplot.config import load_config
from d3dplot.plotting.stage import read_replot_metadata
from d3dplot.store import (
    OutputLayout,
    OutputWriteGuard,
    atomic_path,
    initialize_output_root,
)

runner = CliRunner()


@pytest.mark.integration
def test_plot_overrides_save_resolved_provenance_and_replot_one_figure(
    tmp_path: Path,
) -> None:
    layout, configuration = _prepared_project(tmp_path)

    plotted = runner.invoke(
        app,
        [
            "plot",
            str(configuration),
            "--set",
            "analyses[0].figure.dpi=96",
        ],
    )

    assert plotted.exit_code == 0, plotted.output
    latest = load_config(layout.config / "latest.yaml")
    assert latest.effective_figure(latest.analyses[0]).dpi == 96
    assert latest.simulations[0].path == (Path.cwd() / "model").resolve()

    figures = tuple(sorted(layout.figures.rglob("*.png")))
    assert len(figures) == 2
    with Image.open(figures[0]) as image:
        original_pixels = np.asarray(image.convert("RGBA")).copy()
        provenance_path = Path(image.info["d3dplot_provenance"])
        assert image.info["d3dplot_replot_key"]
    assert provenance_path.is_file()
    assert provenance_path.parent == layout.config

    plotted_from_directory = runner.invoke(app, ["plot", str(layout.root)])
    assert plotted_from_directory.exit_code == 0, plotted_from_directory.output
    assert "2 reused" in plotted_from_directory.output

    _mutate_png_preserving_metadata(figures[0])
    _mutate_png_preserving_metadata(figures[1])
    untouched_bytes = figures[1].read_bytes()

    replayed = runner.invoke(app, ["replot", str(figures[0])])

    assert replayed.exit_code == 0, replayed.output
    with Image.open(figures[0]) as image:
        np.testing.assert_array_equal(
            np.asarray(image.convert("RGBA")),
            original_pixels,
        )
    assert figures[1].read_bytes() == untouched_bytes


@pytest.mark.unit
def test_replot_rejects_png_without_d3dplot_provenance(tmp_path: Path) -> None:
    layout = OutputLayout(tmp_path / "output")
    initialize_output_root(layout, tool_version=__version__, forbidden_roots=())
    figure = layout.figures / "without-metadata.png"
    figure.parent.mkdir(parents=True)
    Image.new("RGB", (4, 4), color="white").save(figure)

    with pytest.raises(Map2DPlotError) as error:
        read_replot_metadata(figure)
    assert error.value.code == "V-PLOT-005"


def _prepared_project(tmp_path: Path) -> tuple[OutputLayout, Path]:
    layout = OutputLayout(tmp_path / "generated" / "reference" / "d3dplot")
    guard = initialize_output_root(
        layout,
        tool_version=__version__,
        forbidden_roots=(),
    )
    grid = _write_grid(layout, guard)
    _write_frame(
        layout,
        guard,
        grid,
        datetime(2021, 1, 1, tzinfo=UTC),
        (1.0, 2.0),
    )
    _write_frame(
        layout,
        guard,
        grid,
        datetime(2021, 1, 2, tzinfo=UTC),
        (2.0, 3.0),
    )
    configuration = tmp_path / "project.yaml"
    configuration.write_text(
        yaml.safe_dump(
            {
                "simulations": [{"path": "model", "label": "reference"}],
                "output": {"root": str(tmp_path / "generated")},
                "figure": {"size_cm": [8.0, 5.0], "dpi": 80},
                "analyses": [
                    {
                        "analysis": "map2d",
                        "variables": ["water_depth"],
                        "map": {"render": "polygons"},
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return layout, configuration


def _write_grid(layout: OutputLayout, guard: OutputWriteGuard) -> Path:
    grid = xu.Ugrid2d(
        node_x=np.array([0.0, 1.0, 1.0, 0.0, 2.0, 2.0]),
        node_y=np.array([0.0, 0.0, 1.0, 1.0, 0.0, 1.0]),
        fill_value=-1,
        face_node_connectivity=np.array([[0, 1, 2, 3], [1, 4, 5, 2]]),
    )
    dataset = xu.UgridDataset(
        xr.Dataset(attrs={"d3dplot_fingerprint": "grid-fingerprint"}),
        grid,
    )
    path = layout.cache_geometry / "grid.nc"
    with atomic_path(path, guard) as temporary:
        dataset.ugrid.to_netcdf(temporary)
    dataset.close()
    return path


def _write_frame(
    layout: OutputLayout,
    guard: OutputWriteGuard,
    grid_path: Path,
    time: datetime,
    values: tuple[float, float],
) -> Path:
    path = (
        layout.cache_data
        / "map2d"
        / "water_depth"
        / "all"
        / f"{time.strftime('%Y%m%dT%H%M%S')}.nc"
    )
    relative_grid = os.path.relpath(grid_path, path.parent).replace("\\", "/")
    with atomic_path(path, guard) as temporary:
        with netCDF4.Dataset(temporary, mode="w", format="NETCDF4") as dataset:
            dataset.createDimension("face", len(values))
            stored = dataset.createVariable("values", "f4", ("face",))
            stored[:] = values
            dataset.d3dplot_fingerprint = f"cache-{time.isoformat()}"
            dataset.time = time.isoformat()
            dataset.variable = "water_depth"
            dataset.selection = "all"
            dataset.grid_file = relative_grid
    return path


def _mutate_png_preserving_metadata(path: Path) -> None:
    with Image.open(path) as image:
        pixels = np.asarray(image.convert("RGBA")).copy()
        metadata = {
            key: value for key, value in image.info.items() if isinstance(value, str)
        }
    pixels[0, 0] = 255 - pixels[0, 0]
    png_metadata = PngInfo()
    for key, value in metadata.items():
        png_metadata.add_text(key, value)
    Image.fromarray(pixels).save(path, pnginfo=png_metadata)
