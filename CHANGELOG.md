# Changelog

All notable changes to Delft3D_plot are documented here. Entries are generated
from conventional commits with Commitizen.

## v0.2.0rc0 (2026-11-12)

### Feat

- **d3d4**: read Delft3D 4 trim runs for processing
- **profile**: render PRF figures in the plot stage
- **analyses**: add PRF profile stage and figures
- **analyses**: validate PRF fraction and section options
- **analyses**: add PRF interface cache and section view
- **analyses**: add PRF profile process core and config
- **variables**: read PRF line topology from the inspection grid
- **variables**: cache PRF line crossings from .pli polylines
- **variables**: add PRF line sampling core
- **map2d**: move colour limits from filenames to a folder
- **plotting**: shorten figure filenames and reuse river-km profile templates
- **map2d**: draw velocity vector overlay
- **map2d**: mask values outside mask_range
- **tests**: add synthetic three-dimensional FM acceptance case
- **model-input**: add residence-time metadata support
- **variables**: add initial three-dimensional quantities
- **variables**: add sampling and vertical selection groundwork
- **slurm**: generate guarded array job template
- **river-km**: add profile measurement overlays
- complete river-kilometre plotting
- **river-km**: add region assignment
- **river-km**: add provider geometry
- **map2d**: add provenance replay and plot CLI
- **map2d**: add guarded movie generation
- **measurements**: implement map measurement panels
- **plotting**: add map overlays
- **plotting**: add map2d figure rendering
- **execution**: integrate map2d process stage
- **variables**: implement 2D quantity engine
- **simulation**: add checked output time axis
- **inspect**: cache partition merge plans
- **cli**: add inspection and validation commands
- **variables**: add canonical variable registry
- **labels**: add localized label catalog
- **store**: add guarded output store
- **simulation**: add status detection
- **simulation**: add FM and SMT discovery
- **model-input**: add model input adapters
- **validation**: add static configuration validation
- **config**: add validated project configuration
- **synthetic**: add plain FM generator variants
- **tools**: add synthetic SMT test case generator

### Fix

- **analyses**: rebuild stale PRF profile caches instead of failing
- **config**: reject accepted-but-unimplemented options (RV-31)
- **tests**: expect intentional V-MDU-002 warning in CLI tests
- **validation**: report model time and path hazards
- **tools**: Matlab script settings for D3D_plot; protect Matlab results
- **tools**: land boundaries as .ldb only; add corrupt-partition variant

### Refactor

- remove direct HYDROLIB-core dependency
- **map1d**: rename the M1D analysis to map1d
- **execution**: move processing runner into package
- **execution**: extract native project dispatch

### Perf

- **inspect**: parallelize subrun metadata scans
