# Upstream contributions

Track upstream changes that d3dplot needs before setting public dependency
minimums. See [the architecture plan](./architecture.md#14-upstream-contributions)
for the contribution workflow.

## HYDROLIB-core

| Item | Status |
|------|--------|
| Local branch | `feat/mdu_morphology_keywords` |
| Local commit | `54011e0d` |
| Changes | Add six standard `[sediment]` morphology keywords and three `ResearchGeometry` keywords; allow `Geometry.iniFieldFile` to use `DiskOnlyFileModel` when `recurse=False` |
| Verification | 3,343 passed, 10 skipped, 2 xfailed; Black and isort checks pass; read-only `recurse=False` loads pass for r209 sub-runs 0, 1, 194 and Rijn D16000 |
| Upstream status | Committed locally only; not pushed, proposed, merged, or released |
| Minimum release for d3dplot | TBD — use the first published HYDROLIB-core release containing these changes; until then, local development uses the editable checkout on the branch above |

### DIMR parallel control

| Item | Status |
|------|--------|
| Contribution branch | `fix/dimr-parallel-startgroup`, based on `main` |
| Local commits | `cb4047dc` makes `Parallel.startGroup` optional while keeping `start` required; `bd69c4ea` restores unrelated formatter changes |
| Integrated test checkout | `feat/mdu_morphology_keywords` includes the fix as cherry-picks `3e4f7317` and `32c63ae4`, on top of WP 0.1 (`54011e0d`) |
| Verification | 3,344 passed, 10 skipped, 2 xfailed in the non-local-data suite; 70 DIMR tests passed; real r209 sub-run 0 DIMR resolved and MDU/`.mor`/`.sed` inputs parsed read-only in 0.390 s |
| Upstream status | Committed locally only; not pushed, proposed, merged, or released |
| Minimum release for d3dplot | TBD — use the first published HYDROLIB-core release containing both WP 0.1 and the DIMR fix; until then, local development uses the editable checkout on the integrated branch above |
