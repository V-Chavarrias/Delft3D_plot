# SPDX-License-Identifier: GPL-3.0-or-later
"""Compatibility wrapper for the native project processing runner."""

from __future__ import annotations

import argparse
from pathlib import Path

from d3dplot.config.io import load_config
from d3dplot.execution import project_runner
from d3dplot.simulation.inspection import inspect_simulation


def _process_project(configuration_path: Path, *, serial: bool = False) -> int:
    """Run the native project runner with the historical helper signature."""
    project = load_config(configuration_path)
    original_inspection = project_runner.inspect_simulation
    project_runner.inspect_simulation = inspect_simulation
    try:
        result = project_runner.run_project(
            project,
            configuration_path=configuration_path,
            executor="serial" if serial else None,
        )
    finally:
        project_runner.inspect_simulation = original_inspection
    if result.exit_code:
        raise ValueError(
            "Project processing failed for: "
            + ", ".join(result.failure_messages or result.failed_simulations)
        )
    return result.exit_code


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Compatibility entry point for d3dplot process."
    )
    parser.add_argument("configuration", type=Path)
    parser.add_argument("--serial", action="store_true")
    arguments = parser.parse_args()
    return _process_project(arguments.configuration, serial=arguments.serial)


main = _main


if __name__ == "__main__":
    raise SystemExit(_main())
