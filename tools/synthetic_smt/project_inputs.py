# SPDX-License-Identifier: GPL-3.0-or-later
"""Project inputs in the same formats as the Maas project: river-km files, region shapefiles, land boundaries."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import Polygon

from mesh import Centreline
from params import Params

CRS = "EPSG:28992"

# regions: name -> (n_min, n_max) across the channel (n > 0 = left bank)
REGIONS = {
    "L1R1": (-50.0, 50.0),  # summer bed
    "L1L3": (0.0, 50.0),  # left half of the summer bed
    "R1R3": (-50.0, 0.0),  # right half of the summer bed
    "WIDE": (-350.0, 350.0),  # whole model width: wider than a small half_width -> coverage warning
}


def km_of_s(p: Params, s):
    return p.km0 + np.asarray(s, float) / 1000.0


def write_rkm_file(path: Path, cl: Centreline, s_values, km_values, tag: str):
    """Fixed format: one header line; columns x, y, '<km>_<TAG>', km (spacing/format as rkm.csv of the Maas project)."""
    x, y, _ = cl.at(s_values)
    lines = ["X,Y,MODELKM,KM "]
    for xi, yi, km in zip(x, y, km_values):
        lines.append(f"   {xi:.6f},   {yi:.6f},{km:.2f}_{tag},  {km:.6f} ")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_rkm_files(folder: Path, p: Params, cl: Centreline) -> dict[str, Path]:
    s100 = np.arange(0.0, p.length + 1, 100.0)
    files = {
        "rkm": (s100, km_of_s(p, s100), "MA"),
        "rkm_1km": (s100[::10], km_of_s(p, s100[::10]), "MA"),
        "rkm_3km": (s100[::30], km_of_s(p, s100[::30]), "MA"),
        "rkm_generic": (s100, km_of_s(p, s100), "SY"),  # unknown tag -> generic river
        "rkm_rijn": (s100, 870.0 + s100 / 1000.0, "WA"),  # Rijntakken tags -> track required
    }
    out = {}
    for name, (s, km, tag) in files.items():
        out[name] = folder / f"{name}.csv"
        write_rkm_file(out[name], cl, s, km, tag)
    return out


def band_polygon(cl: Centreline, s0: float, s1: float, n0: float, n1: float, step: float = 25.0) -> Polygon:
    s = np.linspace(s0, s1, max(2, int(round((s1 - s0) / step)) + 1))
    xr, yr = cl.xy(s, np.full_like(s, n0))
    xl, yl = cl.xy(s[::-1], np.full_like(s, n1))
    return Polygon(np.column_stack([np.concatenate([xr, xl]), np.concatenate([yr, yl])]))


def write_regions(folder: Path, p: Params, cl: Centreline) -> dict[str, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, (n0, n1) in REGIONS.items():
        poly = band_polygon(cl, 0.0, p.length, n0, n1)
        gdf = gpd.GeoDataFrame({"name": [name]}, geometry=[poly], crs=CRS)
        out[name] = folder / f"{name}.shp"
        gdf.to_file(out[name])
    return out


def land_boundary_lines(p: Params, cl: Centreline) -> list[np.ndarray]:
    s = np.arange(0.0, p.length + 1, 50.0)
    edge = p.channel_half_width + p.floodplain_width
    lines = []
    for n in (-edge, edge):
        x, y = cl.xy(s, np.full_like(s, n))
        lines.append(np.column_stack([x, y]))
    s_dike = np.arange(3000.0, 5001.0, 50.0)  # a "dike" on the left floodplain between km 13 and 15
    x, y = cl.xy(s_dike, np.full_like(s_dike, 200.0))
    lines.append(np.column_stack([x, y]))
    return lines


def write_land_boundaries(folder: Path, p: Params, cl: Centreline) -> dict[str, Path]:
    """Land boundaries are input as .ldb (Tekal) only. Matlab's .mat versions were caches of .ldb files."""
    folder.mkdir(parents=True, exist_ok=True)
    lines = land_boundary_lines(p, cl)
    txt = []
    for k, ln in enumerate(lines, start=1):
        txt.append(f"L{k:03d}")
        txt.append(f"{len(ln):6d}{2:6d}")
        txt += [f"{x:15.3f} {y:15.3f}" for x, y in ln]
    ldb = folder / "synthetic.ldb"
    ldb.write_text("\n".join(txt) + "\n", encoding="utf-8")
    return {"ldb": ldb}
