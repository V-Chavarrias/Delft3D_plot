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
for every configured simulation, or only the one selected by label. It measures
partition identity and topology from sub-run 0, reads time quality from every
sub-run, and checks duplicate SMT restart boundaries. Plain FM output without
global face numbers uses one-to-one face-centre matching within 1 mm and reports
the coordinate-matching fallback as `V-OUT-023`. The time-axis cache stores
UTC hydrodynamic and morphological times, merged sub-run/local indices, and
corrupt-partition flags. Later-subrun topology is assumed to match sub-run 0.

Generated caches are written atomically beneath the configured, guarded
`output.root` (or the simulation's default `d3dplot` output folder). Model files
remain read-only. `inspect` does not process quantities or create figures.
It writes a timestamped text report to
`tables/inspection_<timestamp>.txt` beneath the reference simulation's output
root.
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

## Generate a SLURM array-job template

```powershell
d3dplot slurm write project.yaml --dry-run
d3dplot slurm write project.yaml
```

By default, the generated template is written to
`config/slurm_array.sh` beneath the reference simulation's marked output root.
All writes use the output-store guard. `--dry-run` prints the template without
creating the output root or any files. The command never submits a job.

The template is for one node per array job. It uses
`execution.slurm.tasks_per_node` as the CPU count for the worker pool and
requires a cluster-visible config path in `D3DPLOT_CONFIG`. Once the planned
`d3dplot run` command is integrated, choose the shard count when submitting
and start array indices at zero so each task receives the correct deterministic
shard:

```bash
sbatch --array=0-7 \
  --export=ALL,D3DPLOT_CONFIG=/shared/project.yaml \
  /shared/output/reference/d3dplot/config/slurm_array.sh
```

The generated script derives `--shard i/N` from
`SLURM_ARRAY_TASK_ID/SLURM_ARRAY_TASK_COUNT`; Python's randomized `hash()` is
not used. It anticipates `d3dplot run`, which is not yet integrated in this
version, and exits with an explicit error until that command is available.
Multi-node execution and automatic submission remain out of scope for the v1
stub.

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
default report format, and the report is written to
`tables/validation_<timestamp>.txt`. With `--format json`, it is written to
`tables/validation_<timestamp>.json`; JSON reports have the stable shape
`{"valid": true, "issues": []}`.

## Process quantities from Python

The project-level `d3dplot run` command is not integrated yet. For a local
single-simulation processing test, use the temporary helper:

```powershell
.\.venv\Scripts\python.exe tools\process_project.py data_local\maas_sk_initial_final_bed_level.yml
```

The helper processes configured `map2d` and `river_km_statistics` quantities
then invokes the regular `d3dplot plot` stage to create the configured figures.
It writes guarded caches beneath `output.root`, supports `vertical: all` or
explicit `vertical.layers`, and rebuilds all selected quantity caches
regardless of the configured reprocess policy. It opens guarded `.dia` and
debug `.log` files before simulation discovery and inspection, so preflight
failures are recorded under `output.root/<label>/d3dplot/logs`. Add `--serial`
to run sequentially for debugging. It does not modify model or measurement
inputs. The `.dia` records user-facing phase starts and choices; the `.log`
records task and per-analysis progress: map2d and river-km starts/completions,
time steps, quantity evaluation and raw reads, and map/statistics cache writes.
It also records river-km, polygon, and profile-measurement reads. Both files are
flushed as messages arrive, so the `.log` can be used to see which long-running
operation is currently active.

For the Maas local configuration, the repository-root
`run_maas_sk_processing.bat` launches this helper on Windows. Pass `--serial`
to the batch file to run sequentially:

```powershell
.\run_maas_sk_processing.bat
.\run_maas_sk_processing.bat --serial
```

## Print the configuration schema

```powershell
d3dplot schema > d3dplot.schema.json
```

The command prints the JSON Schema for the built-in project configuration.
