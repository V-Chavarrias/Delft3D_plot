# SPDX-License-Identifier: GPL-3.0-or-later
"""Read residence-time metadata from D-WAQ substance files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from d3dplot.model_input.errors import ModelInputError
from d3dplot.model_input.text import read_model_text
from d3dplot.paths import resolve_path_preserving_drive

_TOKEN_RE = re.compile(r"#[^\n]*|'[^']*'|\S+")
_FORTRAN_EXPONENT_RE = re.compile(r"([\d.]+)[dD]([+-]?\d{1,3})")
_PARAMETER_KEYS = frozenset({"description", "unit", "value"})
_BLOCK_TERMINATORS = {
    "substance": "end-substance",
    "output": "end-output",
    "active-processes": "end-active-processes",
}
_SYNTAX_HINT = "Check the .sub syntax and the parameter values."
_RCDECTR_HINT = "Set RcDecTR parameters to positive decay rates."


@dataclass(frozen=True, slots=True)
class SubstanceInfo:
    """Decay rates read from ``RcDecTR<n>`` parameters."""

    decay_rates_s_1: tuple[float, ...]


def read_substance(path: Path) -> SubstanceInfo:
    """Read positive residence-time decay rates from a ``.sub`` file."""
    substance_path = resolve_path_preserving_drive(path)
    try:
        text = read_model_text(substance_path)
    except OSError as error:
        raise ModelInputError(
            substance_path,
            f"Could not read the substance file: {error}",
            "Check that the .sub file exists and is readable.",
        ) from error

    rates: list[float] = []
    for name, value in _read_parameters(_tokenize(text), substance_path):
        if not name.casefold().startswith("rcdectr"):
            continue
        if value <= 0:
            raise ModelInputError(
                substance_path,
                f"Parameter {name!r} must be positive.",
                _RCDECTR_HINT,
            )
        rates.append(value)
    return SubstanceInfo(decay_rates_s_1=tuple(rates))


def _tokenize(text: str) -> list[str]:
    """Split D-WAQ text into tokens, dropping comments and unquoting names."""
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(text):
        token = match.group()
        if token.startswith("#"):
            continue
        if len(token) >= 2 and token.startswith("'") and token.endswith("'"):
            token = token[1:-1]
        tokens.append(token)
    return tokens


def _read_parameters(tokens: list[str], path: Path) -> list[tuple[str, float]]:
    """Return ``(name, value)`` for every ``parameter`` block.

    ``substance``, ``output`` and ``active-processes`` blocks are skipped
    up to their terminator, so keywords inside them are not read.
    """
    parameters: list[tuple[str, float]] = []
    index = 0
    while index < len(tokens):
        keyword = tokens[index].casefold()
        if keyword == "parameter":
            name, raw_value, index = _read_parameter(tokens, index, path)
            parameters.append((name, _parse_value(name, raw_value, path)))
            continue
        terminator = _BLOCK_TERMINATORS.get(keyword)
        index += 1
        if terminator is None:
            continue
        while index < len(tokens) and tokens[index].casefold() != terminator:
            index += 1
        index += 1
    return parameters


def _read_parameter(
    tokens: list[str], start: int, path: Path
) -> tuple[str, str | None, int]:
    """Read one parameter block starting at the ``parameter`` keyword."""
    name = tokens[start + 1] if start + 1 < len(tokens) else ""
    if not name:
        raise ModelInputError(path, "A parameter has no name.", _SYNTAX_HINT)
    fields: dict[str, str] = {}
    index = start + 2
    while index < len(tokens) and tokens[index].casefold() != "end-parameter":
        key = tokens[index].casefold()
        if key not in _PARAMETER_KEYS:
            index += 1
            continue
        value_index = index + 1
        if (
            value_index < len(tokens)
            and tokens[value_index].casefold() != "end-parameter"
        ):
            fields[key] = tokens[value_index]
            index = value_index + 1
        else:
            fields[key] = ""
            index = value_index
    return name, fields.get("value"), index + 1


def _parse_value(name: str, raw_value: str | None, path: Path) -> float:
    """Convert a parameter value, accepting Fortran ``d`` exponents."""
    if raw_value is None:
        raise ModelInputError(path, f"Parameter {name!r} has no value.", _SYNTAX_HINT)
    try:
        return float(_FORTRAN_EXPONENT_RE.sub(r"\1e\2", raw_value))
    except ValueError as error:
        raise ModelInputError(
            path,
            f"Parameter {name!r} is not numeric.",
            _SYNTAX_HINT,
        ) from error
