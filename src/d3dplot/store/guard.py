# SPDX-License-Identifier: GPL-3.0-or-later
"""Enforce marked output roots and keep writes away from inputs."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from d3dplot.store.layout import OUTPUT_MARKER_NAME, is_path_within


class OutputWriteError(RuntimeError):
    """Raised when a requested write is outside an allowed output root."""


class OutputWriteGuard:
    """Authorize writes only beneath a marked root and outside forbidden paths."""

    __slots__ = ("root", "marker", "forbidden_roots", "allowed_roots")

    def __init__(
        self,
        root: str | Path,
        forbidden_roots: Iterable[str | Path] = (),
        allowed_roots: Iterable[str | Path] = (),
    ) -> None:
        self.root = _resolve_path(root)
        self.marker = self.root / OUTPUT_MARKER_NAME
        self.forbidden_roots = tuple(
            _resolve_path(forbidden_root) for forbidden_root in forbidden_roots
        )
        self.allowed_roots = tuple(
            _resolve_path(allowed_root) for allowed_root in allowed_roots
        )

    def with_allowed_roots(self, roots: Iterable[str | Path]) -> OutputWriteGuard:
        """Return a guard that also authorizes explicitly configured cache roots."""
        return OutputWriteGuard(
            self.root,
            self.forbidden_roots,
            (*self.allowed_roots, *roots),
        )

    def validate_root_location(self) -> None:
        """Reject output roots that are inside any configured forbidden root."""
        for forbidden_root in self.forbidden_roots:
            if is_path_within(self.root, forbidden_root):
                raise OutputWriteError(
                    f"Output root {self.root} is inside forbidden root "
                    f"{forbidden_root}."
                )

    def validate_target(self, path: str | Path) -> Path:
        """Return a resolved target only when it is safe to write."""
        self.validate_root_location()
        target = _resolve_path(path)
        in_output_root = target != self.root and is_path_within(target, self.root)
        in_allowed_root = any(
            is_path_within(target, allowed_root) for allowed_root in self.allowed_roots
        )
        if not in_output_root and not in_allowed_root:
            raise OutputWriteError(
                f"Write target {target} is outside output root {self.root}."
            )
        if self.marker.is_symlink() or not self.marker.is_file():
            raise OutputWriteError(
                f"Output root {self.root} has no regular {OUTPUT_MARKER_NAME} marker."
            )
        try:
            marker = self.marker.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise OutputWriteError(
                f"Could not resolve output marker {self.marker}: {error}"
            ) from error
        if not is_path_within(marker, self.root):
            raise OutputWriteError(
                f"Output marker {self.marker} resolves outside output root."
            )
        if target == marker:
            raise OutputWriteError("The output-root marker cannot be overwritten.")
        for forbidden_root in self.forbidden_roots:
            if is_path_within(target, forbidden_root):
                raise OutputWriteError(
                    f"Write target {target} is inside forbidden root {forbidden_root}."
                )
        return target


def _resolve_path(path: str | Path) -> Path:
    try:
        return Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise OutputWriteError(f"Could not resolve path {path}: {error}") from error
