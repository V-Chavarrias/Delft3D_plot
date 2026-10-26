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

## Inspection and time axis

`inspect_simulation(simulation, layout)` builds or reuses guarded grid,
merge-plan, and `times.nc` caches. The time cache contains the merged UTC
hydrodynamic axis, optional morphological times, sub-run and local output
indices, and corrupt-partition flags. Inspection also checks restart-boundary
`morft` and bed-level consistency. Model map files remain read-only.

Use `TimeAxis` to load the checked cache and apply the same `Times` selections
used by analysis configuration:

```python
from pathlib import Path

from d3dplot.config import Times
from d3dplot.simulation import TimeAxis

axis = TimeAxis.from_cache(Path("./postprocessing/reference/d3dplot/cache/times.nc"))
selection = axis.select(Times(at="last"))
if not selection.is_valid:
    print([issue.message for issue in selection.issues])
```

Times in the API are timezone-aware UTC. Naive requested times are interpreted
using the cache's source UTC offset. An unset tolerance chooses the nearest
output without a distance limit; an explicit tolerance is a hard maximum.

## Status

`detect_simulation_status(simulation)` reads only the final 4 KiB of each
partition `.dia` file (or the main `.dia` for an unpartitioned run), plus the
last `time` and `morft` values from a representative map file. A run is
finished only when every partition reports `Computation finished`; controlled
exit messages are reported as interrupted. An incomplete run is reported as
stopped only when its last output time precedes `TStop` and all output files
have been unchanged for `status.stale_after` (one hour by default). An SMT
`output/work` directory takes precedence and means the overall simulation is
running. For numeric `TStop`, map and MDU times are compared as elapsed
seconds; their origins can differ. `inspect_simulation_status(path)` returns a
status with the discovery report, so a valid model input with missing map
output is `not_started` while retaining the V-OUT-001 validation error.

Status JSON and CSV serializers return text or accept a caller-supplied writer,
so callers can route file writes through the guarded store. Corrupt-output
counts remain unknown until the output-quality inspection stage has run.

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
| `V-OUT-030` | A partition has corrupt or inconsistent `time`/`morft` values. |
| `V-OUT-031` | Implausible non-finite or extremely large values were read from a partition. |
| `V-OUT-032` | Hydrodynamic restart-boundary duplicates have different `morft`. |
| `V-OUT-033` | Bed levels differ at a restart-boundary duplicate. |
| `V-TIME-002` | Map, MDU, or configured time-zone offsets conflict. |
| `V-TIME-010` | A requested time cannot be resolved on the selected axis. |
| `V-TIME-011` | A selected output has corrupt partitions; the configured policy applies. |
| `V-TIME-012` | A morphological axis was requested without `morft` values. |
| `V-MDU-001` | The selected MDU or a referenced morphology/sediment file cannot be parsed. |

The same stable validation codes are listed in the
[architecture plan](./architecture.md#12-validation-catalogue).
