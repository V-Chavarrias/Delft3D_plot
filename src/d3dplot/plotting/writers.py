# SPDX-License-Identifier: GPL-3.0-or-later
"""Figure writers used by the shared finalisation path."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from matplotlib.figure import Figure

from d3dplot.config.figure import FigureConfig
from d3dplot.store.atomic import atomic_path
from d3dplot.store.guard import OutputWriteGuard


class FigureWriter(Protocol):
    """Write one figure beneath a guarded output root."""

    def write(
        self,
        figure: Figure,
        path: Path,
        *,
        style: FigureConfig,
        metadata: Mapping[str, str],
        guard: OutputWriteGuard,
    ) -> None: ...


class PngWriter:
    """Write an atomic PNG with text metadata."""

    def write(
        self,
        figure: Figure,
        path: Path,
        *,
        style: FigureConfig,
        metadata: Mapping[str, str],
        guard: OutputWriteGuard,
    ) -> None:
        with atomic_path(path, guard) as temporary:
            figure.savefig(
                temporary,
                format="png",
                dpi=style.dpi,
                metadata=dict(metadata),
            )


_WRITERS: dict[str, FigureWriter] = {"png": PngWriter()}


def get_figure_writer(format_name: str) -> FigureWriter:
    """Return a registered figure writer."""
    try:
        return _WRITERS[format_name.casefold()]
    except KeyError as error:
        supported = ", ".join(sorted(_WRITERS))
        raise ValueError(
            f"Unsupported figure format {format_name!r}; choose from {supported}."
        ) from error
