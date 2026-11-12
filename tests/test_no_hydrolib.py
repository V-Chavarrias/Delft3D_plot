# SPDX-License-Identifier: GPL-3.0-or-later
"""Guard: model input and the CLI work without HYDROLIB-core (plan D121, WP R.10).

The check runs in a fresh interpreter because other tests in the same process may
already have imported HYDROLIB-core. dfm_tools still imports it, so the guard covers
the model-input readers and ``d3dplot info`` only.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_CHILD = r"""
import sys

sys.modules["hydrolib"] = None  # any later "import hydrolib..." raises ImportError
try:
    import hydrolib.core  # noqa: F401
except ImportError:
    pass
else:
    raise SystemExit("hydrolib is still importable")

from pathlib import Path

from typer.testing import CliRunner

from d3dplot.cli import app
from d3dplot.model_input.polyline import read_polyline_file
from d3dplot.model_input.reader import read_model_input

case = Path(sys.argv[1])
model = read_model_input(case / "simulations" / "fm_single" / "dflowfm.mdu")
assert model.mdu is not None
assert len(read_polyline_file(case / "inputs" / "ldb" / "synthetic.ldb")) == 3
result = CliRunner().invoke(app, ["info", str(case / "simulations" / "fm_single")])
assert result.exit_code == 0, result.output
print("ok")
"""


def test_model_input_and_info_run_without_hydrolib(synthetic_case: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD, str(synthetic_case)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip().endswith("ok")
