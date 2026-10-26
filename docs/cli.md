# Command-line interface

Install the package, then use either the `d3dplot` command or
`python -m d3dplot`. The CLI currently provides read-only simulation inspection,
configuration schema output, validation, and starter-configuration generation.

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

Derived quantity dependencies, grid checks, and the metadata cache are handled
by the model-output engine work packages; this CLI validation step does not
build the Phase 2 inspect cache. Errors produce a non-zero exit code. Text is
the default report format; JSON reports have the stable shape
`{"valid": true, "issues": []}`.

## Print the configuration schema

```powershell
d3dplot schema > d3dplot.schema.json
```

The command prints the JSON Schema for the built-in project configuration.
