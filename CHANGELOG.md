# Changelog

All notable changes to Delft3D_plot are documented here. Entries are generated
from conventional commits with Commitizen.

## v0.2.0rc0 (2026-11-12)

First release candidate of d3dplot, the Python successor of the Matlab D3D_plot tool. Published to TestPyPI only.

### Added

- Postprocessing and plotting of D-Flow FM and Delft3D 4 (SMT) output: discovery, inspection, map2d figures with overlays and measurement panels, map1d, river-km plots and PRF profile figures.
- Native model-input readers for MDU, DIMR, polyline (`.pli`, `.pol`, `.ldb`) and substance and sediment files.
- Validated YAML project configuration, with `d3dplot` commands for inspection, validation, processing and plotting.
- Quantity engine with a canonical variable registry, including initial three-dimensional quantities.
- Guarded output store, localized label catalogue, provenance replay for map2d figures, and guarded movie generation.
- Delft3D 4 trim-run support for discovery, inspection, map reading and map2d processing.
- Guarded SLURM array job template.
- Synthetic FM and SMT test case generators.

### Changed

- The distribution is named `d3dplot`, matching the import and CLI name. Install it with `pip install d3dplot`.
- HYDROLIB-core is no longer a dependency. Unknown and obsolete MDU keywords are accepted with an information note in the `.dia` file.
- Configuration options that are accepted but not implemented are rejected during validation.

### Fixed

- Stale PRF profile caches are rebuilt instead of failing the run.
- Validation reports model time and path hazards.

### Notes

- The MDU keyword table (`src/d3dplot/model_input/mdu_keywords.py`) comes from Deltares' MDU specification, `Deltares/Delft3DFM.File.Specifications` (`mdu.json`, pinned at commit `fe15f64`). That repository has no licence file. The table is used on the basis of the maintainers' statement as Deltares contributors. Confirm the licence position before a final release.

### Refactor

- remove direct HYDROLIB-core dependency
- **map1d**: rename the M1D analysis to map1d
- **execution**: move processing runner into package
- **execution**: extract native project dispatch

### Perf

- **inspect**: parallelize subrun metadata scans
