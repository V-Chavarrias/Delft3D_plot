# Delft3D_plot (Python): Architecture Proposal

Status: **draft v2 for discussion** · Scope: requirements and architecture only. Nothing is implemented yet.

Changes since v1: the decisions on the open questions are included (§1.2), and the following are new or reworked:
* v1 scope: **M2D** and **SMB** on **D-Flow FM** and **SMT** (§2.4, §4.4, §5)
* split grid/data NetCDF cache (§4.5)
* reprocessing policy (§4.6)
* simulation status file (§4.7)
* parallel execution on Linux (SLURM) and Windows (§4.8)
* replotting (§6.4)
* naming convention for all options (§7)
* help for users writing input, including agent skills (§8)
* licence (§9.2)
* upstream contributions (§10)

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
| D3 | Cache format | **NetCDF**, with grid and data in separate files (§4.5) |
| D4 | Hosting | Public GitHub repository (personal account). Licence: see §9.2 |
| D5 | Upstream | Gaps are implemented as branches/PRs in HYDROLIB-core and dfm_tools when needed (§10) |
| D6 | Cluster | SLURM on Linux. Local process pool on Windows (§4.8) |
| D7 | Matlab JSON | Reading `D3D_plot` JSON is **not** required |
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
  Storage       │ store/   grid + data NetCDF cache, manifest (§4.5)  ───────────────┘
                                ▼
  Provenance    │ config snapshots, manifest, logs, tables/ (§4.9)                  │
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
│  ├─ variables/                      registry (names, aliases, resolvers), derived quantities, labels (i18n)
│  ├─ selection/                      time.py, vertical.py, fractions.py, spatial.py
│  ├─ geometry/                       river_km.py (axis, km polygons), regions.py (polygon membership)
│  ├─ analyses/
│  │  ├─ base.py                      Analysis protocol (§4.4)
│  │  ├─ map2d.py                     M2D
│  │  └─ river_km_statistics.py       SMB
│  ├─ comparisons.py                  value / differences; regridding
│  ├─ store/                          grid.py, data.py, manifest.py, locking.py
│  ├─ plotting/                       map.py, profile.py, space_time.py, time_series.py, decorations.py, movie.py
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
    grid_id: str                  # hash of the mesh topology
    runs: list[FMRun]             # 1 for FM/DIMR; N ordered sub-runs for SMT
    def times(self, axis: TimeAxis) -> pd.DatetimeIndex     # hydrodynamic or morphological
    def locate(self, t) -> tuple[int, int]                   # (sub-run, local index); Matlab `sim_idx`
    def open_map(self, run: int) -> xu.UgridDataset          # dfm_tools.open_partitioned_dataset
```

* **FM**: one folder with an `.mdu`, or a `dimr_config.xml` pointing to one. HYDROLIB-core gives the output paths and `refdate`. dfm_tools opens the map files.
* **SMT**: `smt.yml` at the root and `output/0`, `output/1`, … each holding one FM run, with `output/work` present while running. The tool presents the sub-runs as **one simulation** with a concatenated, monotonic time axis.
  * Validation checks that all sub-runs share the same grid (`grid_id`).
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
  * Process tasks: (simulation × variable × selection × time chunk).
  * Plot tasks: (simulation × variable × time × comparison × colour-limit set × extent).
* **`river_km_statistics`** (SMB)
  * Preparation task, per (grid × region × river-km set): the *polygon membership*, i.e. the cell → polygon index and the cell area. It is cached as geometry.
  * Process tasks: (simulation × region × km set × variable × time chunk), computing all statistics at once.
  * Post-process tasks: derived quantities (slope, × width, region differences, time averages).
  * Plot tasks: one per (view × comparison × variable × …).

### 4.5 Cache: split grid and data NetCDF files

**Assessment of the Matlab idea (grid in one file, data per variable in others): yes, it is a good idea, with some refinements.**

Advantages:
* A large mesh (often 10⁵–10⁶ cells, with node coordinates and connectivity) is stored **once per grid** instead of once per variable and time step. Data files shrink to the bare values (often 5–20× smaller), and writing them is fast.
* The grid is shared by all analyses, by all times, and by SMT sub-runs with the same mesh.
* Separate small files fit parallel execution: NetCDF/HDF5 does not support concurrent writers to one file, but many workers can write different files safely.

Risks and how they are handled:
* *Data files are not self-describing.* Each data file stores the attribute `grid_id` (hash of the topology) and a relative `grid_file` path. `store.open(...)` re-attaches the topology and returns a `xugrid.UgridDataset`, so users never see the split. A `d3dplot export` command writes standalone UGRID files when one is needed for QGIS or for sharing.
* *A grid change goes unnoticed.* The `grid_id` is checked when files are joined, and a mismatch is an error.
* *Many small files* (one per time step) hurt performance on network drives. Data files hold a **chunk of times** (configurable, default ~10 steps or ~50 MB) instead of a single step. An optional `d3dplot consolidate` merges finished chunks into one file per variable.

Layout (per simulation, under the postprocessing folder):

```
cache/
├─ grid/<grid_id>.nc                         UGRID mesh2d (+ face area, CRS)          written once
├─ geometry/<grid_id>/region_<name>.nc       cell membership per region polygon        (SMB)
├─ geometry/<grid_id>/river_km_<name>.nc     cell -> km-polygon index, segment length  (SMB)
├─ data/map2d/<variable>/<selection>/t<start>-<end>.nc     values (time, face)
├─ data/river_km_statistics/<region>/<km set>/<variable>/t<start>-<end>.nc  (time, polygon, statistic)
├─ times.nc                                  hydrodynamic + morphological time axis (§4.7)
└─ manifest.json                             entry -> fingerprint, created, source, version
```

Data encoding:
* `float32` by default (the model output is usually `float64`, but plotting does not need it). `float64` can be selected.
* zlib compression level 4.
* CF attributes (`long_name`, `units`, `standard_name`).
* Encoding is checked by regression tests.

SMB results are small (polygons × statistics), so the grid split is irrelevant there. The heavy, reusable part is the *geometry* cache (polygon membership), which is computed once per grid and region.

### 4.6 Reprocessing policy

**Assessment of the Matlab rule "do not reprocess what exists unless asked": the intent is right, because reprocessing is the expensive part and repeated runs must be cheap. File existence alone is not a safe criterion, though.** It silently reuses stale data after:
* a re-run of the simulation;
* a change of a processing option (for example depth-average limits that are not part of the file name);
* a code fix in the analysis.

That is why Matlab needed `overwrite` and debug flags such as `tim_just_load`.

Proposal: every cache entry has a **fingerprint** stored in `manifest.json`:

```
fingerprint = hash(
    analysis name + analysis version,             # bumped when the processing algorithm changes
    processing parameters only (normalised),      # e.g. variable, layer, depth-average limits; NOT colours
    source identity of the simulation run,        # run id = hash(MDU) + map 'date_created' attribute
    time values in the chunk                       # appending new time steps does not invalidate old chunks
)
```

Policy option `reprocess` (per analysis, overridable on the CLI):

| Value | Behaviour | Use |
|-------|-----------|-----|
| `if_changed` *(default)* | Reuse when the fingerprint matches; otherwise rebuild that entry only | Normal work |
| `never` | Reuse whatever exists, warn if the fingerprint differs (= Matlab default) | Archived simulations, slow disks, debugging |
| `always` | Rebuild everything requested | Suspected corrupt cache |

Notes:
* For a *running* simulation, chunks that already exist stay valid. New time steps are simply new chunks, so incremental postprocessing during a run works naturally.
* Figures have their own policy, `replot: if_changed | never | always`. A figure fingerprint covers its cache inputs and *all* styling options. Changing a colour limit therefore redraws only the affected figures.
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

The planner expands the configuration into a **task graph**:
* *prepare* tasks (grid, geometry, time axis), which are shared;
* *process* tasks (simulation × variable × selection × time chunk), which are independent;
* *plot* tasks (one per figure);
* *movie* tasks, which run after their frames exist.

Tasks whose output fingerprint already matches are skipped, so every run is **resumable**.

Executors:

| Executor | Where | How |
|----------|-------|-----|
| `serial` | anywhere | One process. Debugging and deterministic tests |
| `processes` *(default)* | Windows and Linux workstations | `concurrent.futures.ProcessPoolExecutor` with the `spawn` start method (the only one on Windows, the same behaviour on Linux). Workers default to `min(cores, memory budget / task estimate)`. Matplotlib uses the `Agg` backend in workers |
| `slurm` | Linux cluster | SLURM **job array** with sharding (below) |
| `dask` *(optional, later)* | Linux cluster or multi-machine | `dask.distributed` / `dask-jobqueue` |

**`dask-jobqueue` vs. job-array sharding** (your question):

* **Job-array sharding** (recommended for v1):
  * The tool submits *one* SLURM job array of *N* tasks (`sbatch --array=0-N-1`).
  * Each array task runs the same command with `--shard $SLURM_ARRAY_TASK_ID/$SLURM_ARRAY_TASK_COUNT`. It computes the full task list deterministically and executes only its own share (e.g. task *i* takes every *N*-th task, balanced by estimated cost).
  * There is no scheduler process and no network communication between jobs. Shards write disjoint cache files.
  * Simple, robust, and it survives node failures: re-run the array and finished entries are skipped. This is the clean replacement of Matlab's `order_anl` trick.
  * Dependencies are handled by stages: prepare → process → plot are submitted as **three arrays chained with `--dependency=afterok`**. Shared items (grid, geometry, time axis) are built in the prepare stage, so no locking is needed across nodes.
* **`dask-jobqueue`**:
  * A Python *scheduler* runs (e.g. on the login node or in a job) and asks SLURM for *worker jobs*. It then sends tasks to them dynamically over the network.
  * Better load balancing and in-memory data exchange between tasks. On the other hand:
    * the scheduler must stay alive for the whole run;
    * the network ports between nodes must be open;
    * the debugging surface is larger.
  * Worth it only for very large interactive analyses. It is kept as an optional later executor behind the same interface.

Usage:

```bash
d3dplot slurm submit config.yaml --array 32 --partition 4vcpu --time 04:00:00
#  -> writes and submits slurm/prepare.sh, slurm/process.sh (array 0-31), slurm/plot.sh (array 0-31)
#     linked by --dependency=afterok; logs go to logs/slurm/
```

**Windows**: there is no SLURM. The recommended solution:
1. **Local process pool** (default). One workstation with *n* cores. It works out of the box and is the common case.
2. **Several Windows machines** on a shared drive (e.g. `P:`): run the same `--shard i/N` command manually or via a small `.bat` generated by `d3dplot shard-script config.yaml --shards N`, one shard per machine. Run the prepare stage first. Sharding needs no scheduler, so it works the same on Windows and Linux.
3. *(Optional)* `dask distributed` (`dask scheduler` + `dask worker` on each machine) also runs on Windows. It is listed for completeness but not recommended for v1.

Robustness on all platforms:
* *Atomic writes*: write `*.nc.tmp`, then `os.replace`.
* *Per-entry lock files* (`filelock`) as a guard against accidental overlap (e.g. a user runs twice).
* Workers open their own NetCDF handles and never share them.
* *Memory guard*: the chunk size is derived from the available memory.

### 4.9 Output layout

```
<postprocess_dir>/            (default: the simulation folder; configurable per simulation)
├─ cache/                     grid/, geometry/, data/, times.nc, simulation_status.json, manifest.json
├─ figures/<analysis>/<series>/<view>/<comparison>/<variable>/…png
├─ tables/                    simulation_status.csv, river_km_statistics/*.csv exports
├─ config/                    run_<timestamp>.yaml (resolved), latest.yaml
└─ logs/                      d3dplot_<timestamp>.log, slurm/
```

Figures that combine simulations (`all_simulations`, differences with the reference) go under the **reference simulation**.

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
                background_tiles=True,
                land_boundaries=[r"p:\proj\gis\mkm-inner.ldb"],
            ),
        ),
        RiverKmStatistics(
            variables=["bed_level", "bed_shear_stress"],
            regions=[Region(name="summer_bed", polygon=r"p:\proj\gis\summerbed.shp")],
            river_km=RiverKm(file=r"p:\proj\gis\rkm.csv", sets={"1km": range(145, 176)}, half_width=1000),
            statistics=["mean", "mean_area_weighted", "std"],
            times=Times.all(axis="morphological"),
            views=["profile", "space_time", "time_series"],
            comparisons=["value", "difference_time", "difference_simulation"],
            time_series_locations_km=[150.5, 170.2],
        ),
    ],
)

if __name__ == "__main__":       # required for multiprocessing on Windows
    project.run()
```

Open point for SMB: the river-km polygon construction in Matlab uses Dutch-river specifics (branch/track codes, bend-cutoff corrections, see `branch_rijntakken`, `correct_for_bendcutoff`). Proposal for v1:
* a **generic** river-axis definition: a CSV with `x, y, km` and an optional `branch` column, and perpendicular segment edges at a `half_width`;
* the Dutch-specific rules as an optional *river-km provider* plugin (`rijntakken`, `maas`).

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
d3dplot slurm submit d3dplot.yaml --array 32    # Linux cluster
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
   * Every saved image carries metadata (embedded in PNG/PDF metadata, and in `figures/manifest.json`): the analysis, the plot-task parameters and the configuration hash.
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
| `fdir_postprocess`, `fdir_mat`, `fdir_fig` | `simulations[k].output_dir`, `output.cache_dir`, `output.figures_dir` | |
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
| `fig_print` (0–4) | `figure.formats: ["png", "pdf", "svg"]` | |
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
| `fpath_rkm` | `river_km.file` |
| `rkm`, `rkm_name` | `river_km.sets: {name: [km...]}` |
| `rkm_br`, `rkm_track` | `river_km.branches` / `river_km.track` (provider plugin) |
| *(parallel distance)* | `river_km.half_width_m` |
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

*(Not legal advice.)* Constraints:
* The tool imports **dfm_tools, which is GPL-3.0**. HYDROLIB-core is MIT.
* A tool that imports a GPL library and is distributed together with it is, in the FSF interpretation, a combined work that must be distributed under GPL-compatible terms.

Options:

| Licence | Pros | Cons |
|---------|------|------|
| **GPL-3.0-or-later** *(recommended)* | Same as dfm_tools, so there is no compatibility question. Guarantees that improvements stay open. Common in the Deltares Python ecosystem | Code cannot be absorbed into permissively licensed projects (e.g. HYDROLIB-core). This matters little, because upstream contributions are made directly in those repos under *their* licence |
| MIT / BSD-3 | Maximum reuse | Users of the combined tool are still bound by dfm_tools' GPL, so the extra freedom is mostly theoretical |
| LGPL-3.0 | Allows use as a library by proprietary code | The same GPL dependency limits the benefit, and it adds complexity |

Recommendation: **GPL-3.0-or-later**, with SPDX headers in every file and the `LICENSE` file at the root.

### 9.3 Test bench

| Layer | Content | Data | When |
|-------|---------|------|------|
| Unit | Config models and names, validation rules and hints, time selection/matching, vertical selection, variable registry, fingerprints and reprocess policy, status detection (synthetic `.dia` files), comparisons, river-km polygon geometry, statistics, sharding, SLURM script generation | Synthetic `xugrid` datasets and folders in fixtures | Every commit, Windows + Linux |
| Integration | `map2d` and `river_km_statistics` end to end on small real FM runs (2D, 3D sigma/z, partitioned, morphodynamic) and a small **SMT** run (≥ 3 sub-runs) | dfm_tools sample data + tiny purpose-made models in `tests/data` (Git LFS) | Every commit |
| Regression: numbers | Cache contents compared with references (`xarray.testing.assert_allclose`) | Same | Every commit |
| Regression: images | `pytest-mpl` baselines | Same | Every commit (Linux baseline) |
| Cache semantics | Changing a parameter / re-running a sim / appending times rebuilds exactly the expected entries. `never` / `always` behave as specified | Same | Every commit |
| Parallel equivalence | `serial` == `processes` == `--shard i/N` (all shards), byte-identical caches. Concurrent shards do not corrupt each other | Same | Every commit |
| Replot | `replot` from `latest.yaml` and from figure metadata reproduces identical images | Same | Every commit |
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
| dfm_tools | River-km utilities: km ↔ xy along an axis, perpendicular km polygons | SMB | v2 |
| HYDROLIB-core | Delft3D4 `.mdf` model | Future D3D4 support | later |

Approach: develop first behind an internal adapter in `d3dplot` (with tests). Open the upstream PR when the API is stable, then switch the adapter to the upstream function and pin the minimum version. Keep a `docs/upstream.md` tracking table with the status of each PR.

---

## 11. Roadmap

| Phase | Deliverable |
|-------|-------------|
| 0 | Repository skeleton, licence, tooling, CI (Win/Linux), docs site, small FM + SMT test data |
| 1 | Config models + JSON schema, simulation discovery (FM/DIMR/SMT), status, variable registry, `info`/`variables`/`init`/`validate` |
| 2 | Core engine: grid/data store, fingerprints, planner, `serial` and `processes` executors |
| 3 | **`map2d`** end to end (comparisons, extents, colour-limit sets, tiles, land boundaries, movies), replotting |
| 4 | **`river_km_statistics`** end to end (geometry cache, statistics, views, derived quantities, time averages) |
| 5 | SLURM executor (job arrays + dependencies), Windows shard scripts, agent skills, docs gallery, **v1.0** |
| 6+ | Further analyses (HIS, PRF, M1D, HFM, GRD, …), Delft3D4, optional dask executor |

---

## 12. Remaining open questions

1. **River-km definition for SMB**: is a generic axis (CSV `x, y, km[, branch]`) + `half_width` enough for v1, with the Rijntakken/Maas branch/track and bend-cutoff rules as an optional provider? Or are those rules required in v1?
2. **Cache location default**: inside each simulation folder (as Matlab) or a separate postprocessing root (useful when simulation folders are read-only/archived)? The proposal supports both. Which should be the default?
3. **Figure formats**: PNG only by default, or PNG + PDF/SVG?
4. **Languages**: are `en` and `nl` enough for labels in v1?
5. **SLURM specifics**: partition names, typical wall-time limits and memory per core on your cluster, to set sensible defaults in `d3dplot slurm submit`.
