# SPDX-License-Identifier: GPL-3.0-or-later
"""Static project-configuration validation checks."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import ValidationError

from d3dplot.analyses.profile.config import ProfileConfig
from d3dplot.analyses.profile.limits import limit_rows, paired_limits
from d3dplot.config.project import ProjectConfig
from d3dplot.store.guard import OutputWriteGuard
from d3dplot.store.layout import OUTPUT_MARKER_NAME, resolve_output_root
from d3dplot.validate.report import ValidationIssue, ValidationReport

_ANALYSIS_DISCRIMINATOR_VALUES = {"map2d", "river_km_statistics"}


def validate_config(data: object) -> tuple[ProjectConfig | None, ValidationReport]:
    """Validate raw configuration data and collect schema and static issues."""
    try:
        config = ProjectConfig.model_validate(data)
    except ValidationError as error:
        issues = tuple(
            ValidationIssue(
                code="V-CFG-001",
                level="error",
                location=_format_location(detail["loc"], data=data),
                message=detail["msg"],
                hint="Correct the value at this location or remove unsupported fields.",
            )
            for detail in error.errors()
        )
        return None, ValidationReport(issues)

    return config, validate_project_config(config)


def validate_project_config(config: ProjectConfig) -> ValidationReport:
    """Run static checks that require a structurally valid project config."""
    issues: list[ValidationIssue] = []
    _check_reference_simulation(config, issues)
    _check_simulation_labels_and_output_roots(config, issues)
    _check_axis_limit_rows(config, issues)
    _check_section_limit_rows(config, issues)
    _check_limit_coverage(config, issues)
    return ValidationReport(tuple(issues))


def _format_location(
    parts: tuple[str | int, ...],
    *,
    data: object = None,
) -> str:
    if not parts:
        return "$"

    if (
        isinstance(data, dict)
        and len(parts) == 2
        and parts[0] == "analyses"
        and isinstance(parts[1], str)
        and isinstance(data.get("analyses"), list)
    ):
        matching_indices = tuple(
            index
            for index, analysis in enumerate(data["analyses"])
            if isinstance(analysis, dict) and parts[1] in analysis
        )
        if len(matching_indices) == 1:
            parts = ("analyses", matching_indices[0], parts[1])

    location = ""
    for index, part in enumerate(parts):
        if (
            index >= 2
            and parts[index - 2] == "analyses"
            and isinstance(parts[index - 1], int)
            and isinstance(part, str)
            and part in _ANALYSIS_DISCRIMINATOR_VALUES
        ):
            continue
        if isinstance(part, int):
            location += f"[{part}]"
        else:
            if location:
                location += "."
            location += part
    return location


def _check_reference_simulation(
    config: ProjectConfig,
    issues: list[ValidationIssue],
) -> None:
    reference = config.reference_simulation
    if isinstance(reference, int):
        if 0 <= reference < len(config.simulations):
            return
        message = (
            f"Reference simulation index {reference} is outside the configured "
            f"range 0..{len(config.simulations) - 1}."
        )
    else:
        if any(simulation.label == reference for simulation in config.simulations):
            return
        message = f"Reference simulation label {reference!r} was not found."

    issues.append(
        ValidationIssue(
            code="V-CFG-002",
            level="error",
            location="reference_simulation",
            message=message,
            hint="Use a configured simulation label or a valid zero-based index.",
        )
    )


def _check_simulation_labels_and_output_roots(
    config: ProjectConfig,
    issues: list[ValidationIssue],
) -> None:
    seen_labels: dict[str, int] = {}
    seen_roots: dict[str, tuple[int, str]] = {}

    for index, simulation in enumerate(config.simulations):
        previous_label = seen_labels.get(simulation.label)
        if previous_label is not None:
            issues.append(
                ValidationIssue(
                    code="V-CFG-003",
                    level="error",
                    location=f"simulations[{index}].label",
                    message=(
                        f"Simulation label {simulation.label!r} duplicates "
                        f"simulations[{previous_label}].label."
                    ),
                    hint="Give every simulation a unique label.",
                )
            )
        else:
            seen_labels[simulation.label] = index

        try:
            output_root = resolve_output_root(
                simulation.path,
                simulation.label,
                project_output_root=config.output.root,
                simulation_output_dir=simulation.output_dir,
            )
        except (OSError, RuntimeError, ValueError) as error:
            _append_output_root_issue(
                config,
                index,
                f"Could not resolve the output root: {error}",
                issues,
            )
            continue
        output_key = os.path.normcase(str(output_root))
        previous_root = seen_roots.get(output_key)
        if previous_root is not None:
            previous_index, previous_name = previous_root
            issues.append(
                ValidationIssue(
                    code="V-CFG-003",
                    level="error",
                    location=f"simulations[{index}]",
                    message=(
                        f"Resolved output root {str(output_root)!r} is also used by "
                        f"simulations[{previous_index}] ({previous_name!r})."
                    ),
                    hint=(
                        "Assign a distinct simulations[*].output_dir to each "
                        "simulation, or omit output.root."
                    ),
                )
            )
        else:
            seen_roots[output_key] = (index, simulation.label)

        problem = _output_root_problem(config, output_root)
        if problem is not None:
            _append_output_root_issue(config, index, problem, issues)


def _output_root_problem(
    config: ProjectConfig,
    output_root: Path,
) -> str | None:
    try:
        guard = OutputWriteGuard(output_root, config.output.forbidden_roots)
        guard.validate_root_location()
    except RuntimeError as error:
        return str(error)

    if output_root.exists():
        if not output_root.is_dir():
            return f"Output root is not a directory: {output_root}"
        marker = output_root / OUTPUT_MARKER_NAME
        if marker.is_symlink():
            return f"Output marker cannot be a symlink: {marker}"
        if marker.exists() and not marker.is_file():
            return f"Output marker is not a regular file: {marker}"
        try:
            if not marker.exists() and any(output_root.iterdir()):
                return f"Refusing non-empty unmarked output root: {output_root}"
        except OSError as error:
            return f"Could not inspect output root {output_root}: {error}"
        writable_location = output_root
    else:
        writable_location = output_root.parent
        while not writable_location.exists():
            parent = writable_location.parent
            if parent == writable_location:
                break
            writable_location = parent
        if not writable_location.is_dir():
            return f"No existing directory can contain output root {output_root}."

    if not os.access(writable_location, os.W_OK):
        return f"Output root is not writable: {output_root}"
    return None


def _append_output_root_issue(
    config: ProjectConfig,
    index: int,
    message: str,
    issues: list[ValidationIssue],
) -> None:
    simulation = config.simulations[index]
    if simulation.output_dir is not None:
        location = f"simulations[{index}].output_dir"
    elif config.output.root is not None:
        location = "output.root"
    else:
        location = f"simulations[{index}].path"
    issues.append(
        ValidationIssue(
            code="V-OUT-041",
            level="error",
            location=location,
            message=message,
            hint=(
                "Choose a writable output location outside output.forbidden_roots "
                "and use a new or empty d3dplot folder."
            ),
        )
    )


def _check_axis_limit_rows(
    config: ProjectConfig,
    issues: list[ValidationIssue],
) -> None:
    for index, analysis in enumerate(config.analyses):
        figure = config.effective_figure(analysis)
        x_limits = figure.x_limits
        y_limits = figure.y_limits
        if (
            isinstance(x_limits, list)
            and isinstance(y_limits, list)
            and len(x_limits) != len(y_limits)
        ):
            issues.append(
                ValidationIssue(
                    code="V-CFG-004",
                    level="error",
                    location=f"analyses[{index}].figure",
                    message=(
                        f"x_limits has {len(x_limits)} row(s), but y_limits has "
                        f"{len(y_limits)} row(s)."
                    ),
                    hint=(
                        "Use the same number of rows for x_limits and y_limits, "
                        "or configure dictionary forms."
                    ),
                )
            )


def _check_section_limit_rows(
    config: ProjectConfig,
    issues: list[ValidationIssue],
) -> None:
    """Section figures pair y_limits and color_limits rows one figure each."""
    for index, analysis in enumerate(config.analyses):
        if not isinstance(analysis, ProfileConfig) or "section" not in analysis.views:
            continue
        figure = config.effective_figure(analysis)
        mismatches: dict[tuple[int, int], list[str]] = {}
        for variable in analysis.variables:
            for comparison in analysis.comparisons:
                y_rows = limit_rows(
                    figure.y_limits, variable, comparison, option="y_limits"
                )
                colour_rows = limit_rows(
                    figure.color_limits,
                    variable,
                    comparison,
                    option="color_limits",
                )
                try:
                    paired_limits(y_rows, colour_rows)
                except ValueError:
                    counts = (len(y_rows), len(colour_rows))
                    mismatches.setdefault(counts, []).append(
                        f"{variable} ({comparison})"
                    )
        for (y_count, colour_count), affected in mismatches.items():
            issues.append(
                ValidationIssue(
                    code="V-CFG-005",
                    level="error",
                    location=f"analyses[{index}].figure",
                    message=(
                        f"y_limits has {y_count} row(s) and color_limits has "
                        f"{colour_count} row(s) for {', '.join(affected)}; section "
                        "figures need the same number of rows, or one row."
                    ),
                    hint=(
                        "Use one row for every figure, or one row per figure in both "
                        "y_limits and color_limits."
                    ),
                )
            )


def _check_limit_coverage(
    config: ProjectConfig,
    issues: list[ValidationIssue],
) -> None:
    """Dictionary-form limits without an entry use automatic limits; say so."""
    for index, analysis in enumerate(config.analyses):
        if not isinstance(analysis, ProfileConfig):
            continue
        figure = config.effective_figure(analysis)
        options: list[tuple[str, object]] = []
        if {"profile", "section"} & set(analysis.views):
            options.append(("y_limits", figure.y_limits))
        if "section" in analysis.views:
            options.append(("color_limits", figure.color_limits))
        for option, configured in options:
            if not isinstance(configured, dict):
                continue
            missing: list[str] = []
            for variable in analysis.variables:
                variable_limits = configured.get(variable)
                if variable_limits is None:
                    missing.append(variable)
                    continue
                missing.extend(
                    f"{variable} ({comparison})"
                    for comparison in analysis.comparisons
                    if comparison not in variable_limits
                )
            if missing:
                issues.append(
                    ValidationIssue(
                        code="V-CFG-006",
                        level="warning",
                        location=f"analyses[{index}].figure.{option}",
                        message=(
                            f"{option} has no entry for {', '.join(missing)}; "
                            "automatic limits are used for them."
                        ),
                        hint=(
                            "Add the missing variable and comparison, or use a flat "
                            "list to apply the same rows to every variable."
                        ),
                    )
                )
