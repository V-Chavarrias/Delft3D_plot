# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the HIS analysis config, time matching and station cache."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import numpy as np
import pytest
from pydantic import ValidationError

from d3dplot.analyses.his.config import HisConfig
from d3dplot.analyses.his.godin import godin_filter
from d3dplot.analyses.his.measurements import read_his_measurements
from d3dplot.analyses.his.stage import (
    HisStageError,
    _interpolate_to_times,
    _read_cache,
    _Run,
    _Series,
    _station_river_km,
    _window_std,
    _write_cache,
)
from d3dplot.config.project import PathsConfig, SimulationConfig
from d3dplot.river_km.file import RiverKmPoint
from d3dplot.simulation.his import HisError, HisSeries
from d3dplot.store import initialize_output_root


def _his(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "analysis": "his",
        "variables": ["water_level"],
        "stations": [1],
    }
    values.update(overrides)
    return values


def test_his_config_rejects_unsupported_comparison_and_time_matching() -> None:
    HisConfig.model_validate(_his(comparisons=["value", "difference_simulation"]))
    with pytest.raises(ValidationError, match="difference_time"):
        HisConfig.model_validate(_his(comparisons=["difference_time"]))
    with pytest.raises(ValidationError, match="time_matching"):
        HisConfig.model_validate(_his(time_matching="own"))


def test_his_config_rejects_duplicate_and_boolean_stations() -> None:
    with pytest.raises(ValidationError, match="unique"):
        HisConfig.model_validate(_his(stations=[1, 1]))
    with pytest.raises(ValidationError, match="stations"):
        HisConfig.model_validate(_his(stations=[True]))


def test_interpolate_bridges_coarser_source_and_leaves_outside_nan() -> None:
    target = np.array([0.0, 50.0, 100.0, 200.0])
    source = np.array([0.0, 100.0, 300.0])
    source_values = np.array([0.0, 10.0, 30.0])

    matched, count = _interpolate_to_times(target, source, source_values, None)

    assert np.allclose(matched[:3], [0.0, 5.0, 10.0])
    assert matched[3] == 20.0
    assert count == 4
    outside, outside_count = _interpolate_to_times(
        np.array([-1.0, 301.0]), source, source_values, None
    )
    assert np.isnan(outside).all()
    assert outside_count == 0


def test_interpolate_skips_masked_samples_and_applies_tolerance() -> None:
    source = np.array([0.0, 100.0, 200.0])
    source_values = np.array([0.0, np.nan, 20.0])
    target = np.array([150.0, 190.0])

    bridged, count = _interpolate_to_times(target, source, source_values, None)
    assert np.allclose(bridged, [15.0, 19.0])
    assert count == 2

    limited, limited_count = _interpolate_to_times(target, source, source_values, 5.0)
    assert np.isnan(limited).all()
    assert limited_count == 0


def test_station_cache_round_trip_and_fingerprint_invalidation(
    tmp_path: Path,
) -> None:
    guard = initialize_output_root(tmp_path / "output", tool_version="0.1.0")
    path = tmp_path / "output" / "his_station.nc"
    series = HisSeries(
        time_seconds=np.array([0.0, 3600.0, 7200.0]),
        values=np.array([1.0, np.nan, 3.0]),
    )
    _write_cache(
        path,
        guard,
        series=series,
        station="s01",
        variable="water_level",
        fingerprint="abc",
    )

    cached = _read_cache(path, "abc")
    assert cached is not None
    times, values = cached
    assert np.array_equal(times, series.time_seconds)
    assert np.allclose(values, [1.0, np.nan, 3.0], equal_nan=True)
    assert _read_cache(path, "changed") is None


def test_godin_filter_keeps_constant_series_and_trims_window_edges() -> None:
    times = np.arange(100) * 3600.0

    filtered_times, filtered = godin_filter(times, np.full(100, 2.5))

    # The 24, 24 and 25 hour windows remove 23 + 23 + 24 = 70 valid samples.
    assert filtered.size == 30
    assert filtered_times.size == 30
    np.testing.assert_allclose(filtered, 2.5)


def test_godin_filter_returns_empty_when_series_is_shorter_than_windows() -> None:
    times, values = godin_filter(np.arange(10) * 3600.0, np.ones(10))

    assert times.size == 0
    assert values.size == 0


def _write_measurements(folder: Path, rows: str) -> Path:
    (folder / "s01.csv").write_text(
        "time,value\n2020-01-01T01:00:00,2.0\n2020-01-01T00:00:00,1.0\n",
        encoding="utf-8",
    )
    catalogue = folder / "catalogue.csv"
    catalogue.write_text("station,variable,file,factor\n" + rows, encoding="utf-8")
    return catalogue


def test_measurements_are_scaled_sorted_and_matched_case_insensitively(
    tmp_path: Path,
) -> None:
    catalogue_path = _write_measurements(tmp_path, "S01,Water_Level,s01.csv,100\n")

    catalogue = read_his_measurements(catalogue_path, PathsConfig(), time_zone="+01:00")

    measurement = catalogue.find("s01", "water_level", 1)
    assert measurement is not None
    np.testing.assert_allclose(measurement.values, [100.0, 200.0])
    # Naive times use the configured +01:00 zone, so 00:00 local is 23:00 UTC of
    # the previous day.
    assert measurement.time_seconds[0] == pytest.approx(
        datetime(2019, 12, 31, 23, tzinfo=UTC).timestamp()
    )
    assert catalogue.find("s02", "water_level", 1) is None


def test_measurement_catalogue_rejects_repeated_station_variable_layer(
    tmp_path: Path,
) -> None:
    catalogue_path = _write_measurements(
        tmp_path,
        "s01,water_level,s01.csv,1\nS01,Water_Level,s01.csv,2\n",
    )

    with pytest.raises(HisError, match=r"\[V-HIS-006\]"):
        read_his_measurements(catalogue_path, PathsConfig(), time_zone=None)


def test_his_config_requires_river_km_for_x_t_and_positive_convergence_time() -> None:
    HisConfig.model_validate(_his(views=["x_t"], river_km_file="rkm.csv"))
    with pytest.raises(ValidationError, match="river_km_file"):
        HisConfig.model_validate(_his(views=["x_t"]))
    with pytest.raises(ValidationError, match="convergence_time"):
        HisConfig.model_validate(
            _his(views=["convergence"], convergence_time=timedelta(0))
        )


def _station_series(
    *,
    label: str = "run",
    station: str = "s01",
    values: list[float],
    times: list[float],
    position: tuple[float, float] | None = None,
) -> _Series:
    return _Series(
        simulation=cast(SimulationConfig, SimpleNamespace(label=label)),
        variable="water_level",
        station_name=station,
        time_seconds=np.asarray(times, dtype=np.float64),
        values=np.asarray(values, dtype=np.float64),
        fingerprint="fingerprint",
        layer=1,
        layered=False,
        position=position,
    )


def test_window_std_uses_last_window_and_ignores_masked_values() -> None:
    times = [0.0, 3600.0, 7200.0, 10800.0]

    # The last window is [3600, 10800]: the first value is outside it.
    assert _window_std(
        _station_series(values=[100.0, 1.0, 3.0, 5.0], times=times), 7200.0
    ) == pytest.approx(2.0)
    # Only one finite value falls in the window, so there is no spread.
    assert np.isnan(
        _window_std(
            _station_series(values=[100.0, 1.0, np.nan, np.nan], times=times), 7200.0
        )
    )


def test_station_river_km_places_station_at_nearest_point_and_logs_it() -> None:
    points = (
        RiverKmPoint(x=0.0, y=0.0, label="a", km=10.0, km_text="10"),
        RiverKmPoint(x=100.0, y=0.0, label="b", km=20.0, km_text="20"),
    )
    run = _Run()
    series = [
        _station_series(values=[1.0], times=[0.0], position=(70.0, 3.0)),
        _station_series(values=[2.0], times=[0.0], position=(70.0, 3.0)),
    ]

    km_of = _station_river_km(series, points, run)

    assert km_of == {("run", "s01"): 20.0}
    assert len(run.messages) == 1
    assert "river km 20" in run.messages[0]


def test_station_river_km_rejects_station_without_position() -> None:
    points = (RiverKmPoint(x=0.0, y=0.0, label="a", km=10.0, km_text="10"),)

    with pytest.raises(HisStageError, match="No position"):
        _station_river_km([_station_series(values=[1.0], times=[0.0])], points, _Run())
