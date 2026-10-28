# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration validation, guarded cache inspection, and setup commands."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Annotated, Literal, Protocol, cast

import typer
import yaml

from d3dplot import __version__
from d3dplot.analyses.map2d.plot import Map2DPlotError
from d3dplot.analyses.river_km_statistics.plot import (
    RiverKmStatisticsPlotError,
    plot_river_km_statistics_stage,
)
from d3dplot.analyses.river_km_statistics.measurements import (
    ProfileMeasurementError,
    convert_mat_profile_measurements,
)
from d3dplot.config.io import (
    apply_overrides,
    dump_config,
    dump_json_schema,
    dump_resolved_config,
    load_config_data,
    write_resolved_config,
)
from d3dplot.config.project import ProjectConfig, SimulationConfig
from d3dplot.execution.diagnostics import DiagnosticLog
from d3dplot.plotting.stage import (
    PlotStageResult,
    ReplotTarget,
    plot_map2d_stage,
    read_replot_metadata,
)
from d3dplot.simulation.discover import (
    Simulation,
    discover_simulation,
)
from d3dplot.simulation.inspection import (
    InspectionResult,
)
from d3dplot.simulation.inspection import (
    inspect_simulation as build_inspection_cache,
)
from d3dplot.simulation.partitions import _NETCDF4_READ_LOCK
from d3dplot.simulation.status import (
    SimulationStatus,
    SimulationStatusError,
    detect_simulation_status,
    inspect_simulation_status,
)
from d3dplot.store import (
    OutputLayout,
    OutputWriteError,
    atomic_write_text,
    initialize_output_root,
)
from d3dplot.validate import (
    ValidationIssue,
    ValidationReport,
    validate_config,
)
from d3dplot.variables import (
    UnknownVariableError,
    VariableKind,
    VariableLocation,
    VariableSpec,
    get_variable_spec,
    list_variable_specs,
)

_ANALYSIS_NAMES = frozenset({"map2d", "river_km_statistics"})
_LINE_QUANTITY_NAMES = frozenset(
    {
        "velocity_parallel",
        "velocity_perpendicular",
        "discharge",
        "vpara",
        "vperp",
        "q",
    }
)


class _NetCDFVariable(Protocol):
    def ncattrs(self) -> list[str]: ...

    def getncattr(self, name: str) -> object: ...


class _NetCDFDataset(Protocol):
    @property
    def variables(self) -> Mapping[str, _NetCDFVariable]: ...

    def ncattrs(self) -> list[str]: ...

    def getncattr(self, name: str) -> object: ...


class _NetCDF4Module(Protocol):
    def Dataset(
        self,
        path: Path,
        *,
        mode: Literal["r"],
    ) -> AbstractContextManager[_NetCDFDataset]: ...


@dataclass(frozen=True, slots=True)
class _SubRunHeader:
    index: int
    map_file: Path
    variable_names: tuple[str, ...]
    crs: str | None


@dataclass(frozen=True, slots=True)
class _RawVariableAvailability:
    name: str
    aliases: tuple[str, ...]
    candidates: tuple[str, ...]
    available_subruns: int
    total_subruns: int

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "aliases": list(self.aliases),
            "netcdf_candidates": list(self.candidates),
            "available_subruns": self.available_subruns,
            "total_subruns": self.total_subruns,
        }


app = typer.Typer(
    add_completion=False,
    help="Inspect simulations and validate d3dplot project configurations.",
    no_args_is_help=True,
)
measurements_app = typer.Typer(
    help="Convert and inspect measurement inputs.",
    no_args_is_help=True,
)
app.add_typer(measurements_app, name="measurements")


@app.command()
def info(
    simulation_path: Annotated[
        Path,
        typer.Argument(help="Simulation folder to inspect."),
    ],
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Print the summary as JSON.",
        ),
    ] = False,
) -> None:
    """Show simulation status, output metadata, variables, and CRS."""
    root = simulation_path.expanduser().resolve(strict=False)
    try:
        simulation, report = discover_simulation(root)
        if simulation is None:
            status, status_report = inspect_simulation_status(root)
            report = status_report
        else:
            status = detect_simulation_status(simulation)
    except SimulationStatusError as error:
        typer.echo(f"Could not inspect simulation status: {error}", err=True)
        raise typer.Exit(code=1) from error

    if status is None:
        _emit_report(report)
        raise typer.Exit(code=1)

    headers: tuple[_SubRunHeader, ...] = ()
    header_report = ValidationReport()
    if simulation is not None:
        headers, header_report = _read_subrun_headers(simulation)
    issues = report.issues + header_report.issues

    if json_output:
        payload = _build_info_payload(status, simulation, headers, issues)
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", nl=False)
    else:
        _emit_info_text(status, simulation, headers, issues)

    if not header_report.is_valid:
        raise typer.Exit(code=1)


@app.command()
def variables(
    simulation_path: Annotated[
        Path,
        typer.Argument(help="Simulation folder to inspect."),
    ],
) -> None:
    """List raw NetCDF fields and registered quantities available to analyses."""
    root = simulation_path.expanduser().resolve(strict=False)
    simulation, report = discover_simulation(root)
    if simulation is None:
        _emit_report(report)
        raise typer.Exit(code=1)

    headers, header_report = _read_subrun_headers(simulation)
    if report.issues or header_report.issues:
        _emit_report(ValidationReport(report.issues + header_report.issues), err=True)
    if not header_report.is_valid:
        raise typer.Exit(code=1)

    raw_names = _raw_variable_names(headers)
    registered = _available_raw_variables(headers)
    unregistered = _unregistered_raw_variables(raw_names)

    typer.echo(f"Simulation: {simulation.root}")
    typer.echo(f"Kind: {simulation.kind.value}")
    typer.echo(f"Sub-runs: {len(simulation.subruns)}")
    typer.echo("")
    typer.echo("Registered raw quantities available in map headers:")
    for item in registered:
        candidates = ", ".join(item.candidates)
        typer.echo(
            f"  {item.name} ({candidates}; "
            f"{item.available_subruns}/{item.total_subruns} sub-runs)"
        )
        if item.aliases:
            typer.echo(f"    aliases: {', '.join(item.aliases)}")

    typer.echo("")
    typer.echo("Unregistered raw NetCDF variables:")
    if unregistered:
        for name in unregistered:
            typer.echo(f"  {name}")
    else:
        typer.echo("  (none)")

    typer.echo("")
    typer.echo("Registered derived quantities:")
    for spec in list_variable_specs(VariableKind.DERIVED):
        alias_text = f"; aliases: {', '.join(spec.aliases)}" if spec.aliases else ""
        input_text = f"; inputs: {', '.join(spec.inputs)}" if spec.inputs else ""
        typer.echo(f"  {spec.name}{alias_text}{input_text}")


@app.command()
def schema() -> None:
    """Print the JSON Schema for project configuration."""
    typer.echo(dump_json_schema(), nl=False)


@app.command()
def validate(
    configuration: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="YAML or JSON project configuration.",
        ),
    ],
    report_format: Annotated[
        Literal["text", "json"],
        typer.Option("--format", help="Validation report format."),
    ] = "text",
) -> None:
    """Run configuration, variable, discovery, and output-inspection checks."""
    try:
        data = load_config_data(configuration)
    except (OSError, ValueError, yaml.YAMLError) as error:
        typer.echo(f"Could not read configuration {configuration}: {error}", err=True)
        raise typer.Exit(code=2) from error

    config, static_report = validate_config(data)
    if config is None:
        _emit_report(static_report, report_format)
        raise typer.Exit(code=1)

    diagnostics = _make_diagnostic_log(
        config,
        config.simulations,
        configuration=configuration,
        command_line=(
            "d3dplot",
            "validate",
            str(configuration),
            "--format",
            report_format,
        ),
        output_is_valid=not any(
            issue.code == "V-OUT-041" for issue in static_report.issues
        ),
    )
    if diagnostics is None:
        report = _validate_project(config, static_report)
        _emit_report(report, report_format)
        if not report.is_valid:
            raise typer.Exit(code=1)
        return

    with diagnostics as diagnostic_log:
        report = _validate_project(config, static_report, diagnostic_log)
        _emit_report(report, report_format)
        if not report.is_valid:
            diagnostic_log.error("Validation failed.")
            raise typer.Exit(code=1)


def _validate_project(
    config: ProjectConfig,
    static_report: ValidationReport,
    diagnostics: DiagnosticLog | None = None,
) -> ValidationReport:
    issues = list(static_report.issues)
    discovered: list[tuple[str, tuple[_SubRunHeader, ...]]] = []
    inspection_candidates: list[tuple[SimulationConfig, Simulation]] = []

    if diagnostics is not None:
        diagnostics.record_validation_issues(static_report.issues)

    for simulation_config in config.simulations:
        simulation, discovery_report = discover_simulation(simulation_config.path)
        issues.extend(discovery_report.issues)
        if diagnostics is not None:
            diagnostics.info(
                f"Simulation discovery: label={simulation_config.label}; "
                f"path={simulation_config.path}."
            )
            diagnostics.record_validation_issues(discovery_report.issues)
        if simulation is None:
            continue
        inspection_candidates.append((simulation_config, simulation))
        if diagnostics is not None:
            map_file_count = sum(len(subrun.map_files) for subrun in simulation.subruns)
            diagnostics.info(
                f"Simulation found: label={simulation_config.label}; "
                f"kind={simulation.kind.value}; "
                f"subruns={len(simulation.subruns)}; map_files={map_file_count}."
            )
        headers, header_report = _read_subrun_headers(simulation)
        issues.extend(header_report.issues)
        if diagnostics is not None:
            diagnostics.record_validation_issues(header_report.issues)
        if header_report.is_valid:
            discovered.append((simulation_config.label, headers))

    previous_issue_count = len(issues)
    _validate_variable_requests(config, discovered, issues)
    if diagnostics is not None:
        diagnostics.record_validation_issues(issues[previous_issue_count:])
    if not any(issue.level == "error" for issue in issues):
        for simulation_config, simulation in inspection_candidates:
            result, inspection_report = build_inspection_cache(
                simulation,
                OutputLayout.for_simulation(simulation_config, config.output),
                crs=config.crs,
                time_zone=config.time_zone,
                check_boundary_duplicates=config.check_boundary_duplicates,
                forbidden_roots=config.output.forbidden_roots,
            )
            issues.extend(inspection_report.issues)
            if diagnostics is not None:
                diagnostics.record_validation_issues(inspection_report.issues)
                if result is not None:
                    cache_state = "reused" if result.cache_reused else "built"
                    diagnostics.info(
                        f"Inspection cache {cache_state}: "
                        f"simulation={simulation_config.label}; "
                        f"merge_plans={result.plan_count}; "
                        f"duration={result.duration_seconds:.3f} s."
                    )
    elif diagnostics is not None:
        diagnostics.info(
            "Inspection cache work skipped because validation errors were found."
        )

    report = ValidationReport(tuple(issues))
    if diagnostics is not None:
        diagnostics.info(
            f"Validation summary: valid={report.is_valid}; "
            f"errors={len(report.errors)}; warnings={len(report.warnings)}."
        )
    return report


@app.command()
def inspect(
    configuration: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="YAML or JSON project configuration.",
        ),
    ],
    simulation_label: Annotated[
        str | None,
        typer.Option(
            "--simulation",
            help="Inspect only the configured simulation with this label.",
        ),
    ] = None,
) -> None:
    """Build or refresh grid and merge-plan caches for configured simulations."""
    try:
        data = load_config_data(configuration)
    except (OSError, ValueError, yaml.YAMLError) as error:
        typer.echo(f"Could not read configuration {configuration}: {error}", err=True)
        raise typer.Exit(code=2) from error

    config, static_report = validate_config(data)
    if config is None or not static_report.is_valid:
        _emit_report(static_report)
        raise typer.Exit(code=1)

    simulation_configs = config.simulations
    if simulation_label is not None:
        simulation_configs = [
            item for item in config.simulations if item.label == simulation_label
        ]
        if not simulation_configs:
            typer.echo(
                f"Configured simulation label not found: {simulation_label}",
                err=True,
            )
            raise typer.Exit(code=2)

    diagnostics = _make_diagnostic_log(
        config,
        simulation_configs,
        configuration=configuration,
        command_line=(
            ("d3dplot", "inspect", str(configuration))
            if simulation_label is None
            else (
                "d3dplot",
                "inspect",
                str(configuration),
                "--simulation",
                simulation_label,
            )
        ),
        output_is_valid=True,
    )
    if diagnostics is None:
        report, results = _inspect_project(config, static_report, simulation_configs)
        _emit_inspection_report(report, results)
        if not report.is_valid:
            raise typer.Exit(code=1)
        return

    with diagnostics as diagnostic_log:
        report, results = _inspect_project(
            config,
            static_report,
            simulation_configs,
            diagnostic_log,
        )
        _emit_inspection_report(report, results)
        if not report.is_valid:
            diagnostic_log.error("Inspection failed.")
            raise typer.Exit(code=1)


def _inspect_project(
    config: ProjectConfig,
    static_report: ValidationReport,
    simulation_configs: list[SimulationConfig],
    diagnostics: DiagnosticLog | None = None,
) -> tuple[ValidationReport, list[tuple[str, InspectionResult]]]:
    issues = list(static_report.issues)
    results: list[tuple[str, InspectionResult]] = []
    if diagnostics is not None:
        diagnostics.record_validation_issues(static_report.issues)
    for simulation_config in simulation_configs:
        simulation, discovery_report = discover_simulation(simulation_config.path)
        issues.extend(discovery_report.issues)
        if diagnostics is not None:
            diagnostics.info(
                f"Simulation discovery: label={simulation_config.label}; "
                f"path={simulation_config.path}."
            )
            diagnostics.record_validation_issues(discovery_report.issues)
        if simulation is None:
            continue
        if diagnostics is not None:
            map_file_count = sum(len(subrun.map_files) for subrun in simulation.subruns)
            diagnostics.info(
                f"Simulation found: label={simulation_config.label}; "
                f"kind={simulation.kind.value}; "
                f"subruns={len(simulation.subruns)}; map_files={map_file_count}."
            )
        result, inspection_report = build_inspection_cache(
            simulation,
            OutputLayout.for_simulation(simulation_config, config.output),
            crs=config.crs,
            time_zone=config.time_zone,
            check_boundary_duplicates=config.check_boundary_duplicates,
            forbidden_roots=config.output.forbidden_roots,
        )
        issues.extend(inspection_report.issues)
        if diagnostics is not None:
            diagnostics.record_validation_issues(inspection_report.issues)
        if result is not None:
            results.append((simulation_config.label, result))
            if diagnostics is not None:
                cache_state = "reused" if result.cache_reused else "built"
                diagnostics.info(
                    f"Inspection cache {cache_state}: "
                    f"simulation={simulation_config.label}; "
                    f"merge_plans={result.plan_count}; "
                    f"duration={result.duration_seconds:.3f} s."
                )

    report = ValidationReport(tuple(issues))
    if diagnostics is not None:
        diagnostics.info(
            f"Inspection summary: valid={report.is_valid}; "
            f"simulations={len(results)}; errors={len(report.errors)}; "
            f"warnings={len(report.warnings)}."
        )
    return report, results


def _emit_inspection_report(
    report: ValidationReport,
    results: list[tuple[str, InspectionResult]],
) -> None:
    _emit_report(report)
    for label, result in results:
        cache_state = "reused" if result.cache_reused else "built"
        typer.echo(
            f"{label}: {result.plan_count} merge plan(s), reference sub-run "
            f"{result.reference_subrun}, cache {cache_state} "
            f"({result.duration_seconds:.2f} s): {result.merge_plan_path}"
        )


def _make_diagnostic_log(
    config: ProjectConfig,
    involved_simulations: list[SimulationConfig],
    *,
    configuration: Path,
    command_line: tuple[str, ...],
    output_is_valid: bool,
) -> DiagnosticLog | None:
    """Create logs in the reference root and each simulation used by a command."""
    if not output_is_valid:
        return None

    reference = config.reference_simulation
    if isinstance(reference, str):
        reference_simulation = next(
            (item for item in config.simulations if item.label == reference),
            None,
        )
    else:
        reference_simulation = (
            config.simulations[reference]
            if 0 <= reference < len(config.simulations)
            else None
        )
    if reference_simulation is None:
        raise ValueError("The configured reference simulation could not be resolved.")

    ordered_simulations = (reference_simulation, *involved_simulations)
    layouts: list[OutputLayout] = []
    seen_roots: set[Path] = set()
    for simulation in ordered_simulations:
        if (
            simulation.output_dir is None
            and config.output.root is None
            and not simulation.path.is_dir()
        ):
            return None
        layout = OutputLayout.for_simulation(simulation, config.output)
        if layout.root not in seen_roots:
            layouts.append(layout)
            seen_roots.add(layout.root)
    return DiagnosticLog.for_project(
        tuple(layouts),
        tool_version=__version__,
        command_line=command_line,
        forbidden_roots=config.output.forbidden_roots,
        configuration_path=configuration,
    )


@app.command()
def plot(
    configuration: Annotated[
        Path,
        typer.Argument(
            help="Resolved YAML configuration or a d3dplot output directory.",
        ),
    ],
    set_values: Annotated[
        list[str] | None,
        typer.Option(
            "--set",
            help="Override a configuration value (repeat for multiple overrides).",
        ),
    ] = None,
    simulation_label: Annotated[
        str | None,
        typer.Option("--simulation", help="Plot only this configured simulation."),
    ] = None,
    variable: Annotated[
        str | None,
        typer.Option("--variable", help="Plot only this configured variable."),
    ] = None,
    analysis_name: Annotated[
        str | None,
        typer.Option("--only", help="Plot only this configured analysis."),
    ] = None,
    fresh_figures: Annotated[
        bool,
        typer.Option(
            "--fresh-figures",
            help="Rebuild figures instead of reusing matching PNGs.",
        ),
    ] = False,
) -> None:
    """Render figures from persisted analysis caches."""
    if set_values is None:
        set_values = []
    try:
        configuration_path = _resolve_plot_configuration(configuration)
        config, report = validate_config(load_config_data(configuration_path))
        if config is None or not report.is_valid:
            _emit_report(report)
            raise typer.Exit(code=1)
        if set_values:
            config = apply_overrides(config, set_values)
            config, report = validate_config(config.model_dump(mode="json"))
            if config is None or not report.is_valid:
                _emit_report(report)
                raise typer.Exit(code=1)
        involved = (
            list(config.simulations)
            if simulation_label is None
            else [item for item in config.simulations if item.label == simulation_label]
        )
        if simulation_label is not None and not involved:
            raise ValueError(
                f"Configured simulation label not found: {simulation_label}"
            )
        command_line_parts = ["d3dplot", "plot", str(configuration_path)]
        for override in set_values:
            command_line_parts.extend(("--set", override))
        if simulation_label is not None:
            command_line_parts.extend(("--simulation", simulation_label))
        if variable is not None:
            command_line_parts.extend(("--variable", variable))
        if analysis_name is not None:
            command_line_parts.extend(("--only", analysis_name))
        if fresh_figures:
            command_line_parts.append("--fresh-figures")
        command_line = tuple(command_line_parts)
        diagnostics = _make_diagnostic_log(
            config,
            involved,
            configuration=configuration_path,
            command_line=command_line,
            output_is_valid=True,
        )
        result = _run_plot_stage(
            config,
            diagnostics=diagnostics,
            simulation_label=simulation_label,
            variable=variable,
            analysis_name=analysis_name,
            fresh_figures=fresh_figures,
        )
    except typer.Exit:
        raise
    except (
        Map2DPlotError,
        RiverKmStatisticsPlotError,
        OSError,
        OutputWriteError,
        ValueError,
        yaml.YAMLError,
    ) as error:
        typer.echo(f"Plot failed: {_format_cli_error(error)}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo(
        f"Plot complete: {len(result.output_paths)} figure(s) written, "
        f"{len(result.reused_paths)} reused, "
        f"{len(result.movie_paths)} movie(s) written, "
        f"{len(result.reused_movie_paths)} reused."
    )


@app.command()
def replot(
    figure: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="PNG figure produced by d3dplot.",
        ),
    ],
    set_values: Annotated[
        list[str] | None,
        typer.Option(
            "--set",
            help="Override a configuration value (repeat for multiple overrides).",
        ),
    ] = None,
) -> None:
    """Regenerate one figure using its embedded configuration provenance."""
    if set_values is None:
        set_values = []
    try:
        figure_path = figure.expanduser().resolve(strict=True)
        configuration_path, target = read_replot_metadata(figure_path)
        config, report = validate_config(load_config_data(configuration_path))
        if config is None or not report.is_valid:
            _emit_report(report)
            raise typer.Exit(code=1)
        if set_values:
            config = apply_overrides(config, set_values)
            config, report = validate_config(config.model_dump(mode="json"))
            if config is None or not report.is_valid:
                _emit_report(report)
                raise typer.Exit(code=1)
        if target.simulation_label not in {item.label for item in config.simulations}:
            raise ValueError(
                "Figure metadata simulation is not present in the resolved config."
            )
        involved = [
            item for item in config.simulations if item.label == target.simulation_label
        ]
        command_line = (
            "d3dplot",
            "replot",
            str(figure_path),
            *(argument for override in set_values for argument in ("--set", override)),
        )
        diagnostics = _make_diagnostic_log(
            config,
            involved,
            configuration=configuration_path,
            command_line=command_line,
            output_is_valid=True,
        )
        _run_plot_stage(
            config,
            diagnostics=diagnostics,
            target=target,
            target_path=figure_path,
            fresh_figures=True,
        )
    except typer.Exit:
        raise
    except (
        Map2DPlotError,
        OSError,
        OutputWriteError,
        ValueError,
        yaml.YAMLError,
    ) as error:
        typer.echo(f"Replot failed: {_format_cli_error(error)}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo(f"Replotted {figure_path}.")


def _resolve_plot_configuration(configuration: Path) -> Path:
    path = configuration.expanduser().resolve(strict=True)
    if path.is_dir():
        candidates: tuple[Path, ...] = (
            path / "config" / "latest.yaml",
            path / "latest.yaml",
        )
        if path.name.casefold() == "config":
            candidates = (path / "latest.yaml",)
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve(strict=True)
        raise ValueError(
            f"No resolved config/latest.yaml found in output directory: {path}"
        )
    if not path.is_file():
        raise ValueError(f"Configuration is not a regular file: {path}")
    return path


def _run_plot_stage(
    config: ProjectConfig,
    *,
    diagnostics: DiagnosticLog | None,
    simulation_label: str | None = None,
    variable: str | None = None,
    analysis_name: str | None = None,
    fresh_figures: bool = False,
    target: ReplotTarget | None = None,
    target_path: Path | None = None,
) -> PlotStageResult:
    def execute(diagnostic_log: DiagnosticLog | None) -> PlotStageResult:
        try:
            replayable_config = _canonical_resolved_config(config)
            provenance_path = _write_resolved_provenance(replayable_config)
            if diagnostic_log is not None:
                diagnostic_log.info(f"Resolved configuration saved: {provenance_path}.")
            map_result = plot_map2d_stage(
                replayable_config,
                provenance_path=provenance_path,
                analysis_name=analysis_name,
                simulation_label=simulation_label,
                variable=variable,
                fresh_figures=fresh_figures,
                target=target,
                target_path=target_path,
                diagnostic=(
                    diagnostic_log.info if diagnostic_log is not None else None
                ),
            )
            river_km_result = (
                plot_river_km_statistics_stage(
                    replayable_config,
                    analysis_name=analysis_name,
                    simulation_label=simulation_label,
                    variable=variable,
                    fresh_figures=fresh_figures,
                    provenance_path=provenance_path,
                    diagnostic=(
                        diagnostic_log.info if diagnostic_log is not None else None
                    ),
                )
                if target is None
                else None
            )
            result = PlotStageResult(
                output_paths=(
                    map_result.output_paths
                    + (() if river_km_result is None else river_km_result.output_paths)
                ),
                reused_paths=(
                    map_result.reused_paths
                    + (() if river_km_result is None else river_km_result.reused_paths)
                ),
                movie_paths=map_result.movie_paths,
                reused_movie_paths=map_result.reused_movie_paths,
                messages=(
                    map_result.messages
                    + (() if river_km_result is None else river_km_result.messages)
                ),
            )
            if diagnostic_log is not None:
                diagnostic_log.info(
                    "Plot summary: "
                    f"written={len(result.output_paths)}; "
                    f"reused={len(result.reused_paths)}; "
                    f"movies_written={len(result.movie_paths)}; "
                    f"movies_reused={len(result.reused_movie_paths)}."
                )
            return result
        except (
            Map2DPlotError,
            RiverKmStatisticsPlotError,
            OSError,
            OutputWriteError,
            ValueError,
        ) as error:
            if diagnostic_log is not None:
                diagnostic_log.error(f"Plot failed: {_format_cli_error(error)}")
            raise

    if diagnostics is None:
        return execute(None)
    with diagnostics as diagnostic_log:
        return execute(diagnostic_log)


def _canonical_resolved_config(config: ProjectConfig) -> ProjectConfig:
    """Use the exact defaulted, absolute-path configuration that replay will load."""
    data = yaml.safe_load(
        dump_resolved_config(
            config,
            __version__,
            base_dir=Path.cwd(),
        )
    )
    if not isinstance(data, dict):
        raise ValueError("Resolved configuration must contain a mapping.")
    return ProjectConfig.model_validate(data)


def _format_cli_error(error: Exception) -> str:
    if isinstance(error, (Map2DPlotError, RiverKmStatisticsPlotError)):
        return f"{error.code}: {error}"
    return str(error)


def _write_resolved_provenance(config: ProjectConfig) -> Path:
    reference = _reference_simulation_config(config)
    layout = OutputLayout.for_simulation(reference, config.output)
    guard = initialize_output_root(
        layout,
        tool_version=__version__,
        forbidden_roots=config.output.forbidden_roots,
    )
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run_path = layout.config / f"run_{timestamp}.yaml"
    latest_path = layout.config / "latest.yaml"

    def writer(path: Path, content: str) -> None:
        atomic_write_text(path, content, guard)

    write_resolved_config(
        config,
        run_path,
        writer,
        __version__,
        base_dir=Path.cwd(),
    )
    write_resolved_config(
        config,
        latest_path,
        writer,
        __version__,
        base_dir=Path.cwd(),
    )
    return run_path.resolve(strict=True)


def _reference_simulation_config(config: ProjectConfig) -> SimulationConfig:
    reference = config.reference_simulation
    if isinstance(reference, str):
        for simulation in config.simulations:
            if simulation.label == reference:
                return simulation
        raise ValueError(f"Configured reference simulation not found: {reference}")
    try:
        return config.simulations[reference]
    except IndexError as error:
        raise ValueError(
            f"Configured reference simulation index is out of range: {reference}"
        ) from error


@app.command()
def init(
    simulation_paths: Annotated[
        list[Path],
        typer.Argument(
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            help="One or more simulation folders.",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option(
            "-o",
            "--output",
            help="New YAML configuration path; existing files are never replaced.",
        ),
    ],
    analyses: Annotated[
        str,
        typer.Option(
            "--analyses",
            help="Comma-separated built-in analyses: map2d, river_km_statistics.",
        ),
    ] = "map2d",
) -> None:
    """Create a commented starter YAML configuration without overwriting files."""
    try:
        analysis_names = _parse_analysis_names(analyses)
        config = _starter_config(simulation_paths, analysis_names)
        content = (
            "# d3dplot starter configuration. Edit paths and variables before use.\n"
            "# River-km analysis paths are placeholders and must be replaced.\n\n"
            + dump_config(config)
        )
        _write_new_text(output, content)
    except (OSError, ValueError) as error:
        typer.echo(f"Could not create starter configuration: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"Created starter configuration: {output.expanduser().resolve()}")


@measurements_app.command("convert")
def measurements_convert(
    source: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Matlab v5/v7 profile-measurement input file.",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option(
            "-o",
            "--output",
            help=(
                "Canonical NetCDF output path beneath a new, empty, or marked "
                "directory. Existing files are never replaced."
            ),
        ),
    ],
    time_zone: Annotated[
        str,
        typer.Option(
            "--time-zone",
            help="Fixed UTC offset for the naive Matlab datenums, e.g. +01:00.",
        ),
    ],
    variable: Annotated[
        str | None,
        typer.Option(
            "--variable",
            help="Convert one variable; by default one file is written per variable.",
        ),
    ] = None,
) -> None:
    """Convert Matlab profile measurements to guarded canonical NetCDF."""
    output_path = output.expanduser().resolve(strict=False)
    try:
        layout = OutputLayout(output_path.parent)
        guard = initialize_output_root(layout, tool_version=__version__)
        output_paths = convert_mat_profile_measurements(
            source,
            output_path,
            time_zone=time_zone,
            guard=guard,
            variable=variable,
        )
    except (OSError, OutputWriteError, ProfileMeasurementError, ValueError) as error:
        code = f"{error.code}: " if isinstance(error, ProfileMeasurementError) else ""
        typer.echo(f"Could not convert profile measurements: {code}{error}", err=True)
        raise typer.Exit(code=1) from error
    for path in output_paths:
        typer.echo(f"Converted profile measurements: {path}")


def main() -> None:
    """Run the Typer application."""
    app()


def _netcdf4_module() -> _NetCDF4Module:
    os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")
    return cast(_NetCDF4Module, import_module("netCDF4"))


def _read_subrun_headers(
    simulation: Simulation,
) -> tuple[tuple[_SubRunHeader, ...], ValidationReport]:
    netcdf4 = _netcdf4_module()
    headers: list[_SubRunHeader] = []
    issues: list[ValidationIssue] = []
    for subrun in simulation.subruns:
        map_file = subrun.map_files[0]
        try:
            with (
                _NETCDF4_READ_LOCK,
                netcdf4.Dataset(
                    map_file,
                    mode="r",
                ) as dataset,
            ):
                headers.append(
                    _SubRunHeader(
                        index=subrun.index,
                        map_file=map_file,
                        variable_names=tuple(
                            sorted(
                                dataset.variables,
                                key=lambda value: (value.casefold(), value),
                            )
                        ),
                        crs=_read_crs(dataset),
                    )
                )
        except (
            EOFError,
            IndexError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as error:
            issues.append(
                ValidationIssue(
                    code="V-OUT-002",
                    level="error",
                    location=str(map_file),
                    message=f"Map-file header could not be read: {error}",
                    hint="Restore or regenerate this D-Flow FM map output.",
                )
            )
    return tuple(headers), ValidationReport(tuple(issues))


def _read_crs(dataset: _NetCDFDataset) -> str | None:
    coordinate_system = dataset.variables.get("projected_coordinate_system")
    if coordinate_system is not None:
        value = _first_crs_attribute(
            coordinate_system,
            ("EPSG_code", "epsg_code", "spatial_ref", "crs_wkt", "name"),
        )
        if value is not None:
            return value
    return _first_crs_attribute(
        dataset,
        ("crs", "spatial_ref", "crs_wkt", "EPSG_code", "epsg_code"),
    )


def _first_crs_attribute(
    target: _NetCDFDataset | _NetCDFVariable,
    names: tuple[str, ...],
) -> str | None:
    available = {name.casefold(): name for name in target.ncattrs()}
    for requested_name in names:
        actual_name = available.get(requested_name.casefold())
        if actual_name is None:
            continue
        value = target.getncattr(actual_name)
        text = (
            value.decode("utf-8", errors="replace")
            if isinstance(value, bytes)
            else str(value)
        )
        text = text.strip()
        if not text:
            continue
        if requested_name.casefold() == "epsg_code" and not text.casefold().startswith(
            "epsg:"
        ):
            return f"EPSG:{text}"
        return text
    return None


def _raw_variable_names(headers: tuple[_SubRunHeader, ...]) -> tuple[str, ...]:
    names = {name for header in headers for name in header.variable_names}
    return tuple(sorted(names, key=lambda value: (value.casefold(), value)))


def _available_raw_variables(
    headers: tuple[_SubRunHeader, ...],
) -> tuple[_RawVariableAvailability, ...]:
    available: list[_RawVariableAvailability] = []
    for spec in list_variable_specs(VariableKind.RAW):
        candidates = tuple(
            candidate
            for candidate in spec.netcdf_candidates
            if any(
                _contains_name(header.variable_names, candidate) for header in headers
            )
        )
        count = sum(
            any(
                _contains_name(header.variable_names, candidate)
                for candidate in spec.netcdf_candidates
            )
            for header in headers
        )
        if count:
            available.append(
                _RawVariableAvailability(
                    name=spec.name,
                    aliases=spec.aliases,
                    candidates=candidates,
                    available_subruns=count,
                    total_subruns=len(headers),
                )
            )
    return tuple(available)


def _unregistered_raw_variables(raw_names: tuple[str, ...]) -> tuple[str, ...]:
    registered_names = {
        candidate.casefold()
        for spec in list_variable_specs(VariableKind.RAW)
        for candidate in spec.netcdf_candidates
    }
    return tuple(name for name in raw_names if name.casefold() not in registered_names)


def _contains_name(names: tuple[str, ...], requested: str) -> bool:
    normalized = requested.strip().casefold()
    return any(name.casefold() == normalized for name in names)


def _summarize_crs(headers: tuple[_SubRunHeader, ...]) -> str | None:
    values = {header.crs for header in headers if header.crs is not None}
    if not values:
        return None
    if len(values) == 1:
        return next(iter(values))
    return "mixed"


def _build_info_payload(
    status: SimulationStatus,
    simulation: Simulation | None,
    headers: tuple[_SubRunHeader, ...],
    issues: tuple[ValidationIssue, ...],
) -> dict[str, object]:
    data = status.to_dict()
    subrun_data = data["subruns"]
    subruns_by_index = (
        {subrun.index: subrun for subrun in simulation.subruns}
        if simulation is not None
        else {}
    )
    headers_by_index = {header.index: header for header in headers}
    if isinstance(subrun_data, list):
        for record in subrun_data:
            if not isinstance(record, dict):
                continue
            index = record.get("index")
            if not isinstance(index, int):
                continue
            subrun = subruns_by_index.get(index)
            header = headers_by_index.get(index)
            record["partition_count"] = subrun.partition_count if subrun else 0
            record["partitioned"] = subrun.partitioned if subrun else False
            record["crs"] = header.crs if header else None
            record["map_header_file"] = str(header.map_file) if header else None

    raw_names = _raw_variable_names(headers)
    data.update(
        {
            "n_subruns": len(status.subruns),
            "total_partitions": sum(
                subrun.partition_count for subrun in simulation.subruns
            )
            if simulation is not None
            else 0,
            "crs": _summarize_crs(headers),
            "raw_variables": list(raw_names),
            "registered_raw_variables": [
                item.to_dict() for item in _available_raw_variables(headers)
            ],
            "unregistered_raw_variables": list(_unregistered_raw_variables(raw_names)),
            "issues": [issue.to_dict() for issue in issues],
        }
    )
    return data


def _emit_info_text(
    status: SimulationStatus,
    simulation: Simulation | None,
    headers: tuple[_SubRunHeader, ...],
    issues: tuple[ValidationIssue, ...],
) -> None:
    subruns_by_index = (
        {subrun.index: subrun for subrun in simulation.subruns}
        if simulation is not None
        else {}
    )
    headers_by_index = {header.index: header for header in headers}

    typer.echo(f"Simulation: {status.label}")
    typer.echo(f"Path: {status.path}")
    typer.echo(f"Kind: {status.kind.value}")
    typer.echo(f"Status: {status.state.value}")
    typer.echo(f"Sub-runs: {len(status.subruns)}")
    typer.echo(f"CRS: {_summarize_crs(headers) or 'not recorded'}")
    typer.echo("Sub-run details:")
    for subrun_status in status.subruns:
        subrun = subruns_by_index.get(subrun_status.index)
        header = headers_by_index.get(subrun_status.index)
        partition_count = subrun.partition_count if subrun is not None else 0
        partitioned = subrun.partitioned if subrun is not None else False
        partition_text = (
            f"{partition_count} partition(s)"
            if partitioned
            else f"{partition_count} map file(s)"
        )
        details = [
            f"state={subrun_status.state.value}",
            partition_text,
        ]
        if subrun_status.last_time is not None:
            details.append(f"last_time={subrun_status.last_time}")
        if subrun_status.last_morph_time is not None:
            details.append(f"last_morph_time={subrun_status.last_morph_time}")
        if header is not None and header.crs is not None:
            details.append(f"crs={header.crs}")
        typer.echo(f"  {subrun_status.index}: " + "; ".join(details))

    typer.echo("Registered raw quantities:")
    for item in _available_raw_variables(headers):
        typer.echo(
            f"  {item.name}: {', '.join(item.candidates)} "
            f"({item.available_subruns}/{item.total_subruns} sub-runs)"
        )
    typer.echo("Raw NetCDF variables:")
    raw_names = _raw_variable_names(headers)
    if raw_names:
        for name in raw_names:
            typer.echo(f"  {name}")
    else:
        typer.echo("  (none)")

    if issues:
        typer.echo("Issues:")
        for issue in issues:
            typer.echo(f"  {issue.level.upper()} {issue.code}: {issue.message}")
            if issue.hint is not None:
                typer.echo(f"    Hint: {issue.hint}")


def _validate_variable_requests(
    config: ProjectConfig,
    discovered: list[tuple[str, tuple[_SubRunHeader, ...]]],
    issues: list[ValidationIssue],
) -> None:
    for analysis_index, analysis in enumerate(config.analyses):
        for variable_index, variable_name in enumerate(analysis.variables):
            location = f"analyses[{analysis_index}].variables[{variable_index}]"
            if variable_name.strip().casefold() in _LINE_QUANTITY_NAMES:
                issues.append(
                    ValidationIssue(
                        code="V-VAR-006",
                        level="error",
                        location=location,
                        message=(
                            f"Line quantity {variable_name!r} cannot be requested "
                            "without line sampling."
                        ),
                        hint=(
                            "Line sampling is not available in v1; use a "
                            "grid-located quantity."
                        ),
                    )
                )
                continue

            try:
                spec = get_variable_spec(variable_name)
            except UnknownVariableError as error:
                _validate_unregistered_variable(
                    variable_name,
                    location,
                    error,
                    discovered,
                    issues,
                )
                continue

            if spec.kind is VariableKind.RAW:
                _validate_registered_raw_variable(
                    variable_name,
                    location,
                    spec,
                    discovered,
                    issues,
                )
            elif spec.location is VariableLocation.LINE:
                issues.append(
                    ValidationIssue(
                        code="V-VAR-006",
                        level="error",
                        location=location,
                        message=(
                            f"Line quantity {variable_name!r} cannot be requested "
                            "without line sampling."
                        ),
                        hint=(
                            "Line sampling is not available in v1; use a "
                            "grid-located quantity."
                        ),
                    )
                )


def _validate_registered_raw_variable(
    variable_name: str,
    location: str,
    spec: VariableSpec,
    discovered: list[tuple[str, tuple[_SubRunHeader, ...]]],
    issues: list[ValidationIssue],
) -> None:
    for simulation_label, headers in discovered:
        missing = tuple(
            header.index
            for header in headers
            if not any(
                _contains_name(header.variable_names, candidate)
                for candidate in spec.netcdf_candidates
            )
        )
        if missing:
            issues.append(
                _missing_variable_issue(
                    variable_name,
                    location,
                    simulation_label,
                    missing,
                    registered_name=spec.name,
                )
            )


def _validate_unregistered_variable(
    variable_name: str,
    location: str,
    error: UnknownVariableError,
    discovered: list[tuple[str, tuple[_SubRunHeader, ...]]],
    issues: list[ValidationIssue],
) -> None:
    all_headers = tuple(header for _, headers in discovered for header in headers)
    if not any(
        _contains_name(header.variable_names, variable_name) for header in all_headers
    ):
        if all_headers:
            issues.append(
                ValidationIssue(
                    code="V-VAR-001",
                    level="error",
                    location=location,
                    message=str(error),
                    hint=(
                        "Use a registered name or alias, or a raw NetCDF variable "
                        "present in the configured simulations."
                    ),
                )
            )
        return

    for simulation_label, headers in discovered:
        missing = tuple(
            header.index
            for header in headers
            if not _contains_name(header.variable_names, variable_name)
        )
        if missing:
            issues.append(
                _missing_variable_issue(
                    variable_name,
                    location,
                    simulation_label,
                    missing,
                )
            )


def _missing_variable_issue(
    variable_name: str,
    location: str,
    simulation_label: str,
    missing_subruns: tuple[int, ...],
    *,
    registered_name: str | None = None,
) -> ValidationIssue:
    variable_description = (
        f"{variable_name!r} (registered as {registered_name!r})"
        if registered_name is not None
        else repr(variable_name)
    )
    missing = ", ".join(str(index) for index in missing_subruns)
    return ValidationIssue(
        code="V-VAR-002",
        level="error",
        location=location,
        message=(
            f"Variable {variable_description} is missing from sub-run(s) "
            f"{missing} of simulation {simulation_label!r}."
        ),
        hint=(
            "Check the variable spelling and ensure the field exists in every sub-run."
        ),
    )


def _emit_report(
    report: ValidationReport,
    report_format: Literal["text", "json"] = "text",
    *,
    err: bool = False,
) -> None:
    rendered = report.to_json() if report_format == "json" else report.to_text()
    typer.echo(rendered, err=err, nl=False)


def _parse_analysis_names(value: str) -> tuple[str, ...]:
    names = tuple(name.strip() for name in value.split(","))
    if not names or any(not name for name in names):
        raise ValueError("--analyses must be a comma-separated list of analysis names.")
    if len(set(names)) != len(names):
        raise ValueError("--analyses cannot contain duplicate names.")
    unsupported = tuple(name for name in names if name not in _ANALYSIS_NAMES)
    if unsupported:
        supported = ", ".join(sorted(_ANALYSIS_NAMES))
        raise ValueError(
            f"Unsupported analysis {unsupported[0]!r}; choose from: {supported}."
        )
    return names


def _starter_config(
    simulation_paths: list[Path],
    analysis_names: tuple[str, ...],
) -> ProjectConfig:
    simulations = [
        {
            "path": str(path.expanduser().resolve(strict=False)),
            "label": label,
        }
        for path, label in zip(
            simulation_paths,
            _unique_simulation_labels(simulation_paths),
            strict=True,
        )
    ]
    analyses: list[dict[str, object]] = []
    for name in analysis_names:
        analysis: dict[str, object] = {
            "analysis": name,
            "variables": ["bed_level"],
        }
        if name == "river_km_statistics":
            analysis["regions"] = [
                {
                    "name": "replace_with_region_name",
                    "polygon": "path/to/region.shp",
                }
            ]
            analysis["river_km"] = {"file": "path/to/river_km.ldb"}
        analyses.append(analysis)
    return ProjectConfig.model_validate(
        {"simulations": simulations, "analyses": analyses}
    )


def _unique_simulation_labels(simulation_paths: list[Path]) -> tuple[str, ...]:
    labels: list[str] = []
    used: set[str] = set()
    for index, path in enumerate(simulation_paths, start=1):
        base = path.name or f"simulation_{index}"
        label = base
        suffix = index
        while label.casefold() in used:
            label = f"{base}_{suffix}"
            suffix += 1
        labels.append(label)
        used.add(label.casefold())
    return tuple(labels)


def _write_new_text(path: Path, content: str) -> None:
    requested_path = path.expanduser()
    if requested_path.exists() or requested_path.is_symlink():
        raise FileExistsError(f"Refusing to overwrite existing file: {requested_path}")
    target = requested_path.resolve(strict=False)
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Refusing to overwrite existing file: {target}")

    created = False
    try:
        with target.open("x", encoding="utf-8", newline="\n") as stream:
            created = True
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise FileExistsError(
            f"Refusing to overwrite existing file: {target}"
        ) from None
    except OSError:
        if created:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
        raise
