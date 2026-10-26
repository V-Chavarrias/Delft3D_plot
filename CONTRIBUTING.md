# Contributing

The implementation follows the [architecture plan](docs/architecture.md). Work
one work package at a time and keep its handover in §0 current.

## Environment

The project supports Python 3.11–3.13. From a checkout with sibling
`dfm_tools` and `HYDROLIB-core` repositories:

### Windows PowerShell

```powershell
uv venv .venv --python 3.12
uv pip install --python .venv\Scripts\python.exe -e ..\dfm_tools -e ..\HYDROLIB-core -e '.[dev,docs]'
```

### Linux

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -e ../dfm_tools -e ../HYDROLIB-core -e '.[dev,docs]'
```

The HYDROLIB-core checkout must include the upstream changes recorded in
[`docs/upstream.md`](docs/upstream.md) for work that reads morphology MDUs.

## Checks

```powershell
.venv\Scripts\python.exe -m pytest -m "not requireslocaldata"
uv run --no-sync ruff check src tests
uv run --no-sync ruff format --check src tests
uv run --no-sync mypy src/d3dplot
uv run --no-sync mkdocs build --strict
```

Install the Git hooks with `pre-commit install` and
`pre-commit install --hook-type commit-msg`. Use conventional commit messages;
Commitizen maintains `CHANGELOG.md` when versions are released.

Never write to P-drive data or commit files from `data_local/`.
