# SPDX-License-Identifier: GPL-3.0-or-later
"""Numerical callbacks for registered derived quantities."""

from __future__ import annotations

from collections.abc import Mapping
from importlib import import_module
from typing import Any

from d3dplot.variables.context import (
    QuantityContext,
    QuantityContextError,
    QuantityRequest,
)

_Array = Any
np: Any = import_module("numpy")


def froude_number(
    inputs: Mapping[str, _Array],
    context: QuantityContext,
    request: QuantityRequest,
) -> _Array:
    """Return velocity magnitude over the gravity-wave celerity."""
    del request
    velocity, depth = _same_shape(inputs, "velocity_magnitude", "water_depth")
    valid = (
        np.isfinite(velocity)
        & np.isfinite(depth)
        & (depth >= context.dry_depth_m)
        & (depth > 0)
    )
    result = np.full(velocity.shape, np.nan, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        np.divide(
            velocity,
            np.sqrt(context.gravity_m_s2 * depth),
            out=result,
            where=valid,
        )
    return result


def specific_discharge(
    inputs: Mapping[str, _Array],
    context: QuantityContext,
    request: QuantityRequest,
) -> _Array:
    """Return depth-integrated discharge per unit width."""
    del context, request
    velocity, depth = _same_shape(inputs, "velocity_magnitude", "water_depth")
    return velocity * depth


def energy_head(
    inputs: Mapping[str, _Array],
    context: QuantityContext,
    request: QuantityRequest,
) -> _Array:
    """Return water level plus velocity head."""
    del request
    water_level, velocity = _same_shape(
        inputs,
        "water_level",
        "velocity_magnitude",
    )
    return water_level + velocity**2 / (2.0 * context.gravity_m_s2)


def shields_number_times_diameter(
    inputs: Mapping[str, _Array],
    context: QuantityContext,
    request: QuantityRequest,
) -> _Array:
    """Return Shields number multiplied by diameter for each sediment fraction."""
    del request
    if "bed_shear_stress" in inputs:
        shear_stress = np.asarray(inputs["bed_shear_stress"], dtype=np.float64)
    else:
        velocity, chezy = _same_shape(inputs, "velocity_magnitude", "chezy")
        shear_stress = np.full(velocity.shape, np.nan, dtype=np.float64)
        valid = np.isfinite(velocity) & np.isfinite(chezy) & (chezy > 0)
        with np.errstate(divide="ignore", invalid="ignore"):
            np.divide(
                context.water_density_kg_m3 * context.gravity_m_s2 * velocity**2,
                chezy**2,
                out=shear_stress,
                where=valid,
            )

    diameters = _sediment_diameters(context)
    densities = _sediment_densities(context)
    if densities.shape != diameters.shape:
        raise QuantityContextError(
            "Sediment density and diameter counts differ between model inputs."
        )
    relative_density = (densities - context.water_density_kg_m3) / (
        context.water_density_kg_m3
    )
    if np.any(relative_density <= 0):
        raise QuantityContextError(
            "Every sediment density must exceed the model water density."
        )
    denominator = context.gravity_m_s2 * context.water_density_kg_m3 * relative_density
    return shear_stress[..., np.newaxis] / denominator


def velocity_direction(
    inputs: Mapping[str, _Array],
    context: QuantityContext,
    request: QuantityRequest,
) -> _Array:
    """Return direction in degrees clockwise from geographic north."""
    del context, request
    eastward, northward = _same_shape(inputs, "mesh2d_ucx", "mesh2d_ucy")
    direction = np.degrees(np.arctan2(eastward, northward))
    return np.mod(direction, 360.0)


def velocity_vector(
    inputs: Mapping[str, _Array],
    context: QuantityContext,
    request: QuantityRequest,
) -> _Array:
    """Return eastward and northward velocity components."""
    del context, request
    eastward, northward = _same_shape(inputs, "mesh2d_ucx", "mesh2d_ucy")
    return np.stack((eastward, northward), axis=-1)


def water_level_rate(
    inputs: Mapping[str, _Array],
    context: QuantityContext,
    request: QuantityRequest,
) -> _Array:
    """Return water-level change per output interval."""
    del request
    current = np.asarray(inputs["water_level"], dtype=np.float64)
    previous = inputs.get("mesh2d_s0", context.previous_water_level)
    elapsed = context.elapsed_time_seconds
    if previous is None or elapsed is None:
        return np.full(current.shape, np.nan, dtype=np.float64)
    previous_values = np.asarray(previous, dtype=np.float64)
    if previous_values.shape != current.shape:
        raise QuantityContextError(
            "Current and previous water-level arrays have different shapes."
        )
    return (current - previous_values) / elapsed


def vorticity(
    inputs: Mapping[str, _Array],
    context: QuantityContext,
    request: QuantityRequest,
) -> _Array:
    """Return counter-clockwise-positive face circulation per unit area."""
    del request
    if context.grid_geometry is None:
        raise QuantityContextError("Vorticity requires inspected grid geometry.")
    return context.grid_geometry.vorticity(inputs["mesh2d_u1"])


def streamfunction(
    inputs: Mapping[str, _Array],
    context: QuantityContext,
    request: QuantityRequest,
) -> _Array:
    """Return a face streamfunction with the first face fixed as its gauge."""
    del request
    if context.grid_geometry is None:
        raise QuantityContextError("Streamfunction requires inspected grid geometry.")
    velocity = np.asarray(inputs["mesh2d_u1"], dtype=np.float64)
    return context.grid_geometry.solve_streamfunction(
        velocity * context.grid_geometry.edge_length
    )


def transport_streamfunction(
    inputs: Mapping[str, _Array],
    context: QuantityContext,
    request: QuantityRequest,
) -> _Array:
    """Return a transport streamfunction, treating missing edge depth as zero."""
    del request
    if context.grid_geometry is None:
        raise QuantityContextError(
            "Transport streamfunction requires inspected grid geometry."
        )
    velocity = np.asarray(inputs["mesh2d_u1"], dtype=np.float64)
    water_depth = np.asarray(inputs["mesh2d_hu"], dtype=np.float64)
    if velocity.shape != water_depth.shape:
        raise QuantityContextError(
            "Edge velocity and edge water-depth arrays have different shapes."
        )
    depth_for_flux = np.where(np.isnan(water_depth), 0.0, water_depth)
    flux = velocity * depth_for_flux * context.grid_geometry.edge_length
    return context.grid_geometry.solve_streamfunction(flux)


def total_sediment_thickness(
    inputs: Mapping[str, _Array],
    context: QuantityContext,
    request: QuantityRequest,
) -> _Array:
    """Sum bed-layer thicknesses for every face."""
    del context, request
    return np.sum(np.asarray(inputs["layer_thickness"], dtype=np.float64), axis=-1)


def morphological_active_area(
    inputs: Mapping[str, _Array],
    context: QuantityContext,
    request: QuantityRequest,
) -> _Array:
    """Return face area where sediment thickness reaches the active threshold."""
    del context
    area, thickness = _same_shape(
        inputs,
        "cell_area",
        "total_sediment_thickness",
    )
    active = thickness >= request.active_sediment_thickness_threshold_m
    values = np.where(active, area, 0.0)
    return np.where(np.isfinite(thickness), values, np.nan)


def sediment_transport_magnitude(
    inputs: Mapping[str, _Array],
    context: QuantityContext,
    request: QuantityRequest,
) -> _Array:
    """Return total suspended-load magnitude per fraction."""
    del context, request
    x_component, y_component = _same_shape(
        inputs,
        "mesh2d_sxtot",
        "mesh2d_sytot",
    )
    return np.hypot(x_component, y_component)


def sediment_transport_magnitude_sum(
    inputs: Mapping[str, _Array],
    context: QuantityContext,
    request: QuantityRequest,
) -> _Array:
    """Sum the per-fraction suspended-load magnitudes."""
    del context, request
    return np.sum(
        np.asarray(inputs["sediment_transport_magnitude"], dtype=np.float64),
        axis=-1,
    )


def bed_load_transport_magnitude(
    inputs: Mapping[str, _Array],
    context: QuantityContext,
    request: QuantityRequest,
) -> _Array:
    """Return bed-load magnitude per sediment fraction."""
    del context, request
    x_component, y_component = _same_shape(
        inputs,
        "mesh2d_sbcx",
        "mesh2d_sbcy",
    )
    return np.hypot(x_component, y_component)


def mean_grain_size_arithmetic(
    inputs: Mapping[str, _Array],
    context: QuantityContext,
    request: QuantityRequest,
) -> _Array:
    """Return the arithmetic fraction-weighted mean diameter."""
    del request
    fractions = np.asarray(inputs["layer_fraction"], dtype=np.float64)
    diameters = _diameters_for_fractions(context, fractions)
    return np.sum(fractions * diameters, axis=-1)


def mean_grain_size_geometric(
    inputs: Mapping[str, _Array],
    context: QuantityContext,
    request: QuantityRequest,
) -> _Array:
    """Return the base-two weighted geometric mean diameter."""
    del request
    fractions = np.asarray(inputs["layer_fraction"], dtype=np.float64)
    diameters = _diameters_for_fractions(context, fractions)
    reference_diameter = 1e-3
    exponent = np.sum(
        fractions * np.log2(diameters / reference_diameter),
        axis=-1,
    )
    return reference_diameter * np.exp2(exponent)


def grain_size_percentile(
    inputs: Mapping[str, _Array],
    context: QuantityContext,
    request: QuantityRequest,
) -> _Array:
    """Interpolate a grain-size percentile linearly in diameter."""
    percentile = request.percentile
    if percentile is None:
        raise QuantityContextError(
            "Request grain_size_percentile using one of its d10, d50, or d90 aliases."
        )
    fractions = np.asarray(inputs["layer_fraction"], dtype=np.float64)
    diameters = _sediment_diameters(context)
    if fractions.shape[-1] != diameters.size:
        raise QuantityContextError(
            "The sediment fraction count does not match the .sed file."
        )
    if np.any(fractions < 0):
        raise QuantityContextError("Sediment fractions cannot be negative.")

    cumulative = np.cumsum(fractions, axis=-1)
    total = cumulative[..., -1]
    valid = np.all(np.isfinite(fractions), axis=-1) & (np.abs(total - 1.0) <= 0.001)
    percentile_fraction = percentile / 100.0
    padded_cumulative = np.concatenate(
        (
            np.zeros((*cumulative.shape[:-1], 1), dtype=np.float64),
            cumulative,
            np.full((*cumulative.shape[:-1], 1), 1.0 + 1e-8, dtype=np.float64),
        ),
        axis=-1,
    )
    first_diameter = diameters[0]
    last_diameter = diameters[-1]
    padded_diameters = np.concatenate(
        (
            np.asarray([first_diameter / 2.0]),
            diameters,
            np.asarray([2.0 * last_diameter]),
        )
    )
    left = np.sum(padded_cumulative <= percentile_fraction, axis=-1) - 1
    left = np.clip(left, 0, padded_diameters.size - 2)
    right = left + 1
    cumulative_left = np.take_along_axis(
        padded_cumulative,
        left[..., np.newaxis],
        axis=-1,
    )[..., 0]
    cumulative_right = np.take_along_axis(
        padded_cumulative,
        right[..., np.newaxis],
        axis=-1,
    )[..., 0]
    diameter_left = padded_diameters[left]
    diameter_right = padded_diameters[right]
    fraction = (percentile_fraction - cumulative_left) / (
        cumulative_right - cumulative_left
    )
    value = diameter_left + fraction * (diameter_right - diameter_left)
    return np.where(valid, value, np.nan)


def _same_shape(inputs: Mapping[str, _Array], *names: str) -> tuple[_Array, ...]:
    arrays = tuple(np.asarray(inputs[name], dtype=np.float64) for name in names)
    if any(array.shape != arrays[0].shape for array in arrays[1:]):
        raise QuantityContextError(
            f"Quantity inputs have different shapes: {', '.join(names)}."
        )
    return arrays


def _sediment_diameters(context: QuantityContext) -> _Array:
    return _sediment_properties(
        context.sediment_diameters_m,
        "sediment diameters",
    )


def _sediment_densities(context: QuantityContext) -> _Array:
    return _sediment_properties(
        context.sediment_densities_kg_m3,
        "sediment densities",
    )


def _sediment_properties(
    values: tuple[float | None, ...],
    label: str,
) -> _Array:
    if not values or any(value is None for value in values):
        raise QuantityContextError(f"Valid {label} are required for this quantity.")
    result = np.asarray(values, dtype=np.float64)
    if np.any(~np.isfinite(result)) or np.any(result <= 0):
        raise QuantityContextError(f"Every {label} value must be finite and positive.")
    return result


def _diameters_for_fractions(context: QuantityContext, fractions: _Array) -> _Array:
    diameters = _sediment_diameters(context)
    if fractions.shape[-1] != diameters.size:
        raise QuantityContextError(
            "The sediment fraction count does not match the .sed file."
        )
    return diameters.reshape((1,) * (fractions.ndim - 1) + (diameters.size,))
