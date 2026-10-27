# SPDX-License-Identifier: GPL-3.0-or-later
"""Connect task execution outcomes to run diagnostics and persisted summaries."""

from __future__ import annotations

import logging
from pathlib import Path

from d3dplot.execution.diagnostics import DiagnosticLog
from d3dplot.execution.resources import ResourceSnapshot
from d3dplot.execution.runner import (
    ExecutorName,
    RunSummary,
    TaskFunction,
    TaskOutputReport,
    run_tasks,
    write_run_summary_csv,
)
from d3dplot.execution.tasks import ProcessTask
from d3dplot.store import OutputWriteGuard


def run_process_stage(
    tasks: tuple[ProcessTask, ...],
    worker: TaskFunction,
    *,
    executor: ExecutorName,
    diagnostics: DiagnosticLog,
    summary_path: str | Path,
    guard: OutputWriteGuard,
    workers: int | None = None,
    estimated_task_memory_bytes: int | None = None,
    resources: ResourceSnapshot | None = None,
    log_handlers: tuple[logging.Handler, ...] | None = None,
) -> RunSummary:
    """Run planned work and persist its outcomes to the run's logs and tables."""
    if log_handlers is None and executor == "processes":
        log_handlers = diagnostics.queue_handlers
    summary = run_tasks(
        tasks,
        worker,
        executor=executor,
        workers=workers,
        estimated_task_memory_bytes=estimated_task_memory_bytes,
        resources=resources,
        log_handlers=log_handlers,
    )
    for result in summary.outcomes:
        diagnostics.record_task_result(result)
        if isinstance(result.value, TaskOutputReport):
            for message in result.value.diagnostics:
                if message.level == "error":
                    diagnostics.error(message.message)
                elif message.level == "warning":
                    diagnostics.warning(message.message)
                else:
                    diagnostics.info(message.message)
    diagnostics.record_run_summary(summary)
    write_run_summary_csv(summary_path, guard, summary)
    diagnostics.info(f"Run summary CSV: {summary_path}")
    return summary
