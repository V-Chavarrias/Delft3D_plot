# Output store

The store resolves simulation output paths, guards writes, performs atomic
replacement, and fingerprints inputs and generated artefacts. Its rules ensure
that generated files stay in an explicitly marked output folder and away from
configured input locations.

## Output layout

Every simulation gets a `d3dplot/` folder using this precedence:

1. `simulations[*].output_dir/d3dplot/`;
2. `output.root/d3dplot/`;
3. `<simulation folder>/d3dplot/`.

Because `output.root` is used directly, it must resolve to a unique output
folder. For projects with multiple simulations, set a distinct
`simulations[*].output_dir` for each one or omit `output.root`.

By default (no `output_dir` and no `output.root`), the output goes to
`<simulation folder>/d3dplot/`. For P-drive projects this is on the P-drive,
which is allowed. Model inputs are never modified.

`OutputLayout.for_simulation(simulation, project.output)` resolves this path and
exposes standard locations such as `cache`, `figures`, `tables`, `config`, and
`logs`. See [configuration](./configuration.md#output-locations-and-safety)
for the corresponding project settings.

Validation and inspection reports are written atomically beneath `tables/` as
`validation_<timestamp>.txt|json` or `inspection_<timestamp>.txt`.

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

## Map1d caches

Chain geometry is cached once per chain at `cache/geometry/map1d_chain_<chain>.nc`.
It holds `index`, `offset_m`, `x` and `y` along the chain, with the fingerprint in the
global attributes. A changed chain or map input rebuilds the same file, so there is one
file per chain. Processed values
are cached per time at `cache/data/map1d/<variable>/<chain>/<yyyymmddTHHMMSS.ffffff>.nc`
and hold only `values` and the fingerprint. Both are fingerprinted from the map
input (path and MDU), so a changed map or MDU rebuilds them.

## Map2d data cache

Processed map values are stored at
`cache/data/map2d/<canonical-variable>/<selection>/<yyyymmddTHHMMSS.ffffff>.nc`.
Variable-specific cache and figure paths use the shared store path helpers, which
resolve configured aliases to registry names (`h` becomes `water_depth`).
Each file contains `values(face)` in the configured cache dtype (float32 by
default), compressed with zlib level 4 and using NaN as the fill value. The
global attributes record the recursive fingerprint, canonical parameters,
hydrodynamic and morphological times, sub-run/local index, variable, selection,
inspected grid path, and merge-plan index. The variable carries its units and
long name.

Writes use `atomic_path` beneath the marked output root. Fingerprints include
the sub-run map files, MDU and optional morphology/sediment inputs, the
fingerprinted inspected grid and merge plan, selection, corrupt-output policy,
cache dtype, and hydrodynamic time. A retained non-singleton extra dimension is
reported as V-VAR-005 instead of being silently flattened into the face axis.

## Map2d geometry and figures

Visible-face subsets are cached as
`cache/geometry/extent_<hash>.nc`. The `faces` variable contains the
visible grid-face indices as int32, with the extent bounds and fingerprint in
global attributes. Cross-grid comparisons may cache an xugrid centroid
regridder at `cache/geometry/regrid_<hash>.nc`.

Profile images are stored below `figures/<series>/profile/<simulation>/<variable>/<line>/`,
so every profile analysis writes into the same `profile` folder and the variable separates them.
Within that, the optional selection folder (for example `layer-2`), the comparison folder
(`value`, `difference_time`, `difference_simulation`), an optional limit-set folder
`l<n>/` when several `y_limits` rows are given, and the time as the filename, for
example `value/20020101T080255.366337.png`. Two profile analyses that would write the same
variable, line, selection and view folders are rejected. Section figures use
`<line>/[<selection>/]section/<comparison>/[l<n>/]` and space-time figures
`<line>/space_time/all_times.png`. Map1d figures follow the same rule: the `all_times` and
`space_time` views are `all_times.png`, not named after the first time. Simulation
differences of profile and map1d are written under the reference simulation's output root,
with `<source>_vs_<reference>` in the simulation folder, as map2d does.

Map images are stored below `figures/<series>/map2d/<variable>/<subject>/<comparison>/`
in a colour-limit folder `c<k>/` (D113), with the hydrodynamic time and extent index
(`e<k>`) in the filename, for example
`bed_level/Hirano_vs_HANNEKE/difference_simulation/c1/20210101T000000.000000_e1.png`.
Map movies are `c<k>/movie_e<k>.mp4` in the same folder. Variable, comparison, plot type and
colour limits are folders, not filename parts. PNG metadata records the tool version, project/analysis configuration
hash, figure fingerprint, and task key. Figure fingerprints include the
processed cache fingerprints, inspected grid, extent, effective figure/map
settings, and figure-hook source hashes. A matching image is reused when
`replot` is `if_changed`; `--fresh-figures` always rebuilds it. PNGs and
geometry caches are written atomically through the marked-root guard.

The test helper in `tests/_input_snapshot.py` captures each input's resolved
path, size, modification time, and content hash before an operation, then
asserts those values remain unchanged afterward.
