# SPDX-License-Identifier: GPL-3.0-or-later
"""Path normalization helpers for configured input paths."""

from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath


def model_text_to_path(text: str) -> Path:
    """Return the path written in a model file, with either separator style.

    Model files written on Windows use ``\\`` (for example ``..\\model\\a.mdu``);
    on POSIX systems these are converted to ``/`` before use.
    """
    return Path(normalise_separators(text, separator=os.sep))


def normalise_separators(text: str, *, separator: str) -> str:
    """Convert backslashes to ``/`` if ``separator`` is ``/``, else return ``text``."""
    if separator == "/":
        return text.replace("\\", "/")
    return text


def resolve_path_preserving_drive(
    path: str | Path,
    *,
    strict: bool = False,
) -> Path:
    """Resolve paths without replacing a Windows mapped drive with its UNC path."""
    expanded = Path(path).expanduser()
    if os.name == "nt":
        windows_path = PureWindowsPath(expanded)
        if windows_path.is_absolute() and windows_path.drive:
            resolved = expanded.resolve(strict=False)
            resolved_drive = PureWindowsPath(resolved).drive
            if resolved_drive.casefold() != windows_path.drive.casefold():
                if strict:
                    expanded.stat()
                return expanded
    return expanded.resolve(strict=strict)
