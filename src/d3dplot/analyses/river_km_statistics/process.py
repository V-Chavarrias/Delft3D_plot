# SPDX-License-Identifier: GPL-3.0-or-later
"""Numerical reductions and post-processing for river-km statistics."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Literal, TypeAlias
from urllib.parse import quote

import numpy as np
from numpy.typing import ArrayLike, NDArray

from d3dplot import __version__
from d3dplot.analyses.river_km_statistics.config import (
    FractionGroupingConfig,
    KmRangeConfig,
    RiverKmStatisticsConfig,
)
from d3dplot.execution.cache import CacheFingerprintReadError, read_cache_fingerprints
from d3dplot.paths import resolve_path_preserving_drive
from d3dplot.river_km.file import (
    RiverKmFileError,
    RiverKmPoint,
    read_river_km_points,
)
from d3dplot.river_km.geometry import (
    RiverKmGeometryError,
    RiverKmSegmentGeometry,
    build_segment_geometry,
)
from d3dplot.river_km.regions import (
    RegionAssignmentError,
    RegionPolygon,
    RegionPolygonError,
    RegionSegmentAssignment,
    assign_faces_to_segments,
    read_region_polygon,
)
from d3dplot.simulation.time_axis import (
    CorruptOutputPolicy,
    TimeAxis,
    TimeEntry,
    TimeSelectionResult,
)
from d3dplot.store import (
    CACHE_SCHEMA_VERSION,
    OutputLayout,
    OutputWriteGuard,
    atomic_path,
    canonical_json,
    fingerprint_artifact,
    fingerprint_small_input,
)
from d3dplot.validate.report import ValidationIssue
from d3dplot.variables.context import QuantityRequest
from d3dplot.variables.geometry import GridGeometry
from d3dplot.variables.grouping import (
    ClassMembership,
    SedimentGroupingError,
    group_sediment_fraction_components,
    sediment_class_grouping_key,
    sediment_class_membership,
)
from d3dplot.variables.registry import VariableSpec
from d3dplot.variables.selection import quantity_selection_label

FloatArray: TypeAlias = NDArray[np.float64]
_shapely = import_module("shapely")
_LOGGER = logging.getLogger(__name__)

SPATIAL_STATISTICS = (
    "mean",
    "std",
    "max",
    "min",
    "count",
    "sum",
    "sum_per_length",
    "mean_area_weighted",
)


class RiverKmStatisticsError(ValueError):
    """A river-km statistic cannot be calculated under the analysis contract."""

    def __init__(self, message: str, *, code: str = "V-SMB-014") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PreparedRegionSegments:
    """Prepared region and segment geometry, including its guarded cache state."""

    region_name: str
    km_set_name: str
    region: RegionPolygon
    geometry: RiverKmSegmentGeometry
    assignment: RegionSegmentAssignment
    cache_path: Path
    fingerprint: str
    cache_action: str
    choices: tuple[str, ...]
    issues: tuple[ValidationIssue, ...]


def _km_centres(value: list[float] | KmRangeConfig) -> FloatArray:
    if isinstance(value, KmRangeConfig):
        if value.end < value.start:
            raise RiverKmStatisticsError(
                "A river-km range must end at or after its start.",
                code="V-RKM-005",
            )
        count = int(np.floor((value.end - value.start) / value.step + 1e-10))
        centres = value.start + np.arange(count + 1, dtype=np.float64) * value.step
    else:
        centres = np.asarray(value, dtype=np.float64)
    if (
        centres.ndim != 1
        or centres.size < 2
        or not np.isfinite(centres).all()
        or np.any(np.diff(centres) <= 0)
    ):
        raise RiverKmStatisticsError(
            "Each river-km set needs at least two finite, strictly increasing centres.",
            code="V-RKM-005",
        )
    return centres


def _source_fingerprints(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        raise RiverKmStatisticsError(
            f"Region polygon input does not exist: {path}.",
            code="V-SMB-001",
        )
    if path.suffix.casefold() != ".shp":
        sources: tuple[Path, ...] = (path,)
    else:
        sources = tuple(
            candidate
            for suffix in (".shp", ".shx", ".dbf", ".prj", ".cpg")
            if (candidate := path.with_suffix(suffix)).is_file()
        )
    if not sources:
        raise RiverKmStatisticsError(
            f"Region polygon input does not exist: {path}.",
            code="V-SMB-001",
        )
    return tuple(fingerprint_small_input(source) for source in sources)


def _river_km_input_cache_path(layout: OutputLayout, fingerprint: str) -> Path:
    key = hashlib.blake2b(fingerprint.encode("ascii"), digest_size=16).hexdigest()
    return layout.cache_inputs / f"river_km_{key}.nc"


def _read_river_km_input_cache(
    path: Path,
    *,
    fingerprint: str,
) -> tuple[RiverKmPoint, ...]:
    netcdf4 = import_module("netCDF4")
    with netcdf4.Dataset(path, mode="r") as dataset:
        if dataset.getncattr("d3dplot_fingerprint") != fingerprint:
            raise ValueError("River-km input-cache fingerprint changed while reading.")
        if "point" not in dataset.dimensions:
            raise ValueError("River-km input cache is missing its point dimension.")
        variables = dataset.variables
        required = {"x", "y", "label", "km", "km_text"}
        missing = required.difference(variables)
        if missing:
            raise ValueError(
                "River-km input cache is missing variables: "
                + ", ".join(sorted(missing))
            )
        count = len(dataset.dimensions["point"])
        x_values = np.asarray(variables["x"][:], dtype=np.float64)
        y_values = np.asarray(variables["y"][:], dtype=np.float64)
        km_values = np.asarray(variables["km"][:], dtype=np.float64)
        labels = tuple(str(value) for value in variables["label"][:])
        km_texts = tuple(str(value) for value in variables["km_text"][:])
    if (
        any(values.shape != (count,) for values in (x_values, y_values, km_values))
        or len(labels) != count
        or len(km_texts) != count
    ):
        raise ValueError("River-km input cache has inconsistent point arrays.")
    if not np.isfinite(x_values).all() or not np.isfinite(y_values).all():
        raise ValueError("River-km input cache contains non-finite coordinates.")
    if not np.isfinite(km_values).all():
        raise ValueError("River-km input cache contains non-finite km values.")
    points = tuple(
        RiverKmPoint(
            x=float(x_values[index]),
            y=float(y_values[index]),
            label=labels[index],
            km=float(km_values[index]),
            km_text=km_texts[index],
        )
        for index in range(count)
    )
    if any(not point.label.strip() or not point.branch for point in points):
        raise ValueError("River-km input cache contains an empty branch label.")
    return points


def _write_river_km_input_cache(
    path: Path,
    guard: OutputWriteGuard,
    points: Sequence[RiverKmPoint],
    *,
    fingerprint: str,
) -> None:
    netcdf4 = import_module("netCDF4")
    with atomic_path(path, guard) as temporary:
        with netcdf4.Dataset(temporary, mode="w", format="NETCDF4") as dataset:
            dataset.createDimension("point", len(points))
            for name, values in (
                ("x", [point.x for point in points]),
                ("y", [point.y for point in points]),
                ("km", [point.km for point in points]),
            ):
                dataset.createVariable(name, "f8", ("point",))[:] = values
            label_variable = dataset.createVariable("label", str, ("point",))
            km_text_variable = dataset.createVariable("km_text", str, ("point",))
            for index, point in enumerate(points):
                label_variable[index] = point.label
                km_text_variable[index] = point.km_text
            dataset.setncattr("d3dplot_fingerprint", fingerprint)
            dataset.setncattr("d3dplot_version", __version__)
            dataset.setncattr("d3dplot_cache_schema", CACHE_SCHEMA_VERSION)


def _load_river_km_points(
    path: Path,
    layout: OutputLayout,
    guard: OutputWriteGuard,
) -> tuple[tuple[RiverKmPoint, ...], str, str, ValidationIssue | None]:
    _LOGGER.info("Starting river-km input fingerprint: %s.", path)
    try:
        source_fingerprint = fingerprint_small_input(path)
    except OSError as error:
        raise RiverKmStatisticsError(
            f"Could not fingerprint river-km file {path}: {error}",
            code="V-RKM-001",
        ) from error
    cache_fingerprint = fingerprint_artifact(
        kind="river_km_input",
        schema_version=CACHE_SCHEMA_VERSION,
        tool_version=__version__,
        algorithm_version="1",
        parameters={},
        inputs=(source_fingerprint,),
    )
    cache_path = _river_km_input_cache_path(layout, cache_fingerprint)
    stale_message: str | None = None
    if cache_path.is_file():
        try:
            _LOGGER.info("Starting river-km point cache read: %s.", cache_path)
            points = _read_river_km_input_cache(
                cache_path,
                fingerprint=cache_fingerprint,
            )
        except (
            KeyError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as error:
            stale_message = (
                f"River-km input cache could not be reused and will be rebuilt: {error}"
            )
        else:
            return points, source_fingerprint, "reused", None
    try:
        _LOGGER.info("Starting river-km point read: %s.", path)
        points = read_river_km_points(path)
    except RiverKmFileError as error:
        raise RiverKmStatisticsError(str(error), code="V-RKM-001") from error
    _LOGGER.info(
        "Finished river-km point read: path=%s; points=%s.",
        path,
        len(points),
    )
    _write_river_km_input_cache(
        cache_path,
        guard,
        points,
        fingerprint=cache_fingerprint,
    )
    issue = (
        ValidationIssue(
            code="V-OUT-025",
            level="warning",
            location=str(cache_path),
            message=stale_message,
            hint="The river-km input cache was rebuilt from its current source file.",
        )
        if stale_message is not None
        else None
    )
    return (
        points,
        source_fingerprint,
        "rebuilt" if stale_message is not None else "created",
        issue,
    )


def _inspected_cache_fingerprints(
    grid_path: Path,
    merge_plan_path: Path,
) -> tuple[str, str]:
    try:
        fingerprints = read_cache_fingerprints((grid_path, merge_plan_path))
    except CacheFingerprintReadError as error:
        raise RiverKmStatisticsError(
            f"Could not fingerprint inspected geometry inputs: {error}",
            code="V-OUT-020",
        ) from error
    values = tuple(
        fingerprints[resolve_path_preserving_drive(path)]
        for path in (grid_path, merge_plan_path)
    )
    if any(value is None for value in values):
        raise RiverKmStatisticsError(
            "An inspected grid or merge plan has no cache fingerprint.",
            code="V-OUT-020",
        )
    return (str(values[0]), str(values[1]))


def _geometry_cache_path(
    layout: OutputLayout,
    region_name: str,
    km_set_name: str,
) -> Path:
    return (
        layout.cache_geometry
        / "river_km_statistics"
        / quote(region_name, safe="-_.")
        / f"{quote(km_set_name, safe='-_.')}.nc"
    )


def read_prepared_region_segments(
    layout: OutputLayout,
    guard: OutputWriteGuard,
    region_name: str,
    km_set_name: str,
) -> PreparedRegionSegments:
    """Load prepared region/segment geometry from its guarded cache."""
    path = _geometry_cache_path(layout, region_name, km_set_name)
    resolved = guard.validate_target(path)
    if not resolved.is_file():
        raise RiverKmStatisticsError(
            f"Prepared geometry cache does not exist: {resolved}.",
            code="V-OUT-020",
        )
    try:
        with import_module("netCDF4").Dataset(resolved, mode="r") as dataset:
            fingerprint = str(dataset.getncattr("d3dplot_fingerprint"))
        return _read_geometry_cache(resolved, fingerprint=fingerprint)
    except RiverKmStatisticsError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise RiverKmStatisticsError(
            f"Could not read prepared geometry cache {resolved}: {error}.",
            code="V-OUT-020",
        ) from error


def _region_segment_fingerprint(
    *,
    config: RiverKmStatisticsConfig,
    region_name: str,
    km_set_name: str,
    centres: FloatArray,
    region_fingerprints: Sequence[str],
    river_fingerprint: str,
    grid_fingerprint: str,
    merge_plan_fingerprint: str,
    model_crs: str | None,
) -> str:
    region = next(item for item in config.regions if item.name == region_name)
    parameters: dict[str, object] = {
        "analysis": "river_km_statistics",
        "region": region_name,
        "concave_hull_ratio": (
            region.concave_hull.ratio if region.concave_hull is not None else None
        ),
        "km_set": km_set_name,
        "centres_km": centres.tolist(),
        "track": config.river_km.track,
        "branches": config.river_km.branches,
        "half_width_m": config.river_km.half_width_m,
        "allow_invalid_segments": config.river_km.allow_invalid_segments,
        "model_crs": model_crs,
    }
    return fingerprint_artifact(
        kind="river_km_geometry",
        schema_version=CACHE_SCHEMA_VERSION,
        tool_version=__version__,
        algorithm_version="1",
        parameters=parameters,
        inputs=(
            *region_fingerprints,
            river_fingerprint,
            grid_fingerprint,
            merge_plan_fingerprint,
        ),
    )


def _read_geometry_cache(
    path: Path,
    *,
    fingerprint: str,
) -> PreparedRegionSegments:
    netcdf4 = import_module("netCDF4")
    shapely = import_module("shapely")
    with netcdf4.Dataset(path, mode="r") as dataset:
        if dataset.getncattr("d3dplot_fingerprint") != fingerprint:
            raise ValueError("Geometry cache fingerprint changed while reading.")
        values = dataset.variables

        def read_float(name: str) -> FloatArray:
            if name not in values:
                raise ValueError(f"Geometry cache is missing {name!r}.")
            return np.asarray(values[name][:], dtype=np.float64)

        def read_uint8(name: str) -> NDArray[np.uint8]:
            if name not in values:
                raise ValueError(f"Geometry cache is missing {name!r}.")
            return np.asarray(values[name][:], dtype=np.uint8)

        def read_int32(name: str) -> NDArray[np.int32]:
            if name not in values:
                raise ValueError(f"Geometry cache is missing {name!r}.")
            return np.asarray(values[name][:], dtype=np.int32)

        region_name = str(dataset.getncattr("region_name"))
        km_set_name = str(dataset.getncattr("km_set_name"))
        region = RegionPolygon(
            geometry=shapely.from_wkb(read_uint8("region_wkb").tobytes()),
            source_crs=str(dataset.getncattr("source_crs")),
            crs=str(dataset.getncattr("crs")),
        )
        segment_geometry = RiverKmSegmentGeometry(
            provider=str(dataset.getncattr("provider")),
            centres_km=read_float("centres_km"),
            edges_km=read_float("edges_km"),
            segment_lengths_km=read_float("segment_lengths_km"),
            centre_branches=tuple(
                json.loads(str(dataset.getncattr("centre_branches")))["values"]
            ),
            edge_branches=tuple(
                json.loads(str(dataset.getncattr("edge_branches")))["values"]
            ),
            centres_xy=read_float("centres_xy"),
            edges_xy=read_float("edges_xy"),
            angles_rad=read_float("angles_rad"),
            left_cut_lines=read_float("left_cut_lines"),
            right_cut_lines=read_float("right_cut_lines"),
            polygons=read_float("polygons"),
            invalid_segment_indices=tuple(
                int(index) for index in np.flatnonzero(read_uint8("invalid_segment"))
            ),
            removed_centres_km=tuple(
                float(value)
                for value in json.loads(str(dataset.getncattr("removed_centres_km")))[
                    "values"
                ]
            ),
        )
        region_inside = read_uint8("region_inside").astype(np.bool_)
        segment = read_int32("segment_index")
        assignment = RegionSegmentAssignment(
            region_inside=region_inside,
            segment=segment,
            uncovered_face_indices=np.flatnonzero(read_uint8("uncovered_face")).astype(
                np.int64
            ),
            overlap_face_indices=np.flatnonzero(read_uint8("overlap_face")).astype(
                np.int64
            ),
            shared_boundary_face_indices=np.flatnonzero(
                read_uint8("shared_boundary_face")
            ).astype(np.int64),
            invalid_segment_indices=segment_geometry.invalid_segment_indices,
        )
        issue_data = json.loads(str(dataset.getncattr("issues")))["values"]
    issues = tuple(
        ValidationIssue(
            code=str(item["code"]),
            level="warning",
            location=str(item["location"]),
            message=str(item["message"]),
            hint=str(item["hint"]),
        )
        for item in issue_data
    )
    return PreparedRegionSegments(
        region_name=region_name,
        km_set_name=km_set_name,
        region=region,
        geometry=segment_geometry,
        assignment=assignment,
        cache_path=path,
        fingerprint=fingerprint,
        cache_action="reused",
        choices=(
            f"River-km geometry {region_name}/{km_set_name} cache reused.",
            *(
                (
                    "Bend-cutoff centres removed: "
                    + ", ".join(
                        format(value, ".8g")
                        for value in segment_geometry.removed_centres_km
                    ),
                )
                if segment_geometry.removed_centres_km
                else ()
            ),
        ),
        issues=issues,
    )


def _write_geometry_cache(
    prepared: PreparedRegionSegments,
    *,
    guard: OutputWriteGuard,
    parameters: dict[str, object],
) -> None:
    netcdf4 = import_module("netCDF4")
    shapely = import_module("shapely")
    region_wkb = np.frombuffer(shapely.to_wkb(prepared.region.geometry), dtype=np.uint8)
    assignment = prepared.assignment
    geometry = prepared.geometry
    issue_data = [
        {
            "code": issue.code,
            "location": issue.location,
            "message": issue.message,
            "hint": issue.hint,
        }
        for issue in prepared.issues
    ]
    with atomic_path(prepared.cache_path, guard) as temporary:
        with netcdf4.Dataset(temporary, mode="w", format="NETCDF4") as dataset:
            dataset.createDimension("face", assignment.segment.size)
            dataset.createDimension("segment", geometry.centres_km.size)
            dataset.createDimension("edge", geometry.edges_km.size)
            dataset.createDimension("vertex", 4)
            dataset.createDimension("coordinate", 2)
            dataset.createDimension("wkb_byte", region_wkb.size)
            dataset.createVariable("region_wkb", "u1", ("wkb_byte",))[:] = region_wkb
            dataset.createVariable(
                "region_inside", "u1", ("face",), zlib=True, complevel=4
            )[:] = assignment.region_inside
            dataset.createVariable(
                "segment_index", "i4", ("face",), zlib=True, complevel=4
            )[:] = assignment.segment
            for variable_name, indices in (
                ("uncovered_face", assignment.uncovered_face_indices),
                ("overlap_face", assignment.overlap_face_indices),
                ("shared_boundary_face", assignment.shared_boundary_face_indices),
            ):
                mask = np.zeros(assignment.segment.size, dtype=np.uint8)
                mask[indices] = 1
                dataset.createVariable(
                    variable_name, "u1", ("face",), zlib=True, complevel=4
                )[:] = mask
            invalid_mask = np.zeros(geometry.centres_km.size, dtype=np.uint8)
            invalid_mask[list(geometry.invalid_segment_indices)] = 1
            dataset.createVariable("invalid_segment", "u1", ("segment",))[:] = (
                invalid_mask
            )
            for name, array in (
                ("centres_km", geometry.centres_km),
                ("segment_lengths_km", geometry.segment_lengths_km),
                ("centres_xy", geometry.centres_xy),
                ("edges_xy", geometry.edges_xy),
                ("angles_rad", geometry.angles_rad),
                ("left_cut_lines", geometry.left_cut_lines),
                ("right_cut_lines", geometry.right_cut_lines),
                ("polygons", geometry.polygons),
            ):
                dimensions = (
                    ("segment",)
                    if name in {"centres_km", "segment_lengths_km"}
                    else ("segment", "vertex", "coordinate")
                    if name == "polygons"
                    else ("segment", "coordinate")
                    if name == "centres_xy"
                    else ("edge",)
                    if name == "angles_rad"
                    else ("edge", "coordinate")
                )
                dataset.createVariable(name, "f8", dimensions, zlib=True)[:] = array
            dataset.createVariable("edges_km", "f8", ("edge",))[:] = geometry.edges_km
            attributes: dict[str, object] = {
                "Conventions": "CF-1.10",
                "d3dplot_fingerprint": prepared.fingerprint,
                "d3dplot_parameters": canonical_json(parameters),
                "d3dplot_version": __version__,
                "d3dplot_cache_schema": CACHE_SCHEMA_VERSION,
                "region_name": prepared.region_name,
                "km_set_name": prepared.km_set_name,
                "provider": geometry.provider,
                "source_crs": prepared.region.source_crs,
                "crs": prepared.region.crs,
                "centre_branches": canonical_json(
                    {"values": list(geometry.centre_branches)}
                ),
                "edge_branches": canonical_json(
                    {"values": list(geometry.edge_branches)}
                ),
                "removed_centres_km": canonical_json(
                    {"values": list(geometry.removed_centres_km)}
                ),
                "choices": canonical_json({"values": list(prepared.choices)}),
                "issues": canonical_json({"values": issue_data}),
            }
            for name, value in attributes.items():
                dataset.setncattr(name, value)


def _cached_geometry(
    path: Path,
    fingerprint: str,
) -> tuple[PreparedRegionSegments | None, str | None]:
    if not path.exists():
        return None, None
    try:
        fingerprints = read_cache_fingerprints((path,))
        existing = fingerprints[resolve_path_preserving_drive(path)]
        if existing != fingerprint:
            return None, "Geometry cache is stale and will be rebuilt."
        prepared = _read_geometry_cache(path, fingerprint=fingerprint)
    except (
        CacheFingerprintReadError,
        EOFError,
        IndexError,
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        _shapely.errors.GEOSException,
    ) as error:
        return None, f"Geometry cache could not be reused and will be rebuilt: {error}"
    return prepared, None


def prepare_region_segments(
    config: RiverKmStatisticsConfig,
    grid_geometry: GridGeometry,
    layout: OutputLayout,
    guard: OutputWriteGuard,
    grid_path: Path,
    merge_plan_path: Path,
    *,
    model_crs: str | None = None,
) -> tuple[PreparedRegionSegments, ...]:
    """Prepare or reuse region and segment geometry in guarded NetCDF caches."""
    if layout.root != guard.root:
        raise ValueError("Output layout and write guard must use the same root.")
    if not config.river_km.sets:
        raise RiverKmStatisticsError(
            "At least one named river-km set is required.",
            code="V-CFG-001",
        )
    _LOGGER.info(
        "Starting river-km geometry preparation: regions=%s; km_sets=%s; "
        "half_width_m=%s.",
        len(config.regions),
        len(config.river_km.sets),
        config.river_km.half_width_m,
    )
    points, river_fingerprint, input_cache_action, input_cache_issue = (
        _load_river_km_points(config.river_km.file, layout, guard)
    )
    try:
        grid_fingerprint, merge_plan_fingerprint = _inspected_cache_fingerprints(
            grid_path,
            merge_plan_path,
        )
    except RiverKmStatisticsError:
        raise
    region_fingerprints: dict[str, tuple[str, ...]] = {}
    for region_config in config.regions:
        _LOGGER.info(
            "Starting region polygon fingerprint: region=%s; path=%s.",
            region_config.name,
            region_config.polygon,
        )
        region_fingerprints[region_config.name] = _source_fingerprints(
            region_config.polygon
        )
    prepared_items: list[PreparedRegionSegments] = []
    for region_config in config.regions:
        for km_set_name, raw_centres in config.river_km.sets.items():
            centres = _km_centres(raw_centres)
            cache_path = _geometry_cache_path(
                layout,
                region_config.name,
                km_set_name,
            )
            fingerprint = _region_segment_fingerprint(
                config=config,
                region_name=region_config.name,
                km_set_name=km_set_name,
                centres=centres,
                region_fingerprints=region_fingerprints[region_config.name],
                river_fingerprint=river_fingerprint,
                grid_fingerprint=grid_fingerprint,
                merge_plan_fingerprint=merge_plan_fingerprint,
                model_crs=model_crs,
            )
            cached, stale_message = _cached_geometry(cache_path, fingerprint)
            if cached is not None:
                _LOGGER.info(
                    "Reusing region/segment geometry cache: region=%s; "
                    "km_set=%s; path=%s.",
                    region_config.name,
                    km_set_name,
                    cache_path,
                )
                prepared_items.append(cached)
                continue

            try:
                _LOGGER.info(
                    "Starting region polygon read: region=%s; path=%s.",
                    region_config.name,
                    region_config.polygon,
                )
                region = read_region_polygon(
                    region_config.polygon,
                    target_crs=model_crs,
                    concave_hull_ratio=(
                        region_config.concave_hull.ratio
                        if region_config.concave_hull is not None
                        else None
                    ),
                )
                _LOGGER.info(
                    "Finished region polygon read: region=%s.",
                    region_config.name,
                )
                _LOGGER.info(
                    "Starting river-km segment geometry: region=%s; km_set=%s.",
                    region_config.name,
                    km_set_name,
                )
                segment_geometry = build_segment_geometry(
                    points,
                    centres.tolist(),
                    track=config.river_km.track,
                    branches=config.river_km.branches,
                    half_width_m=config.river_km.half_width_m,
                    allow_invalid_segments=config.river_km.allow_invalid_segments,
                )
                _LOGGER.info(
                    "Finished river-km segment geometry: region=%s; km_set=%s.",
                    region_config.name,
                    km_set_name,
                )
                _LOGGER.info(
                    "Starting face-to-segment assignment: region=%s; km_set=%s.",
                    region_config.name,
                    km_set_name,
                )
                assignment = assign_faces_to_segments(
                    grid_geometry.face_x,
                    grid_geometry.face_y,
                    region,
                    segment_geometry,
                    allow_invalid_segments=config.river_km.allow_invalid_segments,
                )
                _LOGGER.info(
                    "Finished face-to-segment assignment: region=%s; km_set=%s.",
                    region_config.name,
                    km_set_name,
                )
            except RegionPolygonError as error:
                raise RiverKmStatisticsError(str(error), code=error.code) from error
            except RiverKmGeometryError as error:
                raise RiverKmStatisticsError(str(error), code=error.code) from error
            except RegionAssignmentError as error:
                raise RiverKmStatisticsError(str(error), code=error.code) from error

            cache_action = "rebuilt" if stale_message else "created"
            choices = [
                f"River-km geometry {region_config.name}/{km_set_name} "
                f"cache {cache_action}."
            ]
            issues: list[ValidationIssue] = []
            if stale_message is not None:
                issues.append(
                    ValidationIssue(
                        code="V-OUT-025",
                        level="warning",
                        location=str(cache_path),
                        message=stale_message,
                        hint="The geometry cache was rebuilt from its current inputs.",
                    )
                )
            if assignment.uncovered_face_indices.size:
                issues.append(
                    ValidationIssue(
                        code="V-SMB-011",
                        level="warning",
                        location=f"{region_config.name}/{km_set_name}",
                        message=(
                            f"{assignment.uncovered_face_indices.size} region face(s) "
                            "within the configured river-km span are not assigned "
                            "to a segment."
                        ),
                        hint="Increase river_km.half_width_m or review the km set.",
                    )
                )
            if assignment.overlap_face_indices.size:
                issues.append(
                    ValidationIssue(
                        code="V-SMB-013",
                        level="warning",
                        location=f"{region_config.name}/{km_set_name}",
                        message=(
                            f"{assignment.overlap_face_indices.size} face(s) overlap "
                            "non-shared segment boundaries; the lower index wins."
                        ),
                        hint="Review segment geometry or disable the override.",
                    )
                )
            if segment_geometry.invalid_segment_indices:
                issues.append(
                    ValidationIssue(
                        code="V-SMB-010",
                        level="warning",
                        location=f"{region_config.name}/{km_set_name}",
                        message=(
                            "Invalid segment polygons were accepted by "
                            "allow_invalid_segments."
                        ),
                        hint="Review the river-km centreline and half-width.",
                    )
                )
            if segment_geometry.removed_centres_km:
                choices.append(
                    "Bend-cutoff centres removed: "
                    + ", ".join(
                        format(value, ".8g")
                        for value in segment_geometry.removed_centres_km
                    )
                )
            parameters = {
                "analysis": "river_km_statistics",
                "region": region_config.name,
                "concave_hull_ratio": (
                    region_config.concave_hull.ratio
                    if region_config.concave_hull is not None
                    else None
                ),
                "km_set": km_set_name,
                "centres_km": centres.tolist(),
                "track": config.river_km.track,
                "branches": config.river_km.branches,
                "half_width_m": config.river_km.half_width_m,
                "allow_invalid_segments": config.river_km.allow_invalid_segments,
                "model_crs": model_crs,
            }
            prepared = PreparedRegionSegments(
                region_name=region_config.name,
                km_set_name=km_set_name,
                region=region,
                geometry=segment_geometry,
                assignment=assignment,
                cache_path=cache_path,
                fingerprint=fingerprint,
                cache_action=cache_action,
                choices=tuple(choices),
                issues=tuple(issues),
            )
            _LOGGER.info(
                "Starting region/segment geometry cache write: region=%s; "
                "km_set=%s; path=%s.",
                region_config.name,
                km_set_name,
                cache_path,
            )
            _write_geometry_cache(prepared, guard=guard, parameters=parameters)
            _LOGGER.info(
                "Finished region/segment geometry cache write: region=%s; km_set=%s.",
                region_config.name,
                km_set_name,
            )
            prepared_items.append(prepared)
    if prepared_items:
        first = prepared_items[0]
        input_choices = (
            f"River-km input cache {input_cache_action}: {config.river_km.file.name}."
        )
        prepared_items[0] = replace(
            first,
            choices=(input_choices, *first.choices),
            issues=(
                (*first.issues, input_cache_issue)
                if input_cache_issue is not None
                else first.issues
            ),
        )
    _LOGGER.info(
        "Finished river-km geometry preparation: geometry_sets=%s.",
        len(prepared_items),
    )
    return tuple(prepared_items)


def river_km_statistics_output_path(
    layout: OutputLayout,
    region_name: str,
    km_set_name: str,
    variable_name: str,
    request: QuantityRequest,
    entry: TimeEntry,
    *,
    component_grouping: str | None = None,
) -> Path:
    """Return the stable cache path for one region, set, quantity, and time."""
    variable = quote(variable_name.casefold(), safe="-_.")
    region = quote(region_name, safe="-_.")
    km_set = quote(km_set_name, safe="-_.")
    selection = quantity_selection_label(
        request,
        variable_name=variable_name,
        component_grouping=component_grouping,
    )
    timestamp = entry.time.astimezone(UTC)
    filename = timestamp.strftime("%Y%m%dT%H%M%S")
    if timestamp.microsecond:
        filename += f".{timestamp.microsecond:06d}"
    return (
        layout.cache_data
        / "river_km_statistics"
        / region
        / km_set
        / variable
        / selection
        / f"{filename}.nc"
    )


def component_labels(
    spec: VariableSpec,
    request: QuantityRequest,
    component_count: int,
    *,
    fraction_names: Sequence[str | None] = (),
) -> tuple[str, ...]:
    """Label one retained extra dimension, using sediment names when available."""
    remaining = list(spec.extra_dims)
    if request.sum_fractions or (
        request.fractions is not None and len(request.fractions) == 1
    ):
        if "nSedTot" in remaining:
            remaining.remove("nSedTot")
    if (
        request.layers is not None
        and len(request.layers) == 1
        and "nBedLayers" in remaining
    ):
        remaining.remove("nBedLayers")
    if not remaining and component_count == 0:
        return ()
    if len(remaining) != 1:
        raise RiverKmStatisticsError(
            "SMB supports at most one retained extra quantity dimension.",
            code="V-VAR-005",
        )
    dimension = remaining[0]
    if dimension == "nSedTot":
        indices = request.fractions or tuple(range(1, component_count + 1))
        if len(indices) != component_count:
            raise RiverKmStatisticsError(
                "Selected sediment-fraction labels do not match the quantity shape.",
                code="V-VAR-004",
            )
        labels: list[str] = []
        for index in indices:
            name = (
                fraction_names[index - 1] if 1 <= index <= len(fraction_names) else None
            )
            labels.append(name if name else f"fraction_{index}")
        return tuple(labels)
    if dimension == "nBedLayers":
        indices = request.layers or tuple(range(component_count))
        if len(indices) != component_count:
            raise RiverKmStatisticsError(
                "Selected bed-layer labels do not match the quantity shape.",
                code="V-VAR-004",
            )
        return tuple(f"layer_{index}" for index in indices)
    return tuple(f"component_{index}" for index in range(1, component_count + 1))


def select_time_average_outputs(
    time_axis: TimeAxis,
    config: RiverKmStatisticsConfig,
    *,
    on_corrupt_output: CorruptOutputPolicy = "mask",
) -> tuple[TimeSelectionResult, ...]:
    """Resolve average times through TimeAxis for plotting and process planning.

    Pass these results to ``AnalysisTimeSelection.additional_selections`` so every
    requested average output is included in the shared processing plan.
    """
    return tuple(
        time_axis.select(
            average.times,
            naive_time_zone=time_axis.source_utc_offset,
            duplicate_morphological_times=config.duplicate_morphological_times,
            on_corrupt_output=on_corrupt_output,
        )
        for average in config.time_averages
    )


def _write_segment_statistics_file(
    path: Path,
    guard: OutputWriteGuard,
    *,
    config: RiverKmStatisticsConfig,
    prepared: PreparedRegionSegments,
    request: QuantityRequest,
    spec: VariableSpec,
    values: ArrayLike,
    face_area: ArrayLike,
    component_names: Sequence[str],
    entry: TimeEntry,
    plan_index: int,
    grid_path: Path,
    input_fingerprints: Sequence[str],
    dtype: Literal["float32", "float64"],
    segment_width_m: ArrayLike | None,
    fraction_grouping: FractionGroupingConfig | None,
    fraction_diameters_m: Sequence[float | None],
    component_grouping: str | None,
) -> Path:
    netcdf4 = import_module("netCDF4")
    data = np.asarray(values, dtype=np.float64)
    if (
        data.ndim not in {1, 2}
        or data.shape[0] != prepared.assignment.segment.size
        or (data.ndim == 1 and component_names)
        or (data.ndim == 2 and len(component_names) != data.shape[1])
    ):
        raise RiverKmStatisticsError(
            f"Quantity {request.requested_name!r} has unsupported face/component "
            f"shape {data.shape}.",
            code="V-VAR-005",
        )
    if not config.statistics:
        raise RiverKmStatisticsError(
            "At least one spatial statistic must be configured.",
            code="V-CFG-001",
        )

    lengths_m = prepared.geometry.segment_lengths_km * 1000.0
    reduced = compute_segment_statistics(
        data,
        prepared.assignment.segment,
        face_area,
        lengths_m,
    )
    stored_statistics = [
        name for name in config.statistics if name != "bed_slope_streamwise"
    ]
    if "bed_slope_streamwise" in config.statistics and "mean" not in stored_statistics:
        stored_statistics.insert(0, "mean")
    statistic_values = np.stack([reduced[name] for name in stored_statistics], axis=1)
    if fraction_grouping is not None:
        try:
            statistic_values, grouped_names = group_sediment_fraction_components(
                statistic_values,
                fraction_diameters_m,
                fraction_grouping.limits_m,
                fraction_grouping.labels,
            )
        except SedimentGroupingError as error:
            raise RiverKmStatisticsError(
                f"Could not group {request.requested_name!r} into sediment classes: "
                f"{error}",
                code="V-SMB-015",
            ) from error
        component_names = grouped_names
    elif data.ndim == 1:
        statistic_values = statistic_values[:, :, 0]

    parameters: dict[str, object] = {
        "analysis": "river_km_statistics",
        "region": prepared.region_name,
        "km_set": prepared.km_set_name,
        "variable": spec.name,
        "quantity_version": spec.version,
        "requested_name": request.requested_name.casefold(),
        "fractions": list(request.fractions) if request.fractions is not None else None,
        "layers": list(request.layers) if request.layers is not None else None,
        "sum_fractions": request.sum_fractions,
        "active_sediment_thickness_threshold_m": (
            request.active_sediment_thickness_threshold_m
        ),
        "analysis_active_sediment_thickness_threshold_m": (
            config.active_sediment_thickness_threshold_m
        ),
        "statistics": list(config.statistics),
        "multiply_by_width": config.multiply_by_width,
        "fraction_grouping": (
            {
                "variables": list(fraction_grouping.variables),
                "limits_m": list(fraction_grouping.limits_m),
                "labels": list(fraction_grouping.labels),
                "component_diameters_m": list(fraction_diameters_m),
            }
            if fraction_grouping is not None
            else None
        ),
        "dtype": dtype,
        "plan": plan_index,
        "subrun": entry.subrun,
        "local_index": entry.local_index,
        "morphological_time": (
            entry.morph_time.astimezone(UTC).isoformat()
            if entry.morph_time is not None
            else None
        ),
    }
    fingerprint = fingerprint_artifact(
        kind="river_km_statistics_data",
        schema_version=CACHE_SCHEMA_VERSION,
        tool_version=__version__,
        algorithm_version="1",
        parameters=parameters,
        inputs=(*input_fingerprints, prepared.fingerprint),
        time=entry.time.astimezone(UTC).isoformat(),
    )
    stored_values = np.asarray(statistic_values, dtype=np.dtype(dtype))
    relative_grid_path = os.path.relpath(grid_path, path.parent).replace("\\", "/")
    current_time = entry.time.astimezone(UTC)
    morph_time = (
        entry.morph_time.astimezone(UTC).isoformat()
        if entry.morph_time is not None
        else ""
    )
    with atomic_path(path, guard) as temporary:
        with netcdf4.Dataset(temporary, mode="w", format="NETCDF4") as dataset:
            dataset.createDimension("segment", lengths_m.size)
            dataset.createDimension("statistic", len(stored_statistics))
            dataset.createVariable("km_centre", "f8", ("segment",))[:] = (
                prepared.geometry.centres_km
            )
            dataset.createVariable("km_edge_start", "f8", ("segment",))[:] = (
                prepared.geometry.edges_km[:-1]
            )
            dataset.createVariable("km_edge_end", "f8", ("segment",))[:] = (
                prepared.geometry.edges_km[1:]
            )
            dataset.createVariable("segment_length_m", "f8", ("segment",))[:] = (
                lengths_m
            )
            statistic_coordinate = dataset.createVariable(
                "statistic",
                str,
                ("statistic",),
            )
            for index, statistic_name in enumerate(stored_statistics):
                statistic_coordinate[index] = statistic_name
            dimensions: tuple[str, ...] = ("segment", "statistic")
            if component_names:
                dataset.createDimension("component", len(component_names))
                dimensions = (*dimensions, "component")
                component_coordinate = dataset.createVariable(
                    "component_name",
                    str,
                    ("component",),
                )
                for index, name in enumerate(component_names):
                    component_coordinate[index] = name
            value_variable = dataset.createVariable(
                "values",
                np.dtype(dtype),
                dimensions,
                zlib=True,
                complevel=4,
                fill_value=np.nan,
            )
            value_variable[:] = stored_values
            value_variable.setncattr("units", spec.units)
            value_variable.setncattr("long_name", spec.name)
            if segment_width_m is not None:
                width = np.asarray(segment_width_m, dtype=np.float64)
                if width.shape != lengths_m.shape:
                    raise ValueError("segment_width_m must match the segment count.")
                dataset.createVariable(
                    "segment_width_m",
                    "f8",
                    ("segment",),
                )[:] = width
                dataset.variables["segment_width_m"].setncattr(
                    "basis",
                    config.multiply_by_width,
                )
            attributes: dict[str, object] = {
                "Conventions": "CF-1.10",
                "d3dplot_fingerprint": fingerprint,
                "d3dplot_parameters": canonical_json(parameters),
                "d3dplot_version": __version__,
                "d3dplot_cache_schema": CACHE_SCHEMA_VERSION,
                "time": current_time.isoformat(),
                "time_utc": current_time.timestamp(),
                "time_utc_units": "seconds since 1970-01-01 00:00:00 +00:00",
                "time_utc_calendar": "proleptic_gregorian",
                "morph_time": morph_time,
                "subrun": entry.subrun,
                "local_index": entry.local_index,
                "variable": spec.name,
                "selection": quantity_selection_label(
                    request,
                    variable_name=spec.name,
                    component_grouping=component_grouping,
                ),
                "region": prepared.region_name,
                "km_set": prepared.km_set_name,
                "grid_file": relative_grid_path,
                "plan": plan_index,
            }
            for attribute_name, attribute_value in attributes.items():
                dataset.setncattr(attribute_name, attribute_value)
    return path


def write_segment_statistics_cache(
    layout: OutputLayout,
    guard: OutputWriteGuard,
    config: RiverKmStatisticsConfig,
    prepared: PreparedRegionSegments,
    request: QuantityRequest,
    spec: VariableSpec,
    values: ArrayLike,
    face_area: ArrayLike,
    fraction_names: Sequence[str | None],
    entry: TimeEntry,
    plan_index: int,
    grid_path: Path,
    input_fingerprints: Sequence[str],
    *,
    dtype: Literal["float32", "float64"],
    width_area: ArrayLike | None = None,
    fraction_diameters_m: Sequence[float | None] = (),
) -> Path:
    """Reduce and persist one quantity through the guarded data-cache writer."""
    data = np.asarray(values, dtype=np.float64)
    if data.ndim not in {1, 2} or data.shape[0] != prepared.assignment.segment.size:
        raise RiverKmStatisticsError(
            f"Quantity {request.requested_name!r} has unsupported face/component "
            f"shape {data.shape}.",
            code="V-VAR-005",
        )
    if data.ndim == 2 and data.shape[1] == 0:
        raise RiverKmStatisticsError(
            "A retained quantity component dimension cannot be empty.",
            code="V-VAR-003",
        )
    component_names = component_labels(
        spec,
        request,
        data.shape[1] if data.ndim == 2 else 0,
        fraction_names=fraction_names,
    )
    (
        fraction_grouping,
        selected_diameters_m,
        _,
        component_grouping,
    ) = _fraction_grouping_details(
        config,
        request,
        spec,
        fraction_diameters_m,
    )
    if fraction_grouping is not None:
        component_count = data.shape[1] if data.ndim == 2 else 1
        if len(selected_diameters_m) != component_count:
            raise RiverKmStatisticsError(
                f"Quantity {request.requested_name!r} has {component_count} "
                "components, but the selected .sed fractions provide "
                f"{len(selected_diameters_m)} diameters.",
                code="V-SMB-015",
            )
    segment_width_m: FloatArray | None = None
    if config.multiply_by_width == "flow":
        segment_width_m = compute_segment_width(
            face_area,
            prepared.assignment.segment,
            prepared.geometry.segment_lengths_km * 1000.0,
        )
    elif config.multiply_by_width == "morphological":
        if width_area is None:
            raise RiverKmStatisticsError(
                "Morphological width requires the morphological active-area quantity.",
                code="V-VAR-002",
            )
        segment_width_m = compute_segment_width(
            width_area,
            prepared.assignment.segment,
            prepared.geometry.segment_lengths_km * 1000.0,
        )
    path = river_km_statistics_output_path(
        layout,
        prepared.region_name,
        prepared.km_set_name,
        spec.name,
        request,
        entry,
        component_grouping=component_grouping,
    )
    return _write_segment_statistics_file(
        path,
        guard,
        config=config,
        prepared=prepared,
        request=request,
        spec=spec,
        values=data,
        face_area=face_area,
        component_names=component_names,
        entry=entry,
        plan_index=plan_index,
        grid_path=grid_path,
        input_fingerprints=input_fingerprints,
        dtype=dtype,
        segment_width_m=segment_width_m,
        fraction_grouping=fraction_grouping,
        fraction_diameters_m=selected_diameters_m,
        component_grouping=component_grouping,
    )


def _fraction_grouping_details(
    config: RiverKmStatisticsConfig,
    request: QuantityRequest,
    spec: VariableSpec,
    fraction_diameters_m: Sequence[float | None],
) -> tuple[
    FractionGroupingConfig | None,
    tuple[float | None, ...],
    tuple[ClassMembership, ...],
    str | None,
]:
    grouping = config.fraction_grouping
    if grouping is None or not grouping.applies_to(request.requested_name, spec.name):
        return None, (), (), None
    if request.sum_fractions:
        raise RiverKmStatisticsError(
            "Sediment-class grouping requires retained fraction components.",
            code="V-SMB-015",
        )

    all_diameters = tuple(fraction_diameters_m)
    if request.fractions is None:
        selected_diameters = all_diameters
    else:
        if any(index > len(all_diameters) for index in request.fractions):
            raise RiverKmStatisticsError(
                f"Fraction selection for {request.requested_name!r} has no matching "
                ".sed grain diameter.",
                code="V-SMB-015",
            )
        selected_diameters = tuple(
            all_diameters[index - 1] for index in request.fractions
        )

    try:
        memberships = sediment_class_membership(
            selected_diameters,
            grouping.limits_m,
            grouping.labels,
        )
    except SedimentGroupingError as error:
        raise RiverKmStatisticsError(
            f"Could not group {request.requested_name!r} into sediment classes: "
            f"{error}",
            code="V-SMB-015",
        ) from error
    return (
        grouping,
        selected_diameters,
        memberships,
        sediment_class_grouping_key(grouping.limits_m, grouping.labels),
    )


def fraction_grouping_choice(
    config: RiverKmStatisticsConfig,
    request: QuantityRequest,
    spec: VariableSpec,
    fraction_diameters_m: Sequence[float | None],
) -> str | None:
    """Describe the configured sediment classes for the run diagnostics."""
    grouping, _, memberships, _ = _fraction_grouping_details(
        config,
        request,
        spec,
        fraction_diameters_m,
    )
    if grouping is None:
        return None
    classes = "; ".join(
        f"{label} (fractions {', '.join(str(index + 1) for index in indices)})"
        for label, indices in memberships
    )
    return (
        f"SMB {request.requested_name}: grouped sediment fractions into classes "
        f"using limits {list(grouping.limits_m)} m: {classes}."
    )


def compute_segment_statistics(
    values: ArrayLike,
    segment_index: ArrayLike,
    face_area: ArrayLike,
    segment_length_m: ArrayLike,
) -> dict[str, FloatArray]:
    """Reduce face values per segment, retaining a component axis in every result."""
    data = np.asarray(values, dtype=np.float64)
    if data.ndim == 1:
        data = data[:, np.newaxis]
    if data.ndim != 2:
        raise ValueError("values must have shape (face,) or (face, component).")

    segments = np.asarray(segment_index)
    areas = np.asarray(face_area, dtype=np.float64)
    lengths = np.asarray(segment_length_m, dtype=np.float64)
    if segments.ndim != 1 or not np.issubdtype(segments.dtype, np.integer):
        raise ValueError("segment_index must be a one-dimensional integer array.")
    if areas.ndim != 1 or areas.shape != (data.shape[0],):
        raise ValueError("face_area must have one value per face.")
    if segments.shape != (data.shape[0],):
        raise ValueError("segment_index must have one value per face.")
    if (
        lengths.ndim != 1
        or lengths.size == 0
        or not np.isfinite(lengths).all()
        or np.any(lengths <= 0)
    ):
        raise ValueError("segment_length_m must contain finite positive lengths.")
    if np.any(segments < -1) or np.any(segments >= lengths.size):
        raise ValueError("segment_index contains an out-of-range segment.")

    result = {
        name: np.full((lengths.size, data.shape[1]), np.nan, dtype=np.float64)
        for name in SPATIAL_STATISTICS
    }
    for component in range(data.shape[1]):
        component_values = data[:, component]
        valid = (segments >= 0) & np.isfinite(component_values)
        selected_segments = segments[valid]
        selected_values = component_values[valid]
        counts = np.bincount(
            selected_segments,
            minlength=lengths.size,
        ).astype(np.float64)
        nonempty = counts > 0
        sums = np.bincount(
            selected_segments,
            weights=selected_values,
            minlength=lengths.size,
        )

        means = np.full(lengths.size, np.nan, dtype=np.float64)
        np.divide(sums, counts, out=means, where=nonempty)
        squared_deviations = np.bincount(
            selected_segments,
            weights=(selected_values - means[selected_segments]) ** 2,
            minlength=lengths.size,
        )
        standard_deviations = np.full(lengths.size, np.nan, dtype=np.float64)
        np.divide(
            squared_deviations,
            counts - 1,
            out=standard_deviations,
            where=counts > 1,
        )
        np.sqrt(standard_deviations, out=standard_deviations)

        maxima = np.full(lengths.size, -np.inf, dtype=np.float64)
        minima = np.full(lengths.size, np.inf, dtype=np.float64)
        np.maximum.at(maxima, selected_segments, selected_values)
        np.minimum.at(minima, selected_segments, selected_values)
        maxima[~nonempty] = np.nan
        minima[~nonempty] = np.nan

        result["mean"][:, component] = means
        result["std"][:, component] = standard_deviations
        result["max"][:, component] = maxima
        result["min"][:, component] = minima
        result["count"][:, component] = counts

        component_sums = sums.copy()
        component_sums[~nonempty] = np.nan
        result["sum"][:, component] = component_sums
        result["sum_per_length"][:, component] = component_sums / lengths

        area_valid = valid & np.isfinite(areas) & (areas > 0)
        weighted_segments = segments[area_valid]
        weighted_areas = areas[area_valid]
        weighted_sum = np.bincount(
            weighted_segments,
            weights=component_values[area_valid] * weighted_areas,
            minlength=lengths.size,
        )
        total_area = np.bincount(
            weighted_segments,
            weights=weighted_areas,
            minlength=lengths.size,
        )
        weighted_mean = np.full(lengths.size, np.nan, dtype=np.float64)
        np.divide(weighted_sum, total_area, out=weighted_mean, where=total_area > 0)
        result["mean_area_weighted"][:, component] = weighted_mean

    return result


def compute_segment_width(
    face_area: ArrayLike,
    segment_index: ArrayLike,
    segment_length_m: ArrayLike,
) -> FloatArray:
    """Calculate area-derived width per segment, excluding invalid face areas."""
    areas = np.asarray(face_area, dtype=np.float64)
    segments = np.asarray(segment_index)
    lengths = np.asarray(segment_length_m, dtype=np.float64)
    if segments.ndim != 1 or not np.issubdtype(segments.dtype, np.integer):
        raise ValueError("segment_index must be a one-dimensional integer array.")
    if areas.ndim != 1 or areas.shape != segments.shape:
        raise ValueError("face_area and segment_index must have matching 1D shapes.")
    if (
        lengths.ndim != 1
        or lengths.size == 0
        or not np.isfinite(lengths).all()
        or np.any(lengths <= 0)
    ):
        raise ValueError("segment_length_m must contain finite positive lengths.")
    if np.any(segments < -1) or np.any(segments >= lengths.size):
        raise ValueError("segment_index contains an out-of-range segment.")

    valid = (segments >= 0) & np.isfinite(areas) & (areas > 0)
    area_sum = np.bincount(
        segments[valid],
        weights=areas[valid],
        minlength=lengths.size,
    )
    return area_sum / lengths


def multiply_by_width(values: ArrayLike, width_m: ArrayLike) -> FloatArray:
    """Multiply segment values by width while preserving trailing dimensions."""
    data = np.asarray(values, dtype=np.float64)
    widths = np.asarray(width_m, dtype=np.float64)
    if data.ndim == 0 or widths.ndim != 1 or data.shape[0] != widths.size:
        raise ValueError("width_m must have one value for each leading segment.")
    reshape = (widths.size,) + (1,) * (data.ndim - 1)
    return data * widths.reshape(reshape)


def bed_slope_streamwise(values: ArrayLike, centres_km: ArrayLike) -> FloatArray:
    """Calculate the central-difference streamwise slope per segment."""
    data = np.asarray(values, dtype=np.float64)
    centres = np.asarray(centres_km, dtype=np.float64)
    if (
        data.ndim == 0
        or centres.ndim != 1
        or centres.size != data.shape[0]
        or not np.isfinite(centres).all()
        or np.any(np.diff(centres) <= 0)
    ):
        raise ValueError("centres_km must be finite, increasing, and match values.")
    result = np.full(data.shape, np.nan, dtype=np.float64)
    if centres.size > 2:
        denominator = (centres[2:] - centres[:-2]) * 1000.0
        shape = (denominator.size,) + (1,) * (data.ndim - 1)
        result[1:-1] = (data[2:] - data[:-2]) / denominator.reshape(shape)
    return result


def difference_regions(values_a: ArrayLike, values_b: ArrayLike) -> FloatArray:
    """Subtract region B from region A, preserving NaN propagation."""
    first = np.asarray(values_a, dtype=np.float64)
    second = np.asarray(values_b, dtype=np.float64)
    if first.shape != second.shape:
        raise ValueError("Region values must have matching shapes.")
    return first - second


def difference_simulation_relative(
    values: ArrayLike,
    reference: ArrayLike,
) -> FloatArray:
    """Calculate relative simulation differences as percentages."""
    current = np.asarray(values, dtype=np.float64)
    baseline = np.asarray(reference, dtype=np.float64)
    if current.shape != baseline.shape:
        raise ValueError("Simulation and reference values must have matching shapes.")
    result = np.full(current.shape, np.nan, dtype=np.float64)
    valid = np.isfinite(baseline) & (baseline != 0.0)
    np.divide(current - baseline, baseline, out=result, where=valid)
    result[valid] *= 100.0
    return result


def _ordered_time_intervals(times: Sequence[datetime]) -> FloatArray:
    utc_times: list[datetime] = []
    for instant in times:
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise RiverKmStatisticsError(
                "Post-processing times must be timezone-aware."
            )
        utc_times.append(instant.astimezone(UTC))
    intervals = np.asarray(
        [
            (following - previous).total_seconds()
            for previous, following in zip(utc_times, utc_times[1:], strict=False)
        ],
        dtype=np.float64,
    )
    if not np.isfinite(intervals).all() or np.any(intervals <= 0):
        raise RiverKmStatisticsError(
            "Post-processing times must be distinct and strictly increasing."
        )
    return intervals


def compute_time_average(
    values: ArrayLike,
    times: Sequence[datetime],
) -> dict[str, FloatArray]:
    """Calculate weighted mean/std, extrema, and dominant value over UTC times."""
    data = np.asarray(values, dtype=np.float64)
    if data.ndim == 0 or data.shape[0] != len(times):
        raise ValueError("values must have one leading row per selected time.")
    if len(times) < 2:
        raise RiverKmStatisticsError(
            "A time average requires at least two distinct ordered outputs."
        )
    intervals = _ordered_time_intervals(times)
    if intervals.size == 0:
        raise RiverKmStatisticsError(
            "A time average requires at least two distinct ordered outputs."
        )
    weights = np.concatenate(
        (
            intervals[:1],
            (intervals[:-1] + intervals[1:]) / 2.0,
            intervals[-1:],
        )
    )
    total_weight = float(np.sum(weights))
    weight_shape = (weights.size,) + (1,) * (data.ndim - 1)
    weighted = weights.reshape(weight_shape)
    mean = np.sum(data * weighted, axis=0) / total_weight
    variance = np.sum(weighted * (data - mean) ** 2, axis=0) / total_weight
    with np.errstate(invalid="ignore", over="ignore"):
        dominant = np.power(
            np.sum(weighted * np.power(data, 5.0 / 3.0), axis=0) / total_weight,
            3.0 / 5.0,
        )
    return {
        "mean": np.asarray(mean, dtype=np.float64),
        "std": np.sqrt(variance),
        "max": np.max(data, axis=0),
        "min": np.min(data, axis=0),
        "dominant": np.asarray(dominant, dtype=np.float64),
    }


def compute_cumulative(
    values: ArrayLike,
    times: Sequence[datetime],
) -> FloatArray:
    """Integrate left-block values over ordered UTC time intervals."""
    data = np.asarray(values, dtype=np.float64)
    if data.ndim == 0 or data.shape[0] != len(times):
        raise ValueError("values must have one leading row per selected time.")
    if not times:
        raise ValueError("A cumulative time series needs at least one output.")
    intervals = _ordered_time_intervals(times)
    result = np.zeros(data.shape, dtype=np.float64)
    for index, interval in enumerate(intervals):
        result[index + 1] = result[index] + data[index] * interval
    return result


__all__ = [
    "SPATIAL_STATISTICS",
    "RiverKmStatisticsError",
    "bed_slope_streamwise",
    "compute_cumulative",
    "compute_segment_statistics",
    "compute_segment_width",
    "compute_time_average",
    "difference_simulation_relative",
    "difference_regions",
    "fraction_grouping_choice",
    "multiply_by_width",
    "read_prepared_region_segments",
    "select_time_average_outputs",
]
