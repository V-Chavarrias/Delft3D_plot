# SPDX-License-Identifier: GPL-3.0-or-later
"""Resolve the D-Flow FM MDU referenced by a DIMR configuration."""

from __future__ import annotations

from pathlib import Path

from hydrolib.core.dimr.models import DIMR  # type: ignore[import-untyped]


def resolve_dimr_mdu(path: Path) -> Path:
    """Return the unique ``dflowfm`` component MDU path from a DIMR file."""
    dimr_path = path.resolve(strict=False)
    model = DIMR(dimr_path, recurse=False)
    fm_components = [
        component for component in model.component if component.library == "dflowfm"
    ]
    if not fm_components:
        raise ValueError(f"DIMR file {dimr_path} has no dflowfm component.")
    if len(fm_components) > 1:
        names = ", ".join(component.name for component in fm_components)
        raise ValueError(
            f"DIMR file {dimr_path} has multiple dflowfm components: {names}."
        )

    mdu_path = Path(fm_components[0].filepath)
    if not mdu_path.is_absolute():
        mdu_path = dimr_path.parent / mdu_path
    return mdu_path.resolve(strict=False)
