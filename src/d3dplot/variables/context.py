# SPDX-License-Identifier: GPL-3.0-or-later
"""Model and request metadata used by derived quantity callbacks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any

from d3dplot.model_input.reader import ModelInput
from d3dplot.variables.geometry import GridGeometry

_Array = Any


class QuantityContextError(ValueError):
    """A derived quantity lacks valid model metadata."""

    code = "V-VAR-002"


@dataclass(frozen=True, slots=True)
class QuantityContext:
    """Simulation-level constants and optional state for a map output."""

    gravity_m_s2: float = 9.81
    water_density_kg_m3: float = 1000.0
    sediment_diameters_m: tuple[float | None, ...] = ()
    sediment_densities_kg_m3: tuple[float | None, ...] = ()
    dry_depth_m: float = 0.01
    grid_geometry: GridGeometry | None = None
    previous_water_level: _Array | None = None
    current_time: datetime | None = None
    previous_time: datetime | None = None
    timestep_seconds: float | _Array | None = None

    def __post_init__(self) -> None:
        if not isfinite(self.gravity_m_s2) or self.gravity_m_s2 <= 0:
            raise ValueError("Gravity must be finite and positive.")
        if not isfinite(self.water_density_kg_m3) or self.water_density_kg_m3 <= 0:
            raise ValueError("Water density must be finite and positive.")
        if not isfinite(self.dry_depth_m) or self.dry_depth_m < 0:
            raise ValueError("Dry depth must be finite and non-negative.")
        if self.previous_time is not None and self.current_time is None:
            raise ValueError("A previous time cannot be set without the current time.")
        for value in (self.current_time, self.previous_time):
            if value is not None and (
                value.tzinfo is None or value.utcoffset() is None
            ):
                raise ValueError("Quantity context times must be timezone-aware.")
        if self.current_time is not None and self.previous_time is not None:
            if self.current_time <= self.previous_time:
                raise ValueError("Current time must follow previous time.")

    @classmethod
    def from_model_input(
        cls,
        model_input: ModelInput,
        *,
        grid_geometry: GridGeometry | None = None,
        dry_depth_m: float = 0.01,
    ) -> QuantityContext:
        """Create a context from the simulation's parsed MDU and sediment file."""
        fractions = (
            model_input.sediment.fractions if model_input.sediment is not None else ()
        )
        return cls(
            gravity_m_s2=model_input.mdu.gravity,
            water_density_kg_m3=model_input.mdu.water_density,
            sediment_diameters_m=tuple(item.diameter_m for item in fractions),
            sediment_densities_kg_m3=tuple(
                item.solid_density_kg_m3 for item in fractions
            ),
            dry_depth_m=dry_depth_m,
            grid_geometry=grid_geometry,
        )

    @property
    def elapsed_time_seconds(self) -> float | None:
        """Return the hydrodynamic interval to the previous output, if available."""
        if self.current_time is None or self.previous_time is None:
            return None
        return (self.current_time - self.previous_time).total_seconds()


@dataclass(frozen=True, slots=True)
class QuantityRequest:
    """A quantity spelling and the per-analysis selections applied to it."""

    requested_name: str
    fractions: tuple[int, ...] | None = None
    layers: tuple[int, ...] | None = None
    sum_fractions: bool = False
    active_sediment_thickness_threshold_m: float = 0.001

    def __post_init__(self) -> None:
        if (
            not self.requested_name
            or self.requested_name != self.requested_name.strip()
        ):
            raise ValueError("A quantity request needs a non-empty trimmed name.")
        if self.fractions is not None and any(index < 1 for index in self.fractions):
            raise ValueError("Fraction indices must be one-based positive integers.")
        if self.layers is not None and any(index < 0 for index in self.layers):
            raise ValueError("Layer indices must be zero-based non-negative integers.")
        threshold = self.active_sediment_thickness_threshold_m
        if not isfinite(threshold) or threshold < 0:
            raise ValueError(
                "Active-sediment thickness threshold must be finite and non-negative."
            )

    @property
    def percentile(self) -> float | None:
        """Return the percentile encoded by a Matlab D10/D50/D90 alias."""
        return {
            "d10": 10.0,
            "d50": 50.0,
            "d90": 90.0,
        }.get(self.requested_name.casefold())

    @property
    def cache_key(self) -> tuple[object, ...]:
        """Return the immutable request dimensions used by the per-time cache."""
        return (
            self.requested_name.casefold(),
            self.fractions,
            self.layers,
            self.sum_fractions,
            self.active_sediment_thickness_threshold_m,
        )
