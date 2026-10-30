# User guide

This guide covers the currently available command-line workflow for D-Flow FM
and SMT simulations. The built-in analyses are `map2d` and
`river_km_statistics`.

## Install

After a release is available, install the package with:

```powershell
python -m pip install delft3d-plot
```

For the unreleased source checkout, follow the development setup in
[Contributing](https://github.com/V-Chavarrias/Delft3D_plot/blob/main/CONTRIBUTING.md).
Use `d3dplot --help` to see the installed
commands.

## Inspect a simulation

Start with the read-only commands:

```powershell
d3dplot info C:\models\my-run
d3dplot variables C:\models\my-run
```

`info` reports the simulation type, status, sub-runs, partitions, output times,
and coordinate reference metadata. `variables` lists raw NetCDF fields and
registered quantities, aliases, and derived quantities. These commands do not
create caches or change model files.

## Create and edit a project configuration

Generate a starter YAML file for one or more simulations:

```powershell
d3dplot init C:\models\my-run -o project.yaml
```

The starter file contains a `map2d` analysis for `bed_level`. A minimal
configuration can look like this:

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

Choose an `output.root` on a local disk, outside model inputs and any
read-only/network data locations. Paths are interpreted from the process
working directory. See the [configuration reference](./configuration.md) for
time selection, measurements, plotting, and execution settings.

To compare simulations, add them to `simulations` and select a comparison such
as `difference_simulation`. Use `d3dplot variables` before entering variable
names; registered aliases such as `bl` and `qsp` are accepted as well as their
canonical names.

## Validate and inspect outputs

```powershell
d3dplot validate project.yaml
d3dplot validate project.yaml --format json
```

Validation checks the YAML, simulation inputs, requested variables, and
available map output. If those checks pass, it also builds or reuses the
guarded grid, merge-plan, and time-axis inspection caches. `d3dplot inspect
project.yaml` explicitly builds or refreshes those caches. These commands can
write diagnostics, timestamped reports, and caches beneath the configured
output root; model inputs remain read-only. Validation reports are written by
default to `tables/validation_<timestamp>.txt` (or `.json` when
`--format json` is used). Inspection reports use
`tables/inspection_<timestamp>.txt`.

The output store only writes inside a marked `d3dplot` output root. A new root
is marked automatically; a non-empty, unmarked directory is rejected. See
[Output store](./output-store.md) for the guard and layout rules.

## Plot and replay figures

`d3dplot plot` renders figures from persisted analysis quantity caches:

```powershell
d3dplot plot project.yaml
d3dplot plot project.yaml --variable bed_level --fresh-figures
```

Inspection caches created by `validate` or `inspect` are not processed
quantity caches. The current CLI does not yet expose the end-to-end
`process`/`run` commands that create those quantity caches from model output.
Therefore, plotting requires caches produced through the processing API or an
earlier processing workflow; validation alone does not make a project ready
to plot. The SLURM template also checks for the not-yet-available `d3dplot
run` command and exits explicitly rather than submitting work.

PNG figures contain provenance for replaying an individual map figure:

```powershell
d3dplot replot C:\postprocessing\reference\d3dplot\figures\01\bed_level\figure.png
d3dplot replot C:\postprocessing\reference\d3dplot\figures\01\bed_level\figure.png --set analyses[0].figure.dpi=300
```

`replot` only accepts a d3dplot PNG with valid provenance and regenerates that
figure, not the complete time series or movie.

## Use the packaged agent skills

The package includes the `d3dplot-create-input`, `d3dplot-replot`, and
`d3dplot-troubleshoot` workflow skills. VS Code discovers skills from a
workspace skill directory, not from an arbitrary installed Python package
directory. To use one, copy its complete folder from the installed package's
`d3dplot/skills/` directory into the workspace's `.github/skills/` directory.
The installed resource location can be printed with:

```powershell
python -c "from importlib.resources import files; print(files('d3dplot').joinpath('skills'))"
```

Keep the `SKILL.md` filename and the skill folder together when copying.
