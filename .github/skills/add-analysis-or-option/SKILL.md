---
name: add-analysis-or-option
description: "Workflow to add or port an analysis (e.g. HIS, PRF, M1D, GRD from Matlab D3D_plot), a plot view, a variable or a configuration option to d3dplot. Use when implementing a new feature, porting a Matlab tag or flag, adding a variable to the registry, or adding a new figure type. Enforces reuse of HYDROLIB-core/dfm_tools, performance, shared plot blocks, common finalisation and consistent option names."
argument-hint: "What to add, e.g. 'HIS analysis' or 'option to mask dry cells in map2d'"
---
# Add an analysis, view, variable or option

Follow the plan in `docs/architecture.md`. Steps are in order; do not skip the first two.

## 1. Understand the reference
- Read plan §0, §2 (decisions) and the section of the feature (§7, §8).
- Porting from Matlab: find the tag in `c:\checkouts\oet_matlab\applications\vtools\D3D\plot\` (`gdm_check_tag_<TAG>`, `gdm_create_mat_<TAG>`, `gdm_plot_<TAG>`, `fig_*`). Write down every flag it reads (`isfield_default(flg_loc,'…')`) and what it does. Keep the behaviour, not the structure.
- Real data may be inspected read-only on `p:\` (plan §5). Never write there.

## 2. Look for existing functionality (reuse)
Search, in this order, and note what you will reuse:
1. `src/d3dplot` (views in `plotting/views/`, selections, store, comparisons, measurements, geometry).
2. HYDROLIB-core (`c:\checkouts\HYDROLIB-core\hydrolib\core`) for any model input file.
3. dfm_tools / xugrid (`c:\checkouts\dfm_tools\dfm_tools`) for model output, grids, layers, slicing, rasterising.
If a building block is missing upstream and is general, implement it upstream (skill `upstream-contribution`).

## 3. Design (write it into the plan first)
- Configuration model: reuse names from [the naming checklist](./references/naming-checklist.md) and plan Appendix A. New names only for new concepts. Add Appendix A rows for replaced Matlab flags.
- Stages: what is metadata (inspect), what is prepared once (prepare), what is per sub-run (process), what is per figure group (plot). Cache files and their fingerprints (plan §7.10).
- Figures: compose from `MapView`/`LineView`/`SpaceTimeView`; finish with `finalize_figure`; list the hook `context` fields.
- Validation codes (plan §12) and `.dia` messages (plan §7.19) for every user-visible choice.
- Performance estimate on the real case (plan §5.3 costs). Target: no repeated opening of sub-runs, no per-image grid redraw.
- Update plan §2/§8/§12/§15 (and §0.1 status) in the same PR.

## 4. Implement
- Package layout of plan §7.2: `analyses/<name>/{config,process,plot}.py`; register through the `d3dplot.analyses` entry point.
- New quantities (e.g. a new derived variable) go into the quantity engine registry (`variables/`, plan §7.9a) with `inputs`, `compute`, units, label key and a version; analyses only request quantities from the engine.
- Time-aware UTC, float64 computation, float32 storage, atomic writes through the store.
- No `savefig`/`plt.close` outside the finalisation.

## 5. Test
- Unit tests with hand-computed values for every formula.
- Integration test on the synthetic SMT case (`tools/synthetic_smt/generate.py --out <tmp>`); extend the generator if the feature needs data it lacks (new variable, new variant) and add expected values to `expected.py`.
- Image baselines (pytest-mpl), `--fresh-figures` identity, hook test, parallel equivalence, input-snapshot (read-only) test.
- Matlab comparison on the synthetic case when Matlab results exist (`data_local/synthetic_smt_01/simulations/*/mat`), with float32 tolerances.
- Benchmark for anything touching model I/O or plotting loops; report timings.

## 6. Finish
- `ruff`, `mypy`, tests green on Windows and Linux.
- Docs: configuration reference regenerates from the models; add a gallery example.
- Conventional commit with the Copilot trailer.
