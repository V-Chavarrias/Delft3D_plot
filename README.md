# d3dplot

`d3dplot` is a command-line tool that turns D-Flow FM and SMT simulation results
into figures and tables. You describe what you want in a YAML file, and d3dplot
reads the model output, processes it once, and writes the figures next to your
simulation.

It is the Python successor of the Matlab tool `D3D_plot`.

## Install

Requires Python 3.11, 3.12 or 3.13.

```powershell
python -m pip install d3dplot
d3dplot --help
```

From a source checkout:

```powershell
python -m pip install .
```

## Quick start

```powershell
d3dplot info C:\models\my-run                      # what is in the simulation
d3dplot init C:\models\my-run -o project.yaml      # write a starter configuration
d3dplot validate project.yaml                      # check the configuration
d3dplot run project.yaml                           # process the results and plot
```

`run` processes the results and then makes the figures. Use `process` and `plot`
separately when you want to control each step, and `replot` to redraw a single
figure after you change its settings.

The [user guide](docs/user-guide.md) walks through a complete project.

## Documentation

| Page | Read it to |
| --- | --- |
| [User guide](docs/user-guide.md) | Go from a simulation to figures, step by step. |
| [Command reference](docs/cli.md) | Look up every command and option. |
| [Configuration reference](docs/configuration.md) | Look up every setting in the YAML file, with examples. |
| [Output store](docs/output-store.md) | Find where results, figures, logs and reports are written. |
| [Variables](docs/variables.md) | Find the variable names and aliases you can plot. |
| [Labels](docs/labels.md) | Use labels to refer to simulations and comparisons. |
| [Model input](docs/model-input.md) | Know which model input files d3dplot reads. |
| [Simulation discovery](docs/simulation-discovery.md) | Understand how d3dplot recognises a simulation folder. |
| [Gallery](docs/gallery.md) | See example figures. |
| [Coming from Matlab](docs/from-matlab.md) | Map `D3D_plot` options and workflows to d3dplot. |

The example configuration is in [examples/map1d.yml](examples/map1d.yml).

## Development

Contributors should read [CONTRIBUTING.md](CONTRIBUTING.md). The design decisions
are recorded in [docs/architecture.md](docs/architecture.md).

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).
