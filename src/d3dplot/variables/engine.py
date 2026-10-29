# SPDX-License-Identifier: GPL-3.0-or-later
"""Resolve and compute model quantities from one checked map output."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from importlib import import_module
from importlib.metadata import entry_points
from typing import Any, NoReturn

from d3dplot.simulation.map_reader import MapReaderError, MapReaderSession
from d3dplot.simulation.time_axis import TimeEntry
from d3dplot.validate.report import ValidationIssue
from d3dplot.variables.context import (
    QuantityContext,
    QuantityContextError,
    QuantityRequest,
)
from d3dplot.variables.geometry import GridGeometryError
from d3dplot.variables.registry import (
    VARIABLE_SPECS,
    SelectionTarget,
    UnknownVariableError,
    VariableKind,
    VariableLocation,
    VariableSpec,
    _build_registry,
    suggest_variable_names,
)

_Array = Any
np: Any = import_module("numpy")


class QuantityEngineError(RuntimeError):
    """A quantity could not be resolved or computed with a stable issue."""

    def __init__(self, issue: ValidationIssue) -> None:
        self.issue = issue
        super().__init__(issue.message)


class QuantityEngine:
    """Build dependency graphs and open one read cache per map output."""

    def __init__(
        self,
        specs: Iterable[VariableSpec] | None = None,
        *,
        load_plugins: bool = True,
    ) -> None:
        registered_specs = tuple(VARIABLE_SPECS if specs is None else specs)
        if specs is None and load_plugins:
            registered_specs += load_quantity_plugins()
        self._by_name, self._by_identifier = _build_registry(registered_specs)

    def at_time(
        self,
        map_session: MapReaderSession,
        entry: TimeEntry,
        context: QuantityContext,
    ) -> QuantityTimeSession:
        """Create a per-time cache shared by every analysis in the process task."""
        if map_session.subrun_index != entry.subrun:
            raise ValueError(
                "MapReader session and selected time belong to different sub-runs."
            )
        if context.current_time is not None and context.current_time != entry.time:
            raise ValueError(
                "Quantity context time does not match the selected map output."
            )
        return QuantityTimeSession(self, map_session, entry, context)


class QuantityTimeSession:
    """Resolve requests while retaining raw reads for exactly one map output."""

    def __init__(
        self,
        engine: QuantityEngine,
        map_session: MapReaderSession,
        entry: TimeEntry,
        context: QuantityContext,
    ) -> None:
        self._engine = engine
        self._map_session = map_session
        self._entry = entry
        self._context = context
        self._raw_cache: dict[str, _Array] = {}
        self._quantity_cache: dict[
            tuple[str, tuple[object, ...], bool],
            _Array,
        ] = {}
        self.issues: list[ValidationIssue] = []
        self.choices: list[str] = []

    def get(self, request: str | QuantityRequest) -> _Array:
        """Return one quantity, preserving any alias-specific parameters."""
        spec = self.resolve_spec(request)
        normalized_request = (
            QuantityRequest(requested_name=request)
            if isinstance(request, str)
            else request
        )
        values = self._compute(
            spec,
            normalized_request,
            chain=(spec.name,),
            apply_selection=True,
        )
        if normalized_request.sum_fractions:
            values = _sum_fractions(values, spec)
        return np.asarray(values, dtype=np.float64)

    def resolve_spec(self, request: str | QuantityRequest) -> VariableSpec:
        """Resolve request metadata, including plug-in and present raw quantities."""
        normalized_request = (
            QuantityRequest(requested_name=request)
            if isinstance(request, str)
            else request
        )
        return self._resolve_requested_spec(normalized_request.requested_name)

    def _resolve_requested_spec(self, name: str) -> VariableSpec:
        spec = self._engine._by_identifier.get(name.strip().casefold())
        if spec is not None:
            return spec
        direct = _unregistered_raw_spec(name)
        if self._map_session.has_variable(direct):
            return direct
        raise UnknownVariableError(name, suggest_variable_names(name))

    def _resolve_dependency_spec(
        self,
        name: str,
        parent: VariableSpec,
    ) -> VariableSpec:
        spec = self._engine._by_identifier.get(name.casefold())
        if spec is not None:
            return spec
        return _unregistered_raw_spec(
            name,
            location=parent.location,
            units=parent.units,
            label_key=parent.label_key,
        )

    def _compute(
        self,
        spec: VariableSpec,
        request: QuantityRequest,
        *,
        chain: tuple[str, ...],
        apply_selection: bool,
    ) -> _Array:
        cache_key = (spec.name, request.cache_key, apply_selection)
        cached = self._quantity_cache.get(cache_key)
        if cached is not None:
            return cached

        if spec.kind is VariableKind.RAW:
            values = self._read_raw(spec, chain)
            if apply_selection:
                values = _select_values(values, spec, request, self)
        else:
            if spec.compute is None:
                self._raise_missing(
                    chain,
                    spec.name,
                    "No compute callback is registered for this quantity.",
                )
            self._check_requirements(spec, chain)
            input_names = self._resolve_input_names(spec, chain)
            inputs: dict[str, _Array] = {}
            for name in input_names:
                dependency = self._resolve_dependency_spec(name, spec)
                value = self._compute(
                    dependency,
                    request,
                    chain=(*chain, dependency.name),
                    apply_selection=False,
                )
                if apply_selection and spec.selection is SelectionTarget.INPUTS:
                    value = _select_values(value, dependency, request, self)
                inputs[name] = value
            try:
                values = np.asarray(
                    spec.compute(inputs, self._context, request),
                    dtype=np.float64,
                )
            except QuantityContextError as error:
                self._raise_missing(chain, spec.name, str(error))
            except GridGeometryError as error:
                raise QuantityEngineError(
                    ValidationIssue(
                        code=error.code,
                        level="error",
                        location=f"sub-run {self._entry.subrun}",
                        message=f"{_format_chain(chain)}: {error}",
                        hint=(
                            "Rebuild the inspected grid cache or restore valid "
                            "geometry."
                        ),
                    )
                ) from error
            if values.ndim != 1 + len(spec.extra_dims):
                raise QuantityEngineError(
                    ValidationIssue(
                        code="V-VAR-004",
                        level="error",
                        location=f"sub-run {self._entry.subrun}",
                        message=(
                            f"Computed quantity {_format_chain(chain)} has shape "
                            f"{values.shape}; expected a face axis and "
                            f"{len(spec.extra_dims)} extra dimension(s)."
                        ),
                        hint="Correct the quantity callback or its VariableSpec.",
                    )
                )
            if apply_selection and spec.selection is SelectionTarget.OUTPUT:
                values = _select_values(values, spec, request, self)

            if spec.name == "water_level_rate":
                if self._context.elapsed_time_seconds is None:
                    choice = (
                        "water_level_rate has no preceding map output; "
                        "the first result is NaN"
                    )
                elif "mesh2d_s0" in inputs:
                    choice = "water_level_rate used mesh2d_s0 and the map-time interval"
                elif self._context.previous_water_level is not None:
                    choice = (
                        "water_level_rate used the preceding map output's "
                        "mesh2d_s1 and the map-time interval"
                    )
                else:
                    choice = "water_level_rate has no preceding water-level values"
                self.choices.append(choice)

        self._quantity_cache[cache_key] = values
        return values

    def _read_raw(self, spec: VariableSpec, chain: tuple[str, ...]) -> _Array:
        cached = self._raw_cache.get(spec.name)
        if cached is not None:
            return cached
        try:
            result = self._map_session.read(
                spec,
                local_index=self._entry.local_index,
                corrupt_partitions=self._entry.corrupt_partitions,
            )
        except MapReaderError as error:
            if error.issue.code != "V-VAR-002":
                raise
            self._raise_missing(chain, spec.name, error.issue.message)
        values = np.asarray(result.values, dtype=np.float64)
        if spec.oriented:
            orientation = result.edge_orientation
            if orientation is None or orientation.shape != (values.shape[0],):
                raise QuantityEngineError(
                    ValidationIssue(
                        code="V-OUT-020",
                        level="error",
                        location=f"sub-run {self._entry.subrun}",
                        message=(
                            f"Directed edge quantity {_format_chain(chain)} has no "
                            "matching global orientation array."
                        ),
                        hint="Rebuild the edge merge plan for the inspected grid.",
                    )
                )
            values = values * orientation.reshape(
                (orientation.size,) + (1,) * (values.ndim - 1)
            )
        self.issues.extend(result.issues)
        self._raw_cache[spec.name] = values
        return values

    def _resolve_input_names(
        self,
        spec: VariableSpec,
        chain: tuple[str, ...],
    ) -> tuple[str, ...]:
        names = list(spec.inputs)
        if spec.input_alternatives:
            selected = next(
                (
                    alternative
                    for alternative in spec.input_alternatives
                    if all(
                        self._is_available(name, spec, set()) for name in alternative
                    )
                ),
                None,
            )
            if selected is None:
                alternatives = " or ".join(
                    "(" + ", ".join(group) + ")" for group in spec.input_alternatives
                )
                self._raise_missing(
                    chain,
                    spec.name,
                    f"None of the input alternatives are available: {alternatives}.",
                )
            self.choices.append(f"{spec.name} used inputs: {', '.join(selected)}")
            names.extend(selected)
        for optional_name in spec.optional_inputs:
            if self._is_available(optional_name, spec, set()):
                names.append(optional_name)
        for name in spec.inputs:
            if not self._is_available(name, spec, set()):
                self._raise_missing(
                    chain,
                    name,
                    f"Required input {name!r} is not available.",
                )
        return tuple(names)

    def _is_available(
        self,
        name: str,
        parent: VariableSpec,
        visited: set[str],
    ) -> bool:
        if name in visited:
            return False
        visited.add(name)
        dependency = self._resolve_dependency_spec(name, parent)
        if dependency.kind is VariableKind.RAW:
            return self._map_session.has_variable(dependency)
        if dependency.compute is None or not self._has_requirements(dependency):
            return False
        if not all(
            self._is_available(child, dependency, visited.copy())
            for child in dependency.inputs
        ):
            return False
        if dependency.input_alternatives and not any(
            all(
                self._is_available(child, dependency, visited.copy())
                for child in alternative
            )
            for alternative in dependency.input_alternatives
        ):
            return False
        return True

    def _check_requirements(
        self,
        spec: VariableSpec,
        chain: tuple[str, ...],
    ) -> None:
        if not self._has_requirements(spec):
            missing = tuple(
                requirement
                for requirement in spec.requires
                if not self._requirement_available(requirement)
            )
            self._raise_missing(
                chain,
                spec.name,
                "Required model metadata is unavailable: " + ", ".join(missing),
            )

    def _has_requirements(self, spec: VariableSpec) -> bool:
        return all(
            self._requirement_available(requirement) for requirement in spec.requires
        )

    def _requirement_available(self, requirement: str) -> bool:
        context = self._context
        if requirement in ("grid_geometry", "sparse_laplacian_factorization"):
            return context.grid_geometry is not None
        if requirement == "gravity":
            return context.gravity_m_s2 > 0
        if requirement == "water_density":
            return context.water_density_kg_m3 > 0
        if requirement == "dry_depth_m":
            return context.dry_depth_m >= 0
        if requirement == "map_times":
            return context.current_time is not None
        if requirement == "sediment_diameters":
            return bool(context.sediment_diameters_m) and all(
                value is not None for value in context.sediment_diameters_m
            )
        if requirement == "sediment_density":
            return bool(context.sediment_densities_kg_m3) and all(
                value is not None for value in context.sediment_densities_kg_m3
            )
        return False

    def _raise_missing(
        self,
        chain: tuple[str, ...],
        missing_name: str,
        message: str,
    ) -> NoReturn:
        full_chain = chain if chain[-1] == missing_name else (*chain, missing_name)
        raise QuantityEngineError(
            ValidationIssue(
                code="V-VAR-002",
                level="error",
                location=f"sub-run {self._entry.subrun}",
                message=(
                    f"Required quantity dependency {_format_chain(full_chain)} "
                    f"is unavailable: {message}"
                ),
                hint="Restore the required map variable or model input metadata.",
            )
        )


def load_quantity_plugins() -> tuple[VariableSpec, ...]:
    """Load external quantity providers from the ``d3dplot.quantities`` group."""
    loaded_specs: list[VariableSpec] = []
    for entry_point in entry_points(group="d3dplot.quantities"):
        provider = entry_point.load()
        provided = provider() if callable(provider) else provider
        if isinstance(provided, VariableSpec):
            loaded_specs.append(provided)
            continue
        try:
            plugin_specs = tuple(provided)
        except TypeError as error:
            raise TypeError(
                f"Quantity entry point {entry_point.name!r} must return "
                "VariableSpec instances."
            ) from error
        if not all(isinstance(spec, VariableSpec) for spec in plugin_specs):
            raise TypeError(
                f"Quantity entry point {entry_point.name!r} returned a "
                "non-VariableSpec."
            )
        loaded_specs.extend(plugin_specs)
    return tuple(loaded_specs)


def _unregistered_raw_spec(
    name: str,
    *,
    location: VariableLocation = VariableLocation.FACE,
    units: str = "1",
    label_key: str | None = None,
) -> VariableSpec:
    return VariableSpec(
        name=name,
        kind=VariableKind.RAW,
        aliases=(),
        netcdf_candidates=(name,),
        location=location,
        extra_dims=(),
        units=units,
        label_key=label_key or name,
    )


def _select_values(
    values: _Array,
    spec: VariableSpec,
    request: QuantityRequest,
    session: QuantityTimeSession,
) -> _Array:
    result = np.asarray(values, dtype=np.float64)
    for dimension, indices, one_based in (
        ("nSedTot", request.fractions, True),
        ("nBedLayers", request.layers, True),
    ):
        if indices is None or dimension not in spec.extra_dims:
            continue
        axis = 1 + spec.extra_dims.index(dimension)
        selected_indices = np.asarray(indices, dtype=np.int64)
        if one_based:
            selected_indices = selected_indices - 1
        if np.any(selected_indices >= result.shape[axis]):
            raise QuantityEngineError(
                ValidationIssue(
                    code="V-VAR-003",
                    level="error",
                    location=f"sub-run {session._entry.subrun}",
                    message=(
                        f"Requested {dimension} index exceeds the available "
                        f"dimension of size {result.shape[axis]}."
                    ),
                    hint="Choose an in-range fraction or layer index.",
                )
            )
        result = np.take(result, selected_indices, axis=axis)
    return result


def _sum_fractions(
    values: _Array,
    spec: VariableSpec,
) -> _Array:
    if "nSedTot" not in spec.extra_dims:
        return values
    axis = 1 + spec.extra_dims.index("nSedTot")
    return np.sum(values, axis=axis)


def _format_chain(chain: Sequence[str]) -> str:
    return " -> ".join(chain)


__all__ = [
    "QuantityEngine",
    "QuantityEngineError",
    "QuantityTimeSession",
    "load_quantity_plugins",
]
