# Model input

The `d3dplot.model_input` package provides read-only readers for D-Flow FM
model inputs. The readers are native to d3dplot and do not depend on
HYDROLIB-core at run time. Referenced files are never parsed recursively or
modified. The MDU keyword table is generated from the Deltares
`Delft3DFM.File.Specifications` `mdu.json` by `tools/generate_mdu_keywords.py`.
Unknown and obsolete keywords are accepted and reported, not rejected, so old
simulations can still be processed.

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
`DFM_OUTPUT_<mdu-stem>`. `gravity` and `water_density` use the MDU defaults from the
specification when not specified.

## Read morphology and sediment

`read_model_input(path)` returns a `ModelInput` containing `mdu`,
`morphology`, and `sediment`. Referenced `.mor` files provide `morfac` and
`morstt`. Each repeated `[Sediment]` section in a `.sed` file becomes a
`SedimentFraction` with its name, type, diameter or diameter range, and solid
density. These parsers use d3dplot's INI parser (`d3dplot.model_input.ini`).

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

`resolve_dimr_mdu(path)` reads the DIMR XML with the standard library to select
the unique component whose library is `dflowfm`. Its `workingDir` is resolved
relative to the DIMR file. A configuration with no FM component, multiple FM components,
or an FM component without an input file raises `ValueError`.

## Validation

`d3dplot.validate.validate_model_input(path)` returns `(model_input, report)`.
Unreadable MDUs and unparseable referenced `.mor`/`.sed` files become
`V-MDU-001` errors. For unknown MDU keywords, the report identifies the
section and keyword. Keywords that are deprecated, obsolete or under research
are reported as `V-MDU-003` info notes, which are written to the run's `.dia`
file and also appear in `validate` output. Only structural errors fail the
read.
