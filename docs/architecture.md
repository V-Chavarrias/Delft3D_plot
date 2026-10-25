# Delft3D_plot (Python): Architecture and Implementation Plan

Status: **v6, plan for review.** Only the synthetic test-case generator is implemented ([tools/synthetic_smt](../tools/synthetic_smt/README.md)). This document is self-contained: a new working session (human or AI agent) should be able to continue the project from it alone. Read §0 first.

Document map:
* §0 Session handover
* §1 Goals and constraints
* §2 Decision log
* §3 Review of the plan (what changed and why)
* §4 Reference: Matlab `D3D_plot`
* §5 Reference: real data (`r209`)
* §6 Libraries and tools
* §7 Architecture
* §8 Specification of the v1 analyses
* §9 File formats
* §10 Configuration
* §11 Command line and user workflows
* §12 Validation catalogue
* §13 Quality and tests
* §14 Upstream contributions
* §15 Implementation plan
* §16 Risks
* §17 Open questions
* §18 Glossary
* Appendix A: Matlab → Python names
* Appendix B: history of this document

---

## 0. Session handover

### 0.1 What exists

| Item | Location | State |
|------|----------|-------|
| Repository | GitHub [V-Chavarrias/Delft3D_plot](https://github.com/V-Chavarrias/Delft3D_plot), local `c:\checkouts\Delft3D_plot` | `main`, conventional commits |
| Plan (this file) | `docs/architecture.md` | v6 |
| Licence | `LICENSE` | GPL-3.0 |
| Synthetic SMT test case generator | `tools/synthetic_smt/` | Implemented and verified (§13.3) |
| Generated synthetic case | `data_local/synthetic_smt_01/` | Git-ignored; regenerate with the generator |
| Python environment | `.venv/` (uv, Python 3.12) | Git-ignored. dfm_tools and HYDROLIB-core are installed editable from the local checkouts |
| Package `d3dplot` | — | **Not started** (Phase 1, §15) |
| Matlab reference results of the synthetic case | `data_local/synthetic_smt_01/simulations/r00*/mat`, `fig` | **Pending**: the user runs `scripts/main_plot_synthetic.m` |

Workspace (`Delft3D_plot.code-workspace`) with sibling checkouts used as reference and dependencies:

| Checkout | Path | Use |
|----------|------|-----|
| oet_matlab | `c:\checkouts\oet_matlab` | Matlab reference. `D3D_plot` is in `applications/vtools/D3D/plot/` |
| HYDROLIB-core | `c:\checkouts\HYDROLIB-core` | Model input parsing |
| dfm_tools | `c:\checkouts\dfm_tools` | Model output reading |

### 0.2 Rules that must never be broken

1. **Never modify, create or delete anything on the P-drive (`p:\`).** Reading is allowed, and real project data there may be inspected read-only. This applies to the agent working on the project *and* is a design rule of the tool (§7.17). The generator refuses to write to `p:\`.
2. Inputs of the tool are never modified (§7.17).
3. Do not commit project data. `data_local/` is git-ignored.
4. Every commit message ends with the trailer `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`.

### 0.3 Environment and practical notes

```powershell
# from c:\checkouts\Delft3D_plot (Windows PowerShell 5.1)
uv venv .venv --python 3.12
uv pip install --python .venv\Scripts\python.exe -e c:\checkouts\dfm_tools -e c:\checkouts\HYDROLIB-core rasterio scipy h5py shapely pyyaml
.venv\Scripts\python.exe tools\synthetic_smt\generate.py --force      # regenerate + self-check (~1 min, ~87 MB)
```

* **PowerShell 5.1**:
  * there is no `&&`; use `;` and `if ($?) {…}`;
  * native programs writing to stderr (git, uv, python) make PowerShell report errors even on success, so check the exit code or the output;
  * multi-line commit messages must be written to a UTF-8 file and committed with `git commit -F <file>`. Inline quotes break the arguments.
* Git warns "CRLF will be replaced by LF": expected (`.gitattributes` uses `eol=lf`).
* Opening one `r209` sub-run with dfm_tools takes about 75 s (§5.3). Do not do this in loops.

**Verified environment** (2026-10-25; everything in this document was tested with these versions). Minimum versions in `pyproject.toml` start from these and are relaxed only when CI proves it.

| Package | Version | Package | Version |
|---------|---------|---------|---------|
| Python | 3.12.7 | dfm_tools | 0.47.1 (checkout `2f1fc680`, 2026-10-24) |
| hydrolib-core | 1.5.0 (checkout `14cdeffe`, 2026-10-23) | xugrid | 0.15.3 |
| xarray | 2026.9.0 | netCDF4 | 1.7.5.1 |
| numpy | 2.5.4 | pandas | 3.1.0 |
| scipy | 1.18.1 | shapely | 2.2.0 |
| geopandas | 1.2.0 | pyogrio | 0.13.0 |
| rasterio | 1.5.2 | matplotlib | 3.11.2 |
| pydantic | 2.14.1 | h5py | 3.16.0 |
| oet_matlab (reference) | checkout `ea3ad2482`, 2026-10-14 | | |

APIs the design relies on (check them first when upgrading):
* `dfm_tools.open_partitioned_dataset(file_nc, remove_ghost=True, **xr_kwargs)`;
* `hydrolib.core.dflowfm.ini.parser.Parser.parse_as_dict(path)`;
* `hydrolib.core.dflowfm.polyfile.models.PolyFile(path).objects[i].points`;
* `hydrolib.core.dimr.models.DIMR(path).component[i].inputFile`;
* `xugrid.Ugrid2d.to_dataset()`;
* `shapely.contains_xy`, `shapely.touches`;
* `rasterio.open(...).read(window=...)`.

### 0.4 Where to continue

1. **Review this plan** with the user. Resolve §17 where possible.
2. Start **Phase 1** (§15.2): repository skeleton (`pyproject.toml`, `src/d3dplot`, CI, docs), then the work packages in order.
3. When the user has run the Matlab script on the synthetic case, add the Matlab comparison tests (WP-T3).

---

## 1. Goals and constraints

### 1.1 Goal

A script-based Python tool that postprocesses Delft3D results and makes figures, replacing the Matlab tool `D3D_plot` (OET). It is not a 1:1 translation: concepts are kept, internals and names are redesigned.

### 1.2 Requirements

| # | Requirement | Design consequence |
|---|-------------|--------------------|
| R1 | Script-based | Python script or YAML file + CLI |
| R2 | `D3D_plot` is the functional reference | §4 documents what is kept |
| R3 | Reuse HYDROLIB-core and dfm_tools as much as possible | §6. Gaps are contributed upstream (§14) |
| R4 | Professional project: docs, version control, standards | §13.1 |
| R5 | Extensive test bench | §13 |
| R6 | Linux and Windows | `pathlib`, `spawn` start method, CI on both, short paths (§7.18) |
| R7 | Parallel processing and plotting | §7.13 |
| R8 | Process first, plot later; replot from processed data | Two stages with a persistent cache (§7.10) |
| R9 | Validate all input before processing | §7.4, §12 |
| R10 | Never modify inputs | §7.17 |
| R11 | Plot measurements with model results | §7.15 |
| R12 | Robust to imperfect real output (corrupt partitions, mismatching metadata) | §5, §7.6, §12 |

### 1.3 Scope of version 1

* Model types: **D-Flow FM** (plain or DIMR) and **SMT** (FM-based).
* Analyses: **M2D** (`map2d`) and **SMB** (`river_km_statistics`), including measurements.
* Executors: serial and a local process pool. SLURM is a stub.
* Figures: PNG.
* Labels: English, Dutch, Spanish.

---

## 2. Decision log

Status: **A** = agreed with the user; **P** = proposed in this plan and awaiting review.

| ID | Decision | Rationale | Status |
|----|----------|-----------|--------|
| D1 | v1 analyses: M2D and SMB | User priority | A |
| D2 | v1 models: D-Flow FM and SMT | User priority | A |
| D3 | Cache in NetCDF; one grid file per simulation; **one data file per time step** | Simple, parallel-safe writes; resumable | A |
| D4 | Public GitHub repository, GPL-3.0 | dfm_tools is GPL-3.0 | A |
| D5 | Gaps are contributed upstream through branches/PRs | Reuse | A |
| D6 | Local process pool on Windows and Linux; SLURM stub in v1 | User | A |
| D7 | No reading of Matlab JSON configurations | User | A |
| D8 | Descriptive option names, not Matlab abbreviations (Appendix A) | Readability | A |
| D9 | SMB geometry as Matlab, with a fixed river-km file format; Rijn/Maas rules detected from the file tags | User | A |
| D10 | Output in a `d3dplot/` sub-folder of each simulation folder, with overrides | User | A |
| D11 | PNG only in v1, behind a writer registry | User | A |
| D12 | Labels from separate dictionaries (as `labels4all`), languages en/nl/es | User | A |
| D13 | Measurements: rasters (GeoTIFF/VRT) and polygon shapefiles for M2D; `.mat` **v5/v7** accepted for SMB and converted to NetCDF (v7.3/HDF5 later; V-MEA-006) | User; the real files are v5/v7 (read with scipy) | A |
| D14 | Inputs are never modified | User | A |
| D15 | Rivers, cutoffs, measurement formats and polygon filters are data or plugins | User: "the architecture should be general" | A |
| D16 | Colour limits can come from the measurement range | User | A |
| D17 | Misaligned measurement rasters are resampled, not shifted by half a cell | User | A |
| D18 | Measurement time zone: configured, otherwise the model's | User | A |
| D19 | Synthetic SMT case generated locally; no frozen copy committed | User | A |
| D20 | **Land boundaries are input as `.ldb` only**. Parsed results are cached | User | A |
| D21 | **Model output is read through a per-simulation merge plan**. dfm_tools/xugrid build the merged grid once (§7.6) | Measured: 75 s vs 1 s per `r209` sub-run (§5.3) | P |
| D22 | **MDU/`.mor`/`.sed` are read with HYDROLIB-core's tolerant INI parser** plus a typed adapter. The strict `FMModel` is not used | `FMModel` rejects the real `r209` MDU (§5.4) | P |
| D23 | **Corrupt or inconsistent partitions are detected**. `time`/`morft` come from a reference partition | `r209` sub-run 0, partition 0021 is corrupt (§5.2) | P |
| D24 | Times are stored internally as time-zone-aware UTC and shown in the model time zone. Cache file names use the hydrodynamic time | xarray drops the offset; morphological time is not unique (§7.8) | P |
| D25 | When several outputs share a morphological time, select the **last** one (configurable) | Morphological time repeats during spin-up (§7.8) | P |
| D26 | Cache values in **float64** by default (float32 optional) | Exact comparison with Matlab and analytic results; size is small | P (changes v2) |
| D27 | Process tasks per (simulation × sub-run), each writing one file per time step | Amortises opening a sub-run (§7.13) | P |
| D28 | Environment and packaging with **uv** + `pyproject.toml` (hatchling); pixi optional | Already used, fast, pip-compatible; GDAL via rasterio wheels | P (changes v2: pixi) |
| D29 | Draw-once plotting with matplotlib `PolyCollection` directly; xugrid only for grid objects and rasterising | Speed (§7.14) | P |
| D30 | Windows path length: keep paths short and warn above 240 characters | P-drive paths are long; Windows MAX_PATH is 260 | P |
| D31 | No dask in the processing path. Parallelism only at task level, with single-threaded libraries in workers | Avoids nested parallelism and memory blow-ups | P |
| D32 | Configuration: pydantic v2 models; YAML read with `yaml.safe_load`; CLI with typer | Typed, schema export | P |
| D33 | The CRS comes from the configuration when map files have EPSG 0 | `r209` has EPSG 0 | A (v5) |
| D34 | Validation includes a read-only **inspect** phase that builds the metadata cache. No value processing before a clean report; runtime-only checks are listed explicitly (§7.4) | "Validate first" needs metadata (independent review) | P |
| D35 | **Recursive fingerprints** over all result-affecting inputs and upstream artefacts; content hashes for small inputs, stat-based identity for model output (§7.10) | Stale-cache safety (independent review) | P |
| D36 | Merge plans are validated (partition identity, `globalnr`) and reused only for sub-runs with equal **topology signatures** (§7.6) | Safety for SMT (independent review) | P |
| D37 | Deterministic sharding with a blake2b digest of canonical task keys | Python `hash()` is randomised | P |
| D38 | Fixed UTC offsets only (no named time zones with DST); CF numeric UTC on disk | D-Flow FM uses offsets | P |
| D39 | SMB geometry: boundaries inclusive for regions, shared cut lines to the lower segment; invalid or overlapping segments are errors unless overridden | Determinism (independent review) | P |
| D40 | Every output path contains its subject (simulation, simulation_vs_reference, or all_simulations) | No two tasks write the same file | P |

## 3. Review of the plan (v6)

This round re-checked every earlier decision against real data (`r209`, read-only) and the libraries. Each change is reflected in the relevant section.

| # | Finding (verified) | Impact | Change |
|---|--------------------|--------|--------|
| F1 | `dfm_tools.open_partitioned_dataset` on one `r209` sub-run (44 partitions, 501 230 faces): **75 s to open + 10 s to load one field**. Direct reading of the same field with a precomputed merge plan: **1.4 s once + 1.0 s per field**. Values identical | With 195 sub-runs: hours instead of minutes | D21, §7.6 |
| F2 | HYDROLIB-core `FMModel(..., recurse=False)` **rejects the real MDU**: unknown keywords `morphopol`, `tstarttlfsmo`, `rstignorebl`, `nccompression`, … There is no switch to relax this. `hydrolib.core.dflowfm.ini.parser.Parser.parse_as_dict` reads the MDU, `.mor` and `.sed` in < 0.2 s | The strict model cannot be the main reader | D22, §7.5, upstream §14 |
| F3 | `r209` **sub-run 0, partition 0021**: map times 2–3 have `time` ≈ −5·10⁻¹⁷⁹, `morft` ≈ −5·10²²⁵ and bed levels ≈ ±10³⁰⁸. The other 43 partitions are correct. dfm_tools fails to merge the sub-run ("duplicate time values") | Real output can be corrupt. Matlab results were not affected (they used time 1 only) | D23, §7.6, checks V-OUT-*, synthetic variant `corrupt_partition` |
| F4 | xarray decodes `seconds since 2021-02-08 00:00:00 +01:00` to time-zone-naive UTC (`2021-02-07T23:00`) | Off-by-one-hour errors in names and matching | D24, §7.8 |
| F5 | Morphological time is constant during spin-up: synthetic case exactly; `r209` 0 → 11.5 s while the hydrodynamic time advances 86 400 s | Selecting by morphological time is ambiguous | D25, §7.8 |
| F6 | HYDROLIB-core `PolyFile` reads `.ldb`: about 7.7 s per 10⁶ points (`j21` land boundary: 2.5·10⁶ points ≈ 20 s); a NumPy reader takes 0.7 s | Acceptable once; cache the parsed result | D20, §7.16 |
| F7 | `r209` map files: EPSG 0; MDU `RefDate = 20121231` while the map time units say 2021-02-08; "Computation finished" only in partition `.dia` files; repeated boundary time between sub-runs | Already in v5 | §5 |
| F8 | float32 was proposed for the cache. A comparison with Matlab (double) and with analytic results needs float64; the cache is tiny compared with model output | — | D26 |
| F9 | One process task per time step would open a sub-run once per time step (costly even with a merge plan) | — | D27 |
| F10 | Deep output paths under long P-drive paths can exceed Windows' 260-character limit | — | D30 |
| F11 | Measurements: `.ldb` only for land boundaries (user) | — | D20 |
| F12 | **Independent review** (rubber-duck agent) of the v6 draft found 14 issues: validation needing metadata built later; output collisions between simulations; merge-plan identity assumptions; under-validated plan reuse across sub-runs; incomplete fingerprints; non-deterministic sharding; missing dimension/missing-value contract; conflicting duplicate-time rules; incomplete time encoding; non-deterministic geometry boundaries; undefined derived statistics; MAT v7.3 contradiction; static measurement artists; missing versions and gates | All fixed in this version | D34–D40, §7.4, §7.6–7.8, §7.10, §7.13–7.14, §4.5, §8.2, §0.3, §15.8 |

Earlier decisions re-confirmed:
* NetCDF cache with a separate grid;
* fingerprints inside files;
* the `d3dplot/` output sub-folder;
* the SLURM stub;
* agent skills on top of deterministic tooling;
* GPL-3.0;
* NetCDF for SMB measurements.

Alternatives considered and **not** chosen:

| Alternative | Why not |
|-------------|---------|
| Zarr cache | NetCDF agreed; readable from Matlab and QGIS. Zarr could be added behind the store interface later |
| dask for parallel processing | Task-level parallelism is simpler, predictable in memory, and works the same on Windows. dask arrays are not needed with per-time reads |
| datashader for large maps | An extra heavy dependency. xugrid's rasterisation plus matplotlib `imshow` covers the "raster" render mode |
| pixi environment | uv is already in use, faster to set up, and the package must also install with plain `pip`. A `pixi.toml` can be added for users who prefer it |
| click/argparse CLI | typer gives typed commands with little code, built on click |
| `FMModel` strict parsing | Fails on real MDUs (F2) |
| Reading partitions with `xu.open_mfdataset` each time | Too slow (F1) |

---

## 4. Reference: Matlab `D3D_plot`

Entry point: [D3D_plot.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/D3D_plot.m). Local copy: `c:\checkouts\oet_matlab\applications\vtools\D3D\plot\`.

### 4.1 Workflow

```
in_plot (struct or JSON)
  └─ gdm_read_input, create_mat_default_flags      copy global flags into every tag
  └─ for each simulation:
       simulation_paths -> D3D_simpath              detect D3D4/FM/SOBEK3/SMT, find files, cache simdef.mat
       gdm_create_mat                               per enabled tag: gdm_create_mat_<TAG>  -> mat/*.mat
  └─ gdm_plot (all simulations)                     per enabled tag: gdm_plot_<TAG>        <- mat/*.mat
  └─ gdm_adhoc                                      project-specific functions
  └─ gdm_save_json                                  configuration used (provenance, replot)
  └─ copy log to <reference sim>/log
```

Tags: `GRD`, `M2D`, `M1D`, `PRF`, `SMB`, `HIS`, `HFM`, `CRS`, `STO`, plus legacy tags.

### 4.2 Concepts kept

* **Comparison variants**: value; difference with the initial time; difference with the reference simulation; the combination of both; percentage difference; all simulations in one figure; all times in one figure; space–time; movies.
* **Selections**: variable (short names, [D3D_list_of_variables.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/fcn/D3D_list_of_variables.m)); fraction index; layer, depth average or elevation; times; flow vs. morphological time.
* **Cache**: one file per (tag, variable, time, …) ([mat_tmp_name.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/mat_tmp_name.m)), with the grid stored separately. **Matlab cache names use the hydrodynamic time**, e.g. `M2D_20210222074325_var_bl.mat` is morphological time 2021-08-12.
* **Simulation status** ([D3D_status.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/fcn/D3D_status.m)): not started / running / finished / interrupted, stored only when final.
* **Provenance**: the JSON of the configuration used.
* **Ad-hoc functions** applied to figures (`function_handles`).

### 4.3 Pain points addressed

1. Late, scattered validation.
2. An untyped configuration where typos are silently ignored.
3. A cache keyed only on file names, so stale data is reused.
4. Serial loops, with shuffling (`order_anl`) as the only way to run in parallel.
5. Processing mixed with plotting, and copy-pasted logic.
6. Cryptic, overlapping flags.
7. Every figure is rebuilt from scratch, which is slow for zoomed windows.
8. Measurements are re-read for every colour-limit set, window and simulation.

### 4.4 M2D in Matlab

[gdm_create_mat_M2D.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/gdm_create_mat_M2D.m), [gdm_plot_M2D.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/gdm_plot_M2D.m), [fig_M2D.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/fig_M2D.m)

* **Processing**: per time and variable, read the face values (with layer, fraction, depth-average or elevation selection) and store them. Optional exports: shapefile, NetCDF. Velocity vectors and grid polygons are cached too.
* **Plotting**, per variable, time and comparison: one image per colour-limit set (`clims` rows) × window (`xlims`/`ylims` rows, or windows along river km: `fpath_rkm_plot_along` ± `rkm_tol_x/y`).
* **Overlays**: tiles, land boundary, fixed weirs, velocity vectors, river-km labels.
* **Measurements**: a second panel (§4.6).

### 4.5 SMB in Matlab, including the geometry (must be preserved)

[gdm_create_mat_SMB.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/gdm_create_mat_SMB.m), [gdm_create_mat_SMB_postprocess.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/gdm_create_mat_SMB_postprocess.m), [gdm_plot_SMB.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/gdm_plot_SMB.m), [gdm_read_summerbed.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/gdm_read_summerbed.m), [gdm_load_rkm_polygons.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/gdm_load_rkm_polygons.m), [convert2rkm.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/rkm/convert2rkm.m), [perpendicular_polyline.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/polyline/perpendicular_polyline.m), [angle_polyline.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/polyline/angle_polyline.m), [cen2cor.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/general/cen2cor.m), [branch_str_num.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/rkm/branch_str_num.m), [which_river.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/rkm/which_river.m), [branch_rijntakken.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/rkm/branch_rijntakken.m), [branch_maas.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/rkm/branch_maas.m), [correct_for_bendcutoff.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/rkm/correct_for_bendcutoff.m)

**Concept.** A *region polygon* (typically the summer bed) selects the model cells that belong to the region. Lines perpendicular to the river axis, between consecutive river-km points, cut the region into segments. Statistics per segment are computed from the cells that are in the region **and** in the segment.

```
            L_i ─────────── L_i+1 ─────────── L_i+2        L/R: offset ±half_width
             │   segment i    │   segment i+1   │             perpendicular to the axis
   axis ── e_i ──── c_i ──── e_i+1 ─── c_i+1 ── e_i+2      c: requested km (centre)
             │  ░░region░░░░  │  ░░░░░░░░░░░░░  │           e: segment edge
            R_i ─────────── R_i+1 ─────────── R_i+2        cut line = segment L_k–R_k
```

**River-km file (input; format fixed).**
* Comma-delimited text, one header line, columns `x, y, branch label, km`:
  ```
  X,Y,MODELKM,KM
     175918.445300,   308038.484400,3.00_MA,  3.000000
  ```
* Branch tag = the text after the first `_` of column 3, compared case-insensitively. If there is no `_`, the whole string is the tag.
* Matlab writes a parsed `.mat` copy next to the CSV. **The Python tool caches in its own cache instead.**

**River detection (from the tags in the file; decided).** Matlab decided this from the track string instead.

| Tags in the file | River | Branch of a km |
|------------------|-------|----------------|
| any of `RH`/`Rhein`, `BR`, `PK`, `WA`/`WL`, `BO`, `NI`, `NR`, `LE`, `IJ` | Rijntakken | From the user's **track** and the km (table below); the track is required |
| `MA`, `BM` | Maas | `MA` if km < 230.5, else `BM` (if `BM` is present) |
| other / none | Generic | The single tag. If there are several tags, the user gives the branch |

| Rijntakken track | Branch by km |
|------------------|-------------|
| `WA` (`WL`, `BO`, `NI`) | > 867.00 `WA` (optionally `BO` > 952.85, `NI` > 960.15) · > 852.90 `BR` · else `Rhein` |
| `IJ` | > 878.05 `IJ` · > 867.05 `PK` · > 852.90 `BR` · else `Rhein` |
| `NR`, `LE` | > 929.35 `LE` · > 878.08 `NR` · > 867.05 `PK` · > 852.90 `BR` · else `Rhein` |
| `PK` | `PK` (error outside 867.05–878.05) |
| `BR`, `RH` | as given |

**Algorithm** (per region × km set; distances in m, km in km):
1. **Centres**: `c_1..c_n`, the requested km values; not necessarily equidistant.
2. **Edges and lengths**: `e = cen2cor(c)`, the midpoints with extrapolated ends (`e_1 = c_1 − Δ_1/2`, `e_{n+1} = c_n + Δ_{n−1}/2`). `Δs_i = e_{i+1} − e_i` (km), from the original centres.
3. **Bend cutoffs** (provider data; today IJssel 891.9–896.0, 902.5–903.0, 905.3–910.0):
   * centres inside a cutoff are removed;
   * edges are recomputed as `c_i − Δs_i/2` (last: `c_n + Δs_n/2`);
   * an edge `q` inside a cutoff interval `(a, b)` (tolerance `0.01` km at both ends: `a + 0.01 < q < b − 0.01`), with `c` the centre of its segment, is mapped as in `correct_for_bendcutoff.m`:
     * if `q > c`: `q' = b + (q − a) − 0.1`;
     * else: `q' = a + (q − b) + 0.1`.
     
     The constant `0.1` km is Matlab's `ds_pol`. The provider data holds it per river.
4. **Edge coordinates**: each edge has a branch (from the provider). Its x, y are interpolated linearly in km between the two bracketing points of **that branch** in the file. At a branch transition the two edges of a segment can lie on different branches; the segment polygon is still built from its two edge cut lines, as in Matlab.
5. **Direction**: `θ_k = atan2(y_{k+1} − y_{k−1}, x_{k+1} − x_{k−1})`. The ends copy the angle of their neighbour; with 2 edges, both use the direction between them.
6. **Cut lines**: `L_k = e_k + W·(cos(θ_k+π/2), sin(θ_k+π/2))`, `R_k = e_k − W·(…)`. `W` = `half_width_m` (Matlab default 1000).
7. **Segment polygons**: `P_i = [L_i, L_{i+1}, R_{i+1}, R_i]`.
8. **Region membership**: the face centre is inside the region polygon (shapefile; multi-part allowed). An optional concave hull builds one polygon from unordered parts (Matlab `boundary(…, 0.8)`, default off).
9. **Assignment**: a face belongs to segment `i` if its centre is in `P_i` **and** in the region. Faces whose value is NaN or ±Inf are excluded at processing time.
   * **Boundaries are deterministic.** Points exactly on a region boundary count as inside (Matlab `inpolygon` includes the boundary): `shapely.covers`-equivalent, implemented as `contains_xy | touches`. A point exactly on a shared cut line belongs to the **lower** segment index (Matlab would count it in both segments: a deliberate difference). Ties are resolved by index order, so results do not depend on the shapely version.
   * Multi-part regions are the union of their parts. Holes are respected.

**Statistics per segment** (Matlab definitions):

| Statistic | Definition |
|-----------|------------|
| `mean` | mean of the valid values |
| `std` | standard deviation with N−1 normalisation |
| `max`, `min` | maximum and minimum |
| `count` | number of valid faces |
| `sum` | sum of the valid values |
| `sum_per_length` | `sum / (Δs_i·1000)` |
| `mean_area_weighted` | `Σ(v·A)/ΣA`, with A the face area (`mesh2d_flowelem_ba`) |

**Post-processing**: streamwise slope (`detab_ds`, central difference of `mean` over the km centres); value × width (flow or morphological area per length); region differences; time averages.

**Plots**:
* views: profile along km per time; all simulations; all times; space–time; time series at chosen km; cumulative in time; stacked fractions;
* each view with the difference variants;
* measurements per region;
* region groups (several regions in one figure);
* diagnostic polygon-membership plot.

**New checks** (Matlab had none):
* every `P_i` is a valid, non-self-intersecting quadrilateral (cut lines can cross in sharp bends with a large `W`). **Error** V-SMB-010, unless `allow_invalid_segments: true`;
* no face is assigned to two segments (overlap). **Error** V-SMB-013, same override;
* coverage: region faces in the km range that are in no segment (`W` too small). Warning V-SMB-011 with the count;
* the requested km are within the branch range in the file;
* edge-to-axis distance within tolerance (`TolMinDist`).

*To confirm in the acceptance test:* that the Python face centre (`mesh2d_face_x/y`) equals Matlab's `Xcen/Ycen`.

### 4.6 Measurements in Matlab

**M2D**: [gdm_ini_2D_mea.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/gdm_ini_2D_mea.m), [gdm_load_2D_measurements.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/gdm_load_2D_measurements.m), [SHP_read_and_filter.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/general/SHP_read_and_filter.m), [VRT_bounding_boxes.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/general/VRT_bounding_boxes.m)
* **Catalogue** CSV: `time, file, factor`. Times are ISO 8601 with offset. Files: `.tif`, `.vrt` (bounding boxes from `ComplexSource`/`DstRect` and a `, `-separated `GeoTransform`), `.shp`. Matlab uses the paths as given; relative paths are not resolved.
* **Selection**: `|t_mea − t| < tol_time_measurements` (default 30 days) and bounding box overlapping the window ± `tol_x/y_measurements` (default 1000 m). All files for one image must have the same type.
* **Rasters**: windowed read × `factor`; mosaic in catalogue order (fill only empty cells); validity mask; 0.5-cell shift attempt when grids are not aligned.
* **Polygons**: the value field (`MEAN`) is set to NaN when:
  * `COUNT/area < 0.99`, with area field `oppervlak_` (Matlab default; it is `A` in the 2026 data);
  * the location code (`Locatie`; `locatie` in 2026) is not in −3…3. The code is parsed by [pol_str2double.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/morpho_setup/pol_str2double.m): `R` → +, `L` → −, followed by one digit.
* **Comparisons**:
  * `value`: measurements at *t*;
  * `difference_time`: *t* − *t₀*. Rasters are subtracted on the grid intersection; polygons by order, which requires the same count;
  * differences between simulations: no measurements.
* **Display**: a second panel to the right with the same colormap and limits; title = mean measurement time. `do_clims_measurements=1` takes the colour limits from the measurement range.

**SMB**: [gdm_load_measurements_match_time.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/gdm_load_measurements_match_time.m), [gdm_load_measurements.m](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/gdm_load_measurements.m)
* One `.mat` per region, with the structure:
  ```
  data.<variable>.<statistic>.{rkm[nx], s[nx] (optional), tim_dnum[nt] (Matlab datenum, unsorted), val[nx,nt], unit}
  data.<variable>.source{nt}
  ```
* Aliases:
  * variables: `bl` = `mesh2d_mor_bl` = `DPS`;
  * statistics: the model `val_mean_weighted` is compared with the measured `val_mean`.
* Matching:
  * profile views: the nearest time within `tol_time_measurements` (days);
  * time series: the nearest km within 1 km (hard-coded).
* Differences in time: *t* − *t₀*. None for simulation differences.
* Region differences use `measurements_diff`.
* Axis limits can include the measurements (`do_include_mea_xylims`).

---

## 5. Reference: real data (Maas, `r209`; read-only)

### 5.1 Locations

| Item | Path |
|------|------|
| Example script (acceptance case 1) | `p:\11211565-002-maas-mor-2026\E_Software_Scripts\co\57_plot_Lixhe_Roermond\main_plot_01.m` |
| Project paths function | `…\E_Software_Scripts\co\00_general\paths_project.m` |
| Simulation | `p:\11211565-002-maas-mor-2026\C_Work\01_Model_20m\maas-j21_6-v1_20m_km002_085_cc\computations_smt\r209\` |
| Matlab output of the example | `r209\mat\` (47 files), `r209\fig\` (3 819 PNGs) |
| River-km files | `…\C_Work\00_data\01_rkm\` (`rkm.csv` with `MA` tags, `rkm_1km_lr.csv`, `rkm_3km_lr.csv`, …) |
| Region shapefiles | `…\C_Work\00_data\02_sb\` (`L1R1.shp`, …) |
| SMB measurements | `…\C_Work\00_data\11_bl_DM_pol_measurements\Lixhe-Roermond-2021-2026\<region>_measured.mat` |
| 2D measurement catalogues | `…\C_Work\00_data\12_2D_measurements\2D_measurements_02.csv` |
| Land boundary | `…\C_Work\00_data\05_landuse\` (`j21_ldb.mat` is a Matlab cache of an `.ldb`) |

### 5.2 Structure and peculiarities of `r209`

* **SMT**: `smt.yml`; `output/0 … output/194` (195 sub-runs, no gaps). `output/work` is present only while running.
* **Sub-run folder** contents:
  * `dflowfm.mdu` plus partition MDUs `dflowfm_0000.mdu … dflowfm_0043.mdu`;
  * `mor.mor` (`MorFac = 10`, `MorStt = 86400 s`), `dimr_config.xml`, restart files;
  * `results/dflowfm_00pp_map.nc` (44 partitions, about 113 MB each, so about 8 GB per sub-run and about 1.6 TB in total);
  * `results/dflowfm_00pp.dia` and `results/dflowfm.dia`.
* **Map file**:
  * dimensions: `mesh2d_nFaces` ≈ 12 500 per partition (501 230 faces after merging), `time = 3`, `nSedTot = 12`, `nBedLayers = 7`;
  * 68 variables, among them `mesh2d_mor_bl`, `mesh2d_flowelem_ba`, `mesh2d_flowelem_domain` (0-based, equal to the partition number in the file name), `mesh2d_flowelem_globalnr`, `mesh2d_lyrfrac(time, face, layer, fraction)` and `morft`.
* **Time**:
  * `time` and `morft` both have units `seconds since 2021-02-08 00:00:00 +01:00`;
  * the MDU has `RefDate = 20121231`. The map files were updated afterwards (the script mentions `update_mat_to_newtime.m`);
  * global attribute `time_coverage_start = 2013-01-01…` (stale).
* **Map times per sub-run**: `TStart`, `TStart + spin-up` and `TStop`, with `TStart(k+1) = TStop(k)`. The boundary time therefore appears twice.
* **Morphological time**: constant during spin-up (+11.5 s), then +`MorFac`·Δt.
* **CRS**: `projected_coordinate_system` has EPSG 0 ("Unknown projected").
* **Status**: "Computation finished at:" appears about 10 lines before the end of the partition `.dia` files, never in `results/dflowfm.dia`.
* **Corrupt partition**: sub-run 0, partition 0021, map times 2–3 have garbage `time`/`morft`/values (F3). Sub-runs 1 and 177 are fine.
* **Matlab time cache** (`mat/M2D_tim.mat`): the 4 requested morphological times (2021-02-08, 2021-08-08, 2023-01-15, 2025-12-31, tolerance 10 days) matched 2021-02-08, 2021-08-12, 2023-01-16 and 2026-01-01, in sub-runs 0, 18, 48 and 177.

### 5.3 Measured costs (r209, one sub-run, from a Windows laptop over the network)

| Operation | Time |
|-----------|------|
| `dfm_tools.open_partitioned_dataset` (44 partitions) | 74.7 s |
| … then load `mesh2d_mor_bl` at one time | 9.6 s |
| Merge plan: read `mesh2d_flowelem_domain` of 44 partitions | 1.4 s (once per simulation) |
| Direct read of one variable at one time, 44 partitions, with the merge plan | 1.0 s |
| `Parser.parse_as_dict` of the MDU / `.mor` / `.sed` | 0.19 / 0.08 / 0.10 s |
| HYDROLIB-core `PolyFile` of 10⁶ points (synthetic `.ldb`) | 7.7 s |

### 5.4 HYDROLIB-core on the real MDU

`FMModel(path, recurse=False)` raises pydantic `ValidationError`s ("Unknown keywords are detected in section …") for `[sediment]` (`dredgefile`, `morcfl`, `dzbdtmax`, `morphopol`, `inmorphopol`), `[time]` (`tstarttlfsmo`), `[restart]` (`rstignorebl`), `[output]` (`wrimap_trachytopes`, `writedfminterpretedvalues`, `wrirst_bnd`, `nccompression`) and others. `UnknownKeywordErrorManager` (`hydrolib/core/dflowfm/ini/util.py`) has no option to downgrade these errors to warnings.

## 6. Libraries and tools

### 6.1 Reused domain libraries

| Need | Library and API | Notes |
|------|-----------------|-------|
| MDU, `.mor`, `.sed` (tolerant) | HYDROLIB-core `hydrolib.core.dflowfm.ini.parser.Parser.parse_as_dict` | D22. `.mor` values arrive as `"10   [-]   comment"`: take the first token |
| `dimr_config.xml` | HYDROLIB-core `hydrolib.core.dimr.models.DIMR` (`component[i].inputFile`, `.filepath`) | Verified on the synthetic case |
| `.ldb`, `.pli`, `.pol` | HYDROLIB-core `hydrolib.core.dflowfm.polyfile.models.PolyFile` | 7.7 s/10⁶ points; cached (§7.16) |
| Windows/Linux paths in model files | HYDROLIB-core `FilePathStyleConverter`, `PathStyle` | |
| Merged grid, ghost cells (once per simulation) | dfm_tools `open_partitioned_dataset` (xugrid `merge_partitions`) | Only to build the grid and verify the merge plan (D21) |
| Grid object, topology, plotting helpers, rasterising | xugrid `Ugrid2d`, `UgridDataset`, `ugrid.rasterize` | |
| Regridding between simulations | xugrid regridders (`CentroidLocatorRegridder`, `OverlapRegridder`) | Only when grids differ |
| Layers, depth average, z-coordinates (later, 3D) | dfm_tools `reconstruct_zw_zcc`, `get_Dataset_atdepths` | Not needed for the v1 cases (2D) but kept in the design |
| Coastlines (optional) | dfm_tools `plot_coastlines` | |

### 6.2 General libraries

| Purpose | Choice | Alternatives considered |
|---------|--------|-------------------------|
| Arrays, tables | numpy, pandas | — |
| NetCDF I/O | netCDF4 (direct, fast slices), xarray (writing the cache, user access) | h5netcdf: possible later |
| Geometry | shapely ≥ 2 (vectorised `contains_xy`), geopandas + pyogrio | — |
| Rasters, VRT | rasterio (GDAL wheels for Windows/Linux), rioxarray optional | GDAL Python bindings: hard to install on Windows |
| `.mat` input | scipy.io (v5/v7) in v1; h5py for v7.3 later | mat73: fewer users |
| Configuration | pydantic v2, pyyaml (`safe_load`), JSON schema export | ruamel.yaml: only if comments must be preserved |
| CLI | typer (+ rich for tables and progress) | click, argparse |
| Plotting | matplotlib (Agg); `PolyCollection` directly (D29) | xugrid `.ugrid.plot` per figure: too slow for draw-once |
| Background tiles | contextily (optional extra `tiles`) | Local tile folder: §17 |
| Movies | imageio + imageio-ffmpeg (optional extra `movies`) | — |
| Parallel | `concurrent.futures.ProcessPoolExecutor` (`spawn`) | dask, joblib (§3) |
| Logging | stdlib `logging` + `rich`; workers log through `QueueHandler` | loguru |
| Hashing | `hashlib.blake2b` of canonical JSON | — |

### 6.3 Development tools

| Purpose | Choice |
|---------|--------|
| Environment, lock | uv (`uv.lock`), Python 3.11–3.13 (dfm_tools ≥ 3.11) |
| Build | hatchling, `src/` layout |
| Lint/format | ruff (lint + format, isort rules) |
| Types | mypy (strict for `d3dplot`, lenient for tests) |
| Tests | pytest, pytest-cov, pytest-xdist, pytest-mpl (image baselines), hypothesis (geometry properties), pytest-benchmark |
| Hooks | pre-commit (ruff, mypy, end-of-file, trailing whitespace, check-yaml/toml) |
| Versioning | commitizen (conventional commits → semver + `CHANGELOG.md`) |
| Docs | MkDocs Material + mkdocstrings (as HYDROLIB-core) |
| CI | GitHub Actions: `{ubuntu-latest, windows-latest} × {3.11, 3.12, 3.13}` |

---

## 7. Architecture

### 7.1 Stages and layers

```
            ┌────────────────────────────────────────────────────────────────────┐
  user      │ Python script │ YAML + CLI │ agent skills (on top of CLI + schema)  │
            └──────────────┬─────────────────────────────────────────────────────┘
                           ▼
  config    │ pydantic models, defaults, JSON schema, resolved-config writer         │
                           ▼
  1 validate│ static → discovery → INSPECT → content checks → ValidationReport (§12)  │  seconds–minutes
            │   inspect = read-only scan of model output metadata; builds the        │
            │   metadata cache: grid, merge plan(s), topology signatures, times,      │
            │   status, parsed small inputs (writes only inside the cache)            │
  2 prepare │ geometry (regions, segments, extents), land boundaries, measurements    │  once per input
  3 process │ (simulation × sub-run) tasks → one cache file per time step             │  parallel
  4 plot    │ plot groups → PNGs (draw once, save many)                               │  parallel
  5 movie   │ frames → mp4 (optional)                                                 │
                           ▼
  store     │ grid.nc, times.nc, merge_plan.nc, geometry/, measurements/, data/       │
  output    │ figures/, tables/, config/, logs/                                       │
```

Stages run in order. Within a stage, tasks run in parallel. Every task output carries a fingerprint, so finished work is skipped (resumable runs).

### 7.2 Package layout

```
src/d3dplot/
├─ __init__.py              public API: Project, Simulation, analyses, load_config, __version__
├─ cli.py                   typer app (§11)
├─ config/
│  ├─ project.py            ProjectConfig, SimulationConfig, OutputConfig, ExecutionConfig, LabelsConfig
│  ├─ selections.py         Times, Vertical, Fractions
│  ├─ figure.py             FigureConfig (color_limits, extents, formats, render, …)
│  ├─ measurements.py       MapMeasurementsConfig, PolygonFilters, ProfileMeasurementsConfig
│  └─ io.py                 load/save YAML/JSON, resolved config, JSON schema export, --set overrides
├─ model_input/
│  ├─ ini.py                tolerant INI reading (HYDROLIB-core Parser) + value-token parsing
│  ├─ mdu.py                MduInfo (typed adapter: times, output, files, morphology)
│  ├─ mor.py, sed.py        MorphologyInfo, SedimentInfo (fractions, diameters)
│  └─ dimr.py               find the MDU from dimr_config.xml
├─ simulation/
│  ├─ discover.py           folder → FmSimulation | SmtSimulation; MDU selection rules
│  ├─ subrun.py             SubRun (folder, MduInfo, partitions, output files)
│  ├─ partitions.py         partition file discovery; MergePlan build/load/verify
│  ├─ reader.py             MapReader: read(variable, subrun, time_index, selection) → global face array
│  ├─ timeaxis.py           TimeAxis (merged SMT axis, duplicate handling, selection, matching)
│  ├─ status.py             SimulationStatus detection (.dia tails, work folder, last time vs TStop)
│  └─ quality.py            corruption/consistency checks of output (time/morft/values)
├─ variables/
│  ├─ registry.py           VariableSpec registry (names, aliases, NetCDF candidates, dims, units, label key)
│  ├─ derived.py            derived quantities (mean grain size, Froude, …)
│  └─ selection.py          fraction/layer/vertical reductions
├─ geometry/
│  ├─ river_km.py           RiverKmFile (fixed format), edges, cut lines, segment polygons (§4.5)
│  ├─ rivers/               providers: base.py, rijntakken.py, maas.py, generic.py + data/*.yaml
│  ├─ regions.py            region polygons, membership, concave hull
│  ├─ segments.py           cell → segment assignment + checks
│  ├─ extents.py            extents (explicit, along river km), visible-face subsets
│  └─ landboundary.py       .ldb reading (HYDROLIB-core) + cache
├─ measurements/
│  ├─ catalogue.py          CSV/YAML catalogue, bounding boxes (incl. VRT tiles)
│  ├─ raster.py             windowed reads, mosaic, resampling, differences
│  ├─ polygons.py           shapefile reading, filters (presets), id matching
│  ├─ profiles.py           river-km profile NetCDF (canonical format) read/write
│  ├─ mat_convert.py        Matlab .mat → profile NetCDF
│  └─ match.py              time/location matching
├─ analyses/
│  ├─ base.py               Analysis protocol, registry (entry points "d3dplot.analyses")
│  ├─ map2d/                config.py, process.py, plot.py
│  └─ river_km_statistics/  config.py, geometry.py, process.py, postprocess.py, plot.py
├─ comparisons.py           value/difference operators, regridding cache
├─ store/
│  ├─ layout.py             OutputLayout: all paths of a simulation's d3dplot/ folder
│  ├─ guard.py              write guard (§7.17)
│  ├─ atomic.py             atomic NetCDF/PNG/CSV writes
│  ├─ fingerprint.py        canonical JSON + blake2b; file attribute access
│  └─ cache.py              read/write grid, times, data files; open as xugrid
├─ plotting/
│  ├─ templates.py          MapTemplate, LineTemplate (draw once, update data)
│  ├─ map.py, profile.py, space_time.py, time_series.py
│  ├─ decorations.py        land boundaries, river-km labels, tiles, colour bars
│  ├─ writers.py            writer registry (PNG; metadata in text chunks)
│  └─ movie.py
├─ labels/
│  ├─ builder.py            label(variable, language, kind, scale, datum, fraction)
│  └─ data/variable_to_key.csv, translations.csv, units.csv
├─ execute/
│  ├─ tasks.py              Task dataclasses (prepare/process/plot/movie), fingerprints
│  ├─ planner.py            config → task graph; skip finished tasks; sharding
│  ├─ executors.py          SerialExecutor, ProcessExecutor (+ SlurmExecutor stub)
│  └─ worker.py             worker initialiser (env vars, logging queue)
├─ validate/
│  ├─ report.py             ValidationIssue, ValidationReport (text/JSON)
│  └─ checks/               one module per group (§12)
├─ hooks.py                 hook loading ("module:function"), source hashing
└─ logging.py               logging setup, run summary
```

### 7.3 Core objects

| Object | Responsibility | Key attributes / methods |
|--------|----------------|--------------------------|
| `ProjectConfig` | The validated user input | `simulations`, `reference_simulation`, `analyses`, `output`, `execution`, `labels`, `crs`, `time_zone` |
| `Simulation` | One simulation folder (FM or SMT) | `label`, `kind`, `subruns: list[SubRun]`, `layout: OutputLayout`, `status`, `time_axis`, `merge_plan`, `grid` |
| `SubRun` | One FM run (an SMT sub-run, or the only run) | `index`, `folder`, `mdu: MduInfo`, `map_files: list[Path]` (partition order), `partitioned` |
| `MduInfo` | Typed view of the MDU (D22) | `refdate`, `tunit`, `tstart`, `tstop`, `tzone`, `output_dir`, `map_file`, `net_file`, `mor_file`, `sed_file`, `raw: dict` |
| `MergePlan` | How to assemble global face arrays from partitions | `n_faces`, per partition: `owned_local: int[]`, `global_pos: int[]` |
| `TimeAxis` | Merged output times of a simulation | rows: `(subrun, local_index, time_utc, morph_time_utc)`; `select(spec, axis)`, `match(times, tolerance)` |
| `VariableSpec` | How to read or derive a quantity | `name`, `aliases`, `netcdf_candidates`, `location`, `extra_dims`, `units`, `label_key`, `derive` |
| `OutputLayout` | All output paths of a simulation | `root/cache/…`, `figures/…`, `tables/…`, `config/…`, `logs/…` |
| `Task` | A unit of work | `kind`, `key`, `inputs`, `outputs`, `fingerprint`, `cost_estimate` |
| `ValidationIssue` | One finding | `code` (§12), `level`, `location`, `message`, `hint` |

### 7.4 Run flow

1. **Load configuration**: script objects or YAML → `ProjectConfig` (pydantic; unknown keys are errors).
2. **Validate** (§12), collecting all issues, in four phases:
   1. **static**: the configuration itself;
   2. **discovery**: folders, MDUs, sub-runs, output file lists (metadata only);
   3. **inspect**: read-only scan of model-output metadata. It builds the *metadata cache*: `grid.nc`, merge plan(s), topology signatures (§7.6), `times.nc` with quality flags (§7.7), simulation status, and parsed small inputs (river-km files, `.ldb`, region polygons, measurement catalogues). This is the only part of validation that writes, and it writes only inside the cache (§7.17). Results are reused when their fingerprints match;
   4. **content**: variables present, times available within tolerance, corrupt outputs at requested times, river-km ranges, measurement coverage. These use the metadata cache.
   
   Any error stops the run, unless `--force-invalid` is used for debugging. **No value processing starts before a clean report.**
3. **Plan**: build the task graph; drop tasks whose outputs exist with matching fingerprints.
4. **Execute** the stages: prepare → process → plot → movie.
5. **Write provenance**: resolved configuration, run summary (counts, durations, skipped/failed tasks), status tables, logs.

**Runtime-only checks** (they need values, so they cannot run before processing): V-OUT-031 (implausible values), and SMB statistics on empty segments. They are reported as warnings in the run summary and never silently ignored.

### 7.5 Simulation discovery and model input

**Folder → kind**:

| Condition | Kind |
|-----------|------|
| `smt.yml` exists and `output/<integer>/` folders exist | SMT |
| `dimr_config.xml` with an FM component | FM (the MDU from `inputFile`, relative to `workingDir`) |
| exactly one `*.mdu` that is not a partition MDU (`*_\d{4}.mdu`) | FM |
| otherwise | error V-SIM-001 |

**SMT**:
* sub-runs are `output/<n>` sorted **numerically** (not alphabetically: `10` comes after `9`);
* the numbering must be contiguous (V-SIM-010);
* inside each sub-run the MDU is chosen with the FM rule.

Inside an SMT sub-run, relative paths in the MDU are resolved against the sub-run folder (e.g. `SedFile = ../../sed/…`).

**MDU adapter** (`MduInfo`), from `Parser.parse_as_dict`. Keys are case-insensitive; values keep the first token for numbers:

| Field | MDU key | Notes |
|-------|---------|-------|
| `refdate` | `[time] RefDate` | `yyyymmdd` |
| `tunit` | `[time] Tunit` | `S`, `M`, `H`, `D` |
| `tstart`, `tstop` | `[time] TStart`, `TStop` | in `tunit` from `refdate` |
| `tzone` | `[time] Tzone` | hours |
| `output_dir` | `[output] OutputDir` | default `DFM_OUTPUT_<stem>` |
| `map_file` | `[output] MapFile` | default `<stem>_map.nc`; partitions `<stem>_<pppp>_map.nc` |
| `net_file` | `[geometry] NetFile` | |
| `mor_file`, `sed_file` | `[sediment] MorFile`, `SedFile` | `.mor` → `MorFac`, `MorStt`; `.sed` → fractions (`Name`, `SedTyp`, `SedDia` or `SedMinDia`/`SedMaxDia`) |

**Output files**:
* partitioned: `<output_dir>/<stem>_<pppp>_map.nc` with consecutive `pppp`;
* otherwise: `<stem>_map.nc`.

The partition count must be equal in all sub-runs (V-OUT-003).

### 7.6 Reading model output: merge plan (D21, D23)

**Partition identity** (checked for every partition file during inspect, V-OUT-022):
* the partition number `p` comes from the file name (`<stem>_<pppp>_map.nc`), and the set of numbers must be `0 … N−1` without gaps;
* the owned faces of file `p` are those with `mesh2d_flowelem_domain == p`. This assumes D-Flow FM's convention (0-based domain number equal to the file number), which holds for `r209` and the synthetic case. **It is checked, not assumed**: every file must own at least one face, and the domain values found across all files must be exactly `0 … N−1`;
* `mesh2d_flowelem_globalnr` (when present) must be ≥ 1 (1-based) on owned faces, unique across all owned faces of all partitions, and cover `1 … n_faces` exactly.

**Build a merge plan** (inspect phase) on a reference sub-run: the first sub-run whose partitions pass the time/consistency checks. Sub-run 0 of `r209` would not.
1. Open the sub-run with `dfm_tools.open_partitioned_dataset(..., drop_variables=["morft"], decode_times=False)` to get the merged `Ugrid2d` with ghost cells removed.
2. For every partition `p`: `owned_local = flatnonzero(domain == p)`.
3. Global position of each owned face in the merged grid:
   * **primary**: by `globalnr`, matched against the merged dataset's `globalnr`;
   * **degraded mode** (no `globalnr`): one-to-one matching of face centres with a KD-tree (tolerance 1 mm). Ambiguous or missing matches are an error, not a guess. This mode is logged as a warning.
4. Verify that every merged face is covered exactly once (V-OUT-020).
5. Write `cache/grid.nc` and `cache/merge_plan.nc` (§9.3).

**Topology signatures** (one per partition per sub-run, computed for **every** sub-run during inspect): `blake2b` of `(mesh2d_nFaces, mesh2d_flowelem_domain, mesh2d_flowelem_globalnr, mesh2d_face_nodes)` of the partition file. These are small integer arrays (for `r209` about 1–3 s per sub-run). A merge plan is reused for a sub-run only when all its partition signatures equal those of the plan. If they differ, a new plan is built for that group of sub-runs; `merge_plan.nc` can hold several plans, each with the list of sub-runs it serves. If the merged grids differ between plans, the analyses treat them as different grids, and cross-time comparisons regrid as for different simulations (§7.12). This is rare; it is reported as V-OUT-021 (warning).

**Reading** (`MapReader.read`):
* for each partition, open with netCDF4 (`mode="r"`) and read the variable by **named dimensions**: the `VariableSpec` declares the expected dimensions (e.g. `time`, `mesh2d_nFaces`, `nBedLayers`, `nSedTot`). The reader maps them to positions in the file's actual dimension order, slices `time = t` and `face = owned_local`, and transposes to the canonical order. Unexpected dimensions are an error (V-VAR-004);
* **missing values**: `_FillValue`/`missing_value` (masked arrays), NaN and ±Inf all become NaN before any reduction;
* values are scattered into a preallocated global array at `global_pos`;
* non-partitioned output is read directly (plan = identity);
* handles are opened per task and never shared between processes.
**Time and corruption checks** (`quality.py`):
* `time` and `morft` are read from **every partition** of every sub-run during inspect. These are small reads; for `r209` about 8 600 file opens, a few minutes, cached in `times.nc`.
* The reference values are those of the majority of the partitions. A partition whose `time`/`morft` differ (beyond 1 s), or are not finite, or are outside `[TStart, TStop]`, is marked **corrupt for those map times** (V-OUT-030, warning).
* When a value is read for a time with a corrupt partition, the policy `on_corrupt_output` applies:
  * `error` *(default)*: the task fails with a clear message;
  * `mask`: the faces of that partition are set to NaN, with a warning;
  * `skip`: that time is dropped from the analysis.
* Plausibility of values: `|v| > 1e30` or non-finite in a partition that is not marked → V-OUT-031 (warning during processing; the face values are masked).

### 7.7 SMT time axis

* Per sub-run, the map times are the reference values of §7.6 (majority of the partitions).
* **Merged axis**: all `(subrun, local_index)` outputs, ordered by hydrodynamic time.
* **Boundary duplicates** (same hydrodynamic time within 1 s, from consecutive sub-runs, i.e. a restart): keep the entry of sub-run *k* (the end of the run that produced the state) and drop the one of *k+1*. Their `morft` must also agree within 1 s; otherwise V-OUT-032 (error). A value check (bed level of the reference partition) is on by default (`check_boundary_duplicates: true`, V-OUT-033, warning).
* `cache/times.nc`, per merged entry: `subrun`, `local_index`, `time`, `morph_time`, plus a list of corrupt partitions per entry.
* The axis is trusted from the cache when the simulation status is final (§7.11). Otherwise it is extended incrementally with new sub-runs.

### 7.8 Time handling (D24, D25)

**Representation**:
* **In memory**: time-zone-aware UTC (`pandas.Timestamp`, `tz="UTC"`; NumPy `datetime64[ns]` in UTC inside arrays).
* **On disk (cache, profile NetCDF)**: CF numeric time, `units = "seconds since 1970-01-01 00:00:00"` (UTC), `calendar = "proleptic_gregorian"`, float64. The original offset is kept in the attribute `source_utc_offset` (e.g. `"+01:00"`).
* **Time zones are fixed UTC offsets** (D-Flow FM `Tzone` and map units are offsets). Named zones with daylight saving are not supported in v1: configuration values must be offsets (`"+01:00"`), and validation rejects names (V-CFG-001).

**Model offset**: from the map time units, otherwise MDU `Tzone`, otherwise the configuration `time_zone`. A conflict is a warning (V-TIME-002). Each simulation has its own offset; comparisons work in UTC.

**User times** in the configuration: aware datetimes are used as given. Naive datetimes and dates are interpreted in the **reference simulation's** offset. YAML accepts ISO 8601 strings.

**Display** (titles, tables): in the offset of the simulation shown (reference simulation for multi-simulation figures), format `yyyy-mm-dd HH:MM`.

**File names** of cache data and figures: the **hydrodynamic** time in the simulation's offset, `%Y%m%dT%H%M%S`. It is unique per output; morphological time is not (F5). Both times are stored as attributes.

**Selecting times**, on the configured `time_axis` (`hydrodynamic` | `morphological`):
* `all`, `last`, an explicit list (+ `time_tolerance`), `{indices: […]}` or `{start, end, step}`.
* For each explicit time, the **candidates** are the outputs within `time_tolerance`. None → V-TIME-010 (error, listing the nearest available times).
* **Duplicates** (one rule for every case): candidates whose times on the selection axis differ by less than `duplicate_tolerance` from the nearest candidate form a duplicate group. Default: 60 s on the morphological axis, 1 s on the hydrodynamic axis. The 60 s default covers the spin-up drift observed in `r209` (11.5 s), while distinct morphological outputs are at least `MorFac × map interval` apart (days in practice). Within a group, `duplicate_morphological_times: "last"` *(default: the state after the spin-up of that sub-run)* or `"first"` picks the output by hydrodynamic order. Boundary duplicates are already removed by §7.7, so this rule only sees distinct outputs.
* The selected outputs are listed in the run summary with both times, so the choice is visible.

**Matching between simulations** (`time_matching`): `reference` (default; every simulation uses the reference simulation's selected times, matched within `time_tolerance` with the same duplicate rule) or `own`.

**Measurements**:
* catalogue times carry offsets;
* naive times (catalogues, Matlab datenums) are interpreted in `measurements.time_zone`, otherwise in the reference simulation's offset (D18). The same measurements serve all simulations through UTC;
* Matlab datenum → datetime: `datetime.fromordinal(int(d)) − 366 days + (d mod 1) days`.
### 7.9 Variables

**Registry entries** (data in `variables/registry.py`, documented automatically):

| Field | Example (`bed_level`) |
|-------|-----------------------|
| `name` | `bed_level` |
| `aliases` | `bl`, `mesh2d_mor_bl`, `DPS`, `etab` |
| `netcdf_candidates` | `["mesh2d_mor_bl", "mesh2d_flowelem_bl"]` (first present wins; time-independent fallback is broadcast) |
| `location` | `face` |
| `extra_dims` | — (e.g. `mesh2d_lyrfrac`: `nBedLayers`, `nSedTot`) |
| `units` / `label_key` | `m` / `bed_elevation` (unit type `Lref`) |

**v1 variables** (needed by the two analyses and the acceptance cases):
* `bed_level`;
* `water_level` (`mesh2d_s1`), `water_depth` (`mesh2d_waterdepth`), `velocity_magnitude` (`mesh2d_ucmag`);
* `bed_shear_stress` (`mesh2d_taus`);
* `cell_area` (`mesh2d_flowelem_ba`);
* `layer_fraction` (`mesh2d_lyrfrac`, with `fractions` and `layer` selection);
* `mean_grain_size_arithmetic` / `_geometric` (derived from `lyrfrac` and the `.sed` diameters).

Raw NetCDF names are always accepted.

**Selection order** for a variable: fraction(s) → bed layer / vertical selection → reduction (sum of fractions, depth average). This yields one value per face per time.

### 7.10 Cache store and reprocessing

**Output root of a simulation** (precise rule):
1. `simulations[k].output_dir`, if set → `<output_dir>/d3dplot/`;
2. else, if `output.root` is set → `<output.root>/<label>/d3dplot/`;
3. else → `<simulation folder>/d3dplot/`.

Two simulations may never resolve to the same root (V-CFG-003). Files that combine simulations, and project-level files (logs, resolved configuration, run summary, measurement cache), live under the **reference simulation's** root.

**Layout** of one root (D10):

```
d3dplot/
├─ .d3dplot-output                marker (tool version, created); required before writing or cleaning
├─ cache/
│  ├─ grid.nc                     merged UGRID mesh + face area + CRS
│  ├─ merge_plan.nc               §9.3
│  ├─ times.nc                    merged time axis + corrupt-partition flags (§7.7)
│  ├─ simulation_status.json      §7.11
│  ├─ inputs/                     parsed copies of inputs: river_km_<hash>.nc, ldb_<hash>.nc
│  ├─ geometry/                   region_<name>.nc, segments_<region>_<set>.nc, extent_<hash>.nc
│  ├─ measurements/               (reference simulation only) catalogue_<hash>.csv, profiles/, map2d/
│  └─ data/
│     ├─ map2d/<variable>/<selection>/<yyyymmddTHHMMSS>.nc
│     └─ river_km_statistics/<region>/<km set>/<variable>/<yyyymmddTHHMMSS>.nc
├─ figures/<analysis>/<series>/<view>/<comparison>/<subject>/<variable>/<file>.png
│                                 <subject> = <simulation label> for single-simulation figures,
│                                 <simulation>_vs_<reference> for simulation differences,
│                                 all_simulations for grouped figures
├─ tables/                        simulation_status.csv, river_km_statistics/*.csv
├─ config/                        run_<timestamp>.yaml (resolved), latest.yaml
└─ logs/                          d3dplot_<timestamp>.log
```

* `<selection>` is a short readable label (`all`, `layer-top`, `fraction-3`, `depth-average`). The full selection is in the file attributes.
* Data files contain only values (`face` or `segment × statistic`); `store.open()` re-attaches the grid.
* Encoding: **float64** (D26), zlib level 4, CF attributes. Optional `cache.dtype: float32`.

**Fingerprint** (global attribute `d3dplot_fingerprint`, plus a readable attribute `d3dplot_parameters` with the canonical JSON).

Fingerprints are **recursive**: an artefact's fingerprint includes the fingerprints of everything it was computed from.

```
fingerprint(artefact) = blake2b(canonical_json({
  "kind": artefact kind, "schema": cache schema version, "tool": d3dplot minor version,
  "algorithm": analysis/algorithm version,              # bumped when results change
  "parameters": all result-affecting parameters,        # variable spec (resolved), selection, region, km set,
                                                        # policies (on_corrupt_output, duplicate rule), dtype; NOT styling
  "inputs": [fingerprint of each input],                # files and upstream artefacts, see below
  "time": hydrodynamic time (data files only)
}))
```

Input fingerprints:

| Input | Identity used |
|-------|---------------|
| Model output files (large) | per file `(normalised path, size, mtime_ns)` of every partition of the sub-run, plus the MDU/`.mor`/`.sed` **content** hashes. Content hashing of map files is optional (`cache.hash_model_output: false`); with `true`, the first and last 1 MB plus the size are hashed |
| Small inputs (river-km, `.ldb`, shapefiles + sidecar files, catalogues, `.mat`, label CSVs) | content hash (blake2b of the bytes) |
| Upstream artefacts (grid, merge plan, times, geometry, measurement windows) | their own fingerprint |
| Hooks (figures only) | module name + source hash |

`date_created` attributes are not used: they are often missing or stale (`r209`).

**Reprocess policy** `reprocess` (per analysis; CLI `--reprocess`):

| Value | Behaviour |
|-------|-----------|
| `if_changed` *(default)* | Reuse a file if its fingerprint matches; otherwise rebuild it |
| `never` | Reuse any existing file; warn on a mismatch (Matlab behaviour) |
| `always` | Rebuild |

`replot` (`if_changed | never | always`) does the same for figures, using a fingerprint over the inputs and all styling, stored in the PNG text chunks.

The planner reads fingerprints of existing files once per folder at start-up (one attribute per file).

**Atomic writes**: write `*.tmp` in the same folder, then `os.replace`. There is no global index file, so parallel workers never write the same file.

### 7.11 Simulation status

* **Detection**, per sub-run (FM: the only one):
  1. read the **last 4 KB** of every partition `.dia`, or of the main `.dia` when not partitioned. If all contain `Computation finished` → `finished`;
  2. a controlled-exit message → `interrupted`;
  3. last map time < `TStop` and files unchanged for `status.stale_after` (default 1 h) → `stopped`;
  4. otherwise → `running`;
  5. no map files → `not_started`.
* **SMT**: `output/work` exists → `running`; otherwise the status of the last sub-run (and a check that all sub-runs are finished).
* **Storage**:
  * `cache/simulation_status.json` (machine-readable) is trusted on later runs only when final;
  * `tables/simulation_status.csv` (human-readable) is regenerated every run. Columns: `label, path, kind, state, n_subruns, n_partitions, last_output_time, last_morph_time, morfac, corrupt_outputs, checked_at, dflowfm_version`.
  
  The project summary goes to the reference simulation's `tables/`.
* **Use**: validation warnings, cache trust, reporting.

### 7.12 Comparisons

Applied in the plot stage, from cached values:

| Comparison | Formula | Measurements shown |
|------------|---------|--------------------|
| `value` | `v_s(t)` | measurement(t) |
| `difference_time` | `v_s(t) − v_s(t₀)` | measurement(t) − measurement(t₀) |
| `difference_simulation` | `v_s(t) − v_ref(t)` | none |
| `difference_simulation_time` | `[v_s(t) − v_s(t₀)] − [v_ref(t) − v_ref(t₀)]` | none |
| `difference_simulation_relative` | `100·(v_s − v_ref)/v_ref` | none |

* `t₀` is the first selected time.
* Differences between simulations on different grids: `v_s` is regridded to the reference grid (xugrid `CentroidLocatorRegridder`). The regridder is built once per grid pair and cached in `cache/geometry/regrid_<hash>.nc`.
* For SMB, segments are identical across simulations, so no regridding is needed.

### 7.13 Execution and parallelism

**Task kinds and granularity** (D27):

| Stage | Task | Unit | Writes |
|-------|------|------|--------|
| inspect | `GridTask` | simulation (per merge plan) | `grid.nc`, `merge_plan.nc` |
| inspect | `TimeAxisTask` | simulation (per sub-run, in parallel) | `times.nc` (+ quality flags, signatures) |
| inspect | `StatusTask` | simulation | status json/csv |
| inspect | `InputTask` | each river-km file, `.ldb`, region, catalogue, `.mat` | `cache/inputs/*`, `cache/measurements/profiles/*`, `catalogue_<hash>.csv` |
| prepare | `GeometryTask` | simulation × region × km set; simulation × extent | `cache/geometry/*` |
| prepare | `MeasurementWindowTask` | catalogue × extent × plot time (× difference) | `cache/measurements/map2d/*` |
| process | `ProcessTask` | simulation × **sub-run** × analysis (all requested variables and times of that sub-run) | one data file per (variable, selection, time) |
| plot | `PlotGroupTask` | subject (simulation, or simulation vs reference, or all) × variable × comparison × block of times (map2d); view × comparison × variable × region group (SMB) | PNGs |
| movie | `MovieTask` | sequence of frames | mp4 |

Every task key contains the subject (and the reference label for simulation differences), so no two tasks write the same file.

**Executors**:

| Executor | Notes |
|----------|-------|
| `serial` | Debugging, deterministic tests |
| `processes` *(default)* | `ProcessPoolExecutor(mp_context=spawn)`. Workers = physical cores − 1, capped by available memory / estimated task memory. Tasks are handed out one at a time (dynamic balancing), largest first |
| `slurm` *(stub in v1)* | Writes job scripts (`--dry-run`); submission later. Defaults: `nodes=1`, `tasks_per_node=4`, `partition=4vcpu`, `time_limit=12:00:00`, `job_name=<stage>` |

**Worker initialisation** (`worker.py`):
* `MPLBACKEND=Agg`;
* `OMP_NUM_THREADS=1` and `OPENBLAS_NUM_THREADS=1` (no nested parallelism, D31);
* `HDF5_USE_FILE_LOCKING=FALSE` (reading NetCDF4 files on network drives; read-only access);
* logging through a `QueueHandler` to the main process.

**Sharding**: `--shard i/N` executes only the tasks whose **stable digest** satisfies `int.from_bytes(blake2b(task.key.encode()).digest()[:8], "big") % N == i`. Python's `hash()` is randomised per process and must not be used. Task keys are canonical strings (e.g. `process|map2d|r001|subrun=3`), frozen by tests. This is the building block for SLURM arrays.

**Failure handling**: a failing task is logged with its traceback and does not stop other tasks. The run summary lists failures, and the exit code is non-zero if any task failed. A re-run retries only failed or missing outputs.

**Windows**: a script that starts a run must have `if __name__ == "__main__":`. The CLI handles this itself.

### 7.14 Plotting

**Figure templates** (draw once, save many; D29):
* **Map template** (per simulation × variable × comparison × extent):
  * built once with a `PolyCollection` of only the faces visible in the extent (indices from `cache/geometry/extent_<hash>.nc`);
  * static decorations: land boundaries, river-km labels, tiles, fixed weirs;
  * colour bar and labels;
  * the measurement panel if configured.
* **Loop**: for each time, `collection.set_array(values[visible])` and update the title; for each colour-limit set, `collection.set_clim(...)` → save.
* **Line template** (SMB profiles, time series): axes, structures/hooks and measurement artists are created once; only line data change.
* **Render modes** (`figure.render`):
  * `polygons`;
  * `raster`: values rasterised on the figure's pixel grid with xugrid, then `imshow`;
  * `auto` *(default)*: raster when faces per screen pixel exceed 1.
* **Robustness**:
  * only data, limits and titles change inside a group;
  * figures are closed at the end of every group;
  * after an error the group restarts with fresh figures;
  * `--fresh-figures` rebuilds every image, and a test asserts pixel-identical output.
* **Writers**: a registry by format. v1 has PNG only (`figure.formats: ["png"]`). Metadata goes into PNG text chunks: `d3dplot_version`, `config_hash`, `figure_fingerprint`, `task_key`.
* **Figure file names**: `<variable>_<comparison>_<yyyymmddTHHMMSS>_c<k>_e<k>.png`, inside the `<subject>` folder (§7.10). Short, to stay under the path-length limit (§7.18).
* **Measurement artists may change between times**: different raster grids or tiles, polygon sets, or source types. Each measurement artist has a **compatibility key**: type, grid transform and shape (rasters), or the hash of the polygon ids (polygons). Within a group, the artist is updated in place only when the key of the next time equals the current one; otherwise it is removed and rebuilt. Tests alternate grids and polygon sets.

### 7.15 Measurements

**Kinds**:

| Kind | Used by | Accepted input | Canonical form |
|------|---------|----------------|----------------|
| `raster` | map2d | `.tif`, `.vrt`, NetCDF CF raster `([time,] y, x)` | DataArray + validity mask |
| `polygons` | map2d | `.shp`, `.gpkg` with value field | GeoDataFrame (geometry, value, id) |
| `river_km_profiles` | SMB | **NetCDF** (recommended), `.mat` (converted) | Dataset `(statistic, time, river_km)` (§9.4) |
| `time_series` *(later)* | HIS | NetCDF, CSV | — |

**Map measurements**:
* **Catalogue**: the CSV `time, file, factor` (existing format) or YAML. Paths can be absolute or relative to the catalogue, with Windows↔Linux path mapping (§7.18). One catalogue per variable.
* **Inspect** (metadata only): parse the catalogue, then read the bounding box and CRS of every file and VRT tile → `catalogue_<hash>.csv`.
* **Prepare**: per (extent × plot time × value/difference): select entries (`|Δt| ≤ time_tolerance`, bounding box ∩ extent ± `spatial_margin_m`), read windows, × factor, mosaic (`mosaic: first` default, `last`, `mean`), resample misaligned tiles with nearest neighbour (D17). Then:
  * compute differences with *t₀* (rasters: *t₀* resampled to the *t* grid; polygons: match by `id_field`, then geometry, then order with a count check);
  * write `cache/measurements/map2d/<catalogue>/<extent>/<time>[_diff].nc|.gpkg`.
* **Polygon filters**: configurable rules, with preset `rws_vakken`:
  * value `MEAN`;
  * coverage `COUNT / area ≥ 0.99` (area field candidates `A`, `oppervlak_`);
  * location field candidates `locatie`, `Locatie`, accepted codes −3…3 (`L`→−, `R`→+);
  * id field `code_uniek`.
  
  Field names are case-insensitive.
* **Plot**:
  * layout `side_by_side` (model | measurements, same colormap, limits and colour bar), with the measurement title showing the mean measurement time;
  * `color_limits_from_measurements` takes automatic limits from the measurement range (D16);
  * no measurements in simulation differences.

**SMB measurements**:
* per region (and per region difference) a profile file. `.mat` input is converted in the prepare stage to `cache/measurements/profiles/<region>_<hash>.nc` (§9.4) and only the NetCDF is used afterwards;
* `d3dplot measurements convert` makes permanent copies and never overwrites;
* matching:
  * profile views: nearest time within `measurements_time_tolerance` (30 days);
  * time series: nearest km within `measurements_km_tolerance` (1 km);
  * statistic equivalences: `mean_area_weighted → [mean_area_weighted, mean]`; `mean → [mean, mean_area_weighted]`.

### 7.16 Land boundaries and other overlays

* **Land boundaries**: **`.ldb` only** (D20); Tekal blocks, also `.pli`/`.pol` as polylines.
  * read with HYDROLIB-core `PolyFile` in the prepare stage;
  * cached as `cache/inputs/ldb_<hash>.nc` (NaN-separated x, y arrays), keyed by the content hash of the `.ldb`;
  * clipped per extent when drawn.
* **River-km labels**: from a river-km file (fixed format). Points within the extent get a label with their km.
* **Extents along river km**: one extent per point of a river-km file, of size `2·window_x_m × 2·window_y_m` centred on the point (Matlab `rkm_tol_x/y`).
* **Background tiles**: contextily (optional) with a local cache folder (`tiles_cache_dir`). Tiles are fetched once per extent in the prepare stage. Offline use: see §17.
* **Fixed weirs** (later): HYDROLIB-core `PolyFile` on `.pliz`.

### 7.17 Inputs are read-only (D14)

1. **Single write path**: every write goes through `store.atomic` with the **write guard**. It refuses any target outside an output root that contains `.d3dplot-output`. The marker is created only in a new or empty folder.
2. Nothing is written next to inputs. Parsed copies of inputs live in `cache/inputs/`.
3. Inputs are opened read-only: netCDF4 `mode="r"`, rasterio `"r"`, `open(…, "rb")`.
4. `clean` only deletes inside marked roots and only files it recognises (a fingerprint attribute or a known name). It never follows links out of the root.
5. Converters never overwrite, and refuse targets that are inputs of the configuration.
6. `output.forbidden_roots`: validation fails if any output root is inside one of them.
7. Tests:
   * guard unit tests;
   * a full run with a before/after snapshot (size, mtime, hash) of every input file;
   * acceptance tests on the P-drive use `output.root` in a temporary folder.

### 7.18 Paths and platforms

* **Path mappings** (`paths.mappings`, default `{"p:\\": "/p/"}`, as Matlab `linuxify`): applied to paths read from configuration and catalogues when running on the other operating system. Matching is case-insensitive on Windows. On Linux the real case is resolved with HYDROLIB-core `resolve_casing` when needed.
* **Path length (Windows)**: validation computes the longest planned output path and warns above 240 characters (V-OUT-040). The hint is to use `output.root` or enable long paths. Output names are kept short (§7.14).
* **Line endings**: the tool writes `\n`, and text inputs are read in universal-newline mode.
* **Process start**: always `spawn`.

### 7.19 Logging, provenance and replotting

* **Logging**: console (rich, level INFO) + file `logs/d3dplot_<timestamp>.log` (DEBUG) under the reference simulation; worker records arrive through a queue. Every task logs its key, duration and outcome.
* **Provenance**:
  * `config/run_<timestamp>.yaml` and `config/latest.yaml`: the resolved configuration, with all defaults and the tool version;
  * the run summary at the end of the log and in `tables/run_<timestamp>.csv`;
  * fingerprints in every cache file;
  * metadata in every PNG.
* **Replot**:
  * `d3dplot plot <path to latest.yaml or a d3dplot/ folder>` plus `--set key=value` overrides;
  * `d3dplot replot <figure.png>` regenerates one figure from its metadata;
  * Python: `Project.from_yaml(...)`, `project.plot(...)`, `project.results(...)`.

### 7.20 Labels (as `labels4all`)

* Files in `labels/data/`:
  * `variable_to_key.csv` (`variable,key`);
  * `translations.csv` (`key,en,nl,es,unit_type`), including templates such as `template.difference` = `difference in {quantity}` / `verschil in {quantity}` / `diferencia de {quantity}`;
  * `units.csv` (`unit_type,scale,symbol`).
  
  They are seeded from the Matlab `labels4all_variable_to_key.csv` and `labels4all_translation_keys.csv` (`oet_matlab/applications/vtools/figure/`).
* API: `label(variable, language, kind, scale=1, datum="+NAP", fraction=None) → Label(text, quantity, unit)`.
* Rules:
  * UTF-8;
  * all keys in all three languages (unit test);
  * unknown variables fall back to the NetCDF `long_name`/`units` with a warning;
  * users can add `labels.extra_dictionaries`.

### 7.21 Hooks

* `hooks: ["package.module:function", …]` per analysis, called as `hook(figure, axes, context)` after drawing and before saving.
* `context`: analysis, variable, comparison, time, extent, simulations, labels, river-km provider.
* The hook source hash is part of the figure fingerprint.
* Generic hooks shipped with the tool: `d3dplot.hooks:legend_horizontal`.
* Project-specific hooks (e.g. `add_structures_rkm`) live in the user's own package.

### 7.22 Performance targets (acceptance case 1, Windows laptop, P-drive)

| Step | Target |
|------|--------|
| Validation | < 2 min (dominated by reading 195 MDUs and partition metadata) |
| Prepare (grid, merge plan, times of 195 × 44 partitions, geometry, measurements) | < 15 min, once |
| Process M2D + SMB, 4 times × 1 variable | < 5 min with 8 workers |
| Plot (M2D: about 30 extents × 2 comparisons × 4 times; SMB: as in the script) | < 10 min with 8 workers |
| Re-plot after a styling change | only affected figures |

## 8. Specification of the v1 analyses

### 8.1 `map2d` (Matlab M2D)

**Configuration** (main fields; full names in Appendix A):
* `variables`, `times` (+ `time_axis`, `time_tolerance`), `vertical`, `fractions`;
* `comparisons` (§7.12);
* `figure`: `color_limits`, `extents`, `extents_along_river_km`, `river_km_labels`, `land_boundaries`, `background_tiles`, `aspect`, `colormap`, `render`, `size_cm`, `dpi`;
* `measurements`, `movie`, `export`, `hooks`.

**Prepare**:
* extents → visible-face subsets (`extent_<hash>.nc`);
* land boundaries → cache;
* measurements → windows per (extent, time) (§7.15);
* tiles per extent (optional).

**Process** (`ProcessTask` per simulation × sub-run): for each requested time in that sub-run and each variable and selection, read the global face array (§7.6) and apply the selection (§7.9). Write `data/map2d/<variable>/<selection>/<time>.nc`.
* Variables: `values(face)`.
* Attributes: fingerprint, hydrodynamic and morphological time (ISO, with offset), sub-run, variable, units, selection.

**Plot** (`PlotGroupTask` per simulation × variable × comparison × block of times):
* for every extent: build the map template once;
* for every time: compute the comparison (§7.12) and update the data and title;
* for every colour-limit set: set the limits and save.

Images = times × extents × colour-limit sets. Differences between simulations are stored under the reference simulation's figures.

**Movies**: after all frames of a (simulation, variable, comparison, extent, colour-limit set) exist, `movie.duration_s` sets the frame rate.

**Edge cases**:
* A variable missing in some sub-runs → error V-VAR-002, listing the sub-runs.
* A requested time with a corrupt partition → `on_corrupt_output` (§7.6).
* An extent with no faces → warning, and the extent is skipped.

### 8.2 `river_km_statistics` (Matlab SMB)

**Configuration**:
* `variables`, `times`;
* `regions[]`: name, polygon, optional `measurements`, optional `concave_hull`;
* `region_groups`, `region_differences`;
* `river_km`: `file`, `sets` (`name → [km]`), `track`, `branches`, `half_width_m`;
* `statistics`, `views`, `comparisons`, `time_series_locations_km`;
* `multiply_by_width`, `time_averages`, `diagnostics`;
* `figure` (`x_limits`, `y_limits`), measurement tolerances, `hooks`.

**Inspect**: parse the river-km file → `cache/inputs/river_km_<hash>.nc`; detect the river provider from the tags (§4.5); read the region polygons.

**Prepare** (per simulation × region × km set):
1. Build edges, cut lines and segment polygons (§4.5). Run the geometry checks.
2. Compute region membership (boundary inclusive, §4.5) on face centres → `geometry/region_<name>.nc`.
3. Assign faces to segments (shared cut lines → lower index) → `geometry/segments_<region>_<set>.nc` (§9.6). Coverage and overlap checks; a diagnostic figure if requested.

**Process** (`ProcessTask` per simulation × sub-run): for each time, variable, region and km set, read the global face values and keep the faces with a segment index ≥ 0 and finite values. Compute **all** statistics per segment, vectorised with `numpy` `bincount`-style reductions on the segment index. Write `data/river_km_statistics/<region>/<set>/<variable>/<time>.nc`, holding `values(segment, statistic)` with coordinates `km_centre`, `km_edge_start/end`, `segment_length_m`.

**Post-process** (plot stage, from the cache). Definitions follow Matlab: `gdm_create_mat_SMB_postprocess.m`, `pp_sb_tim_ave_01.m`, `gdm_compute_cumulative.m`.

| Quantity | Definition | Edge rules |
|----------|------------|-----------|
| `bed_slope_streamwise` | `S_i = (v_{i+1} − v_{i−1}) / ((c_{i+1} − c_{i−1})·1000)` with `v` = segment `mean` and `c` the km centres | `S_1 = S_n = NaN`; NaN if a neighbour is NaN. Works for irregular km spacing |
| `multiply_by_width: flow` | `v_i · W_i`, with `W_i = Σ_{faces in segment} A / (Δs_i·1000)` (width from the face area `mesh2d_flowelem_ba`) | Faces with NaN/≤ 0 area are excluded from `W` |
| `multiply_by_width: morphological` | the same with the morphological area: face area only where the total sediment thickness > 0 (Matlab `ba_mor`) | needs the sediment thickness variable; error V-VAR-002 if absent |
| Region difference | `v_a,i − v_b,i` per segment and statistic (same km set required) | NaN if either is NaN |
| Time average over times `t_1…t_n` (n ≥ 2) | weights `w = diff(cen2cor(t))` (in seconds); `mean = Σ v_k w_k / Σ w_k`; `std = sqrt(Σ w_k (v_k − mean)² / Σ w_k)`; `max`, `min`; `dominant = (Σ w_k v_k^{5/3} / Σ w_k)^{3/5}` | Applied to each statistic series separately; a NaN at any time gives NaN (as Matlab: `sum` without `omitnan`); n < 2 → error |
| `cumulative` (view) | `C(t_1) = 0`, `C(t_j) = Σ_{k<j} v(t_k)·(t_{k+1} − t_k)` in value·s (left block sum) | NaN propagates |
| `difference_simulation_relative` | `100·(v_s − v_ref)/v_ref` | NaN where `v_ref = 0` |
| `mean_area_weighted` | `Σ v A / Σ A` over valid faces with `A > 0` | NaN if `Σ A = 0` |

Each definition gets a small numeric unit test with hand-computed values.

**Plot**: views × comparisons × groupings.

| View | x-axis | y-axis | Notes |
|------|--------|--------|-------|
| `profile` | km | value | one figure per time; measurements at the matched time |
| `profile_all_times` | km | value | one line per time |
| `space_time` | km | time | colour = value |
| `time_series` | time | value | at `time_series_locations_km` (nearest segment centre) |
| `cumulative` | km | cumulative value | |

* Groupings: `per_simulation`, `all_simulations`, and region groups (several regions in one figure).
* `x_limits` rows: one figure per row; line templates are reused.

**Edge cases**:
* Segments with no faces → NaN, with count 0.
* Measurements outside the km range → ignored.
* A region group with regions on different km sets → error V-SMB-012.

---

## 9. File formats

### 9.1 River-km file (input; fixed)

As §4.5. Parsed with `csv` (comma, header skipped, whitespace stripped). Columns: `x, y, label, km`, with `branch = label.split("_", 1)[1] if "_" in label else label`. Points of a branch are ordered by km; duplicate km within a branch is an error (V-RKM-003).

### 9.2 Measurement catalogue (input)

```
time, file, factor
2021-02-08T00:00:00+01:00, p:\…\2021_pre_floodwave.vrt , 1.0
```

* Header required; whitespace around fields is stripped.
* `time` in ISO 8601 with offset (or naive → `measurements.time_zone` / model time zone).
* `file`: absolute, or relative to the catalogue.
* `factor`: multiplies the values.

The YAML alternative has the same fields plus optional `type`, `crs`, `value_field`.

### 9.3 `grid.nc` and `merge_plan.nc` (cache)

* `grid.nc`: UGRID-1.0 `mesh2d` as written by xugrid (`Ugrid2d.to_dataset()`), plus `mesh2d_flowelem_ba` (face area), `mesh2d_face_x/y` and global attribute `crs` (from the configuration if EPSG 0).
* `merge_plan.nc`:
  * dimensions `plan`, `partition`, `owned_face`, `subrun`;
  * `plan_of_subrun(subrun)`;
  * per plan: `partition_offset(plan, partition)`, `partition_n_owned(plan, partition)`, `owned_local(plan, owned_face)` (local face index in the partition file, 0-based), `global_pos(plan, owned_face)`, `grid_file(plan)`;
  * `signature(subrun, partition)` (hex string, §7.6);
  * attributes `n_faces`, `reference_subrun`, `matching` (`globalnr` | `coordinates`).
  
  Most simulations have a single plan.

### 9.4 River-km profile measurements (canonical NetCDF; recommended input for new data)

```
dimensions:  statistic, time, river_km
coordinates: time(time)              CF numeric, "seconds since 1970-01-01 00:00:00" (UTC), proleptic_gregorian; attribute source_utc_offset
             river_km(river_km)      km
             s(river_km)             m, distance along the axis (optional)
             branch(river_km)        string (optional)
             statistic(statistic)    string: mean, mean_area_weighted, std, …
variables:   <canonical variable>(statistic, time, river_km)   units, long_name, vertical_datum (if level)
global:      Conventions "CF-1.8", d3dplot_kind "river_km_profiles", region, source_files, history
```

**`.mat` conversion**:
* read with scipy (v5/v7). v7.3 (HDF5) files are rejected in v1 with V-MEA-006 and a hint to save them with `save(…, '-v7')`;
* map statistics `val_<stat>` → SMB names (`val_mean` → `mean`, `val_mean_weighted` → `mean_area_weighted`, `val_std` → `std`, `val_max` → `max`, `val_min` → `min`, `val_num` → `count`, `val_sum` → `sum`, `val_sum_length` → `sum_per_length`);
* map variables through the registry aliases (`bl` → `bed_level`, `detab_ds` → `bed_slope_streamwise`);
* convert datenums (§7.8) and **sort the times**;
* parse the unit string `'<quantity> [<unit>]'` (e.g. `[m+NAP]` → units `m`, datum `NAP`);
* copy `source` → `source_files`;
* validate the shapes `val[n_location, n_time]`;
* if the axes differ between variables, write one dataset per variable.

### 9.5 Cache data file (map2d)

```
dimensions: face
variables:  values(face) float64, _FillValue NaN, units, long_name
attributes: d3dplot_fingerprint, d3dplot_parameters (JSON), d3dplot_version, d3dplot_cache_schema,
            time (ISO with offset), time_utc (CF numeric), morph_time (ISO with offset), subrun, local_index,
            variable, selection, grid_file "../../../../grid.nc" (relative), plan (merge plan id)
```

For SMB: `values(segment, statistic)`, plus `km_centre(segment)`, `km_start(segment)`, `km_end(segment)`, `segment_length_m(segment)`, `count(segment)`.

### 9.6 Geometry files (cache)

* `region_<name>.nc`: `inside(face)` int8; attributes: polygon source path and hash.
* `segments_<region>_<set>.nc`:
  * `segment(face)` int32, −1 = none;
  * `km_centre`, `km_start`, `km_end`, `branch` per segment;
  * `cut_line_x/y(edge, 2)` and `polygon_x/y(segment, 5)`;
  * check results as attributes (`n_uncovered`, `n_overlap`, `invalid_segments`).
* `extent_<hash>.nc`: `faces(n)` int32; attributes: the extent bounds and margin.

### 9.7 Simulation status (`simulation_status.json`)

```json
{"label": "r209", "kind": "smt", "state": "finished", "checked_at": "…",
 "subruns": [{"index": 0, "state": "finished", "last_time": "…", "corrupt": [{"partition": 21, "times": [1, 2]}]}],
 "n_partitions": 44, "morfac": 10.0, "tool_version": "…"}
```

---

## 10. Configuration

### 10.1 Structure

```
ProjectConfig
├─ simulations: [SimulationConfig{path, label, output_dir?}]
├─ reference_simulation: label | index            (default: first)
├─ crs: "EPSG:28992" | null                       (needed when map files have EPSG 0 and CRS-dependent inputs are used)
├─ time_zone: "+01:00" | null                     (default: from the map time units)
├─ language: en | nl | es
├─ series: "01"                                   (figure sub-folder)
├─ output: {root?, forbidden_roots[], dtype: float64}
├─ paths: {mappings: {"p:\\": "/p/"}}
├─ execution: {executor: serial|processes|slurm, workers?, shard?, slurm: {…}}
├─ labels: {vertical_datum: "+NAP", extra_dictionaries: []}
├─ reprocess / replot: if_changed | never | always
├─ on_corrupt_output: error | mask | skip
└─ analyses: [Map2DConfig | RiverKmStatisticsConfig | plugin configs]
```

* All models use `extra="forbid"`.
* Defaults live in the models; global options are inherited by analyses unless overridden.
* `d3dplot schema > d3dplot.schema.json` exports a JSON schema for editor autocompletion.

### 10.2 YAML example (Maas acceptance case, abbreviated)

```yaml
simulations:
  - {path: 'p:\11211565-002-maas-mor-2026\C_Work\01_Model_20m\maas-j21_6-v1_20m_km002_085_cc\computations_smt\r209', label: validation}
crs: EPSG:28992
language: en
series: "05"
output: {root: 'c:\temp\d3dplot_acceptance', forbidden_roots: ['p:\11211565-002-maas-mor-2026']}
execution: {executor: processes, workers: 8}
analyses:
  - analysis: map2d
    variables: [bed_level]
    times: {at: [2021-02-08, 2021-08-08, 2023-01-15, 2025-12-31], axis: morphological, tolerance: 10 days}
    comparisons: [value, difference_time]
    measurements:
      bed_level: {catalogue: 'p:\…\12_2D_measurements\2D_measurements_02.csv', time_tolerance: 60 days, color_limits_from_measurements: true}
    figure:
      color_limits: {bed_level: {difference_time: [null, [-1, 1]]}}
      extents_along_river_km: {file: 'p:\…\01_rkm\rkm_3km_lr.csv', window_x_m: 1500, window_y_m: 1500}
      river_km_labels: {file: 'p:\…\01_rkm\rkm_1km_lr.csv'}
      land_boundaries: ['p:\…\05_landuse\j21.ldb']
      aspect: equal
  - analysis: river_km_statistics
    variables: [bed_level]
    regions:
      - {name: L1R1, polygon: 'p:\…\02_sb\L1R1.shp', measurements: 'p:\…\L1R1_measured.mat'}
      # … L3R3, L1L3, R1R3, L2L3, R2R3
    region_groups: [[R2R3, L2L3], [R1R3, L1L3]]
    river_km: {file: 'p:\…\01_rkm\rkm.csv', sets: {250m: {start: 2, end: 85, step: 0.25}}}
    statistics: [mean_area_weighted]
    times: {at: [2021-02-08, 2021-08-08, 2023-01-15, 2025-12-31], axis: morphological, tolerance: 10 days}
    views: [profile, time_series]
    comparisons: [value, difference_time]
    time_series_locations_km: [15, 16, 17, 18, 23, 24, 25, 26, 27, 28, 29, 30, 35, 36, 37, 38, 39, 40, 41]
    measurements_time_tolerance: 60 days
    figure: {x_limits: [[2, 85], [2, 7], [16, 21], [30, 36], [15, 55]]}
    hooks: ["maas_project.plotting:add_structures_rkm", "d3dplot.hooks:legend_horizontal"]
```

The `.ldb` name is a placeholder: only `j21_ldb.mat` (a cache) was seen (§17).

### 10.3 Python example

The same as §10.2 with objects (`Project`, `Simulation`, `Map2D`, `RiverKmStatistics`, `Times`, `Figure`, `RiverKm`, `Region`, `RegionGroup`, `Measurements`, `Execution`, `Output`), ending with:

```python
if __name__ == "__main__":
    project.run()          # = validate() -> prepare() -> process() -> plot()
```

`Project.from_yaml(path)` and `project.to_yaml(path)` convert between both forms.

### 10.4 Overrides

`--set analyses[0].figure.dpi=300` (dotted path, list indices; values parsed as YAML). The same syntax is used by `replot`.

---

## 11. Command line and user workflows

| Command | Purpose | Key options |
|---------|---------|-------------|
| `d3dplot info <sim>` | Kind, sub-runs, partitions, status, times, variables, CRS | `--json` |
| `d3dplot variables <sim>` | Available variables (canonical, aliases, raw) | |
| `d3dplot init <sim>… -o cfg.yaml` | Commented starter configuration | `--analyses map2d,river_km_statistics` |
| `d3dplot schema` | JSON schema of the configuration | |
| `d3dplot validate cfg.yaml` | All checks (§12); runs inspect (writes only the metadata cache) | `--format text|json` |
| `d3dplot inspect cfg.yaml` | Only build or refresh the metadata cache (grid, merge plans, times, status, parsed inputs) | `--simulation` |
| `d3dplot prepare|process|plot|run cfg.yaml` | Stages (`run` = all) | `--only <analysis>`, `--variable`, `--time`, `--simulation`, `--workers`, `--executor`, `--shard i/N`, `--reprocess`, `--replot`, `--fresh-figures`, `--set` |
| `d3dplot replot <figure.png>` | Regenerate one figure | `--set` |
| `d3dplot status cfg.yaml` | Status table + cache/figure progress | |
| `d3dplot clean cfg.yaml` | Remove stale or all tool output (inside marked roots) | `--stale`, `--figures`, `--cache` |
| `d3dplot measurements convert in.mat -o out.nc` | Permanent conversion | |
| `d3dplot slurm write cfg.yaml` | Write job scripts (stub) | |

**Agent skills** (`skills/`, v1.0):
* `d3dplot-create-input`, `d3dplot-replot`, `d3dplot-troubleshoot`;
* `d3dplot-add-analysis` (developers).

They instruct agents to use `info`, `variables`, `init`, `validate` and the schema rather than guess.

---

## 12. Validation catalogue

Codes are stable, documented, and tested one by one. **E** = error, **W** = warning.

| Code | Level | Check |
|------|-------|-------|
| V-CFG-001 | E | Unknown option, wrong type or value (pydantic) |
| V-CFG-002 | E | `reference_simulation` not found |
| V-CFG-003 | E | Duplicate simulation labels or output folders |
| V-CFG-004 | E | Lists of different length where they must match |
| V-SIM-001 | E | Folder missing, or no model found |
| V-SIM-002 | E | Several candidate MDUs |
| V-SIM-010 | E | SMT sub-run numbering has gaps |
| V-SIM-011 | W | SMT running (`output/work`) |
| V-MDU-001 | E | MDU, `.mor` or `.sed` cannot be parsed |
| V-MDU-002 | W | MDU `RefDate` differs from the map time units |
| V-OUT-001 | E | Map files missing |
| V-OUT-002 | E | Map file unreadable (not NetCDF, truncated) |
| V-OUT-003 | E | Partition count differs between sub-runs |
| V-OUT-020 | E | Merge plan does not cover each face exactly once, or coordinate matching is ambiguous |
| V-OUT-021 | W | Topology signatures differ between sub-runs (several merge plans needed) |
| V-OUT-022 | E | Partition identity: file numbers not `0…N−1`, domain values inconsistent with file numbers, `globalnr` not 1-based/unique/complete |
| V-OUT-030 | W | Corrupt or inconsistent `time`/`morft` in a partition (lists sub-run, partition, times) |
| V-OUT-031 | W | Implausible values (> 1e30 or non-finite) in a partition (runtime) |
| V-OUT-032 | E | Boundary duplicate between sub-runs with different `morft` |
| V-OUT-033 | W | Boundary duplicate between sub-runs with different values |
| V-OUT-040 | W | Output path longer than 240 characters (Windows) |
| V-OUT-041 | E | Output folder not writable, or inside `forbidden_roots` |
| V-TIME-002 | W | Time zone conflict between map units, MDU `Tzone` and configuration |
| V-TIME-010 | E | Requested time not available within the tolerance (lists the nearest) |
| V-TIME-011 | W | Requested time hits a corrupt output (`on_corrupt_output` decides) |
| V-VAR-001 | E | Unknown variable (with "did you mean") |
| V-VAR-002 | E | Variable missing in some sub-runs |
| V-VAR-003 | E | Fraction or layer index out of range |
| V-VAR-004 | E | Variable has dimensions other than those declared in its `VariableSpec` |
| V-RKM-001 | E | River-km file unreadable or wrong format |
| V-RKM-002 | E | Rijntakken tags without a `track`; or several generic branches without `branches` |
| V-RKM-003 | E | Duplicate km in a branch |
| V-RKM-004 | E | Requested km outside the branch range |
| V-SMB-001 | E | Region polygon unreadable or empty |
| V-SMB-010 | E | Invalid or self-intersecting segment polygon (override: `allow_invalid_segments`) |
| V-SMB-011 | W | Region faces not covered by any segment (count; `half_width_m` too small) |
| V-SMB-012 | E | Region group mixes km sets |
| V-SMB-013 | E | A face is assigned to two segments (override: `allow_invalid_segments`) |
| V-MEA-001 | E | Measurement file or catalogue missing or unreadable |
| V-MEA-002 | E | Catalogue columns wrong; mixed file types for one time |
| V-MEA-003 | E | CRS of measurements differs from the model CRS (and no reprojection allowed) |
| V-MEA-004 | W | No measurement within the tolerance for a plot time (lists the nearest) |
| V-MEA-005 | E | Variable or statistic not in the profile file (with aliases/equivalences) |
| V-MEA-006 | E | `.mat` is v7.3 (HDF5), not supported in v1 |
| V-LDB-001 | E | `.ldb` unreadable (or a non-`.ldb` land boundary given) |
| V-ENV-001 | E | Optional dependency missing for a requested feature (tiles, movies) |
| V-HOOK-001 | E | Hook cannot be imported |

## 13. Quality and tests

### 13.1 Engineering standards

* `pyproject.toml` (hatchling, `src/` layout), uv lock file, Python 3.11–3.13.
* ruff (lint + format) and mypy (strict for `src/`), run through pre-commit.
* Conventional commits + commitizen → semver, `CHANGELOG.md`; protected `main`; pull requests with CI.
* SPDX header `GPL-3.0-or-later` in every source file.
* Docstrings (numpy style). MkDocs site with:
  * user guide;
  * configuration reference generated from the pydantic models;
  * variable list generated from the registry;
  * validation codes (§12);
  * a "coming from Matlab D3D_plot" page (Appendix A);
  * an examples gallery;
  * architecture (this file).
* Coverage gate ≥ 85 % for `src/`.

### 13.2 Test layers

| Layer | Content | Data | When |
|-------|---------|------|------|
| Unit | Config, overrides, validation codes, INI adapters, time handling (tz, duplicates, matching), variable registry, labels (all keys, UTF-8), fingerprints, write guard, atomic writes, river-km parsing and providers, geometry algorithm (hypothesis property tests: segments partition the axis, angles, cutoffs), statistics, measurement filters, `.mat` conversion, sharding | Synthetic in-memory objects | Every commit, Windows + Linux |
| Integration | Full runs of `map2d` and `river_km_statistics` on the **synthetic SMT case** (§13.3) and its variants. Results compared with `expected/` | Generated in a session temporary folder (pytest fixture) | Every commit |
| Merge plan | Python reader == dfm_tools merged values for every variable and time (synthetic); corrupt variant detected and handled per policy | Synthetic | Every commit |
| Images | pytest-mpl baselines (Linux, fixed fonts); `--fresh-figures` pixel-identical to draw-once | Synthetic | Every commit (Linux) |
| Parallel equivalence | `serial` == `processes` == union of all shards (byte-identical cache) | Synthetic | Every commit |
| Read-only inputs | Snapshot (size, mtime, hash) of all inputs before and after a full run | Synthetic | Every commit |
| Matlab comparison (synthetic) | Cache values vs Matlab `mat/` of the synthetic case | Synthetic + Matlab results produced by the user (`@pytest.mark.requireslocaldata`) | Locally |
| Acceptance (real) | Acceptance case 1 (§13.4) | P-drive, read-only (`requireslocaldata`) | Manually / before releases |
| Benchmarks | Prepare, process and plot throughput; merge-plan reader vs dfm_tools | Synthetic (CI), r209 (local) | Nightly / locally |

pytest markers: `unit`, `integration`, `mpl`, `slow`, `requireslocaldata`, `acceptance`.

### 13.3 Synthetic SMT case (implemented)

Generator: `tools/synthetic_smt/` ([README](../tools/synthetic_smt/README.md)). Run with `.venv\Scripts\python.exe tools\synthetic_smt\generate.py --force`; the output goes to `data_local/synthetic_smt_01/` (about 87 MB, git-ignored). It verifies itself with HYDROLIB-core, dfm_tools, rasterio, geopandas and scipy. A pytest fixture will generate it into a temporary folder. No frozen copy is committed (D19).

**Model**:
* a 10 km meandering channel (sine-generated, EPSG:28992-like coordinates, km 10–20);
* 6 406 faces: quadrilaterals plus 12 triangles. The summer bed is 4 × 25 m cells; the floodplains are 6 × 50 m on each side; Δs = 25 m;
* **3 partitions** with ghost cells, `mesh2d_flowelem_domain`/`globalnr`. Partition 2 is a strip along the whole left floodplain (as `r209` partitions 12 and 31);
* **SMT**: 4 sub-runs; map times start, end of spin-up and end; `TStart(k+1) = TStop(k)`; `MorFac = 10`, spin-up 1 day; morphological time 0, 30, 90, 210 and 300 days.

**Analytic fields**:
* `mesh2d_mor_bl = z₀ − i·s + A·sin(2πs/250)·cos(πn/100) + e·t_mor` in the summer bed, and `z₀ − i·s + 5` on the floodplains;
* water level, depth, velocity and shear stress depend on a stage per sub-run;
* 3 fractions × 2 bed layers;
* NaN (3 faces) and Inf (1 face) in `r001`, sub-run 2, last time.

**Simulations**: `r001` (erosion −2 mm/day) and `r002` (−4 mm/day).

**Variants**:
* `running` (`output/work`, last sub-run truncated);
* `crashed`;
* `non_partitioned`;
* `gap` (sub-run 2 missing);
* `corrupt_partition` (sub-run 0, partition 1, map times 1–2 garbage; as `r209`).

**`r209` peculiarities reproduced**:
* map time units 2021-02-08 vs MDU `RefDate` 20121231;
* EPSG 0;
* "Computation finished" only in the partition `.dia` files;
* duplicate boundary times between sub-runs;
* `morft` with time units.

**Inputs**:
* river-km files (Maas `MA`, generic `SY`, Rijntakken `WA`; 100 m, 1 km and 3 km);
* regions `L1R1`, `L1L3`, `R1R3`, `WIDE`;
* `synthetic.ldb`.

**Measurements**:
* SMB `.mat` per region (unsorted times);
* VRT of 2 tiles; a half-pixel-shifted GeoTIFF in cm (factor 0.01); a GeoTIFF outside the tolerance;
* "vakken" shapefiles (2026 field names, failing filters, shuffled order);
* catalogues (absolute paths, because Matlab needs them).

**Expected results**:
* `time_axis.csv` (9 merged times);
* `cells_per_segment_250m.csv`;
* `smb_<sim>_<region>_250m.csv` (all statistics, Matlab definitions).

**Matlab input**: `scripts/main_plot_synthetic.m`, to be run by the user once (pending).

### 13.4 Acceptance case 1: Maas Lixhe–Roermond (`r209`)

* Input: the equivalent of `main_plot_01.m` (§10.2).
* **Rules**:
  * read-only on `p:\`;
  * `output.root` in a local temporary folder;
  * `forbidden_roots` contains the project folder;
  * input snapshot check.
* Comparison with `r209\mat` (values) and `r209\fig` (visual review):
  * Matlab face order differs from ours, so faces are matched **by coordinates** (KD-tree on face centres from `r209\mat\grd.mat`, read-only, tolerance 1 mm);
  * times are matched through `M2D_tim.mat`/`tim.mat` (sub-run, local index);
  * SMB per segment: absolute tolerance 1e−6 m on statistics, except where face-centre conventions differ (to be investigated: §4.5).
* Performance targets: §7.22.

### 13.5 CI

GitHub Actions on push and pull request:
* lint (ruff), types (mypy);
* tests on `{ubuntu-latest, windows-latest} × {3.11, 3.12, 3.13}`. The synthetic case is generated once per job and cached between steps;
* coverage upload;
* docs build;
* on tags: build wheel and sdist.

Nightly: benchmarks and slow tests.

---

## 14. Upstream contributions

Develop each item first behind an adapter in `d3dplot`, with tests. Propose it upstream when stable, then switch to the upstream version and pin the minimum version. Track the status in `docs/upstream.md`.

| Repository | Contribution | Why | Priority |
|------------|--------------|-----|----------|
| HYDROLIB-core | Option to report unknown MDU keywords as **warnings** (e.g. a load setting) | `FMModel` rejects real MDUs (F2) | High |
| HYDROLIB-core | `MorphologyModel` (`.mor`) and `SedimentModel` (`.sed`) | Typed access to MorFac, MorStt, fractions | High |
| HYDROLIB-core | Faster `PolyFile` path for large `.ldb`/`.pol` (NumPy block reader) | 7.7 s vs 0.7 s per 10⁶ points (F6) | Medium |
| HYDROLIB-core | Typed `RefDate`/`TStart`/`TStop` → datetime helpers | Time handling | Low |
| dfm_tools | Fast partitioned reader with a cached merge plan (`MergePlan`) | 75 s vs 1 s per sub-run (F1) | High |
| dfm_tools | Robust `open_partitioned_dataset` against corrupt or inconsistent `time`/`morft` (take from a reference partition, report) | Fails on `r209` sub-run 0 (F3) | High |
| dfm_tools | SMT support: `open_smt_dataset` (merged time axis, duplicate handling, sub-run index) | SMT | Medium |
| dfm_tools | Morphological time helper (`morft`, MorFac/MorStt) | Time axis | Medium |
| dfm_tools | Zonal statistics of face values per polygon set (area-weighted, NaN/Inf-safe) | SMB | Medium |
| dfm_tools | River-km utilities (fixed-format file, km ↔ xy, perpendicular cut lines, segment polygons; generic part of §4.5) | SMB | Medium |
| D-Flow FM (report) | Corrupt `time`/values in partition 0021 of `r209` sub-run 0 | Data quality | To decide with the user (§17) |

---

## 15. Implementation plan

### 15.1 Phase 0: done

* Plan (this document), licence, repository.
* Synthetic SMT generator with self-check and variants.
* Exploration of the real case (§5).

### 15.2 Phase 1: foundations

| WP | Content | Deliverables | Done when |
|----|---------|--------------|-----------|
| 1.1 | Repository skeleton | `pyproject.toml` (deps §6; extras `tiles`, `movies`, `dev`, `docs`), `src/d3dplot/__init__.py`, ruff/mypy/pytest config, pre-commit, CI workflow, MkDocs skeleton, `CONTRIBUTING.md`, `CHANGELOG.md`, commitizen | `uv pip install -e .[dev]` works; CI green on Windows + Linux with a smoke test; docs build |
| 1.2 | Synthetic case as a test fixture | `tests/conftest.py`: session fixture adding `tools/synthetic_smt` to `sys.path` and generating into `tmp_path_factory`; marker `requireslocaldata` | Integration tests can use `synthetic_case` |
| 1.3 | Configuration | `config/*` models, YAML IO, `--set` overrides, resolved-config writer, JSON schema | Round trip YAML ↔ models; unknown keys rejected; schema exported |
| 1.4 | Validation framework | `validate/report.py`, codes, text/JSON output, static checks V-CFG-* | Each code has a test |
| 1.5 | Model input | `model_input/*` (tolerant INI, `MduInfo`, `.mor`, `.sed`, DIMR) | Synthetic MDU/`.mor`/`.sed` parsed. (local) real `r209` files parsed |
| 1.6 | Discovery | `simulation/discover.py`, `subrun.py`, partition file discovery, V-SIM-*, V-OUT-001..003 | All synthetic variants classified correctly |
| 1.7 | Status | `simulation/status.py` (`.dia` tails), json/csv writers | States of all synthetic variants correct. (local) `r209` finished |
| 1.8 | Store | `store/layout.py`, `guard.py`, `atomic.py`, `fingerprint.py`; input-snapshot test helper | Guard refuses unmarked or forbidden roots; atomic writes tested |
| 1.9 | Labels | `labels/*` + CSVs seeded from Matlab | en/nl/es complete; templates; units |
| 1.10 | Variables | `variables/registry.py` with the v1 variables, aliases, "did you mean" | Registry docs generated |
| 1.11 | CLI part 1 | `info`, `variables`, `schema`, `validate`, `init` | Commands work on the synthetic case |

### 15.3 Phase 2: model output engine

| WP | Content | Done when |
|----|---------|-----------|
| 2.1 | Inspect phase: partition identity, merge plan(s) + `grid.nc`, topology signatures, reference sub-run selection (§7.6, §9.3) | Values equal dfm_tools on the synthetic case; `corrupt_partition` handled; a fixture with changed topology produces two plans. (local) `r209` merge plan built |
| 2.2 | `MapReader`, quality checks, `times.nc`, `TimeAxis` (merge, duplicates, tz, selection, matching) | Synthetic `time_axis.csv` reproduced; V-OUT-030/031, V-TIME-* tested |
| 2.3 | Execution: tasks, planner, fingerprints and skipping, serial/process executors, worker init, logging queue, run summary, sharding | Parallel-equivalence and resumability tests pass |

### 15.4 Phase 3: `map2d`

| WP | Content | Done when |
|----|---------|-----------|
| 3.1 | Process (selections, files §9.5) | Values equal the analytic fields |
| 3.2 | Plot: templates, extents (explicit, along km), visible-face subsets, render modes, comparisons incl. regridding, writer, naming, metadata | Image baselines; `--fresh-figures` identical |
| 3.3 | Overlays: `.ldb` (+ cache), river-km labels, tiles (optional) | Shown in the baseline images |
| 3.4 | Map measurements: catalogue, rasters (VRT, factor, mosaic, resampling), polygons (filters, id matching), differences, panel, `color_limits_from_measurements` | Synthetic catalogue cases (inside/outside tolerance, cm factor, shifted grid, shuffled polygons) correct |
| 3.5 | Provenance, `replot`, `plot --set`, movies | Replot reproduces identical images |

### 15.5 Phase 4: `river_km_statistics`

| WP | Content | Done when |
|----|---------|-----------|
| 4.1 | River-km file + providers (data files for Rijntakken/Maas, generic), edges, cutoffs, angles, cut lines, polygons, checks | Property tests; Rijntakken track tables tested; synthetic geometry as expected |
| 4.2 | Regions, segment assignment, coverage/overlap checks, diagnostic plot | `cells_per_segment_250m.csv` reproduced |
| 4.3 | Process statistics; post-processing (slope, × width, region differences, time averages) | `smb_*.csv` reproduced exactly (incl. NaN/Inf exclusion) |
| 4.4 | Plots: views, groupings, region groups, `x_limits`, hooks | Image baselines |
| 4.5 | Profile measurements: `.mat` → NetCDF (v5; v7.3 later), matching, plotting | Synthetic `.mat` converted (sorted times, units); measurements drawn at the right times |

### 15.6 Phase 5: hardening and v1.0

| WP | Content |
|----|---------|
| 5.1 | Acceptance case 1 on `r209` (§13.4) incl. coordinate-based face matching and performance targets |
| 5.2 | Matlab comparison on the synthetic case (needs the user's Matlab run) |
| 5.3 | SLURM stub (`slurm write`), `--shard` documentation |
| 5.4 | Documentation (user guide, references, "from Matlab", gallery), agent skills |
| 5.5 | Release v1.0 (GitHub release; PyPI name `delft3d-plot` to be checked) |

### 15.7 Later

HIS, PRF, M1D, HFM and GRD analyses; 3D (layers, depth average with dfm_tools); Delft3D4; more figure formats; full SLURM support; MAT v7.3 measurements; model − measurement maps.

### 15.8 Working conventions for implementation sessions

* One work package per branch and pull request, with tests and docs. Conventional commit messages with the Copilot trailer (§0.2).
* **Completion gates use only data available to everyone**: synthetic case, unit fixtures, dfm_tools sample data. Checks marked "(local)" in the tables need P-drive data or the user's Matlab results. They are run when available and reported, but they never block a work package.
* External behaviour (dfm_tools merging, HYDROLIB-core parsing, rasterio windows) is covered by tests on the synthetic case. Where a behaviour cannot be generated (e.g. a specific D-Flow FM quirk), a minimal NetCDF fixture is written by a helper in `tests/fixtures/`.
* Keep this document current: decisions in §2, status in §0.1, new findings in §3/§5.
* Never weaken a validation rule to make a test pass. Fix the cause or record a decision.

---

## 16. Risks

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Network-drive I/O dominates run time | Slow runs | Merge plan; per-sub-run tasks; worker count tuned by `d3dplot benchmark`; `output.root` on a local disk |
| More kinds of corrupt or odd real output | Wrong results or crashes | Quality checks (§7.6), policies, synthetic variants for every new case found |
| Differences from Matlab (face centres, time selection, mosaics) | Acceptance disputes | Document every intended difference (§4, D17, D25); coordinate-based comparison |
| Upstream API changes (dfm_tools, HYDROLIB-core, xugrid) | Breakage | Pin minimum versions; adapters; CI against latest |
| Large grids are slow to plot | Slow plots | Visible-face subsets, raster mode |
| Windows path length, permissions on shared drives | Failed writes | V-OUT-040/041; short names; `output.root` |
| Offline machines (tiles) | Missing backgrounds | Tiles optional; local tile source (§17) |
| Scope creep beyond v1 | Delay | Plugin architecture; v1 scope fixed (§1.3) |

---

## 17. Open questions

1. **Matlab reference run of the synthetic case**: pending. Run `data_local/synthetic_smt_01/scripts/main_plot_synthetic.m` with Matlab `D3D_plot` and report errors, if any.
2. **Duplicate morphological times** (D25): select the *last* (proposed) or the *first* output with the same morphological time? What does Matlab do in practice? The acceptance comparison will show it.
3. **Source `.ldb` of `j21_ldb.mat`**: where is the original `.ldb` used in the Maas project? It is needed for acceptance case 1.
4. **Offline background tiles**: Matlab used a local `earth_tiles` checkout (`C:\checkouts\earth_tiles`, `p:\studenten-riv\05_OpenEarthTools\earth_tiles`). Should the tool support that format? What is it?
5. **`on_corrupt_output` default**: `error` (proposed) or `mask`?
6. **Reporting the corrupt partition** of `r209` sub-run 0 (partition 0021, map times 2–3) to the model team or D-Flow FM developers?
7. **Changed proposals** to confirm: float64 cache (D26), uv instead of pixi (D28), merge-plan reader (D21), tolerant INI parsing (D22).
8. **Distribution**: PyPI package `delft3d-plot` (name availability), conda-forge later?

---

## 18. Glossary

| Term | Meaning |
|------|---------|
| SMT | Simulation Management Tool: a D-Flow FM simulation split into consecutive sub-runs (`output/<n>`), e.g. a quasi-steady hydrograph |
| Sub-run | One D-Flow FM run within an SMT simulation |
| Partition | Part of the domain computed by one MPI process; one map file `<stem>_<pppp>_map.nc` |
| Ghost cell | A face stored in a partition file but owned by another partition (`mesh2d_flowelem_domain ≠ p`) |
| Merge plan | Per partition: owned local face indices and their position in the merged grid |
| `morft` | Morphological time in the map file (time units) |
| MorFac, spin-up | Morphological acceleration factor; initial hydrodynamic period without bed updates (`MorStt`) |
| River km (rkm) | Chainage along a river axis; fixed-format file `x, y, <km>_<TAG>, km` |
| Track / branch | Rijntakken route (e.g. `WA`) / the branch a km belongs to (e.g. `BR`, `PK`) |
| Region | Polygon selecting faces (e.g. the summer bed) |
| Segment | Part of a region between two cut lines |
| Cut line | Line perpendicular to the river axis at a segment edge, of half-length `half_width_m` |
| Extent | Plot window `(xmin, xmax, ymin, ymax)` |
| Comparison | `value` or a difference (§7.12) |
| View / grouping | SMB plot type / which simulations or regions share a figure |
| Fingerprint | Hash of everything that determines a cache file or figure |
| Plot group | A plot task producing many images from one reused figure |
| Prepare / process / plot | Pipeline stages (§7.1) |

---

## Appendix A: Matlab → Python names

Principles:
* full English words in `snake_case`;
* no `do_` prefixes;
* orthogonal options (view × comparison × grouping) instead of combined flags;
* one name per concept;
* units in names when ambiguous;
* closed sets of string values instead of magic numbers.

### A.1 Comparisons

| Matlab | Python (`comparisons`) | Meaning |
|--------|------------------------|---------|
| `do_p_single` | `value` | The quantity |
| `do_diff_t` | `difference_time` | v(t) − v(t₀) |
| `do_diff_s` | `difference_simulation` | v_sim − v_ref |
| `do_diff_s_t` | `difference_simulation_time` | [sim(t) − sim(t₀)] − [ref(t) − ref(t₀)] |
| `do_diff_s_perc` | `difference_simulation_relative` | 100·(sim − ref)/ref |
| `do_diff_t_first_time` | `include_initial_time_difference` | Also plot the zero difference at t₀ |
| `do_diff_s_ref_sim` | `include_reference_in_differences` | Also plot ref − ref |
| `use_local_time` | `time_matching: reference \| own` | |
| `tim_tol` | `time_tolerance` | |

### A.2 Common options

| Matlab | Python |
|--------|--------|
| `fdir_sim{k}` / `str_sim{k}` | `simulations[k].path` / `.label` |
| `sim_ref` | `reference_simulation` |
| `lan` | `language` |
| `tag_serie` | `series` |
| `fdir_postprocess`, `fdir_mat`, `fdir_fig` | `simulations[k].output_dir`, `output.root` |
| `simdef_overwrite`, `order_anl`, `do`, `do_p`, `do_mat`, `do_create_mat`, `tim_just_load`, `fig_visible` | removed (CLI stages, executors, fingerprints) |
| `overwrite` / `fig_overwrite` | `reprocess` / `replot` |
| `only_adhoc`, `adhoc`, `function_handles` | `hooks` / plugin analyses |
| `var` | `variables` |
| `var_idx` / `sum_var_idx` | `fractions` / `sum_fractions` |
| `layer`, `depth_average`, `depth_average_limits`, `elevation` | `vertical` |
| `tim` | `times` |
| `tim_type` | `time_axis: hydrodynamic \| morphological` |
| `xlims` / `ylims` | `extents` (maps); `x_limits`, `y_limits` (line plots) |
| `clims`, `clims_diff_t`, `clims_diff_s`, … | `color_limits: {<variable>: {<comparison>: [[min, max], …]}}` |
| `ylims_diff_*` | `y_limits: {<variable>: {<comparison>: […]}}` |
| `filter_lims*` | `mask_range` |
| `cmap` | `colormap` |
| `clims_type`, `clims_type_var` | `color_limits_mode: fixed \| {days_since: date}` |
| `fig_print` | `figure.formats: [png]` |
| `fig_resolution`, `fig_size`, `font_size` | `figure.dpi`, `figure.size_cm`, `figure.font_size` |
| `fig_variable`, `fig_unit` | `display_name`, `display_scale` |
| `do_movie`, `tim_movie` | `movie: {duration_s}` |
| `Lref` | `labels.vertical_datum` |
| `do_replace_underscore`, `leg_str` | `legend_labels` |
| `path_tiles` | `tiles_cache_dir` |

### A.3 `map2d`

| Matlab | Python |
|--------|--------|
| `do_3D` | `view_3d` |
| `do_vector` | `velocity_vectors: none \| scaled \| normalized` |
| `do_fxw`, `color_fxw` | `fixed_weirs: none \| input \| snapped`, `fixed_weirs_color` |
| `do_axis_equal` | `aspect: equal \| auto` |
| `plot_tiles` | `background_tiles` |
| `epsg_in` | `crs` |
| `fpath_ldb` | `land_boundaries` (**`.ldb` only**) |
| `do_rkm_disp`, `fpath_rkm_disp`, `rkm_disp_color` | `river_km_labels: {file, color}` |
| `do_plot_along_rkm`, `fpath_rkm_plot_along`, `rkm_tol_x/y` | `extents_along_river_km: {file, window_x_m, window_y_m}` |
| `write_shp`, `save_format` | `export: [shapefile, netcdf]` |
| `measurements` (catalogue) | `measurements: {<variable>: {catalogue, …}}` |
| `tol_time_measurements` | `measurements.<variable>.time_tolerance` (30 days) |
| `tol_x/y_measurements` | `measurements.<variable>.spatial_margin_m` (1000) |
| `measurements_edgecolor` | `measurements.<variable>.edge_color` |
| `measurements_tag_*`, `measurements_coverage`, `measurements_pol_location` | `measurements.<variable>.polygon_filters: {preset, value_field, count_field, area_field, min_coverage, location_field, allowed_locations, id_field}` |
| `do_clims_measurements` | `measurements.<variable>.color_limits_from_measurements` |
| — | `measurements.<variable>.layout`, `mosaic`; `figure.render` |

### A.4 `river_km_statistics`

| Matlab | Python |
|--------|--------|
| `sb_pol{kp,1}` + `measurements{kp,1}` | `regions: [{name, polygon, measurements}]` |
| `sb_pol{kp,2}` … | `region_groups` |
| `sb_pol_diff`, `measurements_diff` | `region_differences: [{regions: [a, b], measurements}]` |
| `tol_time_measurements` / (1 km hard-coded) | `measurements_time_tolerance` / `measurements_km_tolerance` |
| `do_include_mea_xylims` | `limits_include_measurements` |
| `leg_mea` | `measurements_label` |
| `fpath_rkm` | `river_km.file` |
| `rkm`, `rkm_name` | `river_km.sets` |
| `rkm_br`, `rkm_track` | `river_km.branches`, `river_km.track` (river detected from the tags) |
| (1000 m) | `river_km.half_width_m` |
| `do_polygon_boundary`, `polygon_boundary_shrink` | `regions[].concave_hull: {ratio}` |
| `statis_plot` / `val_*` | `statistics: [mean, std, max, min, count, sum, sum_per_length, mean_area_weighted]` |
| `do_p_single`, `do_all_s` | `views: [profile]`, `grouping: all_simulations` |
| `do_xvt`, `do_xvt_cel` | `views: [profile_all_times, space_time]` |
| `do_tv*`, `rkm_plot_tv` | `views: [time_series]`, `time_series_locations_km` |
| `do_cum` | `views: [cumulative]` |
| `do_area` | `stacked_fractions` |
| `do_val_B`, `do_val_B_mor` | `multiply_by_width: none \| flow \| morphological` |
| `detab_ds` | variable `bed_slope_streamwise` |
| `tim_ave`, `tim_ave_type` | `time_averages: [{name, times, time_axis}]` |
| `do_plot_inpolygon` | `diagnostics: [polygon_membership]` |
| `do_plot_structures` | `structures_overlay` (or a hook) |
| `plot_val0`, `do_staircase`, `do_marker`, `xdir` | `show_initial`, `staircase`, `markers`, `x_reversed` |
| `skip_if_not_found` | `on_missing_input: error \| skip` |
| `legend_file` | `legend_labels` |

### A.5 Variables (v1)

| Canonical | Matlab alias | FM NetCDF |
|-----------|--------------|-----------|
| `bed_level` | `bl` | `mesh2d_mor_bl` / `mesh2d_flowelem_bl` |
| `water_level` | `wl` | `mesh2d_s1` |
| `water_depth` | `wd` | `mesh2d_waterdepth` |
| `velocity_magnitude` | `umag` | `mesh2d_ucmag` |
| `bed_shear_stress` | `taub` | `mesh2d_taus` |
| `cell_area` | — | `mesh2d_flowelem_ba` |
| `layer_fraction` | `Fak` (layer 0) | `mesh2d_lyrfrac` |
| `mean_grain_size_arithmetic` / `_geometric` | `dm` / `dg` | derived (`lyrfrac` + `.sed`) |
| `bed_slope_streamwise` | `detab_ds` | derived (SMB) |

---

## Appendix B: history of this document

| Version | Content |
|---------|---------|
| v1 | First architecture proposal |
| v2 | Decisions on scope (M2D, SMB; FM, SMT), cache, reprocessing, status, replot, naming, skills, upstream |
| v3 | SMB geometry specification, one file per time step, Windows process pool, draw-once plotting, labels, GPL-3.0 |
| v4 | Measurements, read-only inputs, hooks, region groups, SLURM stub, acceptance case |
| v5 | Answers on measurements, `r209` findings, synthetic SMT case specification and generator |
| v6 | Thorough review against real data (merge plan, tolerant MDU parsing, corrupt partitions, time zones, duplicate morphological times, float64, task granularity, path lengths); `.ldb`-only land boundaries; independent review (14 issues fixed: inspect phase, recursive fingerprints, topology signatures, deterministic sharding and geometry, derived-statistic definitions, verified versions); consolidated, self-contained plan with handover, file formats, validation catalogue and work packages |
