# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for map2d quantity processing and its NetCDF cache format."""

from __future__ import annotations

import csv
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
from d3dplot.analyses.river_km_statistics.config import RiverKmStatisticsConfig
from d3dplot.execution.diagnostics import DiagnosticLog
from d3dplot.execution.orchestrator import run_process_stage
from d3dplot.execution.tasks import AnalysisTimeSelection, plan_process_tasks
from d3dplot.simulation import discover_simulation, inspect_simulation
from d3dplot.simulation.map_reader import MapReader, MapReaderSession
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


@pytest.mark.integration
@pytest.mark.parametrize("simulation_label", ("r001", "r002"))
def test_combined_map2d_and_river_km_share_quantity_reads_and_match_synthetic(
    synthetic_case: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    simulation_label: str,
) -> None:
    from expected import EXACT_REGIONS

    simulation, report = discover_simulation(
        synthetic_case / "simulations" / simulation_label
    )
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
    entries = time_axis.entries
    expected_by_region: dict[str, list[dict[str, str]]] = {}
    for region_name in EXACT_REGIONS:
        expected_path = (
            synthetic_case
            / "expected"
            / f"smb_{simulation_label}_{region_name}_250m.csv"
        )
        with expected_path.open(newline="", encoding="utf-8") as file:
            expected_by_region[region_name] = list(csv.DictReader(file))
    centres = list(
        dict.fromkeys(float(row["km"]) for row in expected_by_region[EXACT_REGIONS[0]])
    )
    statistics = (
        "count",
        "mean",
        "mean_area_weighted",
        "std",
        "max",
        "min",
        "sum",
        "sum_per_length",
    )
    config = RiverKmStatisticsConfig.model_validate(
        {
            "variables": ["bed_level", "layer_fraction"],
            "regions": [
                {
                    "name": region_name,
                    "polygon": str(
                        synthetic_case / "inputs" / "regions" / f"{region_name}.shp"
                    ),
                }
                for region_name in EXACT_REGIONS
            ],
            "river_km": {
                "file": str(synthetic_case / "inputs" / "rkm" / "rkm.csv"),
                "sets": {"250m": centres},
                "half_width_m": 750.0,
            },
            "statistics": list(statistics),
            "multiply_by_width": "flow",
        }
    )
    map2d_selection = AnalysisTimeSelection(
        analysis_key="map2d-bed-level",
        quantities=(QuantityRequest("bed_level"),),
        selection=TimeSelectionResult(entries, (), ()),
    )
    river_km_selection = AnalysisTimeSelection(
        analysis_key="river-km-bed-level",
        quantities=(
            QuantityRequest("bed_level"),
            QuantityRequest("layer_fraction", fractions=(1, 2), layers=(0,)),
        ),
        selection=TimeSelectionResult(entries, (), ()),
    )
    tasks = plan_process_tasks(
        simulation_label,
        (map2d_selection, river_km_selection),
        workers=1,
        max_block_times=50,
    )
    processor = Map2DTaskProcessor(
        simulation,
        simulation_label,
        inspection,
        layout,
        guard,
        analysis_keys=("map2d-bed-level",),
        river_km_analyses={"river-km-bed-level": config},
        model_crs="EPSG:28992",
    )

    raw_reads: list[tuple[int, int]] = []
    original_read = MapReaderSession.read

    def count_bed_level_reads(
        session: MapReaderSession,
        spec: Any,
        *,
        local_index: int,
        corrupt_partitions: tuple[int, ...] = (),
    ) -> Any:
        if spec.name == "bed_level":
            raw_reads.append((session.subrun_index, local_index))
        return original_read(
            session,
            spec,
            local_index=local_index,
            corrupt_partitions=corrupt_partitions,
        )

    monkeypatch.setattr(MapReaderSession, "read", count_bed_level_reads)
    with DiagnosticLog.for_project(
        (layout,),
        tool_version=__version__,
        command_line=("pytest",),
        forbidden_roots=(),
    ) as diagnostics:
        summary = run_process_stage(
            tasks,
            processor,
            executor="serial",
            diagnostics=diagnostics,
            summary_path=layout.tables / "run-summary.csv",
            guard=guard,
        )

    assert summary.exit_code == 0
    assert set(raw_reads) == {(entry.subrun, entry.local_index) for entry in entries}
    assert len(raw_reads) == len(entries)
    outcomes = [outcome.value for outcome in summary.outcomes]
    assert all(isinstance(outcome, Map2DProcessResult) for outcome in outcomes)
    output_paths = [
        path
        for outcome in outcomes
        for path in outcome.output_paths
        if isinstance(outcome, Map2DProcessResult)
    ]
    map_root = layout.cache_data / "map2d"
    statistics_root = layout.cache_data / "river_km_statistics"
    map_paths = [path for path in output_paths if path.is_relative_to(map_root)]
    statistics_paths = [
        path for path in output_paths if path.is_relative_to(statistics_root)
    ]
    assert len(map_paths) == len(entries)
    assert len(statistics_paths) == 2 * len(entries) * len(EXACT_REGIONS)

    entries_by_location = {
        (entry.subrun, entry.local_index): entry for entry in entries
    }
    expected_by_region_time = {
        (region_name, int(row["time_index"])): []
        for region_name, rows in expected_by_region.items()
        for row in rows
    }
    float32_tolerance = 3 * np.finfo(np.float32).eps
    for region_name, rows in expected_by_region.items():
        for row in rows:
            expected_by_region_time[(region_name, int(row["time_index"]))].append(row)
    layer_fraction_paths: list[Path] = []
    bed_level_path_count = 0
    for path in statistics_paths:
        with netCDF4.Dataset(path, mode="r") as dataset:
            region_name = str(dataset.getncattr("region"))
            if dataset.getncattr("variable") == "layer_fraction":
                layer_fraction_paths.append(path)
                assert dataset.variables["values"].shape[-1] == 2
                continue
            bed_level_path_count += 1
            location = (
                int(dataset.getncattr("subrun")),
                int(dataset.getncattr("local_index")),
            )
            entry = entries_by_location[location]
            actual_statistics = tuple(
                str(value) for value in dataset.variables["statistic"][:]
            )
            assert actual_statistics == statistics
            actual = np.ma.filled(dataset.variables["values"][:], np.nan)
            assert dataset.variables["segment_width_m"].shape == (len(centres),)
            assert dataset.variables["segment_width_m"].getncattr("basis") == "flow"
            rows = expected_by_region_time[(region_name, entry.time_index)]
            expected = np.asarray(
                [
                    [float(row[name]) for name in statistics]
                    for row in sorted(rows, key=lambda row: int(row["segment"]))
                ],
                dtype=np.float64,
            )
            np.testing.assert_allclose(
                actual,
                expected,
                rtol=float32_tolerance,
                atol=float32_tolerance,
            )
    expected_path_count = len(entries) * len(EXACT_REGIONS)
    assert bed_level_path_count == expected_path_count
    assert len(layer_fraction_paths) == expected_path_count
    first_sediment = next(
        subrun.model_input.sediment
        for subrun in simulation.subruns
        if subrun.model_input.sediment is not None
    )
    expected_components = tuple(
        first_sediment.fractions[index - 1].name or f"fraction_{index}"
        for index in (1, 2)
    )
    with netCDF4.Dataset(layer_fraction_paths[0], mode="r") as dataset:
        actual_components = tuple(
            str(name) for name in dataset.variables["component_name"][:]
        )
        assert actual_components == expected_components
