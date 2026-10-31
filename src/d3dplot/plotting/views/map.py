# SPDX-License-Identifier: GPL-3.0-or-later
"""Draw-once face-value map views."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, Literal

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.cm import ScalarMappable
from matplotlib.collections import PolyCollection
from matplotlib.colorbar import Colorbar
from matplotlib.figure import Figure
from matplotlib.image import AxesImage

from d3dplot.config.figure import Extent, FigureConfig, MapViewConfig
from d3dplot.plotting.views.decorations import MapDecorations
from d3dplot.plotting.views.extents import (
    ExtentWindow,
    UgridGrid,
    valid_face_nodes,
)

_Array = Any
np: Any = import_module("numpy")


class MapViewError(RuntimeError):
    """Map data or inspected geometry cannot be drawn."""


@dataclass(slots=True)
class MapView:
    """Map template whose face values and title can be updated per image."""

    figure: Figure
    axes: Axes
    colorbar: Colorbar
    mappable: ScalarMappable
    measurement_axes: Axes | None
    measurement_artist: Any
    measurement_kind: str | None
    window: ExtentWindow
    grid: UgridGrid
    render_mode: str
    raster_face_index: _Array | None
    raster_x: _Array | None
    raster_y: _Array | None
    raster_resolution: float | None
    visible_faces: _Array
    n_faces: int

    @classmethod
    def create(
        cls,
        grid: UgridGrid,
        window: ExtentWindow,
        values: _Array,
        *,
        map_config: MapViewConfig,
        figure_config: FigureConfig,
        colormap: str,
        colorbar_label: str,
        x_label: str | None = None,
        y_label: str | None = None,
        initial_limits: tuple[float | None, float | None] | None = None,
        decorations: MapDecorations | None = None,
        measurement_panel: bool = False,
    ) -> MapView:
        if window.faces.size == 0:
            raise MapViewError(f"Extent {window.name!r} contains no visible faces.")
        values = _as_face_values(values, grid.n_face)
        render_mode = _render_mode(
            map_config.render,
            int(window.faces.size),
            window.bounds,
            figure_config,
        )
        try:
            color_map = plt.get_cmap(colormap)
        except ValueError as error:
            raise MapViewError(f"Unknown Matplotlib colormap {colormap!r}.") from error

        polygons = (
            _face_polygons(grid, window.faces) if render_mode == "polygons" else None
        )
        raster_data: (
            tuple[
                _Array, _Array, _Array, float, _Array, tuple[float, float, float, float]
            ]
            | None
        ) = None
        if render_mode != "polygons":
            x_coordinates, y_coordinates, face_indices, resolution = _raster_grid(
                grid,
                window.bounds,
                figure_config,
            )
            image_values = _raster_values(values, face_indices)
            image_extent = _image_extent(
                x_coordinates,
                y_coordinates,
                resolution,
            )
            raster_data = (
                x_coordinates,
                y_coordinates,
                face_indices,
                resolution,
                image_values,
                image_extent,
            )
        figure = plt.figure()
        if measurement_panel:
            axes = figure.add_subplot(1, 2, 1)
            measurement_axes: Axes | None = figure.add_subplot(1, 2, 2)
            figure.subplots_adjust(
                left=0.06,
                right=0.86,
                bottom=0.14,
                top=0.92,
                wspace=0.12,
            )
        else:
            axes = figure.add_subplot(1, 1, 1)
            measurement_axes = None
            figure.subplots_adjust(left=0.10, right=0.86, bottom=0.14, top=0.92)
        axes.set_xlim(window.bounds[0], window.bounds[1])
        axes.set_ylim(window.bounds[2], window.bounds[3])
        if x_label is not None:
            axes.set_xlabel(x_label)
        if y_label is not None:
            axes.set_ylabel(y_label)
        if decorations is not None:
            decorations.draw_background(axes, window.bounds)
            axes.set_xlim(window.bounds[0], window.bounds[1])
            axes.set_ylim(window.bounds[2], window.bounds[3])
        if render_mode == "polygons":
            if polygons is None:
                raise MapViewError("Polygon render mode has no visible polygons.")
            collection = PolyCollection(
                polygons,
                cmap=color_map,
                edgecolors="none",
                linewidths=0,
            )
            collection.set_array(values[window.faces])
            axes.add_collection(collection)
            mappable: ScalarMappable = collection
            raster_face_index: _Array | None = None
            raster_x: _Array | None = None
            raster_y: _Array | None = None
            raster_resolution: float | None = None
        else:
            if raster_data is None:
                raise MapViewError("Raster render mode has no raster grid.")
            (
                raster_x,
                raster_y,
                raster_face_index,
                raster_resolution,
                image_values,
                image_extent,
            ) = raster_data
            image: AxesImage = axes.imshow(
                image_values,
                extent=image_extent,
                origin="upper",
                interpolation="nearest",
                cmap=color_map,
            )
            mappable = image
        if decorations is not None:
            decorations.draw_foreground(axes, window.bounds)
        axes.set_xlim(window.bounds[0], window.bounds[1])
        axes.set_ylim(window.bounds[2], window.bounds[3])
        axes.set_aspect(map_config.aspect)
        colorbar_axes = (
            (axes, measurement_axes) if measurement_axes is not None else axes
        )
        colorbar = figure.colorbar(mappable, ax=colorbar_axes)
        colorbar.set_label(colorbar_label)
        view = cls(
            figure=figure,
            axes=axes,
            colorbar=colorbar,
            mappable=mappable,
            measurement_axes=measurement_axes,
            measurement_artist=None,
            measurement_kind=None,
            window=window,
            grid=grid,
            render_mode=render_mode,
            raster_face_index=raster_face_index,
            raster_x=raster_x,
            raster_y=raster_y,
            raster_resolution=raster_resolution,
            visible_faces=np.asarray(window.faces, dtype=np.int64),
            n_faces=grid.n_face,
        )
        view.update(values, limits=initial_limits)
        return view

    def update_measurements(
        self,
        measurement: Any,
        *,
        title: str,
        limits: tuple[float | None, float | None] | None,
        edge_color: str,
        aspect: Literal["auto", "equal"],
    ) -> None:
        """Update the measurement panel without recreating the model map."""
        axes = self.measurement_axes
        if axes is None:
            if measurement is not None:
                raise MapViewError("Measurement data was provided without a panel.")
            return
        if measurement is None:
            if self.measurement_artist is not None:
                self.measurement_artist.remove()
                self.measurement_artist = None
                self.measurement_kind = None
            axes.set_title(title)
            axes.set_xlim(self.window.bounds[0], self.window.bounds[1])
            axes.set_ylim(self.window.bounds[2], self.window.bounds[3])
            axes.set_aspect(aspect)
            return

        kind = measurement.kind
        if self.measurement_kind != kind:
            if self.measurement_artist is not None:
                self.measurement_artist.remove()
            self.measurement_artist = None
            self.measurement_kind = kind
        if kind == "raster":
            image_extent = (
                measurement.bounds[0],
                measurement.bounds[2],
                measurement.bounds[1],
                measurement.bounds[3],
            )
            if not isinstance(self.measurement_artist, AxesImage):
                self.measurement_artist = axes.imshow(
                    measurement.values,
                    extent=image_extent,
                    origin="upper",
                    interpolation="nearest",
                    cmap=self.mappable.get_cmap(),
                )
            else:
                self.measurement_artist.set_data(measurement.values)
                self.measurement_artist.set_extent(image_extent)
            self.measurement_artist.set_clim(
                *_resolved_limits(measurement.values, limits)
            )
        elif kind == "polygons":
            polygons, values = _measurement_polygons(measurement)
            if not isinstance(self.measurement_artist, PolyCollection):
                self.measurement_artist = PolyCollection(
                    polygons,
                    cmap=self.mappable.get_cmap(),
                    edgecolors=edge_color,
                    linewidths=0.4,
                )
                axes.add_collection(self.measurement_artist)
            else:
                self.measurement_artist.set_verts(polygons)
            self.measurement_artist.set_array(values)
            self.measurement_artist.set_clim(*_resolved_limits(values, limits))
        else:
            raise MapViewError(f"Unknown measurement render kind {kind!r}.")
        axes.set_title(title)
        axes.set_xlim(self.window.bounds[0], self.window.bounds[1])
        axes.set_ylim(self.window.bounds[2], self.window.bounds[3])
        axes.set_aspect(aspect)

    def update(
        self,
        values: _Array,
        *,
        title: str | None = None,
        limits: tuple[float | None, float | None] | None = None,
    ) -> None:
        """Update cached face values, colour limits, and title."""
        face_values = _as_face_values(values, self.n_faces)
        if self.render_mode == "polygons":
            self.mappable.set_array(face_values[self.visible_faces])
        else:
            if self.raster_face_index is None:
                raise MapViewError("Raster render mode has no face-index raster.")
            image_values = _raster_values(face_values, self.raster_face_index)
            if not isinstance(self.mappable, AxesImage):
                raise MapViewError("Raster render mode has an invalid image artist.")
            self.mappable.set_data(image_values)
        lower, upper = _resolved_limits(face_values, limits)
        self.mappable.set_clim(lower, upper)
        if title is not None:
            self.axes.set_title(title)


def _as_face_values(values: _Array, n_faces: int) -> _Array:
    result = np.asarray(values, dtype=np.float64)
    if result.shape != (n_faces,):
        raise MapViewError(
            f"Expected exactly one value for each of {n_faces} faces; "
            f"received shape {result.shape}."
        )
    return result


def _face_polygons(grid: UgridGrid, face_indices: _Array) -> list[_Array]:
    connectivity = np.asarray(grid.face_node_connectivity, dtype=np.int64)
    nodes = np.asarray(grid.node_coordinates, dtype=np.float64)
    try:
        valid_nodes = valid_face_nodes(connectivity, grid.fill_value)
    except ValueError as error:
        raise MapViewError(str(error)) from error
    polygons: list[_Array] = []
    for face_index in face_indices:
        node_indices = connectivity[face_index][valid_nodes[face_index]]
        if node_indices.size < 3:
            raise MapViewError(f"Grid face {face_index} has invalid node connectivity.")
        polygons.append(nodes[node_indices])
    return polygons


def face_polygons(grid: UgridGrid, face_indices: _Array) -> list[_Array]:
    """Return coordinates for selected grid faces for map overlays."""
    return _face_polygons(grid, np.asarray(face_indices, dtype=np.int64))


def _measurement_polygons(measurement: Any) -> tuple[list[_Array], _Array]:
    polygons: list[_Array] = []
    values: list[float] = []
    for geometry, value in zip(
        measurement.polygons,
        measurement.values,
        strict=True,
    ):
        for polygon in _polygon_parts(geometry):
            coordinates = np.asarray(polygon.exterior.coords, dtype=np.float64)
            if coordinates.shape[0] < 4:
                continue
            polygons.append(coordinates)
            values.append(float(value))
    return polygons, np.asarray(values, dtype=np.float64)


def _polygon_parts(geometry: Any) -> tuple[Any, ...]:
    if geometry is None or geometry.is_empty:
        return ()
    if geometry.geom_type == "Polygon":
        return (geometry,)
    if geometry.geom_type == "MultiPolygon":
        return tuple(geometry.geoms)
    if geometry.geom_type == "GeometryCollection":
        return tuple(
            polygon for part in geometry.geoms for polygon in _polygon_parts(part)
        )
    return ()


def _render_mode(
    configured: str,
    visible_faces: int,
    bounds: Extent,
    figure_config: FigureConfig,
) -> str:
    if configured != "auto":
        return configured
    width_pixels = max(
        1,
        int(figure_config.size_cm[0] / 2.54 * figure_config.dpi * 0.75),
    )
    height_pixels = max(
        1,
        int(figure_config.size_cm[1] / 2.54 * figure_config.dpi * 0.75),
    )
    return "raster" if visible_faces > width_pixels * height_pixels else "polygons"


def _raster_grid(
    grid: UgridGrid,
    bounds: Extent,
    figure_config: FigureConfig,
) -> tuple[_Array, _Array, _Array, float]:
    xmin, xmax, ymin, ymax = bounds
    width_pixels = max(
        1,
        int(figure_config.size_cm[0] / 2.54 * figure_config.dpi * 0.75),
    )
    height_pixels = max(
        1,
        int(figure_config.size_cm[1] / 2.54 * figure_config.dpi * 0.75),
    )
    resolution = max(
        (xmax - xmin) / width_pixels,
        (ymax - ymin) / height_pixels,
    )
    x, y, face_index = grid.rasterize(
        resolution=resolution,
        bounds=(xmin, ymin, xmax, ymax),
    )
    return (
        np.asarray(x, dtype=np.float64),
        np.asarray(y, dtype=np.float64),
        np.asarray(face_index, dtype=np.int64),
        resolution,
    )


def _raster_values(values: _Array, face_index: _Array) -> _Array:
    indices = np.asarray(face_index, dtype=np.int64)
    raster = np.full(indices.shape, np.nan, dtype=np.float64)
    valid = (indices >= 0) & (indices < values.size)
    raster[valid] = values[indices[valid]]
    return raster


def _image_extent(
    x: _Array,
    y: _Array,
    resolution: float,
) -> tuple[float, float, float, float]:
    if x.size == 0 or y.size == 0:
        raise MapViewError("xugrid produced an empty raster grid.")
    return (
        float(x[0] - resolution / 2),
        float(x[-1] + resolution / 2),
        float(y[-1] - resolution / 2),
        float(y[0] + resolution / 2),
    )


def _resolved_limits(
    values: _Array,
    limits: tuple[float | None, float | None] | None,
) -> tuple[float, float]:
    if limits is None or limits == (None, None):
        finite = values[np.isfinite(values)]
        if finite.size == 0:
            return 0.0, 1.0
        lower = float(np.min(finite))
        upper = float(np.max(finite))
    else:
        configured_lower, configured_upper = limits
        finite = values[np.isfinite(values)]
        lower = (
            float(configured_lower)
            if configured_lower is not None
            else (float(np.min(finite)) if finite.size else 0.0)
        )
        upper = (
            float(configured_upper)
            if configured_upper is not None
            else (float(np.max(finite)) if finite.size else 1.0)
        )
    if lower == upper:
        padding = max(abs(lower) * 1.0e-6, 1.0e-12)
        return lower - padding, upper + padding
    return lower, upper
