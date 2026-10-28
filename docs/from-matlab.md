# From Matlab `D3D_plot`

`d3dplot` is a Python successor to the Matlab `D3D_plot` tool, not a
line-by-line port. Start by inspecting the simulation and its available
variables, then create a Python configuration with `d3dplot init`. Review the
configuration schema with `d3dplot schema` and validate it with
`d3dplot validate`.

## Common option mapping

| Matlab concept or flag | Python configuration | Notes |
|---|---|---|
| `tim` | `times.at` | Use `all`, `last`, explicit dates/times, a range, or one-based indices. |
| `tim_type` | `times.axis` | Use `hydrodynamic` or `morphological`. |
| `Ltot_thres` | `active_sediment_thickness_threshold_m` | Analysis-level threshold for the `ba_mor`/`morphological_active_area` quantity; defaults to `0.001` m. |
| `xlims`, `ylims` | `map.extents`; `figure.x_limits`, `figure.y_limits` | Map extents and line-plot axis limits are distinct settings. |
| `clims`, comparison-specific colour limits | `color_limits` | Configure per variable and comparison. |
| `filter_lims` | `mask_range` | Masks values outside configured ranges. |
| `cmap` | `colormap` | Matplotlib colormap name. |
| `fig_resolution`, `fig_size`, `font_size` | `figure.dpi`, `figure.size_cm`, `figure.font_size` | Applied during common figure finalisation. |
| `fig_print` | `figure.formats` | Select supported output formats. |
| `Lref` | `labels.vertical_datum` | Controls the displayed vertical datum. |
| `do_movie`, `tim_movie` | `movie: {duration_s: ...}` | Optional movie export from plotted map frames. |
| Matlab variable aliases such as `bl`, `qsp` | `variables` | Canonical names such as `bed_level` and `specific_discharge` are also accepted. |

Use the [configuration reference](./configuration.md) for the complete
supported settings and [variables](./variables.md) for the registered
quantities. Not every Matlab analysis, flag, or figure is implemented.

## Preserve Matlab time selectors

`times.at` intentionally preserves Matlab-compatible selector behavior:

| Matlab selector | Python YAML |
|---|---|
| `NaN` | `all` |
| `Inf` or `+Inf` | `last` |
| Integer `1` | `1`, meaning the first output (indices are one-based) |
| `[1, 3, Inf]` | `{indices: [1, 3], include_last: true}` |
| Any selector vector containing `NaN` | `all`, even if it also contains indices |

For example:

```yaml
analyses:
  - analysis: map2d
    variables: [bed_level]
    times:
      at:
        indices: [1, 3]
        include_last: true
      axis: hydrodynamic
```

New configurations should use the readable `all` and `last` values rather
than writing non-finite numbers. Negative `Inf` is invalid. Explicit dates and
times use nearest-output matching; an unset tolerance has no maximum distance,
while a configured `times.tolerance` is a hard limit.

## Preserve `ba_mor` meaning

Matlab's `ba_mor` is active sediment area, not sediment volume. In Python it is
named `morphological_active_area`. A face contributes its cell area when its
total sediment thickness is at least the configured threshold; otherwise it
contributes zero:

```yaml
analyses:
  - analysis: map2d
    variables: [ba_mor]
    active_sediment_thickness_threshold_m: 0.001
```

The default `0.001` m matches Matlab's `Ltot_thres = 1e-3`. The threshold can
be adjusted per analysis without changing the model input.

## Measurements and outputs

Map measurements use the configured raster/vector catalogue described in the
[configuration reference](./configuration.md#map-measurements). River-km
profile measurements in Matlab v5/v7 `.mat` files can be converted with
`d3dplot measurements convert`; provide a fixed UTC offset because Matlab
datenums do not include a timezone. Matlab v7.3/HDF5 measurement files are not
supported.

Keep model inputs read-only and direct generated output to a local
`output.root`. The current CLI can validate and inspect projects and can plot
from persisted quantity caches, but it does not yet provide the end-to-end
`process`/`run` commands. See the [user guide](./user-guide.md) for the
currently available workflow and its limits.
