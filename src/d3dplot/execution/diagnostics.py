# SPDX-License-Identifier: GPL-3.0-or-later
"""Line-flushed, guarded run diagnostics."""

from __future__ import annotations

import json
import logging
import socket
import time
from collections.abc import Iterable, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from getpass import getuser
from pathlib import Path
from typing import Literal, TextIO

from rich.logging import RichHandler

from d3dplot.execution.cache import CacheDecision
from d3dplot.execution.runner import RunSummary, TaskResult
from d3dplot.simulation.time_axis import TimeEntry, TimeSelectionResult
from d3dplot.store import (
    OutputLayout,
    OutputWriteGuard,
    fingerprint_small_input,
    guarded_text_stream,
    initialize_output_root,
)
from d3dplot.validate.report import ValidationIssue


@dataclass(frozen=True, slots=True)
class DiagnosticHeader:
    """Run provenance written at the top of each diagnostic copy."""

    tool_version: str
    command_line: tuple[str, ...]
    started_at: datetime
    host: str
    user: str
    configuration_path: Path | None
    configuration_fingerprint: str | None
    resolved_configuration_path: Path | None = None

    def __post_init__(self) -> None:
        if not self.tool_version or not self.command_line:
            raise ValueError("Diagnostic header needs a version and command line.")
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise ValueError("Diagnostic start time must be timezone-aware.")
        if self.started_at.utcoffset() != timedelta(0):
            raise ValueError("Diagnostic start time must be UTC.")
        if (self.configuration_path is None) != (
            self.configuration_fingerprint is None
        ):
            raise ValueError(
                "Configuration path and fingerprint must either both be set or absent."
            )


@dataclass(frozen=True, slots=True)
class _DiagnosticTarget:
    path: Path
    guard: OutputWriteGuard


class DiagnosticLog:
    """Write identical user-facing diagnostics to guarded simulation roots."""

    def __init__(
        self,
        targets: Sequence[_DiagnosticTarget],
        header: DiagnosticHeader,
    ) -> None:
        if not targets:
            raise ValueError("At least one diagnostic output target is required.")
        target_paths = tuple(target.path.resolve(strict=False) for target in targets)
        if len(set(target_paths)) != len(target_paths):
            raise ValueError("Diagnostic target paths must be unique.")
        self._targets = tuple(targets)
        self.header = header
        self.paths = target_paths
        self.debug_path = targets[0].path.with_suffix(".log")
        self._stack: ExitStack | None = None
        self._streams: tuple[TextIO, ...] = ()
        self._debug_handler: logging.StreamHandler[TextIO] | None = None
        self._debug_logger = logging.Logger("d3dplot.run", level=logging.DEBUG)
        self._debug_logger.propagate = False
        self._started = 0.0
        self._active = False

    @classmethod
    def for_project(
        cls,
        layouts: Sequence[OutputLayout],
        *,
        tool_version: str,
        command_line: Sequence[str],
        forbidden_roots: Iterable[str | Path] = (),
        configuration_path: str | Path | None = None,
        resolved_configuration_path: str | Path | None = None,
        started_at: datetime | None = None,
    ) -> DiagnosticLog:
        """Create project-root logs after marking each permitted output root."""
        if not layouts:
            raise ValueError("A diagnostic run needs at least one output layout.")
        if started_at is not None and (
            started_at.tzinfo is None or started_at.utcoffset() is None
        ):
            raise ValueError("Diagnostic start time must be timezone-aware.")
        start = (started_at or datetime.now(UTC)).astimezone(UTC)
        config_path = (
            Path(configuration_path).expanduser().resolve(strict=True)
            if configuration_path is not None
            else None
        )
        config_fingerprint = (
            fingerprint_small_input(config_path) if config_path is not None else None
        )
        resolved_path = (
            Path(resolved_configuration_path).expanduser().resolve(strict=False)
            if resolved_configuration_path is not None
            else None
        )
        stamp = start.strftime("%Y%m%dT%H%M%S.%fZ")
        targets = tuple(
            _DiagnosticTarget(
                path=layout.logs / f"d3dplot_{stamp}.dia",
                guard=initialize_output_root(
                    layout,
                    tool_version=tool_version,
                    forbidden_roots=forbidden_roots,
                ),
            )
            for layout in layouts
        )
        header = DiagnosticHeader(
            tool_version=tool_version,
            command_line=tuple(command_line),
            started_at=start,
            host=socket.gethostname(),
            user=getuser(),
            configuration_path=config_path,
            configuration_fingerprint=config_fingerprint,
            resolved_configuration_path=resolved_path,
        )
        return cls(targets, header)

    def __enter__(self) -> DiagnosticLog:
        if self._active:
            raise RuntimeError("Diagnostic log is already open.")
        if any(
            path.exists() or path.is_symlink()
            for path in (*self.paths, self.debug_path)
        ):
            raise FileExistsError("A diagnostic file already exists for this run.")

        stack = ExitStack()
        try:
            streams = tuple(
                stack.enter_context(
                    guarded_text_stream(
                        target.path,
                        target.guard,
                        exclusive=True,
                    )
                )
                for target in self._targets
            )
            debug_stream = stack.enter_context(
                guarded_text_stream(
                    self.debug_path,
                    self._targets[0].guard,
                    exclusive=True,
                )
            )
        except Exception:
            stack.close()
            raise

        self._stack = stack
        self._streams = streams
        debug_handler = logging.StreamHandler(debug_stream)
        debug_handler.setLevel(logging.DEBUG)
        debug_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(processName)s %(name)s: %(message)s"
            )
        )
        self._debug_handler = debug_handler
        self._debug_logger.addHandler(debug_handler)
        self._started = time.perf_counter()
        self._active = True
        try:
            self._write_header()
        except Exception:
            self._close()
            raise
        return self

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> Literal[False]:
        try:
            elapsed = time.perf_counter() - self._started
            ended_at = datetime.now(UTC)
            if exc is None:
                self.info(
                    f"End time: {ended_at.isoformat()}; duration: "
                    f"{elapsed:.3f} s; outcome: success."
                )
                self.info("d3dplot finished")
            else:
                self.error(
                    f"End time: {ended_at.isoformat()}; duration: "
                    f"{elapsed:.3f} s; outcome: failed."
                )
                self.error(f"d3dplot stopped: {exc}")
        finally:
            self._close()
        return False

    def info(self, message: str) -> None:
        """Write an informational diagnostic to every project copy."""
        self._write("INFO", message)

    def warning(self, message: str) -> None:
        """Write a warning diagnostic to every project copy."""
        self._write("WARNING", message)

    def error(self, message: str) -> None:
        """Write an error diagnostic to every project copy."""
        self._write("ERROR", message)

    @property
    def queue_handlers(self) -> tuple[logging.Handler, ...]:
        """Return console and guarded file handlers for a worker log queue."""
        if not self._active or self._debug_handler is None:
            raise RuntimeError("Diagnostic log is not open.")
        return (
            RichHandler(show_path=False),
            self._debug_handler,
        )

    def record_validation_issues(
        self,
        issues: Iterable[ValidationIssue],
    ) -> None:
        """Record stable validation codes, locations, messages, and hints."""
        for issue in issues:
            location = f" at {issue.location}" if issue.location else ""
            hint = f" Hint: {issue.hint}" if issue.hint else ""
            message = (
                f"{issue.code}{location}: {issue.message}.{hint}"
                if not issue.message.endswith((".", "!", "?"))
                else f"{issue.code}{location}: {issue.message}{hint}"
            )
            if issue.level == "error":
                self.error(message)
            else:
                self.warning(message)

    def record_time_selection(
        self,
        *,
        analysis_key: str,
        simulation_label: str,
        requested: str,
        result: TimeSelectionResult,
    ) -> None:
        """Record requested outputs, selected entries, duplicates, and quality."""
        self.info(
            f"Time selection: analysis={analysis_key}; "
            f"simulation={simulation_label}; requested={requested}."
        )
        for entry in result.entries:
            self.info(
                f"Selected output: analysis={analysis_key}; "
                f"simulation={simulation_label}; {self._entry_text(entry)}."
            )
            if entry.corrupt_partitions:
                self.warning(
                    f"Corrupt partitions present: analysis={analysis_key}; "
                    f"simulation={simulation_label}; {self._entry_text(entry)}; "
                    f"partitions={list(entry.corrupt_partitions)}."
                )
        for duplicate in result.duplicates:
            candidates = "; ".join(
                self._entry_text(candidate) for candidate in duplicate.candidates
            )
            requested_time = (
                duplicate.requested_time.isoformat()
                if duplicate.requested_time is not None
                else "all"
            )
            self.info(
                f"Duplicate resolved: analysis={analysis_key}; "
                f"simulation={simulation_label}; requested_time={requested_time}; "
                f"candidates=[{candidates}]; chosen="
                f"{self._entry_text(duplicate.chosen)}."
            )
        self.record_validation_issues(result.issues)

    def record_cache_decision(
        self,
        *,
        task_key: str,
        output_path: str | Path,
        decision: CacheDecision,
    ) -> None:
        """Record whether one output will be reused or rebuilt."""
        self.info(
            f"Cache {decision.action}: task={task_key}; "
            f"reason={decision.reason}; output={output_path}."
        )
        if decision.warning is not None:
            self.warning(decision.warning)

    def record_task_result(self, result: TaskResult) -> None:
        """Record a task outcome and its duration."""
        message = (
            f"Task {result.status}: key={result.task_key}; "
            f"duration={result.duration_seconds:.3f} s."
        )
        if result.succeeded:
            self.info(message)
        else:
            self.error(f"{message} {result.error or 'Unknown task failure.'}")
            if result.traceback:
                self.error(f"Traceback for {result.task_key}: {result.traceback}")

    def record_run_summary(self, summary: RunSummary) -> None:
        """Record the final aggregate task counts and run duration."""
        self.info(
            f"Run summary: tasks={len(summary.outcomes)}; "
            f"succeeded={summary.succeeded_count}; failed={summary.failed_count}; "
            f"duration={summary.duration_seconds:.3f} s; "
            f"exit_code={summary.exit_code}."
        )

    def _write_header(self) -> None:
        self.info(f"d3dplot version: {self.header.tool_version}")
        self.info(
            "Command line: " + json.dumps(self.header.command_line, ensure_ascii=False)
        )
        self.info(f"Start time: {self.header.started_at.isoformat()}")
        self.info(f"Host: {self.header.host}")
        self.info(f"User: {self.header.user}")
        if self.header.configuration_path is not None:
            self.info(
                f"Configuration: {self.header.configuration_path}; "
                f"fingerprint={self.header.configuration_fingerprint}."
            )
        if self.header.resolved_configuration_path is not None:
            self.info(
                f"Resolved configuration: {self.header.resolved_configuration_path}"
            )

    def _write(self, level: str, message: str) -> None:
        if not self._active:
            raise RuntimeError("Diagnostic log is not open.")
        one_line = " ".join(message.splitlines())
        line = f"** {level:<7}: {one_line}\n"
        for stream in self._streams:
            stream.write(line)
            stream.flush()
        level_number = {
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
        }[level]
        self._debug_logger.log(level_number, one_line)

    @staticmethod
    def _entry_text(entry: TimeEntry) -> str:
        morph_time = (
            entry.morph_time.isoformat() if entry.morph_time is not None else "none"
        )
        return (
            f"time_index={entry.time_index}; subrun={entry.subrun}; "
            f"local_index={entry.local_index}; time={entry.time.isoformat()}; "
            f"morph_time={morph_time}"
        )

    def _close(self) -> None:
        self._active = False
        self._streams = ()
        if self._debug_handler is not None:
            self._debug_logger.removeHandler(self._debug_handler)
            self._debug_handler.flush()
            self._debug_handler.close()
            self._debug_handler = None
        if self._stack is not None:
            self._stack.close()
            self._stack = None
