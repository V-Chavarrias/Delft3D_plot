# SPDX-License-Identifier: GPL-3.0-or-later
"""Read fixed-format river-km point files."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path


class RiverKmFileError(ValueError):
    """A fixed-format river-km file cannot be read."""


@dataclass(frozen=True, slots=True)
class RiverKmPoint:
    """One labelled coordinate and its river kilometre."""

    x: float
    y: float
    label: str
    km: float
    km_text: str

    @property
    def branch(self) -> str:
        """Return the branch suffix following the first underscore."""
        _, separator, branch = self.label.partition("_")
        return branch.strip() if separator else self.label.strip()


def read_river_km_points(path: str | Path) -> tuple[RiverKmPoint, ...]:
    """Read x, y, label, and km rows after the river-km file's header."""
    file_path = Path(path)
    try:
        with file_path.open(encoding="utf-8-sig", newline="") as stream:
            rows = csv.reader(stream)
            header = next(rows)
            if len(header) < 4:
                raise ValueError("header needs x, y, label, and km columns")
            points: list[RiverKmPoint] = []
            for row_number, row in enumerate(rows, start=2):
                if not row:
                    continue
                if len(row) < 4:
                    raise ValueError(
                        f"row {row_number} needs x, y, label, and km columns"
                    )
                x_coord, y_coord = float(row[0]), float(row[1])
                label, km_text = row[2].strip(), row[3].strip()
                km = float(km_text)
                if not label:
                    raise ValueError(f"row {row_number} has an empty label")
                point = RiverKmPoint(
                    x=x_coord,
                    y=y_coord,
                    label=label,
                    km=km,
                    km_text=km_text,
                )
                if not point.branch:
                    raise ValueError(f"row {row_number} has an empty branch")
                if not all(math.isfinite(value) for value in (x_coord, y_coord, km)):
                    raise ValueError(f"row {row_number} has non-finite values")
                points.append(point)
    except (OSError, StopIteration, csv.Error, UnicodeError, ValueError) as error:
        raise RiverKmFileError(
            f"River-km file {file_path} is unreadable or has invalid format: {error}"
        ) from error
    if not points:
        raise RiverKmFileError(f"River-km file contains no points: {file_path}.")
    return tuple(points)
