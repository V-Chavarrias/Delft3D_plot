# SPDX-License-Identifier: GPL-3.0-or-later
"""Reader for D-Flow FM polyline files (.pli, .pliz, .pol, .ldb)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

import numpy as np

from d3dplot.model_input.errors import ModelInputError
from d3dplot.model_input.text import read_model_text

_POLYLINE_HINT = (
    "Each block needs a name line, a 'rows columns' line and one coordinate line "
    "per row. Check the block at the reported line."
)


@dataclass(frozen=True, slots=True)
class Polyline:
    """One named polyline of a polyline file, in model coordinates."""

    name: str
    xy: np.ndarray


class _State(Enum):
    NEW_BLOCK = auto()
    AFTER_DESCRIPTION = auto()
    AFTER_NAME = auto()
    IN_POINTS = auto()


def read_polyline_file(
    path: str | Path,
    *,
    has_z_values: bool | None = None,
) -> tuple[Polyline, ...]:
    """Read every polyline block of a polyline file.

    A block is optional ``*`` description lines, a name line, a ``rows columns``
    line and ``rows`` coordinate lines. Blank lines are ignored. Any malformed
    block raises ModelInputError, as HYDROLIB-core does. Only x and y are kept;
    the z column of a ``.pliz`` file is validated and dropped.

    Args:
        path: Polyline file to read.
        has_z_values: Whether the third column is z. Defaults to True for ``.pliz``.

    Returns:
        The polylines in file order. An empty file gives an empty tuple.
    """
    source = Path(path)
    if has_z_values is None:
        has_z_values = source.suffix == ".pliz"
    try:
        text = read_model_text(source)
    except OSError as error:
        raise ModelInputError(
            source,
            f"Could not read polyline file: {error}",
            _POLYLINE_HINT,
        ) from error
    return _parse_polylines(text, source, has_z_values=has_z_values)


def _parse_polylines(
    text: str,
    path: Path,
    *,
    has_z_values: bool,
) -> tuple[Polyline, ...]:
    lines = text.splitlines()
    polylines: list[Polyline] = []
    state = _State.NEW_BLOCK
    name = ""
    row_count = 0
    column_count = 0
    coordinates: list[float] = []

    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue

        if state is _State.IN_POINTS:
            point = _parse_point(stripped, column_count, has_z_values=has_z_values)
            if point is None:
                raise _block_error(
                    path,
                    line_number,
                    f"expected a coordinate line with {column_count} numeric "
                    "values per row",
                )
            coordinates.extend(point)
            if len(coordinates) == 2 * row_count:
                polylines.append(
                    Polyline(
                        name=name,
                        xy=np.asarray(coordinates, dtype=np.float64).reshape(-1, 2),
                    )
                )
                coordinates = []
                state = _State.NEW_BLOCK
            continue

        if stripped.startswith("*"):
            if state is _State.AFTER_NAME:
                raise _block_error(
                    path,
                    line_number,
                    "expected a 'rows columns' line after the name",
                )
            state = _State.AFTER_DESCRIPTION
            continue

        if state is _State.AFTER_NAME:
            dimensions = _parse_dimensions(stripped)
            if dimensions is None:
                raise _block_error(
                    path,
                    line_number,
                    "expected a 'rows columns' line with two positive integers",
                )
            row_count, column_count = dimensions
            state = _State.IN_POINTS
            continue

        name = stripped
        state = _State.AFTER_NAME

    if state is not _State.NEW_BLOCK:
        raise _block_error(
            path,
            len(lines),
            "end of file reached before the polyline block was complete",
        )
    return tuple(polylines)


def _parse_dimensions(stripped: str) -> tuple[int, int] | None:
    elements = stripped.split()
    if len(elements) != 2:
        return None
    try:
        rows, columns = int(elements[0]), int(elements[1])
    except ValueError:
        return None
    if rows <= 0 or columns <= 0:
        return None
    return rows, columns


def _parse_point(
    stripped: str,
    column_count: int,
    *,
    has_z_values: bool,
) -> tuple[float, float] | None:
    if column_count < 2 or (has_z_values and column_count < 3):
        return None
    elements = stripped.split()
    if len(elements) < column_count:
        return None
    try:
        values = [float(element) for element in elements[:column_count]]
    except ValueError:
        return None
    return values[0], values[1]


def _block_error(path: Path, line_number: int, reason: str) -> ModelInputError:
    return ModelInputError(path, f"line {line_number}: {reason}", _POLYLINE_HINT)
