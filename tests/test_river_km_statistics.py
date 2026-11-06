# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for river-km segment statistics and post-processing."""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pytest
from pydantic import ValidationError

from d3dplot import __version__
from d3dplot.analyses.river_km_statistics.config import (
    RiverKmStatisticsConfig,
)
from d3dplot.analyses.river_km_statistics.plot import (
    RiverKmStatisticsPlotError,
    StatisticCacheFrame,
    _clear_profile_overlays,
    _comparison_values,
    _line_path,
    _plot_axis_times,
    _source_statistic_values,
    statistic_values,
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
    river_km_statistics_output_path,
    select_time_average_outputs,
)
from d3dplot.config.figure import FigureConfig
from d3dplot.config.project import ProjectConfig
from d3dplot.config.selections import TimesConfig
from d3dplot.execution.tasks import AnalysisTimeSelection, plan_process_tasks
from d3dplot.plotting.views.line import LineView
from d3dplot.simulation.time_axis import TimeAxis, TimeEntry, TimeSelectionResult
from d3dplot.store import OutputLayout, initialize_output_root
from d3dplot.variables.context import QuantityRequest
from d3dplot.variables.geometry import GridGeometry
from d3dplot.variables.grouping import (
    SedimentGroupingError,
    group_sediment_fraction_components,
)
from d3dplot.variables.registry import get_variable_spec


def _statistics_test_config(
    *,
    time_matching: str = "reference",
    times: TimesConfig | None = None,
) -> RiverKmStatisticsConfig:
    return RiverKmStatisticsConfig.model_validate(
        {
            "variables": ["bed_level"],
            "regions": [{"name": "region", "polygon": "region.shp"}],
            "river_km": {
                "file": "river.csv",
                "sets": {"250m": [0.125, 0.375]},
            },
            "statistics": ["mean"],
            "time_matching": time_matching,
            "times": times or TimesConfig(at="all"),
        }
    )


def _mean_frame(
    index: int,
    time: datetime,
    value: float,
    *,
    morph_time: datetime | None = None,
) -> StatisticCacheFrame:
    return StatisticCacheFrame(
        time=time,
        morph_time=morph_time,
        path=Path(f"statistics-{index}.nc"),
        fingerprint=f"fingerprint-{index}",
        grid_path=Path("grid.nc"),
        km_centres=np.asarray([0.25]),
        km_edge_start=np.asarray([0.125]),
        km_edge_end=np.asarray([0.375]),
        statistics=("mean",),
        component_names=(),
        values=np.asarray([[value]], dtype=np.float64),
        segment_width_m=None,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("variable", "canonical_name"),
    (("h", "water_depth"), ("bed_level", "bed_level")),
)
def test_river_km_output_paths_use_canonical_variable_names(
    tmp_path: Path,
    variable: str,
    canonical_name: str,
) -> None:
    layout = OutputLayout(tmp_path / "output")
    entry = TimeEntry(
        time_index=0,
        subrun=0,
        local_index=0,
        time=datetime(2021, 1, 1, tzinfo=UTC),
        morph_time=None,
        corrupt_partitions=(),
    )
    cache_path = river_km_statistics_output_path(
        layout,
        "region",
        "250m",
        variable,
        QuantityRequest(variable),
        entry,
    )
    figure_path = _line_path(
        layout,
        "series",
        "simulation",
        "region",
        "250m",
        variable,
        "profile",
        "mean",
        "value",
        None,
        0,
    )

    assert cache_path.parents[1].name == canonical_name
    assert figure_path.parents[2].name == canonical_name
    assert figure_path.name == "all_times_l01.png"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("limit_index", "expected_suffix"),
    ((0, "_l01.png"), (9, "_l10.png")),
)
def test_line_path_zero_pads_limit_index(
    tmp_path: Path,
    limit_index: int,
    expected_suffix: str,
) -> None:
    path = _line_path(
        OutputLayout(tmp_path),
        "simulation",
        "simulation",
        "region",
        "250m",
        "bed_level",
        "profile",
        "mean",
        "simulation",
        None,
        limit_index,
    )

    assert path.name.endswith(expected_suffix)


@pytest.mark.unit
def test_profile_template_clears_overlays_and_rescales_between_frames() -> None:
    x = np.array([0.0, 1.0, 2.0])
    view = LineView.create(
        x,
        np.array([[1.0, 2.0, 3.0]]),
        ("model",),
        display="lines",
        figure_config=FigureConfig(),
        x_label="x",
        y_label="y",
        title="first",
        show_legend=True,
        y_limits=(0.0, 20.0),
    )
    try:
        lines_before_overlay = list(view.axes.lines)
        view.axes.plot([0.0, 2.0], [10.0, 12.0], label="measured")
        view.axes.legend()
        overlay_lines = [
            line for line in view.axes.lines if line not in lines_before_overlay
        ]
        _clear_profile_overlays(view, overlay_lines, None, (0.0, 20.0))
        assert list(view.axes.lines) == list(view.artists)
        assert view.axes.get_legend() is None
        assert view.axes.get_autoscalex_on()
        assert not view.axes.get_autoscaley_on()

        view.update(x, np.array([[4.0, 5.0, 6.0]]), title="second")
        assert view.axes.get_title() == "second"
        assert view.axes.get_xlim()[1] >= 2.0
        assert view.axes.get_ylim() == (0.0, 20.0)
    finally:
        plt.close(view.figure)

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
def test_width_multiplication_uses_area_weighted_mean() -> None:
    analysis = RiverKmStatisticsConfig.model_validate(
        {
            "variables": ["bed_level"],
            "regions": [{"name": "region", "polygon": "region.shp"}],
            "river_km": {"file": "river.csv", "sets": {"250m": [0.125, 0.375]}},
            "statistics": ["mean"],
            "multiply_by_width": "flow",
        }
    )
    frame = StatisticCacheFrame(
        time=datetime(2021, 1, 1, tzinfo=UTC),
        morph_time=None,
        path=Path("statistics.nc"),
        fingerprint="fingerprint",
        grid_path=Path("grid.nc"),
        km_centres=np.asarray([0.25]),
        km_edge_start=np.asarray([0.125]),
        km_edge_end=np.asarray([0.375]),
        statistics=("mean", "mean_area_weighted"),
        component_names=(),
        values=np.asarray([[2.0, 3.0]]),
        segment_width_m=np.asarray([4.0]),
    )

    np.testing.assert_array_equal(
        statistic_values(frame, "mean", analysis),
        [12.0],
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("time_matching", "expected_frame_index"),
    (("reference", 1), ("own", 2)),
)
def test_simulation_time_comparison_matches_with_tolerance_and_uses_matched_baseline(
    time_matching: str,
    expected_frame_index: int,
) -> None:
    base = datetime(2021, 1, 1, tzinfo=UTC)
    current_frames = (
        _mean_frame(0, base, 9.0),
        _mean_frame(1, base + timedelta(days=1), 2.0),
        _mean_frame(2, base + timedelta(days=2), 5.0),
    )
    reference_frames = (
        _mean_frame(10, base + timedelta(days=1, seconds=1), 1.0),
        _mean_frame(11, base + timedelta(days=2, seconds=1), 3.0),
    )
    analysis = _statistics_test_config(
        time_matching=time_matching,
        times=TimesConfig(
            at="all",
            tolerance=timedelta(seconds=2),
        ),
    )
    project = ProjectConfig.model_validate(
        {
            "simulations": [
                {"path": "current", "label": "current"},
                {"path": "reference", "label": "reference"},
            ],
            "reference_simulation": "reference",
            "analyses": [analysis.model_dump(mode="python")],
        }
    )
    messages: list[str] = []

    compared = _comparison_values(
        project.simulations[0],
        project,
        analysis,
        current_frames,
        tuple(statistic_values(frame, "mean", analysis) for frame in current_frames),
        reference_frames,
        "mean",
        "difference_simulation_time",
        report=messages.append,
    )

    assert len(compared) == 1
    result_frame, result_values, fingerprints = compared[0]
    expected_frames = (
        reference_frames if time_matching == "reference" else current_frames
    )
    assert result_frame == expected_frames[expected_frame_index]
    np.testing.assert_array_equal(result_values, [1.0])
    assert len(fingerprints) == 2
    assert any("2021-01-01T00:00:00+00:00" in message for message in messages)


@pytest.mark.unit
def test_region_difference_matches_on_morphological_time_axis() -> None:
    morph_origin = datetime(2021, 1, 1, tzinfo=UTC)
    analysis = _statistics_test_config(
        times=TimesConfig(
            at="all",
            axis="morphological",
            tolerance=timedelta(seconds=2),
        )
    )
    left = _mean_frame(
        0,
        morph_origin + timedelta(days=1),
        3.0,
        morph_time=morph_origin,
    )
    right = _mean_frame(
        1,
        morph_origin + timedelta(days=5),
        1.0,
        morph_time=morph_origin + timedelta(seconds=1),
    )
    messages: list[str] = []

    frames, values = _source_statistic_values(
        (left,),
        (right,),
        "mean",
        analysis,
        report=messages.append,
        context="region difference",
    )

    assert frames == (left,)
    np.testing.assert_array_equal(values[0], [2.0])
    assert not messages


@pytest.mark.unit
def test_region_difference_reports_times_outside_matching_tolerance() -> None:
    morph_origin = datetime(2021, 1, 1, tzinfo=UTC)
    analysis = _statistics_test_config(
        times=TimesConfig(
            at="all",
            axis="morphological",
            tolerance=timedelta(0),
        )
    )
    left = _mean_frame(0, morph_origin, 3.0, morph_time=morph_origin)
    right = _mean_frame(
        1,
        morph_origin + timedelta(days=1),
        1.0,
        morph_time=morph_origin + timedelta(seconds=1),
    )
    messages: list[str] = []

    with pytest.raises(RiverKmStatisticsPlotError, match="matching output time"):
        _source_statistic_values(
            (left,),
            (right,),
            "mean",
            analysis,
            report=messages.append,
            context="region difference",
        )

    assert any("V-TIME-010" in message for message in messages)
    assert any(
        "morphological=2021-01-01T00:00:01+00:00" in message for message in messages
    )


@pytest.mark.unit
def test_plot_axis_times_uses_morphological_time_for_time_series_and_integrals() -> (
    None
):
    analysis = RiverKmStatisticsConfig.model_validate(
        {
            "variables": ["bed_level"],
            "regions": [{"name": "region", "polygon": "region.shp"}],
            "river_km": {"file": "river.csv", "sets": {"250m": [0.125, 0.375]}},
            "statistics": ["mean"],
            "times": {"axis": "morphological"},
        }
    )
    hydro_start = datetime(2021, 1, 1, tzinfo=UTC)
    morph_start = hydro_start + timedelta(days=1)
    frames = tuple(
        StatisticCacheFrame(
            time=hydro_start + timedelta(seconds=index),
            morph_time=morph_start + timedelta(seconds=10 * index),
            path=Path(f"statistics-{index}.nc"),
            fingerprint=f"fingerprint-{index}",
            grid_path=Path("grid.nc"),
            km_centres=np.asarray([0.25]),
            km_edge_start=np.asarray([0.125]),
            km_edge_end=np.asarray([0.375]),
            statistics=("mean",),
            component_names=(),
            values=np.asarray([[float(index)]]),
            segment_width_m=None,
        )
        for index in range(2)
    )

    axis_times = _plot_axis_times(frames, analysis)

    assert axis_times == (
        morph_start,
        morph_start + timedelta(seconds=10),
    )
    np.testing.assert_array_equal(
        compute_cumulative([2.0, 3.0], axis_times),
        [0.0, 20.0],
    )


@pytest.mark.unit
def test_plot_axis_times_rejects_missing_morphological_time() -> None:
    analysis = RiverKmStatisticsConfig.model_validate(
        {
            "variables": ["bed_level"],
            "regions": [{"name": "region", "polygon": "region.shp"}],
            "river_km": {"file": "river.csv", "sets": {"250m": [0.125, 0.375]}},
            "statistics": ["mean"],
            "times": {"axis": "morphological"},
        }
    )
    frame = StatisticCacheFrame(
        time=datetime(2021, 1, 1, tzinfo=UTC),
        morph_time=None,
        path=Path("statistics.nc"),
        fingerprint="fingerprint",
        grid_path=Path("grid.nc"),
        km_centres=np.asarray([0.25]),
        km_edge_start=np.asarray([0.125]),
        km_edge_end=np.asarray([0.375]),
        statistics=("mean",),
        component_names=(),
        values=np.asarray([[1.0]]),
        segment_width_m=None,
    )

    with pytest.raises(RiverKmStatisticsPlotError, match="morphological times"):
        _plot_axis_times((frame,), analysis)


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
