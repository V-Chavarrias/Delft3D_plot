# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the d3dplot command-line interface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from d3dplot.cli import app
from d3dplot.config import load_config

runner = CliRunner()


@pytest.mark.unit
def test_schema_command_prints_project_json_schema() -> None:
    result = runner.invoke(app, ["schema"])

    assert result.exit_code == 0
    schema = json.loads(result.stdout)
    assert "simulations" in schema["properties"]
    assert "analyses" in schema["properties"]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("relative_path", "kind"),
    [
        ("simulations/r001", "smt"),
        ("simulations/fm_partitioned", "fm"),
        ("simulations/fm_single", "fm"),
    ],
)
def test_info_json_reports_synthetic_simulations(
    synthetic_case: Path,
    relative_path: str,
    kind: str,
) -> None:
    result = runner.invoke(
        app,
        ["info", str(synthetic_case / relative_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)
    assert summary["kind"] == kind
    assert summary["state"] == "finished"
    assert summary["n_subruns"] >= 1
    assert summary["crs"] == "EPSG:0"
    assert "mesh2d_s1" in summary["raw_variables"]
    assert summary["subruns"][0]["partition_count"] >= 1
    assert summary["subruns"][0]["last_time"] is not None


@pytest.mark.integration
def test_info_text_reports_status_and_variables(synthetic_case: Path) -> None:
    result = runner.invoke(
        app,
        ["info", str(synthetic_case / "simulations" / "fm_single")],
    )

    assert result.exit_code == 0, result.output
    assert "Status: finished" in result.stdout
    assert "CRS: EPSG:0" in result.stdout
    assert "mesh2d_s1" in result.stdout


@pytest.mark.integration
def test_variables_lists_available_raw_and_derived_quantities(
    synthetic_case: Path,
) -> None:
    result = runner.invoke(
        app,
        ["variables", str(synthetic_case / "simulations" / "r001")],
    )

    assert result.exit_code == 0, result.output
    assert "bed_level" in result.stdout
    assert "mesh2d_flowelem_bl" in result.stdout
    assert "mesh2d_s1" in result.stdout
    assert "mesh2d_face_x" in result.stdout
    assert "froude_number" in result.stdout
    assert "Unregistered raw NetCDF variables:" in result.stdout


@pytest.mark.integration
def test_init_writes_valid_starter_and_refuses_overwrite(
    synthetic_case: Path,
    tmp_path: Path,
) -> None:
    simulation = synthetic_case / "simulations" / "r001"
    configuration = tmp_path / "project.yaml"
    result = runner.invoke(
        app,
        ["init", str(simulation), "--output", str(configuration)],
    )

    assert result.exit_code == 0, result.output
    original = configuration.read_text(encoding="utf-8")
    assert original.startswith("# d3dplot starter configuration.")
    assert load_config(configuration).simulations[0].path == simulation.resolve()

    validation = runner.invoke(
        app,
        ["validate", str(configuration), "--format", "json"],
    )
    assert validation.exit_code == 0, validation.output
    assert json.loads(validation.stdout) == {"valid": True, "issues": []}

    overwrite = runner.invoke(
        app,
        ["init", str(simulation), "--output", str(configuration)],
    )
    assert overwrite.exit_code != 0
    assert configuration.read_text(encoding="utf-8") == original


@pytest.mark.integration
def test_init_supports_river_km_statistics(
    synthetic_case: Path,
    tmp_path: Path,
) -> None:
    configuration = tmp_path / "project.yaml"
    result = runner.invoke(
        app,
        [
            "init",
            str(synthetic_case / "simulations" / "r001"),
            "--output",
            str(configuration),
            "--analyses",
            "map2d,river_km_statistics",
        ],
    )

    assert result.exit_code == 0, result.output
    config = load_config(configuration)
    assert [analysis.analysis for analysis in config.analyses] == [
        "map2d",
        "river_km_statistics",
    ]


@pytest.mark.integration
def test_validate_accepts_registered_and_present_raw_variables(
    synthetic_case: Path,
    tmp_path: Path,
) -> None:
    configuration = _write_config(
        tmp_path,
        synthetic_case / "simulations" / "r001",
        ["bed_level", "mesh2d_s1", "mesh2d_face_x"],
        output_root=tmp_path / "generated",
    )

    result = runner.invoke(
        app,
        ["validate", str(configuration), "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"valid": True, "issues": []}
    assert (
        tmp_path / "generated" / "reference" / "d3dplot" / "cache" / "merge_plan.nc"
    ).is_file()


@pytest.mark.integration
def test_inspect_builds_and_reuses_cache_for_selected_simulation(
    synthetic_case: Path,
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "generated"
    configuration = tmp_path / "project.yaml"
    configuration.write_text(
        yaml.safe_dump(
            {
                "simulations": [
                    {
                        "path": str(synthetic_case / "simulations" / "r001"),
                        "label": "smt",
                    },
                    {
                        "path": str(synthetic_case / "simulations" / "fm_single"),
                        "label": "plain",
                    },
                ],
                "analyses": [{"analysis": "map2d", "variables": ["bed_level"]}],
                "output": {"root": str(output_root)},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    first = runner.invoke(
        app,
        ["inspect", str(configuration), "--simulation", "smt"],
    )

    assert first.exit_code == 0, first.output
    assert "smt: 1 merge plan(s)" in first.stdout
    assert "cache built" in first.stdout
    assert (output_root / "smt" / "d3dplot" / "cache" / "merge_plan.nc").is_file()
    assert not (output_root / "plain" / "d3dplot").exists()

    second = runner.invoke(
        app,
        ["inspect", str(configuration), "--simulation", "smt"],
    )

    assert second.exit_code == 0, second.output
    assert "cache reused" in second.stdout


@pytest.mark.integration
def test_validate_reports_unknown_variable_suggestions(
    synthetic_case: Path,
    tmp_path: Path,
) -> None:
    configuration = _write_config(
        tmp_path,
        synthetic_case / "simulations" / "r001",
        ["bed_lvel"],
    )

    result = runner.invoke(
        app,
        ["validate", str(configuration), "--format", "json"],
    )

    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert report["valid"] is False
    assert report["issues"][0]["code"] == "V-VAR-001"
    assert "bed_level" in report["issues"][0]["message"]


@pytest.mark.integration
def test_validate_reports_registered_raw_variable_missing_from_map(
    synthetic_case: Path,
    tmp_path: Path,
) -> None:
    configuration = _write_config(
        tmp_path,
        synthetic_case / "simulations" / "r001",
        ["chezy"],
    )

    result = runner.invoke(
        app,
        ["validate", str(configuration), "--format", "json"],
    )

    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert [issue["code"] for issue in report["issues"]] == ["V-VAR-002"]


@pytest.mark.integration
def test_validate_rejects_line_quantity_without_sampling(
    synthetic_case: Path,
    tmp_path: Path,
) -> None:
    configuration = _write_config(
        tmp_path,
        synthetic_case / "simulations" / "r001",
        ["velocity_parallel"],
    )

    result = runner.invoke(
        app,
        ["validate", str(configuration), "--format", "json"],
    )

    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert [issue["code"] for issue in report["issues"]] == ["V-VAR-006"]


@pytest.mark.integration
def test_validate_reports_configuration_schema_errors_as_json(
    tmp_path: Path,
) -> None:
    configuration = tmp_path / "invalid.yaml"
    configuration.write_text("simulations: []\nanalyses: []\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["validate", str(configuration), "--format", "json"],
    )

    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert report["valid"] is False
    assert all(issue["code"] == "V-CFG-001" for issue in report["issues"])


def _write_config(
    tmp_path: Path,
    simulation: Path,
    variables: list[str],
    *,
    output_root: Path | None = None,
) -> Path:
    configuration = tmp_path / "project.yaml"
    data: dict[str, object] = {
        "simulations": [{"path": str(simulation), "label": "reference"}],
        "analyses": [{"analysis": "map2d", "variables": variables}],
    }
    if output_root is not None:
        data["output"] = {"root": str(output_root)}
    configuration.write_text(
        yaml.safe_dump(data, sort_keys=False),
        encoding="utf-8",
    )
    return configuration
