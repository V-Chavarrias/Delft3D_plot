---
name: upstream-contribution
description: "Workflow to change HYDROLIB-core or dfm_tools (local branch, tests, optional pull request) when d3dplot needs a fix or feature there. Use when a reader, keyword, parser, grid or partition function is missing or wrong in HYDROLIB-core or dfm_tools, e.g. the MDU keyword change of plan section 14.1, merge-plan reader, SMT support, morphological time, zonal statistics, river-km utilities."
argument-hint: "Which repository and what change, e.g. 'HYDROLIB-core: MDU sediment keywords (plan 14.1)'"
---
# Contribute to HYDROLIB-core or dfm_tools

The user is a contributor of both repositories. **For now, changes stay local** (no push, no PR) unless the user asks.

**Branch or `main`?** Creating a *local* branch needs no access rights; access is only needed to push. Use a local feature branch from the start (step 3): `git pull` on `main` keeps working, several changes stay separate, and the PR is ready when access exists. If work was committed on local `main` anyway, move it later:
```powershell
git -C <checkout> switch -c feat/<short_title>      # new branch at the current main, keeps the commits
git -C <checkout> switch main
git -C <checkout> reset --hard origin/main          # only after checking `git -C <checkout> log origin/main..feat/<short_title>`
```
With uncommitted changes, `git switch -c feat/<short_title>` alone moves them to the new branch. The editable install in d3dplot's `.venv` follows whatever is checked out.

| Repository | Checkout | Remote | Conventions |
|------------|----------|--------|-------------|
| HYDROLIB-core | `c:\checkouts\HYDROLIB-core` | `Deltares/HYDROLIB-core` | poetry, black, isort, pytest, commitizen; `docs/guides/contributing.md`; branches `feat/<issue>_<title>` or `fix/…`; squash merge with `Refs: #<issue>` |
| dfm_tools | `c:\checkouts\dfm_tools` | `Deltares/dfm_tools` | pytest markers (`unittest`, `systemtest`, `requireslocaldata`), flake8; `docs/CONTRIBUTING.md` |

## Procedure
1. **Check the plan**: the change must be listed in `docs/architecture.md` §14 (HYDROLIB-core MDU keywords: detailed in §14.1). If not, add it there first with the reason (finding, measurement).
2. **Check upstream**: search open issues/PRs of the repository for the topic (avoid duplicates; link related ones).
3. **Branch** from an up-to-date `main` in the checkout: `git -C <checkout> switch -c feat/<short_title>` (add the issue number once an issue exists). Do not commit on `main`.
4. **Implement** following the repository's own style and patterns (look at neighbouring code; e.g. `Sediment`/`morfile` in `hydrolib/core/dflowfm/mdu/models.py`). Use the official specifications (e.g. `Deltares/Delft3DFM.File.Specifications` `mdu.json`) for names, types and defaults.
5. **Test in the repository**: hand-written minimal test data only (**never project data from `p:\`**); run the repository's formatters and full tests.
6. **Verify from d3dplot**: the checkout is installed editable in `c:\checkouts\Delft3D_plot\.venv`, so d3dplot uses the branch directly. Verify on the synthetic case and, read-only, on the real case if relevant (e.g. `ResearchFMModel` loads the `r209` MDU).
7. **Commit locally** with conventional commits and the trailer `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`. Do not push unless the user asks.
8. **Record** in plan §14 / `docs/upstream.md`: branch name, status (local / issue / PR / merged / released), and the minimum version d3dplot will need. d3dplot's PyPI release requires an upstream **release** containing the change (plan WP 0.2).

## Rules
- Keep upstream changes general (useful beyond d3dplot) and minimal; no d3dplot-specific code upstream.
- Keep a d3dplot adapter only while the upstream change is not released; remove it afterwards.
- Read-only on `p:\`, always.
