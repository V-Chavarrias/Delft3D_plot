# Command-line interface

Install the package, then use either the `d3dplot` command or
`python -m d3dplot`. The CLI provides read-only simulation summaries, guarded
output inspection, configuration schema output, validation, and
starter-configuration generation, plotting, and profile-measurement conversion.

## Inspect a simulation

```powershell
d3dplot info C:\models\run
d3dplot info C:\models\run --json
d3dplot variables C:\models\run
```

`info` reports simulation kind and status, sub-run and partition counts, the
last hydrodynamic and morphological times, raw variables, and coordinate
reference metadata. `--json` emits the same summary as structured JSON.
`variables` lists detected raw NetCDF names, matching registered raw quantities
and aliases, unregistered fields, and all registered derived quantities.

Both commands use read-only discovery. To report raw fields and CRS, they read
only the first map-file header from each sub-run; status detection may read the
metadata it needs to determine completion times and state. They do not build a
merge plan, create a cache, or modify model files. A simulation without map
output can still be inspected with `info` and is reported as `not_started`.

## Build the output-inspection cache

```powershell
d3dplot inspect project.yaml
d3dplot inspect project.yaml --simulation reference
```

`inspect` builds or reuses the grid, merge-plan, and checked time-axis caches
for every configured simulation, or only the one selected by label. It checks
partition identity and topology, time quality, and duplicate SMT restart
boundaries; it also selects a time-consistent reference sub-run and reports the
cache location and whether it was built or reused. Plain FM output without
global face numbers uses one-to-one face-centre matching within 1 mm and reports
the coordinate-matching fallback as `V-OUT-023`. The time-axis cache stores
UTC hydrodynamic and morphological times, merged sub-run/local indices, and
corrupt-partition flags.

Generated caches are written atomically beneath the configured, guarded
`output.root` (or the simulation's default `d3dplot` output folder). Model files
remain read-only. `inspect` does not process quantities or create figures.
Boundary `morft` mismatches are errors (`V-OUT-032`); boundary bed-level
mismatches are warnings (`V-OUT-033`).

## Create a starter configuration

```powershell
d3dplot init C:\models\run -o project.yaml
d3dplot init C:\models\run-a C:\models\run-b -o project.yaml
d3dplot init C:\models\run -o project.yaml --analyses map2d,river_km_statistics
```

`init` creates a commented YAML file with one `map2d` analysis by default.
Simulation paths are written as absolute paths, and labels are generated from
the folder names. The optional `river_km_statistics` section contains
placeholder polygon and river-km paths; replace them before using that
analysis. Existing files are never overwritten.

## Convert river-km profile measurements

```powershell
d3dplot measurements convert measurements.mat -o converted.nc --time-zone +01:00
d3dplot measurements convert measurements.mat -o converted.nc --time-zone +01:00 --variable bed_level
```

The command converts Matlab v5/v7 profile measurements to canonical NetCDF.
MATLAB datenums do not contain a timezone, so `--time-zone` is required and
must be a fixed UTC offset. Without `--variable`, one output file is written
per variable, with the canonical variable name added to the output filename.
The source `.mat` is left unchanged. Output files are never overwritten; the
destination must be in a new or empty directory, or beneath an existing marked
d3dplot output root. Matlab v7.3/HDF5 inputs are not supported.

## Validate a project

```powershell
d3dplot validate project.yaml
d3dplot validate project.yaml --format json
```

Validation checks the configuration schema and static project rules, discovers
each configured simulation, and checks requested quantity names against the
registry and detected map headers. Registered raw quantities must be present
in every sub-run. An unregistered name is accepted as a raw NetCDF field only
when it is found in the configured simulations; missing fields produce
`V-VAR-002`, while unknown names produce `V-VAR-001` with suggestions when
available. Section quantities such as `velocity_parallel`, `velocity_perpendicular`,
and `discharge` produce `V-VAR-006` because line sampling is not part of v1.

When these checks have no errors, `validate` also builds or reuses the same
grid, merge-plan, and checked time-axis caches as `inspect`. Derived quantity
dependencies are validated as part of this step, but it does not process
quantities or create figures. Errors produce a non-zero exit code. Text is the
default report format; JSON reports have the stable shape
`{"valid": true, "issues": []}`.

## Print the configuration schema

```powershell
d3dplot schema > d3dplot.schema.json
```

The command prints the JSON Schema for the built-in project configuration.
