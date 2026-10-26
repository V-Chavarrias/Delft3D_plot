# SPDX-License-Identifier: GPL-3.0-or-later
"""Resolve the D-Flow FM MDU referenced by a DIMR configuration."""

from __future__ import annotations

from pathlib import Path

from hydrolib.core.dimr.models import DIMR  # type: ignore[import-untyped]


class DimrResolutionError(ValueError):
    """A DIMR configuration does not identify one usable FM input."""


class NoDflowFMComponentError(DimrResolutionError):
    """A DIMR configuration has no D-Flow FM component."""


class MultipleDflowFMComponentsError(DimrResolutionError):
    """A DIMR configuration has several D-Flow FM components."""


def resolve_dimr_mdu(path: Path) -> Path:
    """Return the unique ``dflowfm`` component MDU path from a DIMR file."""
    dimr_path = path.resolve(strict=False)
    model = DIMR(dimr_path, recurse=False)
    fm_components = [
        component for component in model.component if component.library == "dflowfm"
    ]
    if not fm_components:
        raise NoDflowFMComponentError(
            f"DIMR file {dimr_path} has no dflowfm component."
        )
    if len(fm_components) > 1:
        names = ", ".join(component.name for component in fm_components)
        raise MultipleDflowFMComponentsError(
            f"DIMR file {dimr_path} has multiple dflowfm components: {names}."
        )

    input_file = fm_components[0].filepath
    if input_file is None:
        raise DimrResolutionError(
            f"DIMR component {fm_components[0].name!r} in {dimr_path} "
            "does not reference an input file."
        )
    mdu_path = Path(input_file)
    if not mdu_path.is_absolute():
        mdu_path = dimr_path.parent / mdu_path
    return mdu_path.resolve(strict=False)
