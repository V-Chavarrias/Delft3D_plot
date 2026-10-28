# SPDX-License-Identifier: GPL-3.0-or-later
"""Convert, cache, and match river-kilometre profile measurements."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Any

from d3dplot import __version__
from d3dplot.simulation.partitions import _NETCDF4_READ_LOCK
from d3dplot.simulation.time_utils import timezone_from_offset
from d3dplot.store import (
    CACHE_SCHEMA_VERSION,
    OutputLayout,
    OutputWriteError,
    OutputWriteGuard,
    atomic_path,
    fingerprint_artifact,
    fingerprint_small_input,
)
from d3dplot.variables.registry import UnknownVariableError, get_variable_spec

_Array = Any
np: Any = import_module("numpy")
netcdf4: Any = import_module("netCDF4")

_TIME_UNITS = "seconds since 1970-01-01 00:00:00"
_CALENDAR = "proleptic_gregorian"
_STATISTIC_ALIASES = {
    "mean_weighted": "mean_area_weighted",
    "sum_length": "sum_per_length",
}


class ProfileMeasurementError(RuntimeError):
    """A profile measurement input or conversion cannot be used safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ProfileMeasurement:
    """One canonical variable from a river-kilometre measurement file."""

    variable: str
    path: Path
    fingerprint: str
    times: tuple[datetime, ...]
    river_km: _Array
    statistics: Mapping[str, _Array]
    units: str
    vertical_datum: str | None
    source_files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProfileMeasurementMatch:
    """The nearest measurement profile within the requested time tolerance."""

    time: datetime
    river_km: _Array
    values: _Array
    difference: timedelta


@dataclass(frozen=True, slots=True)
class TimeSeriesMeasurementMatch:
    """The measured time series at the nearest location within tolerance."""

    river_km: float
    times: tuple[datetime, ...]
    values: _Array
    difference_km: float


@dataclass(frozen=True, slots=True)
class _ParsedProfile:
    variable: str
    times: tuple[datetime, ...]
    river_km: _Array
    distance_m: _Array | None
    statistics: Mapping[str, _Array]
    units: str
    vertical_datum: str | None
    source_by_time: tuple[str, ...]


DiagnosticSink = Callable[[str], None]


def load_profile_measurements(
    source: str | Path,
    requested_variables: Sequence[str],
    *,
    time_zone: str | None,
    layout: OutputLayout,
    guard: OutputWriteGuard,
    diagnostic: DiagnosticSink | None = None,
) -> dict[str, ProfileMeasurement]:
    """Load canonical NetCDF or cache requested variables from a Matlab file."""
    path = Path(source).expanduser().resolve(strict=False)
    if not path.is_file():
        raise ProfileMeasurementError(
            "V-MEA-001",
            f"Profile measurement file is missing or unreadable: {path}.",
        )
    canonical_variables = _canonical_variables(requested_variables)
    if path.suffix.casefold() == ".mat":
        if time_zone is None:
            raise ProfileMeasurementError(
                "V-MEA-007",
                "A fixed measurement time_zone or an inspected model time offset "
                "is required to interpret MATLAB datenums.",
            )
        return _load_mat_measurements(
            path,
            canonical_variables,
            time_zone,
            layout,
            guard,
            diagnostic,
        )
    if path.suffix.casefold() != ".nc":
        raise ProfileMeasurementError(
            "V-MEA-001",
            f"Unsupported profile measurement format: {path.suffix or '<none>'}.",
        )

    try:
        input_fingerprint = fingerprint_small_input(path)
    except (OSError, ValueError) as error:
        raise ProfileMeasurementError(
            "V-MEA-001",
            f"Could not fingerprint profile measurement file {path}: {error}",
        ) from error
    result: dict[str, ProfileMeasurement] = {}
    for variable in canonical_variables:
        fingerprint = _measurement_fingerprint(
            input_fingerprint,
            variable,
            source_time_zone=None,
        )
        result[variable] = _read_profile_netcdf(
            path,
            variable,
            fingerprint=fingerprint,
        )
    return result


def convert_mat_profile_measurements(
    source: str | Path,
    output: str | Path,
    *,
    time_zone: str,
    guard: OutputWriteGuard,
    variable: str | None = None,
) -> tuple[Path, ...]:
    """Write canonical per-variable NetCDF copies without replacing files."""
    source_path = Path(source).expanduser().resolve(strict=True)
    output_path = Path(output).expanduser().resolve(strict=False)
    if source_path == output_path:
        raise OutputWriteError("The measurement input cannot also be its output.")
    if output_path.suffix.casefold() != ".nc":
        raise ProfileMeasurementError(
            "V-MEA-007",
            f"Profile measurement output must use the .nc suffix: {output_path}.",
        )
    guard.validate_target(output_path)
    requested = None if variable is None else _canonical_variables((variable,))
    parsed = _read_mat_profiles(source_path, time_zone)
    selected = (
        parsed
        if requested is None
        else {name: parsed[name] for name in requested if name in parsed}
    )
    if not selected:
        raise ProfileMeasurementError(
            "V-MEA-005",
            f"No requested profile variable was found in {source_path}.",
        )
    if requested is not None and set(selected) != set(requested):
        missing = sorted(set(requested) - set(selected))
        raise ProfileMeasurementError(
            "V-MEA-005",
            f"Profile measurement variables are missing: {missing}.",
        )
    output_by_variable = {
        name: (
            output_path
            if len(selected) == 1
            else output_path.with_name(f"{output_path.stem}_{name}{output_path.suffix}")
        )
        for name in selected
    }
    for target in output_by_variable.values():
        guard.validate_target(target)
        if target.exists() or target.is_symlink():
            raise OutputWriteError(
                f"Refusing to overwrite profile measurement output: {target}."
            )

    try:
        source_fingerprint = fingerprint_small_input(source_path)
    except (OSError, ValueError) as error:
        raise ProfileMeasurementError(
            "V-MEA-001",
            f"Could not fingerprint profile measurement file {source_path}: {error}",
        ) from error
    outputs: list[Path] = []
    for name, profile in selected.items():
        fingerprint = _measurement_fingerprint(
            source_fingerprint,
            name,
            source_time_zone=time_zone,
        )
        _write_profile_netcdf(
            output_by_variable[name],
            profile,
            fingerprint,
            time_zone,
            guard,
        )
        outputs.append(output_by_variable[name])
    return tuple(outputs)


def match_profile_measurement(
    measurement: ProfileMeasurement,
    statistic: str,
    output_time: datetime,
    tolerance: timedelta,
) -> ProfileMeasurementMatch | None:
    """Return the nearest profile in time when it satisfies the tolerance."""
    values = _measurement_statistic(measurement, statistic)
    index = min(
        range(len(measurement.times)),
        key=lambda item: abs((measurement.times[item] - output_time).total_seconds()),
    )
    difference = abs(measurement.times[index] - output_time)
    if difference > tolerance:
        return None
    return ProfileMeasurementMatch(
        time=measurement.times[index],
        river_km=measurement.river_km,
        values=values[index],
        difference=difference,
    )


def match_time_series_measurement(
    measurement: ProfileMeasurement,
    statistic: str,
    river_km: float,
    tolerance_km: float,
) -> TimeSeriesMeasurementMatch | None:
    """Return all measurement times at the nearest river-km within tolerance."""
    values = _measurement_statistic(measurement, statistic)
    index = int(np.argmin(np.abs(measurement.river_km - river_km)))
    difference = abs(float(measurement.river_km[index]) - river_km)
    if difference > tolerance_km:
        return None
    return TimeSeriesMeasurementMatch(
        river_km=float(measurement.river_km[index]),
        times=measurement.times,
        values=values[:, index],
        difference_km=difference,
    )


def profile_measurement_variable(variable: str, statistic: str) -> str:
    """Resolve the MATLAB profile field used for a model quantity/statistic pair."""
    if statistic == "bed_slope_streamwise":
        try:
            if get_variable_spec(variable).name == "bed_level":
                return "bed_slope_streamwise"
        except UnknownVariableError:
            pass
    return _canonical_variables((variable,))[0]


def _load_mat_measurements(
    source: Path,
    variables: Sequence[str],
    time_zone: str,
    layout: OutputLayout,
    guard: OutputWriteGuard,
    diagnostic: DiagnosticSink | None,
) -> dict[str, ProfileMeasurement]:
    parsed = _read_mat_profiles(source, time_zone)
    missing = tuple(name for name in variables if name not in parsed)
    if missing:
        raise ProfileMeasurementError(
            "V-MEA-005",
            f"Profile measurement variables are missing from {source}: "
            f"{list(missing)}.",
        )
    try:
        source_fingerprint = fingerprint_small_input(source)
    except (OSError, ValueError) as error:
        raise ProfileMeasurementError(
            "V-MEA-001",
            f"Could not fingerprint profile measurement file {source}: {error}",
        ) from error

    result: dict[str, ProfileMeasurement] = {}
    for variable in variables:
        fingerprint = _measurement_fingerprint(
            source_fingerprint,
            variable,
            source_time_zone=time_zone,
        )
        cache_path = (
            layout.cache_measurements / "river_km_statistics" / f"{fingerprint[:24]}.nc"
        )
        cached: ProfileMeasurement | None = None
        if cache_path.is_file():
            try:
                cached = _read_profile_netcdf(
                    cache_path,
                    variable,
                    fingerprint=fingerprint,
                    require_fingerprint=True,
                )
            except ProfileMeasurementError as error:
                if diagnostic is not None:
                    diagnostic(
                        f"Rebuilding invalid profile-measurement cache "
                        f"{cache_path}: {error}"
                    )
        if cached is None:
            _write_profile_netcdf(
                cache_path,
                parsed[variable],
                fingerprint,
                time_zone,
                guard,
            )
            cached = _read_profile_netcdf(
                cache_path,
                variable,
                fingerprint=fingerprint,
                require_fingerprint=True,
            )
            if diagnostic is not None:
                diagnostic(f"Cached profile measurements at {cache_path}.")
        elif diagnostic is not None:
            diagnostic(f"Reused profile-measurement cache {cache_path}.")
        result[variable] = cached
    return result


def _read_mat_profiles(
    source: Path,
    time_zone: str,
) -> dict[str, _ParsedProfile]:
    try:
        with source.open("rb") as stream:
            signature = stream.read(8)
    except OSError as error:
        raise ProfileMeasurementError(
            "V-MEA-001",
            f"Could not read profile measurement file {source}: {error}",
        ) from error
    if signature == b"\x89HDF\r\n\x1a\n":
        raise ProfileMeasurementError(
            "V-MEA-006",
            f"Matlab v7.3/HDF5 measurements are not supported: {source}. "
            "Save the file with save(..., '-v7') and retry.",
        )
    try:
        scipy_io: Any = import_module("scipy.io")
        loaded = scipy_io.loadmat(source, simplify_cells=True)
    except (ImportError, OSError, NotImplementedError, ValueError) as error:
        raise ProfileMeasurementError(
            "V-MEA-001",
            f"Could not read Matlab v5/v7 profile measurements {source}: {error}",
        ) from error
    if "data" not in loaded:
        raise ProfileMeasurementError(
            "V-MEA-007",
            f"Matlab profile measurement file has no data structure: {source}.",
        )
    data = _as_mapping(loaded["data"], f"data in {source}")
    result: dict[str, _ParsedProfile] = {}
    for raw_name, raw_variable in data.items():
        variable_name = _mat_variable_name(str(raw_name))
        if variable_name is None:
            continue
        variable_fields = _as_mapping(
            raw_variable,
            f"variable {raw_name!r} in {source}",
        )
        statistic_fields = {
            str(name): value
            for name, value in variable_fields.items()
            if str(name).startswith("val_")
        }
        if not statistic_fields:
            continue
        profile = _parse_mat_variable(
            variable_name,
            statistic_fields,
            variable_fields.get("source"),
            source,
            time_zone,
        )
        if variable_name in result:
            raise ProfileMeasurementError(
                "V-MEA-007",
                f"Matlab file contains duplicate aliases for {variable_name!r}.",
            )
        result[variable_name] = profile
    if not result:
        raise ProfileMeasurementError(
            "V-MEA-007",
            f"Matlab file contains no supported profile variables: {source}.",
        )
    return result


def _parse_mat_variable(
    variable: str,
    raw_statistics: Mapping[str, object],
    raw_sources: object | None,
    source: Path,
    time_zone: str,
) -> _ParsedProfile:
    try:
        zone = timezone_from_offset(time_zone)
    except ValueError as error:
        raise ProfileMeasurementError(
            "V-MEA-007",
            f"Invalid fixed measurement time zone {time_zone!r}: {error}",
        ) from error

    parsed_statistics: dict[str, _Array] = {}
    reference_km: _Array | None = None
    reference_distance: _Array | None = None
    reference_times: _Array | None = None
    reference_units: str | None = None
    reference_datum: str | None = None
    for matlab_statistic, raw_struct in raw_statistics.items():
        stat = _statistic_name(matlab_statistic)
        fields = _as_mapping(
            raw_struct,
            f"{variable}.{matlab_statistic} in {source}",
        )
        if "rkm" not in fields or "tim_dnum" not in fields or "val" not in fields:
            raise ProfileMeasurementError(
                "V-MEA-007",
                f"{variable}.{matlab_statistic} must contain rkm, tim_dnum, and val.",
            )
        river_km = np.asarray(fields["rkm"], dtype=np.float64).reshape(-1)
        datenum = np.asarray(fields["tim_dnum"], dtype=np.float64).reshape(-1)
        if river_km.size == 0 or datenum.size == 0:
            raise ProfileMeasurementError(
                "V-MEA-007",
                f"{variable}.{matlab_statistic} has empty coordinates.",
            )
        if not np.all(np.isfinite(river_km)) or not np.all(np.isfinite(datenum)):
            raise ProfileMeasurementError(
                "V-MEA-007",
                f"{variable}.{matlab_statistic} has non-finite coordinates or times.",
            )
        raw_values = np.asarray(fields["val"], dtype=np.float64)
        values = _reshape_measurement_values(
            raw_values,
            river_km.size,
            datenum.size,
            f"{variable}.{matlab_statistic}",
        )
        distance = (
            np.asarray(fields["s"], dtype=np.float64).reshape(-1)
            if "s" in fields
            else None
        )
        if distance is not None and (
            distance.size != river_km.size or not np.all(np.isfinite(distance))
        ):
            raise ProfileMeasurementError(
                "V-MEA-007",
                f"{variable}.{matlab_statistic} has an invalid s coordinate.",
            )
        units, datum = _parse_unit(fields.get("unit"), variable, source)
        if reference_km is None:
            reference_km = river_km
            reference_distance = distance
            reference_times = datenum
            reference_units = units
            reference_datum = datum
        elif (
            not np.array_equal(reference_km, river_km)
            or not np.array_equal(reference_times, datenum)
            or not _optional_arrays_equal(reference_distance, distance)
            or reference_units != units
            or reference_datum != datum
        ):
            raise ProfileMeasurementError(
                "V-MEA-007",
                f"Statistics for {variable!r} do not share coordinates and units.",
            )
        parsed_statistics[stat] = values

    if reference_km is None or reference_times is None or reference_units is None:
        raise ProfileMeasurementError(
            "V-MEA-007",
            f"No valid statistics found for {variable!r} in {source}.",
        )
    try:
        times_unsorted = tuple(
            _matlab_datenum_to_utc(value, zone) for value in reference_times
        )
    except (OverflowError, ValueError) as error:
        raise ProfileMeasurementError(
            "V-MEA-007",
            f"Could not convert Matlab datenums for {variable!r}: {error}",
        ) from error
    time_order = np.argsort(
        np.asarray([time.timestamp() for time in times_unsorted]),
        kind="stable",
    )
    sorted_times = tuple(times_unsorted[int(index)] for index in time_order)
    if len(set(sorted_times)) != len(sorted_times):
        raise ProfileMeasurementError(
            "V-MEA-007",
            f"Measurement times for {variable!r} contain duplicates.",
        )
    km_order = np.argsort(reference_km, kind="stable")
    sorted_km = np.asarray(reference_km[km_order], dtype=np.float64)
    if np.any(np.diff(sorted_km) <= 0):
        raise ProfileMeasurementError(
            "V-MEA-007",
            f"River-km coordinates for {variable!r} contain duplicates.",
        )
    sorted_distance = (
        None
        if reference_distance is None
        else np.asarray(reference_distance[km_order], dtype=np.float64)
    )
    sorted_statistics = {
        name: np.asarray(values[km_order, :][:, time_order].T, dtype=np.float64)
        for name, values in parsed_statistics.items()
    }
    sources = _source_strings(raw_sources, len(times_unsorted), source)
    sorted_sources = tuple(sources[int(index)] for index in time_order)
    return _ParsedProfile(
        variable=variable,
        times=sorted_times,
        river_km=sorted_km,
        distance_m=sorted_distance,
        statistics=sorted_statistics,
        units=reference_units,
        vertical_datum=reference_datum,
        source_by_time=sorted_sources,
    )


def _reshape_measurement_values(
    values: _Array,
    n_location: int,
    n_time: int,
    name: str,
) -> _Array:
    if values.ndim == 0:
        values = values.reshape(1, 1)
    elif values.ndim == 1:
        if n_location == 1:
            values = values.reshape(1, -1)
        elif n_time == 1:
            values = values.reshape(-1, 1)
    if values.shape != (n_location, n_time):
        raise ProfileMeasurementError(
            "V-MEA-007",
            f"{name}.val has shape {values.shape}; expected ({n_location}, {n_time}).",
        )
    return values


def _parse_unit(
    raw_unit: object | None,
    variable: str,
    source: Path,
) -> tuple[str, str | None]:
    unit_text = _string_value(raw_unit)
    match = re.fullmatch(r"\s*[^[]+\[([^\]]+)\]\s*", unit_text)
    if match is None:
        raise ProfileMeasurementError(
            "V-MEA-007",
            f"Unit metadata for {variable!r} in {source} must use "
            "'<quantity> [<unit>]'.",
        )
    parts = match.group(1).split("+", maxsplit=1)
    units = parts[0].strip()
    datum = parts[1].strip() if len(parts) == 2 else None
    if not units:
        raise ProfileMeasurementError(
            "V-MEA-007",
            f"Unit metadata for {variable!r} in {source} has an empty unit.",
        )
    return units, datum or None


def _write_profile_netcdf(
    path: Path,
    profile: _ParsedProfile,
    fingerprint: str,
    source_time_zone: str,
    guard: OutputWriteGuard,
) -> None:
    with atomic_path(path, guard) as temporary:
        with netcdf4.Dataset(temporary, mode="w", format="NETCDF4") as dataset:
            dataset.Conventions = "CF-1.8"
            dataset.d3dplot_kind = "river_km_profiles"
            dataset.d3dplot_fingerprint = fingerprint
            dataset.d3dplot_parameters = json.dumps(
                {
                    "variable": profile.variable,
                    "source_utc_offset": source_time_zone,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            dataset.d3dplot_version = __version__
            dataset.d3dplot_cache_schema = CACHE_SCHEMA_VERSION
            dataset.source_utc_offset = source_time_zone
            dataset.source_files = json.dumps(
                list(
                    dict.fromkeys(source for source in profile.source_by_time if source)
                ),
                ensure_ascii=False,
            )
            dataset.history = (
                f"Converted from Matlab profile measurements at "
                f"{datetime.now(UTC).isoformat()}."
            )

            dataset.createDimension("statistic", len(profile.statistics))
            dataset.createDimension("time", len(profile.times))
            dataset.createDimension("river_km", len(profile.river_km))
            statistic_var = dataset.createVariable("statistic", str, ("statistic",))
            statistic_names = tuple(profile.statistics)
            statistic_var[:] = np.asarray(statistic_names, dtype=object)
            time_var = dataset.createVariable("time", "f8", ("time",))
            time_var.units = _TIME_UNITS
            time_var.calendar = _CALENDAR
            time_var.standard_name = "time"
            time_var[:] = netcdf4.date2num(
                profile.times,
                units=_TIME_UNITS,
                calendar=_CALENDAR,
            )
            river_km_var = dataset.createVariable("river_km", "f8", ("river_km",))
            river_km_var.units = "km"
            river_km_var[:] = profile.river_km
            if profile.distance_m is not None:
                distance_var = dataset.createVariable("s", "f8", ("river_km",))
                distance_var.units = "m"
                distance_var.long_name = "distance along the river-km axis"
                distance_var[:] = profile.distance_m

            source_var = dataset.createVariable("source_file", str, ("time",))
            source_var[:] = np.asarray(profile.source_by_time, dtype=object)
            value_var = dataset.createVariable(
                profile.variable,
                "f4",
                ("statistic", "time", "river_km"),
                zlib=True,
                fill_value=np.float32(np.nan),
            )
            value_var.units = profile.units
            value_var.long_name = profile.variable.replace("_", " ")
            if profile.vertical_datum is not None:
                value_var.vertical_datum = profile.vertical_datum
            value_var[:] = np.stack(
                [profile.statistics[name] for name in statistic_names],
                axis=0,
            ).astype(np.float32)


def _read_profile_netcdf(
    path: Path,
    variable: str,
    *,
    fingerprint: str,
    require_fingerprint: bool = False,
) -> ProfileMeasurement:
    try:
        with _NETCDF4_READ_LOCK, netcdf4.Dataset(path, mode="r") as dataset:
            if _attribute_text(dataset, "d3dplot_kind") != "river_km_profiles":
                raise ProfileMeasurementError(
                    "V-MEA-007",
                    f"NetCDF file is not a river-km profile dataset: {path}.",
                )
            if (
                require_fingerprint
                and _attribute_text(dataset, "d3dplot_fingerprint") != fingerprint
            ):
                raise ProfileMeasurementError(
                    "V-MEA-007",
                    f"Profile measurement cache fingerprint does not match: {path}.",
                )
            for name in ("time", "river_km", "statistic", variable):
                if name not in dataset.variables:
                    code = "V-MEA-005" if name == variable else "V-MEA-007"
                    raise ProfileMeasurementError(
                        code,
                        f"Profile measurement file {path} is missing {name!r}.",
                    )
            time_var = dataset.variables["time"]
            if (
                _attribute_text(time_var, "units") != _TIME_UNITS
                or _attribute_text(time_var, "calendar") != _CALENDAR
            ):
                raise ProfileMeasurementError(
                    "V-MEA-007",
                    f"Profile measurement file {path} has unsupported CF time units.",
                )
            time_values = np.asarray(time_var[:], dtype=np.float64)
            river_km = np.asarray(dataset.variables["river_km"][:], dtype=np.float64)
            if (
                time_values.ndim != 1
                or time_values.size == 0
                or not np.all(np.isfinite(time_values))
                or np.any(np.diff(time_values) <= 0)
                or river_km.ndim != 1
                or river_km.size == 0
                or not np.all(np.isfinite(river_km))
                or np.any(np.diff(river_km) <= 0)
            ):
                raise ProfileMeasurementError(
                    "V-MEA-007",
                    f"Profile measurement file {path} has invalid time or "
                    "river-km coordinates.",
                )
            times = tuple(
                datetime.fromtimestamp(float(value), tz=UTC) for value in time_values
            )
            statistic_names = tuple(
                _string_value(value) for value in dataset.variables["statistic"][:]
            )
            if not statistic_names or len(set(statistic_names)) != len(statistic_names):
                raise ProfileMeasurementError(
                    "V-MEA-007",
                    f"Profile measurement file {path} has invalid statistic names.",
                )
            value_var = dataset.variables[variable]
            if value_var.dimensions != ("statistic", "time", "river_km"):
                raise ProfileMeasurementError(
                    "V-MEA-007",
                    f"Variable {variable!r} in {path} has invalid dimensions "
                    f"{value_var.dimensions}.",
                )
            array = np.ma.filled(value_var[:], np.nan).astype(np.float64)
            if array.shape != (
                len(statistic_names),
                len(times),
                river_km.size,
            ):
                raise ProfileMeasurementError(
                    "V-MEA-007",
                    f"Variable {variable!r} in {path} has invalid value shape "
                    f"{array.shape}.",
                )
            statistics = {
                name: array[index] for index, name in enumerate(statistic_names)
            }
            distance = (
                np.asarray(dataset.variables["s"][:], dtype=np.float64)
                if "s" in dataset.variables
                else None
            )
            if distance is not None and (
                distance.shape != river_km.shape or not np.all(np.isfinite(distance))
            ):
                raise ProfileMeasurementError(
                    "V-MEA-007",
                    f"Profile measurement file {path} has invalid s coordinates.",
                )
            source_by_time = (
                tuple(
                    _string_value(value)
                    for value in dataset.variables["source_file"][:]
                )
                if "source_file" in dataset.variables
                else ()
            )
            source_files = _read_source_files(dataset, source_by_time)
            return ProfileMeasurement(
                variable=variable,
                path=path,
                fingerprint=fingerprint,
                times=times,
                river_km=river_km,
                statistics=statistics,
                units=_attribute_text(value_var, "units"),
                vertical_datum=(
                    _attribute_text(value_var, "vertical_datum")
                    if "vertical_datum" in value_var.ncattrs()
                    else None
                ),
                source_files=source_files,
            )
    except ProfileMeasurementError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise ProfileMeasurementError(
            "V-MEA-001",
            f"Could not read canonical profile measurements {path}: {error}",
        ) from error


def _read_source_files(
    dataset: Any, source_by_time: tuple[str, ...]
) -> tuple[str, ...]:
    raw = _attribute_text(dataset, "source_files")
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
            return tuple(parsed)
    return tuple(dict.fromkeys(value for value in source_by_time if value))


def _measurement_fingerprint(
    source_fingerprint: str,
    variable: str,
    *,
    source_time_zone: str | None,
) -> str:
    return fingerprint_artifact(
        kind="river_km_profile_measurement",
        schema_version=CACHE_SCHEMA_VERSION,
        tool_version=__version__,
        algorithm_version="profile-measurements-1",
        parameters={
            "source_utc_offset": source_time_zone,
            "variable": variable,
        },
        inputs=(source_fingerprint,),
    )


def _canonical_variables(variables: Sequence[str]) -> tuple[str, ...]:
    canonical: list[str] = []
    for variable in variables:
        if variable.casefold() in {"bed_slope_streamwise", "detab_ds"}:
            name = "bed_slope_streamwise"
        else:
            try:
                name = get_variable_spec(variable).name
            except UnknownVariableError as error:
                raise ProfileMeasurementError(
                    "V-MEA-005",
                    f"Unknown profile measurement variable {variable!r}.",
                ) from error
        if name not in canonical:
            canonical.append(name)
    return tuple(canonical)


def _measurement_statistic(
    measurement: ProfileMeasurement,
    requested: str,
) -> _Array:
    candidate_names = (
        ["mean"]
        if measurement.variable == "bed_slope_streamwise"
        and requested == "bed_slope_streamwise"
        else [requested]
    )
    if requested == "mean_area_weighted":
        candidate_names.insert(0, "mean")
    elif requested == "mean":
        candidate_names.append("mean_area_weighted")
    for name in candidate_names:
        if name in measurement.statistics:
            return np.asarray(measurement.statistics[name], dtype=np.float64)
    raise ProfileMeasurementError(
        "V-MEA-005",
        f"Measurement file {measurement.path} has no statistic {requested!r} "
        f"for {measurement.variable!r}; available: "
        f"{sorted(measurement.statistics)}.",
    )


def _statistic_name(name: str) -> str:
    value = name.removeprefix("val_")
    return _STATISTIC_ALIASES.get(value, value)


def _mat_variable_name(name: str) -> str | None:
    if name.casefold() in {"detab_ds", "bed_slope_streamwise"}:
        return "bed_slope_streamwise"
    try:
        return get_variable_spec(name).name
    except UnknownVariableError:
        return None


def _matlab_datenum_to_utc(value: float, zone: Any) -> datetime:
    if not math.isfinite(value):
        raise ValueError(f"non-finite datenum {value!r}")
    day = math.floor(value)
    local = datetime.fromordinal(day - 366) + timedelta(days=value - day)
    return local.replace(tzinfo=zone).astimezone(UTC)


def _as_mapping(value: object, context: str) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "_fieldnames"):
        field_names = value._fieldnames
        if isinstance(field_names, list):
            return {name: getattr(value, name) for name in field_names}
    if isinstance(value, np.ndarray) and value.size == 1:
        item = value.item()
        if isinstance(item, Mapping):
            return item
        if hasattr(item, "_fieldnames"):
            field_names = item._fieldnames
            if isinstance(field_names, list):
                return {name: getattr(item, name) for name in field_names}
    raise ProfileMeasurementError(
        "V-MEA-007",
        f"Expected a Matlab structure for {context}.",
    )


def _source_strings(
    raw_sources: object | None,
    count: int,
    source: Path,
) -> tuple[str, ...]:
    if raw_sources is None:
        return tuple(str(source) for _ in range(count))
    values = np.asarray(raw_sources, dtype=object).reshape(-1)
    if values.size == 1 and count > 1:
        values = np.repeat(values, count)
    if values.size != count:
        raise ProfileMeasurementError(
            "V-MEA-007",
            f"Measurement source metadata in {source} has {values.size} entries; "
            f"expected {count}.",
        )
    return tuple(_string_value(value) for value in values)


def _string_value(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, np.ndarray) and value.size == 1:
        return _string_value(value.item())
    return str(value).strip()


def _optional_arrays_equal(left: _Array | None, right: _Array | None) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return bool(np.array_equal(left, right))


def _attribute_text(dataset: Any, name: str) -> str:
    try:
        value = dataset.getncattr(name)
    except (AttributeError, KeyError):
        return ""
    return _string_value(value)
