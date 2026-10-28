# SPDX-License-Identifier: GPL-3.0-or-later
"""Reusable river-kilometre by time colour plot views."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.collections import QuadMesh
from matplotlib.colorbar import Colorbar
from matplotlib.figure import Figure

from d3dplot.config.figure import FigureConfig

_Array = Any
np: Any = import_module("numpy")


@dataclass(slots=True)
class SpaceTimeView:
    """A km-by-time pcolormesh whose values and colour limits can be updated."""

    figure: Figure
    axes: Axes
    colorbar: Colorbar
    mesh: QuadMesh
    x_edges: _Array
    y_edges: _Array

    @classmethod
    def create(
        cls,
        km_edges: _Array,
        time_edges: _Array,
        values: _Array,
        *,
        figure_config: FigureConfig,
        colormap: str,
        colorbar_label: str,
        x_label: str,
        y_label: str,
        title: str,
        limits: tuple[float | None, float | None] | None = None,
    ) -> SpaceTimeView:
        x_values, y_values, data = _validated_data(km_edges, time_edges, values)
        figure = plt.figure()
        axes = figure.add_subplot(1, 1, 1)
        mesh = axes.pcolormesh(
            x_values,
            y_values,
            np.ma.masked_invalid(data),
            shading="flat",
            cmap=colormap,
        )
        colorbar = figure.colorbar(mesh, ax=axes)
        colorbar.set_label(colorbar_label)
        axes.set_xlabel(x_label)
        axes.set_ylabel(y_label)
        axes.set_title(title)
        mesh.set_clim(*_resolved_limits(data, limits))
        figure.tight_layout()
        return cls(figure, axes, colorbar, mesh, x_values, y_values)

    def update(
        self,
        values: _Array,
        *,
        title: str | None = None,
        limits: tuple[float | None, float | None] | None = None,
    ) -> None:
        """Update cached time-kilometre values and colour limits."""
        data = np.asarray(values, dtype=np.float64)
        expected = (self.y_edges.size - 1, self.x_edges.size - 1)
        if data.shape != expected:
            raise ValueError(
                f"SpaceTimeView expected values with shape {expected}, "
                f"received {data.shape}."
            )
        self.mesh.set_array(np.ma.masked_invalid(data).ravel())
        self.mesh.set_clim(*_resolved_limits(data, limits))
        if title is not None:
            self.axes.set_title(title)


def _validated_data(
    km_edges: _Array,
    time_edges: _Array,
    values: _Array,
) -> tuple[_Array, _Array, _Array]:
    x_values = np.asarray(km_edges, dtype=np.float64)
    y_values = np.asarray(time_edges, dtype=np.float64)
    data = np.asarray(values, dtype=np.float64)
    expected = (y_values.size - 1, x_values.size - 1)
    if (
        x_values.ndim != 1
        or y_values.ndim != 1
        or x_values.size < 2
        or y_values.size < 2
        or data.ndim != 2
        or data.shape != expected
        or np.any(np.diff(x_values) <= 0)
        or np.any(np.diff(y_values) <= 0)
    ):
        raise ValueError(
            "SpaceTimeView requires increasing one-dimensional edges and values "
            "with shape (time intervals, kilometre intervals)."
        )
    return x_values, y_values, data


def _resolved_limits(
    values: _Array,
    limits: tuple[float | None, float | None] | None,
) -> tuple[float, float]:
    finite = values[np.isfinite(values)]
    if limits is None:
        if finite.size == 0:
            return 0.0, 1.0
        low = float(np.min(finite))
        high = float(np.max(finite))
    else:
        low = float(np.min(finite)) if limits[0] is None and finite.size else 0.0
        high = float(np.max(finite)) if limits[1] is None and finite.size else 1.0
        if limits[0] is not None:
            low = limits[0]
        if limits[1] is not None:
            high = limits[1]
    if low == high:
        delta = max(abs(low) * 0.05, 0.5)
        return low - delta, high + delta
    return low, high
