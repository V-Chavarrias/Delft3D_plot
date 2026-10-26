# SPDX-License-Identifier: GPL-3.0-or-later
"""Resolve standard paths for one simulation's d3dplot output."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from d3dplot.config.project import OutputConfig, SimulationConfig

OUTPUT_MARKER_NAME = ".d3dplot-output"


def resolve_output_root(
    simulation_path: str | Path,
    label: str,
    *,
    project_output_root: str | Path | None = None,
    simulation_output_dir: str | Path | None = None,
) -> Path:
    """Apply the project output-root precedence and return its d3dplot folder."""
    if simulation_output_dir is not None:
        base = Path(simulation_output_dir)
    elif project_output_root is not None:
        project_root = Path(project_output_root).expanduser().resolve(strict=False)
        label_root = (project_root / label).resolve(strict=False)
        if label_root == project_root or not is_path_within(label_root, project_root):
            raise ValueError("simulation label must stay inside output.root")
        base = label_root
    else:
        base = Path(simulation_path)
    return (base.expanduser() / "d3dplot").resolve(strict=False)


def is_path_within(path: Path, parent: Path) -> bool:
    """Return whether ``path`` is ``parent`` or one of its descendants."""
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


@dataclass(frozen=True, slots=True)
class OutputLayout:
    """Stable paths beneath one simulation's marked output root."""

    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "root", Path(self.root).expanduser().resolve(strict=False)
        )

    @classmethod
    def for_simulation(
        cls,
        simulation: SimulationConfig,
        output: OutputConfig,
    ) -> OutputLayout:
        """Resolve a simulation's output layout using project configuration."""
        return cls(
            resolve_output_root(
                simulation.path,
                simulation.label,
                project_output_root=output.root,
                simulation_output_dir=simulation.output_dir,
            )
        )

    @property
    def marker(self) -> Path:
        return self.root / OUTPUT_MARKER_NAME

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def cache_inputs(self) -> Path:
        return self.cache / "inputs"

    @property
    def cache_geometry(self) -> Path:
        return self.cache / "geometry"

    @property
    def cache_measurements(self) -> Path:
        return self.cache / "measurements"

    @property
    def cache_data(self) -> Path:
        return self.cache / "data"

    @property
    def figures(self) -> Path:
        return self.root / "figures"

    @property
    def tables(self) -> Path:
        return self.root / "tables"

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def logs(self) -> Path:
        return self.root / "logs"
