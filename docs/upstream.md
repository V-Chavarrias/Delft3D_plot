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
