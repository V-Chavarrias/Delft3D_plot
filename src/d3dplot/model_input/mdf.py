# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only parser for Delft3D 4 MDF run files."""

from __future__ import annotations

import re
from pathlib import Path

from d3dplot.model_input.errors import ModelInputError

MDF_HINT = "Check the MDF has 'keyword = value' lines as Delft3D 4 writes them."
_KEYWORD_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
_COMMENT_KEYWORD = "Commnt"


def read_mdf(path: Path) -> dict[str, str]:
    """Return the value text of every keyword, without surrounding '#' quotes."""
    try:
        lines = path.read_text(encoding="latin-1").splitlines()
    except OSError as error:
        raise ModelInputError(
            path,
            f"Could not read the MDF file: {error}",
            MDF_HINT,
        ) from error

    values: dict[str, str] = {}
    for line in lines:
        match = _KEYWORD_LINE.match(line)
        if match is None or match.group(1) == _COMMENT_KEYWORD:
            continue
        key, value = match.group(1), match.group(2)
        if len(value) >= 2 and value.startswith("#") and value.endswith("#"):
            value = value[1:-1].strip()
        values[key] = value
    return values
