# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for process-task planning, execution, caching, and diagnostics."""

from __future__ import annotations

import logging
import os
from csv import DictReader
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path

import netCDF4
import pytest

from d3dplot.config.selections import TimesConfig
from d3dplot.execution import (
    AnalysisTimeSelection,
    CacheFingerprintReadError,
    DiagnosticLog,
    OutputCacheRequest,
    OutputCacheState,
    ProcessTask,
    ResourceSnapshot,
    RunSummary,
    TaskResult,
    decide_cache,
    plan_process_tasks,
    read_cache_fingerprints,
    read_output_cache_states,
    resolve_worker_count,
    run_process_stage,
    run_tasks,
    stable_shard_index,
    write_run_summary_csv,
)
from d3dplot.simulation.discover import discover_simulation
from d3dplot.simulation.inspection import inspect_simulation
from d3dplot.simulation.map_reader import MapReader
from d3dplot.simulation.time_axis import (
    DuplicateResolution,
    TimeAxis,
    TimeEntry,
    TimeSelectionResult,
)
from d3dplot.store import OutputLayout, initialize_output_root
from d3dplot.validate.report import ValidationIssue
from d3dplot.variables.context import QuantityRequest
from d3dplot.variables.registry import get_variable_spec

_BASE_TIME = datetime(2021, 2, 8, tzinfo=UTC)


def _entry(
    time_index: int,
    *,
    subrun: int = 0,
    corrupt_partitions: tuple[int, ...] = (),
) -> TimeEntry:
    time = _BASE_TIME + timedelta(seconds=time_index * 10)
    return TimeEntry(
        time_index=time_index,
        subrun=subrun,
        local_index=time_index,
        time=time,
        morph_time=time + timedelta(days=1),
        corrupt_partitions=corrupt_partitions,
    )


def _selection(
    analysis_key: str,
    entries: tuple[TimeEntry, ...],
    *quantities: str,
) -> AnalysisTimeSelection:
    return AnalysisTimeSelection(
        analysis_key=analysis_key,
        quantities=tuple(QuantityRequest(name) for name in quantities),
        selection=TimeSelectionResult(entries, (), ()),
    )


def _worker(task: ProcessTask) -> tuple[object, ...]:
    logging.getLogger(__name__).info("Executing %s", task.key)
    if task.subrun_index == 1:
        raise RuntimeError("synthetic task failure")
    return (
        tuple(time.entry.time_index for time in task.times),
        os.environ.get("OMP_NUM_THREADS"),
        os.environ.get("MPLBACKEND"),
    )


def _worker_with_context(
    task: ProcessTask,
    *,
    context_value: str,
) -> tuple[object, ...]:
    return (*_worker(task), context_value)


@pytest.mark.unit
def test_process_planner_merges_analyses_and_blocks_per_subrun() -> None:
    entries = (
        _entry(0),
        _entry(1),
        _entry(2),
        _entry(3, subrun=1),
        _entry(4, subrun=1),
    )
    selections = (
        _selection("map2d-0", (entries[0], entries[1], entries[3], entries[4]), "s1"),
        _selection(
            "river-km-0",
            (entries[0], entries[2], entries[4]),
            "bed_level",
            "froude_number",
        ),
    )

    tasks = plan_process_tasks(
        "r001",
        selections,
        workers=1,
        max_block_times=2,
    )

    assert [task.key for task in tasks] == [
        "process|r001|subrun=0|times=0,1",
        "process|r001|subrun=1|times=3,4",
        "process|r001|subrun=0|times=2",
    ]
    assert [len(task.times) for task in tasks] == [2, 2, 1]
    first_time = tasks[0].times[0]
    assert [item.analysis_key for item in first_time.analyses] == [
        "map2d-0",
        "river-km-0",
    ]
    assert [
        request.requested_name for request in first_time.analyses[1].quantities
    ] == [
        "bed_level",
        "froude_number",
    ]


@pytest.mark.unit
def test_process_planner_shards_deterministically_and_rejects_bad_inputs() -> None:
    selections = (
        _selection("map2d-0", tuple(_entry(index) for index in range(12)), "s1"),
    )
    all_tasks = plan_process_tasks(
        "r001",
        selections,
        workers=1,
        max_block_times=2,
    )
    shard_zero = plan_process_tasks(
        "r001",
        selections,
        workers=1,
        max_block_times=2,
        shard="0/2",
    )
    shard_one = plan_process_tasks(
        "r001",
        selections,
        workers=1,
        max_block_times=2,
        shard="1/2",
    )

    assert set(task.key for task in shard_zero).isdisjoint(
        task.key for task in shard_one
    )
    assert {task.key for task in (*shard_zero, *shard_one)} == {
        task.key for task in all_tasks
    }
    assert stable_shard_index("process|r001|subrun=3", 16) == 12
    with pytest.raises(ValueError, match="form index/count"):
        plan_process_tasks(
            "r001",
            selections,
            workers=1,
            max_block_times=2,
            shard="bad-shard",
        )
    with pytest.raises(ValueError, match="less than"):
        plan_process_tasks(
            "r001",
            selections,
            workers=1,
            max_block_times=2,
            shard="2/2",
        )
    with pytest.raises(ValueError, match="invalid time selection"):
        plan_process_tasks(
            "r001",
            (
                AnalysisTimeSelection(
                    "invalid",
                    (QuantityRequest("s1"),),
                    TimeSelectionResult(
                        (),
                        (),
                        (ValidationIssue("V-TIME-010", "error", "", "invalid"),),
                    ),
                ),
            ),
            workers=1,
            max_block_times=2,
        )


@pytest.mark.unit
def test_worker_count_respects_cpu_memory_and_task_limits() -> None:
    resources = ResourceSnapshot(
        physical_cores=8,
        available_memory_bytes=6_000,
    )

    assert resolve_worker_count(20, resources=resources) == 7
    assert (
        resolve_worker_count(
            20,
            configured_workers=5,
            estimated_task_memory_bytes=2_000,
            resources=resources,
        )
        == 3
    )
    assert resolve_worker_count(2, configured_workers=8, resources=resources) == 2
    assert resolve_worker_count(0, resources=resources) == 0


@pytest.mark.unit
def test_cache_decisions_follow_reprocess_policy() -> None:
    matching = decide_cache(
        exists=True,
        existing_fingerprint="same",
        expected_fingerprint="same",
        policy="if_changed",
    )
    stale = decide_cache(
        exists=True,
        existing_fingerprint="old",
        expected_fingerprint="new",
        policy="if_changed",
    )
    forced = decide_cache(
        exists=True,
        existing_fingerprint="same",
        expected_fingerprint="same",
        policy="always",
    )
    reused_stale = decide_cache(
        exists=True,
        existing_fingerprint="old",
        expected_fingerprint="new",
        policy="never",
    )
    missing = decide_cache(
        exists=False,
        existing_fingerprint=None,
        expected_fingerprint="new",
        policy="never",
    )

    assert (matching.action, matching.reason) == ("reuse", "match")
    assert (stale.action, stale.reason) == ("rebuild", "mismatch")
    assert (forced.action, forced.reason) == ("rebuild", "always")
    assert reused_stale.action == "reuse"
    assert reused_stale.warning is not None
    assert (missing.action, missing.reason) == ("rebuild", "missing")


@pytest.mark.unit
def test_cache_fingerprint_index_reads_each_path_and_reports_bad_files(
    tmp_path: Path,
) -> None:
    fingerprinted = tmp_path / "fingerprinted.nc"
    with netCDF4.Dataset(fingerprinted, mode="w") as dataset:
        dataset.setncattr("d3dplot_fingerprint", "abc123")
    without_fingerprint = tmp_path / "without-fingerprint.nc"
    with netCDF4.Dataset(without_fingerprint, mode="w"):
        pass
    missing = tmp_path / "missing.nc"

    values = read_cache_fingerprints(
        (fingerprinted, without_fingerprint, missing, fingerprinted)
    )

    assert values[fingerprinted.resolve()] == "abc123"
    assert values[without_fingerprint.resolve()] is None
    assert values[missing.resolve()] is None
    with pytest.raises(CacheFingerprintReadError, match="regular file"):
        read_cache_fingerprints((tmp_path,))


@pytest.mark.unit
def test_fingerprinted_cache_state_drives_task_resumability(
    tmp_path: Path,
) -> None:
    matching_output = tmp_path / "matching.nc"
    with netCDF4.Dataset(matching_output, mode="w") as dataset:
        dataset.setncattr("d3dplot_fingerprint", "current")
    stale_output = tmp_path / "stale.nc"
    with netCDF4.Dataset(stale_output, mode="w") as dataset:
        dataset.setncattr("d3dplot_fingerprint", "old")
    quantity = QuantityRequest("bed_level")
    requests = (
        OutputCacheRequest(
            time_index=0,
            request=quantity,
            output_path=matching_output,
            expected_fingerprint="current",
            policy="if_changed",
        ),
        OutputCacheRequest(
            time_index=1,
            request=quantity,
            output_path=stale_output,
            expected_fingerprint="new",
            policy="if_changed",
        ),
    )
    cache_states = read_output_cache_states(requests)
    entries = tuple(
        TimeEntry(
            time_index=index,
            subrun=0,
            local_index=index,
            time=_BASE_TIME + timedelta(days=index),
            morph_time=None,
            corrupt_partitions=(),
        )
        for index in range(2)
    )
    selection = AnalysisTimeSelection(
        analysis_key="map2d",
        quantities=(quantity,),
        selection=TimeSelectionResult(entries, (), ()),
        output_cache=cache_states,
    )

    tasks = plan_process_tasks(
        "synthetic",
        (selection,),
        workers=1,
        max_block_times=50,
    )

    assert cache_states[0].decision.reason == "match"
    assert cache_states[1].decision.reason == "mismatch"
    assert len(tasks) == 1
    assert tuple(time.entry.time_index for time in tasks[0].times) == (1,)


@pytest.mark.unit
def test_process_planner_schedules_only_missing_or_stale_outputs() -> None:
    bed_level = QuantityRequest("bed_level")
    water_level = QuantityRequest("water_level")
    entries = (_entry(0), _entry(1))
    cache_states = (
        OutputCacheState(
            time_index=0,
            request=bed_level,
            output_path="bed_level_0.nc",
            expected_fingerprint="bed-0",
            exists=True,
            existing_fingerprint="bed-0",
            policy="if_changed",
        ),
        OutputCacheState(
            time_index=0,
            request=water_level,
            output_path="water_level_0.nc",
            expected_fingerprint="water-0",
            exists=True,
            existing_fingerprint="stale",
            policy="if_changed",
        ),
        OutputCacheState(
            time_index=1,
            request=bed_level,
            output_path="bed_level_1.nc",
            expected_fingerprint="bed-1",
            exists=True,
            existing_fingerprint="bed-1",
            policy="if_changed",
        ),
        OutputCacheState(
            time_index=1,
            request=water_level,
            output_path="water_level_1.nc",
            expected_fingerprint="water-1",
            exists=True,
            existing_fingerprint="water-1",
            policy="if_changed",
        ),
    )
    selection = AnalysisTimeSelection(
        "map2d-0",
        (bed_level, water_level),
        TimeSelectionResult(entries, (), ()),
        cache_states,
    )

    tasks = plan_process_tasks(
        "r001",
        (selection,),
        workers=1,
        max_block_times=10,
    )

    assert len(tasks) == 1
    assert [time.entry.time_index for time in tasks[0].times] == [0]
    assert tasks[0].times[0].analyses[0].quantities == (water_level,)

    all_cached = AnalysisTimeSelection(
        "map2d-0",
        (bed_level, water_level),
        TimeSelectionResult(entries, (), ()),
        tuple(
            OutputCacheState(
                time_index=state.time_index,
                request=state.request,
                output_path=state.output_path,
                expected_fingerprint=state.expected_fingerprint,
                exists=True,
                existing_fingerprint=state.expected_fingerprint,
                policy="if_changed",
            )
            for state in cache_states
        ),
    )
    assert (
        plan_process_tasks(
            "r001",
            (all_cached,),
            workers=1,
            max_block_times=10,
        )
        == ()
    )


@pytest.mark.integration
def test_process_executor_matches_serial_and_continues_after_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tasks = plan_process_tasks(
        "r001",
        (
            _selection("map2d-0", (_entry(0),), "s1"),
            _selection("map2d-1", (_entry(1, subrun=1),), "s1"),
        ),
        workers=2,
        max_block_times=10,
    )
    monkeypatch.setenv("OMP_NUM_THREADS", "7")
    layout = OutputLayout(tmp_path / "output" / "d3dplot")
    guard = initialize_output_root(layout, tool_version="0.1.0")
    diagnostics = DiagnosticLog.for_project(
        (layout,),
        tool_version="0.1.0",
        command_line=("d3dplot", "run", "synthetic"),
    )
    worker = partial(_worker_with_context, context_value="worker context")
    with diagnostics as log:
        serial = run_tasks(tasks, worker, executor="serial")
        parallel = run_process_stage(
            tasks,
            worker,
            diagnostics=log,
            executor="processes",
            workers=2,
            summary_path=layout.tables / "run.csv",
            guard=guard,
        )

    assert serial.exit_code == parallel.exit_code == 1
    assert serial.succeeded_count == parallel.succeeded_count == 1
    assert serial.failed_count == parallel.failed_count == 1
    serial_success = next(result for result in serial.outcomes if result.succeeded)
    parallel_success = next(result for result in parallel.outcomes if result.succeeded)
    assert serial_success.value[0] == parallel_success.value[0]
    assert parallel_success.value[1:] == ("1", "Agg", "worker context")
    assert parallel_success.traceback is None
    failed = next(result for result in parallel.outcomes if not result.succeeded)
    assert "synthetic task failure" in (failed.traceback or "")
    assert os.environ["OMP_NUM_THREADS"] == "7"
    assert "Executing process|" in diagnostics.debug_path.read_text(encoding="utf-8")


@pytest.mark.unit
def test_process_stage_persists_diagnostics_and_run_summary(
    tmp_path: Path,
) -> None:
    layout = OutputLayout(tmp_path / "output" / "d3dplot")
    guard = initialize_output_root(layout, tool_version="0.1.0")
    diagnostics = DiagnosticLog.for_project(
        (layout,),
        tool_version="0.1.0",
        command_line=("d3dplot", "run", "synthetic"),
    )
    tasks = plan_process_tasks(
        "synthetic",
        (_selection("map2d", (_entry(0),), "bed_level"),),
        workers=1,
        max_block_times=50,
    )

    with diagnostics as log:
        summary = run_process_stage(
            tasks,
            _worker,
            executor="serial",
            diagnostics=log,
            summary_path=layout.tables / "run.csv",
            guard=guard,
        )

    assert summary.exit_code == 0
    diagnostic_text = diagnostics.paths[0].read_text(encoding="utf-8")
    assert "Task succeeded:" in diagnostic_text
    assert "Run summary: tasks=1; succeeded=1; failed=0;" in diagnostic_text
    assert "Run summary CSV:" in diagnostic_text
    with (layout.tables / "run.csv").open(
        encoding="utf-8",
        newline="",
    ) as stream:
        rows = list(DictReader(stream))
    assert rows[0]["stage"] == "run"
    assert rows[0]["status"] == "succeeded"
    assert rows[1]["task_key"] == tasks[0].key


@pytest.mark.unit
def test_run_summary_csv_is_written_through_the_guarded_atomic_store(
    tmp_path: Path,
) -> None:
    started_at = datetime(2026, 1, 2, tzinfo=UTC)
    summary = RunSummary(
        started_at,
        started_at + timedelta(seconds=3),
        (
            TaskResult("process|ok", "succeeded", 1.0),
            TaskResult(
                "process|failed",
                "failed",
                2.0,
                error="ValueError: failed",
                traceback="ValueError: failed",
            ),
        ),
    )
    root = tmp_path / "output"
    guard = initialize_output_root(root, tool_version="0.1.0")
    path = root / "tables" / "run.csv"

    write_run_summary_csv(path, guard, summary)

    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(DictReader(stream))
    assert rows[0]["stage"] == "run"
    assert rows[0]["status"] == "failed"
    assert rows[1]["task_key"] == "process|ok"
    assert rows[2]["error"] == "ValueError: failed"


@pytest.mark.integration
def test_diagnostic_log_copies_flushed_choices_and_summary_to_each_root(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "project.yaml"
    config_path.write_text("simulations: []\n", encoding="utf-8")
    start = datetime(2026, 1, 2, tzinfo=UTC)
    log = DiagnosticLog.for_project(
        (
            OutputLayout(tmp_path / "first" / "d3dplot"),
            OutputLayout(tmp_path / "second" / "d3dplot"),
        ),
        tool_version="0.1.0",
        command_line=("d3dplot", "run", str(config_path)),
        configuration_path=config_path,
        started_at=start,
    )
    first = _entry(0)
    chosen = _entry(1, corrupt_partitions=(7,))
    selection = TimeSelectionResult(
        (chosen,),
        (DuplicateResolution(None, (first, chosen), chosen),),
        (
            ValidationIssue(
                "V-OUT-030",
                "warning",
                "subrun=0",
                "One partition has invalid time metadata.",
                "Inspect the affected output.",
            ),
        ),
    )
    summary = RunSummary(
        start,
        start + timedelta(seconds=2),
        (TaskResult("process|r001|subrun=0", "succeeded", 1.5),),
    )

    with log as diagnostics:
        diagnostics.record_time_selection(
            analysis_key="map2d-0",
            simulation_label="r001",
            requested="all",
            result=selection,
        )
        diagnostics.info("line-flush-check")
        assert all(
            path.read_text(encoding="utf-8").endswith("line-flush-check\n")
            for path in diagnostics.paths
        )
        diagnostics.record_cache_decision(
            task_key="process|r001|subrun=0",
            output_path="cache/data/map2d/bed_level.nc",
            decision=decide_cache(
                exists=True,
                existing_fingerprint="old",
                expected_fingerprint="new",
                policy="never",
            ),
        )
        diagnostics.record_task_result(summary.outcomes[0])
        diagnostics.record_run_summary(summary)

    copies = tuple(path.read_text(encoding="utf-8") for path in log.paths)
    assert copies[0] == copies[1]
    assert "line-flush-check" in log.debug_path.read_text(encoding="utf-8")
    assert "fingerprint=" in copies[0]
    assert "Duplicate resolved:" in copies[0]
    assert "partitions=[7]" in copies[0]
    assert "V-OUT-030" in copies[0]
    assert "Cache reuse:" in copies[0]
    assert "Run summary: tasks=1; succeeded=1; failed=0" in copies[0]
    assert copies[0].splitlines()[-1] == "** INFO   : d3dplot finished"


@pytest.mark.integration
def test_diagnostic_log_records_failure_footer(
    tmp_path: Path,
) -> None:
    log = DiagnosticLog.for_project(
        (OutputLayout(tmp_path / "output" / "d3dplot"),),
        tool_version="0.1.0",
        command_line=("d3dplot", "run"),
    )

    with pytest.raises(RuntimeError, match="synthetic run failure"):
        with log as diagnostics:
            diagnostics.info("processing")
            raise RuntimeError("synthetic run failure")

    content = log.paths[0].read_text(encoding="utf-8")
    assert content.splitlines()[-1] == (
        "** ERROR  : d3dplot stopped: synthetic run failure"
    )


@pytest.mark.integration
def test_synthetic_run_diagnostics_record_duplicates_and_masked_partitions(
    synthetic_case: Path,
    tmp_path: Path,
) -> None:
    simulation, discovery_report = discover_simulation(
        synthetic_case / "simulations" / "r001"
    )
    assert simulation is not None, discovery_report.to_text()
    r001_layout = OutputLayout(tmp_path / "r001" / "d3dplot")
    inspection, inspection_report = inspect_simulation(simulation, r001_layout)
    assert inspection is not None, inspection_report.to_text()

    selection = TimeAxis.from_cache(inspection.times_path).select(
        TimesConfig(at="all", axis="morphological")
    )
    assert selection.is_valid
    assert selection.duplicates

    corrupt_simulation, corrupt_discovery_report = discover_simulation(
        synthetic_case / "simulations" / "variants" / "corrupt_partition"
    )
    assert corrupt_simulation is not None, corrupt_discovery_report.to_text()
    corrupt_layout = OutputLayout(tmp_path / "corrupt" / "d3dplot")
    corrupt_inspection, corrupt_inspection_report = inspect_simulation(
        corrupt_simulation,
        corrupt_layout,
    )
    assert corrupt_inspection is not None, corrupt_inspection_report.to_text()
    masked = MapReader(
        corrupt_simulation,
        corrupt_inspection.merge_plan_path,
    ).read(
        get_variable_spec("water_level"),
        subrun_index=0,
        local_index=1,
        corrupt_partitions=(1,),
    )
    assert "V-TIME-011" in {issue.code for issue in masked.issues}

    config_path = tmp_path / "project.yaml"
    config_path.write_text("synthetic: true\n", encoding="utf-8")
    diagnostics = DiagnosticLog.for_project(
        (r001_layout, corrupt_layout),
        tool_version="0.1.0",
        command_line=("d3dplot", "run", str(config_path)),
        configuration_path=config_path,
    )
    with diagnostics as log:
        log.record_time_selection(
            analysis_key="map2d-0",
            simulation_label="r001",
            requested="all on morphological time",
            result=selection,
        )
        log.record_validation_issues(masked.issues)

    copies = tuple(path.read_text(encoding="utf-8") for path in diagnostics.paths)
    assert copies[0] == copies[1]
    assert "Duplicate resolved:" in copies[0]
    assert "Masked" in copies[0]
    assert "corrupt partition 1" in copies[0]
