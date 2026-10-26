# SPDX-License-Identifier: GPL-3.0-or-later
"""Errors raised while reading model input."""

from __future__ import annotations

from pathlib import Path


class ModelInputError(ValueError):
    """A model input file could not be parsed or interpreted."""

    def __init__(self, path: Path, message: str, hint: str) -> None:
        self.path = path.resolve(strict=False)
        self.message = message
        self.hint = hint
        super().__init__(f"{self.path}: {message}")
