# SPDX-License-Identifier: GPL-3.0-or-later
"""Task planning and execution primitives."""

from d3dplot.execution.cache import (
    CacheDecision,
    CacheFingerprintReadError,
    decide_cache,
    read_cache_fingerprints,
)
from d3dplot.execution.diagnostics import DiagnosticHeader, DiagnosticLog
from d3dplot.execution.orchestrator import run_process_stage
from d3dplot.execution.resources import ResourceSnapshot, resolve_worker_count
from d3dplot.execution.runner import (
    RunSummary,
    TaskDiagnostic,
    TaskOutputReport,
    TaskResult,
    run_tasks,
    write_run_summary_csv,
)
from d3dplot.execution.tasks import (
    AnalysisTimeSelection,
    AnalysisTimeWork,
    OutputCacheRequest,
    OutputCacheState,
    ProcessTask,
    ProcessTime,
    plan_process_tasks,
    read_output_cache_states,
    stable_shard_index,
)

__all__ = [
    "AnalysisTimeSelection",
    "AnalysisTimeWork",
    "CacheDecision",
    "CacheFingerprintReadError",
    "DiagnosticHeader",
    "DiagnosticLog",
    "OutputCacheRequest",
    "OutputCacheState",
    "ProcessTask",
    "ProcessTime",
    "ResourceSnapshot",
    "RunSummary",
    "TaskDiagnostic",
    "TaskOutputReport",
    "TaskResult",
    "decide_cache",
    "plan_process_tasks",
    "read_output_cache_states",
    "read_cache_fingerprints",
    "resolve_worker_count",
    "run_process_stage",
    "run_tasks",
    "stable_shard_index",
    "write_run_summary_csv",
]
