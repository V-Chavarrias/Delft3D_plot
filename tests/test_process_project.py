# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the temporary project-processing helper."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from tools import process_project

from d3dplot.config.io import load_config
from d3dplot.store import OutputLayout
from d3dplot.validate.report import ValidationIssue, ValidationReport


@pytest.mark.integration
def test_inspection_failure_writes_diagnostic_and_debug_logs(
    synthetic_case: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration_path = tmp_path / "project.yml"
    configuration_path.write_text(
        yaml.safe_dump(
            {
                "simulations": [
                    {
                        "path": str(synthetic_case / "simulations" / "r001"),
                        "label": "model",
                    },
                ],
                "output": {"root": str(tmp_path / "output")},
                "analyses": [
                    {"analysis": "map2d", "variables": ["bed_level"]},
                ],
            },
        ),
        encoding="utf-8",
    )
    project = load_config(configuration_path)
    layout = OutputLayout.for_simulation(project.simulations[0], project.output)
    inspection_report = ValidationReport(
        (
            ValidationIssue(
                "V-OUT-002",
                "error",
                "synthetic output",
                "Morphological time has unsupported units",
                "Check the output metadata.",
            ),
        ),
    )
    monkeypatch.setattr(
        process_project,
        "inspect_simulation",
        lambda *args, **kwargs: (None, inspection_report),
    )

    with pytest.raises(ValueError, match="V-OUT-002"):
        process_project._process_project(configuration_path, serial=True)

    diagnostic_paths = tuple(layout.logs.glob("*.dia"))
    assert len(diagnostic_paths) == 1
    diagnostic_text = diagnostic_paths[0].read_text(encoding="utf-8")
    assert "V-OUT-002" in diagnostic_text
    assert "d3dplot stopped" in diagnostic_text
    assert diagnostic_paths[0].with_suffix(".log").is_file()
