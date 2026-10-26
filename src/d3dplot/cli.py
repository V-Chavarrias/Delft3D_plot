# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only inspection, configuration validation, and setup commands."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Annotated, Literal, Protocol, cast

import typer
import yaml

from d3dplot.config.io import dump_config, dump_json_schema, load_config_data
from d3dplot.config.project import ProjectConfig
from d3dplot.simulation.discover import (
    Simulation,
    discover_simulation,
)
from d3dplot.simulation.partitions import _NETCDF4_READ_LOCK
from d3dplot.simulation.status import (
    SimulationStatus,
    SimulationStatusError,
    detect_simulation_status,
    inspect_simulation_status,
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
    """Run configuration, variable, and simulation-discovery checks."""
    try:
        data = load_config_data(configuration)
    except (OSError, ValueError, yaml.YAMLError) as error:
        typer.echo(f"Could not read configuration {configuration}: {error}", err=True)
        raise typer.Exit(code=2) from error

    config, static_report = validate_config(data)
    issues = list(static_report.issues)
    discovered: list[tuple[str, tuple[_SubRunHeader, ...]]] = []

    if config is not None:
        for simulation_config in config.simulations:
            simulation, discovery_report = discover_simulation(simulation_config.path)
            issues.extend(discovery_report.issues)
            if simulation is None:
                continue
            headers, header_report = _read_subrun_headers(simulation)
            issues.extend(header_report.issues)
            if header_report.is_valid:
                discovered.append((simulation_config.label, headers))
        _validate_variable_requests(config, discovered, issues)

    report = ValidationReport(tuple(issues))
    _emit_report(report, report_format)
    if not report.is_valid:
        raise typer.Exit(code=1)


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
