# SPDX-License-Identifier: GPL-3.0-or-later
"""Detect and serialize plain-FM and SMT simulation status."""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum
from functools import partial
from importlib import import_module
from pathlib import Path
from typing import Literal, Protocol, TypeAlias, cast

from d3dplot.config.status import DEFAULT_STATUS_STALE_AFTER
from d3dplot.model_input.mdu import MduInfo
from d3dplot.simulation.discover import (
    _MAX_DISCOVERY_WORKERS,
    _MIN_SUBRUNS_FOR_PARALLEL,
    Simulation,
    SimulationKind,
    discover_simulation,
)
from d3dplot.simulation.partitions import _NETCDF4_READ_LOCK
from d3dplot.simulation.subrun import SubRun
from d3dplot.validate.report import ValidationReport

_DIA_TAIL_BYTES = 4096
_FINISHED_MARKER = "Computation finished"
_INTERRUPTED_MARKERS = (
    "*** ERROR Flow exited abnormally",
    "** INFO   : Simulation did not reach stop time",
)
_CF_TIME_UNITS = re.compile(
    r"^\s*(?P<unit>[A-Za-z]+)\s+since\s+(?P<origin>.+?)\s*$",
    re.IGNORECASE,
)
_DATETIME_TOKEN = re.compile(r"^\d{14}$")
_CSV_FIELDS = (
    "label",
    "path",
    "kind",
    "state",
    "n_subruns",
    "n_partitions",
    "last_output_time",
    "last_morph_time",
    "morfac",
    "corrupt_outputs",
    "checked_at",
    "dflowfm_version",
)
_TIME_UNIT_SECONDS = {
    "s": 1.0,
    "sec": 1.0,
    "secs": 1.0,
    "second": 1.0,
    "seconds": 1.0,
    "min": 60.0,
    "mins": 60.0,
    "minute": 60.0,
    "minutes": 60.0,
    "h": 3600.0,
    "hr": 3600.0,
    "hrs": 3600.0,
    "hour": 3600.0,
    "hours": 3600.0,
    "d": 86400.0,
    "day": 86400.0,
    "days": 86400.0,
    "ms": 0.001,
    "millisecond": 0.001,
    "milliseconds": 0.001,
    "us": 0.000001,
    "microsecond": 0.000001,
    "microseconds": 0.000001,
}

TextWriter: TypeAlias = Callable[[Path, str], None]


class SimulationState(StrEnum):
    """Observed state of a simulation or one of its sub-runs."""

    NOT_STARTED = "not_started"
    RUNNING = "running"
    STOPPED = "stopped"
    INTERRUPTED = "interrupted"
    FINISHED = "finished"


class SimulationStatusError(RuntimeError):
    """Raised when status metadata cannot be read consistently."""


class _NetCDFVariable(Protocol):
    """Subset of the netCDF4 variable API used by status inspection."""

    @property
    def shape(self) -> tuple[int, ...]: ...

    def __getitem__(self, index: int) -> object: ...


class _NetCDFDataset(Protocol):
    """Subset of the netCDF4 dataset API used by status inspection."""

    @property
    def variables(self) -> Mapping[str, _NetCDFVariable]: ...

    def ncattrs(self) -> list[str]: ...

    def getncattr(self, name: str) -> object: ...


class _NetCDF4Module(Protocol):
    """Subset of netCDF4 needed to open model outputs."""

    def Dataset(
        self,
        path: Path,
        *,
        mode: Literal["r"],
    ) -> AbstractContextManager[_NetCDFDataset]: ...


@dataclass(frozen=True, slots=True)
class CorruptPartitionStatus:
    """Corrupt map-time indices for one partition, when quality checks ran."""

    partition: int
    times: tuple[int, ...]

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-compatible representation."""
        return {"partition": self.partition, "times": list(self.times)}


@dataclass(frozen=True, slots=True)
class SubRunStatus:
    """Status and final output metadata for one FM sub-run."""

    index: int
    state: SimulationState
    last_time: str | None
    last_morph_time: float | str | None
    corrupt: tuple[CorruptPartitionStatus, ...] | None = None
    dflowfm_version: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-compatible representation."""
        corrupt: list[dict[str, object]] | None
        if self.corrupt is None:
            corrupt = None
        else:
            corrupt = [item.to_dict() for item in self.corrupt]
        return {
            "index": self.index,
            "state": self.state.value,
            "last_time": self.last_time,
            "last_morph_time": self.last_morph_time,
            "corrupt": corrupt,
        }


@dataclass(frozen=True, slots=True)
class SimulationStatus:
    """Machine- and human-readable status summary for one simulation."""

    label: str
    path: Path
    kind: SimulationKind
    state: SimulationState
    checked_at: datetime
    subruns: tuple[SubRunStatus, ...]
    n_partitions: int
    morfac: float | None
    dflowfm_version: str | None
    tool_version: str

    @property
    def corrupt_outputs(self) -> int | None:
        """Count corrupt partition/time pairs, or ``None`` before quality checks."""
        if any(subrun.corrupt is None for subrun in self.subruns):
            return None
        return sum(
            len(item.times) for subrun in self.subruns for item in subrun.corrupt or ()
        )

    def to_dict(self) -> dict[str, object]:
        """Return the stable JSON representation of this status."""
        return {
            "label": self.label,
            "path": str(self.path),
            "kind": self.kind.value,
            "state": self.state.value,
            "checked_at": _utc_isoformat(self.checked_at),
            "subruns": [subrun.to_dict() for subrun in self.subruns],
            "n_partitions": self.n_partitions,
            "morfac": self.morfac,
            "corrupt_outputs": self.corrupt_outputs,
            "dflowfm_version": self.dflowfm_version,
            "tool_version": self.tool_version,
        }

    def to_csv_row(self) -> dict[str, object]:
        """Return the stable one-row CSV representation."""
        last_subrun = self.subruns[-1] if self.subruns else None
        return {
            "label": self.label,
            "path": str(self.path),
            "kind": self.kind.value,
            "state": self.state.value,
            "n_subruns": len(self.subruns),
            "n_partitions": self.n_partitions,
            "last_output_time": (
                last_subrun.last_time if last_subrun is not None else None
            ),
            "last_morph_time": (
                last_subrun.last_morph_time if last_subrun is not None else None
            ),
            "morfac": self.morfac,
            "corrupt_outputs": self.corrupt_outputs,
            "checked_at": _utc_isoformat(self.checked_at),
            "dflowfm_version": self.dflowfm_version,
        }


@dataclass(frozen=True, slots=True)
class _MapMetadata:
    last_time: datetime | None
    last_time_value: float | None
    time_unit_seconds: float | None
    last_morph_time: float | str | None
    dflowfm_version: str | None


@dataclass(frozen=True, slots=True)
class _StopTime:
    elapsed_seconds: float | None
    instant: datetime | None


def detect_simulation_status(
    simulation: Simulation,
    *,
    label: str | None = None,
    now: datetime | None = None,
    stale_after: timedelta = DEFAULT_STATUS_STALE_AFTER,
) -> SimulationStatus:
    """Detect per-sub-run and overall status without modifying model outputs."""
    checked_at = _as_utc(now if now is not None else datetime.now(UTC))
    _validate_stale_after(stale_after)
    detect_status = partial(
        _detect_subrun_status_for_model,
        now=checked_at,
        stale_after=stale_after,
    )
    if len(simulation.subruns) < _MIN_SUBRUNS_FOR_PARALLEL:
        subrun_statuses = tuple(detect_status(subrun) for subrun in simulation.subruns)
    else:
        worker_count = min(_MAX_DISCOVERY_WORKERS, len(simulation.subruns))
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="d3dplot-status",
        ) as executor:
            subrun_statuses = tuple(executor.map(detect_status, simulation.subruns))
    state = _simulation_state(simulation, subrun_statuses)
    morphology = next(
        (
            subrun.model_input.morphology
            for subrun in reversed(simulation.subruns)
            if subrun.model_input.morphology is not None
            and subrun.model_input.morphology.morfac is not None
        ),
        None,
    )
    version = next(
        (
            status.dflowfm_version
            for status in reversed(subrun_statuses)
            if status.dflowfm_version is not None
        ),
        None,
    )
    from d3dplot import __version__

    return SimulationStatus(
        label=label or simulation.root.name,
        path=simulation.root,
        kind=simulation.kind,
        state=state,
        checked_at=checked_at,
        subruns=subrun_statuses,
        n_partitions=(
            simulation.subruns[0].partition_count if simulation.subruns else 0
        ),
        morfac=morphology.morfac if morphology is not None else None,
        dflowfm_version=version,
        tool_version=__version__,
    )


def _detect_subrun_status_for_model(
    subrun: SubRun,
    *,
    now: datetime,
    stale_after: timedelta,
) -> SubRunStatus:
    return detect_subrun_status(
        subrun.index,
        subrun.mdu,
        subrun.map_files,
        now=now,
        stale_after=stale_after,
    )


def inspect_simulation_status(
    path: str | Path,
    *,
    label: str | None = None,
    now: datetime | None = None,
    stale_after: timedelta = DEFAULT_STATUS_STALE_AFTER,
) -> tuple[SimulationStatus | None, ValidationReport]:
    """Discover a simulation and report status, including absent map output."""
    root = Path(path).expanduser().resolve(strict=False)
    simulation, report = discover_simulation(root)
    if simulation is not None:
        return (
            detect_simulation_status(
                simulation,
                label=label,
                now=now,
                stale_after=stale_after,
            ),
            report,
        )
    if not report.errors or any(issue.code != "V-OUT-001" for issue in report.errors):
        return None, report

    checked_at = _as_utc(now if now is not None else datetime.now(UTC))
    _validate_stale_after(stale_after)
    kind = SimulationKind.SMT if (root / "smt.yml").is_file() else SimulationKind.FM
    n_subruns = len(report.errors) if kind is SimulationKind.SMT else 1
    subruns = tuple(
        SubRunStatus(
            index=index,
            state=SimulationState.NOT_STARTED,
            last_time=None,
            last_morph_time=None,
        )
        for index in range(n_subruns)
    )
    state = (
        SimulationState.RUNNING
        if kind is SimulationKind.SMT and _path_exists(root / "output" / "work")
        else SimulationState.NOT_STARTED
    )
    from d3dplot import __version__

    return (
        SimulationStatus(
            label=label or root.name,
            path=root,
            kind=kind,
            state=state,
            checked_at=checked_at,
            subruns=subruns,
            n_partitions=0,
            morfac=None,
            dflowfm_version=None,
            tool_version=__version__,
        ),
        report,
    )


def detect_subrun_status(
    index: int,
    mdu: MduInfo,
    map_files: Sequence[Path],
    *,
    now: datetime | None = None,
    stale_after: timedelta = DEFAULT_STATUS_STALE_AFTER,
) -> SubRunStatus:
    """Detect one FM sub-run from map metadata and the tails of its ``.dia`` files."""
    checked_at = _as_utc(now if now is not None else datetime.now(UTC))
    _validate_stale_after(stale_after)
    if not map_files:
        return SubRunStatus(
            index=index,
            state=SimulationState.NOT_STARTED,
            last_time=None,
            last_morph_time=None,
        )

    dia_paths = tuple(_dia_path(map_file) for map_file in map_files)
    dia_tails = tuple(_read_dia_tail(path) for path in dia_paths)
    metadata = _read_map_metadata(map_files, mdu)
    latest_modified = _latest_modified_time(map_files, dia_paths)

    if all(tail is not None and _FINISHED_MARKER in tail for tail in dia_tails):
        state = SimulationState.FINISHED
    elif any(
        tail is not None and any(marker in tail for marker in _INTERRUPTED_MARKERS)
        for tail in dia_tails
    ):
        state = SimulationState.INTERRUPTED
    elif (
        latest_modified is not None
        and checked_at - latest_modified >= stale_after
        and _is_before_mdu_stop(metadata, mdu)
    ):
        state = SimulationState.STOPPED
    else:
        state = SimulationState.RUNNING

    return SubRunStatus(
        index=index,
        state=state,
        last_time=(
            _utc_isoformat(metadata.last_time)
            if metadata.last_time is not None
            else None
        ),
        last_morph_time=metadata.last_morph_time,
        dflowfm_version=metadata.dflowfm_version,
    )


def dump_simulation_status_json(status: SimulationStatus) -> str:
    """Serialize one simulation status as JSON text."""
    return json.dumps(status.to_dict(), ensure_ascii=False, indent=2) + "\n"


def write_simulation_status_json(
    status: SimulationStatus,
    path: str | Path,
    writer: TextWriter,
) -> None:
    """Write JSON through a caller-supplied, guarded text writer."""
    writer(Path(path), dump_simulation_status_json(status))


def dump_simulation_status_csv(statuses: Iterable[SimulationStatus]) -> str:
    """Serialize simulation statuses as a stable CSV table."""
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=_CSV_FIELDS,
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(status.to_csv_row() for status in statuses)
    return stream.getvalue()


def write_simulation_status_csv(
    statuses: Iterable[SimulationStatus],
    path: str | Path,
    writer: TextWriter,
) -> None:
    """Write the CSV table through a caller-supplied, guarded text writer."""
    writer(Path(path), dump_simulation_status_csv(statuses))


def _simulation_state(
    simulation: Simulation,
    subruns: tuple[SubRunStatus, ...],
) -> SimulationState:
    if simulation.kind is SimulationKind.SMT and _path_exists(
        simulation.root / "output" / "work"
    ):
        return SimulationState.RUNNING
    if not subruns:
        return SimulationState.NOT_STARTED
    if all(subrun.state is SimulationState.FINISHED for subrun in subruns):
        return SimulationState.FINISHED
    if simulation.kind is SimulationKind.FM:
        return subruns[0].state
    for subrun in reversed(subruns):
        if subrun.state is not SimulationState.FINISHED:
            return subrun.state
    return SimulationState.FINISHED


def _read_map_metadata(
    map_files: Sequence[Path],
    mdu: MduInfo,
) -> _MapMetadata:
    netcdf4 = _netcdf4_module()
    fallback: _MapMetadata | None = None
    for path in map_files:
        try:
            with _NETCDF4_READ_LOCK, netcdf4.Dataset(path, mode="r") as dataset:
                time_variable = dataset.variables.get("time")
                if time_variable is None:
                    raise SimulationStatusError(
                        f"Map file has no time variable: {path}"
                    )
                if not time_variable.shape or time_variable.shape[0] == 0:
                    candidate = _MapMetadata(None, None, None, None, None)
                    if fallback is None:
                        fallback = candidate
                    continue

                raw_time = _finite_value(time_variable[-1])
                time_units = _as_text(getattr(time_variable, "units", None))
                time_unit_seconds = _time_unit_seconds(time_units)
                calendar = _as_text(getattr(time_variable, "calendar", None))
                last_time = (
                    _decode_cf_time(
                        raw_time,
                        time_units,
                        calendar or "standard",
                        mdu.tzone,
                    )
                    if raw_time is not None and time_units is not None
                    else None
                )

                morph_variable = dataset.variables.get("morft")
                last_morph_time = _read_morph_time(
                    morph_variable,
                    time_units,
                    calendar or "standard",
                    mdu.tzone,
                )
                source = (
                    _as_text(dataset.getncattr("source"))
                    if "source" in dataset.ncattrs()
                    else None
                )
                candidate = _MapMetadata(
                    last_time=last_time,
                    last_time_value=raw_time,
                    time_unit_seconds=time_unit_seconds,
                    last_morph_time=last_morph_time,
                    dflowfm_version=_dflowfm_version(source),
                )
                if fallback is None:
                    fallback = candidate
                if raw_time is not None:
                    return candidate
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            if isinstance(error, SimulationStatusError):
                raise
            raise SimulationStatusError(
                f"Could not read status metadata from map file {path}: {error}"
            ) from error

    if fallback is not None:
        return fallback
    raise SimulationStatusError(
        f"Could not read status metadata from any map file in {mdu.output_dir}."
    )


def _read_morph_time(
    variable: _NetCDFVariable | None,
    fallback_units: str | None,
    calendar: str,
    tzone: float,
) -> float | str | None:
    if variable is None or not variable.shape or variable.shape[0] == 0:
        return None
    value = _finite_value(variable[-1])
    if value is None:
        return None
    units = _as_text(getattr(variable, "units", None)) or fallback_units
    if units is None:
        return value
    decoded = _decode_cf_time(value, units, calendar, tzone)
    return _utc_isoformat(decoded) if decoded is not None else value


def _finite_value(value: object) -> float | None:
    try:
        if bool(getattr(value, "mask", False)):
            return None
    except (TypeError, ValueError):
        return None
    try:
        numeric = float(cast(float, value))
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _decode_cf_time(
    value: float,
    units: str,
    calendar: str,
    tzone: float,
) -> datetime | None:
    match = _CF_TIME_UNITS.fullmatch(units)
    if match is None:
        return None
    seconds_per_unit = _TIME_UNIT_SECONDS.get(match.group("unit").casefold())
    if seconds_per_unit is None:
        return None
    origin_text = match.group("origin").strip().replace("Z", "+00:00")
    try:
        origin = datetime.fromisoformat(origin_text)
    except ValueError:
        return None
    if origin.tzinfo is None:
        origin = origin.replace(tzinfo=_fixed_timezone(tzone))
    try:
        decoded = origin + timedelta(seconds=value * seconds_per_unit)
    except OverflowError:
        return None
    if calendar.casefold() not in {"standard", "gregorian", "proleptic_gregorian"}:
        return None
    return decoded.astimezone(UTC)


def _time_unit_seconds(units: str | None) -> float | None:
    if units is None:
        return None
    match = _CF_TIME_UNITS.fullmatch(units)
    unit = match.group("unit") if match is not None else units.strip()
    return _TIME_UNIT_SECONDS.get(unit.casefold())


def _is_before_mdu_stop(metadata: _MapMetadata, mdu: MduInfo) -> bool:
    stop_time = _mdu_stop_time(mdu)
    if (
        stop_time.elapsed_seconds is not None
        and metadata.last_time_value is not None
        and metadata.time_unit_seconds is not None
    ):
        # Numeric MDU and map times are elapsed offsets; CF origins may differ.
        return (
            metadata.last_time_value * metadata.time_unit_seconds
            < stop_time.elapsed_seconds
        )
    if stop_time.instant is not None and metadata.last_time is not None:
        return metadata.last_time < stop_time.instant
    return False


def _mdu_stop_time(mdu: MduInfo) -> _StopTime:
    value = str(mdu.tstop).strip()
    if _DATETIME_TOKEN.fullmatch(value):
        instant = datetime.strptime(value, "%Y%m%d%H%M%S").replace(
            tzinfo=_fixed_timezone(mdu.tzone)
        )
        return _StopTime(None, instant.astimezone(UTC))
    try:
        numeric = float(value)
    except ValueError:
        try:
            instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return _StopTime(None, None)
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=_fixed_timezone(mdu.tzone))
        return _StopTime(None, instant.astimezone(UTC))
    seconds_per_unit = _TIME_UNIT_SECONDS.get(mdu.tunit.casefold())
    if seconds_per_unit is None or not math.isfinite(numeric):
        return _StopTime(None, None)
    return _StopTime(numeric * seconds_per_unit, None)


def _fixed_timezone(offset_hours: float) -> timezone:
    if not math.isfinite(offset_hours) or abs(offset_hours) > 14:
        raise SimulationStatusError(
            f"MDU time-zone offset is outside the supported range: {offset_hours}"
        )
    try:
        return timezone(timedelta(hours=offset_hours))
    except ValueError as error:
        raise SimulationStatusError(
            f"Invalid MDU time-zone offset: {offset_hours}"
        ) from error


def _dia_path(map_file: Path) -> Path:
    stem = map_file.stem
    if stem.casefold().endswith("_map"):
        stem = stem[:-4]
    return map_file.with_name(f"{stem}.dia")


def _read_dia_tail(path: Path) -> str | None:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - _DIA_TAIL_BYTES), os.SEEK_SET)
            return stream.read(_DIA_TAIL_BYTES).decode("utf-8", errors="replace")
    except FileNotFoundError:
        return None


def _latest_modified_time(
    map_files: Sequence[Path],
    dia_paths: Sequence[Path],
) -> datetime | None:
    timestamps: list[float] = []
    for path in map_files:
        timestamps.append(path.stat().st_mtime)
    for path in dia_paths:
        try:
            timestamps.append(path.stat().st_mtime)
        except FileNotFoundError:
            continue
    if not timestamps:
        return None
    return datetime.fromtimestamp(max(timestamps), tz=UTC)


def _dflowfm_version(source: str | None) -> str | None:
    if source is None:
        return None
    match = re.search(r"D-Flow\s+FM\s+([^\s;(]+)", source, flags=re.IGNORECASE)
    return match.group(1) if match is not None else None


def _as_text(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace").strip()
    else:
        text = str(value).strip()
    return text or None


def _netcdf4_module() -> _NetCDF4Module:
    os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")
    return cast(_NetCDF4Module, import_module("netCDF4"))


def _path_exists(path: Path) -> bool:
    try:
        path.stat()
    except FileNotFoundError:
        return False
    return True


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be time-zone-aware")
    return value.astimezone(UTC)


def _validate_stale_after(value: timedelta) -> None:
    if value < timedelta(0):
        raise ValueError("stale_after must be non-negative")


def _utc_isoformat(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()
