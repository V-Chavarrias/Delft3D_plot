# SPDX-License-Identifier: GPL-3.0-or-later
"""Load, select, and match a simulation's checked UTC time axis."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from importlib import import_module
from numbers import Integral
from pathlib import Path
from typing import Any, Literal, Protocol, TypeAlias

from d3dplot.config.selections import (
    IndicesSelection,
    TimeRange,
    TimesConfig,
)
from d3dplot.simulation.partitions import _NETCDF4_READ_LOCK
from d3dplot.simulation.time_utils import (
    decode_cf_time,
    timezone_from_offset,
)
from d3dplot.validate.report import ValidationIssue

TimeAxisName: TypeAlias = Literal["hydrodynamic", "morphological"]
DuplicatePolicy: TypeAlias = Literal["first", "last"]
CorruptOutputPolicy: TypeAlias = Literal["mask", "error", "skip"]

_UTC_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_HYDRODYNAMIC_DUPLICATE_TOLERANCE = timedelta(seconds=1)
_MORPHOLOGICAL_DUPLICATE_TOLERANCE = timedelta(seconds=60)
np: Any = import_module("numpy")


class _NetCDFVariable(Protocol):
    dimensions: tuple[str, ...]
    shape: tuple[int, ...]

    def __getitem__(self, key: object) -> object: ...

    def ncattrs(self) -> list[str]: ...

    def getncattr(self, name: str) -> object: ...


class _NetCDFDataset(Protocol):
    variables: dict[str, _NetCDFVariable]

    def ncattrs(self) -> list[str]: ...

    def getncattr(self, name: str) -> object: ...


@dataclass(frozen=True, slots=True)
class TimeEntry:
    """One usable map output, with the quality metadata needed by its reader."""

    time_index: int
    subrun: int
    local_index: int
    time: datetime
    morph_time: datetime | None
    corrupt_partitions: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class DuplicateResolution:
    """An output group resolved to one deterministic entry."""

    requested_time: datetime | None
    candidates: tuple[TimeEntry, ...]
    chosen: TimeEntry


@dataclass(frozen=True, slots=True)
class TimeSelectionResult:
    """Selected outputs, duplicate decisions, and validation diagnostics."""

    entries: tuple[TimeEntry, ...]
    duplicates: tuple[DuplicateResolution, ...]
    issues: tuple[ValidationIssue, ...]

    @property
    def is_valid(self) -> bool:
        """Whether selection has no error-level validation issues."""
        return not any(issue.level == "error" for issue in self.issues)


@dataclass(frozen=True, slots=True)
class TimeAxis:
    """A sorted, UTC-aware view over one simulation's ``times.nc`` cache."""

    entries: tuple[TimeEntry, ...]
    source_utc_offset: str
    morphology_available: bool

    @classmethod
    def from_cache(cls, path: str | Path) -> TimeAxis:
        """Load and validate a time-quality cache written by inspection."""
        cache_path = Path(path)
        try:
            with (
                _NETCDF4_READ_LOCK,
                import_module("netCDF4").Dataset(cache_path, mode="r") as dataset,
            ):
                source_offset = _attribute_text(dataset, "source_utc_offset")
                if source_offset is None:
                    raise ValueError("time cache has no source_utc_offset attribute")
                timezone_from_offset(source_offset)

                schema = dataset.getncattr("d3dplot_cache_schema")
                if not isinstance(schema, Integral) or int(schema) < 1:
                    raise ValueError("time cache has an invalid cache schema")
                morphology_attribute = dataset.getncattr("morphology_available")
                if not isinstance(morphology_attribute, Integral) or int(
                    morphology_attribute
                ) not in (0, 1):
                    raise ValueError("time cache has an invalid morphology flag")

                required_variables = (
                    "time",
                    "subrun",
                    "local_index",
                    "morph_time",
                    "corrupt_partition",
                )
                missing = [
                    name for name in required_variables if name not in dataset.variables
                ]
                if missing:
                    raise ValueError(
                        f"time cache is missing variable(s): {', '.join(missing)}"
                    )

                time_variable = dataset.variables["time"]
                units = _attribute_text(time_variable, "units")
                calendar = _attribute_text(time_variable, "calendar")
                if units is None or calendar is None:
                    raise ValueError("time coordinate has no CF units/calendar")
                if time_variable.dimensions != ("time",):
                    raise ValueError("time coordinate must use the 'time' dimension")

                times = _read_vector(time_variable, "time")
                subruns = _read_integer_vector(dataset.variables["subrun"], "subrun")
                local_indices = _read_integer_vector(
                    dataset.variables["local_index"],
                    "local_index",
                )
                morph_times = _read_vector(
                    dataset.variables["morph_time"], "morph_time"
                )
                corrupt_variable = dataset.variables["corrupt_partition"]
                corrupt = np.ma.asarray(corrupt_variable[...])
                if corrupt.ndim != 2 or corrupt_variable.dimensions != (
                    "time",
                    "partition",
                ):
                    raise ValueError(
                        "corrupt_partition must have dimensions ('time', 'partition')"
                    )
                corrupt_values = np.asarray(corrupt.data, dtype=np.int64)
                if np.ma.getmaskarray(corrupt).any() or np.any(
                    (corrupt_values != 0) & (corrupt_values != 1)
                ):
                    raise ValueError("corrupt_partition values must be 0 or 1")

                row_count = times.size
                if (
                    subruns.size != row_count
                    or local_indices.size != row_count
                    or morph_times.size != row_count
                    or corrupt_values.shape[0] != row_count
                ):
                    raise ValueError("time cache variables have inconsistent lengths")

                entries: list[TimeEntry] = []
                for index in range(row_count):
                    instant = _decode_cache_time(
                        float(times[index]),
                        units,
                        calendar,
                    )
                    morph_instant = (
                        _decode_cache_time(
                            float(morph_times[index]),
                            units,
                            calendar,
                        )
                        if math.isfinite(float(morph_times[index]))
                        else None
                    )
                    corrupt_partitions = tuple(
                        int(partition)
                        for partition in np.flatnonzero(corrupt_values[index])
                    )
                    entries.append(
                        TimeEntry(
                            time_index=index,
                            subrun=int(subruns[index]),
                            local_index=int(local_indices[index]),
                            time=instant,
                            morph_time=morph_instant,
                            corrupt_partitions=corrupt_partitions,
                        )
                    )
        except (EOFError, IndexError, OSError, RuntimeError, TypeError) as error:
            raise ValueError(
                f"Could not read time cache {cache_path}: {error}"
            ) from error

        if any(
            right.time < left.time
            for left, right in zip(entries, entries[1:], strict=False)
        ):
            raise ValueError("time cache entries are not ordered by hydrodynamic time")
        return cls(
            entries=tuple(entries),
            source_utc_offset=source_offset,
            morphology_available=bool(int(morphology_attribute)),
        )

    def select(
        self,
        config: TimesConfig,
        *,
        naive_time_zone: str | None = None,
        duplicate_morphological_times: DuplicatePolicy = "last",
        on_corrupt_output: CorruptOutputPolicy = "mask",
    ) -> TimeSelectionResult:
        """Apply a configured all/last/index/list/range time selection."""
        candidates, issues = self._candidates(config.axis)
        if candidates is None:
            return TimeSelectionResult((), (), tuple(issues))

        duplicate_policy = _effective_duplicate_policy(
            config.axis,
            duplicate_morphological_times,
        )
        at = config.at
        duplicates: list[DuplicateResolution] = []
        requested_times: list[datetime] | None = None
        selected: list[TimeEntry] = []

        if at == "all":
            selected, duplicate_groups = _collapse_duplicates(
                candidates,
                config.axis,
                _duplicate_tolerance(config.axis),
                duplicate_policy,
                requested_time=None,
            )
            duplicates.extend(duplicate_groups)
        elif at == "last":
            collapsed, duplicate_groups = _collapse_duplicates(
                candidates,
                config.axis,
                _duplicate_tolerance(config.axis),
                duplicate_policy,
                requested_time=None,
            )
            duplicates.extend(duplicate_groups)
            if collapsed:
                selected = [max(collapsed, key=lambda entry: _hydro_order(entry))]
        elif isinstance(at, IndicesSelection):
            for index in at.indices:
                if index < 1 or index > len(self.entries):
                    issues.append(
                        _issue(
                            "V-TIME-010",
                            "error",
                            (
                                f"Output index {index} is outside the one-based "
                                f"merged time axis of length {len(self.entries)}."
                            ),
                            (
                                "Choose a one-based index between 1 and "
                                f"{len(self.entries)}."
                            ),
                        )
                    )
                    continue
                entry = self.entries[index - 1]
                if _axis_time(entry, config.axis) is None:
                    issues.append(
                        _issue(
                            "V-TIME-010",
                            "error",
                            (f"Output index {index} has no usable {config.axis} time."),
                            (
                                "Choose a one-based output index with a valid time "
                                "on the selected axis."
                            ),
                        )
                    )
                    continue
                selected.append(entry)
            if at.include_last:
                collapsed, duplicate_groups = _collapse_duplicates(
                    candidates,
                    config.axis,
                    _duplicate_tolerance(config.axis),
                    duplicate_policy,
                    requested_time=None,
                )
                duplicates.extend(duplicate_groups)
                if collapsed:
                    selected.append(
                        max(collapsed, key=lambda entry: _hydro_order(entry))
                    )
        elif isinstance(at, TimeRange):
            try:
                start = _request_datetime(
                    at.start,
                    naive_time_zone or self.source_utc_offset,
                )
                end = _request_datetime(
                    at.end,
                    naive_time_zone or self.source_utc_offset,
                )
            except ValueError as error:
                issues.append(
                    _issue(
                        "V-TIME-010",
                        "error",
                        f"Time range could not be interpreted: {error}",
                        "Use aware datetimes or a valid fixed reference offset.",
                    )
                )
                return TimeSelectionResult((), (), tuple(issues))
            if end < start:
                issues.append(
                    _issue(
                        "V-TIME-010",
                        "error",
                        "Time range end is earlier than its start.",
                        "Set an inclusive range whose end is not before its start.",
                    )
                )
                return TimeSelectionResult((), (), tuple(issues))
            requested_times = _time_range_requests(start, end, at.step)
        elif isinstance(at, list):
            try:
                requested_times = [
                    _request_datetime(
                        item,
                        naive_time_zone or self.source_utc_offset,
                    )
                    for item in at
                ]
            except ValueError as error:
                issues.append(
                    _issue(
                        "V-TIME-010",
                        "error",
                        f"Requested time could not be interpreted: {error}",
                        "Use aware datetimes or a valid fixed reference offset.",
                    )
                )
                return TimeSelectionResult((), (), tuple(issues))
        else:
            raise TypeError(f"Unsupported time selection {at!r}.")

        if requested_times is not None:
            if not requested_times:
                issues.append(
                    _issue(
                        "V-TIME-010",
                        "error",
                        "The explicit time selection contains no requested times.",
                        "Provide at least one date/time or a non-empty time range.",
                    )
                )
            for requested_time in requested_times:
                nearest_entry, duplicate, selection_issue = self._nearest(
                    candidates,
                    requested_time,
                    config.axis,
                    config.tolerance,
                    duplicate_policy,
                )
                if selection_issue is not None:
                    issues.append(selection_issue)
                if nearest_entry is not None:
                    selected.append(nearest_entry)
                if duplicate is not None:
                    duplicates.append(duplicate)

        selected = _unique_in_hydrodynamic_order(selected)
        selected = _apply_corrupt_policy(selected, on_corrupt_output, issues)
        if not selected and not any(issue.level == "error" for issue in issues):
            issues.append(
                _issue(
                    "V-TIME-010",
                    "error",
                    "No usable output exists on the selected time axis.",
                    "Check the model output times and corrupt-output policy.",
                )
            )
        return TimeSelectionResult(
            entries=tuple(selected),
            duplicates=tuple(duplicates),
            issues=tuple(issues),
        )

    def match(
        self,
        reference_entries: Sequence[TimeEntry],
        *,
        axis: TimeAxisName,
        tolerance: timedelta | None,
        duplicate_morphological_times: DuplicatePolicy = "last",
        on_corrupt_output: CorruptOutputPolicy = "mask",
    ) -> TimeSelectionResult:
        """Match reference-simulation outputs to the nearest local outputs."""
        candidates, issues = self._candidates(axis)
        if candidates is None:
            return TimeSelectionResult((), (), tuple(issues))
        if not reference_entries:
            issues.append(
                _issue(
                    "V-TIME-010",
                    "error",
                    "The reference simulation has no selected outputs to match.",
                    "Select at least one usable reference time.",
                )
            )
            return TimeSelectionResult((), (), tuple(issues))

        duplicate_policy = _effective_duplicate_policy(
            axis,
            duplicate_morphological_times,
        )
        selected: list[TimeEntry] = []
        duplicates: list[DuplicateResolution] = []
        for reference_entry in reference_entries:
            requested_time = _axis_time(reference_entry, axis)
            if requested_time is None:
                issues.append(
                    _issue(
                        "V-TIME-010",
                        "error",
                        (
                            f"Reference output index {reference_entry.time_index} "
                            f"has no {axis} time."
                        ),
                        "Use a reference output with a valid time on this axis.",
                    )
                )
                continue
            entry, duplicate, selection_issue = self._nearest(
                candidates,
                requested_time,
                axis,
                tolerance,
                duplicate_policy,
            )
            if selection_issue is not None:
                issues.append(selection_issue)
            if entry is not None:
                selected.append(entry)
            if duplicate is not None:
                duplicates.append(duplicate)

        selected = _unique_in_hydrodynamic_order(selected)
        selected = _apply_corrupt_policy(selected, on_corrupt_output, issues)
        if not selected and not any(issue.level == "error" for issue in issues):
            issues.append(
                _issue(
                    "V-TIME-010",
                    "error",
                    "No usable output could be matched to the reference times.",
                    "Check the output time axis and configured matching tolerance.",
                )
            )
        return TimeSelectionResult(
            entries=tuple(selected),
            duplicates=tuple(duplicates),
            issues=tuple(issues),
        )

    def _candidates(
        self,
        axis: TimeAxisName,
    ) -> tuple[list[TimeEntry] | None, list[ValidationIssue]]:
        if axis == "morphological" and not self.morphology_available:
            return None, [
                _issue(
                    "V-TIME-012",
                    "error",
                    "A morphological time axis was requested for a simulation "
                    "without morft.",
                    "Select the hydrodynamic time axis or use a morphodynamic run.",
                )
            ]
        candidates = [
            entry for entry in self.entries if _axis_time(entry, axis) is not None
        ]
        if not candidates:
            return None, [
                _issue(
                    "V-TIME-010",
                    "error",
                    f"No usable output exists on the {axis} time axis.",
                    "Check the model output time variables and their quality.",
                )
            ]
        return candidates, []

    def _nearest(
        self,
        candidates: Sequence[TimeEntry],
        requested_time: datetime,
        axis: TimeAxisName,
        tolerance: timedelta | None,
        duplicate_policy: DuplicatePolicy,
    ) -> tuple[
        TimeEntry | None,
        DuplicateResolution | None,
        ValidationIssue | None,
    ]:
        distances: list[float] = []
        for entry in candidates:
            candidate_time = _axis_time(entry, axis)
            if candidate_time is not None:
                distances.append(abs((candidate_time - requested_time).total_seconds()))
        if not distances:
            return (
                None,
                None,
                _issue(
                    "V-TIME-010",
                    "error",
                    f"No output can be matched to {requested_time.isoformat()}.",
                    "Check that the simulation has a usable output time axis.",
                ),
            )

        nearest_distance = min(distances)
        nearest_candidates: list[TimeEntry] = []
        for entry in candidates:
            candidate_time = _axis_time(entry, axis)
            if (
                candidate_time is not None
                and abs((candidate_time - requested_time).total_seconds())
                == nearest_distance
            ):
                nearest_candidates.append(entry)
        nearest = min(nearest_candidates, key=lambda entry: _hydro_order(entry))
        if tolerance is not None and nearest_distance > tolerance.total_seconds():
            nearest_text = _format_nearest(
                nearest,
                axis,
                self.source_utc_offset,
            )
            return (
                None,
                None,
                _issue(
                    "V-TIME-010",
                    "error",
                    (
                        f"Requested time {requested_time.isoformat()} is "
                        f"{nearest_distance:g} s from the nearest output, beyond "
                        f"the configured tolerance of {tolerance.total_seconds():g} s. "
                        f"Nearest available: {nearest_text}."
                    ),
                    "Increase times.tolerance or choose a time nearer to an output.",
                ),
            )

        nearest_axis_time = _axis_time(nearest, axis)
        assert nearest_axis_time is not None
        group = tuple(
            entry
            for entry in candidates
            if (candidate_time := _axis_time(entry, axis)) is not None
            and abs((candidate_time - nearest_axis_time).total_seconds())
            < _duplicate_tolerance(axis).total_seconds()
            and (
                tolerance is None
                or abs((candidate_time - requested_time).total_seconds())
                <= tolerance.total_seconds()
            )
        )
        chosen = _choose_duplicate(group or (nearest,), duplicate_policy)
        duplicate = (
            DuplicateResolution(
                requested_time=requested_time,
                candidates=group,
                chosen=chosen,
            )
            if len(group) > 1
            else None
        )
        return chosen, duplicate, None


def _read_vector(variable: _NetCDFVariable, name: str) -> Any:
    raw = np.ma.asarray(variable[...])
    if raw.ndim != 1:
        raise ValueError(f"time cache variable {name!r} must be a vector")
    values = np.asarray(raw.data, dtype=np.float64)
    values[np.ma.getmaskarray(raw)] = np.nan
    if not np.isfinite(values).all() and name == "time":
        raise ValueError("hydrodynamic time coordinate contains non-finite values")
    return values


def _read_integer_vector(variable: _NetCDFVariable, name: str) -> Any:
    raw = np.ma.asarray(variable[...])
    if raw.ndim != 1 or np.ma.getmaskarray(raw).any():
        raise ValueError(f"time cache variable {name!r} must be an unmasked vector")
    if not np.issubdtype(raw.dtype, np.integer):
        raise ValueError(f"time cache variable {name!r} must contain integers")
    return np.asarray(raw.data, dtype=np.int64)


def _decode_cache_time(value: float, units: str, calendar: str) -> datetime:
    instant = decode_cf_time(value, units, calendar, 0.0)
    if instant is None:
        raise ValueError(f"cannot decode cached CF time {value!r}")
    return instant


def _attribute_text(dataset: Any, name: str) -> str | None:
    if name not in dataset.ncattrs():
        return None
    value = dataset.getncattr(name)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return value if isinstance(value, str) else str(value)


def _request_datetime(value: date | datetime, naive_time_zone: str) -> datetime:
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime(value.year, value.month, value.day)
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone_from_offset(naive_time_zone))
    return value.astimezone(UTC)


def _time_range_requests(
    start: datetime,
    end: datetime,
    step: timedelta,
) -> list[datetime]:
    if step <= timedelta(0):
        raise ValueError("time-range step must be positive")
    requested: list[datetime] = []
    current = start
    while current <= end:
        requested.append(current)
        current += step
    return requested


def _axis_time(entry: TimeEntry, axis: TimeAxisName) -> datetime | None:
    return entry.time if axis == "hydrodynamic" else entry.morph_time


def _required_axis_time(entry: TimeEntry, axis: TimeAxisName) -> datetime:
    value = _axis_time(entry, axis)
    if value is None:
        raise ValueError(f"Output index {entry.time_index} has no {axis} time.")
    return value


def _duplicate_tolerance(axis: TimeAxisName) -> timedelta:
    return (
        _HYDRODYNAMIC_DUPLICATE_TOLERANCE
        if axis == "hydrodynamic"
        else _MORPHOLOGICAL_DUPLICATE_TOLERANCE
    )


def _effective_duplicate_policy(
    axis: TimeAxisName,
    duplicate_morphological_times: DuplicatePolicy,
) -> DuplicatePolicy:
    return duplicate_morphological_times if axis == "morphological" else "last"


def _collapse_duplicates(
    candidates: Sequence[TimeEntry],
    axis: TimeAxisName,
    tolerance: timedelta,
    policy: DuplicatePolicy,
    *,
    requested_time: datetime | None,
) -> tuple[list[TimeEntry], list[DuplicateResolution]]:
    ordered = sorted(
        candidates,
        key=lambda entry: (_required_axis_time(entry, axis), _hydro_order(entry)),
    )
    selected: list[TimeEntry] = []
    duplicates: list[DuplicateResolution] = []
    group: list[TimeEntry] = []
    group_start: datetime | None = None

    def finish_group() -> None:
        if not group:
            return
        chosen = _choose_duplicate(group, policy)
        selected.append(chosen)
        if len(group) > 1:
            duplicates.append(
                DuplicateResolution(
                    requested_time=requested_time,
                    candidates=tuple(group),
                    chosen=chosen,
                )
            )

    for entry in ordered:
        candidate_time = _axis_time(entry, axis)
        if candidate_time is None:
            continue
        if (
            group_start is None
            or (candidate_time - group_start).total_seconds()
            >= tolerance.total_seconds()
        ):
            finish_group()
            group = [entry]
            group_start = candidate_time
        else:
            group.append(entry)
    finish_group()
    selected.sort(key=_hydro_order)
    return selected, duplicates


def _choose_duplicate(
    candidates: Sequence[TimeEntry],
    policy: DuplicatePolicy,
) -> TimeEntry:
    return (
        min(candidates, key=_hydro_order)
        if policy == "first"
        else max(
            candidates,
            key=_hydro_order,
        )
    )


def _hydro_order(entry: TimeEntry) -> tuple[datetime, int, int]:
    return entry.time, entry.subrun, entry.local_index


def _unique_in_hydrodynamic_order(
    entries: Sequence[TimeEntry],
) -> list[TimeEntry]:
    unique = {(entry.subrun, entry.local_index): entry for entry in entries}
    return sorted(unique.values(), key=_hydro_order)


def _apply_corrupt_policy(
    entries: Sequence[TimeEntry],
    policy: CorruptOutputPolicy,
    issues: list[ValidationIssue],
) -> list[TimeEntry]:
    selected: list[TimeEntry] = []
    for entry in entries:
        if not entry.corrupt_partitions:
            selected.append(entry)
            continue
        action = {
            "mask": "the affected partition faces will be masked",
            "skip": "the output was skipped",
            "error": "the output was rejected",
        }[policy]
        level: Literal["error", "warning"] = "error" if policy == "error" else "warning"
        issues.append(
            ValidationIssue(
                code="V-TIME-011",
                level=level,
                location=f"sub-run {entry.subrun}, map index {entry.local_index}",
                message=(
                    f"Selected output has corrupt partition(s) "
                    f"{list(entry.corrupt_partitions)}; {action}."
                ),
                hint="Choose another output or change on_corrupt_output.",
            )
        )
        if policy == "mask":
            selected.append(entry)
    return selected


def _format_nearest(
    entry: TimeEntry,
    axis: TimeAxisName,
    source_utc_offset: str,
) -> str:
    instant = _axis_time(entry, axis)
    if instant is None:
        return "<unavailable>"
    return instant.astimezone(timezone_from_offset(source_utc_offset)).isoformat()


def _issue(
    code: str,
    level: Literal["error", "warning"],
    message: str,
    hint: str,
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        level=level,
        location="time axis",
        message=message,
        hint=hint,
    )
