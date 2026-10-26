# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for localized label and unit generation."""

from __future__ import annotations

import csv
from importlib.resources import files
from pathlib import Path

import pytest

from d3dplot.config import LabelsConfig
from d3dplot.labels import Label, LabelKind, label
from d3dplot.labels.builder import _load_label_tables


@pytest.mark.unit
@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("en", "bed elevation [m+NAP]"),
        ("nl", "bodemhoogte [m+NAP]"),
        ("es", "elevación del lecho [m+NAP]"),
    ],
)
def test_label_uses_translation_language_and_vertical_datum(
    language: str,
    expected: str,
) -> None:
    result = label(
        "mesh2d_mor_bl",
        language,
        LabelKind.VALUE,
        datum="+NAP",
    )

    assert result == Label(
        text=expected,
        quantity=expected.removesuffix(" [m+NAP]"),
        unit="[m+NAP]",
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kind", "expected_text", "expected_unit"),
    [
        (LabelKind.QUANTITY, "elevation", "[m+NAP]"),
        (LabelKind.UNIT, "[m+NAP]", "[m+NAP]"),
        (LabelKind.DIFFERENCE, "difference in elevation [m]", "[m]"),
        (LabelKind.BACKGROUND, "elevation above background [m+NAP]", "[m+NAP]"),
        (
            LabelKind.STANDARD_DEVIATION,
            "standard deviation elevation [m]",
            "[m]",
        ),
        (
            LabelKind.DIFFERENCE_ABOVE_BACKGROUND,
            "difference in elevation above background [m]",
            "[m]",
        ),
        (LabelKind.FILTERED, "filtered elevation [m]", "[m]"),
        (LabelKind.RELATIVE, "relative elevation [-]", "[-]"),
        (LabelKind.PERCENTAGE, "difference in elevation [%]", "[%]"),
        (LabelKind.DOMINANT, "dominant elevation [m]", "[m]"),
    ],
)
def test_label_kinds_use_templates_and_unit_rules(
    kind: LabelKind,
    expected_text: str,
    expected_unit: str,
) -> None:
    result = label("eta", "en", kind)

    assert result.text == expected_text
    assert result.quantity == "elevation"
    assert result.unit == expected_unit


@pytest.mark.unit
def test_label_scale_fraction_and_special_units() -> None:
    scaled = label("eta", "nl", LabelKind.DIFFERENCE, scale=0.001)
    fraction = label("lyrfrac", "en", LabelKind.VALUE, fraction=3)
    no_fraction_dutch = label("lyrfrac", "nl", LabelKind.VALUE)
    no_fraction = label("lyrfrac", "es", LabelKind.VALUE)
    hiding_exposure = label("hidexp", "nl", LabelKind.VALUE)
    hiding_exposure_es = label("hidexp", "es", LabelKind.VALUE)
    salinity = label("sal", "en", LabelKind.VALUE)
    chloride = label("cl_surf", "en", LabelKind.VALUE)
    age = label("age", "en", LabelKind.VALUE, scale=1 / 3600 / 24)

    assert scaled.text == "verschil in hoogte [km]"
    assert fraction.text == (
        "volume fraction content of size fraction 3 in active layer [-]"
    )
    assert no_fraction_dutch.text == "volumefractiegehalte in de actieve laag [-]"
    assert no_fraction.text == "contenido de fracción volumétrica en la capa activa [-]"
    assert hiding_exposure.text == "verbergings-blootstellingsfactor [-]"
    assert hiding_exposure_es.text == "factor de ocultación-exposición [-]"
    assert salinity.unit == "[psu]"
    assert chloride.unit == "[mg/l]"
    assert age.unit == "[d]"


@pytest.mark.unit
def test_unknown_variables_use_netcdf_metadata_with_a_warning() -> None:
    with pytest.warns(UserWarning, match="No translation is registered"):
        result = label(
            "new_tracer",
            "en",
            LabelKind.VALUE,
            long_name="Tracer concentration",
            netcdf_units="mol m-3",
        )

    assert result == Label(
        text="Tracer concentration [mol m-3]",
        quantity="Tracer concentration",
        unit="[mol m-3]",
    )


@pytest.mark.unit
def test_extra_dictionaries_add_and_override_translations(tmp_path: Path) -> None:
    dictionary = tmp_path / "extra-labels.csv"
    dictionary.write_text(
        "key,en,nl,es,unit_type\n"
        "bed_elevation,custom bed,maatwerkbodem,suelo personalizado,Lref\n"
        "custom_flow,custom flow,aangepaste afvoer,caudal personalizado,L/T\n",
        encoding="utf-8",
    )
    labels_config = LabelsConfig(extra_dictionaries=[dictionary])

    overridden = label(
        "mesh2d_mor_bl",
        "en",
        LabelKind.VALUE,
        datum="MSL",
        extra_dictionaries=labels_config.extra_dictionaries,
    )
    added = label(
        "custom_flow",
        "es",
        LabelKind.VALUE,
        extra_dictionaries=labels_config.extra_dictionaries,
    )

    assert overridden.text == "custom bed [mMSL]"
    assert added.text == "caudal personalizado [m/s]"


@pytest.mark.unit
def test_extra_dictionary_requires_all_languages(tmp_path: Path) -> None:
    dictionary = tmp_path / "incomplete.csv"
    dictionary.write_text(
        "key,en,nl,es,unit_type\ncustom,custom,,personalizado,-\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="all three languages"):
        label(
            "custom",
            "en",
            LabelKind.VALUE,
            extra_dictionaries=(dictionary,),
        )


@pytest.mark.unit
def test_packaged_label_tables_are_complete_and_consistent() -> None:
    data = files("d3dplot.labels").joinpath("data")
    with data.joinpath("translations.csv").open(
        "r",
        encoding="utf-8",
        newline="",
    ) as stream:
        translation_rows = list(csv.DictReader(stream))
    with data.joinpath("variable_to_key.csv").open(
        "r",
        encoding="utf-8",
        newline="",
    ) as stream:
        variable_rows = list(csv.DictReader(stream))
    with data.joinpath("units.csv").open(
        "r",
        encoding="utf-8",
        newline="",
    ) as stream:
        unit_rows = list(csv.DictReader(stream))

    translations = {row["key"] for row in translation_rows}
    unit_types = {row["unit_type"] for row in unit_rows}

    assert translation_rows
    assert all(
        row[language].strip() and row[language] != "?"
        for row in translation_rows
        for language in ("en", "nl", "es")
    )
    assert all(row["key"] for row in variable_rows)
    assert {row["key"] for row in variable_rows} <= translations
    assert {row["unit_type"] for row in translation_rows} <= unit_types
    assert _load_label_tables(()).variable_to_key["salm2"] == "salt_m2"
