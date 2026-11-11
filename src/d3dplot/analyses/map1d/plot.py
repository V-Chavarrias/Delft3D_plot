# SPDX-License-Identifier: GPL-3.0-or-later
"""map1d plot data transformations and shared-view construction."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

import matplotlib.dates as mdates
import numpy as np

from d3dplot.analyses.map1d.process import Map1DChain, Map1DProcessError
from d3dplot.config.figure import AxisLimit, FigureConfig
from d3dplot.plotting.views.line import LineView
from d3dplot.plotting.views.space_time import SpaceTimeView

Map1DComparison = Literal[
    "value",
    "difference_time",
    "difference_simulation",
    "difference_simulation_time",
    "difference_simulation_relative",
]


def comparison_values(
    values: np.ndarray,
    comparison: Map1DComparison,
    *,
    initial_values: np.ndarray | None = None,
    reference_values: np.ndarray | None = None,
    reference_initial_values: np.ndarray | None = None,
) -> np.ndarray:
    """Apply one map1d comparison to time-by-coordinate values."""
    data = np.asarray(values, dtype=np.float64)
    if data.ndim != 2:
        raise Map1DProcessError("map1d values must have shape (time, coordinate).")
    if comparison == "value":
        return data
    if comparison == "difference_time":
        baseline = data[0] if initial_values is None else np.asarray(initial_values)
        if baseline.shape != data.shape[1:]:
            raise Map1DProcessError("map1d initial values do not match coordinates.")
        return np.asarray(data - baseline[np.newaxis, :], dtype=np.float64)
    if reference_values is None:
        raise Map1DProcessError(
            "map1d simulation differences require reference values."
        )
    reference = np.asarray(reference_values, dtype=np.float64)
    if reference.shape != data.shape:
        raise Map1DProcessError(
            "map1d reference values do not match simulation values."
        )
    if comparison == "difference_simulation":
        return data - reference
    if comparison == "difference_simulation_relative":
        return np.asarray(
            np.divide(
                100.0 * (data - reference),
                reference,
                out=np.full_like(data, np.nan),
                where=reference != 0,
            ),
            dtype=np.float64,
        )
    baseline = data[0] if initial_values is None else np.asarray(initial_values)
    reference_baseline = (
        reference[0]
        if reference_initial_values is None
        else np.asarray(reference_initial_values)
    )
    if baseline.shape != data.shape[1:] or reference_baseline.shape != data.shape[1:]:
        raise Map1DProcessError("map1d comparison baselines do not match coordinates.")
    result: np.ndarray = (data - baseline[np.newaxis, :]) - (
        reference - reference_baseline[np.newaxis, :]
    )
    return result


def create_profile_view(
    chain: Map1DChain,
    values: np.ndarray,
    *,
    variable: str,
    figure_config: FigureConfig,
    title: str,
    x_label: str = "Distance (m)",
    y_label: str = "",
    x_limits: AxisLimit | None = None,
    y_limits: AxisLimit | None = None,
    x_values: np.ndarray | None = None,
) -> LineView:
    """Create a reusable single-profile line view."""
    data = _time_by_coordinate(values, chain)
    return LineView.create(
        chain.offsets_m if x_values is None else x_values,
        data[0],
        (variable,),
        display="lines",
        figure_config=figure_config,
        x_label=x_label,
        y_label=y_label,
        title=title,
        x_limits=x_limits,
        y_limits=y_limits,
    )


def create_all_times_view(
    chain: Map1DChain,
    values: np.ndarray,
    times: tuple[datetime, ...],
    *,
    figure_config: FigureConfig,
    title: str,
    x_label: str = "Distance (m)",
    y_label: str = "",
    x_limits: AxisLimit | None = None,
    y_limits: AxisLimit | None = None,
    x_values: np.ndarray | None = None,
) -> LineView:
    """Create a reusable line view with one series per selected time."""
    data = _time_by_coordinate(values, chain)
    if len(times) != data.shape[0]:
        raise Map1DProcessError("map1d times do not match the value rows.")
    labels = tuple(time.isoformat() for time in times)
    return LineView.create(
        chain.offsets_m if x_values is None else x_values,
        data,
        labels,
        display="lines",
        figure_config=figure_config,
        x_label=x_label,
        y_label=y_label,
        title=title,
        x_limits=x_limits,
        y_limits=y_limits,
    )


def create_space_time_view(
    chain: Map1DChain,
    values: np.ndarray,
    times: tuple[datetime, ...],
    *,
    figure_config: FigureConfig,
    title: str,
    x_label: str = "Distance (m)",
    y_label: str = "Time",
    limits: tuple[float | None, float | None] | None = None,
    x_values: np.ndarray | None = None,
) -> SpaceTimeView:
    """Create a reusable distance-by-time colour view."""
    data = _time_by_coordinate(values, chain)
    if len(times) != data.shape[0] or len(times) < 2:
        raise Map1DProcessError("map1d space-time views need at least two times.")
    x_edges = _centre_edges(chain.offsets_m if x_values is None else x_values)
    y_centres = np.asarray(
        [mdates.date2num(time) for time in times],  # type: ignore[no-untyped-call]
        dtype=np.float64,
    )
    y_edges = _centre_edges(y_centres)
    view = SpaceTimeView.create(
        x_edges,
        y_edges,
        data,
        figure_config=figure_config,
        colormap="viridis",
        colorbar_label="",
        x_label=x_label,
        y_label=y_label,
        title=title,
        limits=limits,
    )
    locator = mdates.AutoDateLocator()  # type: ignore[no-untyped-call]
    view.axes.yaxis.set_major_locator(locator)
    view.axes.yaxis.set_major_formatter(
        mdates.ConciseDateFormatter(locator)  # type: ignore[no-untyped-call]
    )
    return view


def _time_by_coordinate(values: np.ndarray, chain: Map1DChain) -> np.ndarray:
    data = np.asarray(values, dtype=np.float64)
    if data.ndim == 1:
        data = data[np.newaxis, :]
    if data.ndim != 2 or data.shape[1] != chain.indices.size:
        raise Map1DProcessError(
            "map1d values must have one coordinate value per chain point."
        )
    return data


def _centre_edges(values: np.ndarray) -> np.ndarray:
    centres = np.asarray(values, dtype=np.float64)
    if centres.ndim != 1 or centres.size < 2 or np.any(np.diff(centres) <= 0):
        raise Map1DProcessError("map1d centre coordinates must be strictly increasing.")
    inner = (centres[:-1] + centres[1:]) / 2.0
    return np.concatenate(
        (
            [centres[0] - (inner[0] - centres[0])],
            inner,
            [centres[-1] + (centres[-1] - inner[-1])],
        )
    )
