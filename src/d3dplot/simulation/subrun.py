# SPDX-License-Identifier: GPL-3.0-or-later
"""Discover the model input and map files for one FM sub-run."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from d3dplot.model_input.dimr import (
    MultipleDflowFMComponentsError,
    NoDflowFMComponentError,
    resolve_dimr_mdu,
)
from d3dplot.model_input.mdu import MduInfo
from d3dplot.model_input.reader import ModelInput
from d3dplot.simulation.partitions import discover_map_files
from d3dplot.validate import validate_model_input
from d3dplot.validate.report import ValidationIssue

_PARTITION_MDU_SUFFIX = re.compile(r"_\d{4}$")


@dataclass(frozen=True, slots=True)
class SubRun:
    """One FM run, either an SMT sub-run or the only plain-FM run."""

    index: int
    folder: Path
    model_input: ModelInput
    map_files: tuple[Path, ...]
    partitioned: bool

    @property
    def mdu(self) -> MduInfo:
        """Return the typed MDU view for this sub-run."""
        return self.model_input.mdu

    @property
    def partition_count(self) -> int:
        """Return the number of map files for this sub-run."""
        return len(self.map_files)


def discover_subrun(
    folder: Path,
    index: int,
) -> tuple[SubRun | None, tuple[ValidationIssue, ...]]:
    """Read one sub-run's model inputs and identify its map output files."""
    mdu_path, issues = _find_mdu(folder)
    if mdu_path is None:
        return None, issues

    model_input, input_report = validate_model_input(mdu_path)
    if model_input is None:
        return None, input_report.issues

    map_files, output_issues = discover_map_files(model_input.mdu)
    if map_files is None:
        return None, output_issues

    return (
        SubRun(
            index=index,
            folder=folder,
            model_input=model_input,
            map_files=map_files.paths,
            partitioned=map_files.partitioned,
        ),
        input_report.issues + output_issues,
    )


def _find_mdu(folder: Path) -> tuple[Path | None, tuple[ValidationIssue, ...]]:
    dimr_path = folder / "dimr_config.xml"
    if dimr_path.is_file():
        try:
            mdu_path = resolve_dimr_mdu(dimr_path)
        except NoDflowFMComponentError:
            pass
        except MultipleDflowFMComponentsError as error:
            return None, (
                _simulation_issue(
                    "V-SIM-002",
                    dimr_path,
                    str(error),
                    "Keep one dflowfm component per simulation.",
                ),
            )
        except (OSError, ValueError) as error:
            return None, (
                _simulation_issue(
                    "V-SIM-001",
                    dimr_path,
                    f"Could not resolve an MDU from DIMR: {error}",
                    (
                        "Check that dimr_config.xml is valid and references "
                        "a readable MDU."
                    ),
                ),
            )
        else:
            if not mdu_path.is_file():
                return None, (
                    _simulation_issue(
                        "V-SIM-001",
                        mdu_path,
                        "The DIMR dflowfm component references a missing MDU.",
                        "Check the component workingDir and inputFile.",
                    ),
                )
            return mdu_path, ()

    try:
        candidates = sorted(
            (
                candidate
                for candidate in folder.iterdir()
                if candidate.is_file()
                and candidate.suffix.lower() == ".mdu"
                and _PARTITION_MDU_SUFFIX.search(candidate.stem) is None
            ),
            key=lambda candidate: candidate.name.casefold(),
        )
    except OSError as error:
        return None, (
            _simulation_issue(
                "V-SIM-001",
                folder,
                f"Could not list model files: {error}",
                "Check that the simulation folder is readable.",
            ),
        )

    if not candidates:
        return None, (
            _simulation_issue(
                "V-SIM-001",
                folder,
                "No D-Flow FM MDU was found.",
                (
                    "Provide one non-partition MDU or a DIMR configuration "
                    "with a dflowfm component."
                ),
            ),
        )
    if len(candidates) > 1:
        names = ", ".join(candidate.name for candidate in candidates)
        return None, (
            _simulation_issue(
                "V-SIM-002",
                folder,
                f"Several candidate MDUs were found: {names}.",
                (
                    "Provide a DIMR configuration that selects one dflowfm "
                    "component or remove ambiguity."
                ),
            ),
        )
    return candidates[0], ()


def _simulation_issue(
    code: str,
    path: Path,
    message: str,
    hint: str,
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        level="error",
        location=str(path),
        message=message,
        hint=hint,
    )
