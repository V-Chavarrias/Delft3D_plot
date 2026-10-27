# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for map2d quantity processing and its NetCDF cache format."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import netCDF4
import numpy as np
import pytest

from d3dplot import __version__
from d3dplot.analyses.map2d.process import (
    Map2DProcessError,
    Map2DProcessResult,
    Map2DTaskProcessor,
)
from d3dplot.execution.diagnostics import DiagnosticLog
from d3dplot.execution.orchestrator import run_process_stage
from d3dplot.execution.tasks import AnalysisTimeSelection, plan_process_tasks
from d3dplot.simulation import discover_simulation, inspect_simulation
from d3dplot.simulation.map_reader import MapReader
from d3dplot.simulation.time_axis import TimeAxis, TimeSelectionResult
from d3dplot.store import OutputLayout, initialize_output_root
from d3dplot.variables.context import QuantityRequest
from d3dplot.variables.registry import get_variable_spec


@pytest.mark.integration
def test_map2d_processor_writes_analytic_and_selected_quantities(
    synthetic_case: Any,
    tmp_path: Path,
) -> None:
    simulation, report = discover_simulation(synthetic_case / "simulations" / "r001")
    assert simulation is not None
    assert report.is_valid

    layout = OutputLayout(tmp_path / "d3dplot")
    inspection, inspection_report = inspect_simulation(
        simulation,
        layout,
        forbidden_roots=(),
    )
    assert inspection is not None, inspection_report.to_text()
    guard = initialize_output_root(
        layout,
        tool_version=__version__,
        forbidden_roots=(),
    )

    time_axis = TimeAxis.from_cache(inspection.times_path)
    expected = json.loads(
        (synthetic_case / "expected" / "quantities_r001.json").read_text(
            encoding="utf-8"
        )
    )
    entry = time_axis.entries[expected["time_index"]]
    selection = AnalysisTimeSelection(
        analysis_key="map2d-0",
        quantities=(
            QuantityRequest("water_depth"),
            QuantityRequest("Fr"),
            QuantityRequest("vorticity"),
            QuantityRequest("d50", fractions=(1,), layers=(0,)),
            QuantityRequest("dzetadt"),
        ),
        selection=TimeSelectionResult((entry,), (), ()),
    )
    (task,) = plan_process_tasks(
        "r001",
        (selection,),
        workers=1,
        max_block_times=50,
    )
    processor = Map2DTaskProcessor(
        simulation,
        "r001",
        inspection,
        layout,
        guard,
        analysis_keys=("map2d-0",),
    )
    with DiagnosticLog.for_project(
        (layout,),
        tool_version=__version__,
        command_line=("pytest",),
        forbidden_roots=(),
    ) as diagnostics:
        summary = run_process_stage(
            (task,),
            processor,
            executor="serial",
            diagnostics=diagnostics,
            summary_path=layout.tables / "run-summary.csv",
            guard=guard,
        )
    assert summary.exit_code == 0
    result = summary.outcomes[0].value
    assert isinstance(result, Map2DProcessResult)
    assert len(result.output_paths) == 5
    assert any("water_level_rate used mesh2d_s0" in choice for choice in result.choices)
    assert "water_level_rate used mesh2d_s0" in diagnostics.paths[0].read_text(
        encoding="utf-8"
    )

    paths_by_variable = {path.parents[1].name: path for path in result.output_paths}
    face = expected["face_index"]
    reader = MapReader(simulation, inspection.merge_plan_path)
    with reader.session(entry.subrun) as map_session:
        raw_water_depth = map_session.read(
            get_variable_spec("water_depth"),
            local_index=entry.local_index,
            corrupt_partitions=entry.corrupt_partitions,
        ).values

    with netCDF4.Dataset(paths_by_variable["water_depth"], mode="r") as dataset:
        np.testing.assert_allclose(
            dataset.variables["values"][:],
            raw_water_depth,
            rtol=2 * np.finfo(np.float32).eps,
            atol=0,
        )
        assert dataset.variables["values"].dtype == np.dtype("float32")
        assert dataset.variables["values"].filters()["complevel"] == 4
        assert dataset.getncattr("time") == entry.time.isoformat()
        assert dataset.getncattr("variable") == "water_depth"
        assert dataset.getncattr("plan") == 0
        assert dataset.getncattr("d3dplot_cache_schema") >= 1
        assert (
            Path(dataset.getncattr("grid_file")).name == inspection.grid_paths[0].name
        )

    with netCDF4.Dataset(paths_by_variable["fr"], mode="r") as dataset:
        np.testing.assert_allclose(
            dataset.variables["values"][face],
            expected["froude_number"],
            rtol=1e-6,
        )
    with netCDF4.Dataset(paths_by_variable["vorticity"], mode="r") as dataset:
        np.testing.assert_allclose(
            dataset.variables["values"][:],
            expected["vorticity_s_1"],
            rtol=1e-6,
        )
    with netCDF4.Dataset(paths_by_variable["d50"], mode="r") as dataset:
        np.testing.assert_allclose(
            dataset.variables["values"][face],
            expected["d50"],
            rtol=1e-6,
        )
        assert dataset.getncattr("selection") == "fraction-1-layer-0"
    with netCDF4.Dataset(paths_by_variable["dzetadt"], mode="r") as dataset:
        np.testing.assert_allclose(
            dataset.variables["values"][face],
            expected["water_level_rate_m_s"],
            rtol=1e-6,
        )

    component_selection = AnalysisTimeSelection(
        analysis_key="map2d-0",
        quantities=(QuantityRequest("velocity_vector"),),
        selection=TimeSelectionResult((entry,), (), ()),
    )
    (component_task,) = plan_process_tasks(
        "r001",
        (component_selection,),
        workers=1,
        max_block_times=50,
    )
    with pytest.raises(Map2DProcessError) as error:
        processor(component_task)
    assert error.value.issue.code == "V-VAR-005"
