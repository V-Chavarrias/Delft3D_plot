# SPDX-License-Identifier: GPL-3.0-or-later
"""Text decoding shared by the model-input readers."""

from __future__ import annotations

from pathlib import Path


def read_model_text(path: Path) -> str:
    """Read a model input file: UTF-8, falling back to Latin-1, without a BOM.

    D-Flow FM files may contain Windows-1252 characters such as ``°``, which
    Latin-1 decodes. A leading UTF-8 byte-order mark is removed so that it
    cannot change the first line of a file.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="latin-1")
    return text.removeprefix("\ufeff")
