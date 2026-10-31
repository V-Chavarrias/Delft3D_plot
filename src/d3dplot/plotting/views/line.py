# SPDX-License-Identifier: GPL-3.0-or-later
"""Reusable line and stacked-area plot views."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, Literal, cast

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.collections import PolyCollection
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

from d3dplot.config.figure import AxisLimit, FigureConfig

_Array = Any
np: Any = import_module("numpy")


@dataclass(slots=True)
class LineView:
    """A line-plot template with updateable line or stacked-area data."""

    figure: Figure
    axes: Axes
    display: Literal["lines", "stacked_area"]
    artists: tuple[Line2D | PolyCollection, ...]
    n_points: int

    @classmethod
    def create(
        cls,
        x: _Array,
        values: _Array,
        labels: tuple[str, ...],
        *,
        display: Literal["lines", "stacked_area"],
        figure_config: FigureConfig,
        x_label: str,
        y_label: str,
        title: str,
        show_legend: bool = True,
        x_limits: AxisLimit | None = None,
        y_limits: AxisLimit | None = None,
    ) -> LineView:
        x_values, y_values = _validated_data(x, values, len(labels))
        figure = plt.figure()
        axes = figure.add_subplot(1, 1, 1)
        artists: tuple[Line2D | PolyCollection, ...]
        if display == "lines":
            artists = tuple(
                axes.plot(
                    x_values,
                    row,
                    label=label,
                    marker="o" if figure_config.markers else None,
                    drawstyle=("steps-post" if figure_config.staircase else "default"),
                )[0]
                for row, label in zip(y_values, labels, strict=True)
            )
        else:
            artists = cast(
                tuple[Line2D | PolyCollection, ...],
                tuple(
                    axes.stackplot(
                        x_values,
                        y_values,
                        labels=labels,
                    )
                ),
            )
        axes.set_xlabel(x_label)
        axes.set_ylabel(y_label)
        axes.set_title(title)
        if labels and show_legend:
            axes.legend()
        if x_limits is not None:
            axes.set_xlim(*x_limits)
        if y_limits is not None:
            axes.set_ylim(*y_limits)
        if figure_config.x_reversed:
            axes.invert_xaxis()
        figure.tight_layout()
        return cls(figure, axes, display, artists, int(x_values.size))

    def update(
        self,
        x: _Array,
        values: _Array,
        *,
        title: str | None = None,
    ) -> None:
        """Replace plotted values without rebuilding the axes or artists."""
        x_values, y_values = _validated_data(
            x,
            values,
            len(self.artists),
            expected_points=self.n_points,
        )
        if self.display == "lines":
            for artist, row in zip(self.artists, y_values, strict=True):
                if not isinstance(artist, Line2D):
                    raise ValueError("LineView has an invalid line artist.")
                artist.set_data(x_values, row)
        else:
            cumulative = np.cumsum(y_values, axis=0)
            lower = np.concatenate(
                (np.zeros((1, self.n_points), dtype=np.float64), cumulative[:-1]),
                axis=0,
            )
            for index, artist in enumerate(self.artists):
                if not isinstance(artist, PolyCollection):
                    raise ValueError("LineView has an invalid area artist.")
                upper = cumulative[index]
                vertices = np.concatenate(
                    (
                        np.column_stack((x_values, lower[index])),
                        np.column_stack((x_values[::-1], upper[::-1])),
                    ),
                    axis=0,
                )
                artist.set_verts((vertices,))
        self.axes.relim()
        self.axes.autoscale_view()
        if title is not None:
            self.axes.set_title(title)


def _validated_data(
    x: _Array,
    values: _Array,
    n_series: int,
    *,
    expected_points: int | None = None,
) -> tuple[_Array, _Array]:
    x_values = np.asarray(x, dtype=np.float64)
    y_values = np.asarray(values, dtype=np.float64)
    if x_values.ndim != 1 or x_values.size == 0:
        raise ValueError("LineView x values must be a non-empty one-dimensional array.")
    if y_values.ndim == 1:
        y_values = y_values[np.newaxis, :]
    if (
        y_values.ndim != 2
        or y_values.shape[0] != n_series
        or y_values.shape[1] != x_values.size
        or (expected_points is not None and x_values.size != expected_points)
    ):
        raise ValueError(
            "LineView values must have shape (series, points) matching x and labels."
        )
    return x_values, y_values
