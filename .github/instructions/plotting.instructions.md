---
description: "Use when writing or changing figures, plots, maps, line plots, colour bars, hooks or figure saving in d3dplot. Covers the common figure finalisation, shared plot building blocks, draw-once templates and plot option names."
applyTo: "src/d3dplot/plotting/**, src/d3dplot/analyses/**"
---
# Plotting rules (plan §7.14, §7.14a, §7.14b, §7.21)

## Finalisation: one exit for every figure
- Every figure ends in `finalize_figure(fig, context, style, hooks, writer)`. It applies fonts/size, language, **user hooks**, metadata, saves (writer registry, PNG in v1) and closes or keeps the figure.
- Analysis and view code **never** calls `fig.savefig`, `plt.savefig`, `plt.close`, or sets font sizes directly.
- Hooks (Matlab `function_handles`) run after styling and before saving, at project, analysis and view level, with signature `hook(fig, context)`. A new figure type must provide a complete `context` (axes by role, data, times, extent, labels).

## Reuse the building blocks
| Need | Use |
|------|-----|
| Values on a grid | `MapView` (+ `MapDecorations`, `ExtentSet`, optional `MeasurementPanel`) |
| Windows along a river-km file | `ExtentSet` from `extents_along_river_km` (independent of `river_km_labels`) |
| Lines, or stacked areas for multi-component data | `LineView` (`display: auto \| lines \| stacked_area`) |
| km × time colour plot | `SpaceTimeView` |

Do not create a new figure function when a view can be composed. If a view lacks a feature, extend the view (all analyses benefit).

## Performance
- Draw once, save many: create the figure and static artists (grid `PolyCollection`, land boundaries, labels, tiles) once per group; per image only `set_array`, `set_clim`, titles, line data.
- Only draw faces visible in the extent (cached `extent_<hash>.nc`); use raster mode for very dense views.
- Rebuild an artist only when its compatibility key changes (measurement grids, polygon sets).
- `--fresh-figures` must produce pixel-identical images; add/keep that test for new views.

## Options and labels
- Use the shared map settings block (`map:` → `MapViewConfig`) and the existing names: `color_limits`, `colormap`, `extents`, `extents_along_river_km`, `river_km_labels`, `land_boundaries`, `background_tiles`, `aspect`, `render`, `x_limits`, `y_limits`, `display`, `hooks`, `figure.font_size`, `figure.size_cm`, `figure.dpi`, `figure.formats`.
- Axis/legend/colour-bar texts come from `labels.label(...)` (en/nl/es dictionaries), never hard-coded strings.
- Output paths contain the subject (`<simulation>`, `<sim>_vs_<ref>`, `all_simulations`) so no two tasks write the same file.
