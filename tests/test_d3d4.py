# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for Delft3D 4 (trim) run reading, topology and map access."""

from __future__ import annotations

from pathlib import Path

import netCDF4
import numpy as np
import pytest

from d3dplot import __version__
from d3dplot.analyses.map2d.process import Map2DTaskProcessor
from d3dplot.execution.diagnostics import DiagnosticLog
from d3dplot.execution.orchestrator import run_process_stage
from d3dplot.execution.tasks import AnalysisTimeSelection, plan_process_tasks
from d3dplot.model_input.mdf import read_mdf
from d3dplot.model_input.mdu import MduInfo
from d3dplot.simulation import SimulationKind, discover_simulation, inspect_simulation
from d3dplot.simulation.d3d4 import (
    D3D4MapDataset,
    D3D4Run,
    find_d3d4_runs,
    is_morphodynamic,
    topology_path,
    write_topology,
)
from d3dplot.simulation.nefis import NefisFile
from d3dplot.simulation.time_axis import TimeAxis, TimeSelectionResult
from d3dplot.store import OutputLayout, initialize_output_root
from d3dplot.variables.context import QuantityRequest

REPO_ROOT = Path(__file__).resolve().parents[1]
D3D4_RUN = REPO_ROOT / "data_local" / "d3d4"
FM_MAP = REPO_ROOT / "data_local" / "fm" / "DFM_OUTPUT_r001" / "r001_map.nc"

_MDF_BASE = """\
Filcco = #grd.grd#
Itdate = #2000-01-01#
Tunit  = #S#
Tstart = 0.000000000000E+00
Tstop  = 6.016113167856E+04
Tzone  = 0
Ag     =  9.8100000e+000
Rhow   =  1.0000000e+003
"""


def _write_run(folder: Path, name: str, *, morphodynamic: bool) -> Path:
    """Write a minimal trim MDF; the morphology lines are optional."""
    mdf = folder / f"{name}.mdf"
    text = _MDF_BASE
    if morphodynamic:
        text += "Filsed = #sed.sed#\nFilmor = #mor.mor#\n"
    mdf.write_text(text, encoding="latin-1")
    return mdf


@pytest.mark.unit
def test_read_mdf_strips_hash_quotes_and_skips_comments(tmp_path: Path) -> None:
    mdf = tmp_path / "run.mdf"
    mdf.write_text(
        "Tunit  = #S#\nCommnt = #ignored#\nAg     =  9.81\nnot a keyword\n",
        encoding="latin-1",
    )

    assert read_mdf(mdf) == {"Tunit": "S", "Ag": "9.81"}


@pytest.mark.unit
def test_find_d3d4_runs_requires_mdf_and_trim_data(tmp_path: Path) -> None:
    _write_run(tmp_path, "a", morphodynamic=False)
    (tmp_path / "trim-a.def").write_bytes(b"")
    (tmp_path / "trim-a.dat").write_bytes(b"")
    _write_run(tmp_path, "b", morphodynamic=False)
    (tmp_path / "trim-b.def").write_bytes(b"")

    runs = find_d3d4_runs(tmp_path)

    assert [run.def_path.name for run in runs] == ["trim-a.def"]
    assert runs[0].data_path == tmp_path / "trim-a.dat"
    assert runs[0].mdf_path == tmp_path / "a.mdf"


@pytest.mark.unit
@pytest.mark.parametrize("morphodynamic", [True, False])
def test_morphodynamic_requires_sediment_and_morphology_files(
    tmp_path: Path, morphodynamic: bool
) -> None:
    mdf = _write_run(tmp_path, "run", morphodynamic=morphodynamic)

    mdu = MduInfo.from_d3d4_mdf(mdf)

    assert is_morphodynamic(mdu) is morphodynamic


@pytest.mark.requireslocaldata
@pytest.mark.skipif(not D3D4_RUN.is_dir(), reason="data_local/d3d4 not available")
def test_nefis_reads_trim_map_groups() -> None:
    run = find_d3d4_runs(D3D4_RUN)[0]

    with NefisFile(run.data_path, run.def_path) as nefis:
        assert {"map-const", "map-series", "map-infsed-serie"} <= set(nefis.groups)
        assert "S1" in nefis.elements
        s1 = nefis.read("map-series", "S1", index=1)
        dps0 = nefis.read("map-const", "DPS0", index=1)

    assert np.issubdtype(s1.dtype, np.floating)
    assert np.all(np.isfinite(s1))
    assert dps0.shape[:2] == s1.shape[:2]


@pytest.mark.requireslocaldata
@pytest.mark.skipif(not D3D4_RUN.is_dir(), reason="data_local/d3d4 not available")
def test_d3d4_discovery_reports_a_valid_run() -> None:
    simulation, report = discover_simulation(D3D4_RUN)

    assert simulation is not None
    assert report.is_valid
    assert simulation.kind is SimulationKind.D3D4
    assert len(simulation.subruns) == 1
    assert simulation.subruns[0].d3d4_run is not None


@pytest.fixture(scope="module")
def d3d4_topology(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, D3D4Run, MduInfo]:
    """Write the topology of the example run once for the topology tests."""
    simulation, _ = discover_simulation(D3D4_RUN)
    assert simulation is not None
    subrun = simulation.subruns[0]
    run = subrun.d3d4_run
    assert run is not None
    root = tmp_path_factory.mktemp("d3d4_out")
    guard = initialize_output_root(root / "out", tool_version="0.1.0")
    layout = OutputLayout(guard.root)
    path = topology_path(layout, run, subrun.mdu)
    write_topology(run, subrun.mdu, path, guard)
    return path, run, subrun.mdu


@pytest.mark.requireslocaldata
@pytest.mark.skipif(not D3D4_RUN.is_dir(), reason="data_local/d3d4 not available")
def test_topology_has_fm_style_mesh_and_morphology(
    d3d4_topology: tuple[Path, D3D4Run, MduInfo],
) -> None:
    path, _, _ = d3d4_topology

    with netCDF4.Dataset(str(path), mode="r") as dataset:
        assert dataset.dimensions["mesh2d_nFaces"].size == 10000
        assert dataset.dimensions["mesh2d_nNodes"].size == 10201
        assert "mesh2d" in dataset.variables
        assert dataset.variables["mesh2d_face_nodes"].start_index == 0
        assert dataset.variables["mesh2d_mor_bl"].shape == (31, 10000)
        assert dataset.variables["time"].shape == (31,)


@pytest.mark.requireslocaldata
@pytest.mark.skipif(
    not (D3D4_RUN.is_dir() and FM_MAP.is_file()),
    reason="data_local d3d4 or fm run not available",
)
def test_d3d4_face_centres_match_fm_reference(
    d3d4_topology: tuple[Path, D3D4Run, MduInfo],
) -> None:
    path, _, _ = d3d4_topology

    with netCDF4.Dataset(str(path), mode="r") as topology:
        d3d4_x = np.asarray(topology.variables["mesh2d_face_x"][:])
        d3d4_y = np.asarray(topology.variables["mesh2d_face_y"][:])
    with netCDF4.Dataset(str(FM_MAP), mode="r") as fm:
        fm_x = np.asarray(fm.variables["mesh2d_face_x"][:])
        fm_y = np.asarray(fm.variables["mesh2d_face_y"][:])

    d3d4_order = np.lexsort((d3d4_y, d3d4_x))
    fm_order = np.lexsort((fm_y, fm_x))
    assert np.allclose(d3d4_x[d3d4_order], fm_x[fm_order], rtol=0.0, atol=1e-6)
    assert np.allclose(d3d4_y[d3d4_order], fm_y[fm_order], rtol=0.0, atol=1e-6)


@pytest.mark.requireslocaldata
@pytest.mark.skipif(not D3D4_RUN.is_dir(), reason="data_local/d3d4 not available")
def test_map_dataset_reads_water_level_and_velocity(
    d3d4_topology: tuple[Path, D3D4Run, MduInfo],
) -> None:
    path, run, mdu = d3d4_topology

    with D3D4MapDataset(path, run, morphodynamic=is_morphodynamic(mdu)) as dataset:
        s1 = dataset.variables["mesh2d_s1"]
        ucx = dataset.variables["mesh2d_ucx"]
        ucy = dataset.variables["mesh2d_ucy"]
        assert s1.shape == (31, 10000)
        first = np.asarray(s1[0])
        last = np.asarray(s1[30])
        assert first.shape == (10000,)
        assert np.all(np.isfinite(first)) and np.all(np.isfinite(last))
        assert np.all(np.isfinite(np.asarray(ucx[15])))
        assert np.all(np.isfinite(np.asarray(ucy[30])))


@pytest.mark.requireslocaldata
@pytest.mark.skipif(not D3D4_RUN.is_dir(), reason="data_local/d3d4 not available")
def test_map_dataset_derives_depth_speed_and_edge_normal_velocity(
    d3d4_topology: tuple[Path, D3D4Run, MduInfo],
) -> None:
    path, run, mdu = d3d4_topology

    with (
        netCDF4.Dataset(str(path), mode="r") as topology,
        D3D4MapDataset(path, run, morphodynamic=is_morphodynamic(mdu)) as dataset,
    ):
        edge_nodes = np.asarray(topology.variables["mesh2d_edge_nodes"][:])
        node_x = np.asarray(topology.variables["mesh2d_node_x"][:])
        node_y = np.asarray(topology.variables["mesh2d_node_y"][:])
        last = 30
        water_level = np.asarray(dataset.variables["mesh2d_s1"][last])
        bed = np.asarray(topology.variables["mesh2d_mor_bl"][last])
        depth = np.asarray(dataset.variables["mesh2d_waterdepth"][last])
        np.testing.assert_allclose(depth, water_level - bed, rtol=0.0, atol=1e-12)

        speed = np.asarray(dataset.variables["mesh2d_ucmag"][last])
        expected_speed = np.hypot(
            np.asarray(dataset.variables["mesh2d_ucx"][last]),
            np.asarray(dataset.variables["mesh2d_ucy"][last]),
        )
        np.testing.assert_allclose(speed, expected_speed, rtol=0.0, atol=1e-12)

        edge_velocity = np.asarray(dataset.variables["mesh2d_u1"][last])
        assert edge_velocity.shape == (edge_nodes.shape[0],)
        dx = node_x[edge_nodes[:, 1]] - node_x[edge_nodes[:, 0]]
        dy = node_y[edge_nodes[:, 1]] - node_y[edge_nodes[:, 0]]
        vertical = np.abs(dy) > np.abs(dx)
        # Uniform flow in +x: a vertical edge carries sign(dy) times the speed. The
        # computed speed varies by about 1 % over the grid, so the magnitude is loose.
        np.testing.assert_array_equal(
            np.sign(edge_velocity[vertical]), np.sign(dy[vertical])
        )
        np.testing.assert_allclose(np.abs(edge_velocity[vertical]), 1.0, atol=5e-2)
        np.testing.assert_allclose(edge_velocity[~vertical], 0.0, atol=1e-2)


@pytest.mark.requireslocaldata
@pytest.mark.skipif(not D3D4_RUN.is_dir(), reason="data_local/d3d4 not available")
def test_d3d4_map2d_processing_writes_finite_quantities(tmp_path: Path) -> None:
    layout = OutputLayout(tmp_path / "d3dplot")
    simulation, report = discover_simulation(D3D4_RUN, output_layout=layout)
    assert simulation is not None
    assert report.is_valid
    assert simulation.kind is SimulationKind.D3D4

    inspection, inspection_report = inspect_simulation(
        simulation, layout, forbidden_roots=()
    )
    assert inspection is not None, inspection_report.to_text()
    guard = initialize_output_root(layout, tool_version=__version__, forbidden_roots=())

    time_axis = TimeAxis.from_cache(inspection.times_path)
    entry = time_axis.entries[-1]
    selection = AnalysisTimeSelection(
        analysis_key="map2d-0",
        quantities=(
            QuantityRequest("water_depth"),
            QuantityRequest("Fr"),
            QuantityRequest("vorticity"),
        ),
        selection=TimeSelectionResult((entry,), (), ()),
    )
    (task,) = plan_process_tasks("r001", (selection,), workers=1, max_block_times=50)
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
    assert len(result.output_paths) == 3
    for path in result.output_paths:
        with netCDF4.Dataset(str(path), mode="r") as dataset:
            data_variables = [
                np.asarray(variable[:])
                for variable in dataset.variables.values()
                if variable.dtype.kind == "f" and variable.ndim >= 1
            ]
        assert data_variables
        assert any(np.all(np.isfinite(values)) for values in data_variables)
