# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare raster and polygon measurements for map2d figures."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any, Literal

import netCDF4

from d3dplot import __version__
from d3dplot.config.measurements import MapMeasurementsConfig, PolygonFilters
from d3dplot.measurements.catalogue import (
    MapMeasurementCatalogue,
    MapMeasurementEntry,
    MapMeasurementError,
)
from d3dplot.simulation.time_utils import timezone_from_cf_units
from d3dplot.store import (
    CACHE_SCHEMA_VERSION,
    OutputLayout,
    OutputWriteGuard,
    atomic_path,
    fingerprint_artifact,
)

_np: Any = import_module("numpy")
_geopandas: Any = import_module("geopandas")
_pandas: Any = import_module("pandas")
_rasterio: Any = import_module("rasterio")
_rasterio_transform: Any = import_module("rasterio.transform")
_rasterio_warp: Any = import_module("rasterio.warp")
_rasterio_windows: Any = import_module("rasterio.windows")
_pyproj: Any = import_module("pyproj")
_shapely: Any = import_module("shapely")


@dataclass(frozen=True, slots=True)
class PreparedMapMeasurement:
    """Prepared measurement values on one map extent and time."""

    kind: Literal["raster", "polygons"]
    values: Any
    bounds: tuple[float, float, float, float]
    mean_time: datetime
    fingerprint: str
    crs: str
    polygons: tuple[Any, ...] = ()
    ids: tuple[str | None, ...] = ()


def prepare_map_measurement(
    catalogue: MapMeasurementCatalogue,
    settings: MapMeasurementsConfig,
    output_time: datetime,
    extent: tuple[float, float, float, float],
    model_crs: str | None,
    layout: OutputLayout,
    guard: OutputWriteGuard,
    *,
    comparison: Literal["value", "difference_time"] = "value",
    baseline: PreparedMapMeasurement | None = None,
    display_scale: float = 1.0,
    diagnostic: Callable[[str], None] | None = None,
) -> PreparedMapMeasurement | None:
    """Select, align, mosaic, filter, and cache measurements for one figure."""
    target_crs = _target_crs(model_crs)
    extent_bounds = _extent_bounds(extent)
    search_bounds = _expand_bounds(
        extent_bounds,
        target_crs,
        settings.spatial_margin_m,
    )
    selected = tuple(
        entry
        for entry in catalogue.entries
        if abs((entry.time - output_time).total_seconds())
        <= settings.time_tolerance.total_seconds()
        and _intersects(
            _bounds_in_crs(entry, target_crs),
            search_bounds,
        )
    )
    if not selected:
        nearest = min(
            catalogue.entries,
            key=lambda entry: abs((entry.time - output_time).total_seconds()),
        )
        message = (
            f"V-MEA-004: no measurement is within {settings.time_tolerance} of "
            f"{output_time.isoformat()} and the requested spatial extent; nearest "
            f"catalogue time is {nearest.time.isoformat()}."
        )
        if diagnostic is not None:
            diagnostic(message)
        return None
    if diagnostic is not None:
        selected_text = ", ".join(
            f"{entry.path} ({entry.time.isoformat()}, factor={entry.factor:g})"
            for entry in selected
        )
        diagnostic(
            f"Selected measurement sources for {output_time.isoformat()}: "
            f"{selected_text}."
        )
    kinds = {entry.kind for entry in selected}
    if len(kinds) != 1:
        raise MapMeasurementError(
            "V-MEA-002",
            f"Measurement time {output_time.isoformat()} selects mixed file types.",
        )
    kind = selected[0].kind
    if comparison == "difference_time" and baseline is None:
        raise MapMeasurementError(
            "V-MEA-002",
            "difference_time measurements require a prepared baseline.",
        )
    fingerprint = fingerprint_artifact(
        kind="map2d_measurement",
        schema_version=CACHE_SCHEMA_VERSION,
        tool_version=__version__,
        algorithm_version="map-measurement-1",
        parameters={
            "comparison": comparison,
            "extent": list(extent),
            "model_crs": target_crs.to_string(),
            "output_time": output_time.astimezone(UTC).isoformat(),
            "settings": settings.model_dump(mode="json"),
            "selected": [
                {
                    "time": entry.time.isoformat(),
                    "path": str(entry.path),
                    "factor": entry.factor,
                    "fingerprint": entry.source_fingerprint,
                }
                for entry in selected
            ],
        },
        inputs=(
            catalogue.fingerprint,
            *(entry.source_fingerprint for entry in selected),
            *((baseline.fingerprint,) if baseline is not None else ()),
        ),
        time=output_time.astimezone(UTC).isoformat(),
    )
    extension = ".nc" if kind == "raster" else ".gpkg"
    cache_path = layout.cache_measurements / "map2d" / f"{fingerprint[:24]}{extension}"
    cached = _read_cache(cache_path, kind, fingerprint)
    if cached is not None:
        if diagnostic is not None:
            diagnostic(f"Reused measurement cache {cache_path}.")
        return scale_map_measurement(cached, display_scale)
    if cache_path.is_file() and diagnostic is not None:
        diagnostic(f"Unreadable measurement cache was rebuilt: {cache_path}")
    if kind == "raster":
        try:
            measurement = _prepare_raster(
                selected,
                target_crs,
                search_bounds,
                settings,
                fingerprint,
                output_time,
            )
        except MapMeasurementError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise MapMeasurementError(
                "V-MEA-001",
                f"Could not prepare raster measurements at {output_time}: {error}",
            ) from error
    else:
        try:
            measurement = _prepare_polygons(
                selected,
                target_crs,
                extent,
                settings,
                fingerprint,
                output_time,
            )
        except MapMeasurementError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise MapMeasurementError(
                "V-MEA-001",
                f"Could not prepare polygon measurements at {output_time}: {error}",
            ) from error
    if comparison == "difference_time":
        if baseline is None or baseline.kind != measurement.kind:
            raise MapMeasurementError(
                "V-MEA-002",
                "Measurement type changed between the baseline and plot time.",
            )
        measurement = _difference(measurement, baseline, fingerprint)
    if measurement.kind == "raster":
        _write_raster_cache(cache_path, measurement, fingerprint, guard)
    else:
        _write_polygon_cache_from_arrays(cache_path, measurement, fingerprint, guard)
    return scale_map_measurement(measurement, display_scale)


def scale_map_measurement(
    measurement: PreparedMapMeasurement | None,
    scale: float,
) -> PreparedMapMeasurement | None:
    """Scale cached values for display without changing their cache identity."""
    if measurement is None or scale == 1.0:
        return measurement
    return replace(
        measurement,
        values=_np.asarray(measurement.values, dtype=_np.float64) * scale,
    )


def measurement_range(
    measurement: PreparedMapMeasurement | None,
) -> tuple[float, float] | None:
    """Return finite measurement limits, or ``None`` for an empty selection."""
    if measurement is None:
        return None
    values = _np.asarray(measurement.values, dtype=_np.float64)
    finite = values[_np.isfinite(values)]
    if finite.size == 0:
        return None
    minimum = float(_np.min(finite))
    maximum = float(_np.max(finite))
    if minimum == maximum:
        padding = max(abs(minimum) * 0.01, 1.0)
        return minimum - padding, maximum + padding
    return minimum, maximum


def _target_crs(
    model_crs: str | None,
) -> Any:
    if model_crs is None:
        raise MapMeasurementError(
            "V-MEA-003",
            "Measurement plotting requires a model CRS; configure project.crs "
            "when the grid has no CRS.",
        )
    try:
        return _pyproj.CRS.from_user_input(model_crs)
    except (_pyproj.exceptions.CRSError, TypeError, ValueError) as error:
        raise MapMeasurementError(
            "V-MEA-003",
            f"Invalid model CRS for measurement plotting: {model_crs!r}.",
        ) from error


def _expand_bounds(
    bounds: tuple[float, float, float, float],
    crs: Any,
    margin_m: float,
) -> tuple[float, float, float, float]:
    if margin_m == 0:
        return bounds
    if crs.is_projected:
        axis = crs.axis_info[0]
        unit_factor = float(axis.unit_conversion_factor or 1.0)
        margin = margin_m / unit_factor
        return (
            bounds[0] - margin,
            bounds[1] - margin,
            bounds[2] + margin,
            bounds[3] + margin,
        )
    if crs.is_geographic:
        center_x = (bounds[0] + bounds[2]) / 2.0
        center_y = (bounds[1] + bounds[3]) / 2.0
        geod = crs.get_geod()
        east, _, _ = geod.fwd(center_x, center_y, 90, margin_m)
        north, north_y, _ = geod.fwd(center_x, center_y, 0, margin_m)
        dx = abs(east - center_x)
        dy = abs(north_y - center_y)
        return bounds[0] - dx, bounds[1] - dy, bounds[2] + dx, bounds[3] + dy
    raise MapMeasurementError(
        "V-MEA-003",
        f"Cannot apply a metre-based measurement margin in CRS {crs}.",
    )


def _bounds_in_crs(
    entry: MapMeasurementEntry,
    target_crs: Any,
) -> tuple[float, float, float, float]:
    if entry.crs is None:
        raise MapMeasurementError(
            "V-MEA-003",
            f"Measurement source has no CRS: {entry.path}.",
        )
    source_crs = _pyproj.CRS.from_user_input(entry.crs)
    if source_crs == target_crs:
        return entry.bounds
    try:
        transformed = _rasterio_warp.transform_bounds(
            source_crs,
            target_crs,
            *entry.bounds,
            densify_pts=21,
        )
        return (
            float(transformed[0]),
            float(transformed[1]),
            float(transformed[2]),
            float(transformed[3]),
        )
    except (RuntimeError, ValueError) as error:
        raise MapMeasurementError(
            "V-MEA-003",
            f"Could not transform measurement bounds for {entry.path}: {error}",
        ) from error


def _intersects(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> bool:
    return (
        left[0] <= right[2]
        and left[2] >= right[0]
        and left[1] <= right[3]
        and left[3] >= right[1]
    )


def _extent_bounds(
    extent: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    return extent[0], extent[2], extent[1], extent[3]


def _prepare_raster(
    entries: Sequence[MapMeasurementEntry],
    target_crs: Any,
    search_bounds: tuple[float, float, float, float],
    settings: MapMeasurementsConfig,
    fingerprint: str,
    output_time: datetime,
) -> PreparedMapMeasurement:
    with _open_raster(entries[0]) as first:
        if first.crs is not None:
            source_crs = first.crs
        elif entries[0].crs is not None:
            source_crs = _rasterio.crs.CRS.from_user_input(entries[0].crs)
        else:
            raise MapMeasurementError(
                "V-MEA-003",
                f"Raster measurement has no CRS: {entries[0].path}.",
            )
        transform = first.transform
        origin_x, origin_y = _rasterio_warp.transform(
            source_crs,
            target_crs,
            [transform.c],
            [transform.f],
        )
        x_step_x, x_step_y = _rasterio_warp.transform(
            source_crs,
            target_crs,
            [transform.c + transform.a],
            [transform.f + transform.d],
        )
        y_step_x, y_step_y = _rasterio_warp.transform(
            source_crs,
            target_crs,
            [transform.c + transform.b],
            [transform.f + transform.e],
        )
        resolution_x = math.hypot(x_step_x[0] - origin_x[0], x_step_y[0] - origin_y[0])
        resolution_y = math.hypot(y_step_x[0] - origin_x[0], y_step_y[0] - origin_y[0])
    if resolution_x <= 0 or resolution_y <= 0:
        raise MapMeasurementError(
            "V-MEA-002", "Raster measurement has invalid resolution."
        )
    source_bounds = tuple(_bounds_in_crs(entry, target_crs) for entry in entries)
    left = max(search_bounds[0], min(bounds[0] for bounds in source_bounds))
    bottom = max(search_bounds[1], min(bounds[1] for bounds in source_bounds))
    right = min(search_bounds[2], max(bounds[2] for bounds in source_bounds))
    top = min(search_bounds[3], max(bounds[3] for bounds in source_bounds))
    if left >= right or bottom >= top:
        raise MapMeasurementError(
            "V-MEA-004",
            "Selected raster measurements do not overlap the map extent at "
            f"{output_time}.",
        )
    origin_x_value = float(origin_x[0])
    origin_y_value = float(origin_y[0])
    left = (
        origin_x_value
        + math.floor((left - origin_x_value) / resolution_x) * resolution_x
    )
    right = (
        origin_x_value
        + math.ceil((right - origin_x_value) / resolution_x) * resolution_x
    )
    top = (
        origin_y_value + math.ceil((top - origin_y_value) / resolution_y) * resolution_y
    )
    bottom = (
        origin_y_value
        + math.floor((bottom - origin_y_value) / resolution_y) * resolution_y
    )
    width = max(1, int(round((right - left) / resolution_x)))
    height = max(1, int(round((top - bottom) / resolution_y)))
    destination_transform = _rasterio_transform.from_origin(
        left,
        top,
        resolution_x,
        resolution_y,
    )
    mosaic = _np.full((height, width), _np.nan, dtype=_np.float64)
    total = _np.zeros((height, width), dtype=_np.float64)
    count = _np.zeros((height, width), dtype=_np.uint32)
    for entry in entries:
        aligned = _align_raster(
            entry,
            target_crs,
            destination_transform,
            width,
            height,
            (left, bottom, right, top),
        )
        aligned *= entry.factor
        valid = _np.isfinite(aligned)
        if settings.mosaic == "first":
            replace = valid & ~_np.isfinite(mosaic)
            mosaic[replace] = aligned[replace]
        elif settings.mosaic == "last":
            mosaic[valid] = aligned[valid]
        else:
            total[valid] += aligned[valid]
            count[valid] += 1
    if settings.mosaic == "mean":
        _np.divide(total, count, out=mosaic, where=count > 0)
    measurement = PreparedMapMeasurement(
        kind="raster",
        values=mosaic,
        bounds=(left, bottom, right, top),
        mean_time=_mean_time(entry.time for entry in entries),
        fingerprint=fingerprint,
        crs=target_crs.to_string(),
    )
    return measurement


def _align_raster(
    entry: MapMeasurementEntry,
    target_crs: Any,
    destination_transform: Any,
    width: int,
    height: int,
    bounds: tuple[float, float, float, float],
) -> Any:
    with _open_raster(entry) as source:
        source_crs = source.crs
        if source_crs is None:
            if entry.crs is None:
                raise MapMeasurementError(
                    "V-MEA-003",
                    f"Raster measurement has no CRS: {entry.path}.",
                )
            source_crs = _rasterio.crs.CRS.from_user_input(entry.crs)
        source_bounds = _rasterio_warp.transform_bounds(
            target_crs,
            source_crs,
            *bounds,
            densify_pts=21,
        )
        try:
            window = (
                _rasterio_windows.from_bounds(
                    *source_bounds,
                    transform=source.transform,
                )
                .round_offsets()
                .round_lengths()
            )
            window = window.intersection(
                _rasterio_windows.Window(0, 0, source.width, source.height)
            )
        except ValueError:
            return _np.full((height, width), _np.nan, dtype=_np.float64)
        if window.width <= 0 or window.height <= 0:
            return _np.full((height, width), _np.nan, dtype=_np.float64)
        band_index = _raster_band_index(entry, source)
        source_values = _np.ma.asarray(
            source.read(band_index, window=window, masked=True),
            dtype=_np.float64,
        ).filled(_np.nan)
        aligned = _np.full((height, width), _np.nan, dtype=_np.float64)
        _rasterio_warp.reproject(
            source=source_values,
            destination=aligned,
            src_transform=source.window_transform(window),
            src_crs=source_crs,
            src_nodata=_np.nan,
            dst_transform=destination_transform,
            dst_crs=target_crs,
            dst_nodata=_np.nan,
            resampling=_rasterio.enums.Resampling.nearest,
            init_dest_nodata=True,
            num_threads=1,
        )
        return aligned


@contextmanager
def _open_raster(entry: MapMeasurementEntry) -> Iterator[Any]:
    path: str | Path = entry.path
    if entry.path.suffix.casefold() in {".nc", ".nc4"} and entry.value_field:
        path = f'NETCDF:"{entry.path}":{entry.value_field}'
    with _rasterio.open(path) as dataset:
        yield dataset


def _raster_band_index(entry: MapMeasurementEntry, raster: Any) -> int:
    if entry.path.suffix.casefold() not in {".nc", ".nc4"}:
        return 1
    try:
        with netCDF4.Dataset(entry.path, mode="r") as dataset:
            variable_name = entry.value_field or raster.tags(1).get("NETCDF_VARNAME")
            variable = dataset.variables.get(variable_name) if variable_name else None
            if variable is None:
                raise MapMeasurementError(
                    "V-MEA-002",
                    f"CF raster variable is missing from {entry.path}.",
                )
            time_dimension = next(
                (
                    dimension
                    for dimension in variable.dimensions
                    if _is_time_dimension(dataset, dimension)
                ),
                None,
            )
            if time_dimension is None:
                return 1
            coordinate = dataset.variables.get(time_dimension)
            if coordinate is None or not getattr(coordinate, "units", None):
                raise MapMeasurementError(
                    "V-MEA-002",
                    f"CF raster time coordinate has no units: {entry.path}.",
                )
            dates = netCDF4.num2date(
                coordinate[:],
                coordinate.units,
                calendar=getattr(coordinate, "calendar", "standard"),
                only_use_cftime_datetimes=False,
                only_use_python_datetimes=True,
            )
            source_zone = timezone_from_cf_units(coordinate.units) or UTC
            date_values: Any = _np.atleast_1d(dates)
            utc_dates = [_datetime_utc(value, source_zone) for value in date_values]
            return (
                min(
                    range(len(utc_dates)),
                    key=lambda index: abs(
                        (utc_dates[index] - entry.time).total_seconds()
                    ),
                )
                + 1
            )
    except MapMeasurementError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise MapMeasurementError(
            "V-MEA-002",
            f"Could not select a CF raster time in {entry.path}: {error}",
        ) from error


def _is_time_dimension(dataset: Any, dimension: str) -> bool:
    if dimension.casefold() == "time":
        return True
    coordinate = dataset.variables.get(dimension)
    if coordinate is None:
        return False
    standard_name = str(getattr(coordinate, "standard_name", "")).casefold()
    axis = str(getattr(coordinate, "axis", "")).casefold()
    return standard_name == "time" or axis == "t"


def _datetime_utc(value: Any, source_zone: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime(
            value.year,
            value.month,
            value.day,
            value.hour,
            value.minute,
            value.second,
            value.microsecond,
        )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=source_zone)
    return parsed.astimezone(UTC)


def _prepare_polygons(
    entries: Sequence[MapMeasurementEntry],
    target_crs: Any,
    extent: tuple[float, float, float, float],
    settings: MapMeasurementsConfig,
    fingerprint: str,
    output_time: datetime,
) -> PreparedMapMeasurement:
    frames: list[Any] = []
    for entry in entries:
        source_bounds = _rasterio_warp.transform_bounds(
            target_crs,
            entry.crs,
            *_expand_bounds(
                _extent_bounds(extent),
                target_crs,
                settings.spatial_margin_m,
            ),
            densify_pts=21,
        )
        frame = _geopandas.read_file(
            entry.path,
            bbox=source_bounds,
            engine="pyogrio",
        )
        if frame.crs is None:
            if entry.crs is None:
                raise MapMeasurementError(
                    "V-MEA-003",
                    f"Polygon measurement has no CRS: {entry.path}.",
                )
            frame = frame.set_crs(entry.crs)
        frame = frame.to_crs(target_crs)
        value_field, filters = _polygon_fields(entry, frame, settings.polygon_filters)
        values = _numeric_column(frame, value_field, entry.path)
        values *= entry.factor
        keep = _polygon_filter_mask(frame, filters, entry.path)
        frame = frame.loc[keep].copy()
        values = values[keep]
        frame["__d3dplot_value"] = values
        id_field = _id_field(frame, filters, entry.path)
        frame["__d3dplot_id"] = (
            frame[id_field].map(_normalise_id) if id_field is not None else None
        )
        extent_bounds = _extent_bounds(extent)
        clipped = frame.geometry.intersection(
            _shapely.box(
                extent_bounds[0],
                extent_bounds[1],
                extent_bounds[2],
                extent_bounds[3],
            )
        )
        valid = ~clipped.is_empty & clipped.notna()
        frame = frame.loc[valid].copy()
        frame.geometry = clipped.loc[valid]
        frames.append(frame)
    merged = _pandas.concat(frames, ignore_index=True)
    polygons, values, ids = _polygon_arrays(merged)
    measurement = PreparedMapMeasurement(
        kind="polygons",
        values=values,
        bounds=extent_bounds,
        mean_time=_mean_time(entry.time for entry in entries),
        fingerprint=fingerprint,
        crs=target_crs.to_string(),
        polygons=polygons,
        ids=ids,
    )
    return measurement


def _polygon_fields(
    entry: MapMeasurementEntry,
    frame: Any,
    filters: PolygonFilters | None,
) -> tuple[str, PolygonFilters | None]:
    effective_filters = filters
    if effective_filters is None and entry.path.suffix.casefold() == ".shp":
        effective_filters = PolygonFilters(preset="rws_vakken")
    requested = (
        effective_filters.value_field
        if effective_filters is not None and effective_filters.value_field
        else entry.value_field
    )
    if requested is None and effective_filters is not None:
        requested = "MEAN" if effective_filters.preset == "rws_vakken" else None
    if requested is None:
        raise MapMeasurementError(
            "V-MEA-002",
            f"No polygon value_field is configured for {entry.path}.",
        )
    field = _case_insensitive_field(frame.columns, requested)
    if field is None:
        raise MapMeasurementError(
            "V-MEA-002",
            f"Polygon value field {requested!r} is missing from {entry.path}.",
        )
    return field, effective_filters


def _polygon_filter_mask(frame: Any, filters: PolygonFilters | None, path: Path) -> Any:
    if filters is None:
        return _np.ones(len(frame), dtype=bool)
    mask = _np.ones(len(frame), dtype=bool)
    has_coverage = (
        filters.preset == "rws_vakken"
        or filters.count_field is not None
        or filters.area_field is not None
    )
    if has_coverage:
        count_field = _configured_or_candidate(
            frame,
            filters.count_field,
            ("COUNT",),
        )
        area_field = _configured_or_candidate(
            frame,
            filters.area_field,
            ("A", "oppervlak_"),
        )
        if count_field is None or area_field is None:
            raise MapMeasurementError(
                "V-MEA-002",
                f"Coverage fields COUNT and area are missing from {path}.",
            )
        count = _numeric_column(frame, count_field, path)
        area = _numeric_column(frame, area_field, path)
        valid_area = _np.isfinite(area) & (area > 0)
        coverage = _np.full(len(frame), _np.nan, dtype=_np.float64)
        _np.divide(count, area, out=coverage, where=valid_area)
        mask &= _np.isfinite(coverage) & (coverage >= filters.min_coverage)
    if filters.preset == "rws_vakken" or filters.location_field is not None:
        location_field = _configured_or_candidate(
            frame,
            filters.location_field,
            ("locatie", "Locatie"),
        )
        if location_field is None:
            raise MapMeasurementError(
                "V-MEA-002",
                f"Polygon location field is missing from {path}.",
            )
        locations = frame[location_field].map(_location_code).to_numpy(dtype=object)
        mask &= _np.isin(locations, filters.allowed_locations)
    return mask


def _id_field(frame: Any, filters: PolygonFilters | None, path: Path) -> str | None:
    if filters is None:
        return None
    if filters.id_field is not None:
        field = _case_insensitive_field(frame.columns, filters.id_field)
        if field is None:
            raise MapMeasurementError(
                "V-MEA-002",
                f"Polygon ID field {filters.id_field!r} is missing from {path}.",
            )
        return field
    if filters.preset == "rws_vakken":
        return _case_insensitive_field(frame.columns, "code_uniek")
    return None


def _configured_or_candidate(
    frame: Any,
    configured: str | None,
    candidates: tuple[str, ...],
) -> str | None:
    if configured is not None:
        return _case_insensitive_field(frame.columns, configured)
    for candidate in candidates:
        field = _case_insensitive_field(frame.columns, candidate)
        if field is not None:
            return field
    return None


def _case_insensitive_field(columns: Sequence[str], requested: str) -> str | None:
    return next(
        (
            column
            for column in columns
            if str(column).casefold() == requested.casefold()
        ),
        None,
    )


def _numeric_column(frame: Any, field: str, path: Path) -> Any:
    try:
        return _np.array(
            _pandas.to_numeric(frame[field], errors="coerce").to_numpy(
                dtype=_np.float64
            ),
            dtype=_np.float64,
            copy=True,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise MapMeasurementError(
            "V-MEA-002",
            f"Polygon field {field!r} is not numeric in {path}.",
        ) from error


def _location_code(value: object) -> int | None:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return int(value)
    text = str(value).strip().upper()
    if len(text) == 2 and text[0] in {"L", "R"} and text[1].isdigit():
        return -int(text[1]) if text[0] == "L" else int(text[1])
    try:
        return int(text)
    except ValueError:
        return None


def _normalise_id(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    text = str(value).strip()
    return text or None


def _polygon_arrays(frame: Any) -> tuple[tuple[Any, ...], Any, tuple[str | None, ...]]:
    polygons: list[Any] = []
    values: list[float] = []
    ids: list[str | None] = []
    for geometry, value, identifier in zip(
        frame.geometry,
        frame["__d3dplot_value"],
        frame["__d3dplot_id"],
        strict=True,
    ):
        if geometry is None or geometry.is_empty:
            continue
        polygons.append(geometry)
        values.append(float(value))
        ids.append(_normalise_id(identifier))
    return tuple(polygons), _np.asarray(values, dtype=_np.float64), tuple(ids)


def _geometry_parts(geometry: Any) -> tuple[Any, ...]:
    if geometry is None or geometry.is_empty:
        return ()
    if geometry.geom_type == "Polygon":
        return (geometry,)
    if geometry.geom_type == "MultiPolygon":
        return tuple(geometry.geoms)
    if geometry.geom_type == "GeometryCollection":
        return tuple(
            part
            for geometry_part in geometry.geoms
            for part in _geometry_parts(geometry_part)
        )
    return ()


def _difference(
    current: PreparedMapMeasurement,
    baseline: PreparedMapMeasurement,
    fingerprint: str,
) -> PreparedMapMeasurement:
    if current.kind == "raster":
        values = _subtract_rasters(current, baseline)
        result = PreparedMapMeasurement(
            kind="raster",
            values=values,
            bounds=current.bounds,
            mean_time=current.mean_time,
            fingerprint=fingerprint,
            crs=current.crs,
        )
        return result
    values = _subtract_polygons(current, baseline)
    result = PreparedMapMeasurement(
        kind="polygons",
        values=values,
        bounds=current.bounds,
        mean_time=current.mean_time,
        fingerprint=fingerprint,
        crs=current.crs,
        polygons=current.polygons,
        ids=current.ids,
    )
    return result


def _subtract_rasters(
    current: PreparedMapMeasurement,
    baseline: PreparedMapMeasurement,
) -> Any:
    if (
        current.bounds == baseline.bounds
        and current.values.shape == baseline.values.shape
    ):
        base_values = _np.asarray(baseline.values, dtype=_np.float64)
    else:
        current_transform = _rasterio_transform.from_bounds(
            *current.bounds,
            current.values.shape[1],
            current.values.shape[0],
        )
        baseline_transform = _rasterio_transform.from_bounds(
            *baseline.bounds,
            baseline.values.shape[1],
            baseline.values.shape[0],
        )
        base_values = _np.full(current.values.shape, _np.nan, dtype=_np.float64)
        _rasterio_warp.reproject(
            source=_np.asarray(baseline.values, dtype=_np.float64),
            destination=base_values,
            src_transform=baseline_transform,
            src_crs=current.crs,
            src_nodata=_np.nan,
            dst_transform=current_transform,
            dst_crs=current.crs,
            dst_nodata=_np.nan,
            resampling=_rasterio.enums.Resampling.nearest,
            init_dest_nodata=True,
            num_threads=1,
        )
    values = _np.full(current.values.shape, _np.nan, dtype=_np.float64)
    valid = _np.isfinite(current.values) & _np.isfinite(base_values)
    values[valid] = current.values[valid] - base_values[valid]
    return values


def _subtract_polygons(
    current: PreparedMapMeasurement,
    baseline: PreparedMapMeasurement,
) -> Any:
    base_values = _np.asarray(baseline.values, dtype=_np.float64)
    current_values = _np.asarray(current.values, dtype=_np.float64)
    if (
        current.ids
        and baseline.ids
        and all(value is not None for value in current.ids)
        and all(value is not None for value in baseline.ids)
    ):
        if len(set(current.ids)) != len(current.ids) or len(set(baseline.ids)) != len(
            baseline.ids
        ):
            raise MapMeasurementError("V-MEA-002", "Polygon IDs must be unique.")
        baseline_by_id = {
            identifier: index for index, identifier in enumerate(baseline.ids)
        }
        try:
            indexes = [baseline_by_id[identifier] for identifier in current.ids]
        except KeyError as error:
            raise MapMeasurementError(
                "V-MEA-002",
                f"Polygon baseline has no matching ID {error.args[0]!r}.",
            ) from error
        return current_values - base_values[indexes]
    if current.polygons and baseline.polygons:
        baseline_by_geometry = {
            _geometry_key(polygon): index
            for index, polygon in enumerate(baseline.polygons)
        }
        current_keys = [_geometry_key(polygon) for polygon in current.polygons]
        if len(baseline_by_geometry) == len(baseline.polygons) and all(
            key in baseline_by_geometry for key in current_keys
        ):
            indexes = [baseline_by_geometry[key] for key in current_keys]
            return current_values - base_values[indexes]
    if current_values.size != base_values.size:
        raise MapMeasurementError(
            "V-MEA-002",
            "Polygon measurement counts differ and cannot be matched by ID or "
            "geometry.",
        )
    return current_values - base_values


def _geometry_key(polygon: Any) -> bytes:
    return bytes(polygon.normalize().wkb)


def _mean_time(times: Any) -> datetime:
    values = [time.astimezone(UTC).timestamp() for time in times]
    return datetime.fromtimestamp(sum(values) / len(values), tz=UTC)


def _read_cache(
    path: Path,
    kind: Literal["raster", "polygons"],
    fingerprint: str,
) -> PreparedMapMeasurement | None:
    if not path.is_file():
        return None
    try:
        if kind == "raster":
            with netCDF4.Dataset(path, mode="r") as dataset:
                if getattr(dataset, "d3dplot_fingerprint", None) != fingerprint:
                    return None
                stored = dataset.variables["values"][:]
                values = _np.asarray(_np.ma.filled(stored, _np.nan), dtype=_np.float64)
                bounds = tuple(
                    float(getattr(dataset, name))
                    for name in ("left", "bottom", "right", "top")
                )
                mean_time = datetime.fromisoformat(dataset.mean_time).astimezone(UTC)
                crs = str(dataset.crs)
            return PreparedMapMeasurement(
                kind="raster",
                values=values,
                bounds=bounds,  # type: ignore[arg-type]
                mean_time=mean_time,
                fingerprint=fingerprint,
                crs=crs,
            )
        frame = _geopandas.read_file(path, engine="pyogrio")
        if (
            frame.empty
            or not (frame["__d3dplot_fingerprint"].astype(str) == fingerprint).all()
        ):
            return None
        polygons, values, ids = _polygon_arrays(frame)
        bounds = tuple(
            float(value) for value in json.loads(str(frame["__d3dplot_bounds"].iloc[0]))
        )
        mean_time = datetime.fromisoformat(
            str(frame["__d3dplot_time"].iloc[0])
        ).astimezone(UTC)
        return PreparedMapMeasurement(
            kind="polygons",
            values=values,
            bounds=bounds,  # type: ignore[arg-type]
            mean_time=mean_time,
            fingerprint=fingerprint,
            crs=str(frame.crs),
            polygons=polygons,
            ids=ids,
        )
    except (OSError, RuntimeError, TypeError, ValueError, KeyError, AttributeError):
        return None


def _write_raster_cache(
    path: Path,
    measurement: PreparedMapMeasurement,
    fingerprint: str,
    guard: OutputWriteGuard,
) -> None:
    with atomic_path(path, guard) as temporary:
        with netCDF4.Dataset(temporary, mode="w") as dataset:
            dataset.createDimension("y", measurement.values.shape[0])
            dataset.createDimension("x", measurement.values.shape[1])
            values = dataset.createVariable(
                "values",
                "f4",
                ("y", "x"),
                fill_value=_np.float32(-3.4028235e38),
                zlib=True,
            )
            stored = _np.asarray(measurement.values, dtype=_np.float32)
            values[:] = _np.where(
                _np.isfinite(stored),
                stored,
                _np.float32(-3.4028235e38),
            )
            dataset.d3dplot_fingerprint = fingerprint
            dataset.crs = measurement.crs
            dataset.left, dataset.bottom, dataset.right, dataset.top = (
                measurement.bounds
            )
            dataset.mean_time = measurement.mean_time.isoformat()
            dataset.transform = json.dumps(
                list(
                    _rasterio_transform.from_bounds(
                        *measurement.bounds,
                        measurement.values.shape[1],
                        measurement.values.shape[0],
                    )
                )
            )


def _write_polygon_cache_from_arrays(
    path: Path,
    measurement: PreparedMapMeasurement,
    fingerprint: str,
    guard: OutputWriteGuard,
) -> None:
    if not measurement.polygons:
        return
    frame = _geopandas.GeoDataFrame(
        {
            "__d3dplot_value": measurement.values,
            "__d3dplot_id": measurement.ids,
            "__d3dplot_fingerprint": fingerprint,
            "__d3dplot_time": measurement.mean_time.isoformat(),
            "__d3dplot_bounds": json.dumps(measurement.bounds),
        },
        geometry=list(measurement.polygons),
        crs=measurement.crs,
    )
    with atomic_path(path, guard) as temporary:
        temporary.unlink()
        gpkg_temporary = temporary.with_suffix(".gpkg")
        try:
            guard.validate_target(gpkg_temporary)
            frame.to_file(
                gpkg_temporary,
                driver="GPKG",
                layer="measurements",
                engine="pyogrio",
            )
            os.replace(gpkg_temporary, temporary)
        finally:
            gpkg_temporary.unlink(missing_ok=True)
