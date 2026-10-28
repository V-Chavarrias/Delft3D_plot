# Upstream contributions

Track upstream changes that d3dplot needs before setting public dependency
minimums. See [the architecture plan](./architecture.md#14-upstream-contributions)
for the contribution workflow.

## HYDROLIB-core

| Item | Status |
|------|--------|
| Local branch | `feat/mdu_morphology_keywords` |
| Local commit | `54011e0d`; SonarCloud follow-up `69096c17` (not pushed) |
| Changes | Add six standard `[sediment]` morphology keywords and three `ResearchGeometry` keywords; allow `Geometry.iniFieldFile` to use `DiskOnlyFileModel` when `recurse=False` |
| Verification | Full local suite: 3,350 passed, 10 skipped, 2 xfailed; read-only `recurse=False` loads pass for r209 sub-runs 0, 1, 194 and Rijn D16000 |
| Upstream status | PR [Deltares/HYDROLIB-core#1278](https://github.com/Deltares/HYDROLIB-core/pull/1278) is open at remote head `79741685`; local follow-up `69096c17` is not pushed, merged, or released |
| Minimum release for d3dplot | TBD — use the first published HYDROLIB-core release containing these changes; until then, local development uses the editable checkout on the branch above |

### DIMR parallel control

| Item | Status |
|------|--------|
| Contribution branch | `fix/dimr-parallel-startgroup`, based on `main` |
| Local commits | `cb4047dc` makes `Parallel.startGroup` optional while keeping `start` required; `bd69c4ea` restores unrelated formatter changes |
| Integrated test checkout | `feat/mdu_morphology_keywords` includes the fix as cherry-picks `3e4f7317` and `32c63ae4`, on top of WP 0.1 (`54011e0d`) |
| Verification | 3,344 passed, 10 skipped, 2 xfailed in the non-local-data suite; 70 DIMR tests passed; real r209 sub-run 0 DIMR resolved and MDU/`.mor`/`.sed` inputs parsed read-only in 0.390 s |
| Upstream status | Included in open PR [Deltares/HYDROLIB-core#1278](https://github.com/Deltares/HYDROLIB-core/pull/1278); local SonarCloud follow-up `69096c17` is not pushed; not merged or released |
| Minimum release for d3dplot | TBD — use the first published HYDROLIB-core release containing both WP 0.1 and the DIMR fix; until then, local development uses the editable checkout on the integrated branch above |

### DIMR external component libraries

| Item | Status |
|------|--------|
| Contribution branch | `fix/dimr-external-libraries`, based on `origin/main` |
| Local commit | `25e390bd` adds a generic component for non-`rr_dll`/`dflowfm` libraries and a parse/round-trip regression test |
| Integrated development branch | `feat/mdu_morphology_keywords` includes cherry-pick `96d07c87`, after WP 0.1 and the parallel-control fix |
| Verification | Integrated HYDROLIB-core suite: 3,345 passed, 10 skipped, 2 xfailed; 71 DIMR tests passed. d3dplot's 51 CI-eligible tests, strict mypy, changed-file Ruff/format checks, and strict MkDocs build pass; read-only r209 discovery found 195 sub-runs and 8,580 map files without issues in 99.283 s |
| Upstream status | Included in open PR [Deltares/HYDROLIB-core#1278](https://github.com/Deltares/HYDROLIB-core/pull/1278); local SonarCloud follow-up `69096c17` is not pushed; not merged or released |
| Minimum release for d3dplot | TBD — use the first published HYDROLIB-core release containing WP 0.1 and both DIMR fixes; until then, local development uses the integrated branch above |
