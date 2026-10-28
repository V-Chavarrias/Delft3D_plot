---
name: d3dplot-create-input
description: "Create or update a d3dplot YAML project for D-Flow FM or SMT simulations. Use when preparing a new analysis configuration, selecting variables or times, or migrating a Matlab D3D_plot setup."
---
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# Create a d3dplot project configuration

Use this workflow to create a valid project YAML without modifying simulation
inputs.

## Procedure

1. Ask which simulation folders and analysis the user wants if they are not
   specified. The built-in analyses are `map2d` and `river_km_statistics`.
2. Inspect each simulation with `d3dplot info <simulation>` and
   `d3dplot variables <simulation>`. These commands are read-only. Use the
   reported canonical names, aliases, and raw fields rather than guessing.
3. Generate a starter file with
   `d3dplot init <simulation> -o <new-config.yaml>`. The command refuses to
   overwrite an existing file. For multiple simulations, pass each folder as
   an argument and select a reference simulation in the YAML when needed.
4. Edit the generated YAML to set analyses, variables, time selection,
   comparisons, and any analysis-specific inputs. For `river_km_statistics`,
   replace all placeholder region and river-km file paths before validation.
5. Set `output.root` to a local output directory outside model inputs and
   read-only/network data locations. Do not use the simulation input folder or
   P-drive as the generated output location.
6. Preserve Matlab selectors when migrating: `NaN` means all outputs, positive
   `Inf` means the last output, and integer indices are one-based. For the
   `ba_mor` alias, retain
   `active_sediment_thickness_threshold_m` (default `0.001` m) unless the user
   requests another threshold.
7. Run `d3dplot schema` if a field's exact spelling or type is uncertain, then
   run `d3dplot validate <config.yaml>`. Review every issue and fix the YAML
   or source path; do not suppress validation errors.

## Current processing limitation

`validate` may build guarded grid, merge-plan, time-axis, and diagnostic
outputs. Those are inspection artefacts, not processed quantity caches. The
current CLI does not expose the end-to-end `process` or `run` command. Do not
claim that validation has processed model quantities or that a project is
ready to plot unless the required quantity caches already exist.

## Safety

- Treat model files, measurements, shapefiles, and rasters as read-only.
- Never overwrite an existing project YAML or measurement file.
- Keep generated files under a local configured `output.root`.
- Do not invent file paths, available variables, or supported analysis
  settings; inspect the simulation and consult the configuration reference.
