# SPDX-License-Identifier: GPL-3.0-or-later
"""Self-check generated SMT and plain FM cases with the libraries the tool will use."""

from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np


def _ok(msg: str):
    print(f"  [ok] {msg}")


# Minimal readers written for this check only. They deliberately do not use
# d3dplot's readers, so the self-check is independent of the code it verifies.
def _mdu_values(path: Path) -> dict[tuple[str, str], str]:
    """Return the MDU keywords as {(section, key): value}, names in lower case."""
    values: dict[tuple[str, str], str] = {}
    section = ""
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
        elif "=" in line:
            key, value = line.split("=", 1)
            values[(section, key.strip().lower())] = value.strip()
    return values


def _dimr_first_input_file(path: Path) -> str:
    """Return the file name of the first <inputFile> in a DIMR configuration."""
    root = ET.parse(path).getroot()
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "inputFile" and element.text:
            return Path(element.text.strip()).name
    raise AssertionError(f"no inputFile in {path}")


def _tekal_object_count(path: Path) -> int:
    """Count Tekal objects: a name line, a 'rows cols' line, then the rows."""
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    count = 0
    index = 0
    while index < len(lines):
        rows = int(lines[index + 1].split()[0])
        index += 2 + rows
        count += 1
    return count


def verify(case: Path):
    import geopandas as gpd
    import netCDF4
    import rasterio
    import scipy.io
    from dfm_tools import open_partitioned_dataset
    from expected import SEGMENT_LENGTH
    from fields import (
        bed_level,
        map_fields,
        plain_fm_times,
        subrun_times,
    )
    from mesh import make_centreline, make_mesh
    from params import Params

    print("Verifying")
    p = Params()
    mesh = make_mesh(p, make_centreline(p))
    runs = subrun_times(p)
    plain_run = plain_fm_times(p)
    sim_dir = case / "simulations" / "r001"

    # Independent minimal readers check the MDU times, output dir and DIMR config.
    for run in runs:
        values = _mdu_values(sim_dir / "output" / str(run.index) / "dflowfm.mdu")
        assert (
            float(values[("time", "tstart")]) == run.tstart
            and float(values[("time", "tstop")]) == run.tstop
        ), "MDU times"
        assert values[("output", "outputdir")] == "results", "MDU output dir"
    assert (
        _dimr_first_input_file(sim_dir / "output" / "0" / "dimr_config.xml")
        == "dflowfm.mdu"
    )
    _ok("minimal readers: MDU (TStart/TStop/OutputDir) and dimr_config.xml")

    # dfm_tools merges partitions (without ghosts) into the mesh with analytic values.
    sim = p.simulations[0]
    for run in runs:
        uds = open_partitioned_dataset(
            str(sim_dir / "output" / str(run.index) / "results" / "dflowfm_0*_map.nc")
        )
        assert uds.sizes["mesh2d_nFaces"] == mesh.n_faces, (
            f"merged faces {uds.sizes['mesh2d_nFaces']} != {mesh.n_faces}"
        )
        order = uds["mesh2d_flowelem_globalnr"].values.astype(int) - 1
        expected = map_fields(p, mesh, sim, run)["mesh2d_mor_bl"][:, order]
        got = uds["mesh2d_mor_bl"].values
        np.testing.assert_array_equal(np.isnan(got), np.isnan(expected))
        fin = np.isfinite(expected)
        np.testing.assert_allclose(got[fin], expected[fin], rtol=0, atol=1e-12)
        # morft has time units (as in r209), so xarray decodes it to datetimes
        expected_morft = np.datetime64("2021-02-07T23:00:00", "ns") + (
            run.morft * 1e9
        ).astype("timedelta64[ns]")
        np.testing.assert_array_equal(uds["morft"].values, expected_morft)
        for name in ("mesh2d_s1", "mesh2d_waterdepth", "mesh2d_ucmag", "mesh2d_taus"):
            assert np.isfinite(uds[name].values).all(), (
                f"{name}: missing time steps or non-finite values"
            )
    _ok(
        f"dfm_tools merges {p.n_partitions} partitions -> {mesh.n_faces} faces; "
        "mor_bl equals the analytic field"
    )

    variants = case / "simulations" / "variants"
    if variants.exists():
        uds = open_partitioned_dataset(
            str(
                variants
                / "non_partitioned"
                / "output"
                / "0"
                / "results"
                / "dflowfm_map.nc"
            )
        )
        assert uds.sizes["mesh2d_nFaces"] == mesh.n_faces
        assert (variants / "running" / "output" / "work").is_dir()
        assert not (variants / "gap" / "output" / "2").exists()
        bad = variants / "corrupt_partition" / "output" / "0" / "results"
        with (
            netCDF4.Dataset(bad / "dflowfm_0001_map.nc") as ds1,
            netCDF4.Dataset(bad / "dflowfm_0000_map.nc") as ds0,
        ):
            assert (
                ds1["time"][1] != ds0["time"][1]
                and abs(ds1["mesh2d_mor_bl"][1, :]).max() > 1e300
            )
        _ok(
            "variants: non-partitioned readable; running has output/work; "
            "gap misses sub-run 2; corrupt partition present"
        )

    plain_partitioned = case / "simulations" / "fm_partitioned"
    plain_single = case / "simulations" / "fm_single"
    assert not (plain_partitioned / "smt.yml").exists()
    assert not (plain_single / "smt.yml").exists()
    assert not (plain_single / "dimr_config.xml").exists()
    assert list(plain_partitioned.glob("*.mdu")) == [plain_partitioned / "dflowfm.mdu"]
    assert list(plain_single.glob("*.mdu")) == [plain_single / "dflowfm.mdu"]
    mdu_partitioned = _mdu_values(plain_partitioned / "dflowfm.mdu")
    assert float(mdu_partitioned[("time", "tstop")]) == plain_run.tstop
    assert mdu_partitioned.get(("output", "outputdir"), "") == ""
    assert (
        _dimr_first_input_file(plain_partitioned / "dimr_config.xml") == "dflowfm.mdu"
    )
    _ok(
        "plain FM MDUs parse; partitioned run has a root DIMR config; "
        "neither has an SMT manifest"
    )

    partitioned_output = plain_partitioned / "DFM_OUTPUT_dflowfm"
    partition_maps = sorted(partitioned_output.glob("dflowfm_????_map.nc"))
    assert len(partition_maps) == p.n_partitions
    merged_plain = open_partitioned_dataset(
        str(partitioned_output / "dflowfm_0*_map.nc")
    )
    assert merged_plain.sizes["mesh2d_nFaces"] == mesh.n_faces
    assert merged_plain.sizes["time"] == len(plain_run.time)
    with netCDF4.Dataset(partition_maps[0]) as ds:
        partition_time = ds["time"][:]
        partition_morft = ds["morft"][:]
        assert "mesh2d_flowelem_domain" in ds.variables
    single_mdu = _mdu_values(plain_single / "dflowfm.mdu")
    assert single_mdu[("output", "outputdir")] == "single_output"
    single_map = plain_single / "single_output" / "dflowfm_map.nc"
    merged_single = open_partitioned_dataset(str(single_map))
    assert merged_single.sizes["mesh2d_nFaces"] == mesh.n_faces
    assert merged_single.sizes["time"] == len(plain_run.time)
    with netCDF4.Dataset(single_map) as ds:
        single_time = ds["time"][:]
        single_morft = ds["morft"][:]
        assert "mesh2d_flowelem_domain" not in ds.variables
    np.testing.assert_array_equal(partition_time, plain_run.time)
    np.testing.assert_array_equal(partition_morft, plain_run.morft)
    np.testing.assert_array_equal(single_time, plain_run.time)
    np.testing.assert_array_equal(single_morft, plain_run.morft)
    three_d_root = case / "simulations" / "fm_3d"
    three_d_map = three_d_root / "output_3d" / "dflowfm_map.nc"
    assert (three_d_root / "synthetic.sub").is_file()
    with netCDF4.Dataset(three_d_map) as ds:
        assert ds["mesh2d_ucmag"].dimensions[-1] == "nLayers"
        assert ds["mesh2d_flowelem_zw"].dimensions[-1] == "nInterfaces"
        assert ds["mesh2d_cTR1"].dimensions[-1] == "nLayers"
    _ok(
        "plain FM: partitioned and single maps merge to the global mesh "
        "with 40 expected map times; 3D FM fields are readable"
    )

    # land boundary: .ldb only, with 3 polylines
    ldb_objects = _tekal_object_count(case / "inputs" / "ldb" / "synthetic.ldb")
    assert ldb_objects == 3 and not list((case / "inputs" / "ldb").glob("*.mat"))
    _ok("land boundary .ldb has 3 polylines (minimal Tekal count)")

    # finished marker only in partition .dia files
    res0 = sim_dir / "output" / "0" / "results"
    assert "Computation finished" in (res0 / "dflowfm_0000.dia").read_text()
    assert "Computation finished" not in (res0 / "dflowfm.dia").read_text()
    _ok("'Computation finished' in partition .dia files only (as r209)")

    # rasters: VRT mosaic readable by GDAL; cm file with factor 0.01 gives metres
    mea2d = case / "inputs" / "measurements" / "2d"
    vrt = next((mea2d / "rasters").rglob("*.vrt"))
    with rasterio.open(vrt) as src:
        z = src.read(1, masked=True)
        assert z.count() > 0 and src.crs.to_epsg() == 28992
        row, col = np.argwhere(~z.mask)[len(np.argwhere(~z.mask)) // 2]
        x, y = src.xy(row, col)
    cm = next((mea2d / "rasters").rglob("*_cm.tif"))
    with rasterio.open(cm) as src:
        zcm = next(src.sample([(x, y)]))[0]
    assert abs(zcm * 0.01 - float(z[row, col])) < 1.0, (
        "cm raster consistent with metre raster (different time)"
    )
    _ok(
        f"VRT mosaic ({z.shape[1]}x{z.shape[0]}, EPSG:28992) and "
        "half-pixel-shifted cm GeoTIFF readable"
    )

    vak = gpd.read_file(next((mea2d / "vakken").glob("*.shp")))
    assert {"MEAN", "COUNT", "A", "locatie", "code_uniek"} <= set(vak.columns)
    regions = {
        f.stem: gpd.read_file(f) for f in (case / "inputs" / "regions").glob("*.shp")
    }
    assert set(regions) == {"L1R1", "L1L3", "R1R3", "WIDE"}
    _ok(f"shapefiles readable: {len(vak)} vakken polygons, regions {sorted(regions)}")

    # SMB measurements: Matlab structure, unsorted times
    m = scipy.io.loadmat(
        case / "inputs" / "measurements" / "smb" / "L1R1_measured.mat",
        squeeze_me=True,
        struct_as_record=False,
    )
    vm = m["data"].bl.val_mean
    assert vm.val.shape == (len(vm.rkm), len(vm.tim_dnum)) and np.any(
        np.diff(vm.tim_dnum) < 0
    )
    _ok(f"SMB .mat: data.bl.val_mean.val {vm.val.shape}, unsorted tim_dnum")

    # expected: full segments of the summer bed contain 10 x 4 cells
    with (case / "expected" / "cells_per_segment_250m.csv").open() as fh:
        counts = [
            int(r["n_cells"]) for r in csv.DictReader(fh) if r["region"] == "L1R1"
        ]
    assert set(counts) == {40}, set(counts)
    with (case / "expected" / "smb_r001_L1R1_250m.csv").open() as fh:
        rows = list(csv.DictReader(fh))
    assert (
        min(int(r["count"]) for r in rows) == 37
    )  # 3 NaN faces in one segment at one time
    bl0 = bed_level(p, mesh.face_s, mesh.face_n, 0.0, sim.erosion_rate_m_per_day)
    assert np.isfinite(bl0).all()
    _ok(
        "expected results consistent (40 summer-bed cells per segment; "
        "NaN/Inf excluded)"
    )

    plain_expected = case / "expected" / "plain_fm"
    with (plain_expected / "time_axis.csv").open() as fh:
        plain_axis = list(csv.DictReader(fh))
    assert len(plain_axis) == len(plain_run.time)
    np.testing.assert_array_equal(
        [float(row["time_s"]) for row in plain_axis], plain_run.time
    )
    np.testing.assert_array_equal(
        [float(row["morft_s"]) for row in plain_axis], plain_run.morft
    )
    with (plain_expected / "smb_r001_L1R1_250m.csv").open() as fh:
        plain_smb = list(csv.DictReader(fh))
    assert len(plain_smb) == len(plain_run.time) * int(round(p.length / SEGMENT_LENGTH))
    _ok(
        "plain FM expected time axis and SMB statistics are generated for all map times"
    )
    print("Verification passed")
