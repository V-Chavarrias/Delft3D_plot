# SPDX-License-Identifier: GPL-3.0-or-later
"""Read fixed-format river-km point files shared by map views."""

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


def read_river_km_points(path: str | Path) -> tuple[RiverKmPoint, ...]:
    """Read x, y, label, km rows after the river-km file's header."""
    file_path = Path(path)
    try:
        with file_path.open(encoding="utf-8-sig", newline="") as stream:
            rows = csv.reader(stream)
            next(rows)
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
                if not all(math.isfinite(value) for value in (x_coord, y_coord, km)):
                    raise ValueError(f"row {row_number} has non-finite values")
                points.append(
                    RiverKmPoint(
                        x=x_coord,
                        y=y_coord,
                        label=label,
                        km=km,
                        km_text=km_text,
                    )
                )
    except (OSError, StopIteration, csv.Error, UnicodeError, ValueError) as error:
        raise RiverKmFileError(
            f"River-km file {file_path} is unreadable or has invalid format: {error}"
        ) from error
    if not points:
        raise RiverKmFileError(f"River-km file contains no points: {file_path}.")
    return tuple(points)
