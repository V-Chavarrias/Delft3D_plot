# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate the synthetic SMT case (D-Flow FM output + project inputs + measurements + expected results).

Usage:
    python tools/synthetic_smt/generate.py [--out data_local/synthetic_smt_01] [--force] [--no-variants] [--no-verify]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from expected import merged_time_axis, write_expected
from fields import SubRunTimes, bed_level, map_fields, subrun_times
from fm_files import (write_dia, write_dimr_config, write_map, write_mdu, write_mor, write_net, write_sed,
                      write_smt_yml)
from matlab_script import write_matlab_script
from measurements import write_catalogue, write_rasters, write_smb_mat, write_vakken
from mesh import Centreline, Mesh, make_centreline, make_mesh, partition_subset
from params import GENERATOR_VERSION, REFERENCE_TIME, Params, SimulationParams
from project_inputs import write_land_boundaries, write_regions, write_rkm_files

MARKER = ".synthetic_smt"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "data_local" / "synthetic_smt_01"


def _check_output_folder(out: Path, force: bool):
    """Never write to the P-drive, and only replace folders created by this generator."""
    resolved = out.resolve()
    drive = resolved.drive.lower()
    if drive.startswith("p:") or str(resolved).replace("\\", "/").lower().startswith("/p/"):
        raise SystemExit(f"Refusing to write to the P-drive: {resolved}")
    if resolved.exists() and any(resolved.iterdir()):
        if not (resolved / MARKER).is_file():
            raise SystemExit(f"Output folder exists, is not empty and was not created by this generator: {resolved}")
        if not force:
            raise SystemExit(f"Output folder exists: {resolved}. Use --force to regenerate it.")
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True, exist_ok=True)
    (resolved / MARKER).write_text(f"synthetic_smt {GENERATOR_VERSION}\n", encoding="utf-8")


def write_simulation(root: Path, p: Params, mesh: Mesh, sim: SimulationParams, runs: list[SubRunTimes], *,
                     partitioned: bool = True, skip_subruns: tuple[int, ...] = (), truncate_last: bool = False,
                     finished_last: bool = True, work_folder: bool = False,
                     corrupt: tuple[int, int, tuple[int, ...]] | None = None):
    """`corrupt` = (sub-run, partition, map-time indices) with garbage time/values (as r209 sub-run 0, partition 0021)."""
    root.mkdir(parents=True, exist_ok=True)
    write_smt_yml(root / "smt.yml", p, len(runs))
    write_sed(root / "sed" / "synthetic.sed", p)
    initial_bl = bed_level(p, mesh.face_s, mesh.face_n, 0.0, sim.erosion_rate_m_per_day)
    full = partition_subset(mesh, None)
    subsets = [partition_subset(mesh, k) for k in range(p.n_partitions)] if partitioned else []

    for run in runs:
        if run.index in skip_subruns:
            continue
        fields = map_fields(p, mesh, sim, run)
        last = run.index == runs[-1].index
        if last and truncate_last:
            run = replace(run, time=run.time[:2], morft=run.morft[:2])
            fields = {k: v[:2] for k, v in fields.items()}
        finished = finished_last or not last

        d = root / "output" / str(run.index)
        d.mkdir(parents=True, exist_ok=True)
        map_interval = (p.spinup_s, run.tstart, run.tstop)
        write_mdu(d / "dflowfm.mdu", p, run, "synthetic_net.nc", map_interval)
        write_net(d / "synthetic_net.nc", p, mesh, full, initial_bl)
        write_mor(d / "mor.mor", p)
        write_dimr_config(d / "dimr_config.xml", p.n_partitions if partitioned else None)
        res = d / "results"
        if partitioned:
            for sub in subsets:
                k = sub.partition
                bad = corrupt[2] if corrupt and corrupt[0] == run.index and corrupt[1] == k else ()
                write_mdu(d / f"dflowfm_{k:04d}.mdu", p, run, "synthetic_net.nc", map_interval)
                write_map(res / f"dflowfm_{k:04d}_map.nc", p, mesh, sub, run, fields, initial_bl, partitioned=True,
                          corrupt_times=bad)
                write_dia(res / f"dflowfm_{k:04d}.dia", run, k, p.n_partitions, finished, f"dflowfm_{k:04d}.mdu")
            write_dia(res / "dflowfm.dia", run, 0, p.n_partitions, False, "dflowfm.mdu")
        else:
            write_map(res / "dflowfm_map.nc", p, mesh, full, run, fields, initial_bl, partitioned=False)
            write_dia(res / "dflowfm.dia", run, 0, 1, finished, "dflowfm.mdu")
    if work_folder:
        (root / "output" / "work").mkdir(parents=True, exist_ok=True)


VARIANTS = {
    # name: (description, keyword arguments of write_simulation)
    "running": ("SMT run in progress: output/work exists, last sub-run truncated and not finished",
                {"truncate_last": True, "finished_last": False, "work_folder": True}),
    "crashed": ("last sub-run stopped before TStop, no 'Computation finished'",
                {"truncate_last": True, "finished_last": False}),
    "non_partitioned": ("same as r001 but one map file per sub-run (no partitions, no domain numbers)",
                        {"partitioned": False}),
    "gap": ("sub-run 2 is missing: validation must report an error", {"skip_subruns": (2,)}),
    "corrupt_partition": ("sub-run 0, partition 1: garbage time/morft and +-1e308 values at map times 1-2 "
                          "(as r209 sub-run 0, partition 0021)", {"corrupt": (0, 1, (1, 2))}),
}


def write_readme(out: Path, p: Params, runs: list[SubRunTimes], mesh: Mesh, variants: bool):
    axis = merged_time_axis(runs)
    lines = [
        "# Synthetic SMT case",
        "",
        f"Generated by `tools/synthetic_smt/generate.py` (version {GENERATOR_VERSION}). Do not edit by hand: regenerate.",
        "",
        "Artificial D-Flow FM SMT simulation with the **structure and file formats** of the Maas run `r209`",
        "(sub-runs, partitions with ghost cells, map metadata, `.dia`, MDU, `.mor`), but small and with analytic fields,",
        "so expected results are known. No project data is included.",
        "",
        "## Contents",
        "",
        "| Path | Content |",
        "|------|---------|",
        "| `simulations/r001`, `r002` | SMT runs (4 sub-runs × 3 partitions); r002 erodes faster (for differences between simulations); r001 has NaN/Inf faces |",
        "| `simulations/variants/*` | running, crashed, non-partitioned, missing sub-run, corrupt partition |" if variants else "",
        "| `inputs/rkm` | river-km files, fixed format (`MA` = Maas, `SY` = generic, `WA` = Rijntakken) |",
        "| `inputs/regions` | region shapefiles: `L1R1` summer bed, `L1L3`/`R1R3` halves, `WIDE` whole width |",
        "| `inputs/ldb` | land boundary (`.ldb`, Tekal) |",
        "| `inputs/measurements/smb` | `<region>_measured.mat` (Matlab structure, unsorted times) |",
        "| `inputs/measurements/2d` | GeoTIFF/VRT rasters, polygon shapefiles (\"vakken\"), catalogues (`time, file, factor`) |",
        "| `expected` | merged time axis, cells per segment, SMB statistics per simulation and region |",
        "| `scripts/main_plot_synthetic.m` | Matlab `D3D_plot` input; run it to create the Matlab reference |",
        "| `parameters.json` | all generator parameters |",
        "",
        "## Key facts",
        "",
        f"- Mesh: {mesh.n_faces} faces ({int((mesh.face_nodes[:, 3] < 0).sum())} triangles), "
        f"{p.n_partitions} partitions (2 = left-floodplain strip along the whole river).",
        f"- Map time units: `seconds since {REFERENCE_TIME:%Y-%m-%d %H:%M:%S} +01:00`; MDU `RefDate = {p.mdu_refdate}` (mismatch on purpose).",
        f"- MorFac {p.morfac:g}, spin-up {p.spinup_s:.0f} s; `morft` in the map files.",
        "- The first map time of sub-run k>0 duplicates the last of sub-run k-1.",
        "- Map files have EPSG 0 (as r209); inputs are EPSG:28992.",
        "",
        "## Merged time axis",
        "",
        "| # | sub-run | hydrodynamic time | morphological time |",
        "|---|---------|-------------------|--------------------|",
    ]
    lines += [f"| {i} | {r['subrun']} | {r['time']} | {r['morph_time']} |" for i, r in enumerate(axis)]
    (out / "README.md").write_text("\n".join(x for x in lines if x is not None) + "\n", encoding="utf-8")


def generate(out: Path, force: bool = False, variants: bool = True) -> Path:
    p = Params()
    _check_output_folder(out, force)
    cl: Centreline = make_centreline(p)
    mesh = make_mesh(p, cl)
    runs = subrun_times(p)

    sims = out / "simulations"
    for sim in p.simulations:
        write_simulation(sims / sim.label, p, mesh, sim, runs)
        print(f"  simulation {sim.label}")
    if variants:
        base = p.simulations[0]
        for name, (_, kwargs) in VARIANTS.items():
            write_simulation(sims / "variants" / name, p, mesh, base, runs, **kwargs)
            print(f"  variant {name}")

    inputs = out / "inputs"
    write_rkm_files(inputs / "rkm", p, cl)
    write_regions(inputs / "regions", p, cl)
    write_land_boundaries(inputs / "ldb", p, cl)
    erosion = p.simulations[0].erosion_rate_m_per_day
    mea = inputs / "measurements"
    raster_entries = write_rasters(mea / "2d" / "rasters", p, cl, mesh, erosion)
    vakken_entries = write_vakken(mea / "2d" / "vakken", p, cl, erosion)
    write_catalogue(mea / "2d" / "catalogue_raster.csv", raster_entries)
    write_catalogue(mea / "2d" / "catalogue_vakken.csv", vakken_entries)
    write_smb_mat(mea / "smb", p, mesh, erosion, [e[1] for e in vakken_entries])
    print("  inputs and measurements")

    write_expected(out / "expected", p, mesh, runs)
    times = [REFERENCE_TIME + timedelta(seconds=float(r.morft[-1])) for r in runs]
    times = [REFERENCE_TIME] + times
    write_matlab_script(out / "scripts" / "main_plot_synthetic.m", p.km0, p.km0 + p.length / 1000.0, times)
    (out / "parameters.json").write_text(json.dumps(p.to_dict(), indent=2, default=str), encoding="utf-8")
    write_readme(out, p, runs, mesh, variants)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--force", action="store_true", help="replace an existing case created by this generator")
    ap.add_argument("--no-variants", action="store_true")
    ap.add_argument("--no-verify", action="store_true")
    args = ap.parse_args(argv)
    print(f"Generating synthetic SMT case in {args.out}")
    out = generate(args.out, force=args.force, variants=not args.no_variants)
    if not args.no_verify:
        from verify import verify
        verify(out)
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file()) / 1e6
    print(f"Done: {out} ({size:.1f} MB)")


if __name__ == "__main__":
    sys.exit(main())
