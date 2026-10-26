# SPDX-License-Identifier: GPL-3.0-or-later
"""Canonical variable metadata and name resolution."""

from d3dplot.variables.registry import (
    SelectionTarget,
    UnknownVariableError,
    VariableKind,
    VariableLocation,
    VariableSpec,
    get_variable_spec,
    list_variable_specs,
    render_variable_catalogue,
    suggest_variable_names,
)

__all__ = [
    "SelectionTarget",
    "UnknownVariableError",
    "VariableKind",
    "VariableLocation",
    "VariableSpec",
    "get_variable_spec",
    "list_variable_specs",
    "render_variable_catalogue",
    "suggest_variable_names",
]
