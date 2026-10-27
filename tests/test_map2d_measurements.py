# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for catalogue inspection and map-measurement preparation."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import netCDF4
import numpy as np
import pytest
import rasterio
from pyproj import CRS
from rasterio.transform import from_origin
from shapely.geometry import box

from _input_snapshot import assert_inputs_unchanged, snapshot_inputs
from d3dplot.config.measurements import MapMeasurementsConfig
from d3dplot.config.project import PathsConfig
from d3dplot.measurements.catalogue import (
    MapMeasurementError,
    inspect_map_catalogue,
)
from d3dplot.measurements.map2d import prepare_map_measurement
from d3dplot.store import OutputLayout, initialize_output_root

_CRS = "EPSG:28992"


def _output(tmp_path: Path) -> tuple[OutputLayout, Any]:
    layout = OutputLayout(tmp_path / "output")
    guard = initialize_output_root(layout, tool_version="test", forbidden_roots=())
    return layout, guard


def _write_raster(
    path: Path,
    values: np.ndarray,
    *,
    left: float,
    top: float,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=values.shape[0],
        width=values.shape[1],
        count=1,
        dtype="float32",
        crs=_CRS,
        transform=from_origin(left, top, 1, 1),
        nodata=-9999.0,
    ) as dataset:
        dataset.write(values.astype(np.float32), 1)


def _write_cf_raster(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", 2)
        dataset.createDimension("y", 2)
        dataset.createDimension("x", 2)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "seconds since 2021-01-01 00:00:00 +00:00"
        time[:] = [0.0, 3600.0]
        x = dataset.createVariable("x", "f8", ("x",))
        x.standard_name = "projection_x_coordinate"
        x.units = "m"
        x[:] = [0.5, 1.5]
        y = dataset.createVariable("y", "f8", ("y",))
        y.standard_name = "projection_y_coordinate"
        y.units = "m"
        y[:] = [1.5, 0.5]
        spatial_ref = dataset.createVariable("spatial_ref", "i4")
        spatial_ref.spatial_ref = CRS.from_epsg(28992).to_wkt()
        values = dataset.createVariable(
            "measurement",
            "f4",
            ("time", "y", "x"),
        )
        values.grid_mapping = "spatial_ref"
        values.coordinates = "time y x"
        values[:] = [
            [[1.0, 2.0], [3.0, 4.0]],
            [[5.0, 6.0], [7.0, 8.0]],
        ]


def _write_polygons(path: Path, records: list[tuple[str, float, Any]]) -> None:
    frame = gpd.GeoDataFrame(
        {
            "code_uniek": [record[0] for record in records],
            "MEAN": [record[1] for record in records],
            "COUNT": [100.0] * len(records),
            "A": [100.0] * len(records),
            "locatie": ["L1", "R2"][: len(records)],
        },
        geometry=[
            box(0, 0, 1, 1) if record[0] == "A" else box(1, 0, 2, 1)
            for record in records
        ],
        crs=_CRS,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_file(path, driver="GPKG", engine="pyogrio")


def test_catalogue_resolves_relative_paths_and_naive_time_zone(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "inputs"
    raster = input_dir / "value.tif"
    _write_raster(raster, np.array([[1.0]]), left=0, top=1)
    catalogue_path = input_dir / "measurements.csv"
    catalogue_path.write_text(
        "time,file,factor\n2021-01-01T01:00:00,value.tif,2\n",
        encoding="utf-8",
    )
    layout, guard = _output(tmp_path)
    before = snapshot_inputs((catalogue_path, raster))

    catalogue = inspect_map_catalogue(
        catalogue_path,
        PathsConfig(),
        layout,
        guard,
        time_zone="+01:00",
    )

    assert catalogue.entries[0].path == raster.resolve()
    assert catalogue.entries[0].time == datetime(2021, 1, 1, tzinfo=UTC)
    assert catalogue.entries[0].factor == 2.0
    assert catalogue.entries[0].bounds == (0.0, 0.0, 1.0, 1.0)
    assert list(layout.cache_inputs.glob("catalogue_*.csv"))
    messages: list[str] = []
    reused = inspect_map_catalogue(
        catalogue_path,
        PathsConfig(),
        layout,
        guard,
        time_zone="+01:00",
        diagnostic=messages.append,
    )
    assert reused.fingerprint == catalogue.fingerprint
    assert any(
        "Reused inspected measurement catalogue" in message for message in messages
    )
    assert_inputs_unchanged(before, (catalogue_path, raster))


def test_yaml_catalogue_resolves_relative_source_path(tmp_path: Path) -> None:
    raster = tmp_path / "inputs" / "value.tif"
    catalogue_path = tmp_path / "inputs" / "measurements.yaml"
    _write_raster(raster, np.array([[2.0]]), left=0, top=1)
    catalogue_path.write_text(
        "measurements:\n"
        "  - time: 2021-01-01T00:00:00Z\n"
        "    file: value.tif\n"
        "    factor: 0.5\n",
        encoding="utf-8",
    )
    layout, guard = _output(tmp_path)

    catalogue = inspect_map_catalogue(
        catalogue_path,
        PathsConfig(),
        layout,
        guard,
        time_zone=None,
    )

    assert catalogue.entries[0].path == raster.resolve()
    assert catalogue.entries[0].time == datetime(2021, 1, 1, tzinfo=UTC)
    assert catalogue.entries[0].factor == 0.5


def test_raster_mosaic_aligns_shifted_tiles_applies_factors_and_caches(
    tmp_path: Path,
) -> None:
    first = tmp_path / "inputs" / "first.tif"
    second = tmp_path / "inputs" / "second.tif"
    catalogue_path = tmp_path / "inputs" / "measurements.csv"
    _write_raster(first, np.array([[1.0, 2.0], [3.0, 4.0]]), left=0, top=2)
    _write_raster(second, np.array([[10.0, 20.0], [30.0, 40.0]]), left=1, top=2)
    catalogue_path.write_text(
        "time,file,factor\n"
        "2021-01-01T01:00:00,first.tif,1\n"
        "2021-01-01T01:00:00,second.tif,2\n",
        encoding="utf-8",
    )
    layout, guard = _output(tmp_path)
    settings = MapMeasurementsConfig.model_validate(
        {
            "catalogue": catalogue_path,
            "time_zone": "+01:00",
            "time_tolerance": "1 second",
            "spatial_margin_m": 0,
            "mosaic": "last",
        }
    )
    catalogue = inspect_map_catalogue(
        settings.catalogue,
        PathsConfig(),
        layout,
        guard,
        time_zone=settings.time_zone,
    )
    before = snapshot_inputs((catalogue_path, first, second))
    expected_time = datetime(2021, 1, 1, tzinfo=UTC)
    messages: list[str] = []

    result = prepare_map_measurement(
        catalogue,
        settings,
        expected_time,
        (0.0, 3.0, 0.0, 2.0),
        _CRS,
        layout,
        guard,
        diagnostic=messages.append,
    )
    cache_messages: list[str] = []
    cached = prepare_map_measurement(
        catalogue,
        settings,
        expected_time,
        (0.0, 3.0, 0.0, 2.0),
        _CRS,
        layout,
        guard,
        diagnostic=cache_messages.append,
    )

    assert result is not None, (catalogue.entries, messages)
    assert cached is not None
    np.testing.assert_array_equal(
        result.values,
        np.array([[1.0, 20.0, 40.0], [3.0, 60.0, 80.0]]),
    )
    np.testing.assert_array_equal(cached.values, result.values)
    assert cached.fingerprint == result.fingerprint
    assert any("Selected measurement sources" in message for message in messages)
    assert any("Reused measurement cache" in message for message in cache_messages)
    assert list((layout.cache_measurements / "map2d").glob("*.nc"))
    assert_inputs_unchanged(before, (catalogue_path, first, second))


def test_measurement_requires_model_crs(tmp_path: Path) -> None:
    raster = tmp_path / "inputs" / "value.tif"
    catalogue_path = tmp_path / "inputs" / "measurements.csv"
    _write_raster(raster, np.array([[1.0]]), left=0, top=1)
    catalogue_path.write_text(
        "time,file,factor\n2021-01-01T00:00:00Z,value.tif,1\n",
        encoding="utf-8",
    )
    layout, guard = _output(tmp_path)
    settings = MapMeasurementsConfig.model_validate(
        {"catalogue": catalogue_path, "spatial_margin_m": 0}
    )
    catalogue = inspect_map_catalogue(
        catalogue_path,
        PathsConfig(),
        layout,
        guard,
        time_zone=None,
    )

    with pytest.raises(MapMeasurementError, match="configure project.crs") as error:
        prepare_map_measurement(
            catalogue,
            settings,
            datetime(2021, 1, 1, tzinfo=UTC),
            (0.0, 1.0, 0.0, 1.0),
            None,
            layout,
            guard,
        )

    assert error.value.code == "V-MEA-003"


def test_raster_measurement_outside_tolerance_reports_v_mea_004(
    tmp_path: Path,
) -> None:
    raster = tmp_path / "inputs" / "value.tif"
    catalogue_path = tmp_path / "inputs" / "measurements.csv"
    _write_raster(raster, np.array([[1.0]]), left=0, top=1)
    catalogue_path.write_text(
        "time,file,factor\n2021-01-01T00:00:00Z,value.tif,1\n",
        encoding="utf-8",
    )
    layout, guard = _output(tmp_path)
    settings = MapMeasurementsConfig.model_validate(
        {
            "catalogue": catalogue_path,
            "time_tolerance": "1 hour",
            "spatial_margin_m": 0,
        }
    )
    catalogue = inspect_map_catalogue(
        settings.catalogue,
        PathsConfig(),
        layout,
        guard,
        time_zone=None,
    )
    assert catalogue.entries[0].time == datetime(2021, 1, 1, tzinfo=UTC)
    messages: list[str] = []

    result = prepare_map_measurement(
        catalogue,
        settings,
        datetime(2021, 1, 1, 2, tzinfo=UTC),
        (0.0, 1.0, 0.0, 1.0),
        _CRS,
        layout,
        guard,
        diagnostic=messages.append,
    )

    assert result is None
    assert len(messages) == 1
    assert messages[0].startswith("V-MEA-004:")


def test_cf_raster_selects_nearest_internal_time_band(tmp_path: Path) -> None:
    raster = tmp_path / "inputs" / "measurements.nc"
    catalogue_path = tmp_path / "inputs" / "measurements.csv"
    _write_cf_raster(raster)
    catalogue_path.write_text(
        "time,file,factor,value_field\n"
        "2021-01-01T01:00:00Z,measurements.nc,1,measurement\n",
        encoding="utf-8",
    )
    layout, guard = _output(tmp_path)
    settings = MapMeasurementsConfig.model_validate(
        {
            "catalogue": catalogue_path,
            "time_tolerance": "1 second",
            "spatial_margin_m": 0,
        }
    )
    catalogue = inspect_map_catalogue(
        catalogue_path,
        PathsConfig(),
        layout,
        guard,
        time_zone=None,
    )

    result = prepare_map_measurement(
        catalogue,
        settings,
        datetime(2021, 1, 1, 1, tzinfo=UTC),
        (0.0, 2.0, 0.0, 2.0),
        _CRS,
        layout,
        guard,
    )

    assert result is not None
    np.testing.assert_array_equal(result.values, np.array([[5.0, 6.0], [7.0, 8.0]]))


def test_polygon_time_difference_matches_shuffled_ids(
    tmp_path: Path,
) -> None:
    baseline_path = tmp_path / "inputs" / "baseline.gpkg"
    current_path = tmp_path / "inputs" / "current.gpkg"
    catalogue_path = tmp_path / "inputs" / "measurements.csv"
    _write_polygons(
        baseline_path,
        [("A", 2.0, None), ("B", 8.0, None)],
    )
    _write_polygons(
        current_path,
        [("B", 12.0, None), ("A", 5.0, None)],
    )
    catalogue_path.write_text(
        "time,file,factor\n"
        "2021-01-01T00:00:00Z,baseline.gpkg,1\n"
        "2021-01-02T00:00:00Z,current.gpkg,1\n",
        encoding="utf-8",
    )
    layout, guard = _output(tmp_path)
    settings = MapMeasurementsConfig.model_validate(
        {
            "catalogue": catalogue_path,
            "time_tolerance": "1 minute",
            "spatial_margin_m": 0,
            "polygon_filters": {"preset": "rws_vakken"},
        }
    )
    catalogue = inspect_map_catalogue(
        catalogue_path,
        PathsConfig(),
        layout,
        guard,
        time_zone=None,
    )
    before = snapshot_inputs((catalogue_path, baseline_path, current_path))

    baseline = prepare_map_measurement(
        catalogue,
        settings,
        datetime(2021, 1, 1, tzinfo=UTC),
        (0.0, 2.0, 0.0, 1.0),
        _CRS,
        layout,
        guard,
    )
    assert baseline is not None, catalogue.entries
    result = prepare_map_measurement(
        catalogue,
        settings,
        datetime(2021, 1, 2, tzinfo=UTC),
        (0.0, 2.0, 0.0, 1.0),
        _CRS,
        layout,
        guard,
        comparison="difference_time",
        baseline=baseline,
    )

    assert result is not None
    assert result.ids == ("B", "A")
    np.testing.assert_array_equal(result.values, np.array([4.0, 3.0]))
    assert list((layout.cache_measurements / "map2d").glob("*.gpkg"))
    assert_inputs_unchanged(before, (catalogue_path, baseline_path, current_path))


def test_missing_catalogue_has_measurement_validation_code(tmp_path: Path) -> None:
    layout, guard = _output(tmp_path)

    with pytest.raises(MapMeasurementError, match="does not exist") as error:
        inspect_map_catalogue(
            tmp_path / "missing.csv",
            PathsConfig(),
            layout,
            guard,
            time_zone=None,
        )

    assert error.value.code == "V-MEA-001"
