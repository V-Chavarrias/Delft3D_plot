# SPDX-License-Identifier: GPL-3.0-or-later
"""Path normalization helpers for configured input paths."""

from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath


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
