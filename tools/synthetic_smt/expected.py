# SPDX-License-Identifier: GPL-3.0-or-later
"""Expected results, derived from the analytic fields and the mesh construction (not from the SMB algorithm)."""

from __future__ import annotations

import csv
import json
from datetime import timedelta
from pathlib import Path

import numpy as np

from fields import SubRunTimes, map_fields
from mesh import Mesh
from params import (
    GENERATOR_VERSION,
    GRAVITY_M_S2,
    REFERENCE_TIME,
    ROTATION_RATE_S_1,
    SEDIMENT_DIAMETERS_M,
    SEDIMENT_DENSITIES_KG_M3,
    WATER_DENSITY_KG_M3,
    Params,
    SimulationParams,
)
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
        _write_quantity_expected(folder, p, mesh, runs[0], sim)


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


def _write_quantity_expected(
    folder: Path,
    p: Params,
    mesh: Mesh,
    run: SubRunTimes,
    sim: SimulationParams,
) -> None:
    """Write hand-computable sample outputs for the quantity-engine integration test."""
    fields = map_fields(p, mesh, sim, run)
    time_index = 1
    candidates = np.flatnonzero(
        (mesh.face_n > -p.channel_half_width)
        & (mesh.face_n < p.channel_half_width)
        & (np.arange(mesh.n_faces) % 3 == 1)
        & (fields["mesh2d_waterdepth"][time_index] > 0.01)
    )
    face_index = int(candidates[0])
    velocity = float(fields["mesh2d_ucmag"][time_index, face_index])
    depth = float(fields["mesh2d_waterdepth"][time_index, face_index])
    water_level = float(fields["mesh2d_s1"][time_index, face_index])
    water_level_initial = float(fields["mesh2d_s0"][time_index, face_index])
    shear_stress = float(fields["mesh2d_taus"][time_index, face_index])
    diameters = np.asarray(SEDIMENT_DIAMETERS_M[: p.n_fractions], dtype=np.float64)
    densities = np.asarray(
        SEDIMENT_DENSITIES_KG_M3[: p.n_fractions],
        dtype=np.float64,
    )
    relative_density = (densities - WATER_DENSITY_KG_M3) / WATER_DENSITY_KG_M3
    fraction = fields["mesh2d_lyrfrac"][time_index, face_index, 0]
    grain_cumulative = np.concatenate(
        ([0.0], np.cumsum(fraction), [1.0 + 1e-8])
    )
    grain_boundaries = np.concatenate(
        ([diameters[0] / 2.0], diameters, [2.0 * diameters[-1]])
    )
    grain_percentiles = {
        f"d{percentile}": float(
            np.interp(percentile / 100.0, grain_cumulative, grain_boundaries)
        )
        for percentile in (10, 50, 90)
    }
    layer_thickness = fields["mesh2d_thlyr"][time_index, face_index]
    suspended_x = fields["mesh2d_sxtot"][time_index, face_index]
    suspended_y = fields["mesh2d_sytot"][time_index, face_index]
    bedload_x = fields["mesh2d_sbcx"][time_index, face_index]
    bedload_y = fields["mesh2d_sbcy"][time_index, face_index]
    transport_magnitudes = np.hypot(suspended_x, suspended_y)
    expected = {
        "generator_version": GENERATOR_VERSION,
        "simulation": sim.label,
        "time_index": time_index,
        "face_index": face_index,
        "gravity_m_s2": GRAVITY_M_S2,
        "water_density_kg_m3": WATER_DENSITY_KG_M3,
        "sediment_diameters_m": diameters.tolist(),
        "sediment_densities_kg_m3": densities.tolist(),
        "froude_number": velocity / np.sqrt(GRAVITY_M_S2 * depth),
        "specific_discharge_m2_s": velocity * depth,
        "energy_head_m": water_level + velocity**2 / (2.0 * GRAVITY_M_S2),
        "shields_number_times_diameter_m": (
            shear_stress / (GRAVITY_M_S2 * WATER_DENSITY_KG_M3 * relative_density)
        ).tolist(),
        "velocity_direction_degrees": float(
            np.mod(np.degrees(np.arctan2(0.6 * velocity, 0.8 * velocity)), 360.0)
        ),
        "velocity_vector_m_s": [0.6 * velocity, 0.8 * velocity],
        "water_level_rate_m_s": (water_level - water_level_initial)
        / float(run.time[time_index] - run.time[time_index - 1]),
        "vorticity_s_1": ROTATION_RATE_S_1,
        "total_sediment_thickness_m": float(np.sum(layer_thickness)),
        "morphological_active_area_m2": (
            float(mesh.face_area[face_index])
            if np.sum(layer_thickness) >= 0.001
            else 0.0
        ),
        "sediment_transport_magnitude_m2_s": transport_magnitudes.tolist(),
        "sediment_transport_magnitude_sum_m2_s": float(
            np.sum(transport_magnitudes)
        ),
        "bed_load_transport_magnitude_m2_s": np.hypot(bedload_x, bedload_y).tolist(),
        "mean_grain_size_arithmetic_m": float(np.sum(fraction * diameters)),
        "mean_grain_size_geometric_m": float(
            np.exp(np.sum(fraction * np.log(diameters)))
        ),
        **grain_percentiles,
    }
    (folder / f"quantities_{sim.label}.json").write_text(
        json.dumps(expected, indent=2) + "\n",
        encoding="utf-8",
    )
