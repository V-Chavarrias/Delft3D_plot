# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared helpers for the model-input INI formats."""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path

from hydrolib.core.dflowfm.ini.parser import Parser  # type: ignore[import-untyped]
from pydantic import ValidationError

from d3dplot.model_input.errors import ModelInputError

_INI_HINT = "Check the referenced file and correct its INI syntax or values."


def parse_ini_file(path: Path) -> dict[str, object]:
    """Parse an INI file with HYDROLIB-core's format-independent parser."""
    try:
        result = Parser.parse_as_dict(path)
    except (OSError, ValueError, ValidationError) as error:
        raise ModelInputError(
            path,
            f"Could not parse the INI file: {error}",
            _INI_HINT,
        ) from error

    if not isinstance(result, dict):
        raise ModelInputError(
            path,
            "The INI parser returned an unexpected document structure.",
            _INI_HINT,
        )
    return result


def section_mappings(
    value: object,
    *,
    path: Path,
    section_name: str,
) -> tuple[Mapping[str, object], ...]:
    """Normalize one or repeated section mappings from a parsed document."""
    if value is None:
        return ()
    if isinstance(value, Mapping):
        return (value,)
    if isinstance(value, list):
        sections: list[Mapping[str, object]] = []
        for index, section in enumerate(value, start=1):
            if not isinstance(section, Mapping):
                raise ModelInputError(
                    path,
                    f"Section [{section_name}] entry {index} is not a mapping.",
                    _INI_HINT,
                )
            sections.append(section)
        return tuple(sections)
    raise ModelInputError(
        path,
        f"Section [{section_name}] is not a mapping.",
        _INI_HINT,
    )


def optional_text(value: object, *, path: Path, field_name: str) -> str | None:
    """Return a parsed optional text value, rejecting unexpected structures."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ModelInputError(
            path,
            f"{field_name} must be text, not {type(value).__name__}.",
            _INI_HINT,
        )
    return value.strip() or None


def optional_float(value: object, *, path: Path, field_name: str) -> float | None:
    """Parse an optional finite numeric INI value."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ModelInputError(path, f"{field_name} must be numeric.", _INI_HINT)
    if isinstance(value, (int, float)):
        parsed = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        parts = text.split(maxsplit=1)
        numeric_value = parts[0]
        if len(parts) == 2 and not parts[1].startswith("["):
            raise ModelInputError(
                path,
                f"{field_name} has unexpected trailing value {parts[1]!r}.",
                _INI_HINT,
            )
        try:
            parsed = float(numeric_value)
        except ValueError as error:
            raise ModelInputError(
                path,
                f"{field_name} must be numeric, not {value!r}.",
                _INI_HINT,
            ) from error
    else:
        raise ModelInputError(path, f"{field_name} must be numeric.", _INI_HINT)

    if not math.isfinite(parsed):
        raise ModelInputError(path, f"{field_name} must be finite.", _INI_HINT)
    return parsed
