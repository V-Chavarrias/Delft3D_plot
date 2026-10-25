# Delft3D_plot (Python): Architecture Proposal

Status: **draft v5 for discussion** · Scope: requirements and architecture only. Nothing is implemented yet.

Changes since v4:
* answers on measurements (colour limits from the measurements, raster resampling, time zone) and the `d3dplot/` sub-folder
* findings from the real SMT run `r209`: time reference of map files vs. MDU, duplicate times between sub-runs, status from partition `.dia` files (§4.3, §4.7)
* **synthetic SMT test case**: generated input and output with known answers (§9.4)

Changes in v4: measurements; read-only inputs; plot hooks; region groups; SLURM stub; acceptance case.
Changes in v3: SMB geometry specification; one file per time step; process pool on Windows; efficient zoomed plotting; labels dictionary; GPL-3.0.
Changes in v2: decisions on the first open questions; v1 scope; reprocessing policy; simulation status; replotting; naming conventions; agent skills; upstream contributions.

---

## 1. Objectives and decisions

### 1.1 Requirements

| # | Requirement | Consequence for the design |
|---|-------------|----------------------------|
| R1 | Script-based Python tool to postprocess and plot Delft3D results | Primary interface is a Python script (or an equivalent YAML file) plus a thin CLI |
| R2 | `D3D_plot` (Matlab, `oet_matlab`) is the functional reference, **not** a 1:1 translation | Keep the concepts (analyses, comparisons, cache), redesign the internals and the names |
| R3 | Reuse **HYDROLIB-core** and **dfm_tools** as much as possible | No custom readers for MDU, polylines, UGRID, partitions or layers. Gaps go upstream (§10) |
| R4 | Professional project: docs, version control, coding standards | `pyproject.toml`, ruff, mypy, pre-commit, conventional commits, semver, MkDocs, CI |
| R5 | Extensive test bench | Unit, integration, regression (numbers and images) and end-to-end tests |
| R6 | Linux and Windows | `pathlib` only, `spawn`-safe multiprocessing, CI matrix on both OSs |
| R7 | Parallelism in processing and plotting | Task graph on a pluggable executor (serial, local processes, SLURM) |
| R8 | **Process first, plot later** | Two separate stages with a persistent cache. Plotting never reads raw output |
| R9 | **Validate all input up front** | A validation stage that fails fast and reports all problems at once |
| R10 | **Inputs are never modified or deleted** (model files, measurements, GIS files, typically on the P-drive) | All writes go through one guarded store that only writes inside the tool's own output folders (§4.14) |
| R11 | **Measurements** can be plotted with model results (M2D, SMB, later others) | A general measurement layer with readers per format and conversion to NetCDF (§4.12) |

### 1.2 Decisions taken

| # | Topic | Decision |
|---|-------|----------|
| D1 | Analyses in v1 | **M2D** (2D maps) and **SMB** (statistics per river-km polygon in a region such as the summer bed) |
| D2 | Model types in v1 | **D-Flow FM** (plain or through DIMR) and **SMT** (Simulation Management Tool, FM-based) |
| D3 | Cache format | **NetCDF**. One grid file per simulation, **one data file per time step** (§4.5) |
| D4 | Hosting and licence | Public GitHub repository [V-Chavarrias/Delft3D_plot](https://github.com/V-Chavarrias/Delft3D_plot), **GPL-3.0** |
| D5 | Upstream | Gaps are implemented as branches/PRs in HYDROLIB-core and dfm_tools when needed (§10) |
| D6 | Parallel execution | Local process pool (Windows and Linux). SLURM job arrays on Linux: **stub in v1**, detailed later (§4.8) |
| D7 | Matlab JSON | Reading `D3D_plot` JSON is **not** required |
| D9 | SMB geometry | Matlab behaviour is kept: region (summer bed) polygon cut by lines perpendicular to the river axis. The river-km file format is **fixed input**. Rijn and Maas rules apply automatically when the file contains their branch tags; otherwise the river is generic (§2.5) |
| D10 | Output location | Default: inside each simulation folder. Can be overridden per simulation or with a common root (§4.9) |
| D11 | Figure formats | PNG only in v1. Architecture allows more formats later (§4.10) |
| D12 | Labels | Separate translation dictionary as in `labels4all`. Languages: English, Dutch, Spanish (§4.11) |
| D13 | Measurements | M2D: GeoTIFF/VRT and polygon shapefiles through a time catalogue (existing CSV format accepted). SMB: `.mat` accepted and converted automatically to NetCDF, which is the recommended format for new data (§4.12) |
| D14 | Read-only inputs | The tool never modifies or deletes any input file (§4.14) |
| D15 | Generality | River tracks, bend cutoffs, measurement formats and polygon filters are data/plugins, not code branches (§2.5, §4.12) |
| D8 | Naming | Descriptive names, not Matlab abbreviations (§7) |

---

## 2. How Matlab `D3D_plot` works (reference)

Entry point: [D3D_plot.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/D3D_plot.m).

### 2.1 Workflow

```
in_plot (struct or JSON)
  └─ gdm_read_input, create_mat_default_flags (copy global flags into every tag)
  └─ for each simulation:
       simulation_paths -> D3D_simpath   detect D3D4/FM/SOBEK3/SMT, find files, cache in simdef.mat,
                                         create mat/ figures/ csv/ json/ logs/
       gdm_create_mat                    for each enabled tag: gdm_create_mat_<TAG>  -> *.mat cache
  └─ gdm_plot (all simulations)          for each enabled tag: gdm_plot_<TAG>        <- *.mat only
  └─ gdm_adhoc                           project-specific functions
  └─ gdm_save_json                       configuration used (provenance, replotting)
  └─ copy log to <reference sim>/logs
```

### 2.2 Concepts kept

* **Analyses ("tags")**: `GRD`, `M2D`, `M1D`, `PRF`, `SMB`, `HIS`, `HFM`, `CRS`, `STO`, …
* **Comparison variants**: value, difference with the initial time, difference with the reference simulation, the difference of both, percentage difference, all simulations in one figure, all times in one figure, space–time plots, movies.
* **Selections**: variable (short names, see [D3D_list_of_variables.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/fcn/D3D_list_of_variables.m)), sediment fraction index, layer / depth average / elevation, times (all, last, dates, indices), flow vs. morphological time.
* **Cache**: one file per (analysis, variable, time, layer, …) ([mat_tmp_name.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/mat_tmp_name.m)). Existing files are not reprocessed unless `overwrite=1`. The grid is stored separately from the data ([gdm_save_grid_netcdf.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/gdm_save_grid_netcdf.m), [gdm_write_M2D_netcdf.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/gdm_write_M2D_netcdf.m)).
* **Simulation status** ([D3D_status.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/fcn/D3D_status.m)): not started / running / finished / interrupted. It is detected from the `.dia` file (`Computation finished`) or, for SMT, from the presence of `output/work`. It is saved to `mat/status.mat` **only when final** and reused afterwards. The list of times is also trusted from cache when the simulation is finished ([D3D_time_dnum.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/fcn/D3D_time_dnum.m)).
* **Provenance and replotting**: the JSON of the configuration used is saved next to the results.

### 2.3 Pain points addressed

1. Validation is scattered and happens late (errors deep inside loops, after hours).
2. The configuration is an untyped struct. Typos are silently ignored and defaults are spread out.
3. The cache key is a file name. There is no detection of changed sources or parameters.
4. Serial loops. Parallelism relies on shuffling the time order (`order_anl`) and racing on the same files.
5. Processing is mixed with plotting in places, and logic is copy-pasted across tags.
6. Many flags are cryptic (`do_diff_s_t`, `do_xvt_cel`, `clims_type`, …) and partly overlap.

### 2.4 The two v1 analyses in detail

**M2D (2D maps)**: [gdm_create_mat_M2D.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/gdm_create_mat_M2D.m), [gdm_plot_M2D.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/gdm_plot_M2D.m)
* Processing: for every time and variable, read the face values (with layer / fraction / depth-average / elevation selection) and store them. Optional exports: shapefile, NetCDF. Also cached: velocity vectors and grid polygons.
* Plotting, per variable and time:
  * value;
  * difference with the initial time;
  * difference with the reference simulation (interpolated to the reference grid if the grids differ);
  * difference with the reference simulation of the differences with the initial time;
  * percentage difference.
  
  Per figure, one image is made for every colour-limit set and every zoom window. Optional overlays: tiles, land boundary, fixed weirs, velocity vectors, river-km labels. Measurements (GeoTIFF/VRT rasters or polygon shapefiles) are shown in a second panel next to the model (§2.6). Optional 3D view and movies.

**SMB (statistics per river-km polygon)**: [gdm_create_mat_SMB.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/gdm_create_mat_SMB.m), [gdm_create_mat_SMB_postprocess.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/gdm_create_mat_SMB_postprocess.m), [gdm_plot_SMB.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/gdm_plot_SMB.m)
* Geometry (cached per grid):
  * the cells inside a *region polygon* (e.g. the summer bed shapefile) ([gdm_read_summerbed.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/gdm_read_summerbed.m));
  * *river-km polygons*: quadrilaterals between consecutive river-km points, bounded by two lines parallel to the river axis at a given distance, with optional bend-cutoff corrections and branch/track codes for the Dutch rivers ([gdm_load_rkm_polygons.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/gdm_load_rkm_polygons.m)).
* Processing, per region × river-km set × time × variable: `mean`, `std`, `max`, `min`, `count`, `sum`, `sum / segment length`, and the area-weighted mean over the cells in both polygons (NaN and Inf excluded).
* Postprocessing:
  * derived streamwise bed slope (`detab_ds`);
  * value × width (flow or morphological);
  * differences between regions;
  * time averages over periods.
* Plotting:
  * profile along the river km per time;
  * all simulations in one figure;
  * all times in one figure;
  * space–time (river km × time, coloured);
  * time series at selected river km;
  * cumulative in time;
  * stacked areas for fractions;
  * each with the difference variants.
  
  Optional overlays: measurements per region (§2.6), structures. A diagnostic plot shows the cells in each polygon.
* **Region groups**: several regions can be drawn in one figure, e.g. the left and right bank (`sb_pol{kp,1}`, `sb_pol{kp,2}` in Matlab), each with its own measurement file. A group with several regions is not processed again: it reuses the results of its single regions.

### 2.5 SMB geometry: specification (must be preserved)

Sources: [gdm_read_summerbed.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/gdm_read_summerbed.m), [gdm_load_rkm_polygons.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/gdm_load_rkm_polygons.m), [convert2rkm.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/rkm/convert2rkm.m), [perpendicular_polyline.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/polyline/perpendicular_polyline.m), [angle_polyline.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/polyline/angle_polyline.m), [branch_str_num.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/rkm/branch_str_num.m), [which_river.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/rkm/which_river.m), [branch_rijntakken.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/rkm/branch_rijntakken.m), [branch_maas.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/rkm/branch_maas.m), [correct_for_bendcutoff.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/rkm/correct_for_bendcutoff.m).

**Concept.** A *region polygon* (typically the summer bed) selects the model cells that belong to the region. Lines perpendicular to the river axis, placed between consecutive river-km points, cut the region into segments. The statistics of each segment are computed from the cells that are in the region **and** in the segment.

```
            L_i ─────────── L_i+1 ─────────── L_i+2        L/R: offset ±half_width
             │   segment i    │   segment i+1   │             perpendicular to the axis
   axis ── e_i ──── c_i ──── e_i+1 ─── c_i+1 ── e_i+2      c: requested km (centre)
             │  ░░region░░░░  │  ░░░░░░░░░░░░░  │           e: segment edge (between centres)
            R_i ─────────── R_i+1 ─────────── R_i+2        cut line = segment L_k–R_k
```

**River-km file (input, format fixed).**
* Text file, comma-delimited, **one header line**, four columns: `x, y, branch label, km`. Example:
  ```
  X,Y,MODELKM,KM
  175918.445300,308038.484400,3.00_MA,3.000000
  176514.164050,308833.218800,4.00_MA,4.000000
  ```
* The **branch tag** is the text after the first `_` of the third column (`3.00_MA` → `MA`). If there is no `_`, the whole string is the tag. Tags are compared case-insensitively.
* The file is read as-is. Matlab wrote a `.mat` copy *next to* the input file; the Python tool writes its parsed copy to its own cache instead, because input folders may be read-only.

**River detection (from the tags in the file).**

| Tags present in the file | River | Branch of each km |
|--------------------------|-------|-------------------|
| any of `RH`/`Rhein`, `BR`, `PK`, `WA`/`WL`, `BO`, `NI`, `NR`, `LE`, `IJ` | **Rijntakken** | Derived from the user-given **track** and the km (thresholds below) |
| `MA`, `BM` | **Maas** | `MA` if km < 230.5, otherwise `BM` (if `BM` exists in the file) |
| anything else, or no tags | **Generic river** | The single tag of the file. If the file has several tags, the user gives the branch explicitly |

Rijntakken branch per km, by track:

| Track | Branch by km |
|-------|-------------|
| `WA` (also `WL`, `BO`, `NI`) | > 867.00 `WA` (optionally `BO` > 952.85, `NI` > 960.15) · > 852.90 `BR` · else `Rhein` |
| `IJ` | > 878.05 `IJ` · > 867.05 `PK` · > 852.90 `BR` · else `Rhein` |
| `NR`, `LE` | > 929.35 `LE` · > 878.08 `NR` · > 867.05 `PK` · > 852.90 `BR` · else `Rhein` |
| `PK` | `PK` (error outside 867.05–878.05) |
| `BR`, `RH` | as given |

*Change from Matlab:* Matlab decided the river from the *track* string. Here it is decided from the *file tags*, as requested. The track is still required for Rijntakken (to choose the branch after a bifurcation). Validation reports an error if a Rijntakken file is given without a track.

**Generality.** The rules above are *data of river providers*, not special cases in the algorithm:
* A provider (`rijntakken`, `maas`, `generic`, or a user plugin) declares the tags it recognises, how to derive the branch of a km from a track, and its bend cutoffs.
* The branch thresholds and the cutoff table are stored in data files (one per provider: tag list, thresholds per track, cutoff intervals per branch). A new river, a new branch threshold or a cutoff on another branch is a data change, not a code change.
* One track per km set is enough for now (confirmed). The provider interface returns a branch per km, so a future "track that changes along the set" needs no change in the algorithm.

**Algorithm** (per region × km set; all distances in m, km values in km):
1. **Centres** `c_1..c_n`: the km values requested by the user (e.g. `145, 146, …, 175`). They do not need to be equidistant or to coincide with file points.
2. **Segment edges and lengths**: `e` = midpoints between centres, extrapolated at both ends (`cen2cor`). `Δs_i = e_{i+1} − e_i` (km), computed from the *original* centres.
3. **Bend cutoffs** (from the provider's cutoff table; today only the IJssel has entries: 891.9–896.0, 902.5–903.0, 905.3–910.0 km):
   * centres inside a cutoff are removed;
   * edges are recomputed as `c_i − Δs_i/2` (and the last as `c_n + Δs_n/2`);
   * an edge falling inside a cutoff is shifted across it, keeping its distance to its centre (with a 0.1 km polygon-size correction).
   
   The cutoff table is provider data, not code.
4. **Edge coordinates**: for each edge, take the file points of its branch and interpolate `x, y` linearly in km between the two bracketing points.
5. **Local direction** at each edge: `θ_k = atan2(y_{k+1} − y_{k−1}, x_{k+1} − x_{k−1})` (central difference over neighbouring edges). The first and last edges copy the angle of their neighbour. With only two edges, both use the direction between them.
6. **Cut lines**: `L_k = e_k + W·(cos(θ_k+π/2), sin(θ_k+π/2))`, `R_k = e_k − W·(…)`, with `W` = `half_width` (Matlab default 1000 m). The segment `L_k–R_k` is the perpendicular cut line.
7. **Segment polygons**: `P_i = [L_i, L_{i+1}, R_{i+1}, R_i]`. Consecutive segments share their cut line, so along the axis there are no gaps or overlaps as long as the quadrilaterals are valid.
8. **Region membership**: a cell is in the region if its face centre is inside the region polygon (shapefile; several parts allowed). An optional concave-hull step builds one polygon from unordered parts (Matlab `boundary(…, 0.8)`, default off).
9. **Cell assignment**: a cell belongs to segment `i` if its face centre is inside `P_i` **and** in the region. At processing time, cells whose value is NaN or Inf are also excluded.

Statistics per segment are computed from the assigned cells:
* `mean`, `std`, `max`, `min`, `count`, `sum`;
* `sum_per_length` = sum / (`Δs_i`·1000);
* `mean_area_weighted` = Σ(v·A)/ΣA, with A the cell area.

**Validation checks added** (Matlab did not check these):
* each `P_i` is a valid, non-self-intersecting quadrilateral. In sharp bends with a large `W`, neighbouring cut lines can cross;
* no cell is assigned to two segments;
* *coverage*: region cells within the km range that fall in no segment. This happens when `W` is smaller than the distance from the axis to the region edge. They are reported as a count and shown in the diagnostic plot;
* all requested km are inside the range of their branch in the file;
* the distance from each edge point to the axis is within tolerance (Matlab `TolMinDist`).

*To confirm in acceptance tests:* that the "face centre" used in Python (`mesh2d_face_x/y`) is the same point as Matlab's `Xcen/Ycen`. Both are believed to be the cell circumcentre in D-Flow FM.

Implementation:
* `geometry/river_km.py`: file reader, tag parsing, river providers (`rijntakken`, `maas`, `generic`), cutoffs, edges, cut lines, polygons.
* `geometry/regions.py`: region polygons, membership.
* Point-in-polygon is vectorised with `shapely.contains_xy`, and shapefiles are read with `geopandas`.
* The result is **written to a NetCDF file** in `cache/geometry/` (§4.5) and reused by all variables, times and later runs.

### 2.6 Measurements in Matlab (reference)

Example: the Maas project script `p:\11211565-002-maas-mor-2026\E_Software_Scripts\co\57_plot_Lixhe_Roermond\main_plot_01.m` (read-only; used as the acceptance case, §9.3).

**M2D**: [gdm_ini_2D_mea.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/gdm_ini_2D_mea.m), [gdm_load_2D_measurements.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/gdm_load_2D_measurements.m), [fig_M2D.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/fig_M2D.m), [SHP_read_and_filter.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/general/SHP_read_and_filter.m)

* **Input**: a *catalogue* CSV (`measurements`) with columns `time, file, factor`. Example (`2D_measurements_02.csv`):
  ```
  time, file, factor
  2021-02-08T00:00:00+01:00, p:\…\GEOTIFF\2021_pre_floodwave.vrt , 1.0
  2021-08-08T00:00:00+01:00, p:\…\2021 Grensmaas Hoogwater zomer.tif , 1.0
  ```
  * Times carry a time-zone offset. `factor` converts values to model units, e.g. cm → m.
  * The files can be:
    * `.tif` (GeoTIFF);
    * `.vrt` (GDAL virtual mosaic of many GeoTIFF tiles; each tile gets its own bounding box);
    * `.shp` (polygons, "vakken", with statistics per polygon).
  * All files used for one image must be of the same type.
* **Selection** for a plot at time *t* and window *(xlim, ylim)*: every catalogue entry with `|t_mea − t| < tol_time_measurements` (default 30 days; 60 days in the example) whose bounding box overlaps the window enlarged by `tol_x/y_measurements` (default 1000 m).
* **Rasters**:
  * the selected tiles are read in that window, multiplied by `factor`, and mosaicked on a common grid. Cells are filled in catalogue order: a cell already filled is not overwritten;
  * a validity mask is kept;
  * if tile grids do not coincide, Matlab tries a shift of 0.5 cell.
* **Polygons**: the value field (`MEAN`) is shown per polygon. Two filters set polygons to NaN:
  * *coverage*: `COUNT / area < 0.99`, i.e. fewer than one sounding point per m²;
  * *location*: the location code (`Locatie`, e.g. L1…L3, R1…R3) is not in the list `[-3 … 3]`.
  
  The field names are configurable in Matlab. In the 2026 shapefiles they differ from the defaults: the fields are `A` (area) and `locatie`, and there is a unique id field `code_uniek`.
* **Comparisons**:
  * `value` shows the measurements at *t*;
  * `difference_time` shows measurement(*t*) − measurement(*t₀*). Rasters are subtracted on the intersection of their grids. Shapefiles are subtracted polygon by polygon *by order*, which requires the same number of polygons;
  * differences between simulations show no measurements.
* **Display**: a second panel to the right of the model panel, with the same colormap and colour limits. Its title is the mean time of the measurements used. With `do_clims_measurements=1`, the colour limits are taken from the **range of the measurements** (confirmed).
* **Cost**: measurements are re-read for every colour-limit set × window × simulation.

**SMB**: [gdm_load_measurements_match_time.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/gdm_load_measurements_match_time.m), [gdm_load_measurements.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/gdm_load_measurements.m)

* **Input**: one `.mat` file per region (`measurements{kp}` next to `sb_pol{kp}`), plus optional files for region differences (`measurements_diff`).
* **Structure** (verified on `L1R1_measured.mat`):
  ```
  data.<variable>.<statistic>.rkm       [nx]      river km
  data.<variable>.<statistic>.s         [nx]      distance along the axis [m] (optional)
  data.<variable>.<statistic>.tim_dnum  [nt]      Matlab datenum (no time zone); NOT necessarily sorted
  data.<variable>.<statistic>.val       [nx, nt]
  data.<variable>.<statistic>.unit      'bed elevation [m+NAP]'
  data.<variable>.source                {nt}      source files (here: the polygon shapefiles above)
  ```
  The example has `bl` and `detab_ds` with statistic `val_mean`, 989 locations and 4 times: 2021-02-08, 2023-01-15, 2021-08-08 and 2026-02-23. These times are *not* in chronological order.
* **Matching**:
  * variable names are matched with aliases (`bl` = `mesh2d_mor_bl` = `DPS`);
  * statistics are matched with equivalences: the model `val_mean_weighted` is compared with the measured `val_mean`;
  * the x-axis is `rkm` (default for SMB) or `s`.
  * Profile views: the time nearest to *t* within `tol_time_measurements` (days).
  * Time-series views: the location nearest to the km within 1 km (hard-coded).
* **Comparisons**:
  * `value`: measurement(*t*);
  * `difference_time`: measurement(*t*) − measurement(*t₀*);
  * differences between simulations: none.
* **Display**: a line or markers on the same axes as the model, one per region in a region group. Axis limits can include the measurements (`do_include_mea_xylims`).

**Other inputs seen in the example** that the design must accept:
* the land boundary as a `.mat` file (`j21_ldb.mat`: one N×2 array of NaN-separated polylines);
* user functions applied to SMB figures (`function_handles = {@add_structures_rkm, @legend_horizontal}`), see §4.13;
* many x-windows for SMB (`xlims` with 20 rows);
* windows along river km for M2D (`fpath_rkm_plot_along` + `rkm_tol_x/y`).

---

## 3. What we reuse

### 3.1 HYDROLIB-core: model input / metadata

| Need | HYDROLIB-core API |
|------|-------------------|
| Read MDU without loading everything | `FMModel(path, recurse=False)` |
| Output dir, map/his names | `model.output.outputdir`, `.mapfile`, `.hisfile` |
| Reference date, time unit, stop time | `model.time.refdate`, `.tunit`, `.tstop` |
| Net file | `model.geometry.netfile` |
| Fixed weirs, structures | `model.geometry.fixedweirfile` (`PolyFile`), `.structurefile` (`StructureModel`) |
| Morphology / sediment file paths | `model.sediment.morfile`, `.sedfile` (disk-only, see §10) |
| Polylines / polygons / land boundaries | `hydrolib.core.dflowfm.polyfile.models.PolyFile` |
| `dimr_config.xml` → MDU | `hydrolib.core.dimr.models.DIMRModel`, `FMComponent.filepath` |
| Windows/Linux paths in model files | `path_style`, `resolve_casing`, `FilePathStyleConverter` |

### 3.2 dfm_tools / xugrid / xarray: model output

| Need | API |
|------|-----|
| Map file incl. partitions (`*_0000_map.nc`), ghost cells removed | `dfm_tools.open_partitioned_dataset` → `xugrid.UgridDataset` (lazy, dask chunks along time) |
| Layer z-coordinates, values at depth, depth average | `dfm_tools.reconstruct_zw_zcc`, `get_Dataset_atdepths`, `get_vertical_dimensions` |
| Fraction-variable names (WAQ / Fourier) | `rename_waqvars`, `rename_fouvars` |
| 2D map plot | `uds[var].ugrid.plot(...)` (xugrid) |
| Regridding between simulations (difference with the reference simulation on different grids) | `xugrid` regridders (`CentroidLocatorRegridder`, `OverlapRegridder`) |
| Polygon selection | `xugrid` (`ugrid.sel` / `clip`), `geopandas`/`shapely` (`contains_xy`) |
| Satellite tiles, coastlines | `contextily`, `dfm_tools.plot_coastlines` |

---

## 4. Architecture

### 4.1 Layered overview

```
                ┌──────────────────────────────────────────────────────────┐
  User layer    │ Python script  |  YAML config  |  CLI (d3dplot …)  |  skills │
                └───────────────┬──────────────────────────────────────────┘
                                ▼
  Config        │ config/      pydantic models, defaults, JSON schema                │
                                ▼
  Validation    │ validate/    static + dynamic checks  -> ValidationReport          │
                                ▼
  Simulation    │ simulation/  FM, DIMR, SMT discovery + status (§4.7)              │
                                ▼
  Planning      │ plan/        config -> task graph; cache lookup (§4.6)            │
                                ▼
  Execution     │ execute/     serial | processes | slurm  (§4.8)                   │
                    ┌───────────┴───────────┐
                    ▼                       ▼
  Processing    │ analyses/<x>.process │    │ analyses/<x>.plot │  Plotting
                    ▼                       ▲
  Storage       │ store/   grid file + one data file per time step (§4.5)  ─────────────┘
                                ▼
  Provenance    │ config snapshots, logs, tables/ (§4.9)                            │
```

### 4.2 Package layout

```
delft3d-plot/                         repo; distribution "delft3d-plot", import "d3dplot"
├─ pyproject.toml
├─ src/d3dplot/
│  ├─ __init__.py                     public API: Project, Simulation, analyses, run/validate/process/plot
│  ├─ cli.py                          d3dplot init|info|variables|validate|process|plot|run|replot|status|clean|slurm
│  ├─ config/                         base.py, selections.py, figure.py, io.py (YAML/JSON, schema export)
│  ├─ simulation/
│  │  ├─ discover.py                  folder -> FM | DIMR | SMT
│  │  ├─ fm.py                        hydrolib FMModel(recurse=False) wrapper
│  │  ├─ smt.py                       SMT: ordered FM sub-runs, merged time axis
│  │  ├─ status.py                    simulation status (§4.7)
│  │  └─ results.py                   lazy access via dfm_tools (map/his), time axis (hydro/morpho)
│  ├─ variables/                      registry (names, aliases, resolvers), derived quantities
│  ├─ labels/                         label builder + dictionaries (§4.11)
│  │  ├─ builder.py                   label(variable, language, comparison, unit factor, datum, fraction)
│  │  └─ data/variable_to_key.csv, translations.csv, units.csv
│  ├─ selection/                      time.py, vertical.py, fractions.py, spatial.py
│  ├─ geometry/
│  │  ├─ river_km.py                  river-km file, edges, cut lines, segment polygons (§2.5)
│  │  ├─ rivers/                      providers: rijntakken.py, maas.py, generic.py (+ cutoffs data)
│  │  ├─ regions.py                   region (summer bed) membership
│  │  └─ extents.py                   per-extent face subsets for fast plotting (§4.10)
│  ├─ measurements/                   measurement layer (§4.12)
│  │  ├─ catalogue.py                 time catalogue (CSV time,file,factor; YAML; NetCDF stacks)
│  │  ├─ readers/                     raster.py (tif, vrt, nc), polygons.py (shp, gpkg), profiles.py (nc, mat)
│  │  ├─ convert.py                   .mat -> NetCDF (river-km profiles)
│  │  ├─ filters.py                   polygon filters (coverage, location, …), configurable presets
│  │  └─ match.py                     time / location matching, differences (t - t0)
│  ├─ hooks.py                        user plot hooks (§4.13)
│  ├─ analyses/
│  │  ├─ map2d.py                     M2D
│  │  └─ river_km_statistics.py       SMB
│  ├─ comparisons.py                  value / differences; regridding
│  ├─ store/                          grid.py, data.py, fingerprint.py, atomic.py, guard.py (write guard, §4.14)
│  ├─ plotting/                       map.py, profile.py, space_time.py, time_series.py, decorations.py,
│  │                                  movie.py, writers.py (PNG now; registry for more formats)
│  ├─ execute/                        tasks.py, planner.py, executors.py, slurm.py
│  ├─ validate/                       report.py, checks.py
│  └─ logging.py
├─ skills/                            agent skills shipped with the repo (§8)
├─ tests/                             unit/ integration/ regression/ e2e/ data/
├─ docs/                              MkDocs
└─ examples/                          *.py and *.yaml
```

### 4.3 Simulation abstraction (FM and SMT)

```python
class Simulation:                 # one per input folder
    kind: Literal["fm", "dimr", "smt"]
    label: str
    status: SimulationStatus      # §4.7
    runs: list[FMRun]             # 1 for FM/DIMR; N ordered sub-runs for SMT
    def times(self, axis: TimeAxis) -> pd.DatetimeIndex     # hydrodynamic or morphological
    def locate(self, t) -> tuple[int, int]                   # (sub-run, local index); Matlab `sim_idx`
    def open_map(self, run: int) -> xu.UgridDataset          # dfm_tools.open_partitioned_dataset
```

* **FM**: one folder with an `.mdu`, or a `dimr_config.xml` pointing to one. HYDROLIB-core gives the output paths and `refdate`. dfm_tools opens the map files.
* **SMT**: `smt.yml` at the root and `output/0`, `output/1`, … each holding one FM run, with `output/work` present while running. The tool presents the sub-runs as **one simulation** with a concatenated, monotonic time axis.
  * Validation checks that all sub-runs have the same grid (number of faces and node coordinates of the first sub-run vs. the others). This is a one-off check, not something stored in the cache.
  * Relative paths are resolved against the layout MDU, as in Matlab `adapt_paths_smt`.
  * Proposed upstream as a generic dfm_tools helper (§10).
  * **Duplicate boundary times.** Sub-run *k+1* starts at the `TStop` of sub-run *k* from its restart file, so the boundary time appears in both map files (e.g. `r209`: 95 040 s is the last map time of sub-run 0 and the first of sub-run 1). The merged time axis keeps **one** entry, the one from sub-run *k* (the state at the end of the run). Validation can optionally check that both entries are equal.
  * Sub-runs are numbered `0 … N−1` without gaps. A gap is a validation error.
* **Time reference.** The time units of the **map files** are authoritative (e.g. `seconds since 2021-02-08 00:00:00 +01:00`). In `r209` the MDU still says `RefDate = 20121231`, because the map files were updated afterwards. A mismatch between the MDU `RefDate` and the map time units is reported as a **warning**, not an error. The time zone also comes from the map time units. The morphological time comes from `morft` in the map file.

### 4.4 Analysis plugin contract

```python
class Analysis(Protocol):
    name: ClassVar[str]                        # "map2d", "river_km_statistics"
    config_model: ClassVar[type[BaseModel]]
    version: ClassVar[str]                     # part of the cache fingerprint

    def validate(self, cfg, sims) -> list[ValidationIssue]: ...
    def process_tasks(self, cfg, sim) -> Iterable[ProcessTask]: ...
    def process(self, task, sim, store) -> None: ...          # raw -> cache, never plots
    def plot_tasks(self, cfg, sims, store) -> Iterable[PlotTask]: ...
    def plot(self, task, store) -> list[Path]: ...            # cache -> figures, never reads raw
```

Analyses are registered through entry points (`d3dplot.analyses`), so project-specific ones (the Matlab `adhoc`) can live in a separate package.

* **`map2d`** (M2D)
  * Process tasks: (simulation × variable × selection × time step).
  * Plot tasks: one *plot group* per (simulation × variable × comparison × block of times). A group saves all images for its colour-limit sets and extents, reusing one figure (§4.10).
* **`river_km_statistics`** (SMB)
  * Preparation task, per (simulation × region × river-km set): the segment geometry and cell assignment (§2.5), **written to a file** in `cache/geometry/`.
  * Process tasks: (simulation × region × km set × variable × time step), computing all statistics at once. Region groups reuse the results of their regions.
  * Post-process tasks: derived quantities (slope, × width, region differences, time averages).
  * Plot tasks: one per (view × comparison × variable × region group × …).
* **Measurements** are prepared in the *prepare* stage (conversion, catalogue, raster windows), not during plotting (§4.12).

### 4.5 Cache: grid file + one data file per time step

**Design (as in Matlab, confirmed):** the grid is stored once per simulation in its own file, and every processed variable is stored as **one file per time step** that contains only the values.

Why this is good:
* A large mesh (10⁵–10⁶ cells with node coordinates and connectivity) is written once, so data files are small and fast to write and read.
* One file per time step is the natural unit of work. It is written by exactly one task, can be skipped individually when it already exists (§4.6), and gives incremental postprocessing of running simulations for free (new time steps = new files).
* NetCDF/HDF5 does not support several processes writing to one file. With one file per time step, every worker writes its own files, so no locking is needed.
* Plotting one time step reads one small file.

Cost: many files (variables × selections × time steps). This is acceptable:
* files are grouped in sub-folders per analysis/variable/selection, so no folder becomes huge;
* fingerprints are stored *inside* each file (§4.6), so there is no shared index that every worker has to update;
* listing and opening many small files on a network drive is slower than one large file. Space–time views therefore read the time steps once in the plot stage, and can optionally keep a consolidated copy (an option, not the default).

**Why the earlier draft recorded a grid id in each data file, and why it is dropped:** the idea was to protect against a grid that changes under an existing cache (a simulation re-run with a modified net file in the same folder) and to share one cache between simulations. You are right that this is unnecessary:
* each simulation has exactly one grid;
* each simulation has its own cache folder (§4.9);
* SMT sub-runs are checked once during validation to have the same grid.

The remaining case, a re-run with a different grid, is covered by the run identity in the fingerprint (§4.6). A changed run makes all entries of that simulation stale, including the grid file. Data files therefore only store values. `store.open(...)` combines them with the simulation's grid file and returns an `xugrid.UgridDataset`, and `d3dplot export` writes standalone UGRID files when needed (QGIS, sharing).

**Geometry is written to files.** Yes: when this document says that the in-polygon results are "cached", it means they are **written to NetCDF files** in `cache/geometry/` and reused by all variables, time steps and later runs, exactly like Matlab's `.mat` files. The same applies to the per-extent face subsets used for fast plotting (§4.10).

Layout (per simulation; location in §4.9):

```
cache/
├─ grid.nc                                        UGRID mesh2d + face area + CRS            written once
├─ times.nc                                       hydrodynamic + morphological time axis    (§4.7)
├─ simulation_status.json                         (§4.7)
├─ geometry/
│  ├─ river_km_<file>.nc                          parsed river-km file (x, y, branch, km)
│  ├─ region_<name>.nc                            face in region (bool)                       SMB
│  ├─ segments_<region>_<km set>.nc               face -> segment index (-1 = none), centres,
│  │                                              edges, Δs, branch, cut-line and polygon vertices
│  └─ extent_<hash>.nc                            face indices inside a plot extent            (§4.10)
├─ measurements/                                  (reference simulation only; §4.12)
│  ├─ catalogue_<hash>.csv                        parsed catalogue + bounding box per file/tile
│  ├─ profiles/<region>_<hash>.nc                 .mat measurements converted to NetCDF
│  └─ map2d/<catalogue>/<extent>/<time>.nc        raster mosaic in a window (or .gpkg for polygons)
└─ data/
   ├─ map2d/<variable>/<selection>/<yyyymmddHHMMSS>.nc                       values (face)
   └─ river_km_statistics/<region>/<km set>/<variable>/<yyyymmddHHMMSS>.nc   (segment, statistic)
```

`<selection>` is a short readable label (e.g. `layer-top`, `depth-average`, `fraction-2`). The full parameters are stored in the file attributes.

Data encoding:
* `float32` by default (the model output is usually `float64`, but plotting does not need it). `float64` can be selected.
* zlib compression level 4.
* CF attributes (`long_name`, `units`).
* Encoding is checked by regression tests.

Every file is written atomically: write `*.nc.tmp`, then `os.replace` (atomic on Windows and Linux). An interrupted task never leaves a half-written file that looks valid.

### 4.6 Reprocessing policy

**Assessment of the Matlab rule "do not reprocess what exists unless asked": the intent is right, because reprocessing is the expensive part and repeated runs must be cheap. File existence alone is not a safe criterion, though.** It silently reuses stale data after:
* a re-run of the simulation;
* a change of a processing option (for example depth-average limits that are not part of the file name);
* a code fix in the analysis.

That is why Matlab needed `overwrite` and debug flags such as `tim_just_load`.

Proposal: every cache file carries a **fingerprint**, stored as a global attribute *inside the file itself*. With one file per time step and many parallel writers, there is then no shared index to update or corrupt:

```
fingerprint = hash(
    analysis name + analysis version,             # bumped when the processing algorithm changes
    processing parameters only (normalised),      # e.g. variable, layer, depth-average limits; NOT colours
    source identity of the simulation run,        # run id = hash(MDU) + map 'date_created' attribute
    time value of the file                         # new time steps do not invalidate existing files
)
```

Checking a file means reading one attribute, which is cheap. Opening many files only to read an attribute is still slow on network drives, so the planner reads all attributes of a folder once at start-up.

Policy option `reprocess` (per analysis, overridable on the CLI):

| Value | Behaviour | Use |
|-------|-----------|-----|
| `if_changed` *(default)* | Reuse when the fingerprint matches; otherwise rebuild that entry only | Normal work |
| `never` | Reuse whatever exists, warn if the fingerprint differs (= Matlab default) | Archived simulations, slow disks, debugging |
| `always` | Rebuild everything requested | Suspected corrupt cache |

Notes:
* For a *running* simulation, existing time-step files stay valid. New time steps are simply new files, so incremental postprocessing during a run works naturally.
* Figures have their own policy, `replot: if_changed | never | always`. A figure fingerprint covers its cache inputs and *all* styling options, and is stored in the PNG metadata. Changing a colour limit therefore redraws only the affected figures.
* `d3dplot clean --stale` removes entries whose fingerprint no longer matches any configuration.

### 4.7 Simulation status

**Assessment: yes, keep it.** It is cheap to compute once and expensive to recompute (parsing large `.dia` files, opening many partitions), and it serves several purposes:
1. **Validation**: warn when a simulation is still running or crashed, and refuse to compare unfinished simulations unless `allow_unfinished=True`.
2. **Cache trust**: when a run is *finished*, its outputs are immutable. The time axis and source fingerprints can then be reused without reopening the NetCDF files (as Matlab `D3D_time_dnum` did).
3. **Reporting**: a table of runtime, model version and number of processes for the project.

Detection (FM): in order,
1. a `.dia` file contains `Computation finished` / `Simulation finished` → `finished`. For partitioned runs, the partition files (`dflowfm_0000.dia`, …) are checked: in `r209` the main `dflowfm.dia` of a sub-run does not contain the final lines;
2. there is a controlled-exit message → `interrupted`;
3. the last map time < `TStop` and the files have not been modified for a configurable time → `crashed_or_stopped`;
4. otherwise → `running`;
5. no output at all → `not_started`.

Detection (SMT): `output/work` exists → `running`; otherwise inspect the last sub-run as for FM.

Storage, with one source of truth and one human view:
* `cache/simulation_status.json` (machine-readable, reused). Written every time, but **trusted on later runs only when the state is final** (`finished`/`interrupted`). Non-final states are re-checked each run, which is cheap.
* `tables/simulation_status.csv` (human-readable, as requested). It is regenerated each run. **Tables** is the right place: it is a report, and keeping it out of the cache means `clean --cache` does not delete it. A project-level summary of all simulations goes to the `tables/` folder of the reference simulation.

  | label | path | kind | state | checked_at | model_start | model_end | last_output_time | n_outputs | morfac | wall_clock | n_processes | dflowfm_version |

### 4.8 Execution and parallelism

The planner expands the configuration into a **task graph** in stages:
1. *prepare*: grid, time axis, status, geometry. Shared by later tasks.
2. *process*: (simulation × variable × selection × time step). Independent of each other.
3. *plot*: plot groups (§4.10).
4. *movie*: after its frames exist.

Each stage finishes before the next starts. Tasks whose output file exists with a matching fingerprint are skipped, so every run is **resumable**.

Executors:

| Executor | Where | Notes |
|----------|-------|-------|
| `serial` | anywhere | One process. Debugging and deterministic tests |
| `processes` *(default)* | Windows and Linux, one machine | Local process pool, explained below |
| `slurm` | Linux cluster | SLURM job arrays, explained below |

#### Local process pool (Windows and Linux)

What happens when a user runs `python main_project.py` or `d3dplot run config.yaml --workers 8` on a Windows (or Linux) workstation:

1. The **main process** validates the input, builds the task list and removes the tasks that are already done.
2. It starts **8 worker processes**. These are ordinary Python processes on the same machine (Python's `concurrent.futures.ProcessPoolExecutor`). On Windows each worker is started fresh ("spawn") and imports `d3dplot`, which takes a second or two. The same start method is used on Linux so that the behaviour is identical.
3. The main process hands out tasks **one at a time to whichever worker is free** (dynamic load balancing). A worker opens the model output itself (read-only), computes, **writes its own output file**, and returns only a short status. Workers never write to the same file (§4.5), so no locking is needed.
4. The main process shows progress and writes the log. A failing task is logged with its traceback and listed in the final summary. The other tasks continue.

What the user must know:
* A Python script that starts the run must have the `if __name__ == "__main__":` guard (shown in all examples). Without it, Windows workers would re-run the script. `d3dplot init` writes it automatically.
* The default number of workers is the number of physical cores minus one, reduced if the estimated memory per task would exceed the available memory. It can be set with `execution.workers` / `--workers`.
* On network drives (`P:`), many workers reading at once can be limited by the network rather than by the CPU. More workers do not always help. `d3dplot benchmark` can suggest a value.
* **Scope on Windows: one machine.** Distributing work over several Windows machines is **not** part of v1. On Windows, the large jobs should go to the Linux cluster.

#### SLURM (Linux): stub in v1

SLURM support is **a stub in v1** and will be detailed later. What v1 fixes now, so that the rest of the design does not depend on the details:
* `slurm` is an executor behind the same interface as `serial` and `processes`.
* The task list is deterministic. A `--shard i/N` option executes only one share of it, which is the building block for job arrays.
* Stages (prepare → process → plot) are separate commands, so they can later be chained as dependent jobs.
* The configuration has an `execution.slurm` section. v1 validates it and writes the job scripts with `--dry-run`, but does not submit.

Cluster defaults recorded for later:

```yaml
execution:
  slurm:
    nodes: 1
    tasks_per_node: 4
    partition: 4vcpu
    time_limit: "12:00:00"
    job_name: process          # per stage: prepare / process / plot
```

The choice between job arrays with sharding and `dask-jobqueue` (explained in v2) stays open until the SLURM part is detailed.
### 4.9 Output location and layout

**Default: inside each simulation folder**, in **one sub-folder `d3dplot/`**. It can be overridden:
* per simulation: `simulations[k].output_dir`;
* for all simulations: `output.root`, giving `<root>/<simulation label>/`. Useful when simulation folders are archived or read-only, and used by the acceptance tests (§9.3).

Why a single sub-folder instead of Matlab's `mat/`, `figures/`, `csv/`, `json/`, `logs/` directly in the simulation folder:
* existing simulation folders already contain Matlab `D3D_plot` output with exactly those names. Mixing both tools' files in one `figures/` folder would be confusing;
* it is a safety measure (§4.14): the tool only writes, and `clean` only deletes, inside folders it created and marked;
* removing all postprocessing of a simulation is removing one folder.

Validation checks that output folders are writable, are not inside an input-only location given by the user (`output.forbidden_roots`, §4.14), and that no two simulations share one.

```
<simulation folder or output_dir>/d3dplot/
├─ .d3dplot-output   marker file (tool version, creation time); required before anything is written or cleaned
├─ cache/            grid.nc, times.nc, simulation_status.json, geometry/, measurements/, data/   (§4.5)
├─ figures/          <analysis>/<series>/<view>/<comparison>/<variable>/<name>.png
├─ tables/           simulation_status.csv, river_km_statistics/*.csv
├─ config/           run_<timestamp>.yaml (resolved), latest.yaml
└─ logs/             d3dplot_<timestamp>.log
```

Figures and tables that combine simulations (`all_simulations`, differences with the reference) go under the **reference simulation**. So do the project-level measurement caches.
### 4.10 Efficient plotting: "draw once, save many"

**Problem.** For M2D, one dataset (a variable at one time) usually produces many images: several colour-limit sets and several *extents* (x/y windows zooming into parts of a large model). Matlab rebuilt the complete figure for every image: it drew all faces of the grid and then set `xlim`/`ylim`. This is robust (no state carried over), but slow:
* creating the patch object for 10⁵–10⁶ faces is repeated for every image;
* the renderer still processes all faces, including the ones outside the window.

**Proposal.** Plot tasks are grouped. A *plot group* is (simulation × variable × comparison × block of time steps) and works like this:

1. **Grid geometry once per worker.** The face polygons are built once from `grid.nc` and kept in memory.
2. **Per extent, only the faces that are visible.** For each extent, the faces whose bounding box intersects the window (plus a small margin) are computed once per simulation and extent, and **written to** `cache/geometry/extent_<hash>.nc`. The figure for that extent draws only those faces. A zoom covering a small part of the model then renders only that part.
3. **One figure per extent, reused.** It is created once with everything that does not change: the visible-face collection, background tiles (downloaded once per extent and kept on disk), land boundaries, fixed weirs, river-km labels, axis labels and colour bar.
4. **Only the data changes inside the loop.** For each time step, only the colour values (`collection.set_array`) and the title are updated. For each colour-limit set, only the limits (`set_clim`) are updated. Then the PNG is saved. No figure or polygon objects are created in the loop.
5. **Very large full-domain views** (many faces per screen pixel): optional `render: auto | polygons | raster`. `raster` first rasterizes the field to the figure's pixel grid (`xugrid`/`dfm_tools.rasterize_ugrid`) and shows it as an image. It looks the same at that resolution and is much faster. `auto` chooses raster when the number of faces per pixel is above a threshold.

**Keeping Matlab's robustness:**
* inside a group, only data, colour limits and title are changed. Everything else is fixed when the figure is created;
* figures are closed at the end of every group, and a group is a separate task, so state never leaks between groups;
* if anything fails inside a group, the group discards its figures and redoes the remaining images with fresh figures;
* a `--fresh-figures` debug option rebuilds every image from scratch (Matlab behaviour). A regression test asserts that both modes give **pixel-identical** PNGs.

The block size of a group balances figure reuse against parallelism (default: enough groups for about 4 per worker). The speed-up will be measured by benchmarks in the test bench (§9.3). The expectation is a large gain for zoomed extents and for many colour-limit sets.

The same principle (static parts once, data updates in a loop) applies to SMB profiles and time series, where the gain is smaller.

**Figure formats.** v1 writes **PNG** only (`figure.formats: ["png"]`, the default and only value accepted for now). Saving goes through a small *writer registry* (`plotting/writers.py`). Adding PDF/SVG later is a new writer plus allowing the value; for vector formats the face collection would be rasterized inside the vector file. Figure metadata (configuration hash, fingerprint) is written in the PNG text chunks.

### 4.11 Labels and languages

The Matlab idea in [labels4all.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/figure/labels4all.m) is kept: labels come from **separate dictionary files**, not from the code. The Matlab tool has two tables, `labels4all_variable_to_key.csv` (`variable,key`) and `labels4all_translation_keys.csv` (`key,en,nl,es,un_type`). It builds the label from the translated name and a unit derived from the unit type, the unit conversion factor and the vertical datum (`Lref`, default `+NAP`). It also builds the derived labels: difference, standard deviation, relative, percentage, above background, filtered.

Python design (`src/d3dplot/labels/`):

| File | Columns | Purpose |
|------|---------|---------|
| `data/variable_to_key.csv` | `variable,key` | Any accepted name (descriptive name, Matlab alias, raw NetCDF name) → label key |
| `data/translations.csv` | `key,en,nl,es,unit_type` | Translated quantity names. Also holds the **templates** for derived labels (e.g. key `template.difference`: `difference in {quantity}` / `verschil in {quantity}` / `diferencia de {quantity}`), so word order can differ per language |
| `data/units.csv` | `unit_type,scale,symbol` | Unit symbol for a unit type and conversion factor (e.g. `L,1,m`; `L,1000,mm`; `L/T,1,m/s`). `Lref` adds the datum: `m+NAP` |

API:

```python
label(variable="bed_level", language="nl", kind="difference_time", scale=1, datum="+NAP", fraction=None)
# -> Label(text="verschil in bodemhoogte [m]", quantity="bodemhoogte", unit="m")
```

`kind` follows the comparisons and statistics: `value`, `difference_time`, `difference_simulation`, `difference_simulation_relative` (`[%]`), `std`, `relative`, … Fractions append the fraction number (later: the fraction name from the `.sed` file).

Rules:
* Languages in v1: `en`, `nl`, `es`. A unit test asserts that every key has all three.
* Files are UTF-8, also checked by a test (accented Spanish characters must survive Windows and Linux).
* The dictionaries are seeded from the Matlab CSV files and extended with the descriptive variable names of §7.6.
* Unknown variable: fall back to the NetCDF `long_name` and `units`, with a warning listing the missing key.
* Users can add or override entries with their own CSV files in the same format (`labels.extra_dictionaries`), without changing the package.
* The `language` option, the datum (`labels.vertical_datum`, default `+NAP`) and the display scale (`display_scale`) are configuration options.

### 4.12 Measurements

Goals:
* the same capabilities as Matlab for M2D and SMB;
* general enough for later analyses (HIS, PRF);
* measurements are prepared in the *prepare* stage, not while plotting;
* inputs are never modified.

**Measurement kinds**, each with a small documented data model and readers selected by file type (a registry, so new formats are plugins):

| Kind | Used by | Accepted input | In memory |
|------|---------|----------------|-----------|
| `raster` | M2D | GeoTIFF `.tif`, GDAL `.vrt` mosaics, NetCDF CF raster (`[time,] y, x` + CRS) | `xarray.DataArray (y, x)` + validity mask, read by window |
| `polygons` | M2D | `.shp`, `.gpkg` with a value field | `geopandas.GeoDataFrame` (geometry, value, id) |
| `river_km_profiles` | SMB | **NetCDF** (recommended), `.mat` (Matlab structure, converted automatically) | `xarray.Dataset (statistic, time, river_km)` |
| `time_series` *(later)* | HIS | NetCDF (CF `timeSeries`), CSV | — |

New dependency: `rioxarray`/`rasterio` (GDAL wheels for Windows and Linux) for GeoTIFF/VRT. `geopandas`/`pyogrio` already come with dfm_tools.

#### M2D: catalogue, selection, preparation

* **Catalogue.**
  * The existing CSV format is accepted unchanged: `time, file, factor`. Times are ISO 8601 with offset. Paths are absolute or relative to the CSV, and Windows/Linux path styles are converted.
  * Alternatives:
    * a YAML catalogue with the same columns plus optional `type`, `crs`, `value_field`;
    * a single NetCDF raster stack with a `time` dimension, which needs no catalogue.
* A catalogue is attached to a **variable** (`measurements: {bed_level: …}`), because measurements are of one quantity.
* **Prepare, metadata only:**
  * parse the catalogue and resolve the paths;
  * read the bounding box and CRS of every file, and of every tile of a VRT;
  * write `cache/measurements/catalogue_<hash>.csv`.
* **Selection** (as Matlab), per plot time *t* and extent:
  * the entries with `|t_mea − t| ≤ time_tolerance` whose bounding box overlaps the extent enlarged by `spatial_margin_m`;
  * the panel title shows the mean time of the selected entries.
* **Rasters**:
  * only the window is read (`rioxarray`/`rasterio` windowed reads; VRTs are handled by GDAL) and multiplied by `factor`;
  * the tiles are mosaicked on the grid of the first tile. Cells are filled in catalogue order (Matlab rule, `mosaic: first`; alternatives `last`, `mean`);
  * *Change from Matlab (agreed):* tiles whose grid is not aligned are resampled (nearest) onto the common grid, instead of the 0.5-cell shift. Values can differ slightly at tile edges. This is covered by the synthetic test case (§9.4).
* **Polygons**:
  * read with a bounding-box filter (`geopandas.read_file(..., bbox=…)`);
  * the polygon filters are applied and the value is multiplied by `factor`.
* **Polygon filters** are configurable rules, not code. A preset `rws_vakken` reproduces Matlab:
  * value field `MEAN`;
  * coverage `COUNT / area ≥ 0.99`, with the area field taken from the candidates `A`, `oppervlak_`;
  * location field `locatie`/`Locatie`, allowed codes −3…3 (L3…R3).
  
  Field names are matched case-insensitively against candidate lists, because they differ between datasets (§2.6).
* **Differences** for `difference_time`, measurement(*t*) − measurement(*t₀*):
  * rasters: the *t₀* mosaic is resampled onto the *t* grid, and the result is valid only where both are;
  * polygons are matched by **id field** (`code_uniek`) when available, otherwise by identical geometry. Only as a last resort are they matched by order with a count check (the Matlab behaviour), with a warning.
* **Written to files**: `cache/measurements/map2d/<catalogue>/<extent>/<time>[_diff].nc` for rasters (x, y, value, mask), or `.gpkg` for polygons. They are prepared once per (extent × plot time × value/difference) and reused by all colour-limit sets and simulations. Matlab re-read them for every colour-limit set × window × simulation.

#### M2D: plotting

* **Layout** `side_by_side` (Matlab default): a model panel and a measurement panel, with the same colormap, colour limits and colour bar. The titles show the model time and the measurement time.
  * The layout is a parameter of the figure template, so `overlay` or `model_minus_measurement` (which needs the measurements interpolated to the faces) can be added later.
* `color_limits_from_measurements` (Matlab `do_clims_measurements`): the automatic colour limits are taken from the range of the measurement values in the window. Explicit `color_limits` still take precedence.
* Draw once, save many (§4.10): the measurement image is created once per extent, and only its data changes per time.
* No measurements are shown in the `difference_simulation*` comparisons.

#### SMB: river-km profiles

**Canonical NetCDF format** (recommended for all new measurement data):

```
dimensions:  statistic, time, river_km
coordinates: time(time)              datetime64 (UTC); attribute source_time_zone
             river_km(river_km)      km
             s(river_km)             m, distance along the axis (optional)
             branch(river_km)        string (optional)
             statistic(statistic)    string: mean, mean_area_weighted, std, …   (SMB statistic names, §7.5)
variables:   bed_level(statistic, time, river_km)              units "m", vertical_datum "NAP", long_name
             bed_slope_streamwise(statistic, time, river_km)   units "1"
global:      Conventions "CF-1.8", d3dplot_kind "river_km_profiles", region, source_files, history
```

Variable names are the canonical names (§7.6). Aliases such as `bl`, `mesh2d_mor_bl` and `DPS` are resolved through the variable registry.

**`.mat` input is accepted.** In the prepare stage it is converted automatically to `cache/measurements/profiles/<region>_<hash>.nc`, where the hash covers the source path, size and modification time. Every later step uses the NetCDF; the `.mat` file is only read. The converter:
* reads MAT v5/v7 with `scipy.io.loadmat`, and v7.3 (HDF5) with `h5py`;
* maps `val_<statistic>` to the SMB statistic names (`val_mean` → `mean`, `val_mean_weighted` → `mean_area_weighted`, …) and variable aliases to canonical names (`bl` → `bed_level`, `detab_ds` → `bed_slope_streamwise`);
* converts Matlab datenums to datetimes. Datenums have no time zone, so they are interpreted in `measurements.time_zone` if given, and otherwise in the model's time zone (agreed);
* **sorts the times**. The example file is not in chronological order;
* parses the unit string (`'bed elevation [m+NAP]'` → units `m`, datum `NAP`) and copies `source` to `source_files`;
* checks that `val` is `[n_location, n_time]` and consistent with `rkm`, `s` and `tim_dnum`. If variables have different axes, it writes one dataset per variable.

`d3dplot measurements convert L1R1_measured.mat -o L1R1_measured.nc` converts a file permanently. It never overwrites an existing file. The docs describe the format and include a short Python example to write it, so that new data is delivered as NetCDF.

**Matching** (Matlab rules, configurable):
* *profile views*: the nearest measurement time within `measurements_time_tolerance` (default 30 days);
* *time-series views*: the nearest location within `measurements_km_tolerance` (default 1 km; hard-coded in Matlab);
* *statistic equivalences*, default `mean_area_weighted → [mean_area_weighted, mean]` and `mean → [mean, mean_area_weighted]` (Matlab prefers the measured `val_mean`);
* *x-axis*: `river_km` (default) or `s`.

**Comparisons**:
* `value`;
* `difference_time`: measurement(*t*) − measurement(*t₀*), with *t₀* matched to the first plot time;
* differences between simulations: none.

**Regions**:
* every region in a region group has its own measurement file;
* region differences have their own file (`region_differences[].measurements`).

**Display**: markers or a line per region. `limits_include_measurements` extends the axis limits to include them.

#### Validation of measurements

* Files exist and are readable.
* Catalogue columns and file types are valid.
* CRS matches the model.
* Times have a time-zone offset, or a `time_zone` is configured.
* The requested variable and statistic exist (with aliases and equivalences).
* The km ranges overlap the requested river km.
* For every plot time, a measurement exists within the tolerance. If not, a **warning** lists the nearest available measurement times.

*Future*:
* SMB profiles computed directly from the polygon shapefiles with the same segment geometry (§2.5). The `.mat` files are derived from such shapefiles, see `source`;
* model − measurement maps;
* station time series for HIS.

### 4.13 Plot hooks (user functions)

Matlab `function_handles` (e.g. `@add_structures_rkm`, `@legend_horizontal`) let projects add elements to figures. Python equivalent:

```yaml
analyses:
  river_km_statistics:
    hooks: ["maas_project.plotting:add_structures_rkm", "d3dplot.hooks:legend_horizontal"]
```

* A hook is an importable `"module:function"` string, so it works from YAML and inside worker processes.
* Signature: `hook(figure, axes, context)`. `context` holds the analysis, variable, time, extent, simulations, labels and the river-km provider.
* Hooks are called after the tool has drawn and before saving.
* The hook names and the hash of their source code are part of the figure fingerprint, so changing a hook replots the affected figures.
* Generic hooks are shipped with the tool (e.g. `legend_horizontal`). Project-specific ones live in the user's project package.

### 4.14 Inputs are read-only

Principle: **the tool never modifies or deletes any input file** (model files, measurements, GIS files, river-km files). It only creates files in its own output folders.

1. **Single write path.** Every file write goes through the `store` atomic writer. Its *write guard* refuses any target that is not inside an output folder carrying the `.d3dplot-output` marker. The marker is only created in a new or empty folder.
2. **Nothing is written next to inputs.**
   * Matlab wrote parsed copies next to inputs (`rkm.mat` next to `rkm.csv`) and `simdef.mat` in the simulation folder. The Python tool keeps such data in its own cache.
   * The default output does create a *new* `d3dplot/` folder inside the simulation folder (§4.9). It never changes existing files there.
   * If even that is not allowed (archived or input-only locations), use `output.root`.
3. **Inputs are opened read-only**: NetCDF in mode `r`, rasterio in read mode, other files as `"rb"`.
4. **`clean`** deletes only inside marked folders, and only files it recognises (a fingerprint attribute or a known tool file name). It never follows links out of the folder.
5. **Converters** never overwrite existing files and refuse to write to a path that is an input of the current configuration.
6. **`output.forbidden_roots`** (e.g. `p:\11211565-002-maas-mor-2026\C_Work\00_data`): validation fails if any output folder resolves inside them.
7. **Tests**:
   * unit tests for the guard;
   * an integration test that records size, modification time and hash of every input file before and after a full run, and asserts that nothing changed;
   * the acceptance tests on P-drive data run with `output.root` in a local temporary folder (§9.3).

---
## 5. v1 analyses: configuration sketch

The example below is the Python equivalent of the Maas acceptance case `main_plot_01.m` (§2.6, §9.3). Paths are abbreviated.

```python
from datetime import datetime, timedelta
from pathlib import Path
from d3dplot import Project, Simulation
from d3dplot.analyses import Map2D, RiverKmStatistics
from d3dplot.config import Times, Figure, RiverKm, Region, RegionGroup, Measurements, Execution, Output

prj = Path(r"p:\11211565-002-maas-mor-2026")
data = prj / "C_Work" / "00_data"
mea_smb = data / "11_bl_DM_pol_measurements" / "Lixhe-Roermond-2021-2026"
times = [datetime(2021, 2, 8), datetime(2021, 8, 8), datetime(2023, 1, 15), datetime(2025, 12, 31)]

def region(name):                                   # region polygon + its measurements (.mat accepted)
    return Region(name=name, polygon=data / "02_sb" / f"{name}.shp",
                  measurements=mea_smb / f"{name}_measured.mat")

project = Project(
    simulations=[Simulation(path=prj / r"C_Work\…\computations_smt\r209", label="validation")],  # SMT
    reference_simulation="validation",
    language="en",
    series="05",
    output=Output(forbidden_roots=[data]),          # never write into the input data folder
    execution=Execution(executor="processes", workers=8),
    analyses=[
        Map2D(
            variables=["bed_level"],
            times=Times.at(times, axis="morphological", tolerance=timedelta(days=10)),
            comparisons=["value", "difference_time"],
            measurements={"bed_level": Measurements(
                catalogue=data / "12_2D_measurements" / "2D_measurements_02.csv",
                time_tolerance=timedelta(days=60),
                color_limits_from_measurements=True)},
            figure=Figure(
                color_limits={"bed_level": {"difference_time": [None, (-1, 1)]}},   # None = automatic
                extents_along_river_km={"file": data / "01_rkm" / "rkm_3km_lr.csv", "window_x_m": 1500, "window_y_m": 1500},
                river_km_labels={"file": data / "01_rkm" / "rkm_1km_lr.csv"},
                land_boundaries=[data / "05_landuse" / "j21_ldb.mat"],
                aspect="equal",
            ),
        ),
        RiverKmStatistics(
            variables=["bed_level"],
            regions=[region(n) for n in ["L1R1", "L3R3", "L1L3", "R1R3", "L2L3", "R2R3"]],
            region_groups=[RegionGroup(["R2R3", "L2L3"]), RegionGroup(["R1R3", "L1L3"])],
            river_km=RiverKm(file=data / "01_rkm" / "rkm.csv",       # MA tags -> Maas rules, no track needed
                             sets={"250m": [2 + 0.25 * i for i in range(333)]}),
            statistics=["mean_area_weighted"],
            times=Times.at(times, axis="morphological", tolerance=timedelta(days=10)),
            views=["profile", "time_series"],
            comparisons=["value", "difference_time"],
            time_series_locations_km=[*range(15, 19), *range(23, 31), *range(35, 42)],
            measurements_time_tolerance=timedelta(days=60),
            figure=Figure(x_limits=[(2, 85), (2, 7), (7, 12), (16, 21), (30, 36), (15, 55)]),   # one figure each
            hooks=["maas_project.plotting:add_structures_rkm", "d3dplot.hooks:legend_horizontal"],
        ),
    ],
)

if __name__ == "__main__":       # required for multiprocessing on Windows
    project.run()
```

The river (Rijntakken, Maas or generic) is detected from the branch tags in the river-km file (§2.5). Here the file has `MA` tags, so the Maas rules apply and no track is needed (Matlab needed `maas_branches`).

---

## 6. User interaction

### 6.1 Installation

```bash
pip install delft3d-plot                    # or: pixi/conda environment with dfm_tools + hydrolib-core
d3dplot --version
```

### 6.2 Getting started (input creation, §8)

```bash
d3dplot info      p:/proj/runs/r002             # kind (FM/SMT), status, files, grid, times, CRS
d3dplot variables p:/proj/runs/r002             # available names + aliases + raw names, units
d3dplot init      p:/proj/runs/r002 p:/proj/runs/r003 -o d3dplot.yaml   # commented starter config
```

### 6.3 Running

Python script (§5) or YAML + CLI:

```bash
d3dplot validate d3dplot.yaml                   # seconds; reads metadata only
d3dplot process  d3dplot.yaml --workers 16      # build cache (resumable)
d3dplot plot     d3dplot.yaml --only map2d --variable bed_level --time last
d3dplot run      d3dplot.yaml                   # validate + process + plot
d3dplot slurm submit d3dplot.yaml --dry-run     # v1: only writes SLURM scripts (stub, §4.8)
d3dplot status   d3dplot.yaml                   # simulation status table + cache/figure progress
```

### 6.4 Replotting

The Matlab JSON served provenance *and* replotting. The proposal keeps both, at three levels:

1. **Whole project**:
   * Every run writes the *resolved* configuration (all defaults filled in) to `d3dplot/config/run_<timestamp>.yaml` and `d3dplot/config/latest.yaml` (under the reference simulation).
   * `d3dplot plot <simulation folder>/d3dplot` (or `… config/latest.yaml`) replots from the cache without needing the original script.
   * Because figures have fingerprints, only the figures whose options changed are redrawn.
2. **With overrides**, without editing files:
   ```bash
   d3dplot plot config/latest.yaml --set analyses.map2d.figure.color_limits.bed_level.value='[[-5,5]]' \
                                    --only map2d --variable bed_level
   ```
3. **A single figure**:
   * Every saved image carries metadata in the PNG text chunks: the analysis, the plot-task parameters and the configuration hash.
   * `d3dplot replot figures/map2d/…/bed_level_20200101.png --set figure.dpi=600` regenerates exactly that figure.
   * The Python equivalent is `Project.from_figure(path).replot(...)`.

Users who want their own figures can load the cached data directly:

```python
ds = project.results("map2d", simulation="reference", variable="bed_level", time="last")   # xugrid
df = project.results("river_km_statistics", simulation="dredging", variable="bed_level").to_dataframe()
```

### 6.5 Typical iteration loop

1. Write the input (`init`, an agent skill, or an example). `validate` gives feedback in seconds.
2. `process` once (parallel, resumable, possibly on SLURM).
3. `plot` / `replot` repeatedly while tuning limits, colours, language or subsets. This takes seconds and does no raw I/O.
4. Changing a processing option rebuilds only the affected cache entries.

---

## 7. Naming conventions

### 7.1 Principles

1. **Full English words in `snake_case`.** No abbreviations unless they are universal (`crs`, `dpi`, `km`). Plural for lists (`variables`, `times`).
2. **No `do_` prefixes.** Enabling something means listing it, e.g. `comparisons=[...]` or `views=[...]`. Booleans are adjectives or nouns (`background_tiles=True`).
3. **Orthogonal options instead of combinatorial flags.** Matlab has `do_xvt_diff_t`, `do_all_s_diff_t`, `do_tv_all_s`, …. Here a figure is *view × comparison × grouping*, each chosen independently.
4. **One name per concept** across all analyses (`times`, `color_limits`, `extents`, …).
5. **Units in the name** when ambiguous (`half_width_m`, `duration_s`, `size_cm`), or typed values (`timedelta`).
6. Values are **strings from closed sets** (enums), validated and documented. No magic numbers (`tim_type=2`, `NaN`=top layer, `Inf`=last).

### 7.2 Comparisons (shared by all analyses)

| Matlab flag | New value in `comparisons` | Meaning |
|-------------|---------------------------|---------|
| `do_p_single` | `value` | The quantity itself |
| `do_diff_t` | `difference_time` | Value(t) − value(t₀) of the same simulation |
| `do_diff_s` | `difference_simulation` | Value(sim) − value(reference) at the same time |
| `do_diff_s_t` | `difference_simulation_time` | [sim(t) − sim(t₀)] − [ref(t) − ref(t₀)] |
| `do_diff_s_perc` | `difference_simulation_relative` | 100·(sim − ref)/ref [%] |
| `do_diff_t_first_time` | `include_initial_time_difference: bool` | Also plot the (zero) difference at t₀ |
| `do_diff_s_ref_sim` | `include_reference_in_differences: bool` | Also plot reference − reference |
| `use_local_time` | `time_matching: "reference" \| "own"` | Which time axis is used to pair simulations |
| `tim_tol` | `time_tolerance: timedelta` | Maximum mismatch when pairing times |

### 7.3 Common options

| Matlab | New | Notes |
|--------|-----|-------|
| `fdir_sim{k}` / `str_sim{k}` | `simulations[k].path` / `.label` | |
| `sim_ref` | `reference_simulation` | Label or index |
| `lan` | `language` | `en`, `nl`, `es` |
| `tag_serie` | `series` | Figure sub-folder to keep versions apart |
| `fdir_postprocess`, `fdir_mat`, `fdir_fig` | `simulations[k].output_dir`, `output.root` | Default: the simulation folder (§4.9) |
| `simdef_overwrite` | *(removed)* | Discovery is cached with a fingerprint |
| `order_anl`, `order_anl_param` | *(removed)* | Replaced by `execution` (§4.8) |
| `do`, `do_p`, `do_mat`, `do_create_mat`, `tim_just_load` | *(removed)* | Use `enabled: bool` and CLI stages (`process`, `plot`) |
| `overwrite` / `fig_overwrite` | `reprocess` / `replot` | `if_changed` \| `never` \| `always` (§4.6) |
| `only_adhoc`, `adhoc`, `function_handles` | `hooks: ["module:function", ...]` / plugin analyses | §4.13 |
| `var` | `variables` | Descriptive names, aliases accepted (§7.6) |
| `var_idx` / `sum_var_idx` | `fractions: list[int] \| "all"` / `sum_fractions: bool` | |
| `layer` (`NaN`/`Inf`/`[]`/n) | `vertical: "top" \| "bottom" \| "all" \| {layers: [...]}` | |
| `depth_average`, `depth_average_limits` | `vertical: {depth_average: {z_range: [zmin, zmax]}}` | |
| `elevation` | `vertical: {elevation: z}` | |
| `tim` (`NaN`/`Inf`/dates/idx) | `times: "all" \| "last" \| [datetime...] \| {indices: [...]} \| {start, end, step}` | |
| `tim_type` (1/2) | `time_axis: "hydrodynamic" \| "morphological"` | |
| `xlims` / `ylims` | `extents: [[xmin,xmax,ymin,ymax], ...]` (maps); `x_limits`, `y_limits` (line plots) | A list gives one figure per entry |
| `clims`, `clims_diff_t`, `clims_diff_s`, … | `color_limits: {<variable>: {<comparison>: [[min,max], ...]}}` | One figure per entry; `null` = automatic |
| `ylims_diff_t`, `ylims_diff_s`, … | `y_limits: {<variable>: {<comparison>: [...]}}` | Same structure |
| `filter_lims*` | `mask_range: {<comparison>: [lower, upper]}` | Values inside the range are hidden |
| `cmap` | `colormap` | Name or list of colours |
| `clims_type=2`, `clims_type_var` | `color_limits_mode: "fixed" \| {"days_since": date}` | Residence-time plots |
| `fig_print` (0–4) | `figure.formats: ["png"]` | Only PNG in v1; extensible (§4.10) |
| *(new)* | `figure.render: "auto" \| "polygons" \| "raster"` | Map rendering for very large grids (§4.10) |
| `Lref` (in `labels4all`) | `labels.vertical_datum` | Default `+NAP` (§4.11) |
| *(new)* | `labels.extra_dictionaries` | User label CSV files (§4.11) |
| `fig_resolution` | `figure.dpi` | |
| `fig_size`, `font_size` | `figure.size_cm`, `figure.font_size` | |
| `fig_visible` | *(removed)* | Always off-screen |
| `fig_variable`, `fig_unit` | `display_name`, `display_scale` | |
| `do_movie`, `tim_movie` | `movie: {duration_s: 40}` (absent = no movie) | |
| `measurements`, `tol_time_measurements` | `measurements` (per variable for maps; per region for SMB), `time_tolerance` | §4.12 |
| *(new)* | `output.forbidden_roots` | Output folders may never be inside these (§4.14) |
| `do_replace_underscore`, `leg_str` | `legend_labels` | Labels are taken from `simulations[].label` |

### 7.4 `map2d` (M2D)

| Matlab | New |
|--------|-----|
| `do_3D` | `view_3d: bool` |
| `do_vector` (0/1/2) | `velocity_vectors: "none" \| "scaled" \| "normalized"` |
| `do_fxw` (0/1/2), `color_fxw` | `fixed_weirs: "none" \| "input" \| "snapped"`, `fixed_weirs_color` |
| `do_axis_equal` | `aspect: "equal" \| "auto"` |
| `plot_tiles`, `path_tiles` | `background_tiles: bool`, `tiles_cache_dir` |
| `epsg_in` | `crs` |
| `fpath_ldb` | `land_boundaries` (`.ldb`, `.pli`/`.pol`, `.shp`, `.mat` with an N×2 NaN-separated array) |
| `do_rkm_disp`, `fpath_rkm_disp`, `rkm_disp_color` | `river_km_labels: {file, color}` |
| `do_plot_along_rkm`, `fpath_rkm_plot_along`, `rkm_tol_x/y` | `extents_along_river_km: {file, window_x_m, window_y_m}` |
| `write_shp`, `save_format` | `export: ["shapefile", "netcdf"]` |
| `measurements` (catalogue CSV) | `measurements: {<variable>: {catalogue, ...}}` |
| `tol_time_measurements` | `measurements.<variable>.time_tolerance` (default 30 days) |
| `tol_x_measurements`, `tol_y_measurements` | `measurements.<variable>.spatial_margin_m` (default 1000) |
| `measurements_edgecolor` | `measurements.<variable>.edge_color` |
| `measurements_tag_variable`, `_tag_count`, `_tag_area`, `_tag_location`, `measurements_coverage`, `measurements_pol_location` | `measurements.<variable>.polygon_filters: {preset: "rws_vakken", value_field, count_field, area_field, min_coverage, location_field, allowed_locations, id_field}` |
| `do_clims_measurements` | `measurements.<variable>.color_limits_from_measurements` (colour limits from the measurement range) |
| *(new)* | `measurements.<variable>.layout: "side_by_side"`, `mosaic: "first" \| "last" \| "mean"` |

### 7.5 `river_km_statistics` (SMB)

| Matlab | New |
|--------|-----|
| `sb_pol` + `measurements{kp,1}` | `regions: [{name, polygon, measurements}]` (`.nc` recommended, `.mat` accepted) |
| `sb_pol{kp,2}`, `measurements{kp,2}` (several regions in one figure) | `region_groups: [[region_a, region_b]]` |
| `sb_pol_diff`, `measurements_diff` | `region_differences: [{regions: [a, b], measurements}]` |
| `tol_time_measurements` | `measurements_time_tolerance` (default 30 days) |
| *(hard-coded 1 km)* | `measurements_km_tolerance` (default 1 km) |
| `do_include_mea_xylims` | `limits_include_measurements` |
| `leg_mea` | `measurements_label` |
| `fpath_rkm` | `river_km.file` (format fixed, §2.5) |
| `rkm`, `rkm_name` | `river_km.sets: {name: [km...]}` |
| `rkm_br`, `rkm_track` | `river_km.branches` (generic files with several branches) / `river_km.track` (required for Rijntakken). The river is detected from the file tags |
| *(parallel distance, 1000 m)* | `river_km.half_width_m` |
| `do_polygon_boundary`, `polygon_boundary_shrink` | `regions[].concave_hull: {ratio}` (absent = off) |
| `statis_plot` / `val_mean`, `val_std`, `val_max`, `val_min`, `val_num`, `val_sum`, `val_sum_length`, `val_mean_weighted` | `statistics: ["mean", "std", "max", "min", "count", "sum", "sum_per_length", "mean_area_weighted"]` |
| `do_p_single` (profile per time) | `views: ["profile"]` |
| `do_all_s` | `grouping: "all_simulations"` |
| `do_xvt` / `do_xvt_cel` | `views: ["profile_all_times", "space_time"]` |
| `do_tv`, `rkm_plot_tv`, `do_tv_single`, `do_tv_all_s` | `views: ["time_series"]`, `time_series_locations_km` |
| `do_cum` | `views: ["cumulative"]` |
| `do_area` | `stacked_fractions: bool` |
| `do_val_B`, `do_val_B_mor` | `multiply_by_width: "none" \| "flow" \| "morphological"` |
| `detab_ds` | variable `bed_slope_streamwise` (derived) |
| `tim_ave`, `tim_ave_type`, `overwrite_ave` | `time_averages: [{name, times, time_axis}]` |
| `do_plot_inpolygon` / `do_plot_inpoly` | `diagnostics: ["polygon_membership"]` |
| `do_plot_structures` | `structures_overlay: bool` |
| `plot_val0`, `do_staircase`, `do_marker`, `xdir` | `show_initial`, `staircase`, `markers`, `x_reversed` |
| `skip_if_not_found` | `on_missing_input: "error" \| "skip"` |
| `legend_file` | `legend_labels` |

### 7.6 Variable names

The variable registry has three layers, all accepted in `variables`:

| Descriptive name (canonical) | Matlab alias | FM NetCDF (example) |
|------------------------------|--------------|---------------------|
| `bed_level` | `bl` | `mesh2d_mor_bl` / `mesh2d_flowelem_bl` |
| `water_level` | `wl` | `mesh2d_s1` |
| `water_depth` | `wd` | `mesh2d_waterdepth` |
| `velocity_magnitude` | `umag` | `mesh2d_ucmag` |
| `velocity` | `uv` | `mesh2d_ucx`, `mesh2d_ucy` |
| `bed_shear_stress` | `taub` | `mesh2d_taus` |
| `mean_grain_size_arithmetic` / `_geometric` | `dm` / `dg` | derived from `mesh2d_lyrfrac` |
| `active_layer_fraction` | `Fak` | `mesh2d_lyrfrac` (layer 0) |
| `active_layer_thickness` | `La` | `mesh2d_thlyr` (layer 0) |
| `total_sediment_transport` | `stot` | `mesh2d_sxtot`, `mesh2d_sytot` |
| `froude_number` | `Fr` | derived |
| `specific_discharge` | `qsp` | derived |
| `chezy` | `czs` | `mesh2d_czs` |
| … | | (full list in docs, generated from the registry) |

Raw NetCDF names are always accepted, as an escape hatch for anything not yet in the registry.

---

## 8. Helping users write input: are agent skills the best solution?

**Short answer: skills are valuable, but they should not be the only help.** A skill guides an AI agent (e.g. GitHub Copilot in VS Code). It works best on top of *deterministic* tooling that also helps users who do not use an agent. Proposed layers:

| Layer | What | Who benefits |
|-------|------|--------------|
| 1. Typed config + **JSON schema** | Exported from the pydantic models. VS Code YAML extension → autocompletion, inline docs, red squiggles | Everyone, offline |
| 2. **`d3dplot init`** | Inspects the simulations (`info`, available variables, times, CRS) and writes a commented starter YAML/script with only the valid options | Everyone |
| 3. **`d3dplot validate`** with hints | Messages like *"`velocity_magnitud` is not a variable; did you mean `velocity_magnitude`?"*, plus `--format json` for tools | Everyone, and agents |
| 4. **Examples gallery** | `examples/` with one tested script/YAML per use case; rendered in the docs with the resulting figures | Everyone |
| 5. **Agent skills** | `skills/` in the repo (installable into `.github/skills/` of a user project). They instruct the agent to *use layers 1–4* rather than guess | Users of Copilot/agents |

Proposed skills (`SKILL.md` files, versioned with the package and tested in CI against the schema):
* **`d3dplot-create-input`**: interview the user (goal, simulations, analyses, variables, times, comparisons). Then run `d3dplot info` / `variables`, write the YAML or script, run `validate`, and iterate until it is clean. Use the descriptive names (§7).
* **`d3dplot-replot`**: locate `config/latest.yaml` or a figure, change styling with `--set`, and replot only the affected figures.
* **`d3dplot-troubleshoot`**: read `logs/`, `tables/simulation_status.csv` and the validation report, and explain the failures.
* *(developers)* **`d3dplot-add-analysis`**: scaffold a new analysis plugin with its config model, tests and docs.

Why this split:
* Skills cannot guarantee correctness. The validator and schema can.
* The deterministic layers keep working without an agent and on the cluster.
* The skills stay small because the knowledge lives in the schema and CLI.

---

## 9. Project management

### 9.1 Engineering standards

| Topic | Choice |
|-------|--------|
| Python | ≥ 3.11 (dfm_tools floor). CI on 3.11–3.13 |
| Packaging | `pyproject.toml` (hatchling), `src/` layout. Environment through `pixi` (as dfm_tools) |
| Lint/format/types | `ruff` (lint + format), `mypy --strict` |
| Hooks | `pre-commit` |
| VCS | GitHub, protected `main`, PRs with review, conventional commits + `commitizen` → semver + `CHANGELOG.md` |
| Docs | MkDocs Material + mkdocstrings. Includes an option reference generated from the config models, a variable list generated from the registry, and a "coming from Matlab D3D_plot" page with the §7 tables |
| CI | GitHub Actions: `{ubuntu, windows} × {3.11, 3.12, 3.13}`. Lint, types, tests, coverage gate (≥ 85 %), docs build, wheel on tag |

### 9.2 Licence

**Decided: GPL-3.0** (`GPL-3.0-or-later`).
* The `LICENSE` file holds the full GPL-3.0 text.
* Every source file gets an SPDX header (`# SPDX-License-Identifier: GPL-3.0-or-later`).
* `pyproject.toml` declares `license = "GPL-3.0-or-later"`.

This matches dfm_tools (GPL-3.0), which the tool imports. HYDROLIB-core (MIT) is compatible. Upstream contributions are made in those repositories under *their* licences.
### 9.3 Test bench

| Layer | Content | Data | When |
|-------|---------|------|------|
| Unit | Config models and names, validation rules and hints, time selection/matching, vertical selection, variable registry, labels (all keys in en/nl/es, UTF-8, templates), fingerprints and reprocess policy, status detection (synthetic `.dia` files), comparisons, statistics, sharding, SLURM script generation | Synthetic `xugrid` datasets and folders in fixtures | Every commit, Windows + Linux |
| SMB geometry | River-km file parsing (tags, no-tag files), river detection (Rijntakken/Maas/generic), branch per km for every track, bend cutoffs, edges, angles, cut lines, segment polygons, cell assignment, coverage and self-intersection checks. Compared with values computed by Matlab for the same inputs | Synthetic straight and curved rivers + real Rijntakken/Maas km files | Every commit |
| Measurements | Catalogue parsing (CSV with offsets, relative paths, Windows/Linux paths), bounding boxes incl. VRT tiles, time/space selection, raster mosaic and resampling, polygon filters (preset and custom field names), differences (rasters; polygons by id/geometry/order), `.mat` → NetCDF conversion (v5/v7.3, datenum, unsorted times, units), statistic/variable equivalences, km matching | Small synthetic GeoTIFF/VRT/shapefiles/`.mat` files created in fixtures | Every commit |
| Read-only inputs | Write guard. Before/after snapshot (size, mtime, hash) of every input file of a full run must be identical. `clean` and converters never touch inputs | Same | Every commit |
| Integration | `map2d` and `river_km_statistics` end to end on small real FM runs (2D, 3D sigma/z, partitioned, morphodynamic) and a small **SMT** run (≥ 3 sub-runs) | dfm_tools sample data + tiny purpose-made models in `tests/data` (Git LFS) | Every commit |
| Regression: numbers | Cache contents compared with references (`xarray.testing.assert_allclose`) | Same | Every commit |
| Regression: images | `pytest-mpl` baselines | Same | Every commit (Linux baseline) |
| Cache semantics | Changing a parameter / re-running a sim / appending times rebuilds exactly the expected entries. `never` / `always` behave as specified | Same | Every commit |
| Parallel equivalence | `serial` == `processes` == `--shard i/N` (all shards), byte-identical caches. Concurrent shards do not corrupt each other | Same | Every commit |
| Replot | `replot` from `latest.yaml` and from figure metadata reproduces identical images | Same | Every commit |
| Figure reuse | "Draw once, save many" gives PNGs pixel-identical to `--fresh-figures` (§4.10) | Same | Every commit |
| Benchmarks | Process and plot throughput (incl. zoomed extents, fresh vs. reused figures) tracked over time | Medium model | Nightly |
| End-to-end | `examples/*` through the CLI | Same | Nightly |
| Acceptance | Real projects compared with Matlab `D3D_plot` output (`@pytest.mark.requireslocaldata`). **Case 1: Maas Lixhe–Roermond** (below) | P-drive, **read-only** | Manual / scheduled |
| SLURM | Script generation (`--dry-run`) in v1; submission tests when the SLURM part is detailed | — | Release |

**Acceptance case 1: Maas Lixhe–Roermond** (from `p:\11211565-002-maas-mor-2026\E_Software_Scripts\co\57_plot_Lixhe_Roermond\main_plot_01.m`)
* Simulation: SMT run `r209` (`computations_smt`), morphological time.
* M2D: `bed_level` (value, difference in time), measurements from `2D_measurements_02.csv` (VRT/GeoTIFF), windows along `rkm_3km_lr.csv` (±1500 m), river-km labels, land boundary `j21_ldb.mat`.
* SMB: `bed_level`, river-km file `rkm.csv` (Maas tags), 250 m segments from km 2 to 85, regions L1R1, L3R3, L1L3, R1R3, L2L3, R2R3 with `.mat` measurements, two region groups, time series at km 15–18, 23–30, 35–41.
* Comparison targets: the existing Matlab output of this case in the `r209` simulation folder, read-only:
  * `mat/`: 47 files, among them `M2D_<time>_var_bl.mat`, `SMB_<time>_pol_250m_var_bl_sb_<region>.mat`, `SMB_pol_250m.mat`, region membership `<region>.mat`, `M2D_tim.mat`/`tim.mat` and `status.mat`;
  * `fig/`: 3 819 PNGs in `M2D/<series>/bl/{val,diff_t}` and `SMB/03/<region>`.
  
  Note: the Matlab cache file names use the *hydrodynamic* time even when the analysis uses morphological time (`M2D_20210222074325` = morphological 2021-08-12). According to `M2D_tim.mat`, the 4 requested times come from sub-runs 0, 18, 48 and 177.
* **Rules**:
  * the test only reads from the P-drive;
  * `output.root` points to a local temporary folder;
  * `output.forbidden_roots` contains the project folder;
  * the input-snapshot check (above) runs as part of the test.

### 9.4 Synthetic SMT test case

The real case (`r209`: 195 sub-runs × 44 partitions, ≈ 8 GB per sub-run) is too large and too project-specific for everyday testing. A **synthetic SMT simulation** is therefore generated, input *and* output. It has the same structure and file formats as `r209`, but it is small, can be shared publicly, and its fields are analytic, so the expected results are known exactly.

**Generator.**
* `tools/synthetic_smt/` (committed) builds the case deterministically (fixed seed, versioned parameters).
* Default output: `data_local/synthetic_smt_01/`, git-ignored, for local experiments. Tests generate it into a temporary folder.
* A frozen copy of the generated case plus its **Matlab reference results** is committed under `tests/data/synthetic_smt_01/` (target < 10 MB). It is used to compare with Matlab `D3D_plot` in CI.
* Dependencies: `numpy`, `netCDF4`, `shapely`, `rasterio` (GeoTIFF), `scipy`/`h5py` (`.mat`). The VRT is written as XML text.

**River and grid.**
* A meandering channel about 10 km long, generated from a sine-generated curve, in projected coordinates (EPSG:28992-like range).
* A main channel (summer bed) of about 100 m with 20 m quadrilateral cells, and floodplains of about 300 m on each side with coarser cells. A few triangles at transitions test mixed face types.
* UGRID `mesh2d`, with face areas consistent with the geometry.
* **3 partitions** along the river, with ghost cells, `mesh2d_flowelem_domain` and `mesh2d_flowelem_globalnr` as written by D-Flow FM. One partition is a long strip along the whole floodplain, like partitions 12 and 31 in `r209`. This tests partition merging with dfm_tools.

**SMT layout** (as `r209`, reduced):

```
synthetic_smt_01/
├─ smt.yml                       minimal, same structure as r209
├─ sed/synthetic.sed
└─ output/
   ├─ 0/  dflowfm.mdu  mor.mor  dimr_config.xml
   │      results/dflowfm_000{0,1,2}_map.nc  dflowfm_000{0,1,2}.dia  dflowfm.dia
   ├─ 1/ …
   ├─ 2/ …
   └─ 3/ …                       4 sub-runs; TStart(k+1) = TStop(k); restart file names chained
```

* 3 map times per sub-run (start, after spin-up, end), with the **duplicate boundary time** between sub-runs.
* `MorFac = 10`, `MorStt = 86400 s`, and `morft` in the map files.
* Map time units `seconds since 2021-02-08 00:00:00 +01:00`, while the MDU keeps `RefDate = 20121231`. This reproduces the mismatch of §4.3.
* The partition `.dia` files end with `Computation finished`; the main `dflowfm.dia` does not (as `r209`).
* Variants generated with a flag:
  * *running*: an `output/work` folder exists and the last sub-run is truncated;
  * *crashed*: the last map time is before `TStop` and there is no finish line;
  * *non-partitioned*;
  * *gap in sub-run numbering*: must produce a validation error.

**Analytic fields** (the expected results follow in closed form):
* `mesh2d_mor_bl(x, y, t) = z₀ − i·s + A·sin(2π s/L_b)·cos(π n/B) + e·t_mor`. Here `s` and `n` are the along- and across-channel coordinates. The bar wavelength `L_b` divides the 250 m segment length, so the bars average out per segment, and the bed degrades linearly in morphological time.
* `mesh2d_flowelem_ba` is the exact cell area.
* `mesh2d_s1`, `mesh2d_waterdepth`, `mesh2d_ucmag` and `mesh2d_taus` are simple smooth functions.
* `mesh2d_lyrfrac` has 3 fractions, to test the fraction selection.
* Some cells are NaN, and one is Inf, to test the SMB exclusion rule.

Expected results stored with the case:
* SMB area-weighted mean, mean, count and sum per segment;
* the cells per segment;
* M2D values at given faces and times;
* differences in time;
* the merged time axis.

**Project inputs** (same formats as the Maas project):
* River-km files in the fixed format (`x, y, label, km`):
  * `rkm.csv` with `_MA` tags (Maas rules);
  * `rkm_generic.csv` with an unknown tag (generic river);
  * `rkm_rijn.csv` with Rijntakken tags (track required);
  * `rkm_1km.csv` / `rkm_3km.csv` for labels and extents.
* Region shapefiles:
  * `L1R1` (the summer bed);
  * `L1L3` and `R1R3` (left and right halves), for region groups;
  * one region wider than `half_width`, to trigger the coverage warning.
* SMB measurements:
  * `<region>_measured.mat` with the real structure (`data.bl.val_mean.{rkm, s, unit, tim_dnum, val}`, `source`) and **unsorted times**. There is also a v7.3 (HDF5) variant;
  * the expected NetCDF after conversion.
* 2D measurements:
  * two GeoTIFF tiles (one shifted by half a cell, to test resampling) and a `.vrt` that mosaics them;
  * a polygon shapefile ("vakken") with the fields `MEAN`, `COUNT`, `A`, `locatie`, `code_uniek`, some of them failing the coverage and location filters;
  * a catalogue CSV with time-zone offsets, one entry with `factor = 0.01` (cm → m) and times within and outside the tolerance.
* Land boundary as `.mat` (an N×2 NaN-separated array) and as `.ldb`.

**Scripts.**
* `main_plot_synthetic.m`: the Matlab `D3D_plot` input equivalent to the Maas case (M2D + SMB with measurements, region groups, windows along km). **You run it once** with Matlab to produce the Matlab reference (`mat/`, `fig/`), which is then frozen in `tests/data/`.
* `main_plot_synthetic.py` / `.yaml`: the same case for the Python tool. It is the first end-to-end example and the input of the main regression tests.

---

## 10. Upstream contributions (branches/PRs when needed)

| Repo | Contribution | Needed for | Priority |
|------|--------------|-----------|----------|
| HYDROLIB-core | `MorphologyModel` for `.mor` (at least `MorFac`, `MorStt`, `BedUpd`, output flags) | Morphological time axis, validation | **v1** |
| HYDROLIB-core | `SedimentModel` for `.sed` (fractions, `SedTyp`, `SedDia`) | Fraction names/labels, grain-size variables | v1 (fraction variables) |
| HYDROLIB-core | Confirm/extend `PolyFile` for `.ldb` land boundaries (Tekal format without names) | Map overlays | v1 |
| HYDROLIB-core | Typed access to `TStop` / `TStart` with units → `datetime` helpers (refdate is an `int`) | Status, time validation | v1 |
| dfm_tools | `open_smt_dataset(path)`: concatenate SMT sub-runs into one lazy `UgridDataset` with a monotonic time axis (and a sub-run index) | SMT support | **v1** |
| dfm_tools | Morphological time coordinate helper (from `morfac`/`morstt` or `mesh2d_morft` in map) | Time axis | **v1** |
| dfm_tools | Simulation status from `.dia` / output (finished, interrupted, running), runtime, version | Status file | v1 (can start locally, then move) |
| dfm_tools | Zonal statistics on UGRID: statistics of face values per polygon set, NaN/Inf-safe, area-weighted | SMB | v1 (can start locally, then move) |
| dfm_tools | River-km utilities: reading the river-km file, km ↔ xy along a branch, perpendicular cut lines and segment polygons (generic part of §2.5). The Rijntakken/Maas rules stay in `d3dplot` unless the maintainers want them | SMB | v1 (start locally, then propose) |
| HYDROLIB-core | Delft3D4 `.mdf` model | Future D3D4 support | later |

Approach: develop first behind an internal adapter in `d3dplot` (with tests). Open the upstream PR when the API is stable, then switch the adapter to the upstream function and pin the minimum version. Keep a `docs/upstream.md` tracking table with the status of each PR.

---

## 11. Roadmap

| Phase | Deliverable |
|-------|-------------|
| 0 | Repository skeleton, GPL-3.0 licence, tooling, CI (Win/Linux), docs site, **synthetic SMT generator** (§9.4) and Matlab reference run on it |
| 1 | Config models + JSON schema, simulation discovery (FM/DIMR/SMT), status, variable registry, label dictionaries (en/nl/es), write guard + read-only input checks, `info`/`variables`/`init`/`validate` |
| 2 | Core engine: grid file + per-time-step data files, fingerprints, planner, `serial` and `processes` executors |
| 3 | **`map2d`** end to end (comparisons, extents with visible-face subsets, figure reuse, colour-limit sets, tiles, land boundaries incl. `.mat`, movies), **raster/polygon measurements**, replotting, hooks |
| 4 | **`river_km_statistics`** end to end (river-km file, river providers, cut lines and segments, geometry files, statistics, views, region groups, derived quantities, time averages), **`.mat` → NetCDF profile measurements**, acceptance case 1 |
| 5 | SLURM stub (`--shard`, `--dry-run` scripts), agent skills, docs gallery, **v1.0** |
| 6+ | Full SLURM support, further analyses (HIS, PRF, M1D, HFM, GRD, …), more figure formats, Delft3D4 |

---

## 12. Remaining open questions

Resolved in v5:
* `do_clims_measurements`: the colour limits are taken from the measurement range;
* `d3dplot/` output sub-folder: agreed;
* raster resampling instead of the 0.5-cell shift: agreed;
* time zone of `.mat` datenums: `measurements.time_zone` if given, otherwise the model's;
* Matlab reference of the acceptance case: `r209/mat` and `r209/fig`.

Still open:
1. **Synthetic case, frozen copy**: OK to commit the generated synthetic case and its Matlab reference results to the repository (`tests/data/`, target < 10 MB, public)? The alternative is to keep them only in `data_local/` and regenerate them.
2. **Matlab reference run**: once the generator exists, can you run `main_plot_synthetic.m` with your Matlab `D3D_plot` to produce the reference results?