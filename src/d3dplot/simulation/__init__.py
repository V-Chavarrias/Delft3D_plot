# SPDX-License-Identifier: GPL-3.0-or-later
"""Discover D-Flow FM and SMT simulations."""

from d3dplot.simulation.discover import (
    Simulation,
    SimulationKind,
    discover_simulation,
)
from d3dplot.simulation.inspection import InspectionResult, inspect_simulation
from d3dplot.simulation.map_reader import (
    MapReader,
    MapReaderError,
    MapReadResult,
)
from d3dplot.simulation.status import (
    SimulationState,
    SimulationStatus,
    SubRunStatus,
    detect_simulation_status,
    dump_simulation_status_csv,
    dump_simulation_status_json,
    inspect_simulation_status,
    write_simulation_status_csv,
    write_simulation_status_json,
)
from d3dplot.simulation.subrun import SubRun
from d3dplot.simulation.time_axis import (
    DuplicateResolution,
    TimeAxis,
    TimeEntry,
    TimeSelectionResult,
)

__all__ = [
    "Simulation",
    "SimulationKind",
    "SimulationState",
    "SimulationStatus",
    "InspectionResult",
    "MapReadResult",
    "MapReader",
    "MapReaderError",
    "DuplicateResolution",
    "TimeAxis",
    "TimeEntry",
    "TimeSelectionResult",
    "SubRun",
    "SubRunStatus",
    "detect_simulation_status",
    "discover_simulation",
    "dump_simulation_status_csv",
    "dump_simulation_status_json",
    "inspect_simulation_status",
    "inspect_simulation",
    "write_simulation_status_csv",
    "write_simulation_status_json",
]
