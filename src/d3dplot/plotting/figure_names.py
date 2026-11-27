# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared figure file names and axis labels, so every analysis uses the same forms.

A figure file is ``<prefix>/<variable>/<folders>/<time>.png``. Each folder level is
one choice (line, selection, comparison or view) and the file holds one time.
Axis labels carry their unit in square brackets, for example ``Bed level [m]``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, tzinfo
from pathlib import Path

from d3dplot.labels.builder import LabelKind, Language, label
from d3dplot.simulation.time_utils import format_timestamp_text
from d3dplot.store.layout import OutputLayout
from d3dplot.store.paths import OutputArea, build_quantity_output_path


def figure_file_path(
    layout: OutputLayout,
    *,
    prefix: Sequence[str | Path],
    variable: str,
    folders: Sequence[str | Path],
    time: datetime | None,
    zone: tzinfo,
    filename_suffix: str = "",
) -> Path:
    """Return the path of one figure; ``time=None`` names a figure over all times.

    The time is written in ``zone``, the same zone as the figure's title.
    """
    if time is None:
        filename = "all_times.png"
    else:
        stamp = format_timestamp_text(time.astimezone(zone))
        filename = f"{stamp}{filename_suffix}.png"
    return build_quantity_output_path(
        layout,
        OutputArea.FIGURES,
        variable,
        prefix=prefix,
        suffix=folders,
        filename=filename,
    )


def difference_subject(source_label: str, reference_label: str) -> str:
    """Return the folder label of a simulation difference, ``<source>_vs_<reference>``.

    Simulation differences are written to the reference simulation's tree under
    this label, so every analysis places them in the same folder.
    """
    return f"{source_label}_vs_{reference_label}"


def axis_label(
    label_key: str,
    language: Language,
    *,
    datum: str,
    extra_dictionaries: Sequence[str | Path] = (),
    kind: LabelKind | str = LabelKind.VALUE,
) -> str:
    """Return a translated ``Quantity [unit]`` label for an axis or colour bar."""
    return label(
        label_key,
        language,
        kind,
        datum=datum,
        extra_dictionaries=extra_dictionaries,
    ).text


__all__ = ["axis_label", "difference_subject", "figure_file_path"]
