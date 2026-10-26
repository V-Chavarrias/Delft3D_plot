# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared pytest fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def synthetic_case(tmp_path_factory: pytest.TempPathFactory) -> Path:
    generator_directory = (
        Path(__file__).resolve().parents[1] / "tools" / "synthetic_smt"
    )
    generator_path = str(generator_directory)
    if generator_path not in sys.path:
        sys.path.insert(0, generator_path)

    from generate import main as generate_synthetic_case

    output = tmp_path_factory.mktemp("synthetic_smt")
    generate_synthetic_case(["--out", str(output)])
    return output
