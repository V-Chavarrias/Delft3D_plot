# SPDX-License-Identifier: GPL-3.0-or-later
"""Compatibility wrapper for the internal project processing runner."""

from d3dplot.execution.project_runner import main

if __name__ == "__main__":
    raise SystemExit(main())
