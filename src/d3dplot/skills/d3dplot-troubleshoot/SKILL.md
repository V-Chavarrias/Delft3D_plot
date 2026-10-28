---
name: d3dplot-troubleshoot
description: "Diagnose d3dplot configuration, simulation discovery, validation, cache, plotting, and provenance errors. Use when a d3dplot command fails or reports a validation code."
---
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# Troubleshoot d3dplot

Diagnose from the exact command and reported evidence. Do not modify model
inputs to make a check pass.

## Procedure

1. Capture the full command, exit code, error text, validation code, and
   relevant configuration section. Do not ask the user to expose unrelated
   private model files.
2. For discovery problems, run the read-only
   `d3dplot info <simulation>` and `d3dplot variables <simulation>` commands.
   Check that the selected folder contains the expected MDU or SMT layout and
   readable map output.
3. For project problems, run `d3dplot validate <config.yaml>`. Read the
   reported stable code and fix the specific invalid path, variable, time,
   CRS, or option. Use `d3dplot schema` for exact field names and types.
4. Distinguish inspection from processing: `validate` and `inspect` can write
   guarded inspection caches and diagnostics, but they do not produce
   per-analysis quantity caches. The current CLI has no end-to-end
   `process`/`run` command.
5. For plot failures, check that the required processed caches exist and that
   the configuration and input fingerprints still match. For `replot`, verify
   that the PNG contains valid d3dplot provenance and its source cache remains
   available.
6. Make any proposed output root local and outside model inputs. Inspect
   output-store errors before suggesting cleanup; do not delete caches or
   figures unless the user explicitly requests it.
7. Summarize the cause, the supported corrective action, and any remaining
   limitation. If the error points to an upstream parser or reader, follow the
   project's upstream-contribution workflow instead of adding a local reader.

## Safety

- Model files and measurement inputs are read-only.
- Never direct output to P-drive or other read-only/network input locations.
- Do not bypass output guards, validation, or provenance checks.
- Do not describe a failed command as successful or silently substitute
  defaults for invalid input.
