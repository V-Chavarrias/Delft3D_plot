# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for canonical variable metadata and resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from d3dplot.labels.builder import _load_label_tables
from d3dplot.variables import (
    UnknownVariableError,
    VariableKind,
    get_variable_spec,
    list_variable_specs,
    render_variable_catalogue,
    suggest_variable_names,
)
from d3dplot.variables.registry import (
    VariableLocation,
    VariableSpec,
    _build_registry,
)


@pytest.mark.unit
def test_registry_contains_all_v1_raw_and_derived_quantities() -> None:
    assert len(list_variable_specs(VariableKind.RAW)) == 10
    assert len(list_variable_specs(VariableKind.DERIVED)) == 23
    assert get_variable_spec("bed_level").kind is VariableKind.RAW
    assert get_variable_spec("froude_number").kind is VariableKind.DERIVED


@pytest.mark.unit
@pytest.mark.parametrize(
    "name",
    [
        "bed_level",
        "bl",
        "DPS",
        "mesh2d_mor_bl",
        "MESH2D_FLOWELEM_BL",
    ],
)
def test_canonical_matlab_and_netcdf_names_resolve_case_insensitively(
    name: str,
) -> None:
    assert get_variable_spec(name).name == "bed_level"


@pytest.mark.unit
def test_raw_variable_preserves_netcdf_candidate_priority_and_dimensions() -> None:
    bed_level = get_variable_spec("bed_level")
    layer_fraction = get_variable_spec("mesh2d_lyrfrac")

    assert bed_level.netcdf_candidates == (
        "mesh2d_mor_bl",
        "mesh2d_flowelem_bl",
    )
    assert layer_fraction.extra_dims == ("nBedLayers", "nSedTot")


@pytest.mark.unit
@pytest.mark.parametrize("name", ["d10", "d50", "d90"])
def test_grain_size_percentile_aliases_resolve_to_the_canonical_quantity(
    name: str,
) -> None:
    assert get_variable_spec(name).name == "grain_size_percentile"


@pytest.mark.unit
def test_unknown_variable_error_includes_canonical_suggestions() -> None:
    with pytest.raises(UnknownVariableError) as error:
        get_variable_spec("bed_lvel")

    assert error.value.code == "V-VAR-001"
    assert error.value.suggestions == ("bed_level",)
    assert "Did you mean 'bed_level'?" in str(error.value)


@pytest.mark.unit
def test_unknown_variable_suggestions_are_bounded_and_deduplicated() -> None:
    suggestions = suggest_variable_names("velocit_magnitdue")

    assert suggestions
    assert len(suggestions) <= 3
    assert len(set(suggestions)) == len(suggestions)
    assert "velocity_magnitude" in suggestions
    assert suggest_variable_names("x") == ()


@pytest.mark.unit
def test_registry_rejects_ambiguous_aliases() -> None:
    first = VariableSpec(
        name="bed_level",
        kind=VariableKind.RAW,
        aliases=("bl",),
        netcdf_candidates=("mesh2d_mor_bl",),
        location=VariableLocation.FACE,
        extra_dims=(),
        units="m",
        label_key="bed_elevation",
    )
    second = VariableSpec(
        name="bottom_level",
        kind=VariableKind.RAW,
        aliases=("BL",),
        netcdf_candidates=("mesh2d_bottom_bl",),
        location=VariableLocation.FACE,
        extra_dims=(),
        units="m",
        label_key="bed_elevation",
    )

    with pytest.raises(ValueError, match="claimed by both"):
        _build_registry((first, second))


@pytest.mark.unit
def test_registry_label_keys_exist_in_the_label_catalogue() -> None:
    translations = _load_label_tables(()).translations

    assert all(
        spec.label_key.casefold() in translations for spec in list_variable_specs()
    )


@pytest.mark.unit
def test_variable_documentation_is_generated_from_the_registry() -> None:
    documentation = Path(__file__).resolve().parents[1] / "docs" / "variables.md"

    assert documentation.read_text(encoding="utf-8") == render_variable_catalogue()
