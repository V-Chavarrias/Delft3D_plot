# SPDX-License-Identifier: GPL-3.0-or-later
"""Sampling protocols used by the central quantity engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from d3dplot.variables.geometry import GridGeometry

SamplingPlan = Any


class Sampling(Protocol):
    """Prepare and apply a sampling operation to global quantity arrays."""

    def fingerprint(self) -> str:
        """Return the stable identity of this sampling operation."""

    def prepare(
        self,
        grid: GridGeometry | None,
        merge_plan: object | None,
    ) -> SamplingPlan:
        """Prepare a plan once for one inspected grid and merge plan."""

    def apply(self, values: Any, plan: SamplingPlan) -> Any:
        """Apply the prepared plan to a global quantity array."""


@dataclass(frozen=True, slots=True)
class GridSampling:
    """Default sampling that retains all global grid faces or edges."""

    kind: str = "grid"

    def fingerprint(self) -> str:
        """Return the stable identity of grid sampling."""
        return "grid-v1"

    def prepare(
        self,
        grid: GridGeometry | None,
        merge_plan: object | None,
    ) -> GridSampling:
        """Return the immutable no-op plan."""
        del grid, merge_plan
        return self

    def apply(self, values: Any, plan: SamplingPlan) -> Any:
        """Return global values unchanged."""
        if plan is not self and not isinstance(plan, GridSampling):
            raise ValueError("GridSampling received an incompatible sampling plan.")
        return values


__all__ = ["GridSampling", "Sampling", "SamplingPlan"]
