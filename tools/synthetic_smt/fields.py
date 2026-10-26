# SPDX-License-Identifier: GPL-3.0-or-later
"""Analytic model fields and SMT time structure. Expected results are derived from the same functions."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from mesh import Mesh
from params import Params, SimulationParams

RHO = 1000.0
G = 9.81
CHEZY = 40.0
DAY = 86_400.0


@dataclass
class SubRunTimes:
    index: int
    tstart: float  # s since reference (map time units)
    tstop: float
    time: np.ndarray  # hydrodynamic map times (s since reference)
    morft: np.ndarray  # morphological time at the map times (s since reference)


def subrun_times(p: Params) -> list[SubRunTimes]:
    runs = []
    tstart, mor = 0.0, 0.0
    for k, days in enumerate(p.hydro_days_after_spinup):
        tstop = tstart + p.spinup_s + days * DAY
        time = np.array([tstart, tstart + p.spinup_s, tstop])
        mor_end = mor + p.morfac * days * DAY
        runs.append(SubRunTimes(k, tstart, tstop, time, np.array([mor, mor, mor_end])))
        tstart, mor = tstop, mor_end
    return runs


def plain_fm_times(p: Params) -> SubRunTimes:
    """One regular run spanning the hydrograph duration, with one initial spin-up."""
    tstop = p.spinup_s + sum(p.hydro_days_after_spinup) * DAY
    time = np.linspace(0.0, tstop, p.plain_fm_map_times)
    morft = np.maximum(time - p.spinup_s, 0.0) * p.morfac
    return SubRunTimes(0, 0.0, tstop, time, morft)


def bed_level(p: Params, s, n, t_mor_s, erosion_rate_m_per_day: float):
    s, n = np.asarray(s, float), np.asarray(n, float)
    base = p.z0 - p.slope * s
    channel = np.abs(n) < p.channel_half_width
    bars = p.bar_amplitude * np.sin(2 * np.pi * s / p.bar_wavelength) * np.cos(np.pi * n / (2 * p.channel_half_width))
    erosion = erosion_rate_m_per_day * np.asarray(t_mor_s, float) / DAY
    return np.where(channel, base + bars + erosion, base + p.floodplain_height)


def hydro_fields(p: Params, s, bl, stage: float):
    s1 = np.broadcast_to(p.z0 - p.slope * np.asarray(s, float) + p.base_depth + stage, np.shape(bl)).copy()
    h = np.maximum(s1 - bl, 0.0)
    q = 2.0 * (1.0 + stage)
    u = np.where(h > 0.05, q / np.maximum(h, 0.05), 0.0)
    taus = RHO * G * u**2 / CHEZY**2
    return s1, h, u, taus


def layer_fractions(p: Params, s) -> np.ndarray:
    """(F, n_bed_layers, n_fractions); sums to 1 per layer."""
    s = np.asarray(s, float)
    f1 = 0.3 + 0.1 * np.sin(2 * np.pi * s / 1000.0)
    f2 = np.full_like(f1, 0.5)
    top = np.stack([f1, f2, 1.0 - f1 - f2], axis=-1)
    sub = np.broadcast_to(np.array([0.3, 0.4, 0.3]), top.shape)
    return np.stack([top, sub], axis=1)


def special_faces(p: Params, mesh: Mesh) -> dict[str, np.ndarray]:
    """Faces that get NaN / Inf bed level (r001, sub-run 2, last map time): tests the SMB exclusion rule."""
    channel_col = np.isclose(mesh.face_n, -12.5)
    nan = np.flatnonzero(channel_col & np.isin(mesh.face_i, [50, 51, 52]))
    inf = np.flatnonzero(channel_col & (mesh.face_i == 75))
    return {"nan": nan, "inf": inf, "subrun": np.array([2]), "time_index": np.array([2])}


def map_fields(p: Params, mesh: Mesh, sim: SimulationParams, run: SubRunTimes) -> dict[str, np.ndarray]:
    """All time-dependent fields of one sub-run on the global mesh: arrays (time, F[, ...])."""
    nt = len(run.time)
    bl = np.stack([bed_level(p, mesh.face_s, mesh.face_n, run.morft[k], sim.erosion_rate_m_per_day) for k in range(nt)])
    if sim.nan_faces:
        sp = special_faces(p, mesh)
        if run.index in sp["subrun"]:
            k = int(sp["time_index"][0])
            bl[k, sp["nan"]] = np.nan
            bl[k, sp["inf"]] = np.inf
    stage = p.stage_m[run.index]
    bl_finite = np.where(np.isfinite(bl), bl, bed_level(p, mesh.face_s, mesh.face_n, run.morft[:, None], sim.erosion_rate_m_per_day))
    s1, h, u, taus = hydro_fields(p, mesh.face_s[None, :], bl_finite, stage)
    frac = np.broadcast_to(layer_fractions(p, mesh.face_s), (nt, mesh.n_faces, p.n_bed_layers, p.n_fractions))
    return {
        "mesh2d_mor_bl": bl,
        "mesh2d_s1": s1,
        "mesh2d_waterdepth": h,
        "mesh2d_ucmag": u,
        "mesh2d_taus": taus,
        "mesh2d_lyrfrac": np.array(frac),
    }
