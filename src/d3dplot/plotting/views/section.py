# SPDX-License-Identifier: GPL-3.0-or-later
"""Reusable distance by elevation section views with draw-once polygons."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.collections import PolyCollection
from matplotlib.colorbar import Colorbar
from matplotlib.figure import Figure

from d3dplot.plotting.views.space_time import _resolved_limits

_Array = Any
np: Any = import_module("numpy")


@dataclass(slots=True)
class SectionView:
    """Crossing-by-layer cells in distance and elevation, updated in place.

    Each crossing is a vertical strip from ``x_start`` to ``x_stop``; each layer
    is the cell between two vertical interfaces of that crossing's face.
    """

    figure: Figure
    axes: Axes
    colorbar: Colorbar
    collection: PolyCollection
    x_start: _Array
    x_stop: _Array
    layers: int

    @classmethod
    def create(
        cls,
        x_start: _Array,
        x_stop: _Array,
        interfaces: _Array,
        values: _Array,
        *,
        colormap: str,
        colorbar_label: str,
        x_label: str,
        y_label: str,
        title: str,
        limits: tuple[float | None, float | None] | None = None,
    ) -> SectionView:
        starts, stops, edges, data = _validated(x_start, x_stop, interfaces, values)
        figure = plt.figure()
        axes = figure.add_subplot(1, 1, 1)
        collection = PolyCollection(
            _polygons(starts, stops, edges), cmap=colormap, edgecolors="none"
        )
        collection.set_array(np.ma.masked_invalid(data).ravel())
        axes.add_collection(collection)
        colorbar = figure.colorbar(collection, ax=axes)
        colorbar.set_label(colorbar_label)
        axes.set_xlabel(x_label)
        axes.set_ylabel(y_label)
        axes.set_title(title)
        axes.set_xlim(float(np.min(starts)), float(np.max(stops)))
        axes.set_ylim(*_vertical_range(edges))
        collection.set_clim(*_resolved_limits(data, limits))
        figure.tight_layout()
        return cls(
            figure,
            axes,
            colorbar,
            collection,
            starts,
            stops,
            edges.shape[1] - 1,
        )

    def update(
        self,
        interfaces: _Array,
        values: _Array,
        *,
        title: str | None = None,
        limits: tuple[float | None, float | None] | None = None,
    ) -> None:
        """Replace the interfaces and values of the drawn section."""
        edges, data = _validated_update(
            self.x_start.size, self.layers, interfaces, values
        )
        self.collection.set_verts(_polygons(self.x_start, self.x_stop, edges))
        self.collection.set_array(np.ma.masked_invalid(data).ravel())
        self.collection.set_clim(*_resolved_limits(data, limits))
        self.axes.set_ylim(*_vertical_range(edges))
        if title is not None:
            self.axes.set_title(title)


def _validated(
    x_start: _Array,
    x_stop: _Array,
    interfaces: _Array,
    values: _Array,
) -> tuple[_Array, _Array, _Array, _Array]:
    starts = np.asarray(x_start, dtype=np.float64)
    stops = np.asarray(x_stop, dtype=np.float64)
    if starts.ndim != 1 or starts.shape != stops.shape or starts.size == 0:
        raise ValueError("SectionView requires one start and stop per crossing.")
    edges = np.asarray(interfaces, dtype=np.float64)
    if edges.ndim != 2 or edges.shape[0] != starts.size or edges.shape[1] < 2:
        raise ValueError(
            "SectionView interfaces must have shape (crossings, interfaces >= 2)."
        )
    data = np.asarray(values, dtype=np.float64)
    if data.shape != (starts.size, edges.shape[1] - 1):
        raise ValueError(
            "SectionView values must have shape (crossings, layers) = "
            f"{(starts.size, edges.shape[1] - 1)}, received {data.shape}."
        )
    return starts, stops, edges, data


def _validated_update(
    crossings: int,
    layers: int,
    interfaces: _Array,
    values: _Array,
) -> tuple[_Array, _Array]:
    edges = np.asarray(interfaces, dtype=np.float64)
    data = np.asarray(values, dtype=np.float64)
    if edges.shape != (crossings, layers + 1) or data.shape != (crossings, layers):
        raise ValueError(
            "SectionView.update keeps the drawn shape: interfaces "
            f"{(crossings, layers + 1)}, values {(crossings, layers)}; "
            f"received {edges.shape} and {data.shape}."
        )
    return edges, data


def _polygons(starts: _Array, stops: _Array, edges: _Array) -> _Array:
    """Return one quadrilateral per (crossing, layer), crossing-major."""
    lower = np.minimum(edges[:, :-1], edges[:, 1:])
    upper = np.maximum(edges[:, :-1], edges[:, 1:])
    x_left = np.broadcast_to(starts[:, None], lower.shape)
    x_right = np.broadcast_to(stops[:, None], lower.shape)
    corners = np.stack(
        [
            np.stack([x_left, lower], axis=-1),
            np.stack([x_right, lower], axis=-1),
            np.stack([x_right, upper], axis=-1),
            np.stack([x_left, upper], axis=-1),
        ],
        axis=-2,
    )
    return corners.reshape(-1, 4, 2)


def _vertical_range(edges: _Array) -> tuple[float, float]:
    finite = edges[np.isfinite(edges)]
    if finite.size == 0:
        return 0.0, 1.0
    low = float(np.min(finite))
    high = float(np.max(finite))
    if low == high:
        return low - 0.5, high + 0.5
    return low, high
