# SPDX-License-Identifier: GPL-3.0-or-later
"""Model-input validation checks."""

from __future__ import annotations

from pathlib import Path

from d3dplot.model_input.errors import ModelInputError
from d3dplot.model_input.reader import ModelInput, read_model_input
from d3dplot.validate.report import ValidationIssue, ValidationReport


def validate_model_input(
    path: Path,
) -> tuple[ModelInput | None, ValidationReport]:
    """Read model input and report parsing failures as V-MDU-001."""
    try:
        model_input = read_model_input(path)
    except ModelInputError as error:
        return None, ValidationReport(
            (
                ValidationIssue(
                    code="V-MDU-001",
                    level="error",
                    location=str(error.path),
                    message=error.message,
                    hint=error.hint,
                ),
            )
        )
    return model_input, ValidationReport()
