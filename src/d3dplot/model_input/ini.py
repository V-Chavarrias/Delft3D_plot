# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared helpers for the model-input INI formats."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from d3dplot.model_input.errors import ModelInputError
from d3dplot.model_input.text import read_model_text

_INI_HINT = "Check the referenced file and correct its INI syntax or values."

_COMMENT_DELIMITER = "#"
_LEADING_DIGITS = re.compile(r"\d+")
_DIGIT_WORDS = {
    "0": "zero",
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
}


@dataclass
class _IniSection:
    header: str
    properties: list[tuple[str, str | None, str | None]] = field(default_factory=list)


def parse_ini_file(path: Path) -> dict[str, object]:
    """Parse a Deltares INI file into nested dictionaries.

    The result has the same shape as HYDROLIB-core's ``Parser.parse_as_dict``:
    section keys are normalised with ``normalise_key``, repeated sections or keys
    become lists, and each section carries a ``comments`` mapping.
    """
    try:
        text = read_model_text(path)
    except OSError as error:
        raise ModelInputError(
            path,
            f"Could not parse the INI file: {error}",
            _INI_HINT,
        ) from error
    return _parse_ini_text(text)


def _parse_ini_text(text: str) -> dict[str, object]:
    """Build the section list, then flatten it to the public dictionary shape.

    Lines before the first section and lines without ``=`` that are not
    comments or headers are ignored, as in HYDROLIB-core.
    """
    sections: list[_IniSection] = []
    current: _IniSection | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(_COMMENT_DELIMITER):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            current = _IniSection(header=stripped[1:-1].strip())
            sections.append(current)
        elif current is not None and "=" in line:
            current.properties.append(_parse_property(line))

    document: dict[str, object] = {}
    for section in sections:
        key = normalise_key(section.header)
        value = _section_to_dict(section)
        if key not in document:
            document[key] = value
            continue
        existing = document[key]
        if isinstance(existing, list):
            existing.append(value)
        else:
            document[key] = [existing, value]
    return document


def _parse_property(line: str) -> tuple[str, str | None, str | None]:
    """Return the normalised key, value and comment of one ``key = value`` line."""
    raw_key, _, raw_value = line.partition("=")
    key = normalise_key(raw_key.strip())
    value_text = raw_value.strip()
    if not value_text:
        return key, None, None
    comment, value = _split_value_and_comment(value_text)
    return key, value, comment


def _split_value_and_comment(text: str) -> tuple[str | None, str | None]:
    """Split a stripped value from its comment.

    ``#`` both delimits comments and quotes values, so ``#a b#`` is a value
    and ``#a b# note`` has a comment. Empty results become ``None``.
    """
    if _COMMENT_DELIMITER not in text:
        comment, value = "", text
    else:
        parts = text.split(_COMMENT_DELIMITER)
        hash_count = len(parts) - 1
        if hash_count == 1:
            comment, value = parts[-1], parts[0]
        elif text.startswith(_COMMENT_DELIMITER):
            comment = _COMMENT_DELIMITER.join(parts[3:]) if hash_count >= 3 else ""
            value = _COMMENT_DELIMITER.join(parts[0:3])
        else:
            comment = _COMMENT_DELIMITER.join(parts[1:])
            value = parts[0]
    return comment.strip() or None, value.strip() or None


def _section_to_dict(section: _IniSection) -> dict[str, object]:
    values: dict[str, object] = {}
    comments: dict[str, object] = {}
    for key, value, comment in section.properties:
        _combine(values, key, value)
        _combine(comments, key, comment)
    result: dict[str, object] = {"_header": section.header, "datablock": None}
    result.update(values)
    result["comments"] = comments
    return result


def _combine(target: dict[str, object], key: str, value: str | None) -> None:
    """Store a repeated key as a list instead of overwriting it."""
    if key not in target:
        target[key] = value
        return
    existing = target[key]
    if isinstance(existing, list):
        existing.append(value)
    else:
        target[key] = [existing, value]


def normalise_key(name: str) -> str:
    """Normalise a key the way HYDROLIB-core does (leading digits to words)."""
    match = _LEADING_DIGITS.match(name)
    if match:
        digits = "".join(_DIGIT_WORDS.get(char, char) for char in match.group())
        name = digits + name[match.end() :]
    return name.lower().replace(" ", "_").replace("-", "")


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
