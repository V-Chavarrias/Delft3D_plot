# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare and draw shared map overlays."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from hashlib import blake2b
from importlib import import_module
from pathlib import Path
from typing import Any

import netCDF4
from matplotlib.axes import Axes
from matplotlib.collections import LineCollection

from d3dplot import __version__
from d3dplot.config.figure import Extent, FigureConfig, MapViewConfig
from d3dplot.config.project import ProjectConfig
from d3dplot.river_km.file import (
    RiverKmPoint,
    read_river_km_points,
)
from d3dplot.store import (
    CACHE_SCHEMA_VERSION,
    OutputLayout,
    OutputWriteError,
    OutputWriteGuard,
    atomic_path,
    canonical_json,
    fingerprint_artifact,
    fingerprint_small_input,
)

_Array = Any
np: Any = import_module("numpy")


class MapDecorationsError(RuntimeError):
    """A configured map overlay cannot be prepared or written safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class BackgroundTiles:
    """One pre-fetched tile mosaic in the model coordinate system."""

    image: _Array
    extent: Extent
    attribution: str
    fingerprint: str


@dataclass(slots=True)
class MapDecorations:
    """Fingerprintable land, river-km, and background overlays for map figures."""

    land_lines: tuple[tuple[_Array, _Array], ...]
    land_fingerprints: tuple[str, ...]
    river_km_points: tuple[RiverKmPoint, ...]
    river_km_fingerprint: str | None
    land_color: str
    land_width: float
    river_km_color: str | None
    river_km_font_size: float
    base_fingerprint: str
    diagnostic: Callable[[str], None] | None = None
    contextily: Any | None = None
    provider: Any | None = None
    provider_name: str | None = None
    crs: object | None = None
    _backgrounds: dict[Extent, BackgroundTiles | None] = field(default_factory=dict)
    _reported_unavailable: set[Extent] = field(default_factory=set)

    @classmethod
    def prepare(
        cls,
        project: ProjectConfig,
        map_config: MapViewConfig,
        figure_config: FigureConfig,
        grid_crs: object | None,
        layout: OutputLayout,
        guard: OutputWriteGuard,
        *,
        diagnostic: Callable[[str], None] | None = None,
    ) -> MapDecorations:
        land_lines: list[tuple[_Array, _Array]] = []
        land_fingerprints: list[str] = []
        for path in map_config.land_boundaries:
            lines, fingerprint = _load_land_boundary(path, layout, guard, diagnostic)
            land_lines.append(lines)
            land_fingerprints.append(fingerprint)

        river_km_points: tuple[RiverKmPoint, ...] = ()
        river_km_fingerprint: str | None = None
        if map_config.river_km_labels is not None:
            path = map_config.river_km_labels.file
            try:
                river_km_points = read_river_km_points(path)
                river_km_fingerprint = fingerprint_small_input(path)
            except (OSError, ValueError) as error:
                raise MapDecorationsError(
                    "V-RKM-001",
                    f"Could not prepare river-km labels from {path}: {error}",
                ) from error

        provider: Any | None = None
        provider_name: str | None = None
        contextily: Any | None = None
        crs = project.crs or grid_crs
        if map_config.background_tiles is not False:
            contextily, provider, provider_name = _load_tile_provider(
                map_config.background_tiles.provider
            )
            if crs is None:
                raise MapDecorationsError(
                    "V-CFG-001",
                    "background_tiles requires a model CRS; configure project.crs.",
                )
            cache_dir = _tile_cache_dir(project)
            cache_guard = guard.with_allowed_roots((cache_dir,))
            try:
                cache_guard.validate_target(cache_dir)
                cache_dir.mkdir(parents=True, exist_ok=True)
                cache_guard.validate_target(cache_dir)
                contextily.set_cache_dir(str(cache_dir))
            except (OSError, OutputWriteError) as error:
                raise MapDecorationsError(
                    "V-OUT-041",
                    f"Could not initialize guarded tile cache {cache_dir}: {error}",
                ) from error
            if diagnostic is not None:
                diagnostic(f"Using persistent background-tile cache {cache_dir}.")

        base_fingerprint = fingerprint_artifact(
            kind="map_decorations",
            schema_version=CACHE_SCHEMA_VERSION,
            tool_version=__version__,
            algorithm_version="1",
            parameters={
                "land_color": "black",
                "land_width": 0.5,
                "provider": provider_name,
                "crs": str(crs) if crs is not None else None,
                "river_km_color": (
                    map_config.river_km_labels.color
                    if map_config.river_km_labels is not None
                    else None
                ),
                "river_km_font_size": figure_config.font_size,
            },
            inputs=(
                *land_fingerprints,
                *((river_km_fingerprint,) if river_km_fingerprint else ()),
            ),
        )
        return cls(
            land_lines=tuple(land_lines),
            land_fingerprints=tuple(land_fingerprints),
            river_km_points=river_km_points,
            river_km_fingerprint=river_km_fingerprint,
            land_color="black",
            land_width=0.5,
            river_km_color=(
                map_config.river_km_labels.color
                if map_config.river_km_labels is not None
                else None
            ),
            river_km_font_size=figure_config.font_size,
            base_fingerprint=base_fingerprint,
            diagnostic=diagnostic,
            contextily=contextily,
            provider=provider,
            provider_name=provider_name,
            crs=crs,
        )

    def prepare_backgrounds(self, bounds: Sequence[Extent]) -> None:
        """Fetch each distinct background extent once, using contextily's disk cache."""
        for extent in dict.fromkeys(bounds):
            if self.contextily is None or self.provider is None or self.crs is None:
                self._backgrounds[extent] = None
                continue
            self._backgrounds[extent] = self._fetch_background(extent)

    def fingerprint_for(self, bounds: Extent) -> str:
        background = self._backgrounds.get(bounds)
        background_fingerprint = (
            background.fingerprint if background is not None else "no-background"
        )
        return fingerprint_artifact(
            kind="map_decoration_extent",
            schema_version=CACHE_SCHEMA_VERSION,
            tool_version=__version__,
            algorithm_version="1",
            parameters={
                "bounds": list(bounds),
                "background": background_fingerprint,
                "base": self.base_fingerprint,
            },
            inputs=(self.base_fingerprint, background_fingerprint),
        )

    def visible_river_km(self, bounds: Extent) -> tuple[RiverKmPoint, ...]:
        """Return label points strictly inside a map extent."""
        xmin, xmax, ymin, ymax = bounds
        return tuple(
            point
            for point in self.river_km_points
            if xmin < point.x < xmax and ymin < point.y < ymax
        )

    def draw_background(self, axes: Axes, bounds: Extent) -> None:
        """Draw the already-fetched tile mosaic beneath map values."""
        background = self._backgrounds.get(bounds)
        if background is None:
            return
        axes.imshow(
            background.image,
            extent=background.extent,
            origin="upper",
            interpolation="bilinear",
            zorder=0,
        )

    def draw_foreground(self, axes: Axes, bounds: Extent) -> None:
        """Draw clipped land lines and visible river-km labels."""
        segments: list[_Array] = []
        for x_coordinates, y_coordinates in self.land_lines:
            segments.extend(_clip_line(x_coordinates, y_coordinates, bounds))
        if segments:
            axes.add_collection(
                LineCollection(
                    segments,
                    colors=self.land_color,
                    linewidths=self.land_width,
                    zorder=3,
                ),
                autolim=False,
            )

        visible_points = self.visible_river_km(bounds)
        if visible_points and self.river_km_color is not None:
            axes.scatter(
                [point.x for point in visible_points],
                [point.y for point in visible_points],
                s=10,
                color=self.river_km_color,
                zorder=4,
            )
            for point in visible_points:
                axes.text(
                    point.x,
                    point.y,
                    point.label,
                    color=self.river_km_color,
                    fontsize=self.river_km_font_size,
                    zorder=4,
                )

        background = self._backgrounds.get(bounds)
        if background is not None and background.attribution:
            axes.text(
                0.01,
                0.01,
                background.attribution,
                color="black",
                fontsize=5,
                ha="left",
                va="bottom",
                transform=axes.transAxes,
                zorder=5,
            )

    def _fetch_background(self, bounds: Extent) -> BackgroundTiles | None:
        if self.contextily is None or self.provider is None or self.crs is None:
            return None
        request_exception = import_module("requests").exceptions.RequestException
        try:
            rasterio_warp = import_module("rasterio.warp")
            model_crs = import_module("rasterio.crs").CRS.from_user_input(self.crs)
            west, south, east, north = rasterio_warp.transform_bounds(
                model_crs,
                "EPSG:3857",
                bounds[0],
                bounds[2],
                bounds[1],
                bounds[3],
                densify_pts=21,
            )
            image, tile_extent = self.contextily.bounds2img(
                west,
                south,
                east,
                north,
                source=self.provider,
                use_cache=True,
                n_connections=1,
            )
            if model_crs.to_epsg() != 3857:
                image, tile_extent = self.contextily.warp_tiles(
                    image,
                    tile_extent,
                    t_crs=model_crs,
                )
        except request_exception as error:
            if bounds not in self._reported_unavailable:
                self._reported_unavailable.add(bounds)
                if self.diagnostic is not None:
                    self.diagnostic(
                        f"V-ENV-002: background tiles unavailable for extent "
                        f"{bounds}; plotting without them: {error}"
                    )
            return None
        except (ValueError, TypeError) as error:
            raise MapDecorationsError(
                "V-CFG-001",
                f"Could not transform map extent {bounds} for tiles: {error}",
            ) from error

        image_array = np.asarray(image)
        if image_array.ndim != 3 or image_array.shape[2] < 3:
            raise MapDecorationsError(
                "V-ENV-002",
                f"Tile provider {self.provider_name!r} returned an invalid image.",
            )
        image_hash = blake2b(
            np.ascontiguousarray(image_array).tobytes()
            + np.asarray(tile_extent, dtype=np.float64).tobytes(),
            digest_size=32,
        ).hexdigest()
        fingerprint = fingerprint_artifact(
            kind="background_tile_mosaic",
            schema_version=CACHE_SCHEMA_VERSION,
            tool_version=__version__,
            algorithm_version="1",
            parameters={
                "bounds": list(bounds),
                "crs": str(self.crs),
                "provider": self.provider_name,
            },
            inputs=(image_hash,),
        )
        attribution = str(self.provider.get("attribution", ""))
        extent = (
            float(tile_extent[0]),
            float(tile_extent[1]),
            float(tile_extent[2]),
            float(tile_extent[3]),
        )
        return BackgroundTiles(
            image=image_array,
            extent=extent,
            attribution=attribution,
            fingerprint=fingerprint,
        )


def _load_land_boundary(
    source: Path,
    layout: OutputLayout,
    guard: OutputWriteGuard,
    diagnostic: Callable[[str], None] | None,
) -> tuple[tuple[_Array, _Array], str]:
    if source.suffix.casefold() != ".ldb" or not source.is_file():
        raise MapDecorationsError(
            "V-LDB-001",
            f"Land boundary must be a readable .ldb file: {source}.",
        )
    try:
        source_fingerprint = fingerprint_small_input(source)
    except (OSError, ValueError) as error:
        raise MapDecorationsError(
            "V-LDB-001",
            f"Could not fingerprint land boundary {source}: {error}",
        ) from error
    fingerprint = fingerprint_artifact(
        kind="land_boundary",
        schema_version=CACHE_SCHEMA_VERSION,
        tool_version=__version__,
        algorithm_version="1",
        parameters={"format": ".ldb"},
        inputs=(source_fingerprint,),
    )
    cache_path = layout.cache_inputs / f"ldb_{fingerprint[:24]}.nc"
    cached = _read_land_boundary_cache(cache_path, fingerprint)
    if cached is not None:
        if diagnostic is not None:
            diagnostic(f"Reused land-boundary cache for {source.name}.")
        return cached, fingerprint

    try:
        from hydrolib.core.dflowfm import PolyFile  # type: ignore[import-untyped]

        poly_file = PolyFile(source)
        x_values: list[float] = []
        y_values: list[float] = []
        for poly_object in poly_file.objects:
            if len(poly_object.points) < 2:
                raise ValueError(
                    f"polyline {poly_object.metadata.name!r} has fewer than two points"
                )
            for point in poly_object.points:
                x_coord, y_coord = float(point.x), float(point.y)
                if not (np.isfinite(x_coord) and np.isfinite(y_coord)):
                    raise ValueError(
                        f"polyline {poly_object.metadata.name!r} has non-finite points"
                    )
                x_values.append(x_coord)
                y_values.append(y_coord)
            x_values.append(float("nan"))
            y_values.append(float("nan"))
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise MapDecorationsError(
            "V-LDB-001",
            f"Could not read land boundary {source} with HYDROLIB-core: {error}",
        ) from error

    if not x_values:
        raise MapDecorationsError(
            "V-LDB-001", f"Land boundary contains no polylines: {source}."
        )
    x_array = np.asarray(x_values[:-1], dtype=np.float64)
    y_array = np.asarray(y_values[:-1], dtype=np.float64)
    _write_land_boundary_cache(cache_path, x_array, y_array, fingerprint, guard)
    if diagnostic is not None:
        diagnostic(f"Cached land boundary {source.name}.")
    return (x_array, y_array), fingerprint


def _read_land_boundary_cache(
    path: Path,
    fingerprint: str,
) -> tuple[_Array, _Array] | None:
    if not path.is_file():
        return None
    try:
        with netCDF4.Dataset(path, mode="r") as dataset:
            if getattr(dataset, "d3dplot_fingerprint", None) != fingerprint:
                return None
            x_values = np.asarray(dataset.variables["x"][:], dtype=np.float64)
            y_values = np.asarray(dataset.variables["y"][:], dtype=np.float64)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        return None
    if (
        x_values.ndim != 1
        or y_values.ndim != 1
        or x_values.shape != y_values.shape
        or x_values.size == 0
        or np.any(np.isinf(x_values))
        or np.any(np.isinf(y_values))
    ):
        return None
    return x_values, y_values


def _write_land_boundary_cache(
    path: Path,
    x_values: _Array,
    y_values: _Array,
    fingerprint: str,
    guard: OutputWriteGuard,
) -> None:
    with atomic_path(path, guard) as temporary:
        with netCDF4.Dataset(temporary, mode="w") as dataset:
            dataset.createDimension("point", int(x_values.size))
            x_variable = dataset.createVariable("x", "f8", ("point",))
            y_variable = dataset.createVariable("y", "f8", ("point",))
            x_variable[:] = x_values
            y_variable[:] = y_values
            dataset.d3dplot_fingerprint = fingerprint
            dataset.d3dplot_parameters = canonical_json(
                {"point_count": int(x_values.size), "nan_separated": True}
            )


def _load_tile_provider(provider_name: str) -> tuple[Any, Any, str]:
    try:
        contextily = import_module("contextily")
    except ImportError as error:
        raise MapDecorationsError(
            "V-ENV-001",
            "Install the optional 'tiles' extra to use background_tiles.",
        ) from error
    provider: Any = contextily.providers
    try:
        for component in provider_name.split("."):
            provider = getattr(provider, component)
    except AttributeError as error:
        raise MapDecorationsError(
            "V-CFG-001",
            f"Unknown contextily tile provider {provider_name!r}.",
        ) from error
    if not isinstance(provider, dict) and not hasattr(provider, "get"):
        raise MapDecorationsError(
            "V-CFG-001",
            f"Contextily tile provider {provider_name!r} is not a provider.",
        )
    return contextily, provider, provider_name


def _tile_cache_dir(project: ProjectConfig) -> Path:
    if project.tiles_cache_dir is None:
        try:
            platformdirs = import_module("platformdirs")
        except ImportError as error:
            raise MapDecorationsError(
                "V-ENV-001",
                "Install the optional 'tiles' extra to configure a persistent "
                "tile cache.",
            ) from error
        cache_dir = Path(platformdirs.user_cache_dir("d3dplot")) / "tiles"
    else:
        cache_dir = project.tiles_cache_dir.expanduser()
    cache_dir = cache_dir.resolve(strict=False)
    posix_path = cache_dir.as_posix().casefold()
    if (
        cache_dir.drive.casefold() == "p:"
        or posix_path == "/p"
        or posix_path.startswith("/p/")
    ):
        raise MapDecorationsError(
            "V-OUT-041",
            f"Background-tile cache cannot be written to the P-drive: {cache_dir}.",
        )
    return cache_dir


def _clip_line(x_values: _Array, y_values: _Array, bounds: Extent) -> list[_Array]:
    coordinates = np.column_stack((x_values, y_values))
    segments: list[_Array] = []
    current: list[_Array] = []
    for start, end in zip(coordinates[:-1], coordinates[1:], strict=True):
        clipped = _clip_segment(start, end, bounds)
        if clipped is None:
            if len(current) >= 2:
                segments.append(np.asarray(current))
            current = []
            continue
        first, last = clipped
        if current and np.array_equal(current[-1], first):
            current.append(last)
        else:
            if len(current) >= 2:
                segments.append(np.asarray(current))
            current = [first, last]
    if len(current) >= 2:
        segments.append(np.asarray(current))
    return segments


def _clip_segment(
    start: _Array,
    end: _Array,
    bounds: Extent,
) -> tuple[_Array, _Array] | None:
    if not (np.isfinite(start).all() and np.isfinite(end).all()):
        return None

    xmin, xmax, ymin, ymax = bounds
    direction = end - start
    lower, upper = 0.0, 1.0
    for denominator, numerator in (
        (-direction[0], start[0] - xmin),
        (direction[0], xmax - start[0]),
        (-direction[1], start[1] - ymin),
        (direction[1], ymax - start[1]),
    ):
        if denominator == 0:
            if numerator < 0:
                return None
            continue
        ratio = numerator / denominator
        if denominator < 0:
            lower = max(lower, ratio)
        else:
            upper = min(upper, ratio)
        if lower > upper:
            return None
    return start + lower * direction, start + upper * direction
