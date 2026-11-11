# SPDX-License-Identifier: GPL-3.0-or-later
"""Discover plain FM, D3D4 and SMT simulation folders."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from pathlib import Path

from d3dplot.paths import resolve_path_preserving_drive
from d3dplot.simulation.d3d4 import D3D4Run, find_d3d4_runs
from d3dplot.simulation.subrun import (
    SubRun,
    discover_d3d4_subrun,
    discover_subrun,
    discover_subrun_from_template,
)
from d3dplot.store.layout import OutputLayout
from d3dplot.validate.report import ValidationIssue, ValidationReport

_MAX_DISCOVERY_WORKERS = 64
_MIN_SUBRUNS_FOR_PARALLEL = 8


class SimulationKind(StrEnum):
    """Supported simulation folder types."""

    FM = "fm"
    SMT = "smt"
    D3D4 = "d3d4"


@dataclass(frozen=True, slots=True)
class Simulation:
    """A discovered simulation and its ordered FM sub-runs."""

    root: Path
    kind: SimulationKind
    subruns: tuple[SubRun, ...]


def discover_simulation(
    path: Path,
    *,
    check_map_headers: bool = False,
    output_layout: OutputLayout | None = None,
) -> tuple[Simulation | None, ValidationReport]:
    """Discover one plain-FM, D3D4 or SMT simulation without modifying its inputs.

    ``output_layout`` locates the cached D3D4 topology; it defaults to the
    simulation's own ``d3dplot`` folder.
    """
    root = resolve_path_preserving_drive(path)
    if not root.is_dir():
        return None, ValidationReport(
            (
                ValidationIssue(
                    code="V-SIM-001",
                    level="error",
                    location=str(root),
                    message="Simulation folder does not exist or is not a directory.",
                    hint=(
                        "Provide the path to a readable D-Flow FM or SMT "
                        "simulation folder."
                    ),
                ),
            )
        )

    issues: list[ValidationIssue] = []
    output_dir = root / "output"
    layout = output_layout or OutputLayout(root / "d3dplot")
    d3d4_runs: tuple[D3D4Run, ...] = ()
    subrun_folders: list[tuple[int, Path]] = []
    if (root / "smt.yml").is_file():
        subrun_folders, folder_issue = _integer_subrun_folders(output_dir)
        if folder_issue is not None:
            return None, ValidationReport((folder_issue,))

    if subrun_folders:
        kind = SimulationKind.SMT
        if (output_dir / "work").exists():
            issues.append(
                ValidationIssue(
                    code="V-SIM-011",
                    level="warning",
                    location=str(output_dir / "work"),
                    message=(
                        "SMT output/work exists; the simulation may still be running."
                    ),
                    hint=(
                        "The in-progress work folder is excluded from "
                        "discovered sub-runs."
                    ),
                )
            )
        issues.extend(_check_subrun_numbering(root, subrun_folders))
    else:
        d3d4_runs = find_d3d4_runs(root)
        if len(d3d4_runs) > 1:
            # One D3D4 run per folder keeps each topology cache path unambiguous.
            return None, ValidationReport(
                (
                    ValidationIssue(
                        code="V-SIM-013",
                        level="error",
                        location=str(root),
                        message=(
                            f"Folder holds {len(d3d4_runs)} D3D4 runs; "
                            "one D3D4 run per folder is supported."
                        ),
                        hint="Move each trim-<name>.def run into its own folder.",
                    ),
                )
            )
        if d3d4_runs:
            kind = SimulationKind.D3D4
        else:
            kind = SimulationKind.FM
            subrun_folders = [(0, root)]

    subruns: list[SubRun] = []
    if kind is SimulationKind.D3D4:
        discovered_subruns = [discover_d3d4_subrun(d3d4_runs[0], layout)]
    elif kind is SimulationKind.SMT:
        discovered_subruns = _discover_smt_subruns(
            subrun_folders,
            check_map_headers=check_map_headers,
        )
    else:
        discovered_subruns = _discover_subruns(
            subrun_folders,
            check_map_headers=check_map_headers,
        )
    for subrun, subrun_issues in discovered_subruns:
        issues.extend(subrun_issues)
        if subrun is not None:
            subruns.append(subrun)

    issues.extend(_path_length_issues(subruns))

    if kind is SimulationKind.SMT:
        issues.extend(_check_partition_counts(root, subruns))

    report = ValidationReport(tuple(issues))
    if not report.is_valid or not subruns:
        return None, report
    return Simulation(root=root, kind=kind, subruns=tuple(subruns)), report


def _path_length_issues(subruns: Sequence[SubRun]) -> tuple[ValidationIssue, ...]:
    """Warn when discovered model-output paths exceed the Windows safe limit."""
    issues: list[ValidationIssue] = []
    for subrun in subruns:
        for map_file in subrun.map_files:
            path_length = len(str(map_file))
            if path_length <= 240:
                continue
            issues.append(
                ValidationIssue(
                    code="V-OUT-040",
                    level="warning",
                    location=str(map_file),
                    message=(
                        f"Model-output path is {path_length} characters long; "
                        "Windows NetCDF/HDF5 access may exceed MAX_PATH."
                    ),
                    hint=(
                        "Use a shorter simulation/output path or a drive "
                        "mapping before processing."
                    ),
                )
            )
    return tuple(issues)


def _discover_subruns(
    subrun_folders: list[tuple[int, Path]],
    *,
    check_map_headers: bool,
) -> list[tuple[SubRun | None, tuple[ValidationIssue, ...]]]:
    if not subrun_folders:
        return []

    worker_count = min(_MAX_DISCOVERY_WORKERS, len(subrun_folders))
    if worker_count == 1 or len(subrun_folders) < _MIN_SUBRUNS_FOR_PARALLEL:
        return [
            discover_subrun(
                folder,
                index,
                check_map_headers=check_map_headers,
            )
            for index, folder in subrun_folders
        ]

    discover = partial(discover_subrun, check_map_headers=check_map_headers)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        return list(
            executor.map(
                discover,
                (folder for _, folder in subrun_folders),
                (index for index, _ in subrun_folders),
            )
        )


def _discover_smt_subruns(
    subrun_folders: list[tuple[int, Path]],
    *,
    check_map_headers: bool,
) -> list[tuple[SubRun | None, tuple[ValidationIssue, ...]]]:
    """Read SMT model inputs once and reuse the first sub-run as a template."""
    if not subrun_folders:
        return []
    first_index, first_folder = subrun_folders[0]
    first = discover_subrun(
        first_folder,
        first_index,
        check_map_headers=check_map_headers,
    )
    template, _ = first
    if template is None:
        return [first]

    remaining = subrun_folders[1:]
    if not remaining:
        return [first]
    discover = partial(
        discover_subrun_from_template,
        template,
        check_map_headers=check_map_headers,
    )
    worker_count = min(_MAX_DISCOVERY_WORKERS, len(remaining))
    if worker_count == 1 or len(remaining) < _MIN_SUBRUNS_FOR_PARALLEL:
        discovered = [discover(folder, index) for index, folder in remaining]
    else:
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="d3dplot-discovery",
        ) as executor:
            discovered = list(
                executor.map(
                    discover,
                    (folder for _, folder in remaining),
                    (index for index, _ in remaining),
                )
            )
    return [first, *discovered]


def _integer_subrun_folders(
    output_dir: Path,
) -> tuple[list[tuple[int, Path]], ValidationIssue | None]:
    if not output_dir.is_dir():
        return [], None
    try:
        with os.scandir(output_dir) as entries:
            folders = [
                (int(entry.name), output_dir / entry.name)
                for entry in entries
                if re.fullmatch(r"[0-9]+", entry.name) and entry.is_dir()
            ]
    except OSError as error:
        return [], ValidationIssue(
            code="V-SIM-001",
            level="error",
            location=str(output_dir),
            message=f"Could not list SMT sub-run folders: {error}",
            hint="Check that the output folder is readable.",
        )
    folders.sort(key=lambda item: (item[0], item[1].name))
    return folders, None


def _check_subrun_numbering(
    root: Path,
    subrun_folders: list[tuple[int, Path]],
) -> tuple[ValidationIssue, ...]:
    indices = [index for index, _ in subrun_folders]
    if not indices:
        return ()

    missing = sorted(set(range(indices[-1] + 1)) - set(indices))
    duplicates = sorted(index for index in set(indices) if indices.count(index) > 1)
    if not missing and not duplicates:
        return ()

    details: list[str] = []
    if missing:
        details.append("missing " + ", ".join(str(index) for index in missing))
    if duplicates:
        details.append("duplicate " + ", ".join(str(index) for index in duplicates))
    return (
        ValidationIssue(
            code="V-SIM-010",
            level="error",
            location=str(root / "output"),
            message=(
                "SMT sub-run numbering must be unique and contiguous from 0; "
                + "; ".join(details)
                + "."
            ),
            hint="Restore missing sub-run folders or correct their numeric names.",
        ),
    )


def _check_partition_counts(
    root: Path,
    subruns: list[SubRun],
) -> tuple[ValidationIssue, ...]:
    if len(subruns) < 2:
        return ()

    counts = {subrun.index: subrun.partition_count for subrun in subruns}
    if len(set(counts.values())) == 1:
        return ()

    summary = ", ".join(
        f"sub-run {index}: {count}" for index, count in sorted(counts.items())
    )
    return (
        ValidationIssue(
            code="V-OUT-003",
            level="error",
            location=str(root),
            message=f"Map-file partition counts differ between sub-runs ({summary}).",
            hint="Use a consistent partition count across all SMT sub-runs.",
        ),
    )
