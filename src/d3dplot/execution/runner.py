# SPDX-License-Identifier: GPL-3.0-or-later
"""Serial and spawn-based task execution with per-task failure isolation."""

from __future__ import annotations

import logging
import multiprocessing
import sys
import traceback
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from csv import DictWriter
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from logging.handlers import QueueListener
from multiprocessing.queues import Queue
from pathlib import Path
from typing import Literal

from d3dplot.execution.resources import ResourceSnapshot, resolve_worker_count
from d3dplot.execution.tasks import ProcessTask
from d3dplot.execution.worker import initialize_worker, worker_environment
from d3dplot.store import OutputWriteGuard, atomic_write_text

TaskFunction = Callable[[ProcessTask], object]
ExecutorName = Literal["serial", "processes"]
TaskStatus = Literal["succeeded", "failed"]
TaskDiagnosticLevel = Literal["info", "warning", "error"]
_PROCESS_WORKER: TaskFunction | None = None


@dataclass(frozen=True, slots=True)
class TaskDiagnostic:
    """One user-facing message returned by a successful task."""

    level: TaskDiagnosticLevel
    message: str


@dataclass(frozen=True, slots=True)
class TaskOutputReport:
    """Common diagnostic payload for analysis-specific task results."""

    diagnostics: tuple[TaskDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class TaskResult:
    """Outcome of one task, including its traceback when it fails."""

    task_key: str
    status: TaskStatus
    duration_seconds: float
    value: object | None = None
    error: str | None = None
    traceback: str | None = None

    @property
    def succeeded(self) -> bool:
        """Whether the task completed without an exception."""
        return self.status == "succeeded"


@dataclass(frozen=True, slots=True)
class RunSummary:
    """Immutable aggregate outcome for a batch of tasks."""

    started_at: datetime
    finished_at: datetime
    outcomes: tuple[TaskResult, ...]

    def __post_init__(self) -> None:
        for value in (self.started_at, self.finished_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("Run summary timestamps must be timezone-aware.")
        if self.finished_at < self.started_at:
            raise ValueError("Run summary finish time precedes its start time.")

    @property
    def succeeded_count(self) -> int:
        """Number of successful tasks."""
        return sum(result.succeeded for result in self.outcomes)

    @property
    def failed_count(self) -> int:
        """Number of failed tasks."""
        return len(self.outcomes) - self.succeeded_count

    @property
    def exit_code(self) -> int:
        """Return a non-zero status if any task failed."""
        return int(self.failed_count > 0)

    @property
    def duration_seconds(self) -> float:
        """Return wall-clock duration for the task batch."""
        return (self.finished_at - self.started_at).total_seconds()

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible summary without arbitrary task values."""
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_seconds": self.duration_seconds,
            "task_count": len(self.outcomes),
            "succeeded_count": self.succeeded_count,
            "failed_count": self.failed_count,
            "exit_code": self.exit_code,
            "failures": [
                {
                    "task_key": result.task_key,
                    "error": result.error,
                    "traceback": result.traceback,
                }
                for result in self.outcomes
                if not result.succeeded
            ],
        }


def run_tasks(
    tasks: tuple[ProcessTask, ...],
    worker: TaskFunction,
    *,
    executor: ExecutorName,
    workers: int | None = None,
    estimated_task_memory_bytes: int | None = None,
    resources: ResourceSnapshot | None = None,
    log_handlers: tuple[logging.Handler, ...] | None = None,
) -> RunSummary:
    """Run tasks serially or in a spawn pool, continuing after task failures."""
    if executor not in {"serial", "processes"}:
        raise ValueError(f"Unsupported task executor: {executor!r}.")
    task_keys = [task.key for task in tasks]
    if len(set(task_keys)) != len(task_keys):
        raise ValueError("Task keys must be unique within one executor run.")
    started_at = datetime.now(UTC)
    if not tasks:
        return RunSummary(started_at, datetime.now(UTC), ())

    worker_count = (
        1
        if executor == "serial"
        else resolve_worker_count(
            len(tasks),
            workers,
            estimated_task_memory_bytes=estimated_task_memory_bytes,
            resources=resources,
        )
    )
    ordered_tasks = tuple(sorted(tasks, key=lambda task: (-len(task.times), task.key)))
    if executor == "serial":
        outcomes = tuple(_execute_task(worker, task) for task in ordered_tasks)
    else:
        outcomes = _run_process_tasks(
            ordered_tasks,
            worker,
            worker_count,
            log_handlers=log_handlers,
        )
    return RunSummary(
        started_at,
        datetime.now(UTC),
        tuple(
            sorted(
                outcomes,
                key=lambda outcome: outcome.task_key,
            )
        ),
    )


def write_run_summary_csv(
    path: str | Path,
    guard: OutputWriteGuard,
    summary: RunSummary,
) -> None:
    """Atomically persist the aggregate and per-task outcomes as CSV."""
    output = StringIO(newline="")
    writer = DictWriter(
        output,
        fieldnames=(
            "stage",
            "task_key",
            "status",
            "duration_seconds",
            "error",
            "traceback",
        ),
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerow(
        {
            "stage": "run",
            "status": "succeeded" if summary.exit_code == 0 else "failed",
            "duration_seconds": f"{summary.duration_seconds:.6f}",
        }
    )
    for result in summary.outcomes:
        writer.writerow(
            {
                "stage": "task",
                "task_key": result.task_key,
                "status": result.status,
                "duration_seconds": f"{result.duration_seconds:.6f}",
                "error": result.error or "",
                "traceback": result.traceback or "",
            }
        )
    atomic_write_text(path, output.getvalue(), guard)


def _run_process_tasks(
    tasks: tuple[ProcessTask, ...],
    worker: TaskFunction,
    worker_count: int,
    *,
    log_handlers: tuple[logging.Handler, ...] | None,
) -> tuple[TaskResult, ...]:
    context = multiprocessing.get_context("spawn")
    log_queue = context.Queue()
    handlers = log_handlers or (logging.StreamHandler(sys.stderr),)
    listener = QueueListener(log_queue, *handlers, respect_handler_level=True)
    outcomes: list[TaskResult] = []
    with worker_environment():
        listener.start()
        try:
            with ProcessPoolExecutor(
                max_workers=worker_count,
                mp_context=context,
                initializer=_initialize_process_worker,
                initargs=(log_queue, worker),
            ) as pool:
                futures = {
                    pool.submit(_execute_initialized_task, task): task for task in tasks
                }
                for future in as_completed(futures):
                    task = futures[future]
                    try:
                        outcomes.append(future.result())
                    except Exception as error:
                        outcomes.append(
                            TaskResult(
                                task_key=task.key,
                                status="failed",
                                duration_seconds=0.0,
                                error=f"{type(error).__name__}: {error}",
                                traceback=traceback.format_exc(),
                            )
                        )
        finally:
            listener.stop()
            log_queue.close()
            log_queue.join_thread()
    return tuple(outcomes)


def _initialize_process_worker(
    log_queue: Queue[logging.LogRecord],
    worker: TaskFunction,
) -> None:
    global _PROCESS_WORKER
    initialize_worker(log_queue)
    _PROCESS_WORKER = worker


def _execute_initialized_task(task: ProcessTask) -> TaskResult:
    worker = _PROCESS_WORKER
    if worker is None:
        raise RuntimeError("Process worker was not initialized.")
    return _execute_task(worker, task)


def _execute_task(worker: TaskFunction, task: ProcessTask) -> TaskResult:
    import time

    started = time.perf_counter()
    try:
        value = worker(task)
    except Exception as error:
        return TaskResult(
            task_key=task.key,
            status="failed",
            duration_seconds=time.perf_counter() - started,
            error=f"{type(error).__name__}: {error}",
            traceback=traceback.format_exc(),
        )
    return TaskResult(
        task_key=task.key,
        status="succeeded",
        duration_seconds=time.perf_counter() - started,
        value=value,
    )
