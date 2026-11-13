# Contributing

The implementation follows the [architecture plan](docs/architecture.md). Work
one work package at a time and keep its handover in §0 current.

## Environment

The project supports Python 3.11–3.13. From a checkout with a sibling
`dfm_tools` repository:

### Windows PowerShell

```powershell
uv venv .venv --python 3.12
uv pip install --python .venv\Scripts\python.exe -e ..\dfm_tools -e '.[dev,docs]'
```

### Linux

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -e ../dfm_tools -e '.[dev,docs]'
```

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

While developing d3dplot, never write to P-drive data (`p:\`), and never commit files from `data_local/`. The tool itself writes its default output into each simulation's `d3dplot/` folder, which can be on the P-drive (plan D124); set `output.root` to a local folder to keep outputs elsewhere.
