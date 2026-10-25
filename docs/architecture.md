# Delft3D_plot (Python): Architecture Proposal

Status: **draft v3 for discussion** · Scope: requirements and architecture only. Nothing is implemented yet.

Changes since v2:
* SMB geometry specified in detail, including the river-km file format (unchanged input) and the Rijn/Maas rules (§2.5)
* cache: **one file per time step**, one grid file per simulation, no grid reference in data files, geometry written to files (§4.5)
* fingerprints stored in each cache file instead of a central manifest (§4.6)
* parallel execution: how the Windows process pool works; SLURM defaults (§4.8)
* output written to the simulation folder by default, with an override (§4.9)
* efficient plotting of zoomed views: "draw once, save many" (§4.10)
* labels and translations through a separate dictionary, as `labels4all` (§4.11)
* figure formats: PNG only for now, extensible (§4.10)
* licence decided: **GPL-3.0** (§9.2)

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

### 1.2 Decisions taken

| # | Topic | Decision |
|---|-------|----------|
| D1 | Analyses in v1 | **M2D** (2D maps) and **SMB** (statistics per river-km polygon in a region such as the summer bed) |
| D2 | Model types in v1 | **D-Flow FM** (plain or through DIMR) and **SMT** (Simulation Management Tool, FM-based) |
| D3 | Cache format | **NetCDF**. One grid file per simulation, **one data file per time step** (§4.5) |
| D4 | Hosting and licence | Public GitHub repository [V-Chavarrias/Delft3D_plot](https://github.com/V-Chavarrias/Delft3D_plot), **GPL-3.0** |
| D5 | Upstream | Gaps are implemented as branches/PRs in HYDROLIB-core and dfm_tools when needed (§10) |
| D6 | Parallel execution | Linux: local process pool and SLURM job arrays (defaults in §4.8). Windows: local process pool on one machine only |
| D7 | Matlab JSON | Reading `D3D_plot` JSON is **not** required |
| D9 | SMB geometry | Matlab behaviour is kept: region (summer bed) polygon cut by lines perpendicular to the river axis. The river-km file format is **fixed input**. Rijn and Maas rules apply automatically when the file contains their branch tags; otherwise the river is generic (§2.5) |
| D10 | Output location | Default: inside each simulation folder. Can be overridden per simulation or with a common root (§4.9) |
| D11 | Figure formats | PNG only in v1. Architecture allows more formats later (§4.10) |
| D12 | Labels | Separate translation dictionary as in `labels4all`. Languages: English, Dutch, Spanish (§4.11) |
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
  
  Per figure, one image is made for every colour-limit set and every zoom window. Optional overlays: tiles, land boundary, fixed weirs, velocity vectors, river-km labels, measurements. Optional 3D view and movies.

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
  
  Optional overlays: measurements per region, structures. A diagnostic plot shows the cells in each polygon.

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

**Algorithm** (per region × km set; all distances in m, km values in km):
1. **Centres** `c_1..c_n`: the km values requested by the user (e.g. `145, 146, …, 175`). They do not need to be equidistant or to coincide with file points.
2. **Segment edges and lengths**: `e` = midpoints between centres, extrapolated at both ends (`cen2cor`). `Δs_i = e_{i+1} − e_i` (km), computed from the *original* centres.
3. **Bend cutoffs** (Rijntakken, currently IJssel only: 891.9–896.0, 902.5–903.0, 905.3–910.0 km):
   * centres inside a cutoff are removed;
   * edges are recomputed as `c_i − Δs_i/2` (and the last as `c_n + Δs_n/2`);
   * an edge falling inside a cutoff is shifted across it, keeping its distance to its centre (with a 0.1 km polygon-size correction).
   
   The cutoff table is stored as package data, not in the code.
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
│  ├─ analyses/
│  │  ├─ base.py                      Analysis protocol (§4.4)
│  │  ├─ map2d.py                     M2D
│  │  └─ river_km_statistics.py       SMB
│  ├─ comparisons.py                  value / differences; regridding
│  ├─ store/                          grid.py, data.py, fingerprint.py, atomic.py
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
  * Process tasks: (simulation × region × km set × variable × time step), computing all statistics at once.
  * Post-process tasks: derived quantities (slope, × width, region differences, time averages).
  * Plot tasks: one per (view × comparison × variable × …).

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
1. the `.dia` file contains `Computation finished` / `Simulation finished` → `finished`;
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

#### SLURM (Linux)

* **Job arrays with sharding.** Each array element runs the same command with `--shard $SLURM_ARRAY_TASK_ID/$SLURM_ARRAY_TASK_COUNT`. It computes the same task list and executes only its own share, balanced by estimated cost.
* Inside an element, the local process pool uses as many workers as tasks/CPUs allocated to it.
* There is no scheduler process and no communication between jobs.
* The stages are submitted as separate jobs chained with `--dependency=afterok`. *prepare* is a single job (shared items are created once). *process* and *plot* are arrays.
* Re-submitting skips finished files.

Defaults (from your cluster; all overridable in `execution.slurm` or on the CLI):

```yaml
execution:
  slurm:
    nodes: 1
    tasks_per_node: 4
    partition: 4vcpu
    time_limit: "12:00:00"
    array_size: 16                   # number of shards for process/plot
    setup: ["source ~/miniforge3/bin/activate d3dplot"]   # lines run before the command
```

Generated script (example for the *process* stage):

```bash
#!/bin/bash
#SBATCH --job-name=process
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=4
#SBATCH --partition=4vcpu
#SBATCH --time=12:00:00
#SBATCH --array=0-15
#SBATCH --output=logs/slurm/%x_%A_%a.out
source ~/miniforge3/bin/activate d3dplot
d3dplot process config.yaml --shard ${SLURM_ARRAY_TASK_ID}/${SLURM_ARRAY_TASK_COUNT} --workers ${SLURM_NTASKS_PER_NODE}
```

```bash
d3dplot slurm submit config.yaml            # writes slurm/{prepare,process,plot}.sh and submits the chain
d3dplot slurm submit config.yaml --dry-run  # only writes the scripts
```

Note: here one Python process per node uses the 4 allocated tasks as 4 worker processes. If your cluster's convention is `--ntasks=1 --cpus-per-task=4` instead, the tool reads either variable.

*(Later, optional)* `dask-jobqueue`: a scheduler process requests worker jobs from SLURM and sends them tasks over the network. Better load balancing, but more moving parts. It can be added behind the same executor interface if needed.

### 4.9 Output location and layout

**Default: inside each simulation folder** (as Matlab). It can be overridden:
* per simulation: `simulations[k].output_dir`;
* for all simulations: `output.root`, giving `<root>/<simulation label>/`. Useful when simulation folders are archived or read-only.

Validation checks that output folders are writable and that no two simulations share one.

```
<simulation folder or output_dir>/
├─ cache/        grid.nc, times.nc, simulation_status.json, geometry/, data/        (§4.5)
├─ figures/      <analysis>/<series>/<view>/<comparison>/<variable>/<name>.png
├─ tables/       simulation_status.csv, river_km_statistics/*.csv
├─ config/       run_<timestamp>.yaml (resolved), latest.yaml
└─ logs/         d3dplot_<timestamp>.log, slurm/
```

Figures and tables that combine simulations (`all_simulations`, differences with the reference) go under the **reference simulation**.

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

---
## 5. v1 analyses: configuration sketch

```python
from d3dplot import Project, Simulation
from d3dplot.analyses import Map2D, RiverKmStatistics
from d3dplot.config import Times, Vertical, Figure, RiverKm, Region, Execution

project = Project(
    simulations=[
        Simulation(path=r"p:\proj\runs\r002", label="reference"),
        Simulation(path=r"p:\proj\runs\r003", label="dredging"),     # FM or SMT, detected automatically
    ],
    reference_simulation="reference",
    language="en",
    execution=Execution(executor="processes", workers=8),
    analyses=[
        Map2D(
            variables=["bed_level", "velocity_magnitude"],
            times=Times.all(axis="morphological"),
            vertical=Vertical.depth_average(),
            comparisons=["value", "difference_time", "difference_simulation"],
            figure=Figure(
                color_limits={"bed_level": {"value": [(-6.0, 4.5)], "difference_time": [(-1, 1)]}},
                extents=[None, (180_000, 195_000, 425_000, 435_000)],   # None = full model
                background_tiles=True,
                land_boundaries=[r"p:\proj\gis\mkm-inner.ldb"],
            ),
        ),
        RiverKmStatistics(
            variables=["bed_level", "bed_shear_stress"],
            regions=[Region(name="summer_bed", polygon=r"p:\proj\gis\summerbed.shp")],
            river_km=RiverKm(
                file=r"p:\proj\gis\rkm_rijntakken.csv",   # fixed format: x, y, branch label, km
                sets={"1km": range(868, 952)},
                track="WA",                               # required for Rijntakken files, else optional
                half_width_m=1000,
            ),
            statistics=["mean", "mean_area_weighted", "std"],
            times=Times.all(axis="morphological"),
            views=["profile", "space_time", "time_series"],
            comparisons=["value", "difference_time", "difference_simulation"],
            time_series_locations_km=[880.5, 930.2],
        ),
    ],
)

if __name__ == "__main__":       # required for multiprocessing on Windows
    project.run()
```

The river (Rijntakken, Maas or generic) is detected from the branch tags in the river-km file (§2.5).

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
d3dplot slurm submit d3dplot.yaml               # Linux cluster (defaults in §4.8)
d3dplot status   d3dplot.yaml                   # simulation status table + cache/figure progress
```

### 6.4 Replotting

The Matlab JSON served provenance *and* replotting. The proposal keeps both, at three levels:

1. **Whole project**:
   * Every run writes the *resolved* configuration (all defaults filled in) to `config/run_<timestamp>.yaml` and `config/latest.yaml` (under the reference simulation).
   * `d3dplot plot <postprocess_dir>` (or `… config/latest.yaml`) replots from the cache without needing the original script.
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
| `only_adhoc`, `adhoc`, `function_handles` | `hooks` / plugin analyses | |
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
| `measurements`, `tol_time_measurements` | `measurements: {file, time_tolerance}` | |
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
| `fpath_ldb` | `land_boundaries` |
| `do_rkm_disp`, `fpath_rkm_disp`, `rkm_disp_color` | `river_km_labels: {file, color}` |
| `do_plot_along_rkm`, `fpath_rkm_plot_along`, `rkm_tol_x/y` | `extents_along_river_km: {file, window_x_m, window_y_m}` |
| `write_shp`, `save_format` | `export: ["shapefile", "netcdf"]` |

### 7.5 `river_km_statistics` (SMB)

| Matlab | New |
|--------|-----|
| `sb_pol` | `regions: [{name, polygon}]` |
| `sb_pol_diff` | `region_differences: [[region_a, region_b]]` |
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
| Integration | `map2d` and `river_km_statistics` end to end on small real FM runs (2D, 3D sigma/z, partitioned, morphodynamic) and a small **SMT** run (≥ 3 sub-runs) | dfm_tools sample data + tiny purpose-made models in `tests/data` (Git LFS) | Every commit |
| Regression: numbers | Cache contents compared with references (`xarray.testing.assert_allclose`) | Same | Every commit |
| Regression: images | `pytest-mpl` baselines | Same | Every commit (Linux baseline) |
| Cache semantics | Changing a parameter / re-running a sim / appending times rebuilds exactly the expected entries. `never` / `always` behave as specified | Same | Every commit |
| Parallel equivalence | `serial` == `processes` == `--shard i/N` (all shards), byte-identical caches. Concurrent shards do not corrupt each other | Same | Every commit |
| Replot | `replot` from `latest.yaml` and from figure metadata reproduces identical images | Same | Every commit |
| Figure reuse | "Draw once, save many" gives PNGs pixel-identical to `--fresh-figures` (§4.10) | Same | Every commit |
| Benchmarks | Process and plot throughput (incl. zoomed extents, fresh vs. reused figures) tracked over time | Medium model | Nightly |
| End-to-end | `examples/*` through the CLI | Same | Nightly |
| Acceptance | Real projects compared with Matlab `D3D_plot` output (`@pytest.mark.requireslocaldata`) | P-drive | Manual / scheduled |
| SLURM | Submit a tiny job array (self-hosted runner or manual) | Cluster | Release |

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
| 0 | Repository skeleton, GPL-3.0 licence, tooling, CI (Win/Linux), docs site, small FM + SMT test data |
| 1 | Config models + JSON schema, simulation discovery (FM/DIMR/SMT), status, variable registry, label dictionaries (en/nl/es), `info`/`variables`/`init`/`validate` |
| 2 | Core engine: grid file + per-time-step data files, fingerprints, planner, `serial` and `processes` executors |
| 3 | **`map2d`** end to end (comparisons, extents with visible-face subsets, figure reuse, colour-limit sets, tiles, land boundaries, movies), replotting |
| 4 | **`river_km_statistics`** end to end (river-km file, Rijntakken/Maas/generic, cut lines and segments, geometry files, statistics, views, derived quantities, time averages), Matlab comparison |
| 5 | SLURM executor (job arrays + dependencies, cluster defaults), agent skills, docs gallery, **v1.0** |
| 6+ | Further analyses (HIS, PRF, M1D, HFM, GRD, …), more figure formats, Delft3D4, optional dask executor |

---

## 12. Remaining open questions

Resolved in v3:
* river-km for SMB: Matlab rules kept, fixed file format, river detected from tags (§2.5);
* cache location: simulation folder by default (§4.9);
* figure formats: PNG (§4.10);
* languages: en, nl, es (§4.11);
* SLURM defaults (§4.8);
* licence: GPL-3.0.

Still open:
1. **Rijntakken track for a km set that spans several tracks**: Matlab uses one track per km set. Is that always enough, or are km sets that change track (e.g. Waal → IJssel) needed?
2. **Bend cutoffs**: the table only contains IJssel cutoffs. Are cutoffs for other branches (or for the Maas) known and needed?
3. **Matlab reference results for SMB**: can you provide one or two real cases (river-km file, summer-bed shapefile, simulation, Matlab output) to use as acceptance tests for §2.5?
4. **Environment activation on the cluster**: which command activates the Python environment in a SLURM job (conda/pixi/module)? It goes into the `setup` lines of the generated scripts.