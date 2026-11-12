# SPDX-License-Identifier: GPL-3.0-or-later
"""Resolve the D-Flow FM MDU referenced by a DIMR configuration."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from d3dplot.paths import model_text_to_path, resolve_path_preserving_drive

_FM_LIBRARY = "dflowfm"


class DimrResolutionError(ValueError):
    """A DIMR configuration does not identify one usable FM input."""


class NoDflowFMComponentError(DimrResolutionError):
    """A DIMR configuration has no D-Flow FM component."""


class MultipleDflowFMComponentsError(DimrResolutionError):
    """A DIMR configuration has several D-Flow FM components."""


@dataclass(frozen=True, slots=True)
class _DflowFMComponent:
    name: str
    working_dir: str
    input_file: str | None


def resolve_dimr_mdu(path: Path) -> Path:
    """Return the unique ``dflowfm`` component MDU path from a DIMR file.

    Only the ``component`` elements are read. ``control``, couplers and global
    settings are not validated, so DIMR files with obsolete elements still resolve.
    """
    dimr_path = resolve_path_preserving_drive(path)
    fm_components = _read_dflowfm_components(dimr_path)
    if not fm_components:
        raise NoDflowFMComponentError(
            f"DIMR file {dimr_path} has no dflowfm component."
        )
    if len(fm_components) > 1:
        names = ", ".join(component.name for component in fm_components)
        raise MultipleDflowFMComponentsError(
            f"DIMR file {dimr_path} has multiple dflowfm components: {names}."
        )

    component = fm_components[0]
    if component.input_file is None:
        raise DimrResolutionError(
            f"DIMR component {component.name!r} in {dimr_path} "
            "does not reference an input file."
        )
    mdu_path = model_text_to_path(component.working_dir) / model_text_to_path(
        component.input_file
    )
    if not mdu_path.is_absolute():
        mdu_path = dimr_path.parent / mdu_path
    return resolve_path_preserving_drive(mdu_path)


def _read_dflowfm_components(dimr_path: Path) -> list[_DflowFMComponent]:
    try:
        root = ET.parse(dimr_path).getroot()
    except ET.ParseError as error:
        raise DimrResolutionError(
            f"DIMR file {dimr_path} is not well-formed XML: {error}."
        ) from error
    return [
        _DflowFMComponent(
            name=element.get("name", "").strip(),
            working_dir=_child_text(element, "workingDir") or ".",
            input_file=_child_text(element, "inputFile") or None,
        )
        for element in root
        if _local_name(element.tag) == "component"
        and _child_text(element, "library") == _FM_LIBRARY
    ]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(element: ET.Element, name: str) -> str | None:
    for child in element:
        if _local_name(child.tag) == name:
            return (child.text or "").strip()
    return None
