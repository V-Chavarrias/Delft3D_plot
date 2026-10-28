# Delft3D_plot (Python): Architecture and Implementation Plan

Status: **v6.71; WP 1.10–1.11, WP 2.1–2.3, WP 3.1–3.5, WP 4.1–4.5, and WP 5.2 are implemented and locally validated.** WP 4.5 profile measurements is committed as `73e16c5` on `feat/river-km-measurements`; WP 5.2 synthetic Matlab comparisons are implemented on `feat/matlab-comparison`. WP 2.3 and WP 3.1 were committed on `feat/execution` (`735fb38`); WP 3.2 on `feat/map2d-plotting` (`d508cf7`); WP 3.3 on `feat/map2d-overlays` (`53d1bb7`); WP 3.4 on `feat/map2d-measurements` (`6da45e8`); WP 3.5 on `feat/map2d-provenance` (`38a84a4`, `c86f3a3`); WP 4.4 on `feat/river-km-plotting` (`a52d8f6`). WP 4.2's 6,406-face/40-segment synthetic preparation took 0.388 s; WP 3.5's two-frame synthetic-cache timings were 0.910 s fresh, 0.517 s reused, and 0.757 s for single-figure replay. Both WP 5.2 local comparison tests pass for r001 and r002 in 63.27 s including fixture generation. The 284-test CI-eligible suite passes; strict mypy over 86 source files, strict MkDocs, changed-file Ruff/format, and `git diff --check` pass. The river-km implementation includes provider and region geometry, guarded caches, shared-quantity statistics processing, all five shared plot views, diagnostics, groupings, comparisons, and profile-measurement conversion, matching, and overlays. The CLI provides read-only `info`/`variables`, schema output, validation and inspection, cache-backed `plot`, provenance-backed `replot`, and `measurements convert`; complete CLI `run` integration remains. P-drive r209/Rijn acceptance is deferred per D65, and no P-drive data was accessed or written during WP 5.2. D69–D82 record the time, quantity, plotting, provider, UGRID, measurement-conversion, and Matlab-comparison decisions. See [the synthetic case generator](https://github.com/V-Chavarrias/Delft3D_plot/blob/main/tools/synthetic_smt/README.md). This document is self-contained: a new working session (human or AI agent) should be able to continue the project from it alone. Read §0 first.

The synthetic overlay-only loop (40 frames × 1,000 measured points; median of 7 `time.perf_counter` runs) took 0.805739 s with profile overlays versus 0.000022 s without, an added 20.143 ms per frame; this excludes model plotting and figure finalisation.

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
| Repository | GitHub [V-Chavarrias/Delft3D_plot](https://github.com/V-Chavarrias/Delft3D_plot), local `c:\checkouts\Delft3D_plot` | `feat/matlab-comparison`, local and unpushed; WP 4.5 committed as `73e16c5`; WP 5.2 locally validated; P-drive acceptance deferred per D65 |
| Plan (this file) | `docs/architecture.md` | v6.71 |
| Licence | `LICENSE` | GPL-3.0 |
| Synthetic SMT and plain FM test case generator | `tools/synthetic_smt/` | Implemented and verified locally (§13.3) |
| Land-boundary converter (`.mat` cache → `.ldb`) | `tools/mat_to_ldb.py` | Implemented. Used to recreate the Maas land boundary (D45) |
| Generated synthetic case | `data_local/synthetic_smt_01/` | Git-ignored; regenerate with the generator |
| Recreated Maas land boundary | `data_local/maas/j21_ldb.ldb` (55.7 MB) | Git-ignored. 174 220 polylines, 2 343 057 points; read by HYDROLIB-core in 22 s, identical to the `.mat` |
| HYDROLIB-core modifications | §14.1–§14.3 | WP 0.1 (`54011e0d`) and both DIMR fixes (`3e4f7317`, `96d07c87`) are integrated locally on `feat/mdu_morphology_keywords`; the external-library fix is also committed on `fix/dimr-external-libraries` |
| Python environment | `.venv/` (uv, Python 3.12) | Git-ignored. dfm_tools and HYDROLIB-core are installed editable from the local checkouts; HYDROLIB-core is currently on `feat/mdu_morphology_keywords` with WP 0.1 and the DIMR fix |
| Package `d3dplot` | `pyproject.toml`, `src/d3dplot/` | WP 1.1–1.11 foundations, WP 2.1–2.3 execution, WP 3.1–3.5 map2d, and WP 4.1–4.5 river-km geometry, processing, plotting, and profile measurements implemented and locally validated |
| Configuration design | §2 decisions D57–D59, D69–D70 | Nested `times` object, Matlab-compatible one-based time indices, configurable active-sediment threshold, and shared `map.render`; WP 1.3 complete locally; plugin config integration deferred to the analysis registry |
| Validation framework | `src/d3dplot/validate/` | WP 1.4 complete locally: report serialization and static V-CFG-001..004 checks |
| Model-input adapters | `src/d3dplot/model_input/` | WP 1.5 implemented locally; MDU, `.mor`, `.sed`, DIMR, V-MDU-001, and real r209 read-only validation complete |
| Simulation discovery | `src/d3dplot/simulation/` | WP 1.6 implemented locally; synthetic FM/SMT cases pass; real r209 discovery completes read-only in 99.283 s |
| Simulation status | `src/d3dplot/simulation/status.py` | WP 1.7 implemented locally; synthetic statuses pass; real r209 is finished with no validation issues; bounded status scan 27.371 s |
| Output store | `src/d3dplot/store/` | WP 1.8 implemented locally; guarded marked roots, atomic writers, fingerprints, and input snapshots are tested |
| Labels | `src/d3dplot/labels/` | WP 1.9 implemented locally; 141 variable aliases, 127 complete translations, and 22 unit types are packaged and tested |
| Variables | `src/d3dplot/variables/` | WP 1.10 and WP 2.2a complete locally; 19 raw and 23 derived v1 specs, dependency resolution, per-time caching, formula callbacks, grid operators, aliases, NetCDF candidates, suggestions, and generated documentation |
| CLI | `src/d3dplot/cli.py` | WP 1.11 and WP 2.1 implementation: console and module entry points; `info`, `variables`, `schema`, `validate`, `inspect`, and no-overwrite `init`; `validate` and `inspect` now write guarded `.dia` diagnostics and a debug `.log` |
| Synthetic-case pytest fixture | `tests/conftest.py` | Session-scoped fixture generates and verifies in pytest's temporary folder (WP 1.2) |
| CI and documentation site | `.github/workflows/ci.yml`, `mkdocs.yml` | All 284 CI-eligible tests pass; both local Matlab comparison tests pass; strict MkDocs, strict mypy over all 86 source files, changed-file Ruff/format, and `git diff --check` pass; remote CI not run |
| Matlab reference results of the synthetic case | `data_local/synthetic_smt_01/`, `data_local/synthetic_smt_02/` | Original v0.1.0 references preserved. Current v0.3.0 case has five bed-level and specific-discharge maps per simulation and 15 SMB files per simulation; numerical references are complete. Matlab's later SMB figure stage exits in `fig_1D_01` on a measurement-overlay size mismatch; numeric comparisons pass. |
| Agent guidance for development | `AGENTS.md`, `.github/instructions/plotting.instructions.md`, `.github/skills/{add-analysis-or-option,upstream-contribution}` | Done (§13.6) |

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

1. **WP 0.1** (§15.1a, §14.1) is complete locally on HYDROLIB-core branch `feat/mdu_morphology_keywords`, commit `54011e0d`. The full test suite and read-only MDU checks pass; no upstream PR or release exists yet. See [upstream.md](./upstream.md).
2. **WP 1.1** (§15.2) is complete locally on branch `feat/repository-skeleton`, commit `29b08d3`. Editable installation, smoke test, Ruff, mypy, pre-commit config validation, and strict docs build pass on Windows/Python 3.12; the Windows/Linux GitHub Actions matrix is configured but has not run remotely.
3. **WP 1.2** is complete locally on branch `feat/synthetic-case-fixture`: `synthetic_case` generates and verifies once per test session in `tmp_path_factory`; its integration test confirms model and expected-result files. The CI-eligible suite passed (2 tests, 18.85 s).
4. **WP 1.2a generator deliverables** are implemented locally on branch `feat/plain-fm-synthetic-variants`: `fm_partitioned` and `fm_single` each have one run and 40 map times, with shared expected time-axis and SMB statistics. The generator self-check and WP 1.6 discovery acceptance pass.
5. **WP 1.3** is complete locally on `feat/config-models`: Pydantic models for the built-in analyses, safe YAML/JSON IO, `--set` overrides, resolved-config writer, and JSON Schema. Use nested `times: {at, axis, tolerance}` (D57) and the shared `map.render` option (D58). Plugin config discovery and schema support are deferred until the `d3dplot.analyses` registry is implemented (D59). The 12 focused configuration tests, 14-test CI-eligible suite, Ruff, strict mypy, lock check, pre-commit validation, and strict documentation build passed on Windows/Python 3.12.
6. Set d3dplot's minimum HYDROLIB-core version to the first upstream release containing WP 0.1; do not guess or pin an unreleased version.
7. The Matlab reference results of the synthetic case exist: add the Matlab comparison tests (WP 5.2) as soon as the cache is produced (from Phase 3).
8. **WP 1.4** is complete locally on `feat/validation-framework`: structured issues, text/JSON reports, Pydantic error conversion, and static V-CFG-002..004 checks. All 24 CI-eligible tests, Ruff, strict mypy, lock check, pre-commit validation, and strict documentation build passed on Windows/Python 3.12.
9. **WP 1.5** is implemented locally on `feat/model-input`: typed MDU, `.mor`, `.sed`, and DIMR adapters, V-MDU-001 reporting, and synthetic tests are complete. All 31 CI-eligible tests pass; strict mypy, strict MkDocs, and changed-file Ruff/format checks pass. The full HYDROLIB-core suite passes with WP 0.1 and the DIMR fixes integrated, and real r209 sub-run 0 DIMR resolution plus model-input parsing succeeds read-only in 0.390 s. Whole-repository Ruff and formatting checks report unrelated pre-existing issues; remote CI has not run. WP 1.6 is complete; see item 10.
10. **WP 1.6** is complete locally on `feat/simulation-discovery`: plain-FM/SMT discovery, typed V-SIM/V-OUT reporting, and read-only NetCDF header validation are tested. All 51 CI-eligible tests, strict mypy, strict MkDocs, and changed-file Ruff/format checks pass. Real r209 discovery found 195 sub-runs and 8,580 map files with no issues in 99.283 s (<2 min), using D61's bounded I/O thread pool. Do not push or open a pull request unless asked.
11. **WP 1.7** is complete locally on `feat/simulation-status`: status inspection includes `not_started` when valid model input has no maps, per-sub-run `.dia` and NetCDF metadata, configurable staleness, and caller-writer JSON/CSV serializers. Corrupt-output counts remain unknown until WP 2.2. The 69 CI-eligible tests, strict mypy, strict MkDocs, and changed-file Ruff/format checks pass. Read-only r209 status is `finished` (195 sub-runs, 44 partitions, 0 validation issues); bounded status detection took 27.371 s, down from 433.548 s serially (15.8×). Discovery took 124.220 s and discovery plus status 151.590 s. Numeric `TStop` comparison uses elapsed seconds, not the decoded CF origin.
12. **WP 1.8** is complete locally on `feat/store`: output-root precedence, marked-root and forbidden-path guards, atomic text/bytes/JSON writers, canonical recursive fingerprints, and before/after input snapshots are covered. V-OUT-041 reports forbidden, unwritable, and invalid output roots. All 82 CI-eligible tests, strict mypy, strict MkDocs, and changed-file Ruff/format checks pass.
13. **WP 1.9** is complete locally on `feat/labels`: the Matlab mapping and translation catalogs are packaged with completed en/nl/es entries, comparison templates, and unit-scale definitions. The API supports project extra dictionaries, datum/fraction formatting, and warned NetCDF metadata fallback. All 100 CI-eligible tests, strict mypy, strict MkDocs, and changed-file Ruff/format checks pass; the wheel contains all three CSV resources.
14. **WP 1.10** is complete locally on `feat/variables`: the registry covers 10 raw and 23 derived v1 quantities, resolves canonical names, Matlab aliases, and registered NetCDF candidates case-insensitively, and raises V-VAR-001 with canonical suggestions. Generated `docs/variables.md` is tested against the registry; the three missing label translations were added. All 115 CI-eligible tests, strict mypy, changed-file Ruff/format checks, and strict MkDocs pass. The registry is metadata-only; computation callbacks remain WP 2.2a/b.
15. **WP 1.11** is complete locally on `feat/cli-part-1`: added the `d3dplot` console entry point and `python -m d3dplot`, plus `info`, `variables`, `schema`, `validate`, and `init`. `info`/`variables` read the first map-file header per sub-run for variables and CRS; the initial `validate` scope combined configuration checks, V-VAR name/raw-field availability checks, and simulation discovery without building an inspect cache (D63). `init` writes commented YAML and refuses to overwrite a target. WP 2.1 now adds cache inspection to `validate` and the dedicated `inspect` command (D64).
16. **WP 2.1 implementation** is on `feat/inspect-phase`: partition identity, topology signatures, time-consistent reference selection, coordinate fallback, merge plans, grid geometry, atomic guarded caches, and CLI integration are implemented. Synthetic tests compare scattered values with dfm_tools, cover cache reuse, plain-FM coordinate matching, corrupt reference selection, invalid identity, and changed topologies; validation also builds/reuses caches when earlier checks have no errors. Read-only r209 discovery found 195 sub-runs and no issues in 200.628 s, but the full inspect run did not finish after more than 20 minutes and was stopped at the user's request. Its output was directed to and cleaned from local temporary storage.
17. **WP 2.2 implementation** is complete locally on `feat/map-reader-time-axis`: the face/edge `MapReader`, edge merge plan, `times.nc`, fixed-offset precedence, restart-boundary checks, duplicate resolution, selection, cross-simulation matching, and corrupt-output policies are implemented. Local synthetic tests reproduce all nine rows of `expected/time_axis.csv`, verify face/edge scattering and corrupt-partition metadata, and cover V-OUT-030/031/032/033 and V-TIME-002/010/011/012. P-drive acceptance remains deferred until local-data work is complete (D65), with any final model-data run read-only and its `output.root` on a local drive. **WP 2.2a is complete on `feat/quantity-engine`, commit `e890c45`; WP 2.3 is underway on `feat/execution`.**
18. **WP 2.3 execution foundations** are implemented and locally validated on `feat/execution`: cross-analysis task grouping by simulation/sub-run/time block, stable sharding, fingerprint-policy decisions populated from one-pass NetCDF reads, CPU/memory-aware worker limits, serial and `spawn` executors with failure isolation, worker environment and logging-queue setup, run summaries, and guarded `.dia`/`.log` diagnostics. `validate` and `inspect` record provenance, validation findings, and cache outcomes. `run_process_stage` connects the executor to task diagnostics and guarded atomic run-summary CSV output. The 13 execution tests pass; full end-to-end CLI `run` integration remains.
19. **WP 3.1 map2d processing** is implemented and locally validated on the synthetic SMT case. The processor requests raw and derived quantities from the quantity engine, writes fingerprinted/guarded NetCDF cache files, records quantity choices in `.dia`, and rejects retained vector dimensions with V-VAR-005. The integration test verifies `water_depth`, `froude_number`, `vorticity`, selected `d50`, and `water_level_rate`.
20. **Core WP 3.2 map2d plotting** is implemented and locally validated with synthetic UGRID/cache tests. It consumes map caches only, caches visible-face subsets, reuses draw-once polygon/raster views, applies comparisons and centroid regridding, honours fixed and Matlab-compatible `days_since` limits, snapshots hook changes per image, and routes PNGs through common finalisation and the guarded writer. Measurements and complete CLI `run` wiring remain.
21. **WP 3.3 map overlays** are implemented and locally validated on `feat/map2d-overlays`, commit `53d1bb7`: `.ldb` files use HYDROLIB-core `PolyFile` and a guarded, fingerprinted NetCDF cache; river-km windows and labels use the shared fixed-format reader but retain independent files; and optional contextily backgrounds show provider attribution, persist tiles in an explicitly allow-listed cache, and degrade to V-ENV-002 when offline. Tests cover clipping, cache reuse, input snapshots, independent files, attribution, offline reuse/fallback, and V-LDB-001/V-RKM-001/V-ENV-001/002. All 201 CI-eligible tests, strict mypy (70 source files), strict MkDocs, changed-file Ruff/format, and lockfile checks pass.
22. **WP 3.4 map measurements** are implemented and locally validated on `feat/map2d-measurements`. Follow §7.15 and D16–D17: resolve catalogue and entry paths through `paths.mappings`; parse CSV and YAML catalogues once; inspect source bounds/CRS/type; interpret naive timestamps with `measurements.<variable>.time_zone` or the model's fixed offset; select by time tolerance and spatial margin; apply factors; window, align to the first selected raster grid with nearest-neighbour resampling, and mosaic; filter polygons before drawing; and cache by source/configuration/extent/time. Plot `value` and `difference_time` measurements beside the model with a shared colour scale; do not show measurements for simulation differences. Tests cover YAML/CSV catalogues and metadata-cache reuse, raster factors/mosaics/alignment/cache reuse, CF time-band selection, polygon filters and shuffled-ID differences, missing model CRS, diagnostics, input snapshots, and plot-panel limits/hook visibility. A local 1024×1024 raster preparation took 0.1945 s cold and 0.0285 s from cache. All 211 CI-eligible tests, strict mypy over 73 source files, strict MkDocs, changed-file Ruff/format, lockfile consistency, and diff checks pass. No new behavior decision is needed because these options and acceptance behaviors are already specified in §7.15, §9.2, §7.18, and Appendix A.
22. **WP 3.4 map measurements** are implemented and locally validated on `feat/map2d-measurements`. Follow §7.15 and D16–D17: resolve catalogue and entry paths through `paths.mappings`; parse CSV and YAML catalogues once; inspect source bounds/CRS/type; interpret naive timestamps with `measurements.<variable>.time_zone` or the model's fixed offset; select by time tolerance and spatial margin; apply factors; window, align to the first selected raster grid with nearest-neighbour resampling, and mosaic; filter polygons before drawing; and cache by source/configuration/extent/time. Plot `value` and `difference_time` measurements beside the model with a shared colour scale; do not show measurements for simulation differences. Tests cover YAML/CSV catalogues and metadata-cache reuse, raster factors/mosaics/alignment/cache reuse, CF time-band selection, polygon filters and shuffled-ID differences, missing model CRS, diagnostics, input snapshots, and plot-panel limits/hook visibility. A local 1024×1024 raster preparation took 0.1945 s cold and 0.0285 s from cache. All 211 CI-eligible tests, strict mypy over 73 source files, strict MkDocs, changed-file Ruff/format, lockfile consistency, and diff checks pass. No new behavior decision is needed because these options and acceptance behaviors are already specified in §7.15, §9.2, §7.18, and Appendix A.

---

23. **WP 4.3 processing and WP 4.4 plotting** are implemented and locally validated on `feat/river-km-plotting`: guarded river-km and geometry caches; shared-quantity SMB processing; component-aware statistics and post-processing; and all five plot views, groupings, diagnostic maps, hooks, time averages, and simulation/region comparisons. Synthetic acceptance matches SMB CSVs at float32 precision, checks plotted `r002 − r001` values, verifies fresh-render pixel identity, and includes pytest-mpl baselines for stacked-area and space-time figures. **WP 4.5 profile measurements** is complete locally on `feat/river-km-measurements`: guarded `.mat` v5/v7 conversion and cache, explicit time-zone handling, nearest-time and nearest-km matching, overlays on supported profile/time-series views, time-difference measurements, explicit region-difference measurements, and the no-overwrite `measurements convert` CLI. Synthetic tests cover cache reuse and input immutability, metadata and malformed inputs, time/km tolerances, time-difference values, simulation-difference omission, region-difference overlays, hooks, and pixel-identical fresh rendering. The 283-test CI-eligible suite passed before the final test-only regression additions; the final eight measurement tests and a focused r001 process-to-plot integration test pass. Strict mypy over 86 source files, strict MkDocs, changed-file Ruff/format, and diff checks pass. P-drive acceptance remains deferred per D65.

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

* Model types: **D-Flow FM** (plain or DIMR, partitioned or not) **and SMT** (FM-based). Both are first-class: a plain FM simulation is handled as a simulation with **exactly one sub-run**, so every component (discovery, merge plan, time axis, status, quantity engine, tasks, cache) has one code path for both (§7.5, §7.13). Tests cover both kinds (§13.3).
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
| D21 | **Model output is read through a per-simulation merge plan**. dfm_tools/xugrid build the merged grid once (§7.6) | Measured: 75 s vs 1 s per `r209` sub-run (§5.3) | A (general terms, v6.6) |
| D22 | **The MDU is read with HYDROLIB-core `ResearchFMModel(path, recurse=False)`**, after HYDROLIB-core is modified to accept the missing keywords (§14.1). `.mor`/`.sed` are read with HYDROLIB-core's INI parser until HYDROLIB-core has typed models for them | User: "read the mdu with the right file in hydrolibcore and modify the file to accept morphopol and the other flags"; reuse (R3) | A (v6.2; replaces "tolerant parser") |
| D23 | **Corrupt or inconsistent partitions are detected**. `time`/`morft` come from the partition majority. Affected faces are **masked** (NaN) and every masking is written to the diagnostic file (D41) | `r209` sub-run 0, partition 0021 is corrupt (§5.2); user | A |
| D24 | Times are stored internally as time-zone-aware UTC and shown in the model time zone. Cache file names use the hydrodynamic time | xarray drops the offset; morphological time is not unique (§7.8); user: time zones must be handled | A |
| D25 | When several outputs share a morphological time, select the **last** one, and write the choice to the diagnostic file (D41) | Morphological time repeats during spin-up (§7.8); user | A |
| D26 | Cache values in **float32** by default (`output.dtype: float64` optional). Computations (selections, SMB statistics, differences) are done in float64 in memory; only the stored values are float32 | User: faster reading and writing, enough for visualisation. float32 has ~7 significant digits: 1 mm on a bed level of 1 000 m | A (v6.3; replaces float64) |
| D27 | **Unit of work of the process stage**: one `ProcessTask` per (simulation × sub-run × **block of times**), serving **all analyses** at once. It opens the sub-run's map files once, asks the quantity engine for every quantity any analysis needs at the times of the block (each raw variable read once), and lets each analysis reduce the arrays in memory and write its own files (one per analysis, quantity, selection, time). Only outputs whose fingerprint is missing or stale are computed; tasks with nothing missing are skipped. Tasks run in parallel; SMT parallelises over sub-runs, plain FM over time blocks (§7.13). Revised in v6.5–v6.7; originally one task per (simulation × sub-run × analysis) | Avoids reading the same variable once per analysis; plain FM simulations (one sub-run) also run in parallel (§7.13, §7.9a) | A |
| D28 | Environment and packaging with **uv** + `pyproject.toml` (hatchling) | Already used, fast, pip-compatible; GDAL via rasterio wheels | A |
| D29 | Draw-once plotting with matplotlib `PolyCollection` directly; xugrid only for grid objects and rasterising | Speed (§7.14); visible faces are selected from cached geometry and static artists are reused within each plot group | A |
| D30 | Windows path length: keep paths short and warn above 240 characters | P-drive paths are long; Windows MAX_PATH is 260 | A |
| D31 | No dask in the processing path. Parallelism only at task level, with single-threaded libraries in workers | Avoids nested parallelism and memory blow-ups | A |
| D32 | Configuration: pydantic v2 models; YAML read with `yaml.safe_load`; CLI with typer | Typed, schema export | A |
| D33 | The CRS comes from the configuration when map files have EPSG 0 | `r209` has EPSG 0 | A (v5) |
| D34 | Validation includes a read-only **inspect** phase that builds the metadata cache. No value processing before a clean report; runtime-only checks are listed explicitly (§7.4) | "Validate first" needs metadata (independent review) | A |
| D35 | **Recursive fingerprints** over all result-affecting inputs and upstream artefacts; content hashes for small inputs, stat-based identity for model output (§7.10) | Stale-cache safety (independent review) | A |
| D36 | Merge plans are validated (partition identity, `globalnr`) and reused only for sub-runs with equal **topology signatures** (§7.6) | Safety for SMT (independent review) | A |
| D37 | Deterministic sharding with a blake2b digest of canonical task keys | Python `hash()` is randomised | A |
| D38 | Fixed UTC offsets only (no named time zones with DST); CF numeric UTC on disk | D-Flow FM uses offsets | A |
| D39 | SMB geometry: boundaries inclusive for regions, shared cut lines to the lower segment; invalid or overlapping segments are errors unless overridden | Determinism (independent review) | A |
| D40 | Every output path contains its subject (simulation, simulation_vs_reference, or all_simulations) | No two tasks write the same file | A |
| D41 | **Every run writes a diagnostic file** `logs/d3dplot_<timestamp>.dia` recording what was done: inputs, choices (selected times, duplicates resolved), masked corrupt outputs, reused/rebuilt cache, files written, warnings, errors (§7.19) | User: "make sure a dia file is created with what is being done" | A |
| D42 | **Background tiles with contextily** (as recommended by dfm_tools, which deprecated its own `plot_background`) and contextily's persistent on-disk cache (`tiles_cache_dir`). This replaces Matlab's `earth_tiles` cache (`plotMapTiles`) | User; dfm_tools | A |
| D43 | Land boundaries `.ldb` have the `.pli`/`.pol` (Tekal) format and are read with HYDROLIB-core `PolyFile` | User; verified (§3 F6) | A |
| D44 | **Distribution on PyPI** as `delft3d-plot` (import name `d3dplot`), published from GitHub Actions with PyPI Trusted Publishing on version tags. The names `delft3d-plot`, `d3dplot` and `delft3dplot` were free on PyPI on 2026-10-25 | User | A |
| D45 | The Maas land boundary is recreated from the Matlab cache `j21_ldb.mat` as `.ldb` with `tools/mat_to_ldb.py`, into `data_local/maas/j21_ldb.ldb`. The original polyline names are not in the `.mat`, so blocks are named `L000001…` | User: "recreate it from the mat file"; the source `.ldb` is unknown | A |
| D46 | **Common figure finalisation** for every figure of every analysis (present and future): one function applies fonts and sizes, legend/labels language, user hooks (Matlab `function_handles`), metadata, saving through the writer registry, and closing (§7.14a) | User; Matlab repeats this per figure function (`apply_adhoc_functions`, `fig_print_close`) | A |
| D47 | **Reusable plot building blocks**: the map view (grid values, extents incl. windows along a river-km file, river-km labels from a possibly different river-km file, land boundaries, tiles, measurements panel) and the line view (lines or stacked areas) are shared by M2D, the SMB diagnostic maps and future analyses (§7.14b) | User; Matlab reuses `fig_M2D` for SMB in-polygon maps | A |
| D48 | **Agent guidance**: always-on `AGENTS.md` (principles: plan first, read-only P-drive, reuse HYDROLIB-core/dfm_tools, performance, finalisation, names, time, diagnostics), a plotting file instruction, and two skills (`add-analysis-or-option`, `upstream-contribution`) (§13.6) | User: future agent development must follow the plan | A |
| D49 | The user is a contributor of HYDROLIB-core and dfm_tools. Upstream changes are developed locally in the checkouts; branches, issues and PRs are created when the user decides (§14, skill `upstream-contribution`). Recommended: commit on a **local feature branch** from the start (no access needed); committing on local `main` is possible and can be moved to a branch later | User | A |
| D50 | **Quantity engine**: one central service resolves requested quantities (raw or derived, e.g. Froude, vorticity), reads each raw input once per (sub-run, time) through the merge plan, computes derived quantities in dependency order, and is the only reader of model output for all analyses (§7.9a). The merge plan includes edges for edge-located inputs | User; Matlab `gdm_read_data_map_simdef` | A |
| D51 | HYDROLIB-core PR 1 scope: **6 `[sediment]` keywords** (`dredgeFile`, `morCFL`, `DzbDtMax`, `morphoPol`, `inMorphoPol`, `upperLimitSSC`) and **3 `ResearchGeometry` keywords** (`circumcenterMethod`, `circumcenterTolerance`, `calculateBedLevelOverNonActiveLinks`); the other missing `[sediment]` keywords will be deprecated and are not added (§14.1) | User; agreed with the HYDROLIB-core maintainer | A |
| D52 | Second acceptance case: the Rijn **plain FM** run `D16000` (§5.5, §13.4a) | User | A |
| D53 | **Physical constants from the model**: `g` (MDU `Ag`), `ρ` (MDU `Rhomean`) per simulation, `ρ_s` per sediment fraction (`.sed` `RhoSol`); D-Flow FM defaults when absent; values written to the `.dia` (§7.9a) | User | A |
| D54 | **All quantities of the §7.9a catalogue are in v1**, including the 3D ones (layer mean, chloride mass, residence times, vertical selections), and the stream functions. Exceptions: `morphodynamic_celerity` (ECT is not ported or coupled for now) and the section quantities, which belong to PRF (D55) | User | A |
| D55 | **Reading along a line (plot type PRF) is a second phase.** v1 reads quantities on the whole grid only (faces, edges internally). The engine already takes a `Sampling` argument (§7.9b) so that line sampling, the section quantities and the PRF analysis can be added later without changing the engine, the store or the analyses of v1 | User | A |
| D56 | `Geometry.inifieldfile` accepts a `DiskOnlyFileModel` as well as `IniFieldModel` when `recurse=False`, preserving path-only loading and allowing `ResearchFMModel(path, recurse=False)` to load MDUs that reference an initial-fields file | Reproduced on the synthetic MDU and real `r209` before the new keywords are reached; user approved this WP 0.1 scope expansion on 2026-10-26 | A |
| D57 | Time selection uses one nested `times` object: `at` selects `all`, `last`, explicit times, indices, or a range; `axis` selects hydrodynamic or morphological time; `tolerance` controls matching. Measurement-specific tolerances remain scoped to their measurement settings | User selected the §10.2 nested YAML shape over the separate Appendix A names | A |
| D58 | The map rendering mode is `map.render` in the shared `MapViewConfig`; `figure` contains global figure styling and output settings | User selected the shared map-settings path over `figure.render` | A |
| D59 | WP 1.3 validates and exports schemas for the built-in analyses only. Plugin config discovery, validation, and schema integration are delivered with the `d3dplot.analyses` entry-point registry | Keep WP 1.3 bounded and define the plugin config contract alongside the analysis registry | A |
| D60 | When both are configured as lists, `figure.x_limits` and `figure.y_limits` must have the same number of rows (V-CFG-004) | User selected row-count validation for the two per-figure axis-limit lists | A |
| D61 | Simulation discovery uses a bounded thread pool (up to 64 workers) for I/O-bound sub-run tasks; small cases remain serial, and netCDF4 access is serialized | Read-only r209 discovery with 64 workers found 195 sub-runs and 8,580 map files without issues in 99.283 s; 16 workers took 131.981 s | A |
| D62 | WP 1.10 provides metadata for v1 quantities before the engine exists: resolve canonical names, Matlab aliases, and registered NetCDF names case-insensitively while preserving candidate spelling for file reads; unknown names raise V-VAR-001 with up to three canonical suggestions. Compute callbacks and dependency execution are added in WP 2.2a/b | The registry and CLI need stable names before model-output processing is implemented | A |
| D63 | At the WP 1.11 stage, `info`/`variables` inspected only the first map-file header per sub-run and `validate` combined config checks, variable-name/raw-field availability checks, and discovery without building inspect caches; `init` refused to overwrite an existing target. An unregistered raw NetCDF name is accepted only when present in the selected simulation | Deliver useful CLI commands before the merge-plan/inspect phase existed, and protect existing user configuration | A |
| D64 | WP 2.1 adds `inspect cfg.yaml [--simulation label]` to build or reuse guarded grid and merge-plan caches; `validate` runs the same inspect phase only when earlier config, discovery, and variable checks have no errors. `info`/`variables` remain read-only | Expose the output-inspection phase consistently while avoiding cache writes for already-invalid project inputs | A |
| D65 | Develop and run automated acceptance against local synthetic data first. Defer P-drive acceptance runs until the planned local-data work packages and tests are complete; then perform the required model-data acceptance read-only, with `output.root` on a local drive. This changes sequencing, not acceptance requirements | User proposed avoiding expensive network-model runs during development while retaining final real-data verification | A |
| D66 | With `times.tolerance: null`, explicit time requests select the nearest available output without a distance limit. A configured tolerance remains a hard maximum distance | User selected unbounded nearest-output matching for the unset default | A |
| D67 | Build the edge merge plan and edge reader in WP 2.2; WP 2.2a consumes that plan for edge quantities | User selected implementation in WP 2.2 to resolve the overlap between the work-package descriptions | A |
| D68 | Match Matlab's velocity direction as degrees clockwise from geographic north (`mod(degrees(atan2(u_x, u_y)), 360)`). Match its grain-size percentile calculation by linearly interpolating in diameter between cumulative fractions, with endpoint diameters `D_1/2` and `2·D_n`; return NaN when the fraction sum is outside `1 ± 0.001` | Verified against `EHY_getMapModelData` and `grain_size_dX_mat`/`grain_size_dX`; the catalog requires these Matlab conventions | A |
| D69 | Preserve Matlab time-selector semantics in `times.at`: `NaN` anywhere in a selector vector selects all outputs; positive `Inf` selects the last output and may be combined with one-based integer indices; integer selectors are one-based (`1` is the first output). Python configuration also accepts the readable `all` and `last` spellings and `{indices: [...], include_last: true}` for the mixed form | User requested Matlab behavior and selected one-based indexing for both integer forms | A |
| D70 | `ba_mor` is `morphological_active_area`, not morphological volume: return face area when total sediment thickness is at least `active_sediment_thickness_threshold_m` (default `0.001 m`), otherwise zero. The threshold is configurable per analysis | Matlab `gdm_read_data_map_ba_mor` applies `Ltot_thres = 1e-3` and zeros area only when `Ltot < Ltot_thres`; user clarified the physical meaning and requested a configurable threshold | A |
| D71 | Water-level rate uses the interval between consecutive map outputs. When `mesh2d_s0` is available, compute `(s1 − s0)/Δt`; otherwise compute `(s1[current] − s1[previous])/Δt`. The first output has no preceding map interval and returns NaN. The source choice is recorded in the diagnostic file | User clarified the reference behavior after the Matlab reader's `timestep` use was found to conflict with the original wording | A |
| D72 | `color_limits_mode: {days_since: date}` sets each frame's colour limits to `[0, max((time_UTC − date_at_UTC_midnight).days, 0)]`, retaining fractional days and overriding configured numeric bounds | Matches Matlab `gdm_plot_M2D.fcn_clims_type` (`clims_type=2`) | A |
| D73 | When hooks are configured, `figure.hooks_mode` defaults to `rebuild`: create a fresh figure for every image so arbitrary hook mutations cannot leak between frames. With no hooks, continue reusing the draw-once template. `per_image` remains an explicit opt-in to snapshot/restore of common mutations | User selected rebuilding hooked images as the safe behavior; snapshots cannot undo every possible Matplotlib mutation | A |
| D74 | Built-in river-km providers are versioned package data for Rijntakken, Maas, and generic rivers. Provider dispatch uses the fixed-file branch tags case-insensitively; provider rules resolve track branches and bend cutoffs, while generic multi-branch files require an explicit branch assignment. `branches` accepts one branch to broadcast or one branch per requested km centre | Keep provider data separate from geometry code as required by D15 and WP 4.1; make branch selection deterministic | P |
| D75 | WP 4.2 prepares region and segment membership/check results; diagnostic maps are rendered in WP 4.4 through the shared `MapView` and common finalisation, not by a separate WP 4.2 plot | Reconcile the WP 4.2 package row with the shared-view diagnostic-map specification in WP 4.4 (D46–D47) | P |
| D76 | V-SMB-011 coverage is measured only for region face centres between the perpendicular endpoint planes through the first and last river-km edges; lateral distance from the centreline is unrestricted for this range test | Detect an undersized corridor without warning for region faces beyond the configured km span | P |
| D77 | WP 4.2 exposes deterministic geometry and assignment results in memory; WP 4.3 wires these into the guarded NetCDF geometry caches together with the SMB prepare stage | The execution layer does not yet provide an analysis `GeometryTask`; keep this package independently testable and avoid adding an unused writer | P |
| D78 | `segment_membership` figure bounds are the segment-polygon bounds padded by 10% of the larger dimension on every side | The diagnostic specification requires a segment-centred window with margin but no numeric margin; this deterministic rule keeps neighbouring geometry visible | P |
| D79 | Configured SMB time-average selections are supplementary selections in the shared process plan; duplicate output indices are merged so the quantity engine reads each output once | Plot-stage averages must have processed caches for every selected output without creating duplicate processing work | P |
| D80 | Plotters treat both the declared UGRID fill value and `-1` as face-node padding; other negative indices remain invalid | Inspection normalizes masked connectivity to `-1`, while xugrid may expose a different declared `fill_value` for the same cache | P |
| D81 | Standalone `measurements convert` requires a fixed `--time-zone`; analysis loading uses `ProfileMeasurementsConfig.time_zone` or the inspected model offset. Conversion writes guarded, no-overwrite NetCDF files and never modifies `.mat` inputs | MATLAB datenums contain no timezone, while standalone conversion has no model time axis from which to inherit the offset (D18) | P |
| D82 | The synthetic Matlab comparison uses `qsp` for derived-map parity. Matlab's vorticity helper currently returns 2,800 values while `grd.mat` exposes 6,406 face centres, without a corresponding subset index; defer vorticity parity until that mapping is available | Observed while generating the v1 synthetic Matlab references; do not compare a partial, unmapped field to the full Python grid | P |

## 3. Review of the plan (v6)

This round re-checked every earlier decision against real data (`r209`, read-only) and the libraries. Each change is reflected in the relevant section.

| # | Finding (verified) | Impact | Change |
|---|--------------------|--------|--------|
| F1 | `dfm_tools.open_partitioned_dataset` on one `r209` sub-run (44 partitions, 501 230 faces): **75 s to open + 10 s to load one field**. Direct reading of the same field with a precomputed merge plan: **1.4 s once + 1.0 s per field**. Values identical | With 195 sub-runs: hours instead of minutes | D21, §7.6 |
| F2 | HYDROLIB-core `FMModel(..., recurse=False)` **rejects the real MDU**: 13 unknown keywords. `ResearchFMModel` rejects only 7 (5 standard `[sediment]` keywords missing upstream, 2 deprecated `[geometry]` keywords). There is no switch to relax the check | The strict model cannot read real morphological MDUs today | D22 (v6.2): read with `ResearchFMModel` after modifying HYDROLIB-core (§14.1); §5.4 |
| F3 | `r209` **sub-run 0, partition 0021**: map times 2–3 have `time` ≈ −5·10⁻¹⁷⁹, `morft` ≈ −5·10²²⁵ and bed levels ≈ ±10³⁰⁸. The other 43 partitions are correct. dfm_tools fails to merge the sub-run ("duplicate time values") | Real output can be corrupt. Matlab results were not affected (they used time 1 only) | D23, §7.6, checks V-OUT-*, synthetic variant `corrupt_partition` |
| F4 | xarray decodes `seconds since 2021-02-08 00:00:00 +01:00` to time-zone-naive UTC (`2021-02-07T23:00`) | Off-by-one-hour errors in names and matching | D24, §7.8 |
| F5 | Morphological time is constant during spin-up: synthetic case exactly; `r209` 0 → 11.5 s while the hydrodynamic time advances 86 400 s | Selecting by morphological time is ambiguous | D25, §7.8 |
| F6 | HYDROLIB-core `PolyFile` reads `.ldb`: about 7.7 s per 10⁶ points (`j21` land boundary: 2.5·10⁶ points ≈ 20 s); a NumPy reader takes 0.7 s | Acceptable once; cache the parsed result | D20, §7.16 |
| F7 | `r209` map files: EPSG 0; MDU `RefDate = 20121231` while the map time units say 2021-02-08; "Computation finished" only in partition `.dia` files; repeated boundary time between sub-runs | Already in v5 | §5 |
| F8 | float32 vs float64 cache. v6 chose float64 for exact comparisons; v6.3 (user) returns to **float32** for speed, with float64 computation in memory. Tests compare with tolerances (§13.2) | Comparisons use tolerances, not equality | D26 |
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
| `FMModel` (standard model) for reading the MDU | Does not know the research keywords used in real MDUs; `ResearchFMModel` is used instead, after the upstream fix (D22, §14.1) |
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

**Provider implementation and Matlab flags.** Provider tags, track thresholds, cutoffs, and `ds_pol_km` are stored in package data, not embedded in geometry code. Provider matching is case-insensitive. The Maas switch is `MA` below 230.5 km and `BM` at or above 230.5 km only when `BM` occurs in the file; files without `BM` use `MA`. On a Waal track, the `BO` and `NI` transitions are used only when those branches occur in the file; otherwise the points remain on `WA`. A single explicit branch is broadcast, or `branches` may supply one value per requested centre. A Rijntakken `track` takes precedence over explicit branches, matching `gdm_load_rkm_polygons.m`.

The Matlab checker maps legacy tag `fig_map_summerbed_01` to `SMB` and reads no geometry flags. `gdm_create_mat_SMB.m` and `gdm_plot_SMB.m` call `gdm_parse_summerbed.m`: `sb_pol` and `fpath_rkm` are required; `rkm_name` and `rkm` are required; `rkm_br` and `rkm_track` default empty, and one must supply a branch or track. `gdm_load_rkm_polygons.m` defaults its optional `parallel_distance` to 1000 m and `ds_pol` to 0.1 km. `do_plot_inpoly` defaults false for the per-segment diagnostic. The remaining `do_*`, `tim_tol`, and plotting-limit defaults in `gdm_parse_summerbed.m` apply to processing/plotting work packages, not provider geometry.

**Algorithm** (per region × km set; distances in m, km in km):
1. **Centres**: `c_1..c_n`, the requested km values; not necessarily equidistant.
2. **Edges and lengths**: `e = cen2cor(c)`, the midpoints with extrapolated ends (`e_1 = c_1 − Δ_1/2`, `e_{n+1} = c_n + Δ_{n−1}/2`). `Δs_i = e_{i+1} − e_i` (km), from the original centres.
3. **Bend cutoffs** (provider data; today IJssel 891.9–896.0, 902.5–903.0, 905.3–910.0):
   * centres inside a cutoff are removed;
   * after filtering, edges are rebuilt as `c_i − Δs_i/2` for each retained centre and `c_n + Δs_n/2` for the final edge, even when no centre was removed; the retained `Δs_i` remain the segment lengths used by statistics;
   * an edge `q` inside a cutoff interval `(a, b)` (tolerance `0.01` km at both ends: `a + 0.01 < q < b − 0.01`), with `c` the centre of its segment, is mapped as in `correct_for_bendcutoff.m`:
     * if `q > c`: `q' = b + (q − a) − 0.1`;
     * else: `q' = a + (q − b) + 0.1`.
     
     The constant `0.1` km is Matlab's `ds_pol`. The provider data holds it per river.
4. **Edge coordinates**: each edge has a branch (from the provider). Its x, y are interpolated linearly in km between the two bracketing points of **that branch** in the file. At a branch transition the two edges of a segment can lie on different branches; the segment polygon is still built from its two edge cut lines, as in Matlab.
5. **Direction**: `θ_k = atan2(y_{k+1} − y_{k−1}, x_{k+1} − x_{k−1})`. The ends copy the angle of their neighbour; with 2 edges, both use the direction between them.
6. **Cut lines**: `L_k = e_k + W·(cos(θ_k+π/2), sin(θ_k+π/2))`, `R_k = e_k − W·(…)`. `W` = `half_width_m` (Matlab default 1000).
7. **Segment polygons**: `P_i = [L_i, L_{i+1}, R_{i+1}, R_i]`.
8. **Region membership**: the face centre is inside the region polygon (shapefile; multi-part allowed). Multipart geometries are unioned, holes are preserved, and an optional concave hull builds one polygon from unordered parts (Matlab `boundary(…, 0.8)`, default off). Region CRS is retained and transformed to the model CRS when available; a missing source CRS or failed transformation is V-SMB-002.
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
* coverage: region faces between the perpendicular endpoint planes through the first and last river-km edges that are in no segment (`W` too small). Warning V-SMB-011 with the count; points on a shared cut line are assigned to the lower index and are not reported as an overlap;
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
| Land boundary | `…\C_Work\00_data\05_landuse\j21_ldb.mat` (Matlab cache; the source `.ldb` is unknown). Recreated as `data_local\maas\j21_ldb.ldb` (D45), in the format of `…\maas-j21_6-v1_20m_km002_085_cc\geometry\maas-j21_6-v1_20m_km002_085_cc_thd.pli` |

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

`ResearchFMModel` (`hydrolib.core.dflowfm.research.models`, which also accepts research keywords) gets much further. It fails only on:
* `[geometry]` `circumcentermethod`, `circumcentertolerance`: deprecated keywords; D-Flow FM itself only warns about them (seen in the `r209` `.dia`);
* `[sediment]` `dredgefile`, `morcfl`, `dzbdtmax`, `morphopol`, `inmorphopol`: valid morphology keywords that are missing from the model.

Related upstream issues:
* [#931](https://github.com/Deltares/HYDROLIB-core/issues/931) (obsolete/deprecated keywords; its to-do list includes "unknown keywords → warning, later error, once all keywords are in `mdu.json`");
* [#643](https://github.com/Deltares/HYDROLIB-core/issues/643) (strict unknown-keyword checks for other files);
* [#788](https://github.com/Deltares/HYDROLIB-core/issues/788), [#648](https://github.com/Deltares/HYDROLIB-core/issues/648) (precedents: missing MDU keywords added).

No issue existed (2026-10-25) for the missing `[sediment]` keywords.

**Cross-check with the official MDU specification** (`Deltares/Delft3DFM.File.Specifications`, `specifications/dflowfm/mdu.json`, commit `a4d5827`) of all 13 keywords rejected by `FMModel` in the `r209` MDU:

| Section | Keyword (spec spelling) | Spec type, default | Spec status | HYDROLIB-core today | Needed change |
|---------|-------------------------|--------------------|-------------|---------------------|---------------|
| sediment | `dredgeFile` | path, `''` | standard | missing | add to `Sediment` |
| sediment | `morCFL` | intbool, `1` | standard | missing | add to `Sediment` |
| sediment | `DzbDtMax` | float, `0.1` (m) | standard | missing | add to `Sediment` |
| sediment | `morphoPol` | path (`*.pol`), `''` | standard | missing | add to `Sediment` |
| sediment | `inMorphoPol` | intenum {0, 1}, `1` | standard | missing | add to `Sediment` |
| geometry | `circumcenterMethod` | enum {`internalNetlinksEdge`, `internalNetlinksLoop`, `allNetlinksLoop`}, `allNetlinksLoop` | **deprecated** (2026-03-01) | missing | accept, warn as deprecated |
| geometry | `circumcenterTolerance` | float, `1e-15` (m) | **deprecated** (2026-03-01) | missing | accept, warn as deprecated |
| output | `ncCompression` | intbool, `0` | standard | research only | move to `Output` (optional, 2nd PR) |
| output | `wriMap_trachytopes` | intbool, `0` | standard | research only | move to `Output` (optional, 2nd PR) |
| time | `tStartTlfsmo` | float, `0.0` (tUnit) | standard | research only | move to `Time` (optional, 2nd PR) |
| output | `wrirst_bnd` | intbool, `1` | research | research | none |
| output | `writedfminterpretedvalues` | intbool, `0` | research | research | none |
| restart | `rstignorebl` | intbool, `0` | research | research | none |

With the 5 `[sediment]` and 2 `[geometry]` changes, `ResearchFMModel` reads the `r209` MDU (PR 1, §14.1, adds these plus `upperLimitSSC` and `calculateBedLevelOverNonActiveLinks`). The last 6 keywords are already covered by `ResearchFMModel`, which is why D22 uses it rather than `FMModel`.

### 5.5 Reference: plain D-Flow FM run (Rijn, `D16000`; read-only)

Second real case, a **regular (non-SMT) D-Flow FM simulation** (acceptance case 2, §13.4):

| Item | Value |
|------|-------|
| Folder | `p:\11212773-003-26mad09-rijn\C_Work\818_dflowfm2d-rijn-beno24_6-v1a\01_standaardsommen\computations\test\D16000\` |
| MDU | `RIJN.mdu` (plus partition MDUs `RIJN_0000…0003.mdu`) |
| DIMR | `dimr_config.xml` with **two components**: `FlowFM` (`dflowfm`, `RIJN.mdu`, processes 0–3) and `Real-Time Control` (`FBCTools_BMI`, working dir `../../../rtc/rtc_ini1`). Discovery must select the `dflowfm` component, not the first one |
| Net file | `rijn-beno24_6-v1a_net.nc` (270 MB) and partition net files `…_000p_net.nc` |
| Time | `RefDate = 20000101`, `Tunit = M`, `Tzone = 1`, run defined by `StartDateTime = 20000101000000` / `StopDateTime = 20000119000000` (instead of `TStart`/`TStop`; `MduInfo` must handle both) |
| Output | `results/`: 4 partition map files (≈ 400 MB each), `RIJN_0000_his.nc` (3.2 GB, partition 0 only), 4 `*_fou.nc`, `.dia` files |
| Map | `MapInterval = 14400 s` → **109 map times**; units `seconds since 2000-01-01 00:00:00 +01:00`; EPSG 0; faces per partition ≈ 131 000 (≈ 523 600 owned in total), up to 6 nodes per face (hexagons), 268 651 edges in partition 0 |
| Map variables (time-dependent) | `mesh2d_s1`, `mesh2d_waterdepth`, `mesh2d_ucx`, `mesh2d_ucy`, `mesh2d_ucmag`, `mesh2d_u1`, `mesh2d_cfcl`, `mesh2d_cftrt`, `mesh2d_dtcell`, `mesh2d_Numlimdt`; **no morphology** (no `morft`, no `mesh2d_mor_bl`; bed level from `mesh2d_flowelem_bl`) |
| Status | "Computation finished" in the 4 partition `.dia` files, not in `RIJN.dia` (same pattern as `r209`) |
| HYDROLIB-core | `FMModel` rejects 8 keywords (`[geometry]` 3, `[output]` 5); `ResearchFMModel` rejects only `[geometry]` `calculateBedLevelOverNonActiveLinks`, `circumcenterMethod`, `circumcenterTolerance` → added in PR 1 (§14.1) |

What it tests: plain FM discovery (DIMR with several components), `StartDateTime` handling, time-block parallelism (109 times, §7.13), hexagonal faces, a hydrodynamic-only run (`times.axis: morphological` must be rejected, V-TIME-012), derived quantities from velocity (Froude, vorticity from `mesh2d_u1`).

## 6. Libraries and tools

### 6.1 Reused domain libraries

| Need | Library and API | Notes |
|------|-----------------|-------|
| MDU | HYDROLIB-core `hydrolib.core.dflowfm.research.models.ResearchFMModel(path, recurse=False)` | D22. Requires the HYDROLIB-core change of §14.1 for real morphological MDUs |
| `.mor`, `.sed` | HYDROLIB-core `hydrolib.core.dflowfm.ini.parser.Parser.parse_as_dict` | Until typed models exist upstream (§14). `.mor` values arrive as `"10   [-]   comment"`: take the first token |
| `dimr_config.xml` | HYDROLIB-core `hydrolib.core.dimr.models.DIMR` (`component[i].inputFile`, `.filepath`) | Verified on the synthetic case |
| `.ldb`, `.pli`, `.pol` | HYDROLIB-core `hydrolib.core.dflowfm.polyfile.models.PolyFile` | 7.7 s/10⁶ points; cached (§7.16) |
| Windows/Linux paths in model files | HYDROLIB-core `FilePathStyleConverter`, `PathStyle` | |
| Merged grid, ghost cells (once per simulation) | dfm_tools `open_partitioned_dataset` (xugrid `merge_partitions`) | Only to build the grid and verify the merge plan (D21) |
| Grid object, topology, plotting helpers, rasterising | xugrid `Ugrid2d`, `UgridDataset`, `ugrid.rasterize` | |
| Regridding between simulations | xugrid regridders (`CentroidLocatorRegridder`, `OverlapRegridder`) | Only when grids differ |
| Layers, depth average, z-coordinates (3D, v1, D54) | dfm_tools `reconstruct_zw_zcc`, `get_Dataset_atdepths` | Needed by the 3D quantities of §7.9a and the vertical selections |
| Faces crossed by a line (PRF, later) | dfm_tools `polyline_mapslice` | `LineSampling` (§7.9b) |
| D-WAQ substance file (`.sub`) | HYDROLIB-core `SubstanceModel` | Decay rates for residence times (§7.9a) |
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
| Background tiles | contextily (optional extra `tiles`) with its persistent cache (D42) | dfm_tools `plot_background` (deprecated in favour of contextily); Matlab `earth_tiles` cache (replaced) |
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
│  ├─ base.py               ConfigModel, fixed-UTC-offset and duration types
│  ├─ project.py            ProjectConfig, SimulationConfig, OutputConfig, ExecutionConfig, LabelsConfig
│  ├─ analysis.py           AnalysisConfigBase, shared fields and processing policies
│  ├─ selections.py         Times, Vertical, Fractions
│  ├─ figure.py             FigureConfig (style, formats, hooks_mode, line limits), MapViewConfig (map settings including render)
│  ├─ measurements.py       MapMeasurementsConfig, PolygonFilters, ProfileMeasurementsConfig
│  └─ io.py                 load/save YAML/JSON, resolved config, JSON schema export, --set overrides
├─ model_input/
│  ├─ ini.py                INI reading for .mor/.sed (HYDROLIB-core Parser) + value-token parsing
│  ├─ mdu.py                MduInfo (typed adapter: times, output, files, morphology)
│  ├─ mor.py, sed.py        MorphologyInfo, SedimentInfo (fractions, diameters)
│  └─ dimr.py               find the MDU from dimr_config.xml
├─ simulation/
│  ├─ discover.py           folder → FmSimulation | SmtSimulation; MDU selection rules
│  ├─ subrun.py             SubRun (folder, MduInfo, partitions, output files)
│  ├─ partitions.py         partition file discovery; MergePlan (faces and edges) build/load/verify
│  ├─ reader.py             MapReader: read(raw variable, subrun, time_index) → global face/edge array (only used by the engine)
│  ├─ timeaxis.py           TimeAxis (merged SMT axis, duplicate handling, selection, matching)
│  ├─ status.py             SimulationStatus detection (.dia tails, work folder, last time vs TStop)
│  └─ quality.py            corruption/consistency checks of output (time/morft/values)
├─ variables/
│  ├─ registry.py           VariableSpec registry: raw and derived specs (names, aliases, NetCDF candidates, inputs, dims, units, label key)
│  ├─ engine.py             QuantityEngine: dependency graph, read each raw input once, compute derived, per-task memory cache (§7.9a)
│  ├─ derived/              derived quantities: hydrodynamics.py (Froude, specific discharge, velocity vector),
│  │                        spatial.py (vorticity, …; uses grid geometry), sediment.py (mean grain size, transport magnitude)
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
│  ├─ base.py               Analysis protocol and entry-point registry ("d3dplot.analyses"); plugin config models join validation/schema here (D59)
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
│  ├─ finalize.py           finalize_figure: style, language, hooks, metadata, save, close (§7.14a)
│  ├─ views/                MapView, MapDecorations, ExtentSet, MeasurementPanel, LineView (lines/stacked areas), SpaceTimeView (§7.14b)
│  ├─ templates.py          draw-once groups on top of the views
│  ├─ style.py              fonts, sizes, colour orders
│  ├─ map.py, profile.py, space_time.py, time_series.py   analysis-specific compositions of the views
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
5. **Write provenance**: resolved configuration, run summary (counts, durations, skipped/failed tasks), status tables, logs. Plot commands save both `config/run_<timestamp>.yaml` and `config/latest.yaml` under the reference output root; configuration paths are resolved before saving so the file can be replayed from any working directory.

**Runtime-only checks** (they need values, so they cannot run before processing): V-OUT-031 (implausible values), and SMB statistics on empty segments. They are reported as warnings in the run summary and never silently ignored.

### 7.5 Simulation discovery and model input

**Folder → kind**:

| Condition | Kind |
|-----------|------|
| `smt.yml` exists and `output/<integer>/` folders exist | SMT |
| `dimr_config.xml` with an FM component | FM (the MDU from `inputFile` of the component whose `library` is `dflowfm`, relative to `workingDir`; other components such as RTC are ignored, §5.5) |
| exactly one `*.mdu` that is not a partition MDU (`*_\d{4}.mdu`) | FM |
| otherwise | error V-SIM-001 |

**SMT**:
* sub-runs are `output/<n>` sorted **numerically** (not alphabetically: `10` comes after `9`);
* the numbering must be contiguous (V-SIM-010);
* inside each sub-run the MDU is chosen with the FM rule.

Inside an SMT sub-run, relative paths in the MDU are resolved against the sub-run folder (e.g. `SedFile = ../../sed/…`).

**MDU adapter** (`MduInfo`): a thin, read-only view on HYDROLIB-core's `ResearchFMModel(path, recurse=False)` (D22). With `recurse=False`, referenced files are not loaded; file references are `DiskOnlyFileModel` objects whose `.filepath` is resolved relative to the MDU folder. If HYDROLIB-core rejects an MDU (for example a keyword from a newer kernel), validation reports V-MDU-001 with the unknown keywords and section, and the hint to update HYDROLIB-core or report the keyword upstream.

| Field | HYDROLIB-core attribute | Notes |
|-------|-------------------------|-------|
| `refdate` | `model.time.refdate` | `yyyymmdd` (int) |
| `tunit` | `model.time.tunit` | `S`, `M`, `H`, `D` |
| `tstart`, `tstop` | `model.time.startdatetime`/`stopdatetime` when set (`yyyymmddHHMMSS`), otherwise `model.time.tstart`/`tstop` | `tstart`/`tstop` are in `tunit` from `refdate`; the Rijn run uses `StartDateTime`/`StopDateTime` with `Tunit = M` (§5.5) |
| `tzone` | `model.time.tzone` | hours |
| `output_dir` | `model.output.outputdir` | default `DFM_OUTPUT_<stem>` |
| `map_file` | `model.output.mapfile` | default `<stem>_map.nc`; partitions `<stem>_<pppp>_map.nc` |
| `net_file` | `model.geometry.netfile` | |
| `mor_file`, `sed_file` | `model.sediment.morfile`, `model.sediment.sedfile` | then `.mor` → `MorFac`, `MorStt`; `.sed` → per fraction `Name`, `SedTyp`, `SedDia` or `SedMinDia`/`SedMaxDia`, **`RhoSol`** (D53) with the INI parser |
| `gravity`, `water_density` | `model.physics.ag`, `model.physics.rhomean` | D53; defaults 9.81 and 1000 when absent |

**Output files**:
* partitioned: `<output_dir>/<stem>_<pppp>_map.nc` with consecutive `pppp`;
* otherwise: `<stem>_map.nc`.
* Discovery opens each map header read-only. NetCDF3 classic/64-bit-offset headers use SciPy's memory-mapped reader; other formats use netCDF4. A header that cannot be parsed reports V-OUT-002.

The partition count must be equal in all sub-runs (V-OUT-003).

### 7.6 Reading model output: merge plan (D21, D23)

**In plain words.** A partitioned D-Flow FM run writes one map file per processor (44 for `r209`). Each file holds its own piece of the grid plus a strip of neighbouring cells that belong to other files ("ghost cells"). To get one value per cell of the whole model, the pieces must be glued together: drop the ghost cells, then put every remaining cell at its place in the full grid.

dfm_tools does this gluing, but it rebuilds the whole grid every time a sub-run is opened: 75 s for one `r209` sub-run, 195 times. Yet the grid is the same in every sub-run and at every time; only the values change. So the tool does the expensive part **once per simulation** and stores the result. That stored result is the *merge plan*: for each map file, the list "local cell number → position in the full grid". Afterwards, getting the bed level of any sub-run and time means reading that variable from the 44 files and placing the values with the plan: about 1 s instead of 85 s. dfm_tools is still used to build the full grid once, and the result is checked against it.

The rest of this section specifies how the plan is built, validated and used.

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

**Edges** (WP 2.2, D67): map each partition's edge endpoints to the merged grid's nodes by coordinates within 1 mm, then match the endpoint pair to one merged edge. For partitioned output, an edge belongs to the lowest-numbered domain among its adjacent faces; this deterministically selects one copy, including at partition boundaries. A non-partitioned file owns every edge. Store the edge orientation relative to the merged UGRID edge direction, and verify every merged edge is covered exactly once. The edge topology is included in the partition signature so a plan is not reused if local edge ordering changes. Directed edge quantities can apply the stored orientation in the quantity engine.

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
  * `mask` *(default; agreed)*: the faces owned by that partition are set to NaN. Each occurrence is written to the diagnostic file (D41) with sub-run, partition, map time and the number of masked faces. It is also counted in the run summary;
  * `error`: the task fails with a clear message;
  * `skip`: that time is dropped from the analysis (also written to the diagnostic file).
* Plausibility of values: `|v| > 1e30` or non-finite in a partition that is not marked → V-OUT-031 (warning during processing; the face values are masked).

### 7.7 SMT time axis

* Per sub-run, the map times are the reference values of §7.6 (majority of the partitions).
* **Merged axis**: all `(subrun, local_index)` outputs, ordered by hydrodynamic time.
* **Boundary duplicates** (same hydrodynamic time within 1 s, from consecutive sub-runs, i.e. a restart): keep the entry of sub-run *k* (the end of the run that produced the state) and drop the one of *k+1*. Their `morft` must also agree within 1 s; otherwise V-OUT-032 (error). A value check (bed level of the lowest-numbered partition that is healthy at both outputs) is on by default (`check_boundary_duplicates: true`, V-OUT-033, warning); values are aligned by `globalnr` when available, and finite values must agree exactly.
* `cache/times.nc`, per merged entry: `subrun`, `local_index`, `time`, `morph_time`, plus `corrupt_partition(time, partition)`. The `time` coordinate is CF numeric UTC seconds since the Unix epoch; `morph_time` uses the same units and calendar. Entries without a majority hydrodynamic time are not usable and are omitted. The cache records its fingerprint, schema, and the simulation's source UTC offset.
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

**Selecting times**, on `times.axis` (`hydrodynamic` | `morphological`):
* The nested `times` object has `at`, `axis` and `tolerance` fields (D57). `at` accepts `all` (`NaN`), `last` (positive `Inf`), a one-based integer index, an explicit list of times, `{indices: […]}` with one-based indices, or `{start, end, step}` (D69). The readable `all` and `last` forms are preferred in YAML; NaN/Inf aliases are accepted for Matlab-compatible input.
* In the WP 1.3 models, `at` defaults to `all`, `axis` to `hydrodynamic`, and `tolerance` is unset (`null`). With `tolerance: null`, an explicit request selects the nearest output without a distance limit (D66); a configured tolerance restricts candidates to that maximum distance.
* For each explicit time, select the nearest candidate on the configured axis. If a configured tolerance excludes every output, report V-TIME-010 and list the nearest available times. An empty usable axis also reports V-TIME-010.
* **Duplicates** (one rule for every case): candidates whose times on the selection axis differ by less than `duplicate_tolerance` from the nearest candidate form a duplicate group. Default: 60 s on the morphological axis, 1 s on the hydrodynamic axis. The 60 s default covers the spin-up drift observed in `r209` (11.5 s), while distinct morphological outputs are at least `MorFac × map interval` apart (days in practice). Within a group, the **last** output in hydrodynamic order is selected (agreed: the state after the spin-up of that sub-run). `duplicate_morphological_times: "first"` remains available for comparisons with other tools. Every resolved duplicate is written to the diagnostic file (D41): requested time, candidates with both times, and the chosen output. Boundary duplicates are already removed by §7.7, so this rule only sees distinct outputs.
* The selected outputs are listed in the run summary with both times, so the choice is visible.

**Matching between simulations** (`time_matching`): `reference` (default; every simulation uses the reference simulation's selected times, matched within `times.tolerance` with the same duplicate rule) or `own`.

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

Raw NetCDF names are always accepted. Names outside the static registry are passed through as direct raw fields when present in the selected simulation; a missing unregistered name is V-VAR-001. The quantity engine will add contextual dimension and location metadata when `MapReader` is implemented.

The registry resolves canonical names, Matlab aliases, and registered NetCDF candidate names case-insensitively; it preserves each candidate's exact spelling for model-file reads. An unknown name raises V-VAR-001 with up to three canonical-name suggestions when matches are available. WP 1.10 supplies registry metadata only and does not open model output; quantity computation and dependency execution are delivered in WP 2.2a/b.

**Selection order** for a variable: fraction(s) → bed layer / vertical selection → reduction (sum of fractions, depth average). This yields one value per face per time.

### 7.9a Quantity engine: central reading and derived quantities (D50)

Several plotted quantities are not stored in the map file but computed from one or more stored variables, e.g. the Froude number, the vorticity, the specific discharge or the mean grain size. Matlab does this in one central function, `gdm_read_data_map_simdef.m` (a `switch` over `Fr`, `vorticity`, `qsp`, `dm`, `Ltot`, `stot`, …), used by all tags. The Python tool keeps that idea, as **one engine used by every analysis**:

* **No analysis reads model output directly.** M2D, SMB and every future analysis ask the `QuantityEngine` (`variables/engine.py`) for `(quantity, selection)` pairs at a sub-run and time. Only the engine calls `MapReader` (§7.6).
* **Registry of quantities.** A `VariableSpec` (§7.9) is either
  * `raw`: read from `netcdf_candidates`; or
  * `derived`: `inputs` (other quantity names, raw or derived), `requires` (grid geometry, `.sed` fractions, `.mor` settings, constants), `compute(inputs, context) → array`, `location`, units and label key, and a `version` that enters the fingerprint.
  WP 1.10 establishes the metadata catalog for v1 quantities; the compute callbacks and executable dependency graph are added with the engine in WP 2.2a/b.
* **Executable dependencies.** `inputs` are required together; `input_alternatives` lists ordered, mutually exclusive input sets, and the first complete set is used. `optional_inputs` are read when present and otherwise left for the compute callback to resolve from context. Shields uses `bed_shear_stress` when available, otherwise `velocity_magnitude` plus `chezy`; `water_level_rate` uses `mesh2d_s0` when available and otherwise the preceding map output. Dependency errors include the selected quantity chain and sub-run.
* **Dependency resolution.** For a request, the engine builds the dependency graph, detects cycles at registration time, and **reads every raw input once** per (sub-run, time), even if several requested quantities or several analyses need it. The arrays live in a per-task memory cache that is cleared after each time.
* **Where selections apply.** Each derived spec declares `selection: inputs` (default: vertical/fraction selection is applied to the inputs before computing, e.g. Froude from depth-averaged velocity and depth) or `selection: output`. Scalar grain-size descriptors (`dm`, `dg`, `d10`, `d50`, `d90`) use the full sediment-fraction distribution and expose layer selection only; fraction selection and `sum_fractions` apply to component-valued quantities, not these mixture scalars.
* **Grid information** needed by spatial operators (face–edge connectivity, edge lengths and orientation signs, face areas, neighbours) is computed once per merge plan in the inspect phase and stored in `cache/grid.nc` (§9.3). Spatial operators work on **global** arrays, which the merge plan provides, so they are correct across partition boundaries.
* **Reader lifetime.** A process task holds one `MapReader` session for its sub-run/time block. The session opens each partition map once, reads raw variables under the NetCDF read lock, and closes all handles at task completion; the quantity engine's per-time cache is cleared between times.
* **Request identity.** Requests retain the spelling used by the caller as well as the canonical spec and selection, so aliases with parameters (`d10`, `d50`, `d90`) compute distinct percentiles while sharing the same dependency graph.
* **Sediment-density default.** Shields calculations use `RhoSol` from each `.sed` fraction. The default for an omitted `RhoSol` has not been verified from an authoritative D-Flow FM source; until then no generic density such as 2650 kg/m³ is assumed, and a Shields request reports the missing value.
* **Validation.** Requested quantities are resolved during validation: a missing input reports V-VAR-002 with the dependency chain (e.g. `vorticity → mesh2d_u1 missing in sub-runs 3–5`).
* **Extensible.** Projects can register their own derived quantities through the entry point `d3dplot.quantities` (same mechanism as analyses and hooks), with tests.

**Catalogue of quantities** (all derived quantities of Matlab `D3D_plot`, which M2D and SMB share through `gdm_read_data_map_simdef.m`, its line-profile variant `gdm_read_data_map_ls_simdef.m` and `gdm_read_data_map.m`; definitions taken from the Matlab code). **Physical constants are read from the model of each simulation, not hard-coded** (D53): gravity `g` from MDU `[physics] Ag` (HYDROLIB-core `physics.ag`), water density `ρ` from MDU `[physics] Rhomean` (`physics.rhomean`), sediment density `ρ_s,i` **per fraction** from the `.sed` file (`RhoSol` of each `[Sediment]` block). When a keyword is absent, the D-Flow FM default applies (HYDROLIB-core defaults: `Ag = 9.81`, `Rhomean = 1000`). The values used are written to the `.dia` file. Matlab hard-codes `g = 9.81`, `ρ = 1000`, `ρ_s = 2650`; both real runs have `Ag = 9.813` (§5), so Matlab comparisons of g-dependent quantities use a relative tolerance of 1e-3. Each entry gets a unit test with hand-computed values and, where the synthetic case allows it, an analytic test. Notation: `‖u‖` velocity magnitude, `h` water depth, `F_i` volume fraction of sediment fraction `i`, `D_i` its diameter.

*Raw quantities with aliases* (registry entries of kind `raw`; Matlab short names from `D3D_var_num2str.m`):

| Canonical | Matlab | FM NetCDF |
|-----------|--------|-----------|
| `water_depth` | `h`, `wd`, `waterdepth` | `mesh2d_waterdepth` |
| `velocity_magnitude` | `umag` | `mesh2d_ucmaga` in 3D, otherwise `mesh2d_ucmag` (2D depth-averaged magnitude) |
| `velocity_magnitude_layered` | `umag_layer` | `mesh2d_ucmag` per `nLayers` (3D; WP 2.2b) |
| `water_level` | `wl` | `mesh2d_s1` |
| `bed_level` | `bl`, `DPS`, `etab` | `mesh2d_mor_bl` (morphological runs), otherwise `mesh2d_flowelem_bl` |
| `layer_thickness` | `thlyr`, `La` (layer 0 = active layer) | `mesh2d_thlyr` |
| `layer_fraction` | `Fak`, `lyrfrac`, `Fs` | `mesh2d_lyrfrac` (selection `fractions`, `layer`, `sum_fractions`) |
| `chezy` | `czs` | `mesh2d_czs` |
| `bed_shear_stress` | `taub` | `mesh2d_taus` |
| `mean_grain_size_geometric_output` | `mesh2d_dg` | `mesh2d_dg` (as written by D-Flow FM; differs from the derived `dg` below) |
| `cell_area` | `ba` | `mesh2d_flowelem_ba` |

*Derived quantities* (registry entries of kind `derived`):

| Canonical | Matlab (file) | Inputs | Definition | Phase |
|-----------|---------------|--------|------------|-------|
| `froude_number` | `Fr`, `fr` (`gdm_read_data_map_Fr`) | `velocity_magnitude`, `water_depth` | `‖u‖ / sqrt(g·h)`; NaN where `h < dry_depth_m` (default 0.01 m; Matlab has no threshold) | v1 |
| `specific_discharge` | `qsp` (`gdm_read_data_map_q`) | `velocity_magnitude`, `water_depth` | `‖u‖·h` (m²/s) | v1 |
| `energy_head` | `E` (`gdm_read_data_map_E`) | `water_level`, `velocity_magnitude` | `η + ‖u‖²/(2g)` (m) | v1 |
| `shields_number_times_diameter` | `ShieldsD` (`gdm_read_data_map_ShieldsD`) | `bed_shear_stress`; if absent `velocity_magnitude`, `chezy`; `g`, `ρ`, `ρ_s,i` | `θ_i·D_i = τ_b / (g·ρ·R_i)` (m), `R_i = (ρ_s,i − ρ)/ρ`; without `τ_b`: `τ_b = ρ·(g/C²)·‖u‖²`. Matlab's formula has no diameter, hence the name. One value per fraction (component dimension) when `RhoSol` differs between fractions, one value otherwise. Requires a `.sed` file | v1 |
| `velocity_direction` | `udir` (`gdm_read_data_map_udir`) | `mesh2d_ucx`, `mesh2d_ucy` | `mod(degrees(atan2(u_x, u_y)), 360)`: degrees clockwise from geographic north, in `[0, 360)` (D68) | v1 |
| `velocity_vector` | `uv` | `mesh2d_ucx`, `mesh2d_ucy` | two components; M2D arrows | v1 |
| `velocity_magnitude_layer_mean` | `umag` with `layer` (`gdm_read_data_map_umag`) | `velocity_magnitude_layered` | arithmetic mean over the selected layers (Matlab behaviour; not thickness-weighted). Needs 3D layers (§7.9) | v1 (3D) |
| `water_level_rate` | `dzetadt` (`gdm_read_data_map_dzetadt`) | `mesh2d_s1`, optional `mesh2d_s0` | `(s1 − s0)/Δt`, with `Δt` between map outputs (m/s). If `mesh2d_s0` is absent, use the preceding map output's `mesh2d_s1`; the first output is NaN. The source choice is written to the `.dia` (D71) | v1 |
| `vorticity` | `vorticity` (`gdm_read_data_map_vorticity`, `D3D_vorticity_from_u1`) | `mesh2d_u1` (edges), grid geometry | `ω_f = Σ_{e∈f} s_{f,e}·u1_e·L_e / A_f` (s⁻¹, positive counter-clockwise) | v1 |
| `streamfunction`, `transport_streamfunction` | same names (`gdm_read_data_map_streamfunction`, `D3D_compute_streamfunction`) | `mesh2d_u1` (× `mesh2d_hu` for transport; NaN → 0), grid Laplacian | edge fluxes `u_e·L_e` assembled into `b` (`b_L −=`, `b_R +=`), gauge `b_1 = 0`, `ψ = L⁻¹ b` (sparse solve, scipy; the factorised Laplacian is cached per merge plan) | v1 |
| `total_sediment_thickness` | `Ltot` (`gdm_read_data_map_Ltot`) | `layer_thickness` | `Σ_k thlyr_k` (m) | v1 |
| `morphological_active_area` | `ba_mor` (`gdm_read_data_map_ba_mor`) | `cell_area`, `total_sediment_thickness` | `A_f` where `L_tot ≥ active_sediment_thickness_threshold_m` (default `0.001 m`), otherwise `0`; `Ltot_thres` is configurable per analysis (D70) | v1 |
| `sediment_transport_magnitude` | `stot` (`gdm_read_data_map_stot`) | `mesh2d_sxtot`, `mesh2d_sytot` | `sqrt(sx² + sy²)` per fraction (component dimension) | v1 |
| `sediment_transport_magnitude_sum` | `stot_sum` (`gdm_read_data_map_stot_sum`) | `sediment_transport_magnitude` | `Σ_i` over fractions | v1 |
| `bed_load_transport_magnitude` | `sb` (profile reader) | `mesh2d_sbcx`, `mesh2d_sbcy` | `sqrt(sbx² + sby²)` | v1 |
| `mean_grain_size_arithmetic` | `dm` (`gdm_read_data_map_mean_grain_size`) | `layer_fraction`, `.sed` diameters | `d_m = Σ_i F_i·D_i` (m) | v1 |
| `mean_grain_size_geometric` | `dg` (same file) | `layer_fraction`, `.sed` diameters | `d_g = d_0·2^{Σ_i F_i·log2(D_i/d_0)}`, `d_0 = 10⁻³ m` | v1 |
| `grain_size_percentile` | `d10`, `d50`, `d90` (profile reader `gdm_read_data_map_ls_grainsize`) | `layer_fraction`, `.sed` diameters | Matlab-compatible cumulative fraction interpolation, linear in diameter; pad with `D_1/2` and `2·D_n`; return NaN when fractions do not sum to `1 ± 0.001` (D68) | v1 |
| `velocity_parallel`, `velocity_perpendicular` | `vpara`, `vperp` (profile reader) | `velocity_vector`, a section polyline | projection on the section direction / its normal, on the faces crossed by the section (dfm_tools `polyline_mapslice`) | later (PRF, D55) |
| `discharge` | `Q` (profile reader `gdm_read_data_map_ls_Q`) | `velocity_perpendicular`, `water_depth` along a section polyline | `Σ u_⊥·h·Δs` over the crossed faces (m³/s) | later (PRF, D55) |
| `chloride_mass` | `clm2` (`gdm_read_data_map_sal_mass`) | `sal`, `mesh2d_flowelem_zw` | `Σ_k (Cl_k/1000)·Δz_k`, `Cl = sal2cl(sal)` (port of the small Matlab `sal2cl`, formula and reference documented) | v1 (3D) |
| `residence_time_max` / `_depth_average` / `_surface` | `T_max`, `T_da`, `T_surf` (`gdm_read_data_map_T_max`) | `mesh2d_cTR<i>`, `mesh2d_dTR<i>`, `zw`, decay rate `RcDecTR<i>` from the substance file (`.sub`, read with HYDROLIB-core `hydrolib.core.dflowfm.substance.models.SubstanceModel`; positive in the file, a decay in the formula) | `T = ln(C_d/C_c)/r`, with Matlab's clipping rules; depth average, surface layer, or max over layers. Requires a `.sub` file (V-VAR-002 otherwise) | v1 (3D) |
| `morphodynamic_celerity` | `*cel_morpho*` (`gdm_read_data_map_cel_morpho`, `call_ECT`) | `velocity_magnitude`, `water_depth`, `chezy`, active layer, fractions, transport parameters | eigenvalues of the morphodynamic system (ECT) | later (D54: ECT, 27 Matlab files, is not ported or coupled for now) |

*Selections that work like derived quantities* (applied by the engine to any 3D input, §7.9): `vertical: {depth_average: {z_range}}` = `Σ v_k Δz_k / Σ Δz_k` with `Δz` from `mesh2d_flowelem_zw`, layers outside `z_range` masked (Matlab `depth_average`, `depth_average_limits`); `vertical: {elevation: z}` = linear interpolation of the profile at elevation `z` using `mesh2d_flowelem_zc` (Matlab `elevation`). v1 (D54); `zw`/`zc` are read when written, otherwise reconstructed with dfm_tools `reconstruct_zw_zcc`.

"v1" quantities are implemented in WP 2.2a (2D) and WP 2.2b (3D); `morphodynamic_celerity` (D54) and the section quantities (D55) are added later through the same registry.

**Section quantities** (`velocity_parallel`, `velocity_perpendicular`, `discharge`) only exist along a line. They are registered with `location: line` and come with PRF (D55, §7.9b). In v1 requesting them is a validation error (V-VAR-006).

**Edge-located inputs** (e.g. `mesh2d_u1`): the merge plan (§7.6) also has an **edge part**. Owned edges per partition are those whose owning face (first of `mesh2d_edge_faces`) is owned by the partition. Their global positions are matched by edge mid-point coordinates against the merged grid (KD-tree, 1 mm, one-to-one, error on ambiguity). Coverage is checked as for faces (V-OUT-020).

**Two levels of "derived"** (not to be confused):
* **face-level** derived quantities: computed by the engine from model output, before any analysis (this section);
* **analysis-level** post-processing: computed by an analysis from its own processed results, e.g. SMB `bed_slope_streamwise` from segment means, or values × width (§8.2).

**Sharing between analyses.** The process stage runs **one `ProcessTask` per (simulation × sub-run) for all analyses together** (D27, revised). The planner collects the requests of all analyses for that sub-run, the engine reads each raw input once, and each analysis then reduces the arrays in memory (M2D writes face values, SMB computes segment statistics). The `bed_level` needed by both M2D and SMB is therefore read once.

### 7.9b Sampling: extension point for reading along a line (PRF, D55)

Matlab `D3D_plot` has the plot type **PRF**: quantities read along a line (a `.pli` polyline or a river-km track), plotted against the distance along the line, per time or as a space-time plot, including 3D vertical sections. PRF is **not in v1**, but the v1 architecture is built so that it can be added as an ordinary analysis:

* **Engine requests carry a sampling.** Every request is `(quantity, selection, sampling)` at a (sub-run, time). `Sampling` is a small protocol: `kind`, `fingerprint()`, `prepare(grid, merge_plan) → SamplingPlan` (built once per merge plan in a `GeometryTask` and cached under `cache/geometry/`, like the SMB region membership), and `apply(global_array, plan) → array`. v1 implements only `GridSampling` (all faces; edges for edge-located inputs), the default, so M2D and SMB never mention it.
* **PRF adds `LineSampling(polyline, spacing_m=None)`**: the faces crossed by the line and the crossing segments with their lengths and directions, from dfm_tools `polyline_mapslice`, read with HYDROLIB-core `PolyFile`. Values are still read through the merge plan for the whole grid and then sampled, so reading stays centralised (§7.9a) and the raw-read-once rule still holds when M2D, SMB and PRF run in the same task.
* **Registry.** `VariableSpec.location` is `face`, `edge` or `line`. `line` quantities (section quantities of §7.9a) receive the `SamplingPlan` (segment directions, lengths) in their `compute` context. The validator rejects a `line` quantity without a `LineSampling` (V-VAR-006).
* **Store.** PRF writes its own files per (analysis, simulation, time) with a `distance` coordinate (and `layer`/`z` for vertical sections), next to the M2D and SMB files (§9.5); fingerprints include the sampling fingerprint, so reprocessing and skipping work as in v1.
* **Plotting.** PRF profiles use `LineView` (x = distance or river km) and `SpaceTimeView`; vertical sections need a new `SectionView` (§7.14b). Measurements along a line reuse the profile measurements of SMB (§7.15). Finalisation and hooks are the common ones (§7.14a).
* **Configuration.** A future `profile` analysis takes `lines` (paths to `.pli` files or river-km tracks) and the usual options (`variables`, `times`, `comparisons`, `x_limits`, `hooks`, …); no v1 option needs to change.

**v1 test of the extension point** (WP 2.2b): a test registers a dummy sampling (a fixed list of faces) and a dummy `line` quantity, and checks that the engine applies it and that the fingerprint changes with the sampling. This keeps the protocol honest without implementing PRF.

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
└─ logs/                          d3dplot_<timestamp>.dia (diagnostic file, D41), d3dplot_<timestamp>.log (debug)
```

* `<selection>` is a short readable label (`all`, `layer-top`, `fraction-3`, `depth-average`). The full selection is in the file attributes.
* Data files contain only values (`face` or `segment × statistic`); `store.open()` re-attaches the grid.
* Encoding: **float32** (D26), zlib level 4, CF attributes, `_FillValue` NaN. Optional `output.dtype: float64`. Values are computed in float64 and rounded only when written; coordinates, areas and times stay float64.

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
  1. no map files → `not_started`;
  2. read the **last 4 KB** of every partition `.dia`, or of the main `.dia` when not partitioned. If all contain `Computation finished` → `finished`;
  3. a controlled-exit message → `interrupted`;
  4. last map time < `TStop` and all map/`.dia` files unchanged for `status.stale_after` (default 1 h) → `stopped`;
  5. otherwise → `running`.
* For numeric `TStop`, compare the map time coordinate and `TStop` as elapsed seconds after converting their respective units. The map's CF origin may differ from MDU `RefDate` (V-MDU-002), so do not compare decoded absolute instants in this case. Date/time `TStop` values are compared as UTC instants.
* `inspect_simulation_status(path)` returns the status alongside the discovery report. A valid MDU with missing map output has status `not_started` while the report still contains V-OUT-001; invalid or unselectable model input has no status.
* **SMT**: `output/work` exists → `running`; otherwise the status of the last sub-run (and a check that all sub-runs are finished).
* SMT status checks with at least eight sub-runs use the bounded I/O thread pool from discovery; NetCDF4 reads share its access lock.
* **Storage**:
  * `cache/simulation_status.json` (machine-readable) is trusted on later runs only when final;
  * `tables/simulation_status.csv` (human-readable) is regenerated every run. Columns: `label, path, kind, state, n_subruns, n_partitions, last_output_time, last_morph_time, morfac, corrupt_outputs, checked_at, dflowfm_version`.
  * `corrupt_outputs` is `null` until output-quality inspection has populated the per-sub-run counts.
  
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
| process | `ProcessTask` | simulation × sub-run × **block of times**, for **all analyses** together (D27): the quantity engine (§7.9a) reads each raw input once, each analysis reduces in memory | one data file per (analysis, quantity, selection, time) |
| plot | `PlotGroupTask` | subject (simulation, or simulation vs reference, or all) × variable × comparison × block of times (map2d); view × comparison × variable × region group (SMB) | PNGs |
| movie | `MovieTask` | subject × variable × comparison × extent × colour-limit set | guarded MP4 |

Every task key contains the subject (and the reference label for simulation differences), so no two tasks write the same file.

**Parallelism for plain FM and SMT simulations.** The unit of work is a block of output times of one sub-run, so both kinds parallelise the same way:

| Simulation | Sub-runs | Process tasks | Example |
|------------|----------|---------------|---------|
| SMT (`r209`) | 195 | per sub-run; usually one block per sub-run (3 map times each), so parallel **over sub-runs** | 4 requested times → 4 tasks (the 4 sub-runs that contain them); all times → 195 tasks |
| Plain FM, partitioned or not | 1 | the requested times are split into **blocks**, so parallel **over time blocks** | a 1-year run with 365 daily map outputs, all requested, 8 workers → about 32 blocks of ~12 times |
| Several simulations | any | tasks of all simulations are mixed in one queue | 3 plain FM runs × 32 blocks → 96 tasks |

* **Block size**: `block_size = clamp(ceil(n_times / (workers × 4)), 1, max_block_times)`. The factor 4 gives enough tasks for load balancing. `max_block_times` (default 50) limits memory. A sub-run never shares a task with another sub-run. Configurable: `execution.process_block_times`.
* **Cost of a task**: open the map files of its sub-run (for `r209`, 44 files ≈ 1 s, §5.3) and read the requested times with the merge plan (≈ 1 s per variable and time). The merge plan itself is built once per simulation in the inspect phase, never per task. Opening the files once per block instead of once per sub-run costs about 1 s per extra block, which is negligible against the read time of a block.
* **Very large single times** (hundreds of millions of faces, beyond v1): a block could be split further by partition. Not needed for the expected models; the task interface allows it later.
* **Inspect phase**: reading `time`/`morft` and topology signatures is parallel per (sub-run × partition-file group), so a plain FM run with 44 partitions is also inspected in parallel.
* **Plot stage**: independent of the model kind; parallel over plot groups.

**Discovery validation** is the I/O-bound exception to the default process executor: for at least eight sub-runs it uses a `ThreadPoolExecutor` with up to 64 workers, one task per sub-run; smaller simulations are serial. There is no nested parallelism. netCDF4 access is protected by a lock because its native reader is not thread-safe.

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
* logging through a `QueueHandler` to the main process;
* the task callable and its read-only worker context are sent once to each spawned worker during initialisation; individual task submissions then contain only the `ProcessTask`.

**Sharding**: `--shard i/N` executes only the tasks whose **stable digest** satisfies `int.from_bytes(blake2b(task.key.encode()).digest()[:8], "big") % N == i`. Python's `hash()` is randomised per process and must not be used. Task keys are canonical strings (e.g. `process|r001|subrun=3`), frozen by tests. This is the building block for SLURM arrays.

**Failure handling**: a failing task is logged with its traceback and does not stop other tasks. The run summary lists failures, and the exit code is non-zero if any task failed. A re-run retries only failed or missing outputs.

**Windows**: a script that starts a run must have `if __name__ == "__main__":`. The CLI handles this itself.

### 7.14 Plotting

**Figure templates** (draw once, save many; D29):
* **Map template** (per simulation × variable × comparison × extent):
  * built once with a `PolyCollection` of only the faces visible in the extent (indices from `cache/geometry/extent_<hash>.nc`);
  * static decorations: land boundaries, river-km labels, tiles, fixed weirs;
  * colour bar and labels;
  * the measurement panel if configured.
* **Loop**: for each time, `collection.set_array(values[visible])` and update the title; for each colour-limit set, `collection.set_clim(...)` → save. `color_limits_mode: {days_since: date}` overrides each set with `[0, max(elapsed_UTC_days, 0)]` for that frame (D72).
* **Line template** (SMB profiles, time series): axes, structures/hooks and measurement artists are created once; only line data change.
* **Render modes** (`map.render`, shared by map2d and river-km diagnostic maps):
  * `polygons`;
  * `raster`: values rasterised on the figure's pixel grid with xugrid, then `imshow`;
  * `auto` *(default)*: raster when faces per screen pixel exceed 1.
* **Robustness**:
  * only data, limits and titles change inside a group;
  * figures are closed at the end of every group;
  * after an error the group restarts with fresh figures;
  * `--fresh-figures` rebuilds every image, and a test asserts pixel-identical output.
* **Writers**: a registry by format. v1 has PNG only (`figure.formats: ["png"]`). Metadata goes into PNG text chunks: `d3dplot_version`, `config_hash`, `figure_fingerprint`, `task_key`, the resolved provenance path, and the replot selector (analysis index, simulation, variable, comparison, time, extent, and colour-limit set).
* **Figure file names**: `<variable>_<comparison>_<yyyymmddTHHMMSS>_c<k>_e<k>.png`, inside the `<subject>` folder (§7.10). Short, to stay under the path-length limit (§7.18).
* **Measurement artists may change between times**: different raster grids or tiles, polygon sets, or source types. Each measurement artist has a **compatibility key**: type, grid transform and shape (rasters), or the hash of the polygon ids (polygons). Within a group, the artist is updated in place only when the key of the next time equals the current one; otherwise it is removed and rebuilt. Tests alternate grids and polygon sets.

### 7.14a Common figure finalisation (D46)

Every figure of every analysis, present and future, ends in **one function**, `plotting.finalize.finalize_figure(fig, context, style, hooks, writer)`. Analyses never save or close figures themselves. Matlab repeats this in each figure function (`apply_adhoc_functions(in_p)` in 20 functions, then `fig_print_close`).

Steps, in this order:
1. **Style**: font family and sizes (`figure.font_size`, `figure.font_family`; tick, label, title and legend sizes derived from it), figure size (`figure.size_cm`), line widths. Applied to all axes, colour bars and legends of the figure.
2. **Language**: axis labels, colour-bar labels, titles and legend entries come from the label builder (§7.20) in `language`.
3. **Hooks** (Matlab `function_handles`): every hook in `hooks` is called as `hook(fig, context)` (§7.21). Hooks may add or change artists. They run after the tool has drawn everything and **after** the style step, so a hook's changes are not overwritten, and before saving.
4. **Metadata**: tool version, configuration hash, figure fingerprint (including the hook source hashes) and task key, written by the writer.
5. **Save** through the writer registry (§7.14): each format in `figure.formats` (PNG in v1), resolution `figure.dpi`; atomic write; overwrite policy `replot`.
6. **Close or keep**: in a draw-once group, the figure is kept for the next image and only the data change (§7.14); the finalisation then re-applies only steps 3–5. Otherwise it is closed.
7. **Diagnostic file**: one `** INFO` line per saved figure group (count, folder), errors with the hook name.

Draw-once and hooks: a hook can change anything, so after hooks the figure is no longer a clean template. Policy `hooks_mode`:
* `rebuild` *(default)*: when hooks are configured, rebuild the figure for every image. Hooks may mutate arbitrary Matplotlib state, so this is the only mode that guarantees isolation. When no hooks are configured, retain draw-once template reuse.
* `per_image`: explicit performance opt-in; snapshot and restore added artists and common text/axes properties after each image. Arbitrary mutations may not be restored.

A test runs a hook that adds a line and changes the title and checks that every saved image contains exactly one such line.

### 7.14b Reusable plot building blocks (D47)

Analyses compose figures from shared blocks. New analyses (HIS, PRF, …) use the same blocks.

| Block | Draws | Used by |
|-------|-------|---------|
| `MapView` | face values of a grid (`PolyCollection`, or raster mode), colour map and limits, colour bar | M2D; SMB `segment_map`/`segment_membership`; future GRD, HFM |
| `MapDecorations` | land boundaries (`.ldb`), river-km labels, background tiles, fixed weirs, polygons (regions, cut lines) | every `MapView` |
| `ExtentSet` | the list of plot windows: full model, explicit `extents`, and **windows along a river-km file** (`extents_along_river_km: {file, window_x_m, window_y_m}`, one window per point) | every `MapView` |
| `MeasurementPanel` | second map panel with measurements (§7.15) | M2D (later others) |
| `LineView` | lines with styles per simulation/time/region, or **stacked areas** for multi-component data; measurements as lines/markers; x-limits sets | SMB views; future HIS, PRF |
| `SpaceTimeView` | km × time colour plot | SMB `space_time`; future M1D, PRF |
| `finalize_figure` | §7.14a | all |

**River-km files in maps are independent of each other**: the windows (`extents_along_river_km.file`, Matlab `fpath_rkm_plot_along`), the labels (`river_km_labels.file`, Matlab `fpath_rkm_disp`) and the SMB segment definition (`river_km.file`, Matlab `fpath_rkm`) can be three different files, all in the fixed format (§9.1). In the Maas case they are `rkm_3km_lr.csv`, `rkm_1km_lr.csv` and `rkm.csv`.

The map settings form one configuration block, `MapViewConfig` (§10.1), used as `map:` in `map2d` and in `river_km_statistics` (for its diagnostic maps). The same extents, labels and land boundaries therefore produce the same windows in both analyses, and the visible-face subsets (`extent_<hash>.nc`) are computed once and shared.

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
* **Prepare**: per (extent × plot time × value/difference): select entries (`|Δt| ≤ time_tolerance`, bounding box ∩ extent ± `spatial_margin_m`), read windows, × factor, align misaligned tiles to the first selected tile's grid with nearest neighbour (D17), then mosaic (`mosaic: first` default, `last`, `mean`). Then:
  * compute differences with *t₀* (rasters: *t₀* resampled to the *t* grid; polygons: match by `id_field`, then geometry, then order with a count check);
  * write fingerprinted results atomically to `cache/measurements/map2d/<catalogue>/<extent>/<time>[_diff].nc|.gpkg`. Fingerprints include the catalogue and selected source identities, extent, time, factors, filters, mosaic policy, and target grid.
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
  * Extent generation and labels share one fixed-format reader, but retain independent file paths as required by D47.
* **Background tiles** (D42): contextily, an optional extra. dfm_tools deprecated its own `plot_background` in favour of contextily.
  * Default provider: satellite imagery (`Esri.WorldImagery`); configurable (`background_tiles: {provider: …}`), with the provider's attribution shown on the figure.
  * Tiles are fetched once per extent in the prepare stage, in the model CRS (configured `crs`, since the map files have EPSG 0).
  * Caching: `contextily.set_cache_dir(tiles_cache_dir)` gives a persistent on-disk cache. This replaces Matlab's `earth_tiles` folder (Matlab `plotMapTiles`: `<cache>/<server>/<z>/<x>/<y>.png`). Default: a per-user cache folder. It can point to a shared folder so several users and offline machines reuse downloaded tiles; the write guard allows this one configured folder.
  * Without network and without cached tiles: a warning (V-ENV-002) and the figure is drawn without a background. The provider and attribution are resolved once per map group; tiles are drawn behind the model values.
* **Fixed weirs** (later): HYDROLIB-core `PolyFile` on `.pliz`.

### 7.17 Inputs are read-only (D14)

1. **Single write path**: every write goes through `store.atomic` with the **write guard**. It refuses any target outside an output root that contains `.d3dplot-output`. The marker is created only in a new or empty folder. Cache, data, and configuration outputs use atomic replacement; the streaming `.dia` and `.log` files are deliberate exceptions because diagnostic progress and worker records must remain visible after a crash. They open exclusively through `guarded_text_stream` in `store.atomic` and use the same guard. Contextily's library-managed tile cache is the only additional exception: its default or explicitly configured folder is allow-listed on the output guard, and contextily is never given any other write location.
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

* **Logging**: console (rich, level INFO) + file `logs/d3dplot_<timestamp>.log` (DEBUG, for developers); worker records arrive through a queue. Every task logs its key, duration and outcome.
* **Diagnostic file** (D41): `logs/d3dplot_<timestamp>.dia`, written by the main process for **every** command that does work (`validate`, `inspect`, `prepare`, `process`, `plot`, `run`, `replot`, `clean`). It is plain UTF-8 text in the style of D-Flow FM `.dia` files: one message per line, prefixed `** INFO   :`, `** WARNING:` or `** ERROR  :`. It is meant for users: everything that was done and every choice made, in order:
  1. header: tool version, command line, start time, host, user, configuration file and its hash, resolved-configuration path;
  2. inputs: each simulation (kind, sub-runs, partitions, status), each input file (path, content hash);
  3. validation: all issues (code, location, message, hint);
  4. **choices**: per analysis and simulation, the requested times → selected outputs (sub-run, hydrodynamic and morphological time), including every resolved duplicate (D25) and every time matched between simulations;
  5. **data quality**: every corrupt partition found (V-OUT-030) and every masking or skipping applied at read time (D23), with the number of faces; implausible values (V-OUT-031);
  6. work: per stage, tasks run / reused from cache / failed, with durations. Files written are counted per folder; individual paths go to the `.log`;
  7. footer: end time, total duration, outcome, and `** INFO   : d3dplot finished` as the last line (or `** ERROR  : d3dplot stopped` with the reason).
  
  The file is written to the reference simulation's root, and a copy goes to every other simulation root involved, so each simulation's `d3dplot/` folder documents the work done on it. Messages are flushed line by line, so the file is useful even after a crash. The tool's own `.dia` files live in `d3dplot/logs/`, never in model output folders, so they cannot be confused with D-Flow FM's `.dia` files by the status detection (§7.11).
  * **Implementation status:** `validate` and `inspect` write guarded `.dia` copies with configuration provenance, validation findings, discovery/inspection outcomes, and cache state. `plot` and `replot` report the resolved configuration, figure writes/reuse, and errors; `plot` writes the resolved `run_<timestamp>.yaml` and `latest.yaml` files. `replot` loads the marked-root provenance referenced by the PNG, then uses the normal cache-only map2d plot path while restricting writes to that single figure. `run_process_stage` connects task outcomes to diagnostics and the guarded atomic run-summary CSV writer. The CLI `run` command and model-value worker integration remain outstanding.
* **Provenance**:
  * `config/run_<timestamp>.yaml` and `config/latest.yaml`: the resolved configuration, with all defaults and the tool version;
  * the run summary at the end of the log and in `tables/run_<timestamp>.csv`;
  * fingerprints in every cache file;
  * metadata in every PNG.
* **Replot**:
  * `d3dplot plot <path to latest.yaml or a d3dplot/ folder>` plus `--set key=value` overrides;
  * `d3dplot replot <figure.png>` regenerates one map2d figure from its metadata; missing or invalid provenance is V-PLOT-005;
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

### 7.21 Hooks (Matlab `function_handles`)

* Available for **every figure of every analysis** (present and future), because they run in the common finalisation (§7.14a).
* Configuration, at three levels, combined in this order: project (`hooks`, all figures), analysis (`analyses[i].hooks`), and view (e.g. `views: {profile: {hooks: […]}}`, Matlab `p_single_function_handles`). `hooks_replace: true` at a lower level replaces instead of extends.
* A hook is an importable `"package.module:function"` string, so it works from YAML and in worker processes. Python scripts may also pass callables; they must be importable (module-level functions) for the process executor.
* Signature `hook(fig, context)`. `context` (a read-only dataclass) holds what Matlab passed in `in_p`: analysis, view, comparison, variable, simulation(s), time(s) (UTC and display), extent, axes handles by role (`context.axes["model"]`, `["measurements"]`, `["colorbar"]`), the plotted data, labels, language, river-km provider and the output path.
* A failing hook: the figure is not saved, the error (with hook name and traceback) goes to the diagnostic file and the run summary; other figures continue.
* The hook names and source hashes are part of the figure fingerprint.
* Generic hooks shipped with the tool: `d3dplot.hooks:legend_horizontal`, `d3dplot.hooks:add_polylines` (draw `.pli`/`.ldb` files with a style).
* Project-specific hooks (e.g. `add_structures_rkm`) live in the user's own package.

### 7.22 Performance targets (acceptance case 1, Windows laptop, P-drive)

| Step | Target |
|------|--------|
| Validation | < 2 min (dominated by reading 195 MDUs and partition metadata) |
| Prepare (grid, merge plan, times of 195 × 44 partitions, geometry, measurements) | < 15 min, once |
| Process M2D + SMB, 4 times × 1 variable | < 5 min with 8 workers |
| Plot (M2D: about 30 extents × 2 comparisons × 4 times; SMB: as in the script) | < 10 min with 8 workers |
| Re-plot after a styling change | only affected figures |

Local read-only r209 validation completed in **99.283 s**: 195 sub-runs, 8,580 map files, no issues, using the discovery thread pool (D61).

## 8. Specification of the v1 analyses

### 8.1 `map2d` (Matlab M2D)

**Configuration** (main fields; full names in Appendix A):
* `variables`, `times: {at, axis, tolerance}`, `vertical`, `fractions`;
* `comparisons` (§7.12);
* `figure` (fonts, size, dpi, hooks via finalisation §7.14a) and `map` (the shared map view §7.14b: `color_limits`, `extents`, `extents_along_river_km`, `river_km_labels`, `land_boundaries`, `background_tiles`, `aspect`, `colormap`, `render`);
* `measurements`, `movie`, `export`, `hooks`.

`variables` accepts canonical names, Matlab aliases, and registered raw NetCDF names through the §7.9 registry.

**Prepare**:
* extents → visible-face subsets (`extent_<hash>.nc`);
* land boundaries → cache;
* measurements → windows per (extent, time) (§7.15);
* tiles per extent (optional).

**Process** (inside the shared `ProcessTask` per simulation × sub-run, §7.13): for each requested time in that sub-run, the quantity engine (§7.9a) returns the global face array of each requested quantity and selection (raw or derived, e.g. `froude_number`, `vorticity`). M2D writes them to `cache/data/map2d/<requested-variable>/<selection>/<time>.nc`.
* Variables: `values(face)`.
* Attributes: fingerprint, hydrodynamic and morphological time (ISO, with offset), sub-run, variable, units, selection.

**Plot** (`PlotGroupTask` per subject × variable × comparison × extent × block of times):
* Read the fingerprinted map2d data files only; model output is not reopened.
* Build an `ExtentSet` from the full model, explicit windows, and optional windows from `extents_along_river_km` (fixed CSV format §9.1). Cache each visible-face subset as `cache/geometry/extent_<hash>.nc`, fingerprinted from the grid and bounds. An extent with no faces is reported and skipped.
* Build a shared `MapView` template once per group from the inspected UGRID mesh and visible faces. `map.render: polygons` uses a matplotlib `PolyCollection`; `raster` uses xugrid rasterisation at the figure's pixel resolution; `auto` selects raster when visible faces exceed screen pixels.
* For every time: apply the comparison (§7.12), update the map values and title, and for every colour-limit set update limits and save. `color_limits_mode: {days_since: date}` uses `[0, max(elapsed_UTC_days, 0)]` per frame (D72); `figure.display_scale` scales both values and limits. Cross-simulation differences use the reference grid; regridders are constructed once per grid pair.
* Run every image through `plotting.finalize.finalize_figure` and the guarded PNG writer. The group reuses the template when no hooks are configured; with hooks, `figure.hooks_mode: rebuild` creates a fresh view for every image by default, while the explicit `per_image` mode snapshots common mutations.

Images = times × extents × colour-limit sets. Differences between simulations are stored under the reference simulation's figures.

**Movies**: after all PNG frames of a (simulation, variable, comparison, extent, colour-limit set) exist, write one guarded MP4 beside the figures. Frames remain in selected hydrodynamic-time order; `movie.duration_s` is the duration of each frame. A guarded adjacent `.mp4.json` stores the fingerprint of the ordered PNG fingerprints and duration, so unchanged movies are reused. MP4 encoding uses the optional `movies` extra and reports V-ENV-001 if its encoder is unavailable; invalid frames or encoding failures report V-PLOT-004.

**Edge cases**:
* A variable missing in some sub-runs → error V-VAR-002, listing the sub-runs.
* A requested time with a corrupt partition → `on_corrupt_output` (§7.6).
* An extent with no faces → warning, and the extent is skipped.
* A selected quantity retaining a non-singleton extra dimension → error V-VAR-005; map2d cache files contain one value per face.

### 8.2 `river_km_statistics` (Matlab SMB)

**Configuration**:
* `variables`, `times`;
* `regions[]`: name, polygon, optional `measurements`, optional `concave_hull`;
* `region_groups`, `region_differences`;
* `river_km`: `file`, `sets` (`name → [km]`), `track`, `branches`, `half_width_m`; `allow_invalid_segments` defaults to `false` and controls the V-SMB-010/V-SMB-013 override;
* `statistics`, `views`, `comparisons`, `time_series_locations_km`;
* `multiply_by_width`, `time_averages`, `diagnostics: [segment_map, segment_membership]`, `display: auto | lines | stacked_area` (per variable);
* `map`: the shared map-view settings for the diagnostic maps (§7.14b), including `extents_along_river_km` and `river_km_labels`;
* `figure` (`x_limits`, `y_limits`, fonts), measurement tolerances, `hooks`.

`variables` accepts canonical names, Matlab aliases, and registered raw NetCDF names through the §7.9 registry.

`river_km.branches` is either one explicit branch broadcast to every requested centre, or one branch per centre. For multiple named sets, the list must be broadcastable or match each set; Rijntakken branch transitions are inferred from `track` and take precedence. Generic files with multiple branch tags require an explicit branch assignment.

**Inspect/prepare**: parse the river-km file into the guarded `cache/inputs/river_km_<hash>.nc`; detect the river provider from the tags (§4.5); read the region polygons, retaining their CRS and rejecting missing source CRS (V-SMB-002).

**Prepare** (per simulation × region × km set; WP 4.2 supplies the calculation core and WP 4.3 writes it to guarded caches):
1. Build edges, cut lines and segment polygons (§4.5). Transform region polygons to the model CRS when available and run the geometry checks.
2. Compute boundary-inclusive region membership and deterministic face-to-segment assignment (shared cut lines → lower index) in `cache/geometry/river_km_statistics/<region>/<set>.nc` (§9.6). Check coverage and overlaps and retain the results for diagnostics; WP 4.4 renders requested figures through the shared `MapView` (D75).

**Process** (inside the shared `ProcessTask` per simulation × sub-run, §7.13): for each time, variable, region and km set, the quantity engine (§7.9a) returns the global face values (raw or derived, e.g. `froude_number`); SMB keeps the faces with a segment index ≥ 0 and finite values. Compute **all** statistics per segment, vectorised with `numpy` `bincount`-style reductions on the segment index. Write `cache/data/river_km_statistics/<region>/<set>/<variable>/<selection>/<time>.nc`, holding `values(segment, statistic[, component])` with coordinates `km_centre`, `km_edge_start/end`, `segment_length_m`, and (when requested) `segment_width_m`.

**Multi-dimensional variables** (Matlab `var_idx` with several indices, e.g. `Fak` or `stot` per sediment fraction): when the selection keeps one extra dimension (`fractions: [1, 2, …]` or `"all"`, without `sum_fractions`), the statistics are computed **per component**, and the data file has the extra dimension `component` (with the fraction names from the `.sed` file as labels). At most one extra dimension is kept; more is a validation error (V-VAR-005). The NaN/Inf exclusion is applied per component. This intentionally follows the v1 contract rather than Matlab's shared vector mask, which excludes a face when any component is invalid; scalar outputs remain directly comparable with the Matlab reference.

**Post-process** (plot stage, from the cache). WP 4.3 implements and tests the numerical kernels; WP 4.4 wires them into plot views. The cache keeps raw spatial statistics and optional width; matching Matlab post-processing applies width to `mean` and `mean_area_weighted`, not to counts or every statistic. Definitions follow Matlab: `gdm_create_mat_SMB_postprocess.m`, `pp_sb_tim_ave_01.m`, `gdm_compute_cumulative.m`.

| Quantity | Definition | Edge rules |
|----------|------------|-----------|
| `bed_slope_streamwise` | `S_i = (v_{i+1} − v_{i−1}) / ((c_{i+1} − c_{i−1})·1000)` with `v` = segment `mean` and `c` the km centres | `S_1 = S_n = NaN`; NaN if a neighbour is NaN. Works for irregular km spacing |
| `multiply_by_width: flow` | `v_i · W_i`, with `W_i = Σ_{faces in segment} A / (Δs_i·1000)` (width from the face area `mesh2d_flowelem_ba`) | Faces with NaN/≤ 0 area are excluded from `W` |
| `multiply_by_width: morphological` | the same with the morphological active area: face area where total sediment thickness is at least `active_sediment_thickness_threshold_m` (default `0.001 m`; Matlab `ba_mor`) | needs the sediment thickness variable; error V-VAR-002 if absent |
| Region difference | `v_a,i − v_b,i` per segment and statistic (same km set required) | NaN if either is NaN |
| Time average over times `t_1…t_n` (n ≥ 2) | weights `w = diff(cen2cor(t))` (in seconds); `mean = Σ v_k w_k / Σ w_k`; `std = sqrt(Σ w_k (v_k − mean)² / Σ w_k)`; `max`, `min`; `dominant = (Σ w_k v_k^{5/3} / Σ w_k)^{3/5}` | Applied to each statistic series separately; a NaN at any time gives NaN (as Matlab: `sum` without `omitnan`); n < 2 → error. Resolve configured times through `TimeAxis` and merge them as supplementary process selections (D79) |
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
* **Line or area** (`display`, per variable): `lines` (one line per simulation/time/region), `stacked_area` (components stacked, as Matlab `do_area`, e.g. fractions of a sediment transport or composition) or `auto` *(default)*: `stacked_area` when the variable has a `component` dimension, otherwise `lines`. In `stacked_area` mode each figure shows one simulation and one time (or one km for `time_series`), the components in a fixed colour order with a legend of fraction names. Differences in time or between simulations are drawn per component as lines, because stacking differences is misleading. Measurements are drawn as lines on top. This is built on the shared line view (§7.14b).

**Diagnostic maps** (`diagnostics`, Matlab `do_plot_inpolygon` and `do_plot_inpoly`), drawn with the shared map view (§7.14b), so they get the same land boundaries, river-km labels, tiles, hooks and finalisation as M2D maps:

| Diagnostic | Content | Extents | Matlab |
|------------|---------|---------|--------|
| `segment_map` | faces coloured by the km centre of the segment they belong to (region ∩ segment); faces in no segment transparent; region outline and cut lines on top | full model **and** every window of `map.extents_along_river_km` | `gdm_plot_SMB` → `fig_M2D` (`inpoly`, `inpoly_<k>`) |
| `segment_membership` | one figure per segment: the faces of that segment highlighted, region outline, title with the km | window around the segment (bounding box + margin) | `gdm_create_mat_SMB` → `fig_inpoly` |

* Diagnostics are produced once per simulation × region × km set from `geometry/segments_*.nc` (prepare stage output), not per time.
* Faces counted twice or regions not covered (V-SMB-011/013) are highlighted in `segment_map`.
* Matlab skips the diagnostic when several regions are grouped; here they are drawn per region.
* `segment_membership` windows use the segment polygon bounds padded by 10% of the larger bounds dimension on every side (D78).

**Edge cases**:
* Segments with no faces → NaN, with count 0.
* Measurements outside the km range → ignored.
* A region group with regions on different km sets → error V-SMB-012.

**Profile measurements (WP 4.5)**: a configured region or region-difference input may be canonical NetCDF or Matlab v5/v7 `.mat`. Convert each requested variable once per source fingerprint and fixed UTC offset into the guarded `cache/measurements/river_km_statistics/` cache; never reopen or rewrite a `.mat` for every figure. Interpret naive MATLAB datenums using the measurement-specific offset when configured, otherwise the inspected `times.nc` source offset. Profiles (`profile`, `profile_all_times`) select the nearest measurement time within `measurements_time_tolerance` and overlay measured values at their own river-km coordinates. `time_series` selects the nearest measured river-km within `measurements_km_tolerance` and plots its full measurement-time series. `mean_area_weighted` accepts Matlab's `val_mean` equivalence, preferring it as Matlab does. `difference_time` subtracts the measurement profile at the first model time from the matched current-time profile; simulation-difference comparisons omit measurement overlays. Region differences use only their explicitly configured difference-measurement input. Missing matches are reported as V-MEA-004; absent requested variables/statistics are V-MEA-005. Measurement source fingerprints participate in figure reuse and hook context includes the measurement artists/data.

---

## 9. File formats

### 9.1 River-km file (input; fixed)

As §4.5. Parsed with `csv` (comma, header skipped, whitespace stripped). Columns: `x, y, label, km`, with `branch = label.split("_", 1)[1] if "_" in label else label`. Points are grouped case-insensitively and sorted by km for interpolation; duplicate km within a branch is an error (V-RKM-003). Built-in provider records are packaged under `d3dplot.river_km/data/`; generic provider data has no river-specific cutoffs.

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
  * dimensions `plan`, `partition`, `owned_face`, `owned_edge`, `subrun`;
  * `plan_of_subrun(subrun)`;
  * per plan, faces: `partition_offset(plan, partition)`, `partition_n_owned(plan, partition)`, `owned_local(plan, owned_face)` (local face index in the partition file, 0-based), `global_pos(plan, owned_face)`, `grid_file(plan)`;
  * per plan, edges (D67): `edge_partition_offset`, `edge_partition_n_owned`, `edge_owned_local(plan, owned_edge)`, `edge_global_pos(plan, owned_edge)`, and `edge_orientation(plan, owned_edge)` (+1/−1 relative to the merged UGRID edge direction);
  * `signature(subrun, partition)` (hex string, §7.6);
  * attributes `n_faces`, `n_edges`, `reference_subrun`, `matching` (`globalnr` | `coordinates`); `matching` describes the reference plan.
  
  Most simulations have a single plan.
* `times.nc`: dimensions `time` and `partition`; `time(time)` is CF numeric UTC seconds since 1970-01-01 with a proleptic Gregorian calendar; variables `subrun(time)`, `local_index(time)`, `morph_time(time)` (same units/calendar as `time`), and `corrupt_partition(time, partition)` (int8, 1 = corrupt); global attributes include `source_utc_offset` and the cache fingerprint/schema metadata.
* `grid.nc` also stores the grid geometry for spatial operators (§7.9a): `face_edges(face, max_edges)`, `face_edge_sign(face, max_edges)` (+1/−1 orientation), `edge_length(edge)`, `face_area(face)`.

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
* if the axes differ between variables, write one dataset per variable; cached and explicitly converted datasets are per variable;
* convert naive MATLAB datenums with a configured fixed UTC offset. Analysis loading falls back to `times.nc:source_utc_offset`; the standalone converter requires `--time-zone` because it has no model context (D81);
* cache conversion by source fingerprint, canonical variable, timezone, and converter/schema version. The input file remains read-only;
* standalone conversion writes beneath a new or empty marked output root and refuses to overwrite an existing target. For a multi-variable input, output names receive the canonical variable suffix.

### 9.5 Cache data file (map2d)

```
dimensions: face
variables:  values(face) float32 (D26), _FillValue NaN, units, long_name
attributes: d3dplot_fingerprint, d3dplot_parameters (JSON), d3dplot_version, d3dplot_cache_schema,
            time (ISO with offset), time_utc (CF numeric), morph_time (ISO with offset), subrun, local_index,
            variable, selection, grid_file "../../../../grid.nc" (relative), plan (merge plan id)
```

For SMB: `values(segment, statistic)`, plus `km_centre(segment)`, `km_start(segment)`, `km_end(segment)`, `segment_length_m(segment)`, `count(segment)`.

### 9.6 Geometry files (cache)

* `region_<name>.nc`: `inside(face)` int8; attributes: polygon source path and hash, source/model CRS, and optional concave-hull ratio.
* `segments_<region>_<set>.nc`:
  * `segment(face)` int32, −1 = none;
  * `km_centre`, `km_start`, `km_end`, `branch` per segment;
  * `cut_line_x/y(edge, 2)` and `polygon_x/y(segment, 5)`;
  * check results as attributes (`n_uncovered`, `n_overlap`, `invalid_segments`); shared-cut-line ties count once for the lower segment and are excluded from `n_overlap`.
* `extent_<hash>.nc`: `faces(n)` int32; attributes: the extent bounds and margin.

### 9.7 Simulation status (`simulation_status.json`)

```json
{"label": "r209", "path": "…", "kind": "smt", "state": "finished", "checked_at": "…",
 "subruns": [{"index": 0, "state": "finished", "last_time": "…", "last_morph_time": "…", "corrupt": null}],
 "n_partitions": 44, "morfac": 10.0, "corrupt_outputs": null,
 "dflowfm_version": "…", "tool_version": "…"}
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
├─ output: {root?, forbidden_roots[], dtype: float32 | float64}   (default float32, D26)
├─ status: {stale_after: 1 h}                     (default; threshold for an unchanged incomplete run)
├─ paths: {mappings: {"p:\\": "/p/"}}
├─ execution: {executor: serial|processes|slurm, workers?, shard?, slurm: {…}}
├─ labels: {vertical_datum: "+NAP", extra_dictionaries: []}
├─ tiles_cache_dir: path                         (optional; default per-user cache, D42)
├─ figure: {font_size, font_family, size_cm, dpi, formats: [png], hooks_mode: rebuild | per_image, x_limits, y_limits}   (rebuild default when hooks are configured; D73)
├─ hooks: ["module:function", …]                (applied to every figure; §7.21)
├─ reprocess / replot: if_changed | never | always
├─ on_corrupt_output: mask | error | skip         (default mask; every masking goes to the diagnostic file)
└─ analyses: [Map2DConfig | RiverKmStatisticsConfig] (plugin configs join with the analysis registry; D59)
     each with: times, figure (overrides), hooks, map: MapViewConfig, and map2d measurements
MapViewConfig (shared by map2d and the SMB diagnostic maps, §7.14b)
├─ color_limits, colormap, render, aspect
├─ extents: [[xmin, xmax, ymin, ymax], …]       (null = full model)
├─ extents_along_river_km: {file, window_x_m, window_y_m}   (Matlab fpath_rkm_plot_along, rkm_tol_x/y)
├─ river_km_labels: {file, color}                (Matlab fpath_rkm_disp; may differ from the file above)
├─ land_boundaries: [*.ldb]
└─ background_tiles: {provider, …} | false
```

* All models use `extra="forbid"`.
* Defaults live in the models; global options are inherited by analyses unless overridden.
* When both effective `figure.x_limits` and `figure.y_limits` are lists, their row counts must match (D60).
* `d3dplot schema > d3dplot.schema.json` exports a JSON schema for editor autocompletion.
* WP 1.3 currently accepts the two built-in analysis discriminators only. Third-party plugin models will be discovered, validated, and included in the schema when the `d3dplot.analyses` registry is implemented (D59).

The following WP 1.3 model defaults were not specified elsewhere in the plan:

| Setting | Default |
|---------|---------|
| `times` | `at: all`, `axis: hydrodynamic`, `tolerance: null` (unset) |
| `figure` | `font_size: 10` pt, `font_family: DejaVu Sans`, `size_cm: [16, 10]`, `dpi: 150` |
| `map` | `colormap: viridis`, `render: auto`, `aspect: auto` |
| `river_km_statistics` | `statistics: [mean_area_weighted]`, `views: [profile]` |

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
    map:
      color_limits: {bed_level: {difference_time: [null, [-1, 1]]}}
      extents_along_river_km: {file: 'p:\…\01_rkm\rkm_3km_lr.csv', window_x_m: 1500, window_y_m: 1500}
      river_km_labels: {file: 'p:\…\01_rkm\rkm_1km_lr.csv'}
      land_boundaries: ['c:\checkouts\Delft3D_plot\data_local\maas\j21_ldb.ldb']   # recreated from j21_ldb.mat (D45)
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

The land boundary is the `.ldb` recreated from `j21_ldb.mat` (D45).

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
| `d3dplot validate cfg.yaml` | Configuration/static checks, variable validation, simulation discovery, and grid/merge-plan inspection when earlier checks pass | `--format text|json` |
| `d3dplot inspect cfg.yaml` | Build or refresh grid and merge-plan caches | `--simulation <label>` |
| `d3dplot prepare|process|plot|run cfg.yaml` | Stages (`run` = all) | `--only <analysis>`, `--variable`, `--time`, `--simulation`, `--workers`, `--executor`, `--shard i/N`, `--reprocess`, `--replot`, `--fresh-figures`, `--set` |
| `d3dplot replot <figure.png>` | Regenerate one figure | `--set` |
| `d3dplot status cfg.yaml` | Status table + cache/figure progress | |
| `d3dplot clean cfg.yaml` | Remove stale or all tool output (inside marked roots) | `--stale`, `--figures`, `--cache` |
| `d3dplot measurements convert in.mat -o out.nc --time-zone +01:00` | Permanent conversion | `--variable <name>` selects one variable; otherwise output is one canonical-variable file per variable |
| `d3dplot slurm write cfg.yaml` | Write job scripts (stub) | |

`info` and `variables` use read-only discovery and inspect only the first map-file
header per sub-run for raw variable names and CRS. `info` reports per-sub-run
partition counts and last hydrodynamic/morphological times; `--json` returns the
same summary as structured JSON. `variables` lists detected raw NetCDF names,
matching canonical registry entries and their aliases, plus the registered
derived quantities. `inspect` checks partition identity and topology, selects a
time-consistent reference sub-run, and atomically builds or reuses each
simulation's grid and merge-plan caches. It accepts `--simulation <label>` to
limit the run. `validate` runs configuration checks, validates known variable
names and raw-field availability, and discovers the configured simulations;
when those earlier checks have no errors, it also builds or reuses the inspect
caches. `init` writes a commented starter YAML and refuses to overwrite an
existing target.

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
| V-CFG-004 | E | Effective `figure.x_limits` and `figure.y_limits` are both lists but have different row counts (D60) |
| V-SIM-001 | E | Folder missing, or no model found |
| V-SIM-002 | E | Several candidate MDUs |
| V-SIM-010 | E | SMT sub-run numbering has gaps |
| V-SIM-011 | W | SMT running (`output/work`) |
| V-MDU-001 | E | MDU cannot be read by HYDROLIB-core (lists unknown keywords per section), or `.mor`/`.sed` cannot be parsed |
| V-MDU-002 | W | MDU `RefDate` differs from the map time units |
| V-OUT-001 | E | Map files missing |
| V-OUT-002 | E | Map file unreadable (not NetCDF, truncated) |
| V-OUT-003 | E | Partition count differs between sub-runs |
| V-OUT-020 | E | Merge plan does not cover each face exactly once, or coordinate matching is ambiguous |
| V-OUT-021 | W | Topology signatures differ between sub-runs (several merge plans needed) |
| V-OUT-022 | E | Partition identity: file numbers not `0…N−1`, domain values inconsistent with file numbers, `globalnr` not 1-based/unique/complete |
| V-OUT-023 | W | Global face numbers are absent; merge-plan matching uses face centres within 1 mm |
| V-OUT-024 | E | No sub-run has time-consistent partition metadata for a reference merge plan |
| V-OUT-025 | W | Existing inspect cache is incomplete, unreadable, or stale and is rebuilt |
| V-OUT-030 | W | Corrupt or inconsistent `time`/`morft` in a partition (lists sub-run, partition, times) |
| V-OUT-031 | W | Implausible values (> 1e30 or non-finite) in a partition (runtime) |
| V-OUT-032 | E | Boundary duplicate between sub-runs with different `morft` |
| V-OUT-033 | W | Boundary duplicate between sub-runs with different values |
| V-OUT-040 | W | Output path longer than 240 characters (Windows) |
| V-OUT-041 | E | Output folder not writable, or inside `forbidden_roots` |
| V-TIME-002 | W | Time zone conflict between map units, MDU `Tzone` and configuration |
| V-TIME-010 | E | Explicit time cannot be resolved: no usable output exists, or a configured tolerance excludes every output (lists the nearest when available) |
| V-TIME-011 | W | Requested time hits a corrupt output (`on_corrupt_output` decides) |
| V-TIME-012 | E | `times.axis: morphological` requested for a simulation without morphology (no `morft`) |
| V-VAR-001 | E | Unknown variable (suggest up to three canonical names when close matches exist; an unregistered raw NetCDF name is accepted only when present) |
| V-VAR-002 | E | Variable or required raw input missing in some sub-runs |
| V-VAR-003 | E | Fraction or layer index out of range |
| V-VAR-004 | E | Variable has dimensions other than those declared in its `VariableSpec` |
| V-VAR-005 | E | After selection, a variable keeps extra dimensions unsupported by the analysis (SMB allows one `component` dimension; map2d requires scalar face values) |
| V-VAR-006 | E | A `line` quantity (section quantity, §7.9a) requested without a line sampling; in v1 always an error, because PRF comes later (D55) |
| V-RKM-001 | E | River-km file unreadable or wrong format |
| V-RKM-002 | E | Rijntakken tags without a valid `track`, ambiguous generic branches without a valid assignment, or mixed recognized river tags |
| V-RKM-003 | E | Duplicate km in a branch |
| V-RKM-004 | E | Requested km outside the branch range |
| V-RKM-005 | E | Requested km set has fewer than two finite, strictly increasing, distinct centres |
| V-SMB-001 | E | Region polygon unreadable or empty |
| V-SMB-002 | E | Region polygon CRS missing, invalid, or incompatible with the model grid |
| V-SMB-010 | E | Invalid or self-intersecting segment polygon (override: `allow_invalid_segments`) |
| V-SMB-011 | W | Region faces not covered by any segment (count; `half_width_m` too small) |
| V-SMB-012 | E | Region group mixes km sets |
| V-SMB-013 | E | A face is assigned to two segments (override: `allow_invalid_segments`) |
| V-SMB-014 | E | Time average has fewer than two distinct ordered outputs |
| V-MEA-001 | E | Measurement file or catalogue missing or unreadable |
| V-MEA-002 | E | Catalogue columns wrong; mixed file types for one time |
| V-MEA-003 | E | Measurement or model CRS is missing, invalid, or incompatible |
| V-MEA-004 | W | No measurement within the tolerance for a plot time (lists the nearest) |
| V-MEA-005 | E | Variable or statistic not in the profile file (with aliases/equivalences) |
| V-MEA-006 | E | `.mat` is v7.3 (HDF5), not supported in v1 |
| V-MEA-007 | E | Malformed profile-measurement structure, coordinate, time, or value shape |
| V-PLOT-001 | W | Configured map extent contains no visible faces; list the extent and skip it |
| V-PLOT-002 | E | Required map2d data/grid/extent cache is missing, unreadable, or has invalid geometry or face count |
| V-PLOT-003 | E | Invalid map comparison request or incompatible simulation/time match (identify the missing reference or mismatched key) |
| V-PLOT-004 | E | Movie frame is unreadable or incompatible, or movie encoding fails |
| V-PLOT-005 | E | Figure lacks valid d3dplot provenance/replot metadata, or the recorded figure cannot be regenerated |
| V-LDB-001 | E | `.ldb` unreadable (or a non-`.ldb` land boundary given) |
| V-ENV-001 | E | Optional dependency missing for a requested feature (tiles, movies) |
| V-ENV-002 | W | Background tiles not available (no network and not cached); figures drawn without background |
| V-HOOK-001 | E | Hook cannot be imported |

## 13. Quality and tests

### 13.1 Engineering standards

* `pyproject.toml` (hatchling, `src/` layout), uv lock file, Python 3.11–3.13.
* ruff (lint + format; Python 3.11 syntax target) and mypy (strict for `src/`;
  Python 3.12 type target because NumPy 2.5 stubs use PEP 695), run through
  pre-commit. The mypy target does not change the supported runtime range.
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
| Unit | **Comparisons of cache values use tolerances** (float32 cache, D26: relative 1e-6, absolute 1e-5 m for levels); Config, overrides, validation codes, INI adapters, time handling (tz, duplicates, matching), variable registry, labels (all keys, UTF-8), fingerprints, write guard, atomic writes, river-km parsing and providers, geometry algorithm (hypothesis property tests: segments partition the axis, angles, cutoffs), statistics, measurement filters, `.mat` conversion, sharding | Synthetic in-memory objects | Every commit, Windows + Linux |
| Integration | Full runs of `map2d` and `river_km_statistics` on the **synthetic SMT case** (§13.3) and its variants. Results compared with `expected/` | Generated in a session temporary folder (pytest fixture) | Every commit |
| Merge plan | Python reader == dfm_tools merged values for every variable and time (synthetic); corrupt variant detected and handled per policy | Synthetic | Every commit |
| Images | pytest-mpl baselines (Linux, fixed fonts); `--fresh-figures` pixel-identical to draw-once | Synthetic | Every commit (Linux) |
| Parallel equivalence | `serial` == `processes` == union of all shards (byte-identical cache), for an SMT simulation **and** a plain FM simulation (time blocks) | Synthetic | Every commit |
| Read-only inputs | Snapshot (size, mtime, hash) of all inputs before and after a full run | Synthetic | Every commit |
| Diagnostic file | The `.dia` of each command exists, ends with the finish line, and contains the expected choices (selected times, duplicates, masking) for the synthetic variants | Synthetic | Every commit |
| Matlab comparison (synthetic) | Compare selected `map2d` bed-level and derived-specific-discharge caches with Matlab `M2D` outputs by hydrodynamic time and face-centre coordinates (1 mm); compare all eight SMB statistics for the three exact regions and 40 river-km segments | Synthetic + Matlab results (`@pytest.mark.requireslocaldata`) | Locally |
| Acceptance (real) | Acceptance case 1 (§13.4) | P-drive, read-only (`requireslocaldata`) | Manually / before releases |
| Benchmarks | Prepare, process and plot throughput; merge-plan reader vs dfm_tools | Synthetic (CI), r209 (local) | Nightly / locally |

pytest markers: `unit`, `integration`, `mpl`, `slow`, `requireslocaldata`, `acceptance`.

### 13.3 Synthetic SMT case (implemented)

Generator: `tools/synthetic_smt/` ([README](https://github.com/V-Chavarrias/Delft3D_plot/blob/main/tools/synthetic_smt/README.md)). Run with `.venv\Scripts\python.exe tools\synthetic_smt\generate.py --force`; the output goes to `data_local/synthetic_smt_01/` (about 92 MB, git-ignored). It verifies itself with HYDROLIB-core, dfm_tools, rasterio, geopandas and scipy. The session-scoped `synthetic_case` fixture generates it into pytest's temporary directory for integration tests (WP 1.2). No frozen copy is committed (D19).

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

**Plain FM variants** (WP 1.2a): `fm_partitioned` has one root `dflowfm.mdu`, a root `dimr_config.xml`, three partition files under the default `DFM_OUTPUT_dflowfm/`, and 40 map times. `fm_single` has one root MDU, a single unpartitioned map under the explicit `single_output/`, and 40 map times. Neither has `smt.yml`; both represent one run. Their shared expected time axis and SMB statistics are in `expected/plain_fm/`. Discovery classification and the same integration tests as SMT remain to be completed with WP 1.6. (`non_partitioned` above is an SMT run whose sub-runs are not partitioned.)

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

**Matlab input**: `scripts/main_plot_synthetic.m`, generated with bed level and derived specific-discharge map outputs as well as the SMB statistics above. Run it on a newly generated case to create the Matlab reference (`mat/`, `fig/` in each simulation folder); existing reference results are preserved.

### 13.3a Matlab comparison

The local-only comparison test processes the generated synthetic case into a pytest temporary output root. It matches each Matlab map timestamp to the hydrodynamic time axis and uses a KD-tree on face centres from `grd.mat`; every Matlab face must have a unique Python match within 1 mm. Finite/non-finite masks must match; Python intentionally normalizes non-finite map values to NaN (V-OUT-031). Finite map values use `rtol=1e-6`, `atol=1e-5`. For each of the five selected times, all eight SMB statistics are compared across the three exact regions and 40 river-km segments using the same float32-aware tolerances. The Matlab vorticity helper currently returns 2,800 values for the 6,406-face grid without exposing the subset coordinates, so vorticity comparison remains deferred; derived specific discharge is used for the coordinate-matched derived-quantity comparison. The required Matlab MAT outputs were generated, but the plotting stage terminated in `fig_1D_01` when assigning a 100-point measurement overlay to a scalar handle slot. The test never writes to the Matlab reference case.

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

### 13.4a Acceptance case 2: Rijn plain FM run (`D16000`)

* Input: §5.5 (read-only); `output.root` local; `forbidden_roots` contains the project folder.
* Scope (user): **`map2d` only**; the check is that M2D figures are generated. No `river_km_statistics`, no river-km windows or labels.
* Analyses: `map2d` of `water_level`, `water_depth`, `velocity_magnitude`, and derived `froude_number` (with `g` from `Ag = 9.813`) and `vorticity`, all 109 times, comparisons `value` and `difference_time`, full-model extent plus one explicit extent.
* Checks: the expected PNGs exist (one per variable × comparison × time × extent) and are non-empty; engine values equal direct NetCDF reads; serial == parallel (time blocks); performance (§7.22); visual review. No Matlab reference.

### 13.5 CI

GitHub Actions on push and pull request:
* lint (ruff), types (mypy);
* tests on `{ubuntu-latest, windows-latest} × {3.11, 3.12, 3.13}`. The synthetic case is generated once per job and cached between steps;
* coverage upload;
* docs build;
* on tags: build wheel and sdist.

Nightly: benchmarks and slow tests.

### 13.6 Agent guidance for development (D48)

Future development with AI agents must follow this plan. The guidance is split by when it applies:

| File | Kind | Loaded | Content |
|------|------|--------|---------|
| `AGENTS.md` (root) | always-on instructions (open standard; read by Copilot, Claude, Codex) | every task | plan first (§0, §2); never write on `p:\`; read-only inputs; reuse HYDROLIB-core/dfm_tools before writing code and fix upstream; performance rules (merge plan, one open per sub-run, draw-once, caching, float32 storage); figure finalisation; option names; time handling; `.dia` messages; build/test commands; commit conventions |
| `.github/instructions/plotting.instructions.md` | file instruction (`applyTo` plotting and analyses code) | when editing plots | finalisation as the only exit, hooks, shared views, draw-once, option and label rules |
| `.github/skills/add-analysis-or-option/` | skill (with `references/naming-checklist.md`) | on demand / `/add-analysis-or-option` | steps to port a Matlab tag or add a view, variable or option: reference, reuse search, design in the plan, implementation, tests incl. synthetic case and Matlab comparison, benchmarks |
| `.github/skills/upstream-contribution/` | skill | on demand | local branch workflow for HYDROLIB-core and dfm_tools (D49), conventions of each repository, verification from d3dplot, recording in §14 |

These are for **developers**. The user-facing skills of §11 (`d3dplot-create-input`, …) are a separate deliverable shipped with the package (WP 5.4).

Maintenance: when a decision in §2 changes a rule, update `AGENTS.md` or the instruction in the same commit.

---

## 14. Upstream contributions

Upstream changes are made by the user (a contributor of both repositories) locally in `c:\checkouts\HYDROLIB-core` and `c:\checkouts\dfm_tools` (D49); branches on GitHub, issues and pull requests follow when the user decides. Recommended: a local feature branch from the start (creating local branches needs no access); work committed on local `main` can still be moved to a branch later (recipe in skill `upstream-contribution`). Workflow: skill `upstream-contribution` (§13.6). Develop each item first behind an adapter in `d3dplot`, with tests. Propose it upstream when stable, then switch to the upstream version and pin the minimum version. Track the status in `docs/upstream.md`. The HYDROLIB-core MDU change is the exception: the tool reads the MDU with HYDROLIB-core directly (D22), so that change comes first (§14.1).

| Repository | Contribution | Why | Priority |
|------------|--------------|-----|----------|
| HYDROLIB-core | **MDU keywords**: add 6 morphology keywords to `[sediment]` and 3 deprecated/research keywords to `ResearchGeometry` (detailed plan §14.1; scope agreed with the maintainer, D51) | `ResearchFMModel` must read real morphological MDUs (D22) | **High, first** (prerequisite of WP 1.5) |
| HYDROLIB-core | Support non-recursive `[geometry]` `iniFieldFile` loading by accepting `DiskOnlyFileModel` alongside `IniFieldModel` (§14.1, D56) | `ResearchFMModel(path, recurse=False)` previously failed on synthetic and `r209` MDUs before keyword validation | Completed locally; upstream release still required |
| HYDROLIB-core | Accept a DIMR `<parallel>` control containing only its main `<start>` component (§14.2) | Valid one-component DIMR files such as r209 are rejected because `Parallel.startGroup` is required | **High; prerequisite to finish WP 1.5** |
| HYDROLIB-core | Move the standard keywords `ncCompression`, `wriMap_trachytopes`, `tStartTlfsmo` from the research model to the standard model (§14.1, PR 2) | Spec conformity | Medium |
| HYDROLIB-core | Opt-in lenient loading (unknown keywords → warnings), contributing to #931 | Future kernel keywords | Low (discuss with maintainers) |
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


### 14.1 Plan: HYDROLIB-core MDU keywords (detailed; WP 0.1 complete locally)

#### Goal and scope

**Goal**: `ResearchFMModel(path, recurse=False)` loads the real `r209` MDU, and every MDU using standard morphology keywords, without errors. Every keyword keeps the official name, type, default and description of the MDU specification.

WP 0.1 fixes non-recursive loading of `[geometry]` `iniFieldFile`: when `recurse=False`, the existing path converter returns `DiskOnlyFileModel`, which `Geometry.inifieldfile` now accepts alongside `IniFieldModel`. Recursive loading remains unchanged.

**Local status (2026-10-26)**: implemented and committed in HYDROLIB-core as `54011e0d` on `feat/mdu_morphology_keywords`. The full non-local test suite passes (3,343 passed, 10 skipped, 2 xfailed); Black and isort checks pass. Read-only loading succeeds for r209 sub-runs 0, 1, and 194 and Rijn D16000 with `recurse=False`, preserving each `iniFieldFile` as a relative `DiskOnlyFileModel` path. The synthetic generator's self-check passes, and focused regressions cover `FMModel` and `ResearchFMModel` with `iniFieldFile`. No push or PR has been made; the first published HYDROLIB-core version containing this change is not yet known. See [upstream.md](./upstream.md).

**Reference**: `Deltares/Delft3DFM.File.Specifications`, `specifications/dflowfm/mdu.json` at commit `a4d5827` (2026-10-26). The repository needs a GitHub login; it was read through the GitHub API.

**Findings** (comparison of the spec with HYDROLIB-core 1.5.0, checkout `14cdeffe`, and of two real MDUs, 2026-10-26):
* `[sediment]`: the spec has 31 keywords; HYDROLIB-core `Sediment` has 3 (`Sedimentmodelnr`, `MorFile`, `SedFile`), `ResearchSediment` 4 research keywords. 22 standard keywords are missing in HYDROLIB-core. **Most of them will be deprecated in D-Flow FM soon** (user, 2026-10-26), so only the morphology keywords below are added.
* `[geometry]`: the deprecated `circumcenterMethod`/`circumcenterTolerance` (in both real MDUs) and the research keyword `calculateBedLevelOverNonActiveLinks` (in the Rijn MDU, §5.5) are missing.
* Real MDUs: `ResearchFMModel` fails on `r209` (7 keywords) and on the Rijn `RIJN.mdu` (3 keywords, all in `[geometry]`).
* Non-recursive loading (fixed in WP 0.1): before the fix, `ResearchFMModel(..., recurse=False)` failed when `Geometry.inifieldfile` received a `DiskOnlyFileModel` that its annotation rejected. This blocked D22 and real-MDU verification. The related open issue [Deltares/HYDROLIB-core#1188](https://github.com/Deltares/HYDROLIB-core/issues/1188) concerns migrating the deprecated keyword to `extForceFileNew`, not this path-only type mismatch; no matching PR was found.

**Scope of PR 1** (keyword set agreed with the HYDROLIB-core maintainer, D51; `iniFieldFile` loading fix added by D56):

| Part | Class | Keywords | Why |
|------|-------|----------|-----|
| A | `Sediment` (standard) | `dredgeFile`, `morCFL`, `DzbDtMax`, `morphoPol`, `inMorphoPol`, `upperLimitSSC` | Standard keywords used by morphological models; the other 16 missing `[sediment]` keywords will be deprecated and are **not** added |
| B | `ResearchGeometry` | `circumcenterMethod`, `circumcenterTolerance` (deprecated), `calculateBedLevelOverNonActiveLinks` (research) | Existing convention: deprecated and research keywords live in the research classes (`research/models.py`, comment `DEPRECATED_KEYWORD` for the deprecated ones, default `None`, as `s1incinterval`) |
| C | `Geometry` | `[geometry]` `iniFieldFile` accepts `DiskOnlyFileModel` when `recurse=False` | Required for path-only MDU loading; recursive parsing as `IniFieldModel` is unchanged (D56) |
| — | not in PR 1 | `[geometry]` `zLayerGrowthFactor`, `bedLevMode`; moving `stretchCoef`, `ncCompression`, `wriMap_trachytopes`, `tStartTlfsmo` from research to standard | PR 2: different sections; moving keywords changes the public API |

**Part A: fields added to `Sediment`** (`hydrolib/core/dflowfm/mdu/models.py`). Attribute = lower-case key; alias = spec spelling; types follow existing HYDROLIB-core patterns (`intbool` → `bool`, `path` → file model, `intenum` → `int` validated against the spec values):

| Alias (spec) | Type | Default | Spec description |
|--------------|------|---------|------------------|
| `dredgeFile` | `DiskOnlyFileModel` with `set_default_disk_only_file_model` (as `morfile`) | empty | Dredging/dumping settings file (`*.dad`) |
| `morCFL` | `bool` | `True` | Use CFL-like condition for morphologic updating |
| `DzbDtMax` | `float` | `0.1` | Maximum bed level change (m) per time step for `MorCFL=1` |
| `morphoPol` | `Optional[Union[PolyFile, DiskOnlyFileModel]]` (as `Geometry.partitionfile`) | `None` | Only apply bed updating within the polygon (`*.pol`) |
| `inMorphoPol` | `int` ∈ {0, 1} | `1` | 0 = no update inside the polygon, 1 = update inside |
| `upperLimitSSC` | `float` | `1e-06` | (re-read the spec description and unit at implementation time) |

**Part B: fields added to `ResearchGeometry`** (`hydrolib/core/dflowfm/research/models.py`):

| Alias (spec) | Type | Default | Comment |
|--------------|------|---------|---------|
| `circumcenterMethod` | `Optional[Literal["internalNetlinksEdge", "internalNetlinksLoop", "allNetlinksLoop"]]` | `None` | `DEPRECATED_KEYWORD` |
| `circumcenterTolerance` | `Optional[float]` | `None` | `DEPRECATED_KEYWORD` |
| `calculateBedLevelOverNonActiveLinks` | `Optional[bool]` | `None` | spec description (research, intbool, default 0) |

Each field gets a `Comments` entry. Types, defaults and descriptions are re-read from the spec at implementation time.

**Open point to check during implementation**: `FMModel` uses `skip_empty_properties=False`, so new **standard** fields with defaults (part A) are written by `FMModel().save()`. Precedent: #648, #788. Research fields with default `None` (part B) are not written. Confirm with the maintainer in the PR.
#### Procedure

**Step 0: preparation** (local, `c:\checkouts\HYDROLIB-core`):
* `git switch main; git pull` (read access is enough).
* Environment: HYDROLIB-core uses poetry (`poetry install`). Alternative without poetry: the d3dplot `.venv` already has HYDROLIB-core installed editable, plus `pip install pytest black isort` for the checks.
* Branch: `git switch -c feat/mdu_morphology_keywords` (local; no access needed, D49).
* Baseline: run `pytest tests/dflowfm/test_mdu.py tests/dflowfm/test_research.py` and note the result before changing anything.

**Step 1: tests first** (they fail before the change):
* New test data `tests/data/input/dflowfm_individual_files/mdu_morphology_keywords.mdu`, written by hand, **no project data**: `[sediment]` with the 6 keywords set to non-default values (mixed casing, e.g. `MorphoPol`, `morphopol`); `[geometry]` with the 3 part-B keywords; a small `.pol` next to it for `morphoPol`.
* `tests/dflowfm/test_mdu.py`, new `class TestSedimentMorphologyKeywords`:
  1. `FMModel(path, recurse=False)` loads the `[sediment]` part; each of the 6 attributes has the expected value and type (parametrised);
  2. defaults when absent equal the spec defaults;
  3. `inMorphoPol = 2` → `ValidationError`;
  4. `morphoPol`/`dredgeFile` are file models with the right path; with `recurse=True`, `morphoPol` is parsed as `PolyFile`;
  5. round trip: save and load give equal values;
  6. an unknown keyword in `[sediment]` still raises (pattern of `test_mdu_unknown_keyword_loading_throws_valueerror_for_unknown_keyword`).
* `tests/dflowfm/test_research.py`:
  1. `ResearchFMModel` loads the 3 part-B keywords with the right values; `FMModel` still rejects them;
  2. a new `ResearchFMModel()` saved to file does not contain them;
  3. MDU test files containing exactly the keyword sets that failed in `r209` and in `RIJN.mdu` (synthetic values, §5.4, §5.5) load with `ResearchFMModel`.
* `tests/dflowfm/test_mdu.py` and `test_research.py`: `iniFieldFile` loads as a `DiskOnlyFileModel` with `recurse=False` in both `FMModel` and `ResearchFMModel`; the existing recursive `IniFieldModel` behavior remains covered.

**Step 2: implementation**:
* `Sediment`: part-A fields and their `Comments`; an `inMorphoPol` validator (or `Literal[0, 1]`, whichever matches neighbouring code); `parse_file_model_paths`-style validator if the class needs it for `morphoPol` (check how `Geometry` does it).
* `ResearchGeometry`: part-B fields and `Comments`.
* `Geometry.inifieldfile`: accept `DiskOnlyFileModel` alongside `IniFieldModel` so `recurse=False` can preserve the path without parsing the `.ini`; keep recursive parsing unchanged.
* Nothing changes in `ResearchSediment`/`ResearchFMModel` (they inherit).

**Step 3: documentation**: `docs/reference/dflowfm/mdu/fm-model-attributes-detailed.md` (new `Sediment` attributes and the `Geometry.inifieldfile` union type) and `docs/reference/dflowfm/mdu/research.md` if it lists `ResearchGeometry` attributes. Changelog via commitizen.

**Step 4: checks**: `black .`, `isort .`, full `pytest`, locally with Python 3.12; CI later runs 3.10–3.14 on Windows/Ubuntu/macOS and SonarCloud.

**Step 5: verification from d3dplot** (the editable install follows the checked-out branch), read-only on the P-drive:
* `ResearchFMModel(<r209>\output\{0,1,194}\dflowfm.mdu, recurse=False)` loads; `sediment.morphopol` points to the `.pol`; `sediment.morcfl`, `sediment.dzbdtmax`, `geometry.research_circumcentermethod` have the file's values;
* `ResearchFMModel(<Rijn D16000>\RIJN.mdu, recurse=False)` loads (§5.5);
* the synthetic case MDUs load with `recurse=False`.

**Step 6: commits** (local), conventional, with the Copilot trailer:
1. `test(mdu): cover morphology keywords, research geometry, and non-recursive iniFieldFile`
2. `feat(mdu): add dredgeFile, morCFL, DzbDtMax, morphoPol, inMorphoPol and upperLimitSSC to [sediment]`
3. `feat(research): accept circumcenterMethod, circumcenterTolerance and calculateBedLevelOverNonActiveLinks in [geometry]`
4. `fix(mdu): support non-recursive iniFieldFile loading`
5. `docs(mdu): document morphology keywords and non-recursive iniFieldFile`

**Step 7: when the user has push access** (D49): push the branch, open an issue (title: "MDU: morphology keywords missing in [sediment]; circumcenter and calculateBedLevelOverNonActiveLinks rejected in [geometry]"; body: the tables above, the error messages, links to #648, #788, #931), open a draft PR with `Closes #<issue>`; maintainers squash-merge with `Refs: #<issue>`.
**Done when**: all new and existing HYDROLIB-core tests pass locally; step 5 passes, including `recurse=False` on synthetic, `r209`, and Rijn MDUs; plan §0.1 and §14 updated (branch name, status "local").

**Effort estimate**: about half a day.

#### PR 2 (later, separate issue)

* `[geometry]`: add `zLayerGrowthFactor`, `bedLevMode`; move `stretchCoef` from research to standard.
* `[output]`/`[time]`: move `ncCompression`, `wriMap_trachytopes`, `tStartTlfsmo` from research to standard. Keep the old attribute names as `DeprecatedAttributeAlias` (`hydrolib/core/base/_deprecation.py`) and document in `docs/migration.md`.
* Needs maintainer agreement (public API, written output).

#### Later

Typed `MorphologyModel` (`.mor`) and `SedimentModel` (`.sed`), if specification files exist in `Delft3DFM.File.Specifications`.

#### Dependency for d3dplot

* During development d3dplot uses the local HYDROLIB-core branch (editable install).
* d3dplot's first PyPI release needs a HYDROLIB-core **release** containing PR 1 (PyPI packages cannot depend on git branches); `pyproject.toml` then pins `hydrolib-core >= <that version>` (WP 0.2).

### 14.2 Plan: DIMR parallel control with one component

**Finding (2026-11-15)**: the read-only r209 `dimr_config.xml` contains a single D-Flow FM component and the valid control flow `<control><parallel><start name="FlowFM" /></parallel></control>`. HYDROLIB-core's `Parallel` model requires both `start` and `startGroup`, so `DIMR(path, recurse=False)` raises a validation error for the absent `startGroup`. The DIMR file's own documentation describes `start` as the main component and `startGroup` as the form for additional components. Existing HYDROLIB-core issue #127 concerned generating an empty `control` element and was fixed separately; no matching open issue or pull request was found.

**Scope**: make `Parallel.startGroup` optional while keeping `start` required, and add a regression test that parses a one-component parallel control without changing existing explicit `startGroup` behavior. The d3dplot adapter must continue to use `DIMR(path, recurse=False)` and select the `dflowfm` component.

**Status**: the user selected an upstream fix. It is committed locally on HYDROLIB-core branch `fix/dimr-parallel-startgroup` (`cb4047dc`, with formatter-only cleanup `bd69c4ea`) and cherry-picked onto `feat/mdu_morphology_keywords` for combined testing. The combined HYDROLIB-core suite passes (3,344 passed, 10 skipped, 2 xfailed); the 70 DIMR tests pass, and read-only r209 sub-run 0 DIMR resolution plus MDU/`.mor`/`.sed` parsing succeeds in 0.390 s. Do not push or open a pull request unless asked. No project inputs are modified. WP 1.5 project checks also pass; its d3dplot commit remains.

**Done when**: HYDROLIB-core's DIMR tests pass; d3dplot resolves the r209 DIMR MDU with the local fix; the synthetic one-component DIMR case passes; §0.1 and `docs/upstream.md` record the local branch and commit. The upstream contribution and WP 1.5 validation are complete locally; the d3dplot WP 1.5 commit remains.

### 14.3 Plan: external DIMR component libraries

**Finding (2026-10-26)**: while implementing WP 1.6, parsing the Rijn `dimr_config.xml` fixture with HYDROLIB-core's `DIMR(path, recurse=False)` fails when it reaches the valid `FBCTools_BMI` real-time-control component. The current discriminated union accepts only `rr_dll` and `dflowfm`, even though this configuration also contains the FM component discovery must select (§5.5). The rejection was reproduced with a minimal local regression fixture; no project input was modified.

**Scope**: update HYDROLIB-core's DIMR component model so valid non-FM library identifiers can be retained as generic components, while preserving the specialized `dflowfm` and `rr_dll` parsing and validation. Add a regression test with an external component before a `dflowfm` component. d3dplot must continue to select the FM component through HYDROLIB-core rather than adding an XML parser workaround.

**Status**: the open issue/pull-request search on 2026-10-26 found no matching item. The generic fallback is committed locally on HYDROLIB-core branch `fix/dimr-external-libraries` at `25e390bd` and cherry-picked into the integrated development branch `feat/mdu_morphology_keywords` as `96d07c87`. The isolated and integrated non-local suites pass (3,332 and 3,345 passed, respectively); the integrated DIMR tests pass (71). WP 1.6 is complete locally on `feat/simulation-discovery`; its 51-test CI-eligible suite passes, and read-only r209 discovery found 195 sub-runs and 8,580 maps without issues in 99.283 s. Do not push or open a pull request unless asked. Read-only on `p:\`.

**Done when**: the upstream regression test and DIMR suite pass; d3dplot discovers the FM component in the multi-component synthetic case; the combined HYDROLIB-core suite and WP 1.6 checks pass; `docs/upstream.md` and §0.1 record the local branch and status.

---

## 15. Implementation plan

### 15.1 Phase 0: done

* Plan (this document), licence, repository.
* Synthetic SMT generator with self-check and variants.
* Exploration of the real case (§5).
* Maas land boundary recreated as `.ldb` (D45).

### 15.1a HYDROLIB-core contribution (prerequisite of WP 1.5)

| WP | Content | Done when |
|----|---------|-----------|
| 0.1 | HYDROLIB-core PR 1 on a local branch: 6 `[sediment]` keywords + 3 `ResearchGeometry` keywords (§14.1, steps 0–6; step 7 when access exists) | §14.1 "Done when"; `ResearchFMModel` loads the `r209` MDU locally |
| 0.2 | Follow-up HYDROLIB-core release that contains PR 1 | `hydrolib-core >= <version>` in d3dplot's `pyproject.toml` (needed before the first PyPI release, WP 5.5) |

### 15.2 Phase 1: foundations

| WP | Content | Deliverables | Done when |
|----|---------|--------------|-----------|
| 1.1 | Repository skeleton | `pyproject.toml` (deps §6; extras `tiles`, `movies`, `dev`, `docs`), `src/d3dplot/__init__.py`, ruff/mypy/pytest config, pre-commit, CI workflow, MkDocs skeleton, `CONTRIBUTING.md`, `CHANGELOG.md`, commitizen | `uv pip install -e .[dev]` works; CI green on Windows + Linux with a smoke test; docs build |
| 1.2 | Synthetic case as a test fixture | `tests/conftest.py`: session fixture adding `tools/synthetic_smt` to `sys.path` and generating into `tmp_path_factory`; marker `requireslocaldata` | Integration tests can use `synthetic_case` |
| 1.2a | **Plain FM variants in the generator** (§13.3): `fm_partitioned` (one MDU at the root with `dimr_config.xml`, output in `DFM_OUTPUT_<stem>/`, 3 partitions, ~40 map times so that time blocks are exercised), `fm_single` (non-partitioned, `OutputDir` set explicitly), both without `smt.yml`; expected time axis and SMB statistics | Discovery classifies them as FM with one sub-run; the same tests as for SMT pass on them |
| 1.3 | Configuration | Built-in `config/*` models (nested `times` and shared `map.render`, D57–D59), YAML IO, `--set` overrides, resolved-config writer, JSON schema; plugin config support deferred to the analysis registry | Round trip YAML ↔ models; unknown keys rejected; schema exported |
| 1.4 | Validation framework | `validate/report.py`, `validate/checks/config.py`, V-CFG-001..004, text/JSON output, static configuration checks | Each code has a test; reference selection, duplicate labels/output roots, and effective x/y limit row counts are reported |
| 1.5 | Model input | `model_input/*` (`MduInfo` on `ResearchFMModel(recurse=False)`; `.mor`, `.sed` with the INI parser; DIMR) | Synthetic MDU/`.mor`/`.sed` parsed; V-MDU-001 lists unknown keywords. (local) real `r209` files parsed with the HYDROLIB-core branch of WP 0.1 |
| 1.6 | Discovery | `simulation/discover.py`, `subrun.py`, partition file discovery, V-SIM-*, V-OUT-001..003; bounded thread-based validation for large SMT cases (D61) | All synthetic SMT and plain-FM variants classified correctly; 51 CI-eligible tests pass; read-only r209 discovery of 195 sub-runs/8,580 map files completes in <2 min |
| 1.7 | Status | `simulation/status.py` (`.dia` tails), path-level status inspection, stale-time configuration, JSON/CSV serializers | Synthetic SMT/plain-FM states are correct, including `not_started`; corrupt counts stay unknown until WP 2.2. Read-only r209 is `finished` with no issues; bounded status scan is 27.371 s (433.548 s serial baseline) |
| 1.8 | Store | `store/layout.py`, `guard.py`, `atomic.py`, `fingerprint.py`; input-snapshot test helper | Guard refuses unmarked or forbidden roots; atomic writes tested |
| 1.9 | Labels | `labels/*` + CSVs seeded from Matlab | en/nl/es complete; templates; units |
| 1.10 | Variables | `variables/registry.py` with metadata for every v1 raw/derived quantity, canonical names, aliases, NetCDF candidates, dimensions, units, label keys, and V-VAR-001 suggestions | Alias/candidate lookup and unknown-name suggestions are tested; generated `docs/variables.md` matches the registry |
| 1.11 | CLI part 1 | `d3dplot` entry point and `python -m d3dplot`; `info`, `variables`, `schema`, `validate`, and `init`; `docs/cli.md` | Commands run on synthetic SMT/plain-FM cases; JSON/text shapes, V-VAR errors, raw header availability, schema, and no-overwrite starter config are tested |

The WP 1.2a generator and expected-output deliverables are implemented; their discovery acceptance passes with WP 1.6.

### 15.3 Phase 2: model output engine

| WP | Content | Done when |
|----|---------|-----------|
| 2.1 | Inspect phase: partition identity, merge plan(s) + `grid.nc`, topology signatures, reference sub-run selection (§7.6, §9.3), guarded cache persistence, `inspect` CLI, and validation integration | Synthetic scattered values equal dfm_tools; cache reuse, coordinate fallback, corrupt reference selection, invalid identity, and changed topology producing two plans are tested; (local) `r209` merge plan built |
| 2.2 | Face and edge `MapReader`, edge merge plan (D67), quality checks, `times.nc`, `TimeAxis` (merge, duplicates, tz, selection, matching) | Implemented and tested on local synthetic data: all nine expected `time_axis.csv` rows reproduced; face/edge values scatter to the checked grids; boundary duplicates, corrupt-partition policies, V-OUT-030/031/032/033 and V-TIME-* covered. P-drive acceptance remains deferred per D65 |
| 2.2a | **Quantity engine** (§7.9a): raw/derived registry, dependency resolution, read-once memory cache, grid geometry in `grid.nc`, use the edge merge plan from WP 2.2, and all 2D quantities marked "v1" in the catalogue of §7.9a (Froude, specific discharge, energy head, Shields number, velocity direction and vector, water-level rate, vorticity, stream functions, total sediment thickness, thresholded morphological active area, sediment transport magnitude (per fraction and summed), bed-load transport magnitude, mean grain sizes, grain-size percentiles); entry point `d3dplot.quantities`. **Generator extension**: the synthetic case gets `mesh2d_ucx`/`ucy` and `mesh2d_u1` (edges) with an analytic vorticity, `mesh2d_czs`, `mesh2d_thlyr`, `mesh2d_sxtot`/`sytot` and `mesh2d_sbcx`/`sbcy` per fraction, and `.sed` diameters and `RhoSol` (one fraction with a different value, to test per-fraction densities); the MDU gets `Ag = 9.813` and `Rhomean = 1000` (as the real runs), plus expected values | Local synthetic acceptance: every raw input is read once per (sub-run, time) when shared; all v1 quantities match hand-computed/analytic values, including vorticity across partition boundaries; the configurable active-area threshold follows the Matlab `Ltot_thres` default and comparison rule; missing dependencies report V-VAR-002 with the quantity chain. Direct-NetCDF acceptance on r209 and Rijn `D16000` remains deferred per D65 |
| 2.2b | **3D quantities and sampling protocol** (§7.9a, §7.9b, D54, D55): layer dimension in `VariableSpec`, `zw`/`zc` (read or dfm_tools `reconstruct_zw_zcc`), vertical selections (`depth_average` with `z_range`, `elevation`), `velocity_magnitude_layer_mean`, `chloride_mass` (`sal2cl`), residence times (`.sub` with HYDROLIB-core `SubstanceModel`); the `Sampling` protocol with `GridSampling` only (§7.9b, D55). **Generator extension**: a 3D variant of the plain FM synthetic case (sigma layers; `mesh2d_ucmag` per layer, `mesh2d_sa1`, `mesh2d_cTR1`/`mesh2d_dTR1`, `mesh2d_flowelem_zw`/`zc`; a `.sub` with `RcDecTR1`), with analytic expected values | Every 3D quantity and vertical selection equals its analytic value; the dummy-sampling test of §7.9b passes; a section quantity in `map2d` → V-VAR-006 |
| 2.3 | Execution: tasks, planner (one `ProcessTask` per simulation × sub-run for all analyses, D27), fingerprints and skipping, serial/process executors, worker init, logging queue, run summary, **diagnostic file** (D41), sharding | Parallel-equivalence and resumability tests pass; the `.dia` of a synthetic run lists the selected times, the resolved duplicates and the masked corrupt outputs of `corrupt_partition` |

Per D65, use generated local cases for development, automated tests, and performance work; retain the real r209 and Rijn acceptance cases as a final read-only gate after local implementation is complete. Any run that reads P-drive inputs must direct generated output to a local `output.root`.

### 15.4 Phase 3: `map2d`

| WP | Content | Done when |
|----|---------|-----------|
| 3.1 | Process through the quantity engine (raw and derived quantities, selections, files §9.5) | Implemented and locally validated: the synthetic integration test verifies `water_depth`, `froude_number`, `vorticity`, selected `d50`, and `water_level_rate` cache values/metadata, plus V-VAR-005 for retained extra dimensions |
| 3.2 | Plot from persisted map2d caches: **common finalisation** (§7.14a: style, language, hooks with `hooks_mode`, metadata, guarded writer), **shared views** (§7.14b: `MapView`, `ExtentSet`, draw-once templates, fingerprinted visible-face subsets, polygon/raster/auto render modes, comparisons incl. regridding, naming) | Synthetic tests cover polygon/raster rendering, time and simulation comparisons with regridding, effective figure settings and `days_since` limits, fresh-figure hook isolation by default plus explicit per-image snapshots, pixel-identical `--fresh-figures`, extent-cache and figure-cache reuse, and V-PLOT-001/002. Map decorations and overlays remain WP 3.3 |
| 3.3 | Overlays: `.ldb` (HYDROLIB-core `PolyFile` + guarded cache), river-km labels, background tiles (contextily + persistent cache, D42) | Implemented and locally validated: synthetic tests cover `.ldb` parsing/cache reuse/clipping and input snapshots, independent river-km label/window files, provider attribution and offline reuse/fallback, and V-LDB-001/V-RKM-001/V-ENV-001/002; tile fetches are stubbed, with no network in CI |
| 3.4 | Map measurements: catalogue, rasters (VRT, factor, mosaic, resampling), polygons (filters, id matching), differences, panel, `color_limits_from_measurements` | Implemented and committed on `feat/map2d-measurements` (`6da45e8`): cross-platform catalogue/source paths and naive-time offsets; CSV/YAML catalogue metadata and fingerprints; raster windows and nearest-neighbour alignment; polygon filters and deterministic difference matching; side-by-side value/time-difference panel; measurement-driven limits. Synthetic acceptance covers time tolerance, cm factor, shifted raster grid, shuffled polygon IDs, and no measurements on simulation differences |
| 3.5 | Provenance, `replot`, `plot --set`, movies | Implemented locally on `feat/map2d-provenance`: ordered/grouped atomic MP4 output and fingerprinted reuse; guarded `run_<timestamp>.yaml`/`latest.yaml` with replayable absolute paths and effective defaults; cache-backed `plot` from a YAML or output directory, including repeated `--set`; PNG provenance selectors and single-figure `replot`. Tests verify directory replay/cache reuse, pixel-identical replot, only the requested figure being rewritten, invalid metadata V-PLOT-005, and actual MP4 encoding. All 218 CI-eligible tests and local quality checks pass. A two-frame synthetic cache takes 0.910 s for fresh plotting, 0.517 s for full cache reuse, and 0.757 s for single-figure replay; optional encoder failures use V-ENV-001/V-PLOT-004 |

### 15.5 Phase 4: `river_km_statistics`

| WP | Content | Done when |
|----|---------|-----------|
| 4.1 | Shared river-km reader and packaged providers (Rijntakken/Maas/generic), branch resolution, irregular-centre edges and lengths, bend cutoffs, interpolation, angles, cut lines, segment polygons, and geometry validation | Implemented and locally validated on `feat/river-km-providers`: property tests; Rijntakken track tables and Maas transition; generic branch ambiguity, duplicate/range failures, and synthetic coordinates/polygons match hand-computed results. All 247 CI-eligible tests, strict mypy (79 files), strict MkDocs, changed-file Ruff/format, and diff checks pass |
| 4.2 | Region loading and membership, segment assignment, coverage/overlap checks, and plot-ready diagnostic results (D75); in-memory calculation core only (D77) | `cells_per_segment_250m.csv` reproduced; boundary, hole, multipart, overlap, and coverage cases tested |
| 4.3 | Implemented locally on `feat/river-km-statistics`: guarded `.rkm` input and region/segment geometry caches; shared-`ProcessTask` quantity-engine processing (including derived quantities); float64 segment statistics stored as float32; tested post-processing kernels (slope, × width, region differences, relative simulation differences, cumulative values and time averages) | Every row in both synthetic simulations' `smb_*.csv` files across all exact regions matches at float32 precision, including NaN/Inf masking; the joint M2D+SMB test reads `mesh2d_mor_bl` once per selected time; component labels come from `.sed`; flow width is stored and verified |
| 4.4 | Plots on the shared views: profile/space-time/time-series views, groupings, region groups, `x_limits`, **lines vs stacked areas** for multi-component variables (`display`), **diagnostic maps** `segment_map` (full model + windows along km) and `segment_membership` on `MapView`; hooks through the finalisation | Pytest-mpl baselines for stacked-area and date-formatted space-time figures; synthetic acceptance covers all five views, x-limits, time averages, hooks, fresh-render pixel identity, numeric simulation differences, and diagnostic maps with D78 membership bounds |
| 4.5 | Profile measurements: guarded per-variable `.mat` v5/v7 → NetCDF conversion and cache, fixed-offset time interpretation, Matlab-compatible nearest-time/profile and nearest-km/time-series matching, explicit region-difference measurements, and plotting hooks/fingerprints | Implemented and locally validated on `feat/river-km-measurements`; synthetic conversion, matching, time-difference overlays, simulation-difference omission, region-difference overlays, hook context, input immutability, and guarded no-overwrite CLI conversion pass focused tests; the 283-test CI-eligible suite passed before the final test-only regression additions |

### 15.6 Phase 5: hardening and v1.0

| WP | Content |
|----|---------|
| 5.1 | Acceptance case 1 on `r209` (§13.4) incl. coordinate-based face matching and performance targets |
| 5.2 | Implemented and locally validated on `feat/matlab-comparison`: coordinate-matched bed-level and derived-specific-discharge maps, plus all eight SMB statistics for both simulations; generated current-version references in a new local case without replacing the original |
| 5.3 | SLURM stub (`slurm write`), `--shard` documentation |
| 5.4 | Documentation (user guide, references, "from Matlab", gallery), agent skills |
| 5.5 | Release v1.0 on **PyPI** as `delft3d-plot` (D44): GitHub Actions workflow on tags (`v*`) building sdist + wheel with hatchling, publishing with PyPI Trusted Publishing (no tokens); TestPyPI dry run first; GitHub release with the changelog. Requires WP 0.2 |

### 15.7 Later

**PRF** (reading along a line, D55: `LineSampling`, section quantities, `profile` analysis, `SectionView`, §7.9b); HIS, M1D, HFM and GRD analyses; 3D-specific views (vertical profiles, vertical sections); `morphodynamic_celerity` with a port of ECT (D54); Delft3D4; more figure formats; full SLURM support; MAT v7.3 measurements; model − measurement maps.

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
| HYDROLIB-core rejects MDUs with keywords from newer kernels (D22) | Validation stops | Clear V-MDU-001 message with the keywords; contribute them upstream (same route as §14.1); pin a HYDROLIB-core version that knows the kernel versions in use |
| HYDROLIB-core release with PR 1 not available when d3dplot is ready | PyPI release blocked | Start WP 0.1 early; the release does not depend on it before WP 5.5 |
| Large grids are slow to plot | Slow plots | Visible-face subsets, raster mode |
| Windows path length, permissions on shared drives | Failed writes | V-OUT-040/041; short names; `output.root` |
| Offline machines (tiles) | Missing backgrounds | Tiles optional; persistent contextily cache, shareable (D42); V-ENV-002 |
| Scope creep beyond v1 | Delay | Plugin architecture; v1 scope fixed (§1.3) |

---

## 17. Open questions

Resolved with the user (v6.1–v6.3):
* duplicate morphological times → last, logged (D25);
* corrupt output → mask, logged (D23);
* a diagnostic file for every run (D41);
* `.ldb` = Tekal `.pli`/`.pol` format, read with HYDROLIB-core (D43);
* the Maas `.ldb` recreated from `j21_ldb.mat` (D45);
* background tiles with contextily and its cache (D42);
* uv (D28); float32 cache (D26, v6.3);
* the MDU is read with HYDROLIB-core `ResearchFMModel` after modifying HYDROLIB-core (D22, §14.1);
* the corrupt `r209` partition has been reported by the user;
* distribution on PyPI as `delft3d-plot` (D44);
* hooks for every figure through a common finalisation (D46); shared map/line building blocks (D47);
* SMB stacked areas and diagnostic maps are planned (§8.2);
* D24 and D27–D40 agreed; D27 refined to time blocks so plain FM simulations also run in parallel (v6.7);
* HYDROLIB-core PR 1 scope agreed with the maintainer: 6 `[sediment]` + 3 `ResearchGeometry` keywords (D51);
* second acceptance case: Rijn plain FM run `D16000` (D52, §5.5);
* all Matlab derived quantities catalogued (§7.9a); quantity engine agreed (D50);
* constants `g`, `ρ` from the MDU and `ρ_s` per fraction from the `.sed` (D53);
* Rijn case: only check that M2D figures are generated (§13.4a);
* all quantities in v1 except `morphodynamic_celerity` (D54) and the section quantities;
* reading along a line (PRF) is a second phase; the architecture keeps an extension point (D55, §7.9b);
* the Matlab reference run of the synthetic case is done (the generator needed explicit `statis_plot` and empty `rkm_track`);
* upstream changes are developed locally; the user is a contributor (D49). A local feature branch is recommended, local `main` is possible (skill `upstream-contribution`);
* agent guidance files (D48, §13.6).

No new user design decisions are pending. The official D-Flow FM default for omitted `.sed` `RhoSol` remains a technical question; until verified, the quantity engine does not guess a value. Explicit-time behavior with an unset tolerance, the WP 2.2 edge-plan scope, Matlab quantity conventions, one-based Matlab time indices, and the meaning and threshold of `ba_mor` are recorded as D66–D70.

Scheduling (no decision needed): the HYDROLIB-core change (WP 0.1, §14.1) is done as the first task of Phase 1, on a local branch, because reading the MDU (WP 1.5) depends on it. The merge plan (D21) is agreed in general terms (v6.6).

## 18. Glossary

| Term | Meaning |
|------|---------|
| SMT | Simulation Management Tool: a D-Flow FM simulation split into consecutive sub-runs (`output/<n>`), e.g. a quasi-steady hydrograph |
| Sub-run | One D-Flow FM run within an SMT simulation |
| Partition | Part of the domain computed by one MPI process; one map file `<stem>_<pppp>_map.nc` |
| Ghost cell | A face stored in a partition file but owned by another partition (`mesh2d_flowelem_domain ≠ p`) |
| Merge plan | Per partition: owned local face (and edge) indices and their position in the merged grid |
| Quantity engine | The single service that reads raw model variables and computes derived quantities for all analyses (§7.9a) |
| Raw / derived quantity | Stored in the map file / computed from other quantities (e.g. Froude, vorticity) |
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
| `tim_tol` | `times.tolerance` | |

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
| `only_adhoc`, `adhoc` | plugin analyses |
| `function_handles` (all plots), `p_single_function_handles` (one view) | `hooks` at project, analysis or view level (§7.21) |
| `var` | `variables` |
| `var_idx` / `sum_var_idx` | `fractions` / `sum_fractions` |
| `layer`, `depth_average`, `depth_average_limits`, `elevation` | `vertical` |
| `tim` | `times` |
| `tim_type` | `times.axis: hydrodynamic \| morphological` |
| Matlab `tim = NaN` / `Inf` / integer | `times.at: all` / `last` / one-based integer index; vectors may combine indices with positive `Inf` (`{indices: [...], include_last: true}`), while any `NaN` selects all |
| `Ltot_thres` | `active_sediment_thickness_threshold_m` (analysis-level, default `0.001 m`; `ba_mor`) |
| `xlims` / `ylims` | `extents` (maps); `x_limits`, `y_limits` (line plots) |
| `clims`, `clims_diff_t`, `clims_diff_s`, … | `color_limits: {<variable>: {<comparison>: [[min, max], …]}}` |
| `ylims_diff_*` | `y_limits: {<variable>: {<comparison>: […]}}` |
| `filter_lims*` | `mask_range` |
| `cmap` | `colormap` |
| `clims_type`, `clims_type_var` | `color_limits_mode: fixed \| {days_since: date}` |
| `fig_print` | `figure.formats: [png]` |
| `fig_resolution`, `fig_size`, `font_size` | `figure.dpi`, `figure.size_cm`, `figure.font_size` (applied by the common finalisation, §7.14a) |
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
| — | `measurements.<variable>.time_zone` (fixed offset for naive catalogue times) |
| — | `measurements.<variable>.layout`, `mosaic`; `map.render` |

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
| `do_area` | `display: auto \| lines \| stacked_area` (per variable; auto = stacked area for multi-component variables) |
| `do_val_B`, `do_val_B_mor` | `multiply_by_width: none \| flow \| morphological` |
| `detab_ds` | variable `bed_slope_streamwise` |
| `tim_ave`, `tim_ave_type` | `time_averages: [{name, times: {at, axis, tolerance}}]` |
| `do_plot_inpolygon`, `do_plot_inpoly` | `diagnostics: [segment_map, segment_membership]` |
| `do_plot_structures` | `structures_overlay` (or a hook) |
| `plot_val0`, `do_staircase`, `do_marker`, `xdir` | `show_initial`, `staircase`, `markers`, `x_reversed` |
| `skip_if_not_found` | `on_missing_input: error \| skip` |
| `legend_file` | `legend_labels` |

### A.5 Variables (v1)

The complete model-quantity catalog (all v1 raw and derived registry entries, aliases, NetCDF candidates, dimensions, units, and label keys) is generated in [the variable catalogue](./variables.md). The compact table below lists the primary analysis variables; `bed_slope_streamwise` is an SMB analysis-level statistic rather than a model quantity.

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
| v6.71 | Completed WP 5.2 locally on `feat/matlab-comparison`: added a `requireslocaldata` comparison of bed-level and specific-discharge map caches by hydrodynamic time and 1 mm coordinate matching, plus all eight SMB statistics for both simulations; generated current-version local Matlab MAT references while preserving the original v0.1.0 case. Matlab vorticity output is not coordinate-matchable to the full grid (D82). The 284-test CI-eligible suite, both local comparison tests, strict mypy over 86 files, strict MkDocs, Ruff/format, and diff checks pass. The Matlab figure stage still errors in `fig_1D_01`; numerical references are complete. |
| v6.70 | Completed WP 4.5 locally on `feat/river-km-measurements`: guarded Matlab v5/v7 profile conversion/cache, fixed-offset timezone handling, profile/time-series/time-difference overlays, explicit region-difference measurements, and the `measurements convert` CLI. Added regression coverage for time-difference values and simulation-difference omission; the full 283-test CI-eligible suite and final focused WP 4.5 tests pass. Strict mypy over 86 source files, strict MkDocs, changed-file Ruff/format, and diff checks pass. P-drive acceptance remains deferred per D65 |
| v6.69 | Started WP 4.5 on `feat/river-km-measurements`; specified guarded per-variable `.mat` conversion/cache, fixed-offset precedence (D81), Matlab-compatible time and km matching, difference overlays, input immutability, and the standalone no-overwrite converter. WP 4.4 remains committed as `a52d8f6` |
| v6.68 | Completed WP 4.4 locally on `feat/river-km-plotting`: add pytest-mpl baselines for stacked-area profiles and date-formatted space-time figures; test all five views, x-limits, fresh-render pixel identity, hook context, and numeric r002−r001 comparison values; fix clipped space-time date labels by recalculating the layout after formatting. All 276 CI-eligible tests pass, with focused tests passing after the final plotting change; strict mypy, strict MkDocs, Ruff/format, and diff checks pass. WP 4.5 profile measurements is next |
| v6.67 | Continued WP 4.4 on `feat/river-km-plotting`: synthetic acceptance now covers both diagnostic maps, region and all-simulation grouping paths, r001/r002 simulation differences, region differences, three-component stacked profiles, slope-only cache storage, hooks, and cache reuse. Fixed inspection-normalized UGRID padding and cross-channel cut-line construction; added diagnostic translations (D80). All 274 CI-eligible tests, strict mypy over 85 files, strict MkDocs, Ruff, and diff checks pass. Image baselines remain |
| v6.66 | Continued WP 4.4 on `feat/river-km-plotting`: synthetic acceptance now covers both diagnostic maps, region and all-simulation grouping paths, region differences, three-component stacked profiles, slope-only cache storage, hooks, and cache reuse. Fixed inspection-normalized UGRID padding and cross-channel cut-line construction; added diagnostic translations (D80). All 274 CI-eligible tests, strict mypy over 85 files, strict MkDocs, Ruff, and diff checks pass. Multi-simulation comparison baselines and image baselines remain |
| v6.65 | Continued WP 4.4 on `feat/river-km-plotting`: process-to-plot synthetic acceptance now covers both diagnostic maps, grouped-region plots, and hooks. Fixed handling of inspection-normalized UGRID padding and cross-channel cut-line construction; added diagnostic translations (D80). All 274 CI-eligible tests, strict mypy over 85 files, strict MkDocs, Ruff, and diff checks pass. All-simulation comparisons, region differences, stacked-area integration, slope-only cache storage, and image baselines remain |
| v6.64 | Continued WP 4.4 on `feat/river-km-plotting`: resolve time-average selectors through the shared `TimeAxis`, expose them for supplementary process planning (D79), and add synthetic processing-to-plot coverage for profile, space-time, time-average, and cache reuse. All 273 CI-eligible tests, strict mypy over 85 files, strict MkDocs, changed-file Ruff/format, and diff checks pass. Grouping, diagnostics, hooks, and slope-only cache acceptance remain |
| v6.63 | WP 4.4 remains in progress on `feat/river-km-plotting`. Resolve configured SMB time-average outputs through the shared `TimeAxis` and plan them as supplementary selections, avoiding duplicate output reads (D79); add a focused planner integration test. Thirty focused river-km/view/planner tests pass; full synthetic plot acceptance and package checks remain |
| v6.61 | Completed the WP 4.3 processing implementation: guarded river-km point and region/segment caches; shared per-time quantity processing for M2D and SMB; component labels and per-component finite masks; all synthetic SMB CSVs reproduced at float32 precision; and tested post-processing kernels. Clarified the intentional multi-component-mask difference from Matlab and the WP 4.3/WP 4.4 post-processing boundary. |
| v6.60 | Started WP 4.3 on `feat/river-km-statistics`; documented stable V-SMB-014 for time averages with fewer than two ordered outputs. Processing reuses the per-time quantity engine, preserves per-component finite masks, uses N−1 spatial standard deviation and area-weighted means, and applies centre-to-corner time weights. |
| v6.59 | Completed WP 4.2's in-memory region loading, CRS transformation, boundary-inclusive membership, deterministic segment assignment, overlap/coverage results, and concave-hull option on `feat/river-km-regions`; documented D75–D77 and V-SMB-002. The synthetic CSV acceptance test reproduces exact face IDs for three regions and 40 segments. All 254 CI-eligible tests, strict mypy over 80 files, strict MkDocs, changed-file Ruff/format, and diff checks pass. On 6,406 faces and 40 segments, mesh/segment geometry took 0.257 s and region loading/assignment for three regions 0.130 s (0.388 s total). Guarded geometry-cache wiring and SMB processing are WP 4.3 |
| v6.58 | Completed WP 4.1 locally on `feat/river-km-providers`: shared fixed-format reader, packaged Rijntakken/Maas/generic provider data, branch and cutoff resolution, irregular-centre edges/lengths, interpolation, cut lines, polygons, and validation. All 247 CI-eligible tests pass; strict mypy over 79 files, strict MkDocs, changed-file Ruff/format, and diff checks pass. WP 4.2 region assignment/coverage is next |
| v6.57 | Started WP 4.1 on `feat/river-km-providers`; recorded the data-driven provider contract, Matlab geometry flags, irregular-centre/cutoff rules, and V-RKM-005 before implementation. |
| v6.56 | Completed WP 3.5 locally on `feat/map2d-provenance`: canonical resolved configs with replayable paths, `plot` from YAML/output directories with `--set`, PNG replot selectors, and one-figure pixel-identical replay; added V-PLOT-005. All 218 CI-eligible tests pass, strict mypy over 75 source files, strict MkDocs, Ruff/format, lock, and diff checks pass. Two-frame synthetic-cache timings: fresh plot 0.910 s, cache reuse 0.517 s, single-figure replot 0.757 s; 945×591 MP4 encode/reuse 0.559/0.003 s. CLI `run` integration and P-drive acceptance remain |
| v6.55 | Completed the movie-generation slice of WP 3.5 on `feat/map2d-provenance`: ordered MP4s per map2d frame group, atomic guarded writing, adjacent fingerprint metadata and reuse, plus V-ENV-001/V-PLOT-004. All 215 CI-eligible tests, strict mypy over 74 files, strict MkDocs, Ruff/format, lock, and diff checks pass. Two 945×591 synthetic frames encode in 0.559 s and reuse in 0.003 s. Provenance replay and CLI `plot --set` remain |
| v6.54 | Started WP 3.5 on `feat/map2d-provenance`; implemented ordered map-frame MP4 output with guarded atomic writes, duration and frame fingerprints, reuse, V-ENV-001/V-PLOT-004, and configuration documentation. Twenty-seven focused tests including actual MP4 encoding, strict mypy over 74 source files, strict MkDocs, and changed-file Ruff/format pass; full-suite and provenance/CLI replay remain |
| v6.53 | Committed WP 3.4 measurements as `6da45e8` after 211 CI-eligible tests, strict mypy over 73 source files, strict MkDocs, Ruff/format, lockfile, and diff checks passed. Started WP 3.5 on `feat/map2d-provenance`; movie generation will follow the existing `MovieTask` and optional `movies` extra contract, then provenance replay and CLI overrides |
| v6.52 | Completed and locally validated WP 3.4 map measurements on `feat/map2d-measurements`: CSV/YAML catalogues, guarded metadata and prepared-value caches, raster mosaics/resampling, polygon filters/differences, side-by-side panels, and measurement-driven colour limits. All 211 CI-eligible tests, strict mypy over 73 source files, strict MkDocs, changed-file Ruff/format, lockfile, and diff checks pass. A synthetic 1024×1024 raster prepares in 0.1945 s cold and 0.0285 s from cache |
| v6.51 | Committed WP 3.3 overlays as `53d1bb7` after 201 CI-eligible tests, strict mypy, strict MkDocs, changed-file Ruff/format, lockfile, and diff checks passed. Started WP 3.4 on `feat/map2d-measurements`; its cache, preparation, difference, and side-by-side rendering flow follows the existing D16–D17 and §7.15 contract |
| v6.50 | Completed and locally validated WP 3.3 map overlays on `feat/map2d-overlays`: guarded `.ldb` caching and extent clipping, independent river-km labels/windows, optional contextily tiles with persistent allow-listed caching, attribution, and offline fallback. All 201 CI-eligible tests, strict mypy (70 source files), strict MkDocs, changed-file Ruff/format, `uv lock --check`, and `git diff --check` pass; next work package is WP 3.4 map measurements |
| v6.49 | Started WP 3.3 on `feat/map2d-overlays`: implement shared map decorations, HYDROLIB-core `.ldb` cache, independent river-km labels, and optional contextily tiles while keeping every cache location explicitly write-guarded |
| v6.48 | Implemented D73: rebuild each hooked image by default to guarantee isolation from arbitrary Matplotlib mutations; keep draw-once rendering when hooks are absent and retain snapshots as an explicit performance opt-in. All 196 CI-eligible tests pass, as do strict mypy (68 source files), Ruff/format, strict MkDocs, and `git diff --check` |
| v6.46 | Committed WP 2.3 execution orchestration and WP 3.1 map2d quantity processing (`735fb38`); all 191 CI-eligible tests and local quality checks pass. Started WP 3.2 design: map plots consume persisted data caches, cache visible-face subsets, reuse draw-once templates, support configured comparisons/render modes, and route figures through common finalisation and a guarded writer |
| v6.45 | Implemented and validated the WP 3.1 map2d quantity processor: guarded fingerprinted NetCDF outputs, quantity-choice diagnostics, scalar-dimension validation, and synthetic acceptance for raw/derived/selected quantities. Revalidated `run_process_stage` diagnostics and summaries; all 191 CI-eligible tests pass, along with strict MkDocs, targeted strict mypy, Ruff/format, and `git diff --check` |
| v6.44 | Connected process-task execution to diagnostics and atomic run-summary CSV persistence through `run_process_stage`; process workers now receive the task callable once through the spawn initializer, avoiding repeated context serialization. Added integration coverage for contextual callables and diagnostic/summary output. Validation of this increment is pending |
| v6.43 | Wired one-pass NetCDF fingerprint reads into cache-aware process-task planning with `OutputCacheRequest`/`read_output_cache_states`; matching outputs are omitted and stale outputs are scheduled. Added synthetic resumability coverage. All 189 CI-eligible tests, strict mypy, changed-file Ruff/format, and strict MkDocs pass. A five-run local benchmark over 100 temporary NetCDF outputs measured 0.198 s median for fingerprint indexing and 0.224 s for complete cache-state construction. Processing-stage data I/O and runner/summary integration remain |
| v6.42 | Continued WP 2.3 on `feat/execution`: added one-pass NetCDF fingerprint indexing, unique task-key validation, a guarded debug `.log` with worker queue handlers, and atomic run-summary CSV persistence; validated cache reads, process logging, and summary output locally. Process-stage data I/O, cache-aware task planning, and end-to-end `run` integration remain |
| v6.41 | Started WP 2.3 on `feat/execution`: added shared time-block planning, stable sharding, per-output cache decisions, resource-aware worker sizing, serial/spawn execution with task-failure isolation, worker logging-queue setup, run summaries, and guarded line-flushed `.dia` diagnostics for `validate` and `inspect`; added synthetic diagnostic coverage for duplicate times and corrupt-partition masking; process-stage integration and complete `.log`/summary persistence remain |
| v6.40 | Completed the WP 2.2a quantity engine and local synthetic acceptance; 176 CI-eligible tests, strict mypy, strict docs, Ruff/format, and `git diff --check` pass locally; a one-time 20-quantity synthetic benchmark (median of three runs) improved from 0.067 s without shared caching to 0.046 s with it, reducing raw reads from 31 to 16 (1.47×); documented full-distribution grain-size scalars and the unverified `RhoSol` default; deferred `.dia` choice logging to WP 2.3 and real r209/Rijn acceptance per D65 |
| v6.39 | Recorded D71: water-level rate uses map-output intervals, reads `mesh2d_s0` when available, falls back to the preceding map output, and is NaN at the first output |
| v6.38 | Clarified that NaN in a Matlab time-selector vector selects all and positive Inf can accompany one-based indices; documented the equivalent `include_last` mapping |
| v6.37 | Recorded D69–D70: Matlab-compatible time-selector inputs with one-based integer indices; corrected `ba_mor` to configurable thresholded active sediment area |
| v6.35 | Completed WP 2.2 locally on `feat/map-reader-time-axis`: added the time-quality cache and `TimeAxis` selections/matching, verified nine expected SMT times and boundary/corruption/time-zone behavior with synthetic tests, and documented the runtime contract; P-drive acceptance remains deferred per D65 |
| v6.36 | Updated the handover for `feat/quantity-engine` and WP 2.2a; recorded Matlab-compatible velocity direction and grain-size percentile conventions verified from the reference readers |
| v6.34 | Specified the `times.nc` coordinate and quality-mask schema and the deterministic reference-partition rule for boundary-value checks; WP 2.2 is implementing the time-quality cache and `TimeAxis` locally |
| v6.33 | Added D66 (unbounded nearest-output matching when explicit `times.tolerance` is unset) and D67 (edge merge plan in WP 2.2); aligned WP 2.2/WP 2.2a scope |
| v6.32 | Recorded D65: develop and test on local synthetic cases first, then perform the required P-drive acceptance read-only with a local output root; started WP 2.2 on `feat/map-reader-time-axis` |
| v6.31 | Implemented WP 2.1 locally on `feat/inspect-phase`: topology inspection, merge plans and grid caches, `inspect` CLI, validation integration, coordinate fallback, cache reuse, corruption and changed-topology tests; fixed coordinate matching to use stored merged face centres. Synthetic acceptance passes; r209 discovery took 200.628 s but the full inspect did not finish within 20 min and was stopped at the user's request. Resume with r209 profiling/acceptance before WP 2.2 |
| v6.30 | Completed WP 1.11 locally on `feat/cli-part-1`: added the console and module entry points, five inspection/setup commands, variable/header validation, documentation, and tests; 129 CI-eligible tests, strict mypy, Ruff/format, strict MkDocs, and `git diff --check` pass |
| v6.29 | Started WP 1.11 on `feat/cli-part-1`; specified read-only header inspection, validation before the WP 2.1 inspect cache, direct raw-name availability, and no-overwrite `init` |
| v6.28 | Completed WP 1.10 locally on `feat/variables`: added metadata for all 10 raw and 23 derived v1 quantities, canonical/alias/NetCDF resolution, V-VAR-001 suggestions, generated catalogue docs, and three related label translations; 115 CI-eligible tests, strict mypy, changed-file Ruff/format, and strict MkDocs pass; next handover is WP 1.11 |
| v6.27 | Started WP 1.10 on `feat/variables`; recorded the metadata-only variable registry, case-insensitive canonical/Matlab/registered-NetCDF lookup, V-VAR-001 suggestions, and the WP 2.2a/b boundary for computation callbacks |
| v6.26 | Completed WP 1.9 locally on `feat/labels`: added the Matlab-seeded catalog, completed all en/nl/es translations, comparison templates, unit scales, extra dictionaries, and metadata fallback; all 100 CI-eligible tests, strict mypy, Ruff/format, strict MkDocs, and wheel resource checks pass; next handover is WP 1.10 |
| v6.25 | Completed WP 1.8 locally on `feat/store`: output layout, marked-root write guard, atomic writers, recursive fingerprints, V-OUT-041 checks, input-snapshot helper, and output-store documentation; 82 CI-eligible tests, strict mypy, Ruff/format, and strict MkDocs pass; next handover is WP 1.9 |
| v1 | First architecture proposal |
| v2 | Decisions on scope (M2D, SMB; FM, SMT), cache, reprocessing, status, replot, naming, skills, upstream |
| v3 | SMB geometry specification, one file per time step, Windows process pool, draw-once plotting, labels, GPL-3.0 |
| v4 | Measurements, read-only inputs, hooks, region groups, SLURM stub, acceptance case |
| v5 | Answers on measurements, `r209` findings, synthetic SMT case specification and generator |
| v6 | Thorough review against real data (merge plan, tolerant MDU parsing, corrupt partitions, time zones, duplicate morphological times, float64, task granularity, path lengths); `.ldb`-only land boundaries; independent review (14 issues fixed: inspect phase, recursive fingerprints, topology signatures, deterministic sharding and geometry, derived-statistic definitions, verified versions); consolidated, self-contained plan with handover, file formats, validation catalogue and work packages |
| v6.1 | User answers: last duplicate and masking of corrupt output, both written to a new per-run diagnostic file (`.dia`); `.ldb` read with HYDROLIB-core; contextily cache replaces `earth_tiles`; uv and float64 agreed; HYDROLIB-core keyword contribution analysed (`ResearchFMModel` fails only on 7 keywords) |
| v6.11 | Reading along a line (PRF) moved to a second phase (D55); section quantities later; `Sampling` extension point (§7.9b) tested in WP 2.2b |
| v6.12 | Added D56 and the non-recursive `iniFieldFile` type fix to WP 0.1 after reproducing a `DiskOnlyFileModel`/`IniFieldModel` mismatch on synthetic and real MDUs; user approved the scope extension |
| v6.13 | WP 0.1 complete locally in HYDROLIB-core commit `54011e0d`; recorded upstream status and the pending minimum release; next handover is Phase 1 |
| v6.14 | WP 1.1 package skeleton, dependency lock, CI/docs setup, contributor guide and smoke test implemented locally; local checks pass and remote CI remains pending |
| v6.15 | WP 1.2 session-scoped synthetic-case fixture and integration smoke test implemented; full generator self-check runs once per pytest session; handover moves to WP 1.2a |
| v6.16 | Added 40-time `fm_partitioned` and `fm_single` plain-FM cases, expected time axis/SMB statistics and generator checks; discovery acceptance remains for WP 1.6; next handover is WP 1.3 |
| v6.17 | Resolved configuration-path conflicts with user decisions D57–D58: nested `times: {at, axis, tolerance}` and shared `map.render`; WP 1.3 started on `feat/config-models` |
| v6.18 | Completed WP 1.3 locally: built-in configuration models and IO, documented model defaults and actual package layout, deferred plugin config integration to the analysis registry (D59), and added the configuration page to MkDocs navigation; next handover is WP 1.4 |
| v6.19 | Started WP 1.4 on `feat/validation-framework`; specified V-CFG-004 as matching list row counts for x/y figure limits (D60) |
| v6.24 | Completed WP 1.7 locally: status API and stale-time configuration, `not_started` inspection, caller-writer JSON/CSV serialization, shared NetCDF lock, and bounded sub-run status scan; 69 CI-eligible tests, strict mypy, changed-file Ruff/format, and strict MkDocs pass; read-only r209 is finished, status scan 27.371 s versus 433.548 s serially; next handover is WP 1.8 |
| v6.23 | Completed WP 1.6 locally: plain-FM/SMT discovery, classic NetCDF header validation, D61's bounded I/O thread pool, and full r209 acceptance (195 sub-runs/8,580 maps in 99.283 s); 51 CI-eligible tests, strict mypy, Ruff/format, and strict MkDocs pass; next handover is WP 1.7 |
| v6.22 | Implemented WP 1.5 locally, including DIMR resolution and parallel-control regression coverage; integrated the local HYDROLIB-core fix with WP 0.1, verified all 31 CI-eligible tests and real r209 input parsing; next handover is WP 1.6 |
| v6.21 | Started WP 1.5 on `feat/model-input`; added MDU, `.mor`, and `.sed` adapters with V-MDU-001 reporting and synthetic tests; real r209 model inputs passed read-only parsing; identified the HYDROLIB-core DIMR `startGroup` mismatch |
| v6.20 | Completed WP 1.4 locally: structured validation issues and text/JSON reports, V-CFG-001..004 with static reference, duplicate-root/label, and effective x/y limit checks; 24 CI-eligible tests and all configured checks pass; next handover is WP 1.5 |
| v6.10 | All catalogue quantities in v1 except `morphodynamic_celerity` (D54): 3D layers, residence times (`.sub` via HYDROLIB-core), section quantities (API), new WP 2.2b, V-VAR-006; no open questions |
| v6.9 | Constants from the model (D53): `Ag`, `Rhomean` from the MDU, `RhoSol` per fraction from the `.sed`; Shields per fraction; Rijn case limited to M2D figures; D50 agreed |
| v6.8 | HYDROLIB-core PR 1 reduced to the agreed 6 `[sediment]` + 3 `ResearchGeometry` keywords (D51); catalogue of all Matlab derived quantities with definitions and phases (§7.9a); Rijn plain FM run as reference and acceptance case 2 (§5.5, §13.4a, D52); DIMR with several components, `StartDateTime`, V-TIME-012 |
| v6.7 | Detailed HYDROLIB-core plan (§14.1): spec comparison shows 22 missing standard [sediment] keywords, PR 1 completes the section; D27 agreed and refined to time blocks; parallelism for plain FM vs SMT (§7.13); plain FM synthetic variants planned (WP 1.2a) |
| v6.6 | Merge plan agreed in general terms; D27 explained; HYDROLIB-core change scheduled as first task of Phase 1 |
| v6.5 | Quantity engine for raw and derived quantities (Froude, vorticity, …) shared by all analyses, edge merge plan, one process task per sub-run for all analyses (D50, D27 revised, §7.9a); upstream work on local `main` or branch (D49) |
| v6.4 | Agent guidance (`AGENTS.md`, plotting instruction, two skills); Matlab reference run of the synthetic case done; local upstream branches (user is contributor) |
| v6.3 | Common figure finalisation with hooks for all figures (D46); shared plot building blocks for M2D and SMB diagnostic maps, windows along a river-km file separate from the label file (D47); SMB lines vs stacked areas for multi-component variables; SMB diagnostic maps (`segment_map`, `segment_membership`); float32 cache (D26); D24, D27–D40 agreed |
| v6.2 | MDU read with HYDROLIB-core `ResearchFMModel` after modifying HYDROLIB-core (detailed plan §14.1, cross-checked with the official `mdu.json`); Maas `.ldb` recreated from the `.mat` (`tools/mat_to_ldb.py`); PyPI distribution as `delft3d-plot`; plain-language explanation of the merge plan |
