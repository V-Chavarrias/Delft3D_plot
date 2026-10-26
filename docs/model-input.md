# Model input

The `d3dplot.model_input` package provides read-only adapters for D-Flow FM
model inputs. MDU files are loaded through HYDROLIB-core's
`ResearchFMModel(path, recurse=False)`, so referenced files are not
recursively parsed or modified.

## Read an MDU

```python
from pathlib import Path

from d3dplot.model_input import MduInfo

mdu = MduInfo.from_path(Path("./model/dflowfm.mdu"))
print(mdu.refdate, mdu.tunit, mdu.tstart, mdu.tstop)
print(mdu.output_dir, mdu.map_file)
```

`tstart` and `tstop` use `StartDateTime`/`StopDateTime` when configured;
otherwise they contain the MDU's numeric `TStart`/`TStop` values in `tunit`.
Input references (`net_file`, `mor_file`, and `sed_file`) and `output_dir` are
resolved relative to the MDU directory. `map_file` retains the MDU's output
path, defaulting to `<mdu-stem>_map.nc`; `output_dir` defaults to
`DFM_OUTPUT_<mdu-stem>`. `gravity` and `water_density` use HYDROLIB-core's
MDU defaults when not specified.

## Read morphology and sediment

`read_model_input(path)` returns a `ModelInput` containing `mdu`,
`morphology`, and `sediment`. Referenced `.mor` files provide `morfac` and
`morstt`. Each repeated `[Sediment]` section in a `.sed` file becomes a
`SedimentFraction` with its name, type, diameter or diameter range, and solid
density. These parsers use HYDROLIB-core's format-independent INI parser.

```python
from pathlib import Path

from d3dplot.model_input import read_model_input

model_input = read_model_input(Path("./model/dflowfm.mdu"))
for fraction in model_input.sediment.fractions:
    print(fraction.name, fraction.diameter_m, fraction.solid_density_kg_m3)
```

Missing optional file references and optional values are represented as
`None`. Read or parse failures raise `ModelInputError`.

## Resolve a DIMR component

`resolve_dimr_mdu(path)` uses HYDROLIB-core's `DIMR` model to select the unique
component whose library is `dflowfm`. Its `workingDir` is resolved relative to
the DIMR file. A configuration with no FM component or multiple FM components
raises `ValueError`.

## Validation

`d3dplot.validate.validate_model_input(path)` returns `(model_input, report)`.
Unreadable MDUs and unparseable referenced `.mor`/`.sed` files become
`V-MDU-001` errors. For unknown MDU keywords, the report identifies the
section and keyword and suggests updating HYDROLIB-core or reporting the
keyword upstream.
