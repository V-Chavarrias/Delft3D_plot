# Delft3D_plot

Python tool to postprocess and plot Delft3D (D-Flow FM / SMT) results.

> **Status:** design phase. No code yet. The architecture proposal is in [docs/architecture.md](docs/architecture.md).

## Goals

- Script-based workflow (Python script or YAML + CLI).
- **Process first, plot later**: raw results are processed once into a cache, and figures are made (and remade) from that cache.
- **Validate all input up front** before any heavy processing starts.
- Parallel processing and plotting (local process pool; SLURM job arrays on Linux clusters).
- Reuses [HYDROLIB-core](https://github.com/Deltares/HYDROLIB-core) and [dfm_tools](https://github.com/Deltares/dfm_tools) instead of re-implementing readers.
- Runs on Linux and Windows.

The functional reference is the Matlab tool `D3D_plot` in [OpenEarthTools](https://github.com/openearth/matlab-tools/blob/main/applications/vtools/D3D/plot/D3D_plot.m). This is not a one-to-one translation.

## Documentation

- [Architecture proposal](docs/architecture.md)

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

To be decided (see [docs/architecture.md §9.2](docs/architecture.md)).
