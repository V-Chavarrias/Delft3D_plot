# Configuration

Project files are validated with Pydantic v2 models. Unknown keys and invalid
values are rejected. YAML is loaded with `yaml.safe_load`; JSON files are also
supported. Dates and ISO-8601 datetimes are accepted as time selections.
The [command-line interface](./cli.md) provides schema export and validation
against discovered simulation output.

## Example

```yaml
simulations:
  - path: ./model
    label: reference
reference_simulation: reference
language: en
labels:
  vertical_datum: "+NAP"
  extra_dictionaries: []
output:
  root: ./postprocessing
status:
  stale_after: 1 hour
analyses:
  - analysis: map2d
    variables: [bed_level]
    times:
      at: [2021-02-08, 2021-08-08]
      axis: morphological
      tolerance: 10 days
    comparisons: [value, difference_time]
    map:
      render: auto
      extents: [[10000, 12000, 20000, 22000]]
```

`times.at` accepts `all`, `last`, an explicit list of dates/times, an
`{indices: [...]}` mapping, or a `{start, end, step}` range. `times.axis` is
`hydrodynamic` or `morphological`; `times.tolerance` accepts durations such as
`10 days` or ISO-8601 durations. Measurement-matching tolerances remain scoped
to their measurement settings, for example
`measurements.bed_level.time_tolerance`.
With `times.tolerance: null`, explicit requests select the nearest available
output without a distance limit; a configured tolerance is a hard maximum
distance. Duplicate hydrodynamic times within one second and morphological
times within 60 seconds resolve to the last output in hydrodynamic order.
`duplicate_morphological_times` remains an analysis-level setting and can keep
the first output for morphological duplicates. `time_matching` selects whether
other simulations follow the reference simulation's selected times or use
their own.
`status.stale_after` controls how long output files must remain unchanged before
an incomplete run is reported as stopped; it defaults to one hour and accepts
durations such as `30 minutes`.

`time_zone` accepts a fixed UTC offset such as `+01:00`; it is used only when
the map time units have no offset and the MDU does not explicitly set `Tzone`.
Map time units take precedence over explicit MDU `Tzone`, which takes
precedence over the project setting. Conflicts are reported as `V-TIME-002`.
`on_corrupt_output` controls selected outputs with corrupt partitions: `mask`
keeps the output and masks affected faces, `skip` omits it, and `error` rejects
it. `check_boundary_duplicates` defaults to `true` and compares bed levels at
duplicate SMT restart outputs; mismatched `morft` is an error (`V-OUT-032`) and
different bed levels are a warning (`V-OUT-033`).

Map drawing options belong to the shared `map` block. In particular, use
`map.render` (`polygons`, `raster`, or `auto`) for map rendering. Global figure
style is configured under `figure`; analysis-level values override only the
fields explicitly set there.

`language` selects English (`en`), Dutch (`nl`), or Spanish (`es`) labels.
`labels.vertical_datum` controls the datum shown for reference elevations, and
`labels.extra_dictionaries` lists additional translation CSVs. See
[Labels](./labels.md) for their format and the label-building API.

## Defaults and supported analyses

If omitted, `times.at` defaults to `all`, `times.axis` to `hydrodynamic`, and
`times.tolerance` is unset (`null`), which means unbounded nearest-output
matching for explicit requests. Setting a tolerance limits selection to outputs
within that distance. A morphological time axis requires output `morft` values.

The default figure style is 10 pt DejaVu Sans, 16 × 10 cm at 150 dpi. Maps use
the `viridis` colormap, `auto` rendering, and `auto` aspect. The
`river_km_statistics` analysis defaults to `mean_area_weighted` and the
`profile` view.

The current schema recognizes the built-in `map2d` and `river_km_statistics`
analyses. Plugin configuration models will be discovered and included in
validation and JSON Schema when the planned `d3dplot.analyses` registry is
implemented.

## Static validation

`d3dplot.validate.validate_config(data)` accepts parsed configuration data and
returns `(project_config, report)`. Pydantic errors become `V-CFG-001` issues
and return `None` for the project; otherwise, the validated project is returned
along with static checks for the reference simulation, duplicate simulation
labels or output roots, and matching row counts when both `figure.x_limits` and
`figure.y_limits` are configured as lists. Use `report.is_valid`,
`report.to_text()`, or `report.to_json()` to inspect the result.
`validate_project_config(config)` runs the static checks on an already
validated `ProjectConfig`.

## Output locations and safety

Each simulation writes beneath its own `d3dplot/` folder. The base directory is
chosen in this order:

1. `simulations[*].output_dir`, when set;
2. `output.root/<simulation label>`, when `output.root` is set;
3. the simulation folder.

`output.forbidden_roots` lists directories that must not contain generated
output. Validation reports `V-OUT-041` when a resolved output root is inside
one of those directories, is not writable, or is an invalid non-empty,
unmarked folder.

Before a write, the store requires a `.d3dplot-output` marker. It creates this
marker only in a new or empty output folder; an existing marked folder can be
reused. Writes are guarded and use same-directory temporary files followed by
an atomic replacement. See [Output store](./output-store.md) for the marker,
guard, and fingerprint APIs.

## Python API

```python
from d3dplot import load_config
from d3dplot.config import apply_overrides, dump_config
from d3dplot.validate import validate_config

config = load_config("project.yaml")
config = apply_overrides(config, ["analyses[0].figure.dpi=300"])
yaml_text = dump_config(config)

validated_config, report = validate_config(config.model_dump(mode="python"))
if not report.is_valid:
    print(report.to_text())
```

Override values are parsed as YAML, so booleans, numbers, lists, dates, and
durations retain their types. Dotted paths and zero-based list indices are
supported.

`dump_config` preserves explicitly configured values so project-level figure
settings continue to be inherited. The resolved-config writer serializes model
defaults and effective analysis figure, reprocess, and replot settings. File
writers are supplied by the caller so runtime outputs can go through the
store's write guard.

## JSON Schema

`d3dplot.config.dump_json_schema()` returns the JSON Schema for the project
configuration. It can be saved as `d3dplot.schema.json` for editor
autocompletion.
