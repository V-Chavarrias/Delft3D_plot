# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for validation reports and static configuration checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from d3dplot.config import ProjectConfig
from d3dplot.validate import (
    ValidationIssue,
    ValidationReport,
    validate_config,
    validate_project_config,
)


def _minimal_data() -> dict[str, Any]:
    return {
        "simulations": [{"path": "model", "label": "reference"}],
        "analyses": [{"analysis": "map2d", "variables": ["bed_level"]}],
    }


@pytest.mark.unit
def test_report_serialization_and_text_summary() -> None:
    issue = ValidationIssue(
        code="V-CFG-002",
        level="error",
        location="reference_simulation",
        message="Reference simulation label 'missing' was not found.",
        hint="Use a configured label.",
    )
    report = ValidationReport((issue,))

    assert not report.is_valid
    assert report.errors == (issue,)
    assert report.warnings == ()
    assert report.to_dict() == {
        "valid": False,
        "issues": [
            {
                "code": "V-CFG-002",
                "level": "error",
                "location": "reference_simulation",
                "message": "Reference simulation label 'missing' was not found.",
                "hint": "Use a configured label.",
            }
        ],
    }
    assert json.loads(report.to_json()) == report.to_dict()
    assert report.to_text() == (
        "Validation failed: 1 error(s), 0 warning(s).\n"
        "ERROR V-CFG-002 at reference_simulation: "
        "Reference simulation label 'missing' was not found.\n"
        "  Hint: Use a configured label.\n"
    )

    empty_report = ValidationReport()
    assert empty_report.is_valid
    assert empty_report.to_text() == "Validation passed: no issues.\n"


@pytest.mark.unit
def test_v_cfg_001_collects_pydantic_errors() -> None:
    data = _minimal_data()
    data["unexpected"] = True
    data["language"] = "fr"
    data["analyses"][0]["unsupported"] = "value"

    config, report = validate_config(data)

    assert config is None
    assert not report.is_valid
    assert len(report.issues) == 3
    assert {issue.code for issue in report.issues} == {"V-CFG-001"}
    assert {issue.location for issue in report.issues} == {
        "unexpected",
        "language",
        "analyses[0].unsupported",
    }


@pytest.mark.unit
@pytest.mark.parametrize("reference", ["missing", -1, 1])
def test_v_cfg_002_requires_a_valid_reference_simulation(
    reference: str | int,
) -> None:
    data = _minimal_data()
    data["reference_simulation"] = reference

    config, report = validate_config(data)

    assert config is not None
    assert [issue.code for issue in report.issues] == ["V-CFG-002"]
    assert report.issues[0].location == "reference_simulation"


@pytest.mark.unit
def test_v_cfg_003_rejects_duplicate_labels_and_output_roots() -> None:
    data = _minimal_data()
    data["simulations"] = [
        {"path": "model-a", "label": "duplicate", "output_dir": "shared"},
        {"path": "model-b", "label": "duplicate", "output_dir": "shared"},
    ]

    config, report = validate_config(data)

    assert config is not None
    assert [issue.code for issue in report.issues] == [
        "V-CFG-003",
        "V-CFG-003",
    ]
    assert report.issues[0].location == "simulations[1].label"
    assert report.issues[1].location == "simulations[1]"


@pytest.mark.unit
def test_v_cfg_003_uses_the_project_output_root_and_simulation_label() -> None:
    data = _minimal_data()
    data["simulations"] = [
        {"path": "model-a", "label": "a"},
        {"path": "model-b", "label": "b"},
    ]
    data["output"] = {"root": "shared-output"}

    config, report = validate_config(data)

    assert config is not None
    assert report.is_valid


@pytest.mark.unit
def test_v_cfg_004_checks_effective_list_axis_limit_row_counts() -> None:
    data = _minimal_data()
    data["figure"] = {
        "x_limits": [[0, 1], [1, 2]],
        "y_limits": [[0, 1]],
    }

    config, report = validate_config(data)

    assert config is not None
    assert [issue.code for issue in report.issues] == ["V-CFG-004"]
    assert report.issues[0].location == "analyses[0].figure"

    data["analyses"][0]["figure"] = {"y_limits": [[0, 1], [1, 2]]}
    inherited_config, inherited_report = validate_config(data)

    assert inherited_config is not None
    assert inherited_report.is_valid


@pytest.mark.unit
def test_v_cfg_004_does_not_compare_dictionary_axis_limits() -> None:
    data = _minimal_data()
    data["figure"] = {
        "x_limits": [[0, 1], [1, 2]],
        "y_limits": {"bed_level": {"value": [[0, 1]]}},
    }

    config, report = validate_config(data)

    assert config is not None
    assert report.is_valid


@pytest.mark.unit
def test_validate_project_config_accepts_valid_model() -> None:
    config = ProjectConfig.model_validate(_minimal_data())

    report = validate_project_config(config)

    assert report.is_valid
    assert report.issues == ()


@pytest.mark.unit
def test_v_out_041_rejects_output_roots_inside_forbidden_roots(
    tmp_path: Path,
) -> None:
    protected_root = tmp_path / "protected"
    data = _minimal_data()
    data["simulations"][0]["path"] = str(protected_root / "model")
    data["output"] = {"forbidden_roots": [str(protected_root)]}

    config, report = validate_config(data)

    assert config is not None
    assert [issue.code for issue in report.errors] == ["V-OUT-041"]
    assert report.errors[0].location == "simulations[0].path"
    assert "inside forbidden root" in report.errors[0].message


@pytest.mark.unit
def test_v_out_041_rejects_output_root_that_is_not_writable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import d3dplot.validate.checks.config as config_checks

    monkeypatch.setattr(config_checks.os, "access", lambda _path, _mode: False)
    data = _minimal_data()
    data["output"] = {"root": str(tmp_path / "shared")}

    config, report = validate_config(data)

    assert config is not None
    assert [issue.code for issue in report.errors] == ["V-OUT-041"]
    assert "not writable" in report.errors[0].message
