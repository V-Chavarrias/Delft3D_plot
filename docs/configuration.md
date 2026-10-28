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

`times.at` accepts `all`, `last`, a one-based integer index, an explicit list
of dates/times, an `{indices: [...]}` mapping, or a `{start, end, step}` range.
`NaN` is an alias for `all`; any NaN in a Matlab-style selector vector selects
all outputs. Positive `Inf` is an alias for `last` and may be combined with
one-based integer indices; use `{indices: [1, 3], include_last: true}` for the
equivalent explicit mapping. Use the readable `all`/`last` spellings in new
YAML configurations. Integer selectors are one-based to preserve Matlab
behavior, so `times.at: 1` selects the first output; `{indices: [1, 3]}`
selects the first and third outputs. `times.axis` is
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

Set `movie: {duration_s: 0.5}` on a `map2d` analysis to write one MP4 for each
subject, variable, comparison, extent, and colour-limit set. Frames are ordered
by hydrodynamic time, and `duration_s` is the duration of each frame. MP4
encoding requires the optional `movies` extra (`delft3d-plot[movies]`).

`language` selects English (`en`), Dutch (`nl`), or Spanish (`es`) labels.
`labels.vertical_datum` controls the datum shown for reference elevations, and
`labels.extra_dictionaries` lists additional translation CSVs. See
[Labels](./labels.md) for their format and the label-building API.

## Execution and sharding

```yaml
execution:
  executor: processes
  workers: null
  shard: null
  process_block_times: 50
  slurm:
    nodes: 1
    tasks_per_node: 4
    partition: 4vcpu
    time_limit: "12:00:00"
    job_name: null
```

`execution.shard` uses the zero-based `i/N` form and selects tasks by a stable
digest of their canonical task keys. The SLURM stub uses
`execution.slurm.tasks_per_node` as `--cpus-per-task`; only one node per array
job is currently supported. `d3dplot slurm write project.yaml --dry-run`
prints the guarded job template without writing. The template expects the
cluster-visible YAML path in `D3DPLOT_CONFIG`; choose a zero-based array range
at submission time. Automatic submission and the `d3dplot run` command remain
deferred.

## Map measurements

Map2d measurements are configured per variable. CSV catalogues require `time`,
`file`, and `factor` columns; YAML catalogues use the same fields and may also
specify `type`, `crs`, and `value_field`. Catalogue entries accept GeoTIFF,
VRT, and CF raster NetCDF files, or polygon Shapefiles and GeoPackages.
Relative catalogue paths resolve from the working directory, while relative
source-file paths resolve from the catalogue's directory. `paths.mappings`
translates configured paths when moving between Windows and Linux.

```yaml
analyses:
  - analysis: map2d
    variables: [bed_level]
    measurements:
      bed_level:
        catalogue: ./measurements/bed_level.csv
        time_zone: "+01:00"
        time_tolerance: 30 days
        spatial_margin_m: 1000
        mosaic: first
        color_limits_from_measurements: true
        polygon_filters:
          preset: rws_vakken
```

Naive catalogue timestamps use `measurements.<variable>.time_zone`, then the
model/project fixed UTC offset, and otherwise UTC. A file's values are
multiplied by its catalogue `factor`. Raster tiles are aligned with nearest
neighbour resampling and mosaicked in catalogue order; `mosaic` may be `first`,
`last`, or `mean`. `time_tolerance` and `spatial_margin_m` select the files for
each plotted time and extent. Polygon values can be filtered with explicit
fields or the `rws_vakken` preset. Time differences match polygons by ID, then
geometry, then record order with a count check; simulation differences do not
display measurements. Measurements appear in a side-by-side panel sharing the
model's colormap and limits. `color_limits_from_measurements` uses measurement
values for automatically selected limits. Set `project.crs` when the model grid
does not provide a usable CRS; measurement plotting does not infer the map CRS
from the measurement files.

## Defaults and supported analyses

If omitted, `times.at` defaults to `all`, `times.axis` to `hydrodynamic`, and
`times.tolerance` is unset (`null`), which means unbounded nearest-output
matching for explicit requests. Setting a tolerance limits selection to outputs
within that distance. A morphological time axis requires output `morft` values.
The analysis option `active_sediment_thickness_threshold_m` defaults to
`0.001` m and controls which faces count toward the `morphological_active_area`
quantity (`ba_mor`): a face contributes its cell area when total sediment
thickness is at least the threshold, and zero otherwise.

The default figure style is 10 pt DejaVu Sans, 16 × 10 cm at 150 dpi. Maps use
the `viridis` colormap, `auto` rendering, and `auto` aspect. The
`river_km_statistics` analysis defaults to `mean_area_weighted` and the
`profile` view.

River-km files use the fixed comma-delimited `x, y, label, km` format. The
branch is the label suffix after the first underscore and is matched
case-insensitively. Rijntakken files require `river_km.track`; Maas files switch
from `MA` to `BM` at 230.5 km only when `BM` is present. For a generic file with
multiple branch tags, set `river_km.branches` to one branch to use throughout or
one branch per requested km centre.

River-km region polygons may contain multiple polygon parts and holes. Their
source CRS must be present; when the model CRS is known, polygons are
transformed before face membership is calculated. Missing or incompatible CRS
information is reported as `V-SMB-002`. Segment polygons are validated by
default. Set `river_km.allow_invalid_segments: true` only when intentionally
accepting invalid segment geometry or overlapping segments; overlapping faces
are still assigned deterministically to the lower segment index and remain
available to diagnostics.

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

## Plotting and provenance replay

`d3dplot plot project.yaml` renders configured map2d analyses from existing
processed caches; it does not reopen model output. Each run stores its fully
resolved configuration as `config/run_<timestamp>.yaml` and updates
`config/latest.yaml` under the reference simulation's `d3dplot/` output folder.
The command also accepts that output folder (or its `config/latest.yaml`) as
input. Use repeated `--set path=value` options for temporary overrides, and
`--simulation`, `--variable`, `--only`, or `--fresh-figures` to narrow or rebuild
the plot stage.

Each PNG records the resolved configuration and the identity needed to replay
that figure. `d3dplot replot path/to/figure.png` regenerates only that map2d
figure; it does not regenerate the other frames or movies. `replot` accepts the
same repeated `--set` overrides. It reports `V-PLOT-005` when the PNG lacks
valid d3dplot provenance or its recorded figure can no longer be reproduced.

## JSON Schema

`d3dplot.config.dump_json_schema()` returns the JSON Schema for the project
configuration. It can be saved as `d3dplot.schema.json` for editor
autocompletion.
