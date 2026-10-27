# SPDX-License-Identifier: GPL-3.0-or-later
"""Integration tests for the generated synthetic case fixture."""

import csv
from pathlib import Path

import netCDF4
import pytest


@pytest.mark.integration
def test_synthetic_case_contains_model_and_expected_results(
    synthetic_case: Path,
) -> None:
    mdu_path = synthetic_case / "simulations" / "r001" / "output" / "0" / "dflowfm.mdu"
    expected_path = synthetic_case / "expected" / "cells_per_segment_250m.csv"
    quantity_expected_path = synthetic_case / "expected" / "quantities_r001.json"

    assert (synthetic_case / ".synthetic_smt").is_file()
    assert mdu_path.is_file()
    assert expected_path.is_file()
    assert quantity_expected_path.is_file()

    partitioned = synthetic_case / "simulations" / "fm_partitioned"
    partition_map = partitioned / "DFM_OUTPUT_dflowfm" / "dflowfm_0000_map.nc"
    assert (partitioned / "dflowfm.mdu").is_file()
    assert (partitioned / "dimr_config.xml").is_file()
    assert not (partitioned / "smt.yml").exists()
    with netCDF4.Dataset(partition_map) as dataset:
        assert len(dataset.dimensions["time"]) == 40
        assert "mesh2d_flowelem_domain" in dataset.variables
        assert len(dataset.dimensions["nBedLayers"]) == 2
        assert len(dataset.dimensions["nSedTot"]) == 3
        assert "mesh2d_thlyr" in dataset.variables
        assert "mesh2d_u1" in dataset.variables
        assert "mesh2d_sxtot" in dataset.variables

    single = synthetic_case / "simulations" / "fm_single"
    single_map = single / "single_output" / "dflowfm_map.nc"
    assert (single / "dflowfm.mdu").is_file()
    assert not (single / "smt.yml").exists()
    with netCDF4.Dataset(single_map) as dataset:
        assert len(dataset.dimensions["time"]) == 40
        assert "mesh2d_flowelem_domain" not in dataset.variables

    with (synthetic_case / "expected" / "plain_fm" / "time_axis.csv").open(
        newline="", encoding="utf-8"
    ) as file:
        assert len(list(csv.DictReader(file))) == 40
    assert (
        synthetic_case / "expected" / "plain_fm" / "smb_r001_L1R1_250m.csv"
    ).is_file()
