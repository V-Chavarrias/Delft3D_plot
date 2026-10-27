# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic process-task planning and sharding."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import blake2b
from math import ceil
from pathlib import Path
from urllib.parse import quote

from d3dplot.config.analysis import ProcessingPolicy
from d3dplot.execution.cache import (
    CacheDecision,
    decide_cache,
    read_cache_fingerprints,
)
from d3dplot.simulation.time_axis import TimeEntry, TimeSelectionResult
from d3dplot.variables.context import QuantityRequest


@dataclass(frozen=True, slots=True)
class OutputCacheRequest:
    """Expected identity and destination of one per-time analysis output."""

    time_index: int
    request: QuantityRequest
    output_path: str | Path
    expected_fingerprint: str
    policy: ProcessingPolicy

    def __post_init__(self) -> None:
        if self.time_index < 0:
            raise ValueError("time_index cannot be negative.")
        if not str(self.output_path):
            raise ValueError("output_path must be non-empty.")
        if not self.expected_fingerprint:
            raise ValueError("expected_fingerprint must be non-empty.")
        if self.policy not in {"if_changed", "never", "always"}:
            raise ValueError(f"Unsupported reprocess policy: {self.policy!r}.")


@dataclass(frozen=True, slots=True)
class OutputCacheState:
    """Fingerprint state for one analysis quantity at one selected output."""

    time_index: int
    request: QuantityRequest
    output_path: str
    expected_fingerprint: str
    exists: bool
    existing_fingerprint: str | None
    policy: ProcessingPolicy

    def __post_init__(self) -> None:
        if self.time_index < 0:
            raise ValueError("time_index cannot be negative.")
        if not self.output_path:
            raise ValueError("output_path must be non-empty.")
        if not self.expected_fingerprint:
            raise ValueError("expected_fingerprint must be non-empty.")
        if not self.exists and self.existing_fingerprint is not None:
            raise ValueError("A missing output cannot have an existing fingerprint.")

    @property
    def decision(self) -> CacheDecision:
        """Return the cache action for this output."""
        return decide_cache(
            exists=self.exists,
            existing_fingerprint=self.existing_fingerprint,
            expected_fingerprint=self.expected_fingerprint,
            policy=self.policy,
        )


def read_output_cache_states(
    requests: Sequence[OutputCacheRequest],
) -> tuple[OutputCacheState, ...]:
    """Resolve existing fingerprints into the states consumed by the planner."""
    if not requests:
        return ()

    identities = [(item.time_index, item.request) for item in requests]
    if len(set(identities)) != len(identities):
        raise ValueError("Output cache requests must be unique per time and quantity.")

    paths = tuple(
        Path(item.output_path).expanduser().resolve(strict=False) for item in requests
    )
    if len(set(paths)) != len(paths):
        raise ValueError("Output cache request paths must be unique.")

    fingerprints = read_cache_fingerprints(paths)
    states: list[OutputCacheState] = []
    for item, path in zip(requests, paths, strict=True):
        exists = path.exists()
        states.append(
            OutputCacheState(
                time_index=item.time_index,
                request=item.request,
                output_path=str(path),
                expected_fingerprint=item.expected_fingerprint,
                exists=exists,
                existing_fingerprint=fingerprints[path] if exists else None,
                policy=item.policy,
            )
        )
    return tuple(states)


@dataclass(frozen=True, slots=True)
class AnalysisTimeSelection:
    """One analysis's requested quantities and resolved outputs for a simulation."""

    analysis_key: str
    quantities: tuple[QuantityRequest, ...]
    selection: TimeSelectionResult
    output_cache: tuple[OutputCacheState, ...] = ()

    def __post_init__(self) -> None:
        if not self.analysis_key or self.analysis_key != self.analysis_key.strip():
            raise ValueError("analysis_key must be non-empty and trimmed.")
        if not self.quantities:
            raise ValueError("An analysis selection must contain a quantity request.")
        if len(set(self.quantities)) != len(self.quantities):
            raise ValueError("An analysis selection cannot repeat quantity requests.")
        selected_indices = {entry.time_index for entry in self.selection.entries}
        cache_keys = [(state.time_index, state.request) for state in self.output_cache]
        if len(set(cache_keys)) != len(cache_keys):
            raise ValueError(
                "Output-cache entries must be unique per time and request."
            )
        if any(index not in selected_indices for index, _ in cache_keys):
            raise ValueError("Output-cache entries must belong to selected outputs.")
        if any(request not in self.quantities for _, request in cache_keys):
            raise ValueError("Output-cache entries must match requested quantities.")
        cache_paths = [
            (state.time_index, state.output_path) for state in self.output_cache
        ]
        if len(set(cache_paths)) != len(cache_paths):
            raise ValueError("Output paths must be unique per selected time.")


@dataclass(frozen=True, slots=True)
class AnalysisTimeWork:
    """Quantities requested by one analysis at a selected map output."""

    analysis_key: str
    quantities: tuple[QuantityRequest, ...]

    def __post_init__(self) -> None:
        if not self.analysis_key or not self.quantities:
            raise ValueError("Analysis time work needs a key and quantities.")


@dataclass(frozen=True, slots=True)
class ProcessTime:
    """A selected output and all analyses that need values at that output."""

    entry: TimeEntry
    analyses: tuple[AnalysisTimeWork, ...]

    def __post_init__(self) -> None:
        if not self.analyses:
            raise ValueError("A process time must be used by at least one analysis.")
        analysis_keys = [analysis.analysis_key for analysis in self.analyses]
        if len(set(analysis_keys)) != len(analysis_keys):
            raise ValueError("Process time analysis keys must be unique.")


@dataclass(frozen=True, slots=True)
class ProcessTask:
    """One simulation/sub-run/time block shared by all analyses."""

    key: str
    simulation_label: str
    subrun_index: int
    times: tuple[ProcessTime, ...]

    def __post_init__(self) -> None:
        if not self.key or not self.simulation_label or not self.times:
            raise ValueError("A process task needs a key, simulation, and times.")
        if self.subrun_index < 0:
            raise ValueError("subrun_index cannot be negative.")
        if any(time_work.entry.subrun != self.subrun_index for time_work in self.times):
            raise ValueError("Every process-task time must belong to its sub-run.")
        time_indices = [time_work.entry.time_index for time_work in self.times]
        if len(set(time_indices)) != len(time_indices):
            raise ValueError("A process task cannot repeat time indices.")


def plan_process_tasks(
    simulation_label: str,
    selections: Sequence[AnalysisTimeSelection],
    *,
    workers: int,
    max_block_times: int,
    shard: str | None = None,
) -> tuple[ProcessTask, ...]:
    """Merge analysis selections, group by sub-run, and split into time blocks."""
    if not simulation_label or simulation_label != simulation_label.strip():
        raise ValueError("simulation_label must be non-empty and trimmed.")
    if workers < 1:
        raise ValueError("workers must be positive.")
    if max_block_times < 1:
        raise ValueError("max_block_times must be positive.")

    analysis_keys = [selection.analysis_key for selection in selections]
    if len(set(analysis_keys)) != len(analysis_keys):
        raise ValueError("analysis_key values must be unique within a simulation.")

    by_time: dict[
        tuple[int, int],
        tuple[TimeEntry, dict[str, tuple[QuantityRequest, ...]]],
    ] = {}
    for selection in selections:
        if not selection.selection.is_valid:
            raise ValueError(
                f"Cannot plan invalid time selection for {selection.analysis_key!r}."
            )
        cache_by_time_and_request = {
            (state.time_index, state.request): state for state in selection.output_cache
        }
        for entry in selection.selection.entries:
            needed_quantities = tuple(
                request
                for request in selection.quantities
                if (
                    (
                        state := cache_by_time_and_request.get(
                            (entry.time_index, request)
                        )
                    )
                    is None
                    or state.decision.action == "rebuild"
                )
            )
            if not needed_quantities:
                continue
            time_key = (entry.subrun, entry.time_index)
            existing = by_time.get(time_key)
            if existing is None:
                work_by_analysis: dict[str, tuple[QuantityRequest, ...]] = {}
                by_time[time_key] = (entry, work_by_analysis)
            else:
                existing_entry, work_by_analysis = existing
                if existing_entry != entry:
                    raise ValueError(
                        "Conflicting time entries share sub-run and time indices."
                    )
            work_by_analysis[selection.analysis_key] = needed_quantities

    if not by_time:
        return ()

    block_size = min(
        max_block_times,
        max(1, ceil(len(by_time) / (workers * 4))),
    )
    by_subrun: dict[int, list[ProcessTime]] = {}
    for (subrun_index, _), (entry, work_by_analysis) in by_time.items():
        time_work = ProcessTime(
            entry=entry,
            analyses=tuple(
                AnalysisTimeWork(analysis_key=key, quantities=quantities)
                for key, quantities in sorted(work_by_analysis.items())
            ),
        )
        by_subrun.setdefault(subrun_index, []).append(time_work)

    tasks: list[ProcessTask] = []
    encoded_label = quote(simulation_label, safe="-_.")
    for subrun_index, subrun_times in sorted(by_subrun.items()):
        subrun_times.sort(key=lambda item: item.entry.time_index)
        for offset in range(0, len(subrun_times), block_size):
            block = tuple(subrun_times[offset : offset + block_size])
            time_indices = ",".join(str(item.entry.time_index) for item in block)
            task_key = (
                f"process|{encoded_label}|subrun={subrun_index}|times={time_indices}"
            )
            tasks.append(
                ProcessTask(
                    key=task_key,
                    simulation_label=simulation_label,
                    subrun_index=subrun_index,
                    times=block,
                )
            )

    tasks.sort(key=lambda task: (-len(task.times), task.key))
    if shard is not None:
        shard_index, shard_count = _parse_shard(shard)
        tasks = [
            task
            for task in tasks
            if stable_shard_index(task.key, shard_count) == shard_index
        ]
    return tuple(tasks)


def stable_shard_index(task_key: str, shard_count: int) -> int:
    """Map a canonical task key to a stable zero-based shard."""
    if not task_key:
        raise ValueError("task_key must be non-empty.")
    if shard_count < 1:
        raise ValueError("shard_count must be positive.")
    digest_prefix = blake2b(task_key.encode("utf-8")).digest()[:8]
    return int.from_bytes(digest_prefix, "big") % shard_count


def _parse_shard(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(0|[1-9]\d*)/([1-9]\d*)", value)
    if match is None:
        raise ValueError("shard must use the form index/count.")
    index, count = (int(part) for part in match.groups())
    if index >= count:
        raise ValueError("shard index must be less than its count.")
    return index, count
