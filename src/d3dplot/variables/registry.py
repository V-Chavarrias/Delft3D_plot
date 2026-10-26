# SPDX-License-Identifier: GPL-3.0-or-later
"""Static metadata catalog for model quantities."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from difflib import get_close_matches
from enum import StrEnum
from types import MappingProxyType
from typing import Final


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
    selection: SelectionTarget | None = None
    version: str | None = None

    def __post_init__(self) -> None:
        if not self.name or self.name != self.name.strip():
            raise ValueError("Variable names must be non-empty and trimmed.")
        if not self.units or not self.label_key:
            raise ValueError("Variable units and label_key must be non-empty.")

        identifiers = (self.name, *self.aliases, *self.netcdf_candidates)
        normalized_identifiers = [identifier.casefold() for identifier in identifiers]
        if any(
            not identifier or identifier != identifier.strip()
            for identifier in identifiers
        ):
            raise ValueError("Variable identifiers must be non-empty and trimmed.")
        if len(set(normalized_identifiers)) != len(normalized_identifiers):
            raise ValueError(f"Variable {self.name!r} repeats an identifier.")

        if any(not dim or dim != dim.strip() for dim in self.extra_dims):
            raise ValueError("Variable dimensions must be non-empty and trimmed.")
        if len(set(self.extra_dims)) != len(self.extra_dims):
            raise ValueError(f"Variable {self.name!r} repeats an extra dimension.")

        if self.kind is VariableKind.RAW:
            if not self.netcdf_candidates:
                raise ValueError(f"Raw variable {self.name!r} needs NetCDF candidates.")
            if (
                self.inputs
                or self.requires
                or self.selection is not None
                or self.version
            ):
                raise ValueError(f"Raw variable {self.name!r} has derived metadata.")
            return

        if self.netcdf_candidates or not self.inputs:
            raise ValueError(
                f"Derived variable {self.name!r} needs inputs and no NetCDF candidates."
            )
        if self.selection is None or not self.version:
            raise ValueError(
                f"Derived variable {self.name!r} needs selection and version metadata."
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
    selection: SelectionTarget = SelectionTarget.INPUTS,
    location: VariableLocation = VariableLocation.FACE,
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
        selection=selection,
        version="1",
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
        ("mesh2d_ucmag", "mesh2d_ucmaga"),
        aliases=("umag", "umag_layer"),
        extra_dims=("nLayers",),
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
    _derived_spec(
        "froude_number",
        ("velocity_magnitude", "water_depth"),
        aliases=("Fr",),
        units="1",
        label_key="fr",
        requires=("gravity", "dry_depth_m"),
    ),
    _derived_spec(
        "specific_discharge",
        ("velocity_magnitude", "water_depth"),
        aliases=("qsp",),
        units="m^2/s",
        label_key="specific_discharge",
    ),
    _derived_spec(
        "energy_head",
        ("water_level", "velocity_magnitude"),
        aliases=("E",),
        units="m",
        label_key="energy_head",
        requires=("gravity",),
    ),
    _derived_spec(
        "shields_number_times_diameter",
        ("bed_shear_stress", "velocity_magnitude", "chezy"),
        aliases=("ShieldsD",),
        extra_dims=("nSedTot",),
        units="m",
        label_key="shieldsd",
        requires=("gravity", "water_density", "sediment_density", "sediment_diameters"),
        selection=SelectionTarget.OUTPUT,
    ),
    _derived_spec(
        "velocity_direction",
        ("mesh2d_ucx", "mesh2d_ucy"),
        aliases=("udir",),
        units="degree",
        label_key="direction",
    ),
    _derived_spec(
        "velocity_vector",
        ("mesh2d_ucx", "mesh2d_ucy"),
        aliases=("uv",),
        extra_dims=("component",),
        units="m/s",
        label_key="velocity_vector",
    ),
    _derived_spec(
        "velocity_magnitude_layer_mean",
        ("velocity_magnitude",),
        units="m/s",
        label_key="velocity_magnitude",
    ),
    _derived_spec(
        "water_level_rate",
        ("water_level", "mesh2d_s0"),
        aliases=("dzetadt",),
        units="m/s",
        label_key="dzetadt",
        requires=("map_times",),
    ),
    _derived_spec(
        "vorticity",
        ("mesh2d_u1",),
        units="1/s",
        label_key="vorticity",
        requires=("grid_geometry",),
    ),
    _derived_spec(
        "streamfunction",
        ("mesh2d_u1",),
        units="m^2/s",
        label_key="streamfunction",
        requires=("grid_geometry", "sparse_laplacian_factorization"),
    ),
    _derived_spec(
        "transport_streamfunction",
        ("mesh2d_u1", "mesh2d_hu"),
        units="m^3/s",
        label_key="transport_streamfunction",
        requires=("grid_geometry", "sparse_laplacian_factorization"),
    ),
    _derived_spec(
        "total_sediment_thickness",
        ("layer_thickness",),
        aliases=("Ltot",),
        units="m",
        label_key="sediment_thickness",
    ),
    _derived_spec(
        "morphological_volume",
        ("cell_area", "total_sediment_thickness"),
        aliases=("ba_mor",),
        units="m^3",
        label_key="morphological_volume",
    ),
    _derived_spec(
        "sediment_transport_magnitude",
        ("mesh2d_sxtot", "mesh2d_sytot"),
        aliases=("stot",),
        extra_dims=("nSedTot",),
        units="m^2/s",
        label_key="sediment_transport_size_frac_L2T",
        selection=SelectionTarget.OUTPUT,
    ),
    _derived_spec(
        "sediment_transport_magnitude_sum",
        ("sediment_transport_magnitude",),
        aliases=("stot_sum",),
        units="m^2/s",
        label_key="total_sediment_transport_L2T",
    ),
    _derived_spec(
        "bed_load_transport_magnitude",
        ("mesh2d_sbcx", "mesh2d_sbcy"),
        aliases=("sb",),
        extra_dims=("nSedTot",),
        units="m^2/s",
        label_key="bedload_sediment_transport",
        selection=SelectionTarget.OUTPUT,
    ),
    _derived_spec(
        "mean_grain_size_arithmetic",
        ("layer_fraction",),
        aliases=("dm",),
        units="m",
        label_key="dm",
        requires=("sediment_diameters",),
    ),
    _derived_spec(
        "mean_grain_size_geometric",
        ("layer_fraction",),
        aliases=("dg",),
        units="m",
        label_key="dg",
        requires=("sediment_diameters",),
    ),
    _derived_spec(
        "grain_size_percentile",
        ("layer_fraction",),
        aliases=("d10", "d50", "d90"),
        units="m",
        label_key="grain_size_percentile",
        requires=("sediment_diameters",),
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
    by_name: dict[str, VariableSpec] = {}
    by_identifier: dict[str, VariableSpec] = {}
    for spec in specs:
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
    return MappingProxyType(by_name), MappingProxyType(by_identifier)


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
        "Derived inputs and requirements describe dependencies; computation callbacks",
        "are added with the quantity engine in WP 2.2a/b.",
        "",
        (
            "| Canonical | Kind | Aliases | NetCDF candidates | Location | "
            "Extra dimensions | Units | Label key | Inputs | Requirements | "
            "Selection | Version |"
        ),
        "|-----------|------|---------|-------------------|----------|------------------|-------|-----------|--------|--------------|-----------|---------|",
    ]
    for spec in sorted(VARIABLE_SPECS, key=lambda item: item.name):
        selection = spec.selection.value if spec.selection is not None else "-"
        version = spec.version or "-"
        lines.append(
            f"| `{spec.name}` | {spec.kind.value} "
            f"| {_format_names(spec.aliases)} "
            f"| {_format_names(spec.netcdf_candidates)} "
            f"| {spec.location.value} "
            f"| {_format_names(spec.extra_dims)} "
            f"| `{spec.units}` "
            f"| `{spec.label_key}` "
            f"| {_format_names(spec.inputs)} "
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
