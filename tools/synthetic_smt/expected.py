# SPDX-License-Identifier: GPL-3.0-or-later
"""Expected results, derived from the analytic fields and the mesh construction (not from the SMB algorithm)."""

from __future__ import annotations

import csv
from datetime import timedelta
from pathlib import Path

import numpy as np

from fields import SubRunTimes, map_fields
from mesh import Mesh
from params import REFERENCE_TIME, Params, SimulationParams
from project_inputs import REGIONS, km_of_s

SEGMENT_LENGTH = 250.0
EXACT_REGIONS = ("L1R1", "L1L3", "R1R3")  # WIDE is excluded: cut lines are not exact far from the axis


def merged_time_axis(runs: list[SubRunTimes]) -> list[dict]:
    """The first map time of sub-run k>0 duplicates the last of sub-run k-1; the latter is kept."""
    rows = []
    for run in runs:
        for k, (t, m) in enumerate(zip(run.time, run.morft)):
            if run.index > 0 and k == 0:
                continue
            rows.append({"subrun": run.index, "local_index": k, "time_s": t, "morft_s": m,
                         "time": (REFERENCE_TIME + timedelta(seconds=float(t))).isoformat(),
                         "morph_time": (REFERENCE_TIME + timedelta(seconds=float(m))).isoformat()})
    return rows


def segments(p: Params, mesh: Mesh) -> np.ndarray:
    return np.floor(mesh.face_s / SEGMENT_LENGTH).astype(int)


def _write_csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def write_expected(folder: Path, p: Params, mesh: Mesh, runs: list[SubRunTimes]):
    axis = merged_time_axis(runs)
    _write_csv(folder / "time_axis.csv", [{"time_index": i, **r} for i, r in enumerate(axis)])

    seg = segments(p, mesh)
    n_seg = int(round(p.length / SEGMENT_LENGTH))
    cell_rows = []
    for region in EXACT_REGIONS:
        n0, n1 = REGIONS[region]
        in_region = (mesh.face_n > n0) & (mesh.face_n < n1)
        for k in range(n_seg):
            faces = np.flatnonzero(in_region & (seg == k))
            cell_rows.append({"region": region, "segment": k, "km": km_of_s(p, (k + 0.5) * SEGMENT_LENGTH),
                              "n_cells": len(faces), "globalnr": " ".join(str(f + 1) for f in faces)})
    _write_csv(folder / "cells_per_segment_250m.csv", cell_rows)

    for sim in p.simulations:
        _write_smb_stats(folder, p, mesh, runs, sim, axis, seg, n_seg)


def _write_smb_stats(folder: Path, p: Params, mesh: Mesh, runs, sim: SimulationParams, axis, seg, n_seg):
    values = {run.index: map_fields(p, mesh, sim, run)["mesh2d_mor_bl"] for run in runs}
    for region in EXACT_REGIONS:
        n0, n1 = REGIONS[region]
        in_region = (mesh.face_n > n0) & (mesh.face_n < n1)
        rows = []
        for ti, t in enumerate(axis):
            v_all = values[t["subrun"]][t["local_index"]]
            for k in range(n_seg):
                sel = in_region & (seg == k) & np.isfinite(v_all)  # Matlab excludes NaN and Inf cells
                v, a = v_all[sel], mesh.face_area[sel]
                rows.append({
                    "time_index": ti, "morph_time": t["morph_time"], "segment": k,
                    "km": km_of_s(p, (k + 0.5) * SEGMENT_LENGTH), "count": int(sel.sum()),
                    "mean": v.mean(), "mean_area_weighted": np.sum(v * a) / np.sum(a),
                    "std": v.std(ddof=1), "max": v.max(), "min": v.min(), "sum": v.sum(),
                    "sum_per_length": v.sum() / SEGMENT_LENGTH,
                })
        _write_csv(folder / f"smb_{sim.label}_{region}_250m.csv", rows)
