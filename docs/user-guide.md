# User guide

This guide walks through a typical d3dplot project: look at your simulation,
write a configuration file, check it, produce the figures, and rerun only the
parts you change.

- Every command and option: [command reference](./cli.md)
- Every setting in the YAML file: [configuration reference](./configuration.md)
- Where the results are stored: [output store](./output-store.md)

## 1. Install

```powershell
python -m pip install d3dplot
d3dplot --help
```

To work from a source checkout, follow
[CONTRIBUTING.md](https://github.com/V-Chavarrias/Delft3D_plot/blob/main/CONTRIBUTING.md).

## 2. Look at the simulation

These commands only read the simulation. They do not change any files.

```powershell
d3dplot info C:\models\my-run
d3dplot variables C:\models\my-run
```

- `info` shows the simulation type and status, the sub-runs and partitions, the
  output times and the coordinate system.
- `variables` lists the variable names you can use in the configuration.
  Aliases such as `bl` for `bed_level` are accepted.

## 3. Create a configuration file

Generate a starter file for one or more simulations:

```powershell
d3dplot init C:\models\my-run -o project.yaml
```

The starter file contains one `map2d` analysis. A minimal configuration looks
like this:

```yaml
simulations:
  - path: C:/models/my-run
    label: reference
reference_simulation: reference
output:
  root: ./postprocessing
analyses:
  - analysis: map2d
    variables: [bed_level]
    times:
      at: all
      axis: hydrodynamic
```

Notes:

- `label` is a short name for the simulation. It tells the simulations apart in
  the results.
- `output` is optional. Without it, the results go into a `d3dplot` folder
  inside each simulation folder. For projects on the P-drive, that folder is on
  the P-drive too. Writing there is allowed, and the model files are never
  changed. To keep the results elsewhere, set `output.root` to a local folder.
  With several simulations, give each one its own `output_dir` instead.
- Relative paths are resolved from the folder where you run `d3dplot`.
- To compare two simulations, add a second entry under `simulations` and select
  a comparison in the analysis. See the [configuration reference](./configuration.md).

The [annotated example](https://github.com/V-Chavarrias/Delft3D_plot/blob/main/examples/map1d.yml)
shows a complete configuration with a comment on each option.

## 4. Check the configuration

```powershell
d3dplot validate project.yaml
```

`validate` checks the file, the simulations, the variable names and the map
output before any processing starts. Each problem has a code such as
`V-VAR-001`. The report is written to the `tables` folder of the output.
If there are no errors, `validate` also builds the grid and time caches, so the
next steps are faster.

Fix all errors before you continue. Warnings do not stop the workflow, but read
them: they often explain a figure that looks wrong.

## 5. Process and plot

For most projects, one command does everything:

```powershell
d3dplot run project.yaml
```

`run` computes the configured quantities into caches, and then draws the
figures.

To separate the steps:

```powershell
d3dplot process project.yaml
d3dplot plot project.yaml
```

Use `process` when you only need the data. Use `plot` to draw figures from
existing caches.

### Large runs

- `--workers N` sets the number of worker processes. `--serial` runs everything
  in one process, which is useful for debugging.
- `--shard i/N` processes one part of the work. Run shards `0/N` to `(N-1)/N`,
  for example on separate machines or as SLURM array tasks. When all shards are
  finished, run `d3dplot plot project.yaml` once.
- `d3dplot slurm write project.yaml` writes a SLURM array-job script. It never
  submits the job. See the [command reference](./cli.md#slurm-write).

## 6. Check the results

```powershell
d3dplot status project.yaml
```

`status` shows the state of the outputs for each simulation.

The `d3dplot` folder of each simulation contains:

| Folder | Contents |
|---|---|
| `tables` | Validation and inspection reports. |
| `logs` | `.dia` file with the main steps and choices, and `.log` file with detailed progress. |
| `cache` | Processed quantities, grids and time tables. Reused by the next run. |
| `figures` | The figures as PNG files. |
| `config` | The resolved configuration of the last run and the SLURM script. |

## 7. Change and rerun

- To change the figures only, edit the YAML and run `d3dplot plot project.yaml`.
  Matching figures are reused. Add `--fresh-figures` to redraw all of them.
- To change one figure, run `d3dplot replot FIGURE.png --set KEY=VALUE`. Each
  PNG records how it was made, so `replot` redraws that figure only.
- To limit a `plot` run, use `--simulation`, `--variable` or `--only`.
- To remove generated files, run `d3dplot clean project.yaml --figures` or
  `--cache`. Without an option, both are removed. Model files are never changed.

## Command overview

| Task | Command |
|---|---|
| Look at a simulation | `info`, `variables` |
| Create a configuration | `init` |
| Check a configuration | `validate` |
| Build the grid and time caches | `inspect` |
| Compute the quantities | `process` |
| Compute and plot | `run` |
| Draw the figures | `plot` |
| Redraw one figure | `replot` |
| Check progress | `status` |
| Remove generated files | `clean` |
| Write the JSON Schema | `schema` |
| Convert Matlab profile measurements | `measurements convert` |
| Write a SLURM script | `slurm write` |

The [command reference](./cli.md) describes every command and option.

## Agent skills

The package includes workflow skills for VS Code and GitHub Copilot:
`d3dplot-create-input`, `d3dplot-replot` and `d3dplot-troubleshoot`. VS Code
only finds skills in a workspace's `.github/skills/` folder, so copy the complete
skill folder there. Print the location of the installed skills with:

```powershell
python -c "from importlib.resources import files; print(files('d3dplot').joinpath('skills'))"
```

Copy the whole folder, including its `SKILL.md` file.
