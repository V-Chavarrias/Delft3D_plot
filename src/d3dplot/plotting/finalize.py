# SPDX-License-Identifier: GPL-3.0-or-later
"""Apply common styling, hooks, metadata, atomic saving, and figure cleanup."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, TypeAlias, cast

from matplotlib.artist import Artist
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.text import Text

from d3dplot import __version__
from d3dplot.config.figure import FigureConfig
from d3dplot.plotting.writers import FigureWriter
from d3dplot.store.guard import OutputWriteGuard


@dataclass(frozen=True, slots=True)
class FigureContext:
    """Read-only figure context passed to user hooks."""

    analysis: str
    view: str
    comparison: str
    variable: str
    simulation: str
    time: datetime
    extent: tuple[float, float, float, float]
    axes: Mapping[str, Axes]
    data: Mapping[str, object]
    labels: Mapping[str, str]
    language: str
    output_path: Path

    def __post_init__(self) -> None:
        if self.time.tzinfo is None or self.time.utcoffset() is None:
            raise ValueError("FigureContext.time must be time-zone-aware.")
        object.__setattr__(self, "axes", MappingProxyType(dict(self.axes)))
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))
        object.__setattr__(self, "labels", MappingProxyType(dict(self.labels)))


FigureHook: TypeAlias = Callable[[Figure, FigureContext], None]
HookSpec: TypeAlias = str | FigureHook


class FigureHookError(RuntimeError):
    """A configured figure hook failed before the figure was written."""


@dataclass(frozen=True, slots=True)
class _TextState:
    artist: Text
    text: str
    color: Any
    size: float | str
    family: Any
    position: tuple[float, float]
    visible: bool
    alpha: float | None
    rotation: float

    @classmethod
    def capture(cls, artist: Text) -> _TextState:
        return cls(
            artist=artist,
            text=artist.get_text(),
            color=artist.get_color(),
            size=artist.get_fontsize(),
            family=artist.get_fontfamily(),
            position=artist.get_position(),
            visible=artist.get_visible(),
            alpha=artist.get_alpha(),
            rotation=artist.get_rotation(),
        )

    def restore(self) -> None:
        self.artist.set_text(self.text)
        self.artist.set_color(self.color)
        self.artist.set_fontsize(self.size)
        self.artist.set_fontfamily(self.family)
        self.artist.set_position(self.position)
        self.artist.set_visible(self.visible)
        self.artist.set_alpha(self.alpha)
        self.artist.set_rotation(self.rotation)


@dataclass(frozen=True, slots=True)
class _AxesState:
    axes: Axes
    children: frozenset[Artist]
    x_limits: tuple[float, float]
    y_limits: tuple[float, float]
    aspect: Literal["auto", "equal"] | float
    face_color: Any
    texts: tuple[_TextState, ...]

    @classmethod
    def capture(cls, axes: Axes) -> _AxesState:
        children = frozenset(axes.get_children())
        return cls(
            axes=axes,
            children=children,
            x_limits=axes.get_xlim(),
            y_limits=axes.get_ylim(),
            aspect=cast(Literal["auto", "equal"] | float, axes.get_aspect()),
            face_color=axes.get_facecolor(),
            texts=tuple(
                _TextState.capture(child)
                for child in children
                if isinstance(child, Text)
            ),
        )

    def restore(self) -> None:
        for child in tuple(self.axes.get_children()):
            if child not in self.children and child is not self.axes.patch:
                child.remove()
        self.axes.set_xlim(self.x_limits)
        self.axes.set_ylim(self.y_limits)
        self.axes.set_aspect(self.aspect)
        self.axes.set_facecolor(self.face_color)
        for text in self.texts:
            text.restore()


@dataclass(frozen=True, slots=True)
class FigureSnapshot:
    """Restore hook-added artists and common mutable axes properties."""

    figure: Figure
    figure_children: frozenset[Artist]
    axes: tuple[_AxesState, ...]
    size_inches: tuple[float, float]

    @classmethod
    def capture(cls, figure: Figure) -> FigureSnapshot:
        size = figure.get_size_inches()
        return cls(
            figure=figure,
            figure_children=frozenset(figure.get_children()),
            axes=tuple(_AxesState.capture(axes) for axes in figure.axes),
            size_inches=(float(size[0]), float(size[1])),
        )

    def restore(self) -> None:
        for child in tuple(self.figure.get_children()):
            if child not in self.figure_children:
                child.remove()
        for state in self.axes:
            state.restore()
        self.figure.set_size_inches(*self.size_inches, forward=True)


def finalize_figure(
    figure: Figure,
    context: FigureContext,
    style: FigureConfig,
    hooks: Sequence[HookSpec],
    writer: FigureWriter,
    *,
    metadata: Mapping[str, str],
    guard: OutputWriteGuard,
    close: bool = True,
) -> None:
    """Apply shared styling and hooks, then atomically write one figure."""
    try:
        _apply_style(figure, style)
        for hook_spec in hooks:
            try:
                name, hook = _resolve_hook(hook_spec)
            except (ImportError, ValueError, AttributeError) as error:
                raise FigureHookError(
                    f"Could not load figure hook {hook_spec!r}: {error}"
                ) from error
            try:
                hook(figure, context)
            except Exception as error:
                raise FigureHookError(
                    f"Figure hook {name!r} failed for {context.output_path}: {error}"
                ) from error
        writer.write(
            figure,
            context.output_path,
            style=style,
            metadata={
                "d3dplot_version": __version__,
                **metadata,
            },
            guard=guard,
        )
    finally:
        if close:
            from matplotlib import pyplot

            pyplot.close(figure)


def close_figure(figure: Figure) -> None:
    """Close a figure through the shared finalisation module."""
    from matplotlib import pyplot

    pyplot.close(figure)


def _apply_style(figure: Figure, style: FigureConfig) -> None:
    width_cm, height_cm = style.size_cm
    figure.set_size_inches(width_cm / 2.54, height_cm / 2.54, forward=True)
    for axes in figure.axes:
        axes.tick_params(labelsize=style.font_size)
    for artist in figure.findobj(match=Text):
        artist.set_fontfamily(style.font_family)
        artist.set_fontsize(style.font_size)


def _resolve_hook(hook_spec: HookSpec) -> tuple[str, FigureHook]:
    if callable(hook_spec):
        return (
            f"{hook_spec.__module__}:{hook_spec.__qualname__}",
            hook_spec,
        )
    if hook_spec.count(":") != 1:
        raise ValueError(f"Hook {hook_spec!r} must use the 'module:function' form.")
    module_name, function_name = hook_spec.split(":", maxsplit=1)
    if not module_name or not function_name:
        raise ValueError(f"Hook {hook_spec!r} must use the 'module:function' form.")
    module = importlib.import_module(module_name)
    hook = getattr(module, function_name, None)
    if not callable(hook):
        raise ValueError(f"Configured figure hook {hook_spec!r} is not callable.")
    return hook_spec, hook
