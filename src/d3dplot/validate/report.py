# SPDX-License-Identifier: GPL-3.0-or-later
"""Structured validation issues and reports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, TypeAlias

ValidationLevel: TypeAlias = Literal["error", "warning"]


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One stable, user-facing validation finding."""

    code: str
    level: ValidationLevel
    location: str
    message: str
    hint: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        """Return the stable JSON representation of this issue."""
        return {
            "code": self.code,
            "level": self.level,
            "location": self.location,
            "message": self.message,
            "hint": self.hint,
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Immutable collection of validation findings."""

    issues: tuple[ValidationIssue, ...] = ()

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        """Return the error-level findings."""
        return tuple(issue for issue in self.issues if issue.level == "error")

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        """Return the warning-level findings."""
        return tuple(issue for issue in self.issues if issue.level == "warning")

    @property
    def is_valid(self) -> bool:
        """Whether the report contains no errors."""
        return not self.errors

    def to_dict(self) -> dict[str, object]:
        """Return the stable JSON-compatible report representation."""
        return {
            "valid": self.is_valid,
            "issues": [issue.to_dict() for issue in self.issues],
        }

    def to_json(self) -> str:
        """Serialize the report as UTF-8-compatible JSON text."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n"

    def to_text(self) -> str:
        """Render the report as concise human-readable text."""
        if not self.issues:
            return "Validation passed: no issues.\n"

        status = "failed" if not self.is_valid else "completed with warnings"
        lines = [
            f"Validation {status}: {len(self.errors)} error(s), "
            f"{len(self.warnings)} warning(s)."
        ]
        for issue in self.issues:
            location = f" at {issue.location}" if issue.location else ""
            lines.append(
                f"{issue.level.upper()} {issue.code}{location}: {issue.message}"
            )
            if issue.hint is not None:
                lines.append(f"  Hint: {issue.hint}")
        return "\n".join(lines) + "\n"
