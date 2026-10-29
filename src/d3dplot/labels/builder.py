# SPDX-License-Identifier: GPL-3.0-or-later
"""Build localized labels from the packaged translation and unit tables."""

from __future__ import annotations

import csv
import math
import warnings
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import IO, Literal, cast

from d3dplot.paths import resolve_path_preserving_drive

Language = Literal["en", "nl", "es"]


class LabelKind(StrEnum):
    """Supported label forms for values and common comparisons."""

    VALUE = "value"
    QUANTITY = "quantity"
    UNIT = "unit"
    DIFFERENCE = "difference"
    BACKGROUND = "background"
    STANDARD_DEVIATION = "standard_deviation"
    DIFFERENCE_ABOVE_BACKGROUND = "difference_above_background"
    FILTERED = "filtered"
    RELATIVE = "relative"
    PERCENTAGE = "percentage"
    DOMINANT = "dominant"


@dataclass(frozen=True, slots=True)
class Label:
    """A rendered label and its separately accessible quantity and unit."""

    text: str
    quantity: str
    unit: str


@dataclass(frozen=True, slots=True)
class _Translation:
    en: str
    nl: str
    es: str
    unit_type: str

    def for_language(self, language: Language) -> str:
        if language == "en":
            return self.en
        if language == "nl":
            return self.nl
        return self.es


@dataclass(frozen=True, slots=True)
class _LabelTables:
    variable_to_key: Mapping[str, str]
    translations: Mapping[str, _Translation]
    units: Mapping[str, tuple[tuple[float, str], ...]]


_LANGUAGES = frozenset({"en", "nl", "es"})
_TEMPLATE_KINDS = frozenset(
    {
        "difference",
        "background",
        "standard_deviation",
        "difference_above_background",
        "filtered",
        "relative",
        "percentage",
        "dominant",
    }
)
_DATUM_FREE_KINDS = frozenset(
    {
        "difference",
        "standard_deviation",
        "difference_above_background",
        "filtered",
        "dominant",
    }
)
_CHLORIDE_VARIABLES = frozenset({"cl", "conctte", "cl_surf", "cl_bottom"})


def label(
    variable: str,
    language: Language | str,
    kind: LabelKind | str,
    scale: float = 1,
    datum: str = "+NAP",
    fraction: int | None = None,
    *,
    extra_dictionaries: Sequence[str | Path] = (),
    long_name: str | None = None,
    netcdf_units: str | None = None,
) -> Label:
    """Create a translated label, optionally using project dictionaries.

    Extra dictionaries use the same ``key,en,nl,es,unit_type`` columns as the
    packaged translations. Their rows are applied in order and can override a
    packaged key. Unknown variables use supplied NetCDF metadata when present.
    """
    if language not in _LANGUAGES:
        raise ValueError(f"Unsupported label language: {language!r}")
    if not variable.strip():
        raise ValueError("variable must not be empty")
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("scale must be a finite positive number")

    selected_kind = kind.value if isinstance(kind, LabelKind) else kind.casefold()
    supported_kinds = {
        "value",
        "quantity",
        "unit",
        *_TEMPLATE_KINDS,
    }
    if selected_kind not in supported_kinds:
        raise ValueError(
            f"Unsupported label kind {kind!r}; choose from "
            f"{', '.join(sorted(supported_kinds))}."
        )

    selected_language = cast(Language, language)
    tables = _load_label_tables(_extra_dictionary_signature(extra_dictionaries))
    variable_key = variable.strip().casefold()
    translation_key = tables.variable_to_key.get(variable_key, variable_key)
    translation = tables.translations.get(translation_key)
    if translation is None:
        warnings.warn(
            f"No translation is registered for variable {variable!r}; "
            "using NetCDF metadata where available.",
            UserWarning,
            stacklevel=2,
        )
        quantity = (long_name or variable).strip()
        unit = _external_unit(netcdf_units)
    else:
        quantity = _format_fraction(
            translation.for_language(selected_language),
            translation_key,
            fraction,
            selected_language,
        )
        if selected_kind in {"relative", "percentage"}:
            unit = _unit_text("-" if selected_kind == "relative" else "%")
        else:
            unit_type = translation.unit_type
            if selected_kind in _DATUM_FREE_KINDS and unit_type == "Lref":
                unit_type = "L"
            unit = _unit_text(
                _unit_symbol(
                    tables,
                    unit_type,
                    scale,
                    variable_key,
                    datum,
                )
            )

    if selected_kind in {"relative", "percentage"}:
        unit = _unit_text("-" if selected_kind == "relative" else "%")

    if selected_kind == "unit":
        return Label(text=unit, quantity=quantity, unit=unit)
    if selected_kind == "quantity":
        return Label(text=quantity, quantity=quantity, unit=unit)
    if selected_kind == "value":
        return Label(
            text=_append_unit(quantity, unit),
            quantity=quantity,
            unit=unit,
        )

    template_key = f"template.{selected_kind}"
    template_translation = tables.translations[template_key]
    text = template_translation.for_language(selected_language).format(
        quantity=quantity
    )
    return Label(
        text=_append_unit(text, unit),
        quantity=quantity,
        unit=unit,
    )


def _extra_dictionary_signature(
    paths: Sequence[str | Path],
) -> tuple[tuple[str, int, int], ...]:
    signatures: list[tuple[str, int, int]] = []
    for path_value in paths:
        path = resolve_path_preserving_drive(path_value, strict=True)
        stat = path.stat()
        signatures.append((str(path), stat.st_size, stat.st_mtime_ns))
    return tuple(signatures)


@lru_cache(maxsize=32)
def _load_label_tables(
    extra_signatures: tuple[tuple[str, int, int], ...],
) -> _LabelTables:
    variable_to_key: dict[str, str] = {}
    for _, row in _resource_rows("variable_to_key.csv", ("variable", "key")):
        variable = row["variable"].strip().casefold()
        key = row["key"].strip().casefold()
        if not variable or not key:
            raise ValueError("Packaged variable mappings must not be empty.")
        # Matlab's table lookup selects the first row for duplicate aliases.
        variable_to_key.setdefault(variable, key)

    translations = _read_translations(
        _resource_rows(
            "translations.csv",
            ("key", "en", "nl", "es", "unit_type"),
        ),
        "packaged translations",
    )
    for path_string, _, _ in extra_signatures:
        path = Path(path_string)
        with path.open("r", encoding="utf-8", newline="") as stream:
            extra = _read_translations(
                _csv_rows(
                    stream,
                    ("key", "en", "nl", "es", "unit_type"),
                    str(path),
                ),
                str(path),
            )
        translations.update(extra)

    units: dict[str, list[tuple[float, str]]] = {}
    unit_pairs: dict[str, list[float]] = {}
    for _, row in _resource_rows("units.csv", ("unit_type", "scale", "symbol")):
        unit_type = row["unit_type"].strip()
        symbol = row["symbol"].strip()
        try:
            scale = float(row["scale"])
        except ValueError as error:
            raise ValueError(
                f"Invalid scale {row['scale']!r} for unit type {unit_type!r}."
            ) from error
        if not unit_type or not symbol or not math.isfinite(scale) or scale <= 0:
            raise ValueError("Packaged unit rows must contain valid values.")
        known_scales = unit_pairs.setdefault(unit_type, [])
        if any(
            math.isclose(scale, value, rel_tol=1e-12, abs_tol=1e-15)
            for value in known_scales
        ):
            raise ValueError(f"Duplicate scale {scale!r} for unit type {unit_type!r}.")
        known_scales.append(scale)
        units.setdefault(unit_type, []).append((scale, symbol))

    unknown_unit_types = sorted(
        {item.unit_type for item in translations.values()} - units.keys()
    )
    if unknown_unit_types:
        raise ValueError(
            "Translation tables refer to unknown unit types: "
            + ", ".join(unknown_unit_types)
        )

    return _LabelTables(
        variable_to_key=variable_to_key,
        translations=translations,
        units={key: tuple(value) for key, value in units.items()},
    )


def _resource_rows(
    name: str,
    fields: tuple[str, ...],
) -> Iterator[tuple[int, dict[str, str]]]:
    resource = files("d3dplot.labels").joinpath("data", name)
    with resource.open("r", encoding="utf-8") as stream:
        yield from _csv_rows(stream, fields, str(resource))


def _csv_rows(
    stream: IO[str],
    fields: tuple[str, ...],
    source: str,
) -> Iterator[tuple[int, dict[str, str]]]:
    reader = csv.DictReader(stream)
    if reader.fieldnames != list(fields):
        raise ValueError(
            f"{source} must have columns {','.join(fields)} in that order."
        )
    for line_number, raw_row in enumerate(reader, start=2):
        if None in raw_row:
            raise ValueError(f"Unexpected extra CSV field in {source}:{line_number}.")
        row: dict[str, str] = {}
        for field in fields:
            value = raw_row.get(field)
            if value is None:
                raise ValueError(f"Missing CSV field in {source}:{line_number}.")
            row[field] = value
        yield line_number, row


def _read_translations(
    rows: Iterator[tuple[int, dict[str, str]]],
    source: str,
) -> dict[str, _Translation]:
    translations: dict[str, _Translation] = {}
    for line_number, row in rows:
        key = row["key"].strip().casefold()
        values = tuple(row[language].strip() for language in ("en", "nl", "es"))
        unit_type = row["unit_type"].strip()
        if (
            not key
            or not unit_type
            or any(not value or value == "?" for value in values)
        ):
            raise ValueError(
                f"Translation row {source}:{line_number} must contain a key, "
                "all three languages, and a unit type."
            )
        if key in translations:
            raise ValueError(f"Duplicate translation key {key!r} in {source}.")
        translations[key] = _Translation(
            en=values[0],
            nl=values[1],
            es=values[2],
            unit_type=unit_type,
        )
    return translations


def _format_fraction(
    quantity: str,
    translation_key: str,
    fraction: int | None,
    language: Language,
) -> str:
    if "%d" not in quantity:
        return quantity
    if fraction is not None:
        try:
            return quantity % fraction
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"Could not format fraction {fraction!r} in {translation_key!r}."
            ) from error
    optional_fraction_phrases = {
        "volume_fraction_content_size_frac": (
            "of size fraction %d ",
            "van korrelfractie %d ",
            "de la fracción granulométrica %d ",
        ),
        "hidexp": (
            " for fraction %d",
            " voor fractie %d",
            " en fracción %d",
        ),
    }
    language_index = {"en": 0, "nl": 1, "es": 2}[language]
    optional_phrase = optional_fraction_phrases.get(translation_key)
    if optional_phrase is not None:
        result = quantity.replace(optional_phrase[language_index], "")
        if result != quantity:
            return result
    raise ValueError(f"Translation {translation_key!r} requires a fraction value.")


def _unit_symbol(
    tables: _LabelTables,
    unit_type: str,
    scale: float,
    variable: str,
    datum: str,
) -> str:
    if unit_type == "-":
        if variable == "sal":
            unit_type = "salinity"
        elif variable in _CHLORIDE_VARIABLES:
            unit_type = "chloride_concentration"

    for known_scale, symbol in tables.units[unit_type]:
        if math.isclose(scale, known_scale, rel_tol=1e-12, abs_tol=1e-15):
            return symbol.replace("{datum}", datum)
    supported_scales = ", ".join(
        f"{known_scale:g}" for known_scale, _ in tables.units[unit_type]
    )
    raise ValueError(
        f"Scale {scale!r} is not supported for unit type {unit_type!r}; "
        f"supported scales: {supported_scales}."
    )


def _unit_text(symbol: str) -> str:
    return f"[{symbol}]"


def _external_unit(netcdf_units: str | None) -> str:
    if netcdf_units is None or not netcdf_units.strip():
        return "[?]"
    return _unit_text(netcdf_units.strip())


def _append_unit(text: str, unit: str) -> str:
    return f"{text} {unit}" if unit else text
