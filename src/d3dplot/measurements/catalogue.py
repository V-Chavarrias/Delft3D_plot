# SPDX-License-Identifier: GPL-3.0-or-later
"""Read and inspect CSV/YAML map-measurement catalogues."""

from __future__ import annotations

import csv
import math
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from importlib import import_module
from pathlib import Path
from typing import Any, Literal

import yaml

from d3dplot import __version__
from d3dplot.config.project import PathsConfig
from d3dplot.simulation.time_utils import timezone_from_offset
from d3dplot.store import (
    CACHE_SCHEMA_VERSION,
    OutputLayout,
    OutputWriteGuard,
    atomic_write_text,
    fingerprint_artifact,
    fingerprint_model_output,
    fingerprint_small_input,
)

_Rasterio: Any = import_module("rasterio")
_Pyogrio: Any = import_module("pyogrio")

_METADATA_FIELDS = (
    "catalogue_fingerprint",
    "index",
    "time",
    "path",
    "factor",
    "kind",
    "crs",
    "left",
    "bottom",
    "right",
    "top",
    "source_fingerprint",
    "value_field",
)


class MapMeasurementError(RuntimeError):
    """Map-measurement input is invalid or cannot be prepared."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class MapMeasurementEntry:
    """One catalogue item with inspected source metadata."""

    time: datetime
    path: Path
    factor: float
    kind: Literal["raster", "polygons"]
    crs: str | None
    bounds: tuple[float, float, float, float]
    source_fingerprint: str
    value_field: str | None = None


@dataclass(frozen=True, slots=True)
class MapMeasurementCatalogue:
    """Inspected catalogue and its stable fingerprint."""

    path: Path
    entries: tuple[MapMeasurementEntry, ...]
    fingerprint: str


def inspect_map_catalogue(
    catalogue_path: str | Path,
    paths: PathsConfig,
    layout: OutputLayout,
    guard: OutputWriteGuard,
    *,
    time_zone: str | None,
    diagnostic: Callable[[str], None] | None = None,
) -> MapMeasurementCatalogue:
    """Parse a catalogue, inspect its sources, and cache their metadata."""
    resolved_catalogue = paths.resolve_path(catalogue_path, base_dir=Path.cwd())
    if not resolved_catalogue.is_file():
        raise MapMeasurementError(
            "V-MEA-001",
            f"Measurement catalogue does not exist: {resolved_catalogue}.",
        )
    try:
        catalogue_hash = fingerprint_small_input(resolved_catalogue)
        raw_entries = _read_catalogue(
            resolved_catalogue,
            paths,
            _timezone(time_zone),
        )
        source_fingerprints = tuple(
            _source_fingerprint(entry[1], paths) for entry in raw_entries
        )
        fingerprint = fingerprint_artifact(
            kind="map_measurement_catalogue",
            schema_version=CACHE_SCHEMA_VERSION,
            tool_version=__version__,
            algorithm_version="1",
            parameters={
                "catalogue": str(resolved_catalogue),
                "entries": [
                    {
                        "time": entry[0].isoformat(),
                        "path": str(entry[1]),
                        "factor": entry[2],
                        "kind": entry[3],
                        "crs": entry[4],
                        "value_field": entry[5],
                    }
                    for entry in raw_entries
                ],
            },
            inputs=(catalogue_hash, *source_fingerprints),
        )
    except MapMeasurementError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError, yaml.YAMLError) as error:
        raise MapMeasurementError(
            "V-MEA-001",
            f"Could not inspect measurement catalogue {resolved_catalogue}: {error}",
        ) from error

    metadata_path = layout.cache_inputs / f"catalogue_{fingerprint[:24]}.csv"
    cached_entries = _read_metadata_cache(
        metadata_path,
        fingerprint,
        raw_entries,
        source_fingerprints,
    )
    if cached_entries is not None:
        if diagnostic is not None:
            diagnostic(f"Reused inspected measurement catalogue {metadata_path}.")
        return MapMeasurementCatalogue(
            resolved_catalogue,
            cached_entries,
            fingerprint,
        )
    if metadata_path.is_file() and diagnostic is not None:
        diagnostic(
            f"Unreadable measurement metadata cache was rebuilt: {metadata_path}"
        )

    entries: list[MapMeasurementEntry] = []
    for raw, source_fingerprint in zip(raw_entries, source_fingerprints, strict=True):
        time, path, factor, kind, configured_crs, value_field = raw
        try:
            crs, bounds = _source_metadata(path, kind, configured_crs)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise MapMeasurementError(
                "V-MEA-001",
                f"Could not inspect measurement source {path}: {error}",
            ) from error
        entries.append(
            MapMeasurementEntry(
                time=time,
                path=path,
                factor=factor,
                kind=kind,
                crs=crs,
                bounds=bounds,
                source_fingerprint=source_fingerprint,
                value_field=value_field,
            )
        )
    result = MapMeasurementCatalogue(
        resolved_catalogue,
        tuple(entries),
        fingerprint,
    )
    _write_metadata_cache(metadata_path, result, guard)
    return result


def _read_catalogue(
    path: Path,
    paths: PathsConfig,
    default_zone: timezone,
) -> list[
    tuple[datetime, Path, float, Literal["raster", "polygons"], str | None, str | None]
]:
    if path.suffix.casefold() in {".yaml", ".yml"}:
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise MapMeasurementError(
                "V-MEA-001",
                f"Could not read measurement catalogue {path}: {error}",
            ) from error
        records = (
            document.get("measurements", document.get("entries"))
            if isinstance(document, Mapping)
            else document
        )
        if not isinstance(records, list):
            raise MapMeasurementError(
                "V-MEA-002",
                "YAML measurement catalogue must contain a list of entries.",
            )
        rows = [record if isinstance(record, Mapping) else {} for record in records]
    else:
        try:
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                if reader.fieldnames is None:
                    raise MapMeasurementError(
                        "V-MEA-002",
                        "CSV measurement catalogue has no header.",
                    )
                columns = {
                    field.strip().casefold(): field for field in reader.fieldnames
                }
                required = {"time", "file", "factor"}
                if not required.issubset(columns):
                    raise MapMeasurementError(
                        "V-MEA-002",
                        "CSV measurement catalogue requires time, file, and "
                        "factor columns.",
                    )
                rows = [
                    {key: row.get(column, "") for key, column in columns.items()}
                    for row in reader
                ]
        except MapMeasurementError:
            raise
        except (OSError, UnicodeError, csv.Error) as error:
            raise MapMeasurementError(
                "V-MEA-001",
                f"Could not read measurement catalogue {path}: {error}",
            ) from error

    if not rows:
        raise MapMeasurementError("V-MEA-002", "Measurement catalogue is empty.")
    entries: list[
        tuple[
            datetime, Path, float, Literal["raster", "polygons"], str | None, str | None
        ]
    ] = []
    for index, row in enumerate(rows, start=1):
        try:
            time = _parse_time(row.get("time"), default_zone)
            filename = str(row.get("file", "")).strip()
            if not filename:
                raise ValueError("file must be non-empty")
            source_path = paths.resolve_path(filename, base_dir=path.parent)
            factor_value = row.get("factor")
            if factor_value is None:
                raise ValueError("factor must be provided")
            factor = float(factor_value)
            if not math.isfinite(factor):
                raise ValueError("factor must be finite")
            kind = _source_kind(source_path, row.get("type"))
            configured_crs = _optional_text(row.get("crs"))
            value_field = _optional_text(row.get("value_field"))
        except (TypeError, ValueError) as error:
            raise MapMeasurementError(
                "V-MEA-002",
                f"Invalid measurement catalogue entry {index}: {error}",
            ) from error
        entries.append(
            (
                time,
                source_path,
                factor,
                kind,
                configured_crs,
                value_field,
            )
        )
    return entries


def _parse_time(value: object, default_zone: timezone) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        parsed = datetime.fromisoformat(text)
    else:
        raise ValueError("time must be a non-empty ISO 8601 timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=default_zone)
    return parsed.astimezone(UTC)


def _timezone(offset: str | None) -> timezone:
    if offset is None:
        return UTC
    try:
        return timezone_from_offset(offset)
    except ValueError as error:
        raise MapMeasurementError("V-MEA-002", str(error)) from error


def _source_kind(
    path: Path,
    configured: object,
) -> Literal["raster", "polygons"]:
    if configured is not None and str(configured).strip():
        kind = str(configured).strip().casefold()
        if kind not in {"raster", "polygons"}:
            raise ValueError("type must be 'raster' or 'polygons'")
        return kind  # type: ignore[return-value]
    suffix = path.suffix.casefold()
    if suffix in {".tif", ".tiff", ".vrt", ".nc", ".nc4"}:
        return "raster"
    if suffix in {".shp", ".gpkg"}:
        return "polygons"
    raise ValueError(f"unsupported measurement source type: {path.suffix!r}")


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _source_fingerprint(path: Path, paths: PathsConfig) -> str:
    if not path.exists():
        raise MapMeasurementError("V-MEA-001", f"Measurement source is missing: {path}")
    suffix = path.suffix.casefold()
    if suffix == ".shp":
        sidecars = [
            path.with_suffix(extension)
            for extension in (".shp", ".shx", ".dbf", ".prj", ".cpg")
            if path.with_suffix(extension).is_file()
        ]
        inputs = tuple(fingerprint_small_input(item) for item in sidecars)
    elif suffix == ".vrt":
        dependencies = _vrt_sources(path, paths)
        inputs = (
            fingerprint_small_input(path),
            *(
                fingerprint_model_output(item, hash_content=True)
                for item in dependencies
            ),
        )
    elif suffix in {".tif", ".tiff", ".gpkg", ".nc", ".nc4"}:
        inputs = (fingerprint_model_output(path, hash_content=True),)
    else:
        inputs = (fingerprint_small_input(path),)
    return fingerprint_artifact(
        kind="map_measurement_source",
        schema_version=CACHE_SCHEMA_VERSION,
        tool_version=__version__,
        algorithm_version="1",
        parameters={"path": str(path)},
        inputs=inputs,
    )


def _vrt_sources(path: Path, paths: PathsConfig) -> tuple[Path, ...]:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as error:
        raise MapMeasurementError(
            "V-MEA-001",
            f"Could not parse measurement VRT {path}: {error}",
        ) from error
    sources: list[Path] = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] != "SourceFilename" or not node.text:
            continue
        source = node.text.strip()
        if node.attrib.get("relativeToVRT") == "1":
            source_path = paths.resolve_path(source, base_dir=path.parent)
        else:
            source_path = paths.resolve_path(source, base_dir=path.parent)
        if not source_path.is_file():
            raise MapMeasurementError(
                "V-MEA-001",
                f"VRT source file does not exist: {source_path}",
            )
        sources.append(source_path)
    if not sources:
        raise MapMeasurementError("V-MEA-002", f"VRT has no source files: {path}")
    return tuple(sources)


def _source_metadata(
    path: Path,
    kind: Literal["raster", "polygons"],
    configured_crs: str | None,
) -> tuple[str | None, tuple[float, float, float, float]]:
    if kind == "raster":
        with _Rasterio.open(path) as dataset:
            bounds = tuple(float(value) for value in dataset.bounds)
            crs = dataset.crs
            crs_text = crs.to_string() if crs is not None else configured_crs
    else:
        info = _Pyogrio.read_info(path)
        bounds_value = info.get("total_bounds")
        if bounds_value is None or len(bounds_value) != 4:
            raise ValueError("vector source has no valid bounds")
        bounds = tuple(float(value) for value in bounds_value)
        source_crs = info.get("crs")
        crs_text = str(source_crs) if source_crs is not None else configured_crs
    if len(bounds) != 4 or not all(math.isfinite(value) for value in bounds):
        raise ValueError("source bounds must contain four finite coordinates")
    if bounds[0] > bounds[2] or bounds[1] > bounds[3]:
        raise ValueError("source bounds are inverted")
    return crs_text, bounds


def _read_metadata_cache(
    path: Path,
    fingerprint: str,
    raw_entries: list[
        tuple[
            datetime, Path, float, Literal["raster", "polygons"], str | None, str | None
        ]
    ],
    source_fingerprints: tuple[str, ...],
) -> tuple[MapMeasurementEntry, ...] | None:
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != _METADATA_FIELDS:
                return None
            rows = list(reader)
        if len(rows) != len(raw_entries):
            return None
        entries: list[MapMeasurementEntry] = []
        for index, (row, raw) in enumerate(zip(rows, raw_entries, strict=True)):
            time, source_path, factor, kind, _configured_crs, value_field = raw
            if (
                row["catalogue_fingerprint"] != fingerprint
                or int(row["index"]) != index
                or row["time"] != time.isoformat()
                or row["path"] != str(source_path)
                or float(row["factor"]) != factor
                or row["kind"] != kind
                or row["source_fingerprint"] != source_fingerprints[index]
                or (row["value_field"] or None) != value_field
            ):
                return None
            bounds = tuple(
                float(row[key]) for key in ("left", "bottom", "right", "top")
            )
            entries.append(
                MapMeasurementEntry(
                    time=time,
                    path=source_path,
                    factor=factor,
                    kind=kind,
                    crs=row["crs"] or None,
                    bounds=bounds,  # type: ignore[arg-type]
                    source_fingerprint=row["source_fingerprint"],
                    value_field=value_field,
                )
            )
        return tuple(entries)
    except (OSError, csv.Error, KeyError, TypeError, ValueError):
        return None


def _write_metadata_cache(
    path: Path,
    catalogue: MapMeasurementCatalogue,
    guard: OutputWriteGuard,
) -> None:
    from io import StringIO

    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=_METADATA_FIELDS, lineterminator="\n")
    writer.writeheader()
    for index, entry in enumerate(catalogue.entries):
        writer.writerow(
            {
                "catalogue_fingerprint": catalogue.fingerprint,
                "index": index,
                "time": entry.time.isoformat(),
                "path": str(entry.path),
                "factor": entry.factor,
                "kind": entry.kind,
                "crs": entry.crs or "",
                "left": entry.bounds[0],
                "bottom": entry.bounds[1],
                "right": entry.bounds[2],
                "top": entry.bounds[3],
                "source_fingerprint": entry.source_fingerprint,
                "value_field": entry.value_field or "",
            }
        )
    atomic_write_text(path, stream.getvalue(), guard)
