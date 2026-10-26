# SPDX-License-Identifier: GPL-3.0-or-later
"""Integration tests for the generated synthetic case fixture."""

from pathlib import Path

import pytest


@pytest.mark.integration
def test_synthetic_case_contains_model_and_expected_results(
    synthetic_case: Path,
) -> None:
    mdu_path = synthetic_case / "simulations" / "r001" / "output" / "0" / "dflowfm.mdu"
    expected_path = synthetic_case / "expected" / "cells_per_segment_250m.csv"

    assert (synthetic_case / ".synthetic_smt").is_file()
    assert mdu_path.is_file()
    assert expected_path.is_file()
