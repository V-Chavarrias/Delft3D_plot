# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for river-km profile-measurement conversion and matching."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import scipy.io
from matplotlib.figure import Figure
from typer.testing import CliRunner

from d3dplot import __version__
from d3dplot.analyses.river_km_statistics.config import RiverKmStatisticsConfig
from d3dplot.analyses.river_km_statistics.measurements import (
    ProfileMeasurementError,
    convert_mat_profile_measurements,
    load_profile_measurements,
    match_profile_measurement,
    match_time_series_measurement,
)
from d3dplot.analyses.river_km_statistics.plot import (
    _add_profile_measurement_overlays,
    _ProfileMeasurementOverlay,
)
from d3dplot.cli import app
from d3dplot.store import (
    OutputLayout,
    OutputWriteError,
    fingerprint_small_input,
    initialize_output_root,
)


def _datenum(year: int, month: int, day: int) -> int:
    return datetime(year, month, day).toordinal() + 366


def _write_mat(path: Path, *, valid_shape: bool = True) -> None:
    times = np.asarray(
        [_datenum(2024, 1, 3), _datenum(2024, 1, 1), _datenum(2024, 1, 2)],
        dtype=np.float64,
    )
    river_km = np.asarray([2.0, 1.0, 3.0])
    values = np.asarray(
        [
            [23.0, 21.0, 22.0],
            [13.0, 11.0, 12.0],
            [33.0, 31.0, 32.0],
        ]
    )
    if not valid_shape:
        values = values[:, :2]
    profile = {
        "rkm": river_km,
        "s": np.asarray([200.0, 100.0, 300.0]),
        "tim_dnum": times,
        "unit": "bed elevation [m+NAP]",
        "val": values,
    }
    slope = {
        **profile,
        "unit": "bed slope [-]",
        "val": np.asarray(
            [
                [0.23, 0.21, 0.22],
                [0.13, 0.11, 0.12],
                [0.33, 0.31, 0.32],
            ]
        ),
    }
    scipy.io.savemat(
        path,
        {
            "data": {
                "bl": {
                    "val_mean": profile,
                    "source": np.asarray(
                        ["source-3", "source-1", "source-2"],
                        dtype=object,
                    ),
                },
                "detab_ds": {
                    "val_mean": slope,
                    "source": np.asarray(
                        ["slope-3", "slope-1", "slope-2"],
                        dtype=object,
                    ),
                },
            }
        },
    )


def _root(tmp_path: Path, name: str = "output") -> tuple[OutputLayout, Any]:
    layout = OutputLayout(tmp_path / name)
    guard = initialize_output_root(layout, tool_version=__version__)
    return layout, guard


@pytest.mark.unit
def test_mat_conversion_sorts_times_and_locations_and_preserves_metadata(
    tmp_path: Path,
) -> None:
    source = tmp_path / "measurements.mat"
    _write_mat(source)
    source_fingerprint = fingerprint_small_input(source)
    layout, guard = _root(tmp_path)
    messages: list[str] = []

    measurements = load_profile_measurements(
        source,
        ["bed_level", "bed_slope_streamwise"],
        time_zone="+01:00",
        layout=layout,
        guard=guard,
        diagnostic=messages.append,
    )

    bed_level = measurements["bed_level"]
    assert bed_level.times == (
        datetime(2023, 12, 31, 23, tzinfo=UTC),
        datetime(2024, 1, 1, 23, tzinfo=UTC),
        datetime(2024, 1, 2, 23, tzinfo=UTC),
    )
    np.testing.assert_array_equal(bed_level.river_km, [1.0, 2.0, 3.0])
    np.testing.assert_allclose(
        bed_level.statistics["mean"],
        [[11.0, 21.0, 31.0], [12.0, 22.0, 32.0], [13.0, 23.0, 33.0]],
    )
    assert bed_level.units == "m"
    assert bed_level.vertical_datum == "NAP"
    assert bed_level.source_files == ("source-1", "source-2", "source-3")
    assert measurements["bed_slope_streamwise"].units == "-"
    assert source_fingerprint == fingerprint_small_input(source)
    assert any("Cached profile measurements" in message for message in messages)
    assert (
        len(list((layout.cache_measurements / "river_km_statistics").glob("*.nc"))) == 2
    )


@pytest.mark.unit
def test_profile_and_time_series_matching_honor_tolerances(tmp_path: Path) -> None:
    source = tmp_path / "measurements.mat"
    _write_mat(source)
    layout, guard = _root(tmp_path)
    (measurement,) = load_profile_measurements(
        source,
        ["bed_level"],
        time_zone="+00:00",
        layout=layout,
        guard=guard,
    ).values()

    profile_match = match_profile_measurement(
        measurement,
        "mean_area_weighted",
        datetime(2024, 1, 1, 12, tzinfo=UTC),
        timedelta(hours=12),
    )
    assert profile_match is not None
    assert profile_match.time == datetime(2024, 1, 1, tzinfo=UTC)
    assert profile_match.difference == timedelta(hours=12)
    np.testing.assert_allclose(profile_match.values, [11.0, 21.0, 31.0])
    assert (
        match_profile_measurement(
            measurement,
            "mean",
            datetime(2024, 1, 10, tzinfo=UTC),
            timedelta(days=1),
        )
        is None
    )

    series_match = match_time_series_measurement(
        measurement,
        "mean",
        river_km=2.8,
        tolerance_km=0.25,
    )
    assert series_match is not None
    assert series_match.river_km == 3.0
    np.testing.assert_allclose(series_match.values, [31.0, 32.0, 33.0])
    assert (
        match_time_series_measurement(
            measurement,
            "mean",
            river_km=5.0,
            tolerance_km=0.5,
        )
        is None
    )


@pytest.mark.unit
def test_difference_time_overlay_subtracts_the_baseline_measurement(
    tmp_path: Path,
) -> None:
    source = tmp_path / "measurements.mat"
    _write_mat(source)
    layout, guard = _root(tmp_path)
    (measurement,) = load_profile_measurements(
        source,
        ["bed_level"],
        time_zone="+00:00",
        layout=layout,
        guard=guard,
    ).values()
    analysis = RiverKmStatisticsConfig.model_validate(
        {
            "variables": ["bed_level"],
            "regions": [{"name": "region", "polygon": str(tmp_path / "region.shp")}],
            "river_km": {
                "file": str(tmp_path / "rkm.csv"),
                "sets": {"test": [1.0, 2.0]},
            },
        }
    )
    axes = Figure().subplots()

    entries = _add_profile_measurement_overlays(
        axes,
        (_ProfileMeasurementOverlay("Measurements", measurement),),
        analysis,
        "mean",
        datetime(2024, 1, 2, tzinfo=UTC),
        "difference_time",
        datetime(2024, 1, 1, tzinfo=UTC),
        1.0,
        "profile",
        None,
        None,
        lambda message: None,
    )

    assert len(entries) == 1
    np.testing.assert_array_equal(
        np.asarray(entries[0]["values"]),
        [1.0, 1.0, 1.0],
    )
    np.testing.assert_array_equal(axes.lines[0].get_ydata(), [1.0, 1.0, 1.0])


@pytest.mark.unit
def test_cached_mat_conversion_is_reused_and_input_is_unchanged(
    tmp_path: Path,
) -> None:
    source = tmp_path / "measurements.mat"
    _write_mat(source)
    before = fingerprint_small_input(source)
    layout, guard = _root(tmp_path)
    load_profile_measurements(
        source,
        ["bed_level"],
        time_zone="+01:00",
        layout=layout,
        guard=guard,
    )

    messages: list[str] = []
    second = load_profile_measurements(
        source,
        ["bed_level"],
        time_zone="+01:00",
        layout=layout,
        guard=guard,
        diagnostic=messages.append,
    )

    assert any("Reused profile-measurement cache" in message for message in messages)
    assert second["bed_level"].fingerprint
    assert fingerprint_small_input(source) == before


@pytest.mark.unit
def test_standalone_conversion_is_guarded_and_never_overwrites(
    tmp_path: Path,
) -> None:
    source = tmp_path / "measurements.mat"
    _write_mat(source)
    output_root, guard = _root(tmp_path, "converted")
    output = output_root.root / "profiles.nc"
    before = fingerprint_small_input(source)

    paths = convert_mat_profile_measurements(
        source,
        output,
        time_zone="+00:00",
        guard=guard,
        variable="bl",
    )

    assert paths == (output,)
    loaded = load_profile_measurements(
        output,
        ["bed_level"],
        time_zone=None,
        layout=output_root,
        guard=guard,
    )
    np.testing.assert_array_equal(loaded["bed_level"].river_km, [1.0, 2.0, 3.0])
    assert fingerprint_small_input(source) == before
    with pytest.raises(OutputWriteError, match="Refusing to overwrite"):
        convert_mat_profile_measurements(
            source,
            output,
            time_zone="+00:00",
            guard=guard,
            variable="bl",
        )


@pytest.mark.unit
def test_standalone_conversion_writes_one_file_per_variable(
    tmp_path: Path,
) -> None:
    source = tmp_path / "measurements.mat"
    _write_mat(source)
    output_root, guard = _root(tmp_path, "converted")

    paths = convert_mat_profile_measurements(
        source,
        output_root.root / "profiles.nc",
        time_zone="+00:00",
        guard=guard,
    )

    assert {path.name for path in paths} == {
        "profiles_bed_level.nc",
        "profiles_bed_slope_streamwise.nc",
    }


@pytest.mark.unit
def test_v73_and_malformed_mat_files_have_stable_validation_codes(
    tmp_path: Path,
) -> None:
    layout, guard = _root(tmp_path)
    hdf5 = tmp_path / "measurements_v73.mat"
    hdf5.write_bytes(b"\x89HDF\r\n\x1a\nnot-a-complete-file")
    with pytest.raises(ProfileMeasurementError) as hdf5_error:
        load_profile_measurements(
            hdf5,
            ["bed_level"],
            time_zone="+00:00",
            layout=layout,
            guard=guard,
        )
    assert hdf5_error.value.code == "V-MEA-006"

    malformed = tmp_path / "measurements_bad.mat"
    _write_mat(malformed, valid_shape=False)
    with pytest.raises(ProfileMeasurementError) as shape_error:
        load_profile_measurements(
            malformed,
            ["bed_level"],
            time_zone="+00:00",
            layout=layout,
            guard=guard,
        )
    assert shape_error.value.code == "V-MEA-007"


@pytest.mark.unit
def test_measurements_convert_cli_creates_guarded_netcdf_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "measurements.mat"
    output = tmp_path / "converted" / "profiles.nc"
    _write_mat(source)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "measurements",
            "convert",
            str(source),
            "--output",
            str(output),
            "--time-zone",
            "+01:00",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "converted" / ".d3dplot-output").is_file()
    assert (tmp_path / "converted" / "profiles_bed_level.nc").is_file()
    assert "profiles_bed_slope_streamwise.nc" in result.output
