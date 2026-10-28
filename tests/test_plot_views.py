# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the shared river-kilometre line and space-time views."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import matplotlib.pyplot as plt
import numpy as np
import pytest
from matplotlib import dates as mdates
from matplotlib.figure import Figure

from d3dplot.config.figure import FigureConfig
from d3dplot.plotting.views.extents import valid_face_nodes
from d3dplot.plotting.views.line import LineView
from d3dplot.plotting.views.space_time import SpaceTimeView


def test_face_connectivity_masks_normalized_and_declared_padding() -> None:
    connectivity = np.array([[0, 1, 2, -1], [1, 2, 3, -999]])

    np.testing.assert_array_equal(
        valid_face_nodes(connectivity, fill_value=-999),
        [[True, True, True, False], [True, True, True, False]],
    )
    with pytest.raises(ValueError, match="invalid node indices"):
        valid_face_nodes(np.array([[0, 1, 2, -2]]), fill_value=-999)


def test_line_view_updates_lines_without_replacing_artists() -> None:
    view = LineView.create(
        np.array([1.0, 2.0, 3.0]),
        np.array([[2.0, 3.0, 4.0], [4.0, 3.0, 2.0]]),
        ("one", "two"),
        display="lines",
        figure_config=FigureConfig(),
        x_label="km",
        y_label="value",
        title="initial",
    )
    original_artists = view.artists

    view.update(
        np.array([1.0, 2.0, 3.0]),
        np.array([[3.0, 4.0, 5.0], [5.0, 4.0, 3.0]]),
        title="updated",
    )

    assert view.artists is original_artists
    assert np.array_equal(view.artists[0].get_ydata(), [3.0, 4.0, 5.0])
    assert view.axes.get_title() == "updated"
    plt.close(view.figure)


def test_line_view_stacked_area_updates_component_vertices() -> None:
    view = LineView.create(
        np.array([1.0, 2.0]),
        np.array([[1.0, 2.0], [3.0, 4.0], [2.0, 1.0]]),
        ("sand", "silt", "clay"),
        display="stacked_area",
        figure_config=FigureConfig(),
        x_label="km",
        y_label="value",
        title="fractions",
    )

    view.update(
        np.array([1.0, 2.0]),
        np.array([[2.0, 3.0], [1.0, 2.0], [4.0, 3.0]]),
    )

    assert len(view.artists) == 3
    vertices = view.artists[-1].get_paths()[0].vertices
    assert vertices[:, 1].max() == pytest.approx(8.0)
    plt.close(view.figure)


def test_space_time_view_uses_kilometre_and_time_edges() -> None:
    first = datetime(2024, 1, 1, tzinfo=UTC)
    times = np.array(
        [
            first.timestamp(),
            (first + timedelta(days=1)).timestamp(),
            (first + timedelta(days=3)).timestamp(),
        ]
    )
    view = SpaceTimeView.create(
        np.array([1.0, 2.0, 3.0]),
        times,
        np.array([[1.0, 2.0], [3.0, 4.0]]),
        figure_config=FigureConfig(),
        colormap="viridis",
        colorbar_label="value",
        x_label="km",
        y_label="time",
        title="space-time",
    )

    assert view.mesh.get_array().shape == (2, 2)
    assert np.array_equal(view.x_edges, [1.0, 2.0, 3.0])
    view.update(np.array([[4.0, 3.0], [2.0, 1.0]]))
    assert np.array_equal(view.mesh.get_array(), [4.0, 3.0, 2.0, 1.0])
    plt.close(view.figure)


@pytest.mark.mpl
@pytest.mark.mpl_image_compare
def test_stacked_fraction_profile_image_baseline() -> Figure:
    view = LineView.create(
        np.array([10.0, 10.25, 10.75, 11.0]),
        np.array(
            [
                [0.11, 0.12, 0.13, 0.14],
                [0.25, 0.21, 0.19, 0.18],
                [0.07, 0.08, 0.09, 0.08],
            ]
        ),
        ("fine sand", "medium sand", "coarse sand"),
        display="stacked_area",
        figure_config=FigureConfig(),
        x_label="River kilometre [km]",
        y_label="Layer fraction [-]",
        title="Sediment fractions by river kilometre",
    )
    return view.figure


@pytest.mark.mpl
@pytest.mark.mpl_image_compare
def test_space_time_profile_image_baseline() -> Figure:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    time_centres = np.asarray(
        [mdates.date2num(start + timedelta(days=offset)) for offset in (0, 1, 3)],
        dtype=np.float64,
    )
    time_steps = np.diff(time_centres)
    time_edges = np.concatenate(
        (
            [time_centres[0] - time_steps[0] / 2],
            (time_centres[:-1] + time_centres[1:]) / 2,
            [time_centres[-1] + time_steps[-1] / 2],
        )
    )
    view = SpaceTimeView.create(
        np.array([10.0, 10.25, 10.75, 11.0]),
        time_edges,
        np.array(
            [
                [1.2, 1.4, 1.5],
                [1.3, 1.6, 1.7],
                [1.1, 1.5, 1.8],
            ]
        ),
        figure_config=FigureConfig(),
        colormap="viridis",
        colorbar_label="Mean bed level [m]",
        x_label="River kilometre [km]",
        y_label="Time",
        title="Mean bed level by river kilometre",
        limits=(1.0, 2.0),
    )
    view.axes.yaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d", tz=UTC))
    view.figure.tight_layout()
    return view.figure


def test_plot_views_reject_incompatible_shapes() -> None:
    with pytest.raises(ValueError, match="shape"):
        LineView.create(
            np.array([1.0, 2.0]),
            np.array([[1.0, 2.0, 3.0]]),
            ("one",),
            display="lines",
            figure_config=FigureConfig(),
            x_label="km",
            y_label="value",
            title="invalid",
        )

    with pytest.raises(ValueError, match="shape"):
        SpaceTimeView.create(
            np.array([1.0, 2.0, 3.0]),
            np.array([0.0, 1.0]),
            np.ones((1, 1)),
            figure_config=FigureConfig(),
            colormap="viridis",
            colorbar_label="value",
            x_label="km",
            y_label="time",
            title="invalid",
        )
