# Agent guidelines: Delft3D_plot (`d3dplot`)

Python successor of the Matlab tool `D3D_plot`: postprocessing and plotting of D-Flow FM / SMT results.
**The plan is [docs/architecture.md](docs/architecture.md). Read its §0 (session handover) before any work**, and follow its decisions (§2, IDs `D<n>`). If a task conflicts with the plan, stop and ask; do not silently deviate. Record new decisions in §2 and keep §0.1 current.

## Never

- **Never create, modify or delete anything on the P-drive (`p:\`, `/p/`).** Reading is allowed. Tests and runs on P-drive data use `output.root` in a local folder.
- Never modify tool inputs (model files, measurements, GIS files). All writes go through the store's write guard (plan §7.17).
- Never commit project data. Local data lives in `data_local/` (git-ignored).
- Never weaken a validation rule or a test tolerance to make a test pass.
- Never use `generate.py --discard-matlab-reference` without the user's consent: it deletes the Matlab reference results.

## Reuse before writing code

Use **HYDROLIB-core** for model input (MDU via `ResearchFMModel(path, recurse=False)`, `.pli/.pol/.ldb` via `PolyFile`, DIMR, INI parser) and **dfm_tools / xugrid** for model output (grids, partitions, layers, rasterising). Before writing a reader, parser or geometry helper, search `c:\checkouts\HYDROLIB-core` and `c:\checkouts\dfm_tools`. If something is missing or buggy there, fix it **upstream** (skill `upstream-contribution`) rather than working around it locally; a temporary adapter in `d3dplot` is allowed only with a note in plan §14.

## Performance is a requirement

- Measure before and after (`time.perf_counter`, `pytest-benchmark`); state timings in PR descriptions. Plan §7.22 has the targets.
- Read only what is needed: merge plan + direct netCDF4 slices (plan §7.6), not `open_partitioned_dataset` per sub-run (75 s vs 1 s).
- Open a sub-run once per task (plan §7.13), parallelise at task level only (no dask in workers, `OMP_NUM_THREADS=1`).
- Plot with draw-once templates (plan §7.14): build figures once, update data, never redraw the grid per image.
- Cache derived inputs (`.ldb`, river-km files, geometry) in the store, keyed by fingerprint.
- Cache values are float32 on disk, computations in float64 (D26).

## Reading model output and derived quantities

Analyses never read model files directly. They request quantities from the **quantity engine** (`variables/engine.py`, plan §7.9a), which reads each raw variable once per sub-run and time and computes derived quantities (Froude, vorticity, …) from a registry. A new quantity is a registry entry (`raw` or `derived` with `inputs` and `compute`), never code inside an analysis.

## Figures

Every figure goes through `plotting.finalize.finalize_figure` (plan §7.14a): style, language, user hooks, metadata, saving, closing. Never call `savefig`/`plt.close` in analysis code. Build plots from the shared blocks in `plotting/views/` (plan §7.14b). Details: `.github/instructions/plotting.instructions.md`.

## Names and options

- Reuse existing option names for the same concept (`times`, `times.axis`, `times.tolerance`, `comparisons`, `color_limits`, `extents`, `extents_along_river_km`, `river_km_labels`, `land_boundaries`, `hooks`, `display`, …). Check plan Appendix A and `src/d3dplot/config/` before adding a new one.
- Full English `snake_case`, no `do_` flags, enums instead of magic numbers, units in names when ambiguous (`half_width_m`).
- Every new option: pydantic field with description and default, Appendix A row if it replaces a Matlab flag, docs, test.

## Time

Time-zone-aware UTC in memory, fixed offsets only, CF numeric UTC on disk, file names by hydrodynamic time (plan §7.8). Never use naive datetimes in library code.

## Diagnostics

User-visible choices (selected times, duplicates, masked corrupt output, reused cache) are written to the run's `.dia` file (plan §7.19, D41). New validation checks get a stable code in plan §12.

## Build and test

```powershell
uv venv .venv --python 3.12
uv pip install --python .venv\Scripts\python.exe -e c:\checkouts\dfm_tools -e c:\checkouts\HYDROLIB-core -e .[dev]   # .[dev] once pyproject.toml exists (WP 1.1)
.venv\Scripts\python.exe -m pytest -m "not requireslocaldata"
.venv\Scripts\python.exe tools\synthetic_smt\generate.py --out <tmp>   # synthetic SMT test case (plan §13.3)
```

- Tests use the synthetic case and fixtures; P-drive checks are marked `requireslocaldata` and never block.
- `ruff check`, `ruff format`, `mypy` must pass. SPDX header `GPL-3.0-or-later` in every source file.
- Windows PowerShell 5.1: no `&&`; write multi-line commit messages to a file and use `git commit -F`.

## Commits

Conventional commits, one work package (plan §15) per branch/PR, and the trailer
`Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`.
