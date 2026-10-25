# Synthetic SMT case generator

Generates a small, artificial D-Flow FM **SMT** simulation (input and output) with the structure and file formats of the Maas run `r209`, plus project inputs and measurements in the formats of the Maas project. All fields are analytic, so expected results are known. See [docs/architecture.md §13.3](../../docs/architecture.md).

No project data is used or copied: the real run was only inspected to reproduce its *structure*.

## Usage

```bash
uv venv .venv
uv pip install --python .venv/Scripts/python.exe -r tools/synthetic_smt/requirements.txt   # Linux: .venv/bin/python
.venv/Scripts/python.exe tools/synthetic_smt/generate.py            # -> data_local/synthetic_smt_01 (git-ignored)
.venv/Scripts/python.exe tools/synthetic_smt/generate.py --force    # regenerate (only replaces folders it created)
```

Options: `--out <folder>`, `--no-variants`, `--no-verify`. The generator refuses to write to the P-drive, and it refuses to replace a folder that it did not create (marker file `.synthetic_smt`).

After generating, the generator runs a self-check (`verify.py`) with the libraries the tool will use:
* HYDROLIB-core reads the MDU, `dimr_config.xml` and the `.ldb`;
* dfm_tools merges the partitions and reproduces the analytic bed level;
* rasterio reads the VRT and GeoTIFFs;
* geopandas reads the shapefiles;
* scipy reads the `.mat` files.

It also checks that every map variable has all its time steps.

## What is generated

| Path | Content |
|------|---------|
| `simulations/r001`, `r002` | SMT runs: `smt.yml`, `sed/`, `output/0..3/` with `dflowfm.mdu`, `dflowfm_000p.mdu`, `mor.mor`, `dimr_config.xml`, `synthetic_net.nc`, `results/dflowfm_000p_map.nc`, `.dia`. r002 erodes faster. r001 has NaN/Inf bed levels in a few cells (sub-run 2, last time) |
| `simulations/variants/` | `running` (has `output/work`; last sub-run truncated), `crashed` (stopped before `TStop`), `non_partitioned`, `gap` (sub-run 2 missing), `corrupt_partition` (sub-run 0, partition 1: garbage `time`/`morft` and ±1e308 values at map times 1–2, as `r209` sub-run 0 partition 0021) |
| `inputs/rkm/` | river-km files in the fixed format `x, y, <km>_<TAG>, km`: Maas (`MA`), generic (`SY`), Rijntakken (`WA`); 100 m, 1 km and 3 km spacing |
| `inputs/regions/` | `L1R1` (summer bed), `L1L3`/`R1R3` (halves), `WIDE` (whole width) |
| `inputs/ldb/` | land boundary as `.ldb` (Tekal), the only accepted input format (a `.mat` is only ever a cache) |
| `inputs/measurements/smb/` | `<region>_measured.mat`: `data.<var>.val_mean.{rkm, s, unit, tim_dnum, val}` + `source`, with unsorted times |
| `inputs/measurements/2d/` | VRT mosaic of 2 GeoTIFF tiles; a half-pixel-shifted GeoTIFF in cm (factor 0.01); a GeoTIFF outside the time tolerance; polygon shapefiles ("vakken", fields `MEAN`, `COUNT`, `A`, `locatie`, `code_uniek`, with failing coverage/location filters and shuffled order); catalogues `catalogue_raster.csv`, `catalogue_vakken.csv` |
| `expected/` | merged SMT time axis; cells per 250 m segment; SMB statistics per simulation and region (Matlab definitions) |
| `scripts/main_plot_synthetic.m` | Matlab `D3D_plot` input. Run it once to create the Matlab reference (`mat/`, `fig/` in the simulation folders) |

Features reproduced from `r209`:
* **Partitions**:
  * partitions with ghost cells, and `mesh2d_flowelem_domain` / `mesh2d_flowelem_globalnr`;
  * one partition is a strip along the whole river;
  * the mesh has triangles as well as quadrilaterals.
* **Time axis**:
  * map time units `seconds since 2021-02-08 00:00:00 +01:00`, while the MDU has `RefDate = 20121231`;
  * the first map time of sub-run k>0 duplicates the last of sub-run k−1;
  * `morft` has time units, so xarray decodes it to datetimes.
* **Metadata and diagnostics**:
  * the map files have EPSG 0 ("Unknown projected");
  * "Computation finished" appears only in the partition `.dia` files, not in the main `dflowfm.dia`.

Not yet generated: a MAT v7.3 (HDF5) variant of the SMB measurement files.

## Files

| Module | Role |
|--------|------|
| `params.py` | all parameters (geometry, fields, times, measurements) |
| `mesh.py` | centreline, curvilinear mesh, edges, partitions and ghost cells |
| `fields.py` | analytic fields and SMT time structure |
| `fm_files.py` | map/net NetCDF (FM metadata), MDU, `.mor`, `.sed`, `.dia`, `smt.yml`, DIMR config |
| `project_inputs.py` | river-km files, region shapefiles, land boundaries |
| `measurements.py` | SMB `.mat`, rasters + VRT, vakken shapefiles, catalogues |
| `expected.py` | expected results |
| `matlab_script.py` | Matlab `D3D_plot` input |
| `verify.py` | self-check |
| `generate.py` | entry point |
