# SPDX-License-Identifier: GPL-3.0-or-later
"""Measured station series for the HIS analysis, read from a CSV catalogue.

The catalogue has one row per measured series with the columns ``station``,
``variable`` and ``file``, and the optional columns ``factor`` (default 1) and
``layer`` (default 1). Each file has ``time`` (ISO 8601) and ``value`` columns.
Naive times use the measurement time zone.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from d3dplot import __version__
from d3dplot.config.project import PathsConfig
from d3dplot.simulation.his import HisError
from d3dplot.simulation.time_utils import timezone_from_offset
from d3dplot.store import CACHE_SCHEMA_VERSION, fingerprint_artifact
from d3dplot.store.fingerprint import fingerprint_small_input

_REQUIRED_CATALOGUE_COLUMNS = frozenset({"station", "variable", "file"})
_REQUIRED_FILE_COLUMNS = frozenset({"time", "value"})


@dataclass(frozen=True, slots=True)
class HisMeasurement:
    """One measured series, scaled by its factor and sorted by time."""

    station: str
    variable: str
    layer: int
    time_seconds: NDArray[np.float64]
    values: NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class HisMeasurementCatalogue:
    """Parsed measurement catalogue and its stable fingerprint."""

    path: Path
    entries: tuple[HisMeasurement, ...]
    fingerprint: str

    def find(self, station: str, variable: str, layer: int) -> HisMeasurement | None:
        """Return the measurement of a station, variable and layer, if any."""
        key = _key(station, variable, layer)
        for entry in self.entries:
            if _key(entry.station, entry.variable, entry.layer) == key:
                return entry
        return None


def read_his_measurements(
    catalogue_path: Path,
    paths: PathsConfig,
    *,
    time_zone: str | None,
) -> HisMeasurementCatalogue:
    """Parse a HIS measurement catalogue.

    ``time_zone`` is a fixed offset such as ``+01:00``.
    """
    resolved = paths.resolve_path(catalogue_path, base_dir=Path.cwd())
    default_zone = _zone(time_zone)
    rows = _catalogue_rows(resolved)
    entries: list[HisMeasurement] = []
    file_fingerprints: list[str] = []
    parameter_entries: list[dict[str, object]] = []
    seen: set[tuple[str, str, int]] = set()
    for index, row in enumerate(rows, start=1):
        station = _required(row, "station", index, resolved)
        variable = _required(row, "variable", index, resolved)
        source = paths.resolve_path(
            _required(row, "file", index, resolved), base_dir=resolved.parent
        )
        factor = _number(row, "factor", 1.0, index, resolved)
        layer_value = _number(row, "layer", 1.0, index, resolved)
        if layer_value < 1.0 or not layer_value.is_integer():
            raise HisError(
                "V-HIS-006",
                f"Measurement catalogue {resolved} row {index}: layer must be a "
                "whole number from 1.",
            )
        layer = int(layer_value)
        key = _key(station, variable, layer)
        if key in seen:
            raise HisError(
                "V-HIS-006",
                f"Measurement catalogue {resolved} repeats station {station!r}, "
                f"variable {variable!r}, layer {layer}.",
            )
        seen.add(key)
        time_seconds, values = _read_series_file(source, default_zone)
        entries.append(
            HisMeasurement(
                station=station,
                variable=variable,
                layer=layer,
                time_seconds=time_seconds,
                values=values * factor,
            )
        )
        file_fingerprints.append(fingerprint_small_input(source))
        parameter_entries.append(
            {
                "station": station,
                "variable": variable,
                "layer": layer,
                "factor": factor,
                "file": str(source),
            }
        )
    catalogue_hash = fingerprint_small_input(resolved)
    fingerprint = fingerprint_artifact(
        kind="his_measurement_catalogue",
        schema_version=CACHE_SCHEMA_VERSION,
        tool_version=__version__,
        algorithm_version="1",
        parameters={
            "catalogue": str(resolved),
            "time_zone": default_zone.utcoffset(None).total_seconds(),
            "entries": parameter_entries,
        },
        inputs=(catalogue_hash, *file_fingerprints),
    )
    return HisMeasurementCatalogue(
        path=resolved,
        entries=tuple(entries),
        fingerprint=fingerprint,
    )


def _key(station: str, variable: str, layer: int) -> tuple[str, str, int]:
    return station.strip().casefold(), variable.strip().casefold(), layer


def _zone(offset: str | None) -> timezone:
    if offset is None:
        return UTC
    try:
        return timezone_from_offset(offset)
    except ValueError as error:
        raise HisError("V-HIS-006", str(error)) from error


def _catalogue_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            columns = {
                (field or "").strip().casefold(): field
                for field in reader.fieldnames or ()
            }
            if not _REQUIRED_CATALOGUE_COLUMNS.issubset(columns):
                raise HisError(
                    "V-HIS-006",
                    f"Measurement catalogue {path} requires station, variable and "
                    "file columns.",
                )
            rows = [
                {key: row.get(column) or "" for key, column in columns.items()}
                for row in reader
            ]
    except (OSError, UnicodeError, csv.Error) as error:
        raise HisError(
            "V-HIS-006", f"Could not read measurement catalogue {path}: {error}"
        ) from error
    if not rows:
        raise HisError("V-HIS-006", f"Measurement catalogue {path} is empty.")
    return rows


def _required(row: dict[str, str], column: str, index: int, path: Path) -> str:
    text = row.get(column, "").strip()
    if not text:
        raise HisError(
            "V-HIS-006",
            f"Measurement catalogue {path} row {index}: {column} must be non-empty.",
        )
    return text


def _number(
    row: dict[str, str], column: str, default: float, index: int, path: Path
) -> float:
    text = row.get(column, "").strip()
    if not text:
        return default
    try:
        value = float(text)
    except ValueError as error:
        raise HisError(
            "V-HIS-006",
            f"Measurement catalogue {path} row {index}: {column} must be a number.",
        ) from error
    if not math.isfinite(value):
        raise HisError(
            "V-HIS-006",
            f"Measurement catalogue {path} row {index}: {column} must be finite.",
        )
    return value


def _read_series_file(
    path: Path, default_zone: timezone
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            columns = {
                (field or "").strip().casefold(): field
                for field in reader.fieldnames or ()
            }
            if not _REQUIRED_FILE_COLUMNS.issubset(columns):
                raise HisError(
                    "V-HIS-006", f"Measurement file {path} requires time and value."
                )
            times: list[float] = []
            values: list[float] = []
            for row in reader:
                text = (row.get(columns["value"]) or "").strip()
                if not text:
                    continue
                value = float(text)
                if not math.isfinite(value):
                    continue
                times.append(
                    _epoch_seconds(row.get(columns["time"]) or "", default_zone)
                )
                values.append(value)
    except HisError:
        raise
    except (OSError, UnicodeError, csv.Error, ValueError) as error:
        raise HisError(
            "V-HIS-006", f"Could not read measurement file {path}: {error}"
        ) from error
    if not values:
        raise HisError("V-HIS-006", f"Measurement file {path} has no finite values.")
    order = np.argsort(times, kind="stable")
    return (
        np.asarray(times, dtype=np.float64)[order],
        np.asarray(values, dtype=np.float64)[order],
    )


def _epoch_seconds(text: str, default_zone: timezone) -> float:
    value = text.strip()
    if value.endswith(("Z", "z")):
        value = f"{value[:-1]}+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_zone)
    return parsed.timestamp()
