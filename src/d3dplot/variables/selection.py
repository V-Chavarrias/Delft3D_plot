# SPDX-License-Identifier: GPL-3.0-or-later
"""Stable labels for quantity-selection cache paths."""

from __future__ import annotations

from d3dplot.variables.context import QuantityRequest
from d3dplot.variables.registry import UnknownVariableError, get_variable_spec


def quantity_selection_label(
    request: QuantityRequest,
    *,
    variable_name: str | None = None,
) -> str:
    """Build the canonical selection label shared by analysis data caches."""
    if variable_name is None:
        try:
            variable_name = get_variable_spec(request.requested_name).name
        except UnknownVariableError:
            variable_name = request.requested_name.casefold()

    parts: list[str] = []
    if request.fractions is not None:
        parts.append("fraction-" + "-".join(str(index) for index in request.fractions))
    if request.layers is not None:
        parts.append("layer-" + "-".join(str(index) for index in request.layers))
    if request.sum_fractions:
        parts.append("sum-fractions")
    if variable_name == "morphological_active_area":
        threshold = format(request.active_sediment_thickness_threshold_m, ".8g")
        parts.append(f"active-thickness-{threshold.replace('.', 'p')}m")
    return "-".join(parts) or "all"


__all__ = ["quantity_selection_label"]
