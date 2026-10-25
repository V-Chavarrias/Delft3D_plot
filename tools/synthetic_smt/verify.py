# SPDX-License-Identifier: GPL-3.0-or-later
"""Self-check of a generated case with the libraries the tool will use (HYDROLIB-core, dfm_tools, rasterio, ...)."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np


def _ok(msg: str):
    print(f"  [ok] {msg}")


def verify(case: Path):
    import geopandas as gpd
    import netCDF4
    import rasterio
    import scipy.io
    from dfm_tools import open_partitioned_dataset
    from hydrolib.core.dflowfm.mdu.models import FMModel
    from hydrolib.core.dflowfm.polyfile.models import PolyFile
    from hydrolib.core.dimr.models import DIMR

    from fields import bed_level, map_fields, subrun_times
    from mesh import make_centreline, make_mesh
    from params import Params

    print("Verifying")
    p = Params()
    mesh = make_mesh(p, make_centreline(p))
    runs = subrun_times(p)
    sim_dir = case / "simulations" / "r001"

    # HYDROLIB-core can read the MDU (without loading referenced files) and the DIMR configuration
    for run in runs:
        mdu = FMModel(sim_dir / "output" / str(run.index) / "dflowfm.mdu", recurse=False)
        assert float(mdu.time.tstart) == run.tstart and float(mdu.time.tstop) == run.tstop, "MDU times"
        assert str(mdu.output.outputdir) == "results", "MDU output dir"
    dimr = DIMR(sim_dir / "output" / "0" / "dimr_config.xml")
    assert dimr.component[0].inputFile.name == "dflowfm.mdu"
    _ok("HYDROLIB-core reads MDU (TStart/TStop/OutputDir) and dimr_config.xml")

    # dfm_tools merges the partitions (ghost cells removed) into the global mesh with the analytic values
    sim = p.simulations[0]
    for run in runs:
        uds = open_partitioned_dataset(str(sim_dir / "output" / str(run.index) / "results" / "dflowfm_0*_map.nc"))
        assert uds.sizes["mesh2d_nFaces"] == mesh.n_faces, f"merged faces {uds.sizes['mesh2d_nFaces']} != {mesh.n_faces}"
        order = uds["mesh2d_flowelem_globalnr"].values.astype(int) - 1
        expected = map_fields(p, mesh, sim, run)["mesh2d_mor_bl"][:, order]
        got = uds["mesh2d_mor_bl"].values
        np.testing.assert_array_equal(np.isnan(got), np.isnan(expected))
        fin = np.isfinite(expected)
        np.testing.assert_allclose(got[fin], expected[fin], rtol=0, atol=1e-12)
        # morft has time units (as in r209), so xarray decodes it to datetimes
        expected_morft = np.datetime64("2021-02-07T23:00:00", "ns") + (run.morft * 1e9).astype("timedelta64[ns]")
        np.testing.assert_array_equal(uds["morft"].values, expected_morft)
        for name in ("mesh2d_s1", "mesh2d_waterdepth", "mesh2d_ucmag", "mesh2d_taus"):
            assert np.isfinite(uds[name].values).all(), f"{name}: missing time steps or non-finite values"
    _ok(f"dfm_tools merges {p.n_partitions} partitions -> {mesh.n_faces} faces; mor_bl equals the analytic field")

    variants = case / "simulations" / "variants"
    if variants.exists():
        uds = open_partitioned_dataset(str(variants / "non_partitioned" / "output" / "0" / "results" / "dflowfm_map.nc"))
        assert uds.sizes["mesh2d_nFaces"] == mesh.n_faces
        assert (variants / "running" / "output" / "work").is_dir()
        assert not (variants / "gap" / "output" / "2").exists()
        bad = variants / "corrupt_partition" / "output" / "0" / "results"
        with netCDF4.Dataset(bad / "dflowfm_0001_map.nc") as ds1, netCDF4.Dataset(bad / "dflowfm_0000_map.nc") as ds0:
            assert ds1["time"][1] != ds0["time"][1] and abs(ds1["mesh2d_mor_bl"][1, :]).max() > 1e300
        _ok("variants: non-partitioned readable; running has output/work; gap misses sub-run 2; corrupt partition present")

    # land boundary: .ldb only, readable by HYDROLIB-core
    ldb = PolyFile(case / "inputs" / "ldb" / "synthetic.ldb")
    assert len(ldb.objects) == 3 and not list((case / "inputs" / "ldb").glob("*.mat"))
    _ok("land boundary .ldb readable by HYDROLIB-core PolyFile (3 polylines)")

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
    assert abs(zcm * 0.01 - float(z[row, col])) < 1.0, "cm raster consistent with metre raster (different time)"
    _ok(f"VRT mosaic ({z.shape[1]}x{z.shape[0]}, EPSG:28992) and half-pixel-shifted cm GeoTIFF readable")

    vak = gpd.read_file(next((mea2d / "vakken").glob("*.shp")))
    assert {"MEAN", "COUNT", "A", "locatie", "code_uniek"} <= set(vak.columns)
    regions = {f.stem: gpd.read_file(f) for f in (case / "inputs" / "regions").glob("*.shp")}
    assert set(regions) == {"L1R1", "L1L3", "R1R3", "WIDE"}
    _ok(f"shapefiles readable: {len(vak)} vakken polygons, regions {sorted(regions)}")

    # SMB measurements: Matlab structure, unsorted times
    m = scipy.io.loadmat(case / "inputs" / "measurements" / "smb" / "L1R1_measured.mat", squeeze_me=True, struct_as_record=False)
    vm = m["data"].bl.val_mean
    assert vm.val.shape == (len(vm.rkm), len(vm.tim_dnum)) and np.any(np.diff(vm.tim_dnum) < 0)
    _ok(f"SMB .mat: data.bl.val_mean.val {vm.val.shape}, unsorted tim_dnum")

    # expected: full segments of the summer bed contain 10 x 4 cells
    with (case / "expected" / "cells_per_segment_250m.csv").open() as fh:
        counts = [int(r["n_cells"]) for r in csv.DictReader(fh) if r["region"] == "L1R1"]
    assert set(counts) == {40}, set(counts)
    with (case / "expected" / "smb_r001_L1R1_250m.csv").open() as fh:
        rows = list(csv.DictReader(fh))
    assert min(int(r["count"]) for r in rows) == 37  # 3 NaN faces in one segment at one time
    bl0 = bed_level(p, mesh.face_s, mesh.face_n, 0.0, sim.erosion_rate_m_per_day)
    assert np.isfinite(bl0).all()
    _ok("expected results consistent (40 summer-bed cells per segment; NaN/Inf excluded)")
    print("Verification passed")
