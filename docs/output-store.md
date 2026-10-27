# Output store

The store resolves simulation output paths, guards writes, performs atomic
replacement, and fingerprints inputs and generated artefacts. Its rules ensure
that generated files stay in an explicitly marked output folder and away from
configured input locations.

## Output layout

Every simulation gets a `d3dplot/` folder using this precedence:

1. `simulations[*].output_dir/d3dplot/`;
2. `output.root/<simulation label>/d3dplot/`;
3. `<simulation folder>/d3dplot/`.

`OutputLayout.for_simulation(simulation, project.output)` resolves this path and
exposes standard locations such as `cache`, `figures`, `tables`, `config`, and
`logs`. See [configuration](./configuration.md#output-locations-and-safety)
for the corresponding project settings.

## Marked roots and guarded writes

`initialize_output_root` creates `.d3dplot-output` only when the root is new or
empty. It records the tool version and a time-zone-aware creation time. An
existing marker is validated and reused; a non-empty unmarked folder, invalid
marker, or root inside a forbidden directory is rejected with
`OutputWriteError`.

`OutputWriteGuard` permits writes only beneath the marked root and outside
configured forbidden roots. It also prevents overwriting the marker. Use the
guard with the store writers rather than writing output files directly:

```python
from d3dplot.store import (
    OutputLayout,
    atomic_write_text,
    initialize_output_root,
)

layout = OutputLayout("postprocessing/reference/d3dplot")
guard = initialize_output_root(layout, tool_version="0.1.0")
atomic_write_text(layout.tables / "status.txt", "finished\n", guard)
```

The text, bytes, and JSON writers create a temporary file beside the target and
replace the target with `os.replace`, so readers do not observe a partially
written file. `atomic_path` provides the same guarded lifecycle for other
writers. The user-facing `.dia` and developer `.log` files are the deliberate
streaming exceptions: `guarded_text_stream` still validates the marked root and
write guard, opens each unique run file exclusively, and flushes each line so
diagnostic progress and worker records are retained after a crash. Run-summary
CSVs use the guarded atomic writer. Cache and data outputs continue to use
atomic replacement.

## Fingerprints

Fingerprints use BLAKE2b over deterministic, sorted-key JSON:

* `fingerprint_small_input` hashes the complete contents of a small input file.
* `fingerprint_model_output` uses the normalized path, size, and modification
  time by default. With `hash_content=True`, it also hashes the first and last
  1 MiB and the file size; this costs additional I/O but can detect content
  changes that preserve file metadata.
* `fingerprint_artifact` includes an artefact kind, schema and algorithm
  versions, tool version, result-affecting parameters, upstream fingerprints,
  and an optional hydrodynamic time. Nested parameters are canonicalized
  recursively, so key ordering does not affect the result.

  ## Map2d data cache

  Processed map values are stored at
  `cache/data/map2d/<requested-variable>/<selection>/<yyyymmddTHHMMSS>.nc`.
  Each file contains `values(face)` in the configured cache dtype (float32 by
  default), compressed with zlib level 4 and using NaN as the fill value. The
  global attributes record the recursive fingerprint, canonical parameters,
  hydrodynamic and morphological times, sub-run/local index, variable,
  selection, inspected grid path, and merge-plan index. The variable carries its
  units and long name.

  Writes use `atomic_path` beneath the marked output root. Fingerprints include
  the sub-run map files, MDU and optional morphology/sediment inputs, the
  fingerprinted inspected grid and merge plan, selection, corrupt-output policy,
  cache dtype, and hydrodynamic time. A retained non-singleton extra dimension is
  reported as V-VAR-005 instead of being silently flattened into the face axis.

  The test helper in `tests/_input_snapshot.py` captures each input's resolved
  path, size, modification time, and content hash before an operation, then
  asserts those values remain unchanged afterward.
