# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic measurements in the formats used by the Maas project:
- SMB: <region>_measured.mat (Matlab structure data.<var>.val_mean.{rkm,s,unit,tim_dnum,val}, unsorted times);
- M2D: GeoTIFF tiles + VRT, a half-pixel-shifted GeoTIFF in cm, polygon shapefiles ("vakken");
  time catalogues (CSV: time, file, factor)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import scipy.io
from rasterio.crs import CRS as RioCRS
from rasterio.transform import from_origin

from fields import bed_level
from mesh import Centreline, Mesh
from params import REFERENCE_TIME, TZ, Params
from project_inputs import CRS, REGIONS, band_polygon, km_of_s

NODATA = -9999.0


def _date(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


def morph_seconds(date: datetime) -> float:
    return (date - REFERENCE_TIME).total_seconds()


def datenum(date: datetime) -> float:
    d = date.replace(tzinfo=None)
    return d.toordinal() + 366 + (d - datetime(d.year, d.month, d.day)).total_seconds() / 86400.0


# ---------------------------------------------------------------- SMB (.mat)

def write_smb_mat(folder: Path, p: Params, mesh: Mesh, erosion: float, vakken_paths: list[Path]) -> dict[str, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    dates = [_date(d) for d in p.smb_measurement_dates]  # unsorted on purpose
    centres = np.arange(50.0, p.length, 100.0)
    out = {}
    for region in ("L1R1", "L1L3", "R1R3"):
        n0, n1 = REGIONS[region]
        in_region = (mesh.face_n > n0) & (mesh.face_n < n1)
        bins = np.floor(mesh.face_s / 100.0).astype(int)
        val = np.full((len(centres), len(dates)), np.nan)
        for kt, d in enumerate(dates):
            bl = bed_level(p, mesh.face_s, mesh.face_n, morph_seconds(d), erosion) + p.measurement_bias_m
            for kb in range(len(centres)):
                sel = in_region & (bins == kb)
                val[kb, kt] = np.sum(bl[sel] * mesh.face_area[sel]) / np.sum(mesh.face_area[sel])
        val[20:23, :] = np.nan  # gaps, as in real measurements
        km = km_of_s(p, centres)
        slope = np.full_like(val, np.nan)
        slope[1:-1] = (val[2:] - val[:-2]) / (2 * 100.0)
        common = {"rkm": km, "s": centres, "tim_dnum": np.array([int(round(datenum(d))) for d in dates], dtype=np.int32)}
        source = np.array([str(pth) for pth in vakken_paths] + [""] * (len(dates) - len(vakken_paths)), dtype=object)
        data = {
            "bl": {"val_mean": {**common, "unit": "bed elevation [m+NAP]", "val": val}, "source": source},
            "detab_ds": {"val_mean": {**common, "unit": "bed slope [-]", "val": slope}, "source": source},
        }
        out[region] = folder / f"{region}_measured.mat"
        scipy.io.savemat(out[region], {"data": data})
    return out


# ---------------------------------------------------------------- rasters (GeoTIFF + VRT)

def _raster_grid(p: Params, mesh: Mesh, shift: float = 0.0):
    s0, s1 = (np.array(p.raster_km_range) - p.km0) * 1000.0
    sel = (mesh.face_s >= s0) & (mesh.face_s <= s1)
    res = p.raster_resolution
    xmin = np.floor(mesh.face_x[sel].min() / res) * res - 2 * res + shift
    ymax = np.ceil(mesh.face_y[sel].max() / res) * res + 2 * res + shift
    xmax = np.ceil(mesh.face_x[sel].max() / res) * res + 2 * res
    ymin = np.floor(mesh.face_y[sel].min() / res) * res - 2 * res
    nx = int(round((xmax - xmin) / res))
    ny = int(round((ymax - ymin) / res))
    return xmin, ymax, nx, ny, (s0, s1)


def _raster_values(p: Params, cl: Centreline, xmin, ymax, nx, ny, s_range, t_mor, erosion):
    res = p.raster_resolution
    xc = xmin + (np.arange(nx) + 0.5) * res
    yc = ymax - (np.arange(ny) + 0.5) * res
    X, Y = np.meshgrid(xc, yc)
    s, n = cl.sn(X, Y)
    z = bed_level(p, s, n, t_mor, erosion) + p.measurement_bias_m
    valid = (np.abs(n) <= p.channel_half_width + p.floodplain_width) & (s >= s_range[0]) & (s <= s_range[1])
    return np.where(valid, z, NODATA).astype(np.float32)


def _write_tif(path: Path, data: np.ndarray, xmin: float, ymax: float, res: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=data.shape[0], width=data.shape[1], count=1, dtype="float32",
                       crs=RioCRS.from_string(CRS), transform=from_origin(xmin, ymax, res, res), nodata=NODATA,
                       compress="deflate") as dst:
        dst.write(data, 1)


def _write_vrt(path: Path, tiles: list[tuple[str, int, int, int]], width: int, height: int, xmin: float, ymax: float, res: float):
    """tiles: (relative file name, y offset in rows, rows, columns). Uses ComplexSource/DstRect as read by Matlab
    VRT_bounding_boxes.m, and ', '-separated GeoTransform."""
    wkt = RioCRS.from_string(CRS).to_wkt()
    src = []
    for name, yoff, rows, cols in tiles:
        src.append(
            "    <ComplexSource>\n"
            f'      <SourceFilename relativeToVRT="1">{name}</SourceFilename>\n'
            "      <SourceBand>1</SourceBand>\n"
            f'      <SourceProperties RasterXSize="{cols}" RasterYSize="{rows}" DataType="Float32" BlockXSize="{cols}" BlockYSize="1" />\n'
            f'      <SrcRect xOff="0" yOff="0" xSize="{cols}" ySize="{rows}" />\n'
            f'      <DstRect xOff="0" yOff="{yoff}" xSize="{cols}" ySize="{rows}" />\n'
            f"      <NODATA>{NODATA:g}</NODATA>\n"
            "    </ComplexSource>\n"
        )
    path.write_text(
        f'<VRTDataset rasterXSize="{width}" rasterYSize="{height}">\n'
        f'  <SRS dataAxisToSRSAxisMapping="1,2">{wkt}</SRS>\n'
        f"  <GeoTransform>{xmin:.10e}, {res:.10e}, 0.0000000000e+00, {ymax:.10e}, 0.0000000000e+00, {-res:.10e}</GeoTransform>\n"
        '  <VRTRasterBand dataType="Float32" band="1">\n'
        f"    <NoDataValue>{NODATA:g}</NoDataValue>\n" + "".join(src) +
        "  </VRTRasterBand>\n</VRTDataset>\n",
        encoding="utf-8",
    )


def write_rasters(folder: Path, p: Params, cl: Centreline, mesh: Mesh, erosion: float) -> list[tuple[datetime, Path, float]]:
    """Returns catalogue entries (time, file, factor)."""
    entries = []
    res = p.raster_resolution
    d0, d1, d2 = (_date(d) for d in p.raster_dates)

    # date 0: two aligned tiles + VRT (metres)
    xmin, ymax, nx, ny, sr = _raster_grid(p, mesh)
    z = _raster_values(p, cl, xmin, ymax, nx, ny, sr, morph_seconds(d0), erosion)
    half = ny // 2
    tdir = folder / d0.strftime("%Y%m%d")
    _write_tif(tdir / "tile_a.tif", z[:half], xmin, ymax, res)
    _write_tif(tdir / "tile_b.tif", z[half:], xmin, ymax - half * res, res)
    vrt = tdir / f"{d0:%Y%m%d}.vrt"
    _write_vrt(vrt, [("tile_a.tif", 0, half, nx), ("tile_b.tif", half, ny - half, nx)], nx, ny, xmin, ymax, res)
    entries.append((d0, vrt, 1.0))

    # date 1: one tile, grid shifted by half a pixel, values in cm (factor 0.01)
    xmin1, ymax1, nx1, ny1, sr1 = _raster_grid(p, mesh, shift=res / 2)
    z1 = _raster_values(p, cl, xmin1, ymax1, nx1, ny1, sr1, morph_seconds(d1), erosion)
    z1 = np.where(z1 == NODATA, NODATA, z1 * 100.0).astype(np.float32)
    f1 = folder / d1.strftime("%Y%m%d") / f"{d1:%Y%m%d}_cm.tif"
    _write_tif(f1, z1, xmin1, ymax1, res)
    entries.append((d1, f1, 0.01))

    # date 2: outside any time tolerance of the model output (tests the 'no measurement found' path)
    z2 = _raster_values(p, cl, xmin, ymax, nx, ny, sr, morph_seconds(d2), erosion)
    f2 = folder / d2.strftime("%Y%m%d") / f"{d2:%Y%m%d}.tif"
    _write_tif(f2, z2, xmin, ymax, res)
    entries.append((d2, f2, 1.0))
    return entries


# ---------------------------------------------------------------- polygons ("vakken")

BANDS = {"R3": (-100.0, -50.0), "R2": (-50.0, -25.0), "R1": (-25.0, 0.0), "L1": (0.0, 25.0),
         "L2": (25.0, 50.0), "L3": (50.0, 100.0), "L4": (100.0, 150.0)}  # L4 is not an accepted location


def write_vakken(folder: Path, p: Params, cl: Centreline, erosion: float) -> list[tuple[datetime, Path, float]]:
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(p.seed)
    s_start, s_end = (np.array(p.vakken_km_range) - p.km0) * 1000.0
    rows, geoms = [], []
    k = 0
    for s0 in np.arange(s_start, s_end, 100.0):
        for loc, (n0, n1) in BANDS.items():
            poly = band_polygon(cl, s0, s0 + 100.0, n0, n1, step=25.0)
            ss, nn = np.meshgrid(np.linspace(s0 + 5, s0 + 95, 10), np.linspace(n0 + 1, n1 - 1, 6))
            rows.append({"Sectie": 1, "hm_punt": int(round(km_of_s(p, s0) * 10)), "Z_N": "N", "locatie": loc,
                         "code_uniek": f"{km_of_s(p, s0):06.1f}_{loc}", "A": poly.area,
                         "COUNT": float(round(poly.area * (0.5 if k % 7 == 3 else 1.2))), "_s": ss, "_n": nn})
            geoms.append(poly)
            k += 1
    entries = []
    for d in (_date(x) for x in p.vakken_dates):
        recs = []
        for r in rows:
            z = bed_level(p, r["_s"], r["_n"], morph_seconds(d), erosion) + p.measurement_bias_m
            recs.append({**{key: v for key, v in r.items() if not key.startswith("_")},
                         "MEAN": float(z.mean()), "STD": float(z.std()), "MIN": float(z.min()), "MAX": float(z.max()),
                         "RANGE": float(z.max() - z.min())})
        gdf = gpd.GeoDataFrame(recs, geometry=geoms, crs=CRS)
        if entries:  # second date: different row order, to test matching by id instead of by order
            gdf = gdf.iloc[rng.permutation(len(gdf))].reset_index(drop=True)
        path = folder / f"vakken_{d:%Y%m%d}.shp"
        gdf.to_file(path)
        entries.append((d, path, 1.0))
    return entries


def write_catalogue(path: Path, entries: list[tuple[datetime, Path, float]]):
    """Same format as 2D_measurements_02.csv. Absolute paths: Matlab D3D_plot does not resolve relative ones."""
    lines = ["time, file, factor"]
    for d, f, factor in entries:
        lines.append(f"{d.isoformat()}, {f.resolve()} , {factor:g}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
