# SPDX-License-Identifier: GPL-3.0-or-later
"""Local comparisons of Python caches with Matlab synthetic-case results."""

from __future__ import annotations

import csv
import json
import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import netCDF4
import numpy as np
import pytest
import scipy.io
from scipy.spatial import cKDTree

from d3dplot import __version__
from d3dplot.analyses.map2d.process import Map2DProcessResult, Map2DTaskProcessor
from d3dplot.analyses.river_km_statistics.config import RiverKmStatisticsConfig
from d3dplot.analyses.river_km_statistics.plot import load_statistic_cache_frames
from d3dplot.execution.diagnostics import DiagnosticLog
from d3dplot.execution.orchestrator import run_process_stage
from d3dplot.execution.tasks import AnalysisTimeSelection, plan_process_tasks
from d3dplot.simulation import discover_simulation, inspect_simulation
from d3dplot.simulation.time_axis import TimeAxis, TimeSelectionResult
from d3dplot.store import OutputLayout, initialize_output_root
from d3dplot.variables.context import QuantityRequest

_MATLAB_VARIABLES = {
    "bl": "bed_level",
    "qsp": "specific_discharge",
}
_MATLAB_STATISTICS = {
    "count": "val_num",
    "mean": "val_mean",
    "mean_area_weighted": "val_mean_weighted",
    "std": "val_std",
    "max": "val_max",
    "min": "val_min",
    "sum": "val_sum",
    "sum_per_length": "val_sum_length",
}
_MATLAB_TIME_ZONE = timezone(timedelta(hours=1))
_MATLAB_TIME_PATTERN = re.compile(r"(?:M2D|SMB)_(\d{14})_")
_SIMULATION_LABELS = ("r001", "r002")
_REGION_NAMES = ("L1R1", "L1L3", "R1R3")


def _matching_matlab_map_reference(synthetic_case: Path) -> Path | None:
    generator_version = json.loads(
        (synthetic_case / "parameters.json").read_text(encoding="utf-8")
    )["generator_version"]
    data_local = Path(__file__).resolve().parents[1] / "data_local"
    matching = []
    for candidate in sorted(data_local.glob("synthetic_smt_*")):
        parameters_path = candidate / "parameters.json"
        marker_path = candidate / ".synthetic_smt"
        if not parameters_path.is_file() or not marker_path.is_file():
            continue
        reference_version = json.loads(parameters_path.read_text(encoding="utf-8")).get(
            "generator_version"
        )
        if reference_version == generator_version:
            if all(
                len(
                    tuple(
                        (candidate / "simulations" / simulation_label / "mat").glob(
                            f"M2D_*_var_{matlab_variable}.mat"
                        )
                    )
                )
                == 5
                for simulation_label in _SIMULATION_LABELS
                for matlab_variable in _MATLAB_VARIABLES
            ):
                matching.append(candidate)
    return matching[-1] if matching else None


def _complete_matlab_smb_reference() -> Path | None:
    data_local = Path(__file__).resolve().parents[1] / "data_local"
    matching = []
    for candidate in sorted(data_local.glob("synthetic_smt_*")):
        if not (candidate / ".synthetic_smt").is_file():
            continue
        if all(
            len(
                tuple(
                    (candidate / "simulations" / simulation_label / "mat").glob(
                        f"SMB_*_pol_250m_var_bl_sb_{region_name}.mat"
                    )
                )
            )
            == 5
            for simulation_label in _SIMULATION_LABELS
            for region_name in _REGION_NAMES
        ):
            matching.append(candidate)
    return matching[-1] if matching else None


def _matlab_time(path: Path) -> datetime:
    match = _MATLAB_TIME_PATTERN.search(path.name)
    if match is None:
        raise ValueError(f"Matlab output filename has no hydrodynamic time: {path}")
    return (
        datetime.strptime(match.group(1), "%Y%m%d%H%M%S")
        .replace(tzinfo=_MATLAB_TIME_ZONE)
        .astimezone(UTC)
    )


@pytest.mark.integration
@pytest.mark.requireslocaldata
@pytest.mark.parametrize("simulation_label", ("r001", "r002"))
def test_synthetic_caches_match_matlab(
    synthetic_case: Path,
    tmp_path: Path,
    simulation_label: str,
) -> None:
    map_reference_case = _matching_matlab_map_reference(synthetic_case)
    statistics_reference_case = _complete_matlab_smb_reference()
    if map_reference_case is None or statistics_reference_case is None:
        pytest.skip(
            "Matching Matlab map or complete SMB reference results are unavailable."
        )

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
    analysis = RiverKmStatisticsConfig.model_validate(
        {
            "variables": ["bed_level"],
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
            "statistics": list(_MATLAB_STATISTICS),
        }
    )
    map_selection = AnalysisTimeSelection(
        analysis_key="matlab-map",
        quantities=tuple(
            QuantityRequest(quantity) for quantity in _MATLAB_VARIABLES.values()
        ),
        selection=TimeSelectionResult(entries, (), ()),
    )
    statistics_selection = AnalysisTimeSelection(
        analysis_key="matlab-smb",
        quantities=(QuantityRequest("bed_level"),),
        selection=TimeSelectionResult(entries, (), ()),
    )
    tasks = plan_process_tasks(
        simulation_label,
        (map_selection, statistics_selection),
        workers=1,
        max_block_times=50,
    )
    processor = Map2DTaskProcessor(
        simulation,
        simulation_label,
        inspection,
        layout,
        guard,
        analysis_keys=("matlab-map",),
        river_km_analyses={"matlab-smb": analysis},
        model_crs="EPSG:28992",
    )
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
    outcomes = tuple(outcome.value for outcome in summary.outcomes)
    assert all(isinstance(outcome, Map2DProcessResult) for outcome in outcomes)
    output_paths = tuple(
        path
        for outcome in outcomes
        if isinstance(outcome, Map2DProcessResult)
        for path in outcome.output_paths
    )

    map_cache_by_time: dict[tuple[str, datetime], Path] = {}
    grid_path: Path | None = None
    for path in output_paths:
        if not path.is_relative_to(layout.cache_data / "map2d"):
            continue
        with netCDF4.Dataset(path, mode="r") as dataset:
            variable_name = str(dataset.getncattr("variable"))
            cache_time = datetime.fromisoformat(
                str(dataset.getncattr("time"))
            ).astimezone(UTC)
            map_cache_by_time[(variable_name, cache_time)] = path
            if grid_path is None:
                grid_file = Path(str(dataset.getncattr("grid_file")))
                grid_path = (
                    grid_file
                    if grid_file.is_absolute()
                    else (path.parent / grid_file).resolve(strict=True)
                )
    assert grid_path is not None

    matlab_map_directory = map_reference_case / "simulations" / simulation_label / "mat"
    matlab_statistics_directory = (
        statistics_reference_case / "simulations" / simulation_label / "mat"
    )
    matlab_grid = scipy.io.loadmat(
        matlab_map_directory / "grd.mat",
        simplify_cells=True,
    )["gridInfo"]
    matlab_coordinates = np.column_stack(
        (
            np.asarray(matlab_grid["Xcen"], dtype=np.float64).reshape(-1),
            np.asarray(matlab_grid["Ycen"], dtype=np.float64).reshape(-1),
        )
    )
    with netCDF4.Dataset(grid_path, mode="r") as dataset:
        python_coordinates = np.column_stack(
            (
                np.asarray(dataset.variables["mesh2d_face_x"][:], dtype=np.float64),
                np.asarray(dataset.variables["mesh2d_face_y"][:], dtype=np.float64),
            )
        )
    distances, python_indices = cKDTree(python_coordinates).query(
        matlab_coordinates,
        k=1,
    )
    assert np.all(distances <= 0.001)
    assert np.unique(python_indices).size == matlab_coordinates.shape[0]

    for matlab_variable, quantity in _MATLAB_VARIABLES.items():
        references = sorted(
            matlab_map_directory.glob(f"M2D_*_var_{matlab_variable}.mat")
        )
        assert len(references) == 5
        for reference_path in references:
            matlab_values = np.asarray(
                scipy.io.loadmat(reference_path, simplify_cells=True)["data"],
                dtype=np.float64,
            ).reshape(-1)
            cache_path = map_cache_by_time[(quantity, _matlab_time(reference_path))]
            with netCDF4.Dataset(cache_path, mode="r") as dataset:
                python_values = np.ma.filled(
                    dataset.variables["values"][:],
                    np.nan,
                )[python_indices]
            assert matlab_values.shape == python_values.shape
            np.testing.assert_array_equal(
                np.isfinite(python_values),
                np.isfinite(matlab_values),
            )
            finite = np.isfinite(matlab_values)
            np.testing.assert_allclose(
                python_values[finite],
                matlab_values[finite],
                rtol=1e-6,
                atol=1e-5,
            )

    for region_name in EXACT_REGIONS:
        frames = load_statistic_cache_frames(
            layout,
            analysis,
            region_name,
            "250m",
            "bed_level",
        )
        frames_by_time = {frame.time: frame for frame in frames}
        references = sorted(
            matlab_statistics_directory.glob(
                f"SMB_*_pol_250m_var_bl_sb_{region_name}.mat"
            )
        )
        assert len(references) == 5
        for reference_path in references:
            matlab_data: Any = scipy.io.loadmat(
                reference_path,
                simplify_cells=True,
            )["data"]
            frame = frames_by_time[_matlab_time(reference_path)]
            assert frame.values.shape[0] == len(centres)
            for statistic, matlab_name in _MATLAB_STATISTICS.items():
                matlab_values = np.asarray(
                    matlab_data[matlab_name],
                    dtype=np.float64,
                ).reshape(-1)
                python_values = frame.values[:, frame.statistics.index(statistic)]
                np.testing.assert_allclose(
                    python_values,
                    matlab_values,
                    rtol=1e-6,
                    atol=1e-5,
                    equal_nan=True,
                )
