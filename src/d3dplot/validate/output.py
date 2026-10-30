# SPDX-License-Identifier: GPL-3.0-or-later
"""Persist validation and inspection reports in the guarded output store."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypeAlias

from d3dplot.store import OutputLayout, OutputWriteGuard, atomic_write_text
from d3dplot.validate.report import ValidationReport

ValidationReportFormat: TypeAlias = Literal["text", "json"]


def write_validation_report(
    report: ValidationReport,
    layout: OutputLayout,
    guard: OutputWriteGuard,
    *,
    report_format: ValidationReportFormat = "text",
    prefix: Literal["validation", "inspection"] = "validation",
    timestamp: datetime | None = None,
) -> Path:
    """Write one timestamped report beneath the guarded tables directory."""
    if report_format not in {"text", "json"}:
        raise ValueError(f"Unsupported validation report format: {report_format!r}.")

    moment = datetime.now(UTC) if timestamp is None else timestamp
    if moment.tzinfo is None:
        raise ValueError("Validation report timestamp must be timezone-aware.")
    moment = moment.astimezone(UTC)
    suffix = "json" if report_format == "json" else "txt"
    path = layout.tables / (f"{prefix}_{moment.strftime('%Y%m%dT%H%M%S.%fZ')}.{suffix}")
    rendered = report.to_json() if report_format == "json" else report.to_text()
    atomic_write_text(path, rendered, guard)
    return path
