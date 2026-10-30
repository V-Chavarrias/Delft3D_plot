# Delft3D_plot

Python tool to postprocess and plot Delft3D (D-Flow FM / SMT) results. Version 1 covers 2D maps (`map2d`) and river-km statistics (`river_km_statistics`).

> **Status:** foundations in progress. The package skeleton and [synthetic SMT test case generator](tools/synthetic_smt/README.md) are in place; analysis functionality is being implemented according to [the architecture plan](docs/architecture.md).

## Goals

- Script-based workflow (Python script or YAML + CLI).
- **Process first, plot later**: raw results are processed once into a cache, and figures are made (and remade) from that cache.
- **Validate all input up front** before any heavy processing starts.
- Parallel processing and plotting (local process pool; SLURM job arrays on Linux clusters).
- Reuses [HYDROLIB-core](https://github.com/Deltares/HYDROLIB-core) and [dfm_tools](https://github.com/Deltares/dfm_tools) instead of re-implementing readers.
- Runs on Linux and Windows.

The functional reference is the Matlab tool `D3D_plot` in [OpenEarthTools](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/D3D_plot.m). This is not a one-to-one translation.

## How model output is assembled

Partitioned D-Flow FM output contains one map file per partition. Each file
contains local face and edge numbers, including ghost cells. Before values are
processed, d3dplot creates `cache/grid.nc` and `cache/merge_plan.nc`.

`grid.nc` contains the combined mesh geometry. `merge_plan.nc` contains the
lookup needed to place values from the partition files into that combined mesh:

```text
local value in a partition
        |
        |  owned_local
        v
global position in the combined grid
        |
        |  global_pos
        v
one value in the output array
```

The plan also stores the equivalent lookup for edges, including edge
orientation. It stores indexes and mappings only; it does not store bed levels,
other model variables, or times. Those remain in the model map files and in
`cache/times.nc`.

### Sub-runs and plans

An SMT simulation has several sub-runs, usually representing successive model
periods or restarts. The simulation has one mapping recipe between local
partition indices and its combined global grid. Every sub-run uses that same
simulation-wide recipe:

```text
SMT with one common grid:

sub-runs:        [0, 1, 2, 3]
merge plan:      one plan in merge_plan.nc
```

The grid and merge plan are built from sub-run 0. Time and `morft` metadata are
still read from every sub-run. Different simulations have separate output
roots and therefore separate `grid.nc` and `merge_plan.nc` files.

A non-SMT (plain FM) simulation is represented as one sub-run:

```text
sub-runs:        [0]
merge plan:      one plan in merge_plan.nc
```

If that plain-FM run is partitioned, the plan still contains one entry for
each map partition. If it has one unpartitioned map file, the mapping is
effectively an identity mapping: local face 0 goes to global face 0, local
face 1 goes to global face 1, and so on.

See the [output-store documentation](docs/output-store.md) and the
[architecture plan](docs/architecture.md#76-reading-model-output-merge-plan-d21-d23)
for the cache layout and implementation details.

## Documentation

- [User guide](docs/user-guide.md)
- [From Matlab `D3D_plot`](docs/from-matlab.md)
- [Gallery](docs/gallery.md)
- [Reference](docs/reference.md)
- [Project documentation](docs/index.md)
- [Architecture proposal](docs/architecture.md)
- [Synthetic SMT test case generator](tools/synthetic_smt/README.md)
- [Contributing guide](CONTRIBUTING.md)

## Development workspace

[Delft3D_plot.code-workspace](Delft3D_plot.code-workspace) opens this repository together with sibling checkouts of the reference repositories:

```
checkouts/
├─ Delft3D_plot/     (this repository)
├─ oet_matlab/       https://github.com/openearth/matlab-tools
├─ HYDROLIB-core/    https://github.com/Deltares/HYDROLIB-core
└─ dfm_tools/        https://github.com/Deltares/dfm_tools
```

## Licence

[GPL-3.0-or-later](LICENSE).
