# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for river-km segment statistics and post-processing."""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from d3dplot import __version__
from d3dplot.analyses.river_km_statistics.config import (
    RiverKmStatisticsConfig,
)
from d3dplot.analyses.river_km_statistics.process import (
    RiverKmStatisticsError,
    bed_slope_streamwise,
    component_labels,
    compute_cumulative,
    compute_segment_statistics,
    compute_segment_width,
    compute_time_average,
    difference_regions,
    difference_simulation_relative,
    multiply_by_width,
    prepare_region_segments,
    select_time_average_outputs,
)
from d3dplot.execution.tasks import AnalysisTimeSelection, plan_process_tasks
from d3dplot.simulation.time_axis import TimeAxis, TimeEntry, TimeSelectionResult
from d3dplot.store import OutputLayout, initialize_output_root
from d3dplot.variables.context import QuantityRequest
from d3dplot.variables.geometry import GridGeometry
from d3dplot.variables.grouping import (
    SedimentGroupingError,
    group_sediment_fraction_components,
)
from d3dplot.variables.registry import get_variable_spec


@pytest.mark.unit
def test_segment_statistics_use_componentwise_finite_masks_and_sample_std() -> None:
    result = compute_segment_statistics(
        values=[[1.0, 10.0], [3.0, np.nan], [np.inf, 30.0], [5.0, 50.0]],
        segment_index=[0, 0, 1, 1],
        face_area=[1.0, 2.0, 1.0, np.nan],
        segment_length_m=[2.0, 4.0, 10.0],
    )

    assert result["mean"][0, 0] == pytest.approx(2.0)
    assert result["std"][0, 0] == pytest.approx(np.sqrt(2.0))
    assert result["max"][0, 0] == pytest.approx(3.0)
    assert result["min"][0, 0] == pytest.approx(1.0)
    assert result["count"][0, 0] == 2
    assert result["sum"][0, 0] == pytest.approx(4.0)
    assert result["sum_per_length"][0, 0] == pytest.approx(2.0)
    assert result["mean_area_weighted"][0, 0] == pytest.approx(7.0 / 3.0)

    assert result["mean"][0, 1] == pytest.approx(10.0)
    assert np.isnan(result["std"][0, 1])
    assert result["count"][0, 1] == 1
    assert result["mean"][1, 0] == pytest.approx(5.0)
    assert result["count"][1, 0] == 1
    assert result["mean"][1, 1] == pytest.approx(40.0)
    assert result["std"][1, 1] == pytest.approx(np.sqrt(200.0))
    assert result["mean_area_weighted"][1, 1] == pytest.approx(30.0)
    assert result["count"][2, 0] == 0
    assert np.isnan(result["mean"][2, 0])
    assert np.isnan(result["sum"][2, 0])


@pytest.mark.unit
def test_streamwise_slope_uses_irregular_centres_and_nan_endpoints() -> None:
    result = bed_slope_streamwise(
        [[10.0, 2.0], [8.0, 4.0], [4.0, 8.0], [1.0, 10.0]],
        [1.0, 1.5, 2.5, 4.0],
    )

    assert np.isnan(result[0]).all()
    np.testing.assert_allclose(result[1], [-0.004, 0.004])
    np.testing.assert_allclose(result[2], [-0.0028, 0.0024])
    assert np.isnan(result[-1]).all()


@pytest.mark.unit
def test_width_multiplication_and_region_difference_preserve_dimensions() -> None:
    width = compute_segment_width(
        [4.0, -1.0, np.nan, 6.0],
        [0, 0, 1, 1],
        [2.0, 4.0],
    )
    np.testing.assert_allclose(width, [2.0, 1.5])
    np.testing.assert_allclose(
        multiply_by_width([[3.0, 5.0], [2.0, 4.0]], width),
        [[6.0, 10.0], [3.0, 6.0]],
    )
    np.testing.assert_allclose(
        difference_regions([5.0, np.nan], [2.0, 1.0]),
        [3.0, np.nan],
        equal_nan=True,
    )


@pytest.mark.unit
def test_time_average_uses_centre_to_corner_durations_and_propagates_nan() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    times = (start, start + timedelta(seconds=2), start + timedelta(seconds=6))
    values = np.asarray([[0.0, 1.0], [2.0, np.nan], [4.0, 3.0]])

    result = compute_time_average(values, times)

    assert result["mean"][0] == pytest.approx(22.0 / 9.0)
    expected_variance = (
        2.0 * (0.0 - 22.0 / 9.0) ** 2
        + 3.0 * (2.0 - 22.0 / 9.0) ** 2
        + 4.0 * (4.0 - 22.0 / 9.0) ** 2
    ) / 9.0
    assert result["std"][0] == pytest.approx(np.sqrt(expected_variance))
    assert result["max"][0] == 4.0
    assert result["min"][0] == 0.0
    expected_dominant = (
        (2.0 * 0.0 ** (5.0 / 3.0) + 3.0 * 2.0 ** (5.0 / 3.0) + 4.0 * 4.0 ** (5.0 / 3.0))
        / 9.0
    ) ** (3.0 / 5.0)
    assert result["dominant"][0] == pytest.approx(expected_dominant)
    assert np.isnan(result["mean"][1])
    assert np.isnan(result["max"][1])
    assert np.isnan(result["dominant"][1])


@pytest.mark.unit
def test_time_average_selections_can_be_added_to_the_processing_plan() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    entries = tuple(
        TimeEntry(
            time_index=index,
            subrun=0,
            local_index=index,
            time=start + timedelta(seconds=index * 10),
            morph_time=None,
            corrupt_partitions=(),
        )
        for index in range(4)
    )
    time_axis = TimeAxis(entries, "+00:00", morphology_available=False)
    config = RiverKmStatisticsConfig.model_validate(
        {
            "variables": ["bed_level"],
            "regions": [{"name": "region", "polygon": "region.shp"}],
            "river_km": {"file": "river.csv", "sets": {"250m": [0.125, 0.375]}},
            "time_averages": [
                {
                    "name": "middle-to-last",
                    "times": {"at": {"indices": [2, 4]}},
                }
            ],
        }
    )

    (average_selection,) = select_time_average_outputs(time_axis, config)
    process_selection = AnalysisTimeSelection(
        analysis_key="river-km-0",
        quantities=(QuantityRequest("bed_level"),),
        selection=TimeSelectionResult((entries[0],), (), ()),
        additional_selections=(average_selection,),
    )
    tasks = plan_process_tasks(
        "r001",
        (process_selection,),
        workers=1,
        max_block_times=10,
    )

    assert average_selection.entries == (entries[1], entries[3])
    assert [time.entry.time_index for task in tasks for time in task.times] == [
        0,
        1,
        3,
    ]


@pytest.mark.unit
def test_cumulative_uses_left_block_durations_and_propagates_nan() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    times = (start, start + timedelta(seconds=2), start + timedelta(seconds=6))

    np.testing.assert_allclose(
        compute_cumulative([2.0, 3.0, 4.0], times),
        [0.0, 4.0, 16.0],
    )
    np.testing.assert_allclose(
        compute_cumulative([2.0, np.nan, 4.0], times),
        [0.0, 4.0, np.nan],
        equal_nan=True,
    )


@pytest.mark.unit
def test_relative_simulation_difference_masks_zero_reference() -> None:
    np.testing.assert_allclose(
        difference_simulation_relative(
            [12.0, 1.0, np.nan],
            [10.0, 0.0, 2.0],
        ),
        [20.0, np.nan, np.nan],
        equal_nan=True,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "times",
    [
        (datetime(2024, 1, 1, tzinfo=UTC),),
        (
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 1, tzinfo=UTC),
        ),
    ],
)
def test_time_average_requires_two_distinct_ordered_outputs(
    times: tuple[datetime, ...],
) -> None:
    with pytest.raises(RiverKmStatisticsError) as error:
        compute_time_average(np.ones((len(times), 1)), times)

    assert error.value.code == "V-SMB-014"


@pytest.mark.unit
def test_dominant_is_a_time_average_result_not_a_spatial_statistic() -> None:
    with pytest.raises(ValidationError):
        RiverKmStatisticsConfig.model_validate(
            {
                "variables": ["bed_level"],
                "regions": [{"name": "region", "polygon": "region.shp"}],
                "river_km": {
                    "file": "river.csv",
                    "sets": {"250m": [0.125, 0.375]},
                },
                "statistics": ["dominant"],
            }
        )


@pytest.mark.unit
def test_sediment_grouping_uses_matlab_custom_boundaries_and_sums_components() -> None:
    grouped, names = group_sediment_fraction_components(
        [[1.0, 2.0, 3.0, 4.0, 5.0], [10.0, 20.0, 30.0, 40.0, 50.0]],
        [0.001, 0.002, 0.010, 0.016, 0.020],
        [0.002, 0.016],
        ["sand", "fine gravel", "coarse gravel"],
    )

    assert names == ("sand", "fine gravel", "coarse gravel")
    np.testing.assert_array_equal(grouped, [[1.0, 9.0, 5.0], [10.0, 90.0, 50.0]])


@pytest.mark.unit
def test_sediment_grouping_omits_empty_classes_and_preserves_leading_axes() -> None:
    values = np.arange(12.0).reshape(2, 3, 2)

    grouped, names = group_sediment_fraction_components(
        values,
        [0.001, 0.020],
        [0.002, 0.016],
        ["sand", "fine gravel", "coarse gravel"],
    )

    assert names == ("sand", "coarse gravel")
    assert grouped.shape == (2, 3, 2)
    np.testing.assert_array_equal(grouped[..., 0], values[..., 0])
    np.testing.assert_array_equal(grouped[..., 1], values[..., 1])


@pytest.mark.unit
def test_sediment_grouping_rejects_missing_diameters() -> None:
    with pytest.raises(SedimentGroupingError, match="grain diameter"):
        group_sediment_fraction_components(
            [[1.0, 2.0]],
            [0.001, None],
            [0.002],
            ["sand", "gravel"],
        )


@pytest.mark.unit
def test_fraction_grouping_config_validates_limits_labels_and_variable_scope() -> None:
    config_data: dict[str, Any] = {
        "variables": ["layer_fraction"],
        "regions": [{"name": "region", "polygon": "region.shp"}],
        "river_km": {
            "file": "river.csv",
            "sets": {"250m": [0.125, 0.375]},
        },
        "fraction_grouping": {
            "variables": ["Fak"],
            "limits_m": [0.002, 0.016],
            "labels": ["sand", "fine gravel", "coarse gravel"],
        },
    }

    config = RiverKmStatisticsConfig.model_validate(config_data)
    assert config.fraction_grouping is not None
    assert config.fraction_grouping.applies_to("Fak", "layer_fraction")

    invalid_overrides = (
        {
            "fraction_grouping": {
                **config_data["fraction_grouping"],
                "limits_m": [0.016, 0.002],
            }
        },
        {
            "fraction_grouping": {
                **config_data["fraction_grouping"],
                "labels": ["sand"],
            }
        },
        {"variables": ["bed_level"]},
        {"sum_fractions": True},
    )
    for override in invalid_overrides:
        invalid_config = {**config_data, **override}
        with pytest.raises(ValidationError):
            RiverKmStatisticsConfig.model_validate(invalid_config)


@pytest.mark.unit
def test_component_labels_follow_retained_quantity_dimensions() -> None:
    spec = get_variable_spec("layer_fraction")
    assert component_labels(
        spec,
        QuantityRequest("layer_fraction", fractions=(1, 2), layers=(1,)),
        2,
        fraction_names=("fine", "coarse"),
    ) == ("fine", "coarse")
    assert (
        component_labels(
            spec,
            QuantityRequest("layer_fraction", fractions=(1,), layers=(1,)),
            0,
            fraction_names=("fine", "coarse"),
        )
        == ()
    )


@pytest.mark.integration
def test_geometry_cache_reuses_assignment_and_matches_synthetic_segments(
    synthetic_case: Any,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(
        "INFO",
        logger="d3dplot.analyses.river_km_statistics.process",
    )
    from expected import EXACT_REGIONS, SEGMENT_LENGTH
    from mesh import make_centreline, make_mesh
    from params import Params

    from d3dplot.simulation.discover import discover_simulation
    from d3dplot.simulation.inspection import inspect_simulation

    simulation, report = discover_simulation(synthetic_case / "simulations" / "r001")
    assert simulation is not None
    assert report.is_valid
    layout = OutputLayout(tmp_path / "d3dplot")
    inspection, inspection_report = inspect_simulation(
        simulation,
        layout,
        forbidden_roots=(),
    )
    assert inspection is not None, inspection_report.to_text()
    guard = initialize_output_root(
        layout,
        tool_version=__version__,
        forbidden_roots=(),
    )
    params = Params()
    mesh = make_mesh(params, make_centreline(params))
    count = int(round(params.length / SEGMENT_LENGTH))
    centres = params.km0 + (np.arange(count, dtype=np.float64) + 0.5) * (
        SEGMENT_LENGTH / 1000.0
    )
    config = RiverKmStatisticsConfig.model_validate(
        {
            "variables": ["bed_level"],
            "regions": [
                {
                    "name": name,
                    "polygon": str(
                        synthetic_case / "inputs" / "regions" / f"{name}.shp"
                    ),
                }
                for name in EXACT_REGIONS
            ],
            "river_km": {
                "file": str(synthetic_case / "inputs" / "rkm" / "rkm.csv"),
                "sets": {"250m": centres.tolist()},
                "half_width_m": 750.0,
            },
            "statistics": ["mean", "count", "sum"],
        }
    )
    grid_geometry = GridGeometry.from_path(inspection.grid_paths[0])
    arguments = (
        config,
        grid_geometry,
        layout,
        guard,
        inspection.grid_paths[0],
        inspection.merge_plan_path,
    )
    first = prepare_region_segments(*arguments, model_crs="EPSG:28992")
    second = prepare_region_segments(*arguments, model_crs="EPSG:28992")
    assert "half_width_m=750.0" in caplog.text
    assert "Starting river-km point read:" in caplog.text
    assert "Starting region polygon read:" in caplog.text
    assert len(tuple(layout.cache_inputs.glob("river_km_*.nc"))) == 1
    assert any(
        "input cache created" in choice.casefold()
        for item in first
        for choice in item.choices
    )
    assert any(
        "input cache reused" in choice.casefold()
        for item in second
        for choice in item.choices
    )

    expected_rows: dict[tuple[str, int], list[int]] = {}
    with (synthetic_case / "expected" / "cells_per_segment_250m.csv").open(
        newline="", encoding="utf-8"
    ) as file:
        for row in csv.DictReader(file):
            expected_rows[(row["region"], int(row["segment"]))] = [
                int(global_number) - 1 for global_number in row["globalnr"].split()
            ]
    mesh_indices_by_coordinates = {
        (float(x), float(y)): index
        for index, (x, y) in enumerate(zip(mesh.face_x, mesh.face_y, strict=True))
    }
    grid_to_mesh = np.asarray(
        [
            mesh_indices_by_coordinates[(float(x), float(y))]
            for x, y in zip(
                grid_geometry.face_x,
                grid_geometry.face_y,
                strict=True,
            )
        ],
        dtype=np.int64,
    )
    assert len(first) == len(EXACT_REGIONS)
    for prepared, cached in zip(first, second, strict=True):
        assert prepared.cache_path.is_file()
        assert cached.cache_action == "reused"
        np.testing.assert_array_equal(
            cached.assignment.segment,
            prepared.assignment.segment,
        )
        for segment_index in range(count):
            grid_faces = np.flatnonzero(prepared.assignment.segment == segment_index)
            actual_mesh_indices = sorted(grid_to_mesh[grid_faces].tolist())
            assert (
                actual_mesh_indices
                == expected_rows[(prepared.region_name, segment_index)]
            )
