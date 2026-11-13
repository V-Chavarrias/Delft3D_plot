# Command reference

This page lists every `d3dplot` command and its options. The installed version
is always authoritative: run `d3dplot --help` or `d3dplot <command> --help` to
see the options your version accepts.

`d3dplot` can also be run as `python -m d3dplot`.

## Summary

| Command | What it does | Writes files |
|---|---|---|
| [`info`](#info) | Summarise a simulation: type, status, sub-runs, times, CRS | No |
| [`variables`](#variables) | List the raw NetCDF fields and the available quantities | No |
| [`init`](#init) | Create a starter project YAML | One YAML file |
| [`validate`](#validate) | Check a project; build inspection caches if there are no errors | Reports and caches |
| [`inspect`](#inspect) | Build or refresh the grid, merge-plan and time-axis caches | Caches and a report |
| [`process`](#process) | Compute the configured quantities into caches | Caches |
| [`run`](#run) | `process`, then `plot` (unless sharded) | Caches and figures |
| [`plot`](#plot) | Render figures from the caches | Figures |
| [`replot`](#replot) | Regenerate one figure from its PNG | One figure |
| [`status`](#status) | Show the processing and figure state of each simulation | No |
| [`clean`](#clean) | Remove figures and/or caches | Deletes files |
| [`schema`](#schema) | Print the JSON Schema of the project configuration | No |
| [`measurements convert`](#measurements-convert) | Convert Matlab profile measurements to NetCDF | NetCDF files |
| [`slurm write`](#slurm-write) | Write a SLURM array-job script (never submits) | One script |

## Typical workflow

```powershell
d3dplot info C:\models\run                     # look at the simulation
d3dplot variables C:\models\run                # find the variable names
d3dplot init C:\models\run -o project.yaml     # create a starter configuration
d3dplot validate project.yaml                  # check the configuration and inputs
d3dplot run project.yaml                       # process the quantities and plot
d3dplot status project.yaml                    # check what was produced
```

After you change only the figure settings, `d3dplot plot project.yaml` redraws
the figures from the existing caches. Use `d3dplot process` when you want the
caches without figures, for example before a sharded run.

## Inspecting simulations

### `info`

```powershell
d3dplot info SIMULATION_PATH [--json]
```

Reports the simulation type and status, the number of sub-runs and partitions,
the last hydrodynamic and morphological times, the raw variables and the
coordinate reference system. A simulation without map output is reported as
`not_started`.

| Option | Description |
|---|---|
| `--json` | Print the same summary as JSON. |

`info` only reads the file headers. It does not create caches or change model files.

### `variables`

```powershell
d3dplot variables SIMULATION_PATH
```

Lists:

- the raw NetCDF fields found in the simulation,
- the registered raw quantities and their aliases, for example `bl` for `bed_level`,
- the raw fields that are not registered,
- all registered derived quantities.

Use the names from this list in the `variables` entries of your YAML file.

### `schema`

```powershell
d3dplot schema > d3dplot.schema.json
```

Prints the JSON Schema of the project configuration. Editors that support JSON
Schema can use it for completion and checking.

## Creating and checking a project

### `init`

```powershell
d3dplot init SIMULATION_PATH [SIMULATION_PATH ...] -o project.yaml [--analyses NAMES]
```

Creates a commented starter YAML file. Simulation paths are written as absolute
paths, and labels are taken from the folder names. The file is never
overwritten.

| Option | Description |
|---|---|
| `-o`, `--output` | **Required.** The new YAML file to create. |
| `--analyses` | Comma-separated built-in analyses: `map1d`, `map2d`, `profile`, `river_km_statistics`. Default: `map2d`. |

Example:

```powershell
d3dplot init C:\models\run-a C:\models\run-b -o project.yaml --analyses map2d,river_km_statistics
```

The `river_km_statistics` section contains placeholder polygon and river-km
paths. Replace them before you run that analysis.

### `validate`

```powershell
d3dplot validate project.yaml [--format text|json]
```

Checks the configuration against the schema and the project rules. It also
finds each simulation, checks the requested variables against the registry and
the map files, and checks the derived-quantity dependencies.

If there are no errors, `validate` also builds or reuses the grid, merge-plan
and time-axis inspection caches, the same as [`inspect`](#inspect). It does not
process quantities or create figures.

| Option | Description |
|---|---|
| `--format text` | Default. Writes `tables/validation_<timestamp>.txt`. |
| `--format json` | Writes `tables/validation_<timestamp>.json` with the shape `{"valid": true, "issues": []}`. |

The command exits with a non-zero code if the project has errors. Each issue
has a stable code such as `V-VAR-001` (unknown variable) or `V-VAR-002`
(variable missing from the map files).

### `inspect`

```powershell
d3dplot inspect project.yaml [--simulation LABEL]
```

Builds or reuses the grid, merge-plan and checked time-axis caches for every
simulation, or for the one named by `--simulation`. It reads the partition
layout from the first sub-run, reads the time information from every sub-run,
and checks duplicate SMT restart boundaries. The report is written to
`tables/inspection_<timestamp>.txt` in the reference simulation's output root.
Inspection does not process quantities or create figures.

| Option | Description |
|---|---|
| `--simulation LABEL` | Only inspect the simulation with this label. |

## Processing and plotting

### `process`

```powershell
d3dplot process project.yaml [--serial] [--reprocess POLICY] [--executor NAME] [--workers N] [--shard i/N]
```

Computes the configured quantities for all simulations and writes them as
guarded caches under the output root. Existing caches are reused unless the
reprocessing policy says otherwise. The command does not create figures.

| Option | Description |
|---|---|
| `--serial` | Run sequentially. Useful for debugging. |
| `--reprocess if_changed\|never\|always` | Override the cache reprocessing policy of the configuration. |
| `--executor serial\|processes\|slurm` | Override `execution.executor`. |
| `--workers N` | Override the number of worker processes. |
| `--shard i/N` | Process only shard `i` of `N` (zero-based). Shards are deterministic and do not overlap. |

Progress is written to the run's `.dia` file (the user-facing messages) and
`.log` file (detailed task progress). Both are in `output.root/d3dplot/logs`.

### `run`

```powershell
d3dplot run project.yaml [--serial] [--reprocess POLICY] [--executor NAME] [--workers N] [--shard i/N]
```

Runs [`process`](#process) and then [`plot`](#plot). It takes the same options.
If you use `--shard`, `run` processes that shard and skips the figures. After all
shards have finished, run `d3dplot plot project.yaml` once.

### `plot`

```powershell
d3dplot plot project.yaml [--simulation LABEL] [--variable NAME] [--only ANALYSIS] [--set KEY=VALUE] [--fresh-figures]
d3dplot plot C:\postprocessing\reference\d3dplot
```

Renders the figures from the caches made by `process` or `run`. The argument can
be the project YAML file, or the output folder that contains `config/latest.yaml`
(the resolved configuration written by the last run).

Matching PNG figures are reused. Use `--fresh-figures` to redraw all of them.

| Option | Description |
|---|---|
| `--simulation LABEL` | Only plot this simulation. |
| `--variable NAME` | Only plot this variable. |
| `--only ANALYSIS` | Only plot this analysis, for example `map2d`. |
| `--set KEY=VALUE` | Override a configuration value for this run. Repeat the option for more overrides. |
| `--fresh-figures` | Redraw the figures instead of reusing matching PNG files. |

### `replot`

```powershell
d3dplot replot FIGURE.png [--set KEY=VALUE]
```

Regenerates one figure from the provenance stored in the PNG. Only PNG files
created by d3dplot with valid provenance are accepted. This redraws that
figure only, not the whole time series or a movie.

| Option | Description |
|---|---|
| `--set KEY=VALUE` | Override a value before redrawing, for example `analyses[0].figure.dpi=300`. Repeat the option for more overrides. |

### `status`

```powershell
d3dplot status project.yaml [--json]
```

Shows the state of the outputs for each configured simulation.

| Option | Description |
|---|---|
| `--json` | Print the status as JSON. |

### `clean`

```powershell
d3dplot clean project.yaml [--figures] [--cache]
```

Removes generated output. The model files and measurement files are never
changed, and the output root itself is kept.

| Option | Description |
|---|---|
| `--figures` | Remove the generated figures. |
| `--cache` | Remove the processed and inspection caches. |

Without either option, `clean` removes both figures and caches.

## Working with other tools

### `measurements convert`

```powershell
d3dplot measurements convert measurements.mat -o converted.nc --time-zone +01:00 [--variable NAME]
```

Converts Matlab v5 or v7 river-km profile measurements to NetCDF. Matlab date
numbers have no time zone, so `--time-zone` is required and must be a fixed UTC
offset such as `+01:00`. The source `.mat` file is not changed.

| Option | Description |
|---|---|
| `-o`, `--output` | **Required.** Canonical NetCDF output path, in a new or empty folder (or a marked d3dplot output root). |
| `--time-zone +HH:MM` | **Required.** The fixed UTC offset of the measurement times. |
| `--variable NAME` | Convert only this variable. Without it, one file is written per variable. |

Existing files are never overwritten. The destination must be a new or empty
folder, or a folder inside an existing d3dplot output root. Matlab v7.3 (HDF5)
files are not supported.

### `slurm write`

```powershell
d3dplot slurm write project.yaml [--dry-run]
```

Writes a SLURM array-job script to `config/slurm_array.sh` beneath the output
root. The command never submits a job. Submit the script yourself, with one
array task per shard:

```bash
sbatch --array=0-7 \
  --export=ALL,D3DPLOT_CONFIG=/shared/project.yaml \
  /shared/output/reference/d3dplot/config/slurm_array.sh
```

Array indices start at zero. The script derives the shard from
`SLURM_ARRAY_TASK_ID` and `SLURM_ARRAY_TASK_COUNT`, and runs `d3dplot run` for
that shard. `D3DPLOT_CONFIG` must point to the project file on a path that the
cluster nodes can read. The script uses `execution.slurm.tasks_per_node` as the
number of worker processes. Each array task runs on one node. Multi-node jobs
are not supported.

| Option | Description |
|---|---|
| `--dry-run` | Print the script without writing any files. |

## Output locations and safety

Commands that write files put them in a `d3dplot` folder for each simulation.
Its location is the first of these that is set:

1. `output_dir` of the simulation, followed by `d3dplot`,
2. `output.root` of the project, followed by `d3dplot`,
3. the simulation folder, followed by `d3dplot`.

Inside that folder, `cache`, `figures`, `tables`, `config` and `logs` hold the
results. Model files and measurement files are never changed. See
[Output store](./output-store.md) for the full layout.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | An error occurred, or validation found errors. |
| `2` | A usage error, such as an unknown option. |

## Plugin analyses

A third-party package can register a configuration-only analysis through the
`d3dplot.analyses` entry-point group. Such an analysis is included in `validate`
and `schema`. `process`, `run` and `plot` reject it, because the plugin's
runtime handlers are not part of this version.
