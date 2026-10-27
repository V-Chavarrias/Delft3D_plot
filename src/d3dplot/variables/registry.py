# SPDX-License-Identifier: GPL-3.0-or-later
"""Static metadata catalog for model quantities."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from difflib import get_close_matches
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from d3dplot.variables.compute import (
    bed_load_transport_magnitude,
    energy_head,
    froude_number,
    grain_size_percentile,
    mean_grain_size_arithmetic,
    mean_grain_size_geometric,
    morphological_active_area,
    sediment_transport_magnitude,
    sediment_transport_magnitude_sum,
    shields_number_times_diameter,
    specific_discharge,
    streamfunction,
    total_sediment_thickness,
    transport_streamfunction,
    velocity_direction,
    velocity_vector,
    vorticity,
    water_level_rate,
)

if TYPE_CHECKING:
    from d3dplot.variables.context import QuantityContext, QuantityRequest

QuantityCompute = Callable[
    [Mapping[str, Any], "QuantityContext", "QuantityRequest"],
    Any,
]


class VariableKind(StrEnum):
    """Whether a quantity is read from NetCDF or computed from other inputs."""

    RAW = "raw"
    DERIVED = "derived"


class VariableLocation(StrEnum):
    """Grid location occupied by a quantity."""

    FACE = "face"
    EDGE = "edge"
    LINE = "line"


class SelectionTarget(StrEnum):
    """Where vertical and fraction selections are applied to derived values."""

    INPUTS = "inputs"
    OUTPUT = "output"


@dataclass(frozen=True, slots=True)
class VariableSpec:
    """Metadata used to resolve and describe one canonical model quantity."""

    name: str
    kind: VariableKind
    aliases: tuple[str, ...]
    netcdf_candidates: tuple[str, ...]
    location: VariableLocation
    extra_dims: tuple[str, ...]
    units: str
    label_key: str
    inputs: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    optional_inputs: tuple[str, ...] = ()
    input_alternatives: tuple[tuple[str, ...], ...] = ()
    selection: SelectionTarget | None = None
    version: str | None = None
    compute: QuantityCompute | None = field(default=None, repr=False, compare=False)
    oriented: bool = False

    def __post_init__(self) -> None:
        if not self.name or self.name != self.name.strip():
            raise ValueError("Variable names must be non-empty and trimmed.")
        if not self.units or not self.label_key:
            raise ValueError("Variable units and label_key must be non-empty.")

        identifiers = (*self.aliases, *self.netcdf_candidates)
        if any(
            not identifier or identifier != identifier.strip()
            for identifier in identifiers
        ):
            raise ValueError("Variable identifiers must be non-empty and trimmed.")
        normalized_identifiers = [identifier.casefold() for identifier in identifiers]
        if self.name.casefold() in {alias.casefold() for alias in self.aliases} or len(
            set(normalized_identifiers)
        ) != len(normalized_identifiers):
            raise ValueError(f"Variable {self.name!r} repeats an identifier.")

        if any(not dim or dim != dim.strip() for dim in self.extra_dims):
            raise ValueError("Variable dimensions must be non-empty and trimmed.")
        if len(set(self.extra_dims)) != len(self.extra_dims):
            raise ValueError(f"Variable {self.name!r} repeats an extra dimension.")
        if self.oriented and self.location is not VariableLocation.EDGE:
            raise ValueError("Only edge-located raw variables can be oriented.")

        if self.kind is VariableKind.RAW:
            if not self.netcdf_candidates:
                raise ValueError(f"Raw variable {self.name!r} needs NetCDF candidates.")
            if (
                self.inputs
                or self.requires
                or self.optional_inputs
                or self.input_alternatives
                or self.selection is not None
                or self.version
                or self.compute is not None
            ):
                raise ValueError(f"Raw variable {self.name!r} has derived metadata.")
            return

        if self.oriented:
            raise ValueError("Only raw edge variables can be oriented.")
        if self.netcdf_candidates or not (self.inputs or self.input_alternatives):
            raise ValueError(
                f"Derived variable {self.name!r} needs inputs and no NetCDF candidates."
            )
        if self.selection is None or not self.version:
            raise ValueError(
                f"Derived variable {self.name!r} needs selection and version metadata."
            )
        if any(not alternative for alternative in self.input_alternatives):
            raise ValueError(
                f"Derived variable {self.name!r} has an empty input alternative."
            )
        dependencies = (
            *self.inputs,
            *self.optional_inputs,
            *(name for group in self.input_alternatives for name in group),
        )
        if any(not name or name != name.strip() for name in dependencies):
            raise ValueError(
                f"Derived variable {self.name!r} has an invalid input name."
            )


class UnknownVariableError(LookupError):
    """Raised when a variable name is not registered."""

    code = "V-VAR-001"

    def __init__(self, name: str, suggestions: tuple[str, ...] = ()) -> None:
        self.name = name
        self.suggestions = suggestions
        message = f"Unknown variable {name!r}"
        if suggestions:
            choices = ", ".join(repr(suggestion) for suggestion in suggestions)
            message += f". Did you mean {choices}?"
        super().__init__(message)


def _raw_spec(
    name: str,
    netcdf_candidates: tuple[str, ...],
    *,
    units: str,
    label_key: str,
    aliases: tuple[str, ...] = (),
    extra_dims: tuple[str, ...] = (),
    location: VariableLocation = VariableLocation.FACE,
    oriented: bool = False,
) -> VariableSpec:
    return VariableSpec(
        name=name,
        kind=VariableKind.RAW,
        aliases=aliases,
        netcdf_candidates=netcdf_candidates,
        location=location,
        extra_dims=extra_dims,
        units=units,
        label_key=label_key,
        oriented=oriented,
    )


def _derived_spec(
    name: str,
    inputs: tuple[str, ...],
    *,
    units: str,
    label_key: str,
    aliases: tuple[str, ...] = (),
    extra_dims: tuple[str, ...] = (),
    requires: tuple[str, ...] = (),
    optional_inputs: tuple[str, ...] = (),
    input_alternatives: tuple[tuple[str, ...], ...] = (),
    selection: SelectionTarget = SelectionTarget.INPUTS,
    location: VariableLocation = VariableLocation.FACE,
    compute: QuantityCompute | None = None,
) -> VariableSpec:
    return VariableSpec(
        name=name,
        kind=VariableKind.DERIVED,
        aliases=aliases,
        netcdf_candidates=(),
        location=location,
        extra_dims=extra_dims,
        units=units,
        label_key=label_key,
        inputs=inputs,
        requires=requires,
        optional_inputs=optional_inputs,
        input_alternatives=input_alternatives,
        selection=selection,
        version="1",
        compute=compute,
    )


VARIABLE_SPECS: Final[tuple[VariableSpec, ...]] = (
    _raw_spec(
        "water_depth",
        ("mesh2d_waterdepth",),
        aliases=("h", "wd", "waterdepth"),
        units="m",
        label_key="depth",
    ),
    _raw_spec(
        "velocity_magnitude",
        ("mesh2d_ucmaga", "mesh2d_ucmag"),
        aliases=("umag", "umag_layer"),
        units="m/s",
        label_key="velocity_magnitude",
    ),
    _raw_spec(
        "water_level",
        ("mesh2d_s1",),
        aliases=("wl",),
        units="m",
        label_key="water_level",
    ),
    _raw_spec(
        "bed_level",
        ("mesh2d_mor_bl", "mesh2d_flowelem_bl"),
        aliases=("bl", "DPS", "etab"),
        units="m",
        label_key="bed_elevation",
    ),
    _raw_spec(
        "layer_thickness",
        ("mesh2d_thlyr",),
        aliases=("thlyr", "La"),
        extra_dims=("nBedLayers",),
        units="m",
        label_key="sediment_thickness",
    ),
    _raw_spec(
        "layer_fraction",
        ("mesh2d_lyrfrac",),
        aliases=("Fak", "lyrfrac", "Fs"),
        extra_dims=("nBedLayers", "nSedTot"),
        units="1",
        label_key="volume_fraction_content_size_frac",
    ),
    _raw_spec(
        "chezy",
        ("mesh2d_czs",),
        aliases=("czs",),
        units="m^0.5/s",
        label_key="chezy_friction_coefficient",
    ),
    _raw_spec(
        "bed_shear_stress",
        ("mesh2d_taus",),
        aliases=("taub",),
        units="Pa",
        label_key="bed_shear_stress",
    ),
    _raw_spec(
        "mean_grain_size_geometric_output",
        ("mesh2d_dg",),
        units="m",
        label_key="dg",
    ),
    _raw_spec(
        "cell_area",
        ("mesh2d_flowelem_ba",),
        aliases=("ba",),
        units="m^2",
        label_key="cell_area",
    ),
    _raw_spec(
        "mesh2d_ucx",
        ("mesh2d_ucx",),
        units="m/s",
        label_key="velocity_vector",
    ),
    _raw_spec(
        "mesh2d_ucy",
        ("mesh2d_ucy",),
        units="m/s",
        label_key="velocity_vector",
    ),
    _raw_spec(
        "mesh2d_u1",
        ("mesh2d_u1",),
        units="m/s",
        label_key="velocity_magnitude",
        location=VariableLocation.EDGE,
        oriented=True,
    ),
    _raw_spec(
        "mesh2d_hu",
        ("mesh2d_hu",),
        units="m",
        label_key="depth",
        location=VariableLocation.EDGE,
    ),
    _raw_spec(
        "mesh2d_s0",
        ("mesh2d_s0",),
        units="m",
        label_key="water_level",
    ),
    _raw_spec(
        "mesh2d_sxtot",
        ("mesh2d_sxtot",),
        extra_dims=("nSedTot",),
        units="m^2/s",
        label_key="sediment_transport_size_frac_L2T",
    ),
    _raw_spec(
        "mesh2d_sytot",
        ("mesh2d_sytot",),
        extra_dims=("nSedTot",),
        units="m^2/s",
        label_key="sediment_transport_size_frac_L2T",
    ),
    _raw_spec(
        "mesh2d_sbcx",
        ("mesh2d_sbcx",),
        extra_dims=("nSedTot",),
        units="m^2/s",
        label_key="bedload_sediment_transport",
    ),
    _raw_spec(
        "mesh2d_sbcy",
        ("mesh2d_sbcy",),
        extra_dims=("nSedTot",),
        units="m^2/s",
        label_key="bedload_sediment_transport",
    ),
    _derived_spec(
        "froude_number",
        ("velocity_magnitude", "water_depth"),
        aliases=("Fr",),
        units="1",
        label_key="fr",
        requires=("gravity", "dry_depth_m"),
        compute=froude_number,
    ),
    _derived_spec(
        "specific_discharge",
        ("velocity_magnitude", "water_depth"),
        aliases=("qsp",),
        units="m^2/s",
        label_key="specific_discharge",
        compute=specific_discharge,
    ),
    _derived_spec(
        "energy_head",
        ("water_level", "velocity_magnitude"),
        aliases=("E",),
        units="m",
        label_key="energy_head",
        requires=("gravity",),
        compute=energy_head,
    ),
    _derived_spec(
        "shields_number_times_diameter",
        (),
        aliases=("ShieldsD",),
        extra_dims=("nSedTot",),
        units="m",
        label_key="shieldsd",
        requires=("gravity", "water_density", "sediment_density", "sediment_diameters"),
        input_alternatives=(
            ("bed_shear_stress",),
            ("velocity_magnitude", "chezy"),
        ),
        selection=SelectionTarget.OUTPUT,
        compute=shields_number_times_diameter,
    ),
    _derived_spec(
        "velocity_direction",
        ("mesh2d_ucx", "mesh2d_ucy"),
        aliases=("udir",),
        units="degree",
        label_key="direction",
        compute=velocity_direction,
    ),
    _derived_spec(
        "velocity_vector",
        ("mesh2d_ucx", "mesh2d_ucy"),
        aliases=("uv",),
        extra_dims=("component",),
        units="m/s",
        label_key="velocity_vector",
        compute=velocity_vector,
    ),
    _derived_spec(
        "velocity_magnitude_layer_mean",
        ("velocity_magnitude",),
        units="m/s",
        label_key="velocity_magnitude",
    ),
    _derived_spec(
        "water_level_rate",
        ("water_level",),
        aliases=("dzetadt",),
        units="m/s",
        label_key="dzetadt",
        requires=("map_times",),
        optional_inputs=("mesh2d_s0",),
        compute=water_level_rate,
    ),
    _derived_spec(
        "vorticity",
        ("mesh2d_u1",),
        units="1/s",
        label_key="vorticity",
        requires=("grid_geometry",),
        compute=vorticity,
    ),
    _derived_spec(
        "streamfunction",
        ("mesh2d_u1",),
        units="m^2/s",
        label_key="streamfunction",
        requires=("grid_geometry", "sparse_laplacian_factorization"),
        compute=streamfunction,
    ),
    _derived_spec(
        "transport_streamfunction",
        ("mesh2d_u1", "mesh2d_hu"),
        units="m^3/s",
        label_key="transport_streamfunction",
        requires=("grid_geometry", "sparse_laplacian_factorization"),
        compute=transport_streamfunction,
    ),
    _derived_spec(
        "total_sediment_thickness",
        ("layer_thickness",),
        aliases=("Ltot",),
        units="m",
        label_key="sediment_thickness",
        compute=total_sediment_thickness,
    ),
    _derived_spec(
        "morphological_active_area",
        ("cell_area", "total_sediment_thickness"),
        aliases=("ba_mor",),
        units="m^2",
        label_key="morphodynamic_cell_area",
        compute=morphological_active_area,
    ),
    _derived_spec(
        "sediment_transport_magnitude",
        ("mesh2d_sxtot", "mesh2d_sytot"),
        aliases=("stot",),
        extra_dims=("nSedTot",),
        units="m^2/s",
        label_key="sediment_transport_size_frac_L2T",
        selection=SelectionTarget.OUTPUT,
        compute=sediment_transport_magnitude,
    ),
    _derived_spec(
        "sediment_transport_magnitude_sum",
        ("sediment_transport_magnitude",),
        aliases=("stot_sum",),
        units="m^2/s",
        label_key="total_sediment_transport_L2T",
        compute=sediment_transport_magnitude_sum,
    ),
    _derived_spec(
        "bed_load_transport_magnitude",
        ("mesh2d_sbcx", "mesh2d_sbcy"),
        aliases=("sb",),
        extra_dims=("nSedTot",),
        units="m^2/s",
        label_key="bedload_sediment_transport",
        selection=SelectionTarget.OUTPUT,
        compute=bed_load_transport_magnitude,
    ),
    _derived_spec(
        "mean_grain_size_arithmetic",
        ("layer_fraction",),
        aliases=("dm",),
        extra_dims=("nBedLayers",),
        units="m",
        label_key="dm",
        requires=("sediment_diameters",),
        selection=SelectionTarget.OUTPUT,
        compute=mean_grain_size_arithmetic,
    ),
    _derived_spec(
        "mean_grain_size_geometric",
        ("layer_fraction",),
        aliases=("dg",),
        extra_dims=("nBedLayers",),
        units="m",
        label_key="dg",
        requires=("sediment_diameters",),
        selection=SelectionTarget.OUTPUT,
        compute=mean_grain_size_geometric,
    ),
    _derived_spec(
        "grain_size_percentile",
        ("layer_fraction",),
        aliases=("d10", "d50", "d90"),
        extra_dims=("nBedLayers",),
        units="m",
        label_key="grain_size_percentile",
        requires=("sediment_diameters",),
        selection=SelectionTarget.OUTPUT,
        compute=grain_size_percentile,
    ),
    _derived_spec(
        "chloride_mass",
        ("mesh2d_sa1", "mesh2d_flowelem_zw"),
        aliases=("clm2",),
        units="kg/m^2",
        label_key="chloride_mass",
        requires=("sal2cl",),
    ),
    _derived_spec(
        "residence_time_max",
        ("mesh2d_cTR<i>", "mesh2d_dTR<i>", "mesh2d_flowelem_zw"),
        aliases=("T_max",),
        units="s",
        label_key="maximum_residence_time",
        requires=("substance_file", "decay_rate"),
    ),
    _derived_spec(
        "residence_time_depth_average",
        ("mesh2d_cTR<i>", "mesh2d_dTR<i>", "mesh2d_flowelem_zw"),
        aliases=("T_da",),
        units="s",
        label_key="depth_average_residence_time",
        requires=("substance_file", "decay_rate"),
    ),
    _derived_spec(
        "residence_time_surface",
        ("mesh2d_cTR<i>", "mesh2d_dTR<i>", "mesh2d_flowelem_zw"),
        aliases=("T_surf",),
        units="s",
        label_key="surface_residence_time",
        requires=("substance_file", "decay_rate"),
    ),
)


def _build_registry(
    specs: Iterable[VariableSpec],
) -> tuple[Mapping[str, VariableSpec], Mapping[str, VariableSpec]]:
    registered_specs = tuple(specs)
    by_name: dict[str, VariableSpec] = {}
    by_identifier: dict[str, VariableSpec] = {}
    for spec in registered_specs:
        if spec.name in by_name:
            raise ValueError(f"Duplicate canonical variable name {spec.name!r}.")
        by_name[spec.name] = spec
        for identifier in (spec.name, *spec.aliases, *spec.netcdf_candidates):
            normalized = identifier.casefold()
            registered = by_identifier.get(normalized)
            if registered is not None and registered is not spec:
                raise ValueError(
                    f"Variable identifier {identifier!r} is claimed by both "
                    f"{registered.name!r} and {spec.name!r}."
                )
            by_identifier[normalized] = spec
    _validate_dependency_cycles(by_name, by_identifier)
    return MappingProxyType(by_name), MappingProxyType(by_identifier)


def _validate_dependency_cycles(
    by_name: Mapping[str, VariableSpec],
    by_identifier: Mapping[str, VariableSpec],
) -> None:
    active: list[str] = []
    complete: set[str] = set()

    def visit(spec: VariableSpec) -> None:
        if spec.kind is VariableKind.RAW or spec.name in complete:
            return
        if spec.name in active:
            cycle = " -> ".join((*active, spec.name))
            raise ValueError(f"Quantity dependency cycle detected: {cycle}.")
        active.append(spec.name)
        dependencies = (
            *spec.inputs,
            *spec.optional_inputs,
            *(name for group in spec.input_alternatives for name in group),
        )
        for dependency in dependencies:
            child = by_identifier.get(dependency.casefold())
            if child is not None:
                visit(child)
        active.pop()
        complete.add(spec.name)

    for spec in by_name.values():
        visit(spec)


VARIABLE_REGISTRY, _IDENTIFIER_REGISTRY = _build_registry(VARIABLE_SPECS)
_IDENTIFIER_NAMES: Final[tuple[str, ...]] = tuple(_IDENTIFIER_REGISTRY)
_MAX_SUGGESTIONS: Final = 3
_SUGGESTION_CUTOFF: Final = 0.65


def suggest_variable_names(name: str) -> tuple[str, ...]:
    """Return up to three canonical names close to an unknown identifier."""
    normalized = name.strip().casefold()
    if len(normalized) < 3:
        return ()

    matches = get_close_matches(
        normalized,
        _IDENTIFIER_NAMES,
        n=len(_IDENTIFIER_NAMES),
        cutoff=_SUGGESTION_CUTOFF,
    )
    suggestions: list[str] = []
    for match in matches:
        canonical_name = _IDENTIFIER_REGISTRY[match].name
        if canonical_name not in suggestions:
            suggestions.append(canonical_name)
        if len(suggestions) == _MAX_SUGGESTIONS:
            break
    return tuple(suggestions)


def get_variable_spec(name: str) -> VariableSpec:
    """Resolve a canonical name, alias, or registered NetCDF name."""
    spec = _IDENTIFIER_REGISTRY.get(name.strip().casefold())
    if spec is None:
        raise UnknownVariableError(name, suggest_variable_names(name))
    return spec


def list_variable_specs(
    kind: VariableKind | None = None,
) -> tuple[VariableSpec, ...]:
    """List all canonical specs, optionally filtered by raw or derived kind."""
    if kind is None:
        return VARIABLE_SPECS
    return tuple(spec for spec in VARIABLE_SPECS if spec.kind is kind)


def _format_names(names: tuple[str, ...]) -> str:
    if not names:
        return "-"
    return ", ".join(f"`{name}`" for name in names)


def render_variable_catalogue() -> str:
    """Render the complete Markdown catalogue from the registered specs."""
    lines = [
        "# Variable catalogue",
        "",
        "<!-- Generated from `d3dplot.variables.registry`; do not edit by hand. -->",
        "",
        "Names, Matlab aliases, and registered NetCDF candidates are resolved",
        "case-insensitively. Candidate spellings below are preserved for file reads.",
        "Derived dependencies include required, optional, and alternative inputs.",
        "Registered computation callbacks run through the shared quantity engine.",
        "",
        (
            "| Canonical | Kind | Aliases | NetCDF candidates | Location | "
            "Extra dimensions | Units | Label key | Inputs | Optional inputs | "
            "Alternatives | Requirements | Selection | Version |"
        ),
        "|-----------|------|---------|-------------------|----------|------------------|-------|-----------|--------|----------------|-------------|--------------|-----------|---------|",
    ]
    for spec in sorted(VARIABLE_SPECS, key=lambda item: item.name):
        selection = spec.selection.value if spec.selection is not None else "-"
        version = spec.version or "-"
        alternatives = "; ".join(
            " + ".join(f"`{name}`" for name in alternative)
            for alternative in spec.input_alternatives
        )
        if not alternatives:
            alternatives = "-"
        lines.append(
            f"| `{spec.name}` | {spec.kind.value} "
            f"| {_format_names(spec.aliases)} "
            f"| {_format_names(spec.netcdf_candidates)} "
            f"| {spec.location.value} "
            f"| {_format_names(spec.extra_dims)} "
            f"| `{spec.units}` "
            f"| `{spec.label_key}` "
            f"| {_format_names(spec.inputs)} "
            f"| {_format_names(spec.optional_inputs)} "
            f"| {alternatives} "
            f"| {_format_names(spec.requires)} "
            f"| {selection} "
            f"| {version} |"
        )
    return "\n".join(lines) + "\n"


__all__ = [
    "SelectionTarget",
    "UnknownVariableError",
    "VARIABLE_REGISTRY",
    "VARIABLE_SPECS",
    "VariableKind",
    "VariableLocation",
    "VariableSpec",
    "get_variable_spec",
    "list_variable_specs",
    "render_variable_catalogue",
    "suggest_variable_names",
]
