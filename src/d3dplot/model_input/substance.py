# SPDX-License-Identifier: GPL-3.0-or-later
"""Read residence-time metadata from D-WAQ substance files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hydrolib.core.dflowfm.substance.models import SubstanceModel  # type: ignore[import-untyped]

from d3dplot.model_input.errors import ModelInputError
from d3dplot.paths import resolve_path_preserving_drive


@dataclass(frozen=True, slots=True)
class SubstanceInfo:
    """Decay rates read from ``RcDecTR<n>`` parameters."""

    decay_rates_s_1: tuple[float, ...]


def read_substance(path: Path) -> SubstanceInfo:
    """Read positive residence-time decay rates from a ``.sub`` file."""
    substance_path = resolve_path_preserving_drive(path)
    try:
        model = SubstanceModel(filepath=substance_path)
    except (OSError, TypeError, ValueError) as error:
        raise ModelInputError(
            substance_path,
            f"HYDROLIB-core could not read the substance file: {error}",
            "Check the .sub syntax and the RcDecTR parameters.",
        ) from error

    rates: list[float] = []
    for parameter in model.parameters:
        name = str(parameter.name)
        if not name.casefold().startswith("rcdectr"):
            continue
        try:
            rate = float(parameter.value)
        except (TypeError, ValueError) as error:
            raise ModelInputError(
                substance_path,
                f"Parameter {name!r} is not numeric.",
                "Set RcDecTR parameters to positive decay rates.",
            ) from error
        if rate <= 0:
            raise ModelInputError(
                substance_path,
                f"Parameter {name!r} must be positive.",
                "Set RcDecTR parameters to positive decay rates.",
            )
        rates.append(rate)
    return SubstanceInfo(decay_rates_s_1=tuple(rates))
