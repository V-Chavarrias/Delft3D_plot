---
name: d3dplot-replot
description: "Replay or adjust a d3dplot PNG using embedded provenance. Use when changing one figure's styling or configuration without rerunning other figures."
---
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# Replay one d3dplot figure

`d3dplot replot` regenerates one map2d PNG from its recorded configuration and
figure provenance.

## Procedure

1. Confirm that the target is a PNG produced by d3dplot and that its run
   configuration and processed caches are still available.
2. If the user wants a configuration change, identify the exact YAML path.
   Check its spelling and type with `d3dplot schema`.
3. Run `d3dplot replot <figure.png>`. Apply a temporary override with one or
   more `--set path=value` options, for example:

   ```powershell
   d3dplot replot .\figure.png --set analyses[0].figure.dpi=300
   ```

4. Report whether replay succeeded. If provenance is missing, invalid, or the
   original cache is unavailable, explain the reported error rather than
   recreating or editing metadata manually.
5. Keep generated output within the marked local d3dplot output root. Do not
   write beside model inputs or overwrite source data.

## Limits

- Replot writes only the selected figure; it does not regenerate the full
  figure series or a movie.
- The current implementation replays map2d PNG figures with d3dplot
  provenance.
- An override is temporary and does not edit the original YAML file.
