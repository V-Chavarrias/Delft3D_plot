# SPDX-License-Identifier: GPL-3.0-or-later
"""Discover plain FM and SMT simulation folders."""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from d3dplot.simulation.subrun import SubRun, discover_subrun
from d3dplot.validate.report import ValidationIssue, ValidationReport

_MAX_DISCOVERY_WORKERS = 64
_MIN_SUBRUNS_FOR_PARALLEL = 8


class SimulationKind(StrEnum):
    """Supported simulation folder types."""

    FM = "fm"
    SMT = "smt"


@dataclass(frozen=True, slots=True)
class Simulation:
    """A discovered simulation and its ordered FM sub-runs."""

    root: Path
    kind: SimulationKind
    subruns: tuple[SubRun, ...]


def discover_simulation(
    path: Path,
) -> tuple[Simulation | None, ValidationReport]:
    """Discover one plain-FM or SMT simulation without modifying its inputs."""
    root = path.expanduser().resolve(strict=False)
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
    if (root / "smt.yml").is_file():
        subrun_folders, folder_issue = _integer_subrun_folders(output_dir)
        if folder_issue is not None:
            return None, ValidationReport((folder_issue,))
    else:
        subrun_folders = []

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
        kind = SimulationKind.FM
        subrun_folders = [(0, root)]

    subruns: list[SubRun] = []
    for subrun, subrun_issues in _discover_subruns(subrun_folders):
        issues.extend(subrun_issues)
        if subrun is not None:
            subruns.append(subrun)

    if kind is SimulationKind.SMT:
        issues.extend(_check_partition_counts(root, subruns))

    report = ValidationReport(tuple(issues))
    if not report.is_valid or not subruns:
        return None, report
    return Simulation(root=root, kind=kind, subruns=tuple(subruns)), report


def _discover_subruns(
    subrun_folders: list[tuple[int, Path]],
) -> list[tuple[SubRun | None, tuple[ValidationIssue, ...]]]:
    if not subrun_folders:
        return []

    worker_count = min(_MAX_DISCOVERY_WORKERS, len(subrun_folders))
    if worker_count == 1 or len(subrun_folders) < _MIN_SUBRUNS_FOR_PARALLEL:
        return [discover_subrun(folder, index) for index, folder in subrun_folders]

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        return list(
            executor.map(
                discover_subrun,
                (folder for _, folder in subrun_folders),
                (index for index, _ in subrun_folders),
            )
        )


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
