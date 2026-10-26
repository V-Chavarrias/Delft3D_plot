# Naming checklist

Before adding an option, check whether the concept already has a name. Full table: `docs/architecture.md` Appendix A.

| Concept | Name | Values / type |
|---------|------|---------------|
| Variables to analyse | `variables` | canonical names (`bed_level`, …), aliases and raw NetCDF names accepted |
| Times | `times` | `all`, `last`, list, `{indices}`, `{start, end, step}` |
| Time axis | `time_axis` | `hydrodynamic` \| `morphological` |
| Time matching tolerance | `time_tolerance` | duration |
| Duplicate morphological times | `duplicate_morphological_times` | `last` \| `first` |
| Pairing simulations in time | `time_matching` | `reference` \| `own` |
| Comparisons | `comparisons` | `value`, `difference_time`, `difference_simulation`, `difference_simulation_time`, `difference_simulation_relative` |
| Vertical selection | `vertical` | `top`, `bottom`, `all`, `{layers}`, `{depth_average}`, `{elevation}` |
| Fractions | `fractions`, `sum_fractions` | list or `all`; bool |
| Colour limits | `color_limits` | `{variable: {comparison: [[min, max], …]}}`, `null` = automatic |
| Axis limits (line plots) | `x_limits`, `y_limits` | list of pairs, one figure per pair |
| Map windows | `extents`, `extents_along_river_km` | bounds; `{file, window_x_m, window_y_m}` |
| River-km labels on maps | `river_km_labels` | `{file, color}` |
| Overlays | `land_boundaries`, `background_tiles` | `.ldb` files; provider |
| Lines or areas | `display` | `auto` \| `lines` \| `stacked_area` |
| Plot types | `views` | per analysis, e.g. `profile`, `time_series`, `space_time` |
| Grouping | `grouping` | `per_simulation` \| `all_simulations` |
| User functions | `hooks` | `"module:function"` at project, analysis, view level |
| Figure style | `figure.font_size`, `figure.size_cm`, `figure.dpi`, `figure.formats` | |
| Cache / figure reuse | `reprocess`, `replot` | `if_changed` \| `never` \| `always` |
| Corrupt model output | `on_corrupt_output` | `mask` \| `error` \| `skip` |
| Measurements | `measurements`, `time_tolerance`, `spatial_margin_m`, `color_limits_from_measurements` | |
| Output location | `output.root`, `simulations[k].output_dir` | |

Rules: English `snake_case`; no `do_` prefixes (enable by listing); enums instead of numbers; units in the name when ambiguous (`_m`, `_s`, `_cm`); the same name means the same thing in every analysis.
