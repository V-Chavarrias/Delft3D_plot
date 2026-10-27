# SPDX-License-Identifier: GPL-3.0-or-later
"""Resource-aware worker-count selection."""

from __future__ import annotations

from dataclasses import dataclass

import psutil


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    """Physical CPU and currently available memory used to size a task pool."""

    physical_cores: int
    available_memory_bytes: int
    used_logical_core_fallback: bool = False

    def __post_init__(self) -> None:
        if self.physical_cores < 1:
            raise ValueError("physical_cores must be positive.")
        if self.available_memory_bytes < 0:
            raise ValueError("available_memory_bytes cannot be negative.")


def current_resource_snapshot() -> ResourceSnapshot:
    """Read physical cores and available memory from the local host."""
    physical_cores = psutil.cpu_count(logical=False)
    used_logical_core_fallback = physical_cores is None
    if physical_cores is None:
        physical_cores = psutil.cpu_count(logical=True) or 1
    return ResourceSnapshot(
        physical_cores=max(1, physical_cores),
        available_memory_bytes=psutil.virtual_memory().available,
        used_logical_core_fallback=used_logical_core_fallback,
    )


def resolve_worker_count(
    task_count: int,
    configured_workers: int | None = None,
    *,
    estimated_task_memory_bytes: int | None = None,
    resources: ResourceSnapshot | None = None,
) -> int:
    """Resolve the worker count, capped by tasks and the estimated memory budget."""
    if task_count < 0:
        raise ValueError("task_count cannot be negative.")
    if configured_workers is not None and configured_workers < 1:
        raise ValueError("configured_workers must be positive.")
    if estimated_task_memory_bytes is not None and estimated_task_memory_bytes < 1:
        raise ValueError("estimated_task_memory_bytes must be positive.")
    if task_count == 0:
        return 0

    snapshot = resources or current_resource_snapshot()
    worker_limit = (
        configured_workers
        if configured_workers is not None
        else max(1, snapshot.physical_cores - 1)
    )
    if estimated_task_memory_bytes is not None:
        memory_limit = max(
            1,
            snapshot.available_memory_bytes // estimated_task_memory_bytes,
        )
        worker_limit = min(worker_limit, memory_limit)
    return min(task_count, worker_limit)
