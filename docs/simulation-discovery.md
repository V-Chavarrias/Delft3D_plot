# Simulation discovery

`d3dplot.simulation.discover_simulation(path)` identifies a plain D-Flow FM or
SMT simulation and validates its model inputs and map-output files. Discovery is
read-only and returns a `(simulation, report)` pair. If errors prevent
discovery, `simulation` is `None`; warnings are retained in the report.

```python
from pathlib import Path

from d3dplot.simulation import discover_simulation


def main() -> None:
    simulation, report = discover_simulation(Path("./my-simulation"))
    print(report.to_text())

    if simulation is not None:
        print(simulation.kind)
        for subrun in simulation.subruns:
            print(subrun.index, subrun.mdu.mdu_path, subrun.partition_count)


if __name__ == "__main__":
    main()
```

## Simulation types

An SMT simulation has `smt.yml` and numbered sub-run folders under
`output/<n>/`. Sub-runs are ordered numerically and must be contiguous from
zero. The `output/work` folder is excluded and reported as a warning because
the simulation may still be running.

SMT simulations with at least eight sub-runs validate sub-runs concurrently
using a bounded thread pool (up to 64 workers). Small simulations stay
serial. This read-only I/O stage can also be called from notebooks; process
spawning and a Windows main guard are not required.

A plain FM simulation is represented by one sub-run. When `dimr_config.xml`
contains a `dflowfm` component, its MDU is selected from that component's
`workingDir` and `inputFile`; other DIMR components are ignored. Otherwise,
discovery requires exactly one MDU that is not a four-digit partition MDU.

## Map output

Map files are found from the MDU's `OutputDir` and `MapFile` settings. A
partitioned output uses filenames ending in `_<partition>_map.nc`, with
four-digit partition numbers contiguous from `0000`. Every discovered map file
is opened in read-only mode to verify that its NetCDF header is readable.
Classic NetCDF3 headers use SciPy's memory-mapped reader; other NetCDF formats
use netCDF4, whose native reader access is serialized across discovery threads.
SMT sub-runs must have the same number of map files.

## Validation codes

| Code | Meaning |
|------|---------|
| `V-SIM-001` | Simulation folder is missing or unreadable, or no FM model can be selected. |
| `V-SIM-002` | More than one candidate FM model was found. |
| `V-SIM-010` | SMT sub-run numbering is not unique and contiguous from zero. |
| `V-SIM-011` | `output/work` exists; the SMT simulation may still be running. |
| `V-OUT-001` | Map output folder or required map files are missing, or partition numbering has gaps. |
| `V-OUT-002` | A map file cannot be opened as NetCDF. |
| `V-OUT-003` | Map-file counts differ between SMT sub-runs. |
| `V-MDU-001` | The selected MDU or a referenced morphology/sediment file cannot be parsed. |

The same stable validation codes are listed in the
[architecture plan](./architecture.md#12-validation-catalogue).
