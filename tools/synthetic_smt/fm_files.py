# SPDX-License-Identifier: GPL-3.0-or-later
"""Writers for D-Flow FM / SMT files with the same structure and metadata as real output (r209)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import netCDF4
import numpy as np

from fields import SubRunTimes
from mesh import Mesh, Subset
from params import GENERATOR_VERSION, REFERENCE_TIME, TIME_UNITS, Params

FILL_INT = np.int32(-999)
FILL_DBL = -999.0
SOURCE = f"D-Flow FM 1.2.184 (synthetic_smt {GENERATOR_VERSION}). Model:"

_FACE_ATTRS = {
    "mesh": "mesh2d",
    "location": "face",
    "coordinates": "mesh2d_face_x mesh2d_face_y",
    "cell_methods": "mesh2d_nFaces: mean",
    "cell_measures": "area: mesh2d_flowelem_ba",
    "grid_mapping": "projected_coordinate_system",
}

_DATA_VARS = {
    "mesh2d_mor_bl": {"long_name": "Time-varying bottom level in flow cell center", "units": "m"},
    "mesh2d_s1": {"standard_name": "sea_surface_height", "long_name": "Water level", "units": "m"},
    "mesh2d_waterdepth": {"standard_name": "sea_floor_depth_below_sea_surface", "long_name": "Water depth at pressure points", "units": "m"},
    "mesh2d_ucmag": {"standard_name": "sea_water_speed", "long_name": "Flow element center velocity magnitude", "units": "m s-1"},
    "mesh2d_taus": {"long_name": "Total bed shear stress magnitude", "units": "N m-2"},
}


def _var(ds, name, dtype, dims, attrs=None, fill=None, data=None, zlib=False):
    v = ds.createVariable(name, dtype, dims, fill_value=fill, zlib=zlib, complevel=4 if zlib else None)
    for k, val in (attrs or {}).items():
        v.setncattr(k, val)
    if data is not None:
        v[...] = data
    return v


def _write_grid(ds, mesh: Mesh, sub: Subset, n_partitions: int, with_domain: bool):
    nf, nn, ne = len(sub.faces), len(sub.nodes), len(sub.edges)
    ds.createDimension("Two", 2)
    ds.createDimension("mesh2d_nEdges", ne)
    ds.createDimension("mesh2d_nNodes", nn)
    ds.createDimension("mesh2d_nFaces", nf)
    ds.createDimension("mesh2d_nMax_face_nodes", 4)

    _var(ds, "mesh2d", "i4", (), {
        "cf_role": "mesh_topology", "long_name": "Topology data of 2D mesh", "topology_dimension": np.int32(2),
        "node_coordinates": "mesh2d_node_x mesh2d_node_y", "node_dimension": "mesh2d_nNodes",
        "max_face_nodes_dimension": "mesh2d_nMax_face_nodes", "edge_node_connectivity": "mesh2d_edge_nodes",
        "edge_dimension": "mesh2d_nEdges", "edge_coordinates": "mesh2d_edge_x mesh2d_edge_y",
        "face_node_connectivity": "mesh2d_face_nodes", "face_dimension": "mesh2d_nFaces",
        "edge_face_connectivity": "mesh2d_edge_faces", "face_coordinates": "mesh2d_face_x mesh2d_face_y",
    })
    # as in r209: the CRS is unknown in the model output (EPSG 0); the tool must take it from its configuration
    _var(ds, "projected_coordinate_system", "i4", (), {
        "name": "Unknown projected", "epsg": np.int32(0), "grid_mapping_name": "Unknown projected",
        "longitude_of_prime_meridian": 0.0, "semi_major_axis": 6378137.0, "semi_minor_axis": 6356752.314245,
        "inverse_flattening": 298.257223563, "EPSG_code": "EPSG:0", "value": "value is equal to EPSG code",
        "proj4_params": "", "proj_string": "",
    })
    xy = {"units": "m", "standard_name": "projection_x_coordinate"}
    yy = {"units": "m", "standard_name": "projection_y_coordinate"}
    _var(ds, "mesh2d_node_x", "f8", ("mesh2d_nNodes",), {**xy, "long_name": "x-coordinate of mesh nodes"}, data=mesh.node_x[sub.nodes])
    _var(ds, "mesh2d_node_y", "f8", ("mesh2d_nNodes",), {**yy, "long_name": "y-coordinate of mesh nodes"}, data=mesh.node_y[sub.nodes])

    en = mesh.edge_nodes[sub.edges]
    _var(ds, "mesh2d_edge_x", "f8", ("mesh2d_nEdges",), {**xy, "long_name": "characteristic x-coordinate of the mesh edge (e.g. midpoint)"},
         data=mesh.node_x[en].mean(axis=1))
    _var(ds, "mesh2d_edge_y", "f8", ("mesh2d_nEdges",), {**yy, "long_name": "characteristic y-coordinate of the mesh edge (e.g. midpoint)"},
         data=mesh.node_y[en].mean(axis=1))
    _var(ds, "mesh2d_edge_nodes", "i4", ("mesh2d_nEdges", "Two"),
         {"cf_role": "edge_node_connectivity", "long_name": "Start and end nodes of mesh edges", "start_index": np.int32(1)},
         data=sub.edge_nodes + 1)
    _var(ds, "mesh2d_edge_faces", "i4", ("mesh2d_nEdges", "Two"),
         {"cf_role": "edge_face_connectivity", "long_name": "Neighboring faces of mesh edges", "start_index": np.int32(1)},
         fill=FILL_INT, data=np.where(sub.edge_faces >= 0, sub.edge_faces + 1, FILL_INT))
    boundary = (sub.edge_faces < 0).any(axis=1)
    _var(ds, "mesh2d_edge_type", "i4", ("mesh2d_nEdges",),
         {"mesh": "mesh2d", "location": "edge", "coordinates": "mesh2d_edge_x mesh2d_edge_y",
          "long_name": "edge type (relation between edge and flow geometry)",
          "flag_values": np.array([0, 1, 2, 3], dtype=np.int32), "flag_meanings": "internal_closed internal boundary boundary_closed"},
         fill=FILL_INT, data=np.where(boundary, 3, 1).astype(np.int32))

    _var(ds, "mesh2d_face_nodes", "i4", ("mesh2d_nFaces", "mesh2d_nMax_face_nodes"),
         {"cf_role": "face_node_connectivity", "long_name": "Vertex nodes of mesh faces (counterclockwise)", "start_index": np.int32(1)},
         fill=FILL_INT, data=np.where(sub.face_nodes >= 0, sub.face_nodes + 1, FILL_INT))
    _var(ds, "mesh2d_face_x", "f8", ("mesh2d_nFaces",), {**xy, "long_name": "Characteristic x-coordinate of mesh face", "bounds": "mesh2d_face_x_bnd"},
         data=mesh.face_x[sub.faces])
    _var(ds, "mesh2d_face_y", "f8", ("mesh2d_nFaces",), {**yy, "long_name": "Characteristic y-coordinate of mesh face", "bounds": "mesh2d_face_y_bnd"},
         data=mesh.face_y[sub.faces])
    gfn = mesh.face_nodes[sub.faces]
    bx = np.where(gfn >= 0, mesh.node_x[np.clip(gfn, 0, None)], FILL_DBL)
    by = np.where(gfn >= 0, mesh.node_y[np.clip(gfn, 0, None)], FILL_DBL)
    _var(ds, "mesh2d_face_x_bnd", "f8", ("mesh2d_nFaces", "mesh2d_nMax_face_nodes"),
         {**xy, "long_name": "x-coordinate bounds of mesh faces (i.e. corner coordinates)"}, fill=FILL_DBL, data=bx)
    _var(ds, "mesh2d_face_y_bnd", "f8", ("mesh2d_nFaces", "mesh2d_nMax_face_nodes"),
         {**yy, "long_name": "y-coordinate bounds of mesh faces (i.e. corner coordinates)"}, fill=FILL_DBL, data=by)

    if with_domain:
        _var(ds, "mesh2d_flowelem_domain", "i4", ("mesh2d_nFaces",),
             {**_FACE_ATTRS, "standard_name": "cell_domain_number", "long_name": "domain number of flow element",
              "valid_max": np.int32(n_partitions)},
             fill=FILL_INT, data=mesh.face_partition[sub.faces].astype(np.int32))
        _var(ds, "mesh2d_flowelem_globalnr", "i4", ("mesh2d_nFaces",),
             {**_FACE_ATTRS, "standard_name": "cell_global_number", "long_name": "global flow element numbering",
              "valid_max": np.int32(mesh.n_faces)},
             fill=FILL_INT, data=(sub.faces + 1).astype(np.int32))
    _var(ds, "mesh2d_flowelem_ba", "f8", ("mesh2d_nFaces",),
         {**_FACE_ATTRS, "standard_name": "cell_area", "units": "m2"}, fill=FILL_DBL, data=mesh.face_area[sub.faces])


def _global_attrs(ds, run: SubRunTimes | None):
    created = datetime(2026, 7, 13, 9, 41, 37).strftime("%Y-%m-%dT%H:%M:%S+0200")
    attrs = {
        "institution": "Deltares", "references": "https://www.deltares.nl", "source": SOURCE,
        "history": f"Created on {created}, D-Flow FM (synthetic)", "date_created": created, "date_modified": created,
        "Conventions": "CF-1.8 UGRID-1.0 Deltares-0.10 ACDD-1.3",
    }
    if run is not None:
        t0 = REFERENCE_TIME + timedelta(seconds=float(run.time[0]))
        t1 = REFERENCE_TIME + timedelta(seconds=float(run.time[-1]))
        attrs["time_coverage_start"] = t0.isoformat()
        attrs["time_coverage_end"] = t1.isoformat()
    ds.setncatts(attrs)


def write_map(path: Path, p: Params, mesh: Mesh, sub: Subset, run: SubRunTimes, fields: dict[str, np.ndarray],
              initial_bl: np.ndarray, partitioned: bool, corrupt_times: tuple[int, ...] = ()):
    """`corrupt_times` reproduces r209 sub-run 0 partition 0021: garbage time/morft and +-1e308 values."""
    path.parent.mkdir(parents=True, exist_ok=True)
    time, morft = run.time.copy(), run.morft.copy()
    fields = {k: v[:, sub.faces].copy() for k, v in fields.items()}
    if corrupt_times:
        idx = list(corrupt_times)
        time[idx] = -5.423497149537879e-179
        morft[idx] = -5.182097385888104e225
        for k, v in fields.items():
            v[idx] = np.where(np.arange(v[idx].size).reshape(v[idx].shape) % 2 == 0, -1.2e308, 1.4e308)
    with netCDF4.Dataset(path, "w", format="NETCDF4") as ds:
        _global_attrs(ds, run)
        _write_grid(ds, mesh, sub, p.n_partitions, with_domain=partitioned)
        ds.createDimension("time", None)
        ds.createDimension("nSedTot", p.n_fractions)
        ds.createDimension("nBedLayers", p.n_bed_layers)
        _var(ds, "time", "f8", ("time",), {"standard_name": "time", "units": TIME_UNITS}, data=time)
        _var(ds, "morft", "f8", ("time",), {"long_name": "Current morphological time", "units": TIME_UNITS}, data=morft)
        _var(ds, "mesh2d_flowelem_bl", "f8", ("mesh2d_nFaces",),
             {**_FACE_ATTRS, "standard_name": "altitude", "long_name": "flow element center bedlevel (bl)", "units": "m"},
             fill=FILL_DBL, data=initial_bl[sub.faces])
        for name, attrs in _DATA_VARS.items():
            _var(ds, name, "f8", ("time", "mesh2d_nFaces"), {**_FACE_ATTRS, **attrs}, fill=FILL_DBL,
                 data=fields[name], zlib=True)
        _var(ds, "mesh2d_lyrfrac", "f8", ("time", "mesh2d_nFaces", "nBedLayers", "nSedTot"),
             {**_FACE_ATTRS, "long_name": "Volume fraction in a layer of the bed in flow cell center", "units": "-"},
             fill=FILL_DBL, data=fields["mesh2d_lyrfrac"], zlib=True)


def write_net(path: Path, p: Params, mesh: Mesh, sub: Subset, initial_bl: np.ndarray):
    with netCDF4.Dataset(path, "w", format="NETCDF4") as ds:
        _global_attrs(ds, None)
        _write_grid(ds, mesh, sub, p.n_partitions, with_domain=False)
        # node z: average of the initial bed level of the adjacent faces
        z = np.zeros(len(mesh.node_x))
        cnt = np.zeros(len(mesh.node_x))
        for k in range(mesh.face_nodes.shape[1]):
            nodes = mesh.face_nodes[:, k]
            ok = nodes >= 0
            np.add.at(z, nodes[ok], initial_bl[ok])
            np.add.at(cnt, nodes[ok], 1)
        _var(ds, "mesh2d_node_z", "f8", ("mesh2d_nNodes",),
             {"mesh": "mesh2d", "location": "node", "coordinates": "mesh2d_node_x mesh2d_node_y", "standard_name": "altitude",
              "long_name": "z-coordinate of mesh nodes", "units": "m", "grid_mapping": "projected_coordinate_system"},
             fill=FILL_DBL, data=(z / np.maximum(cnt, 1))[sub.nodes])


def _fmt(key: str, value, comment: str = "") -> str:
    line = f"{key:<36}= {value}"
    return f"{line:<70}# {comment}" if comment else line


def write_mdu(
    path: Path,
    p: Params,
    run: SubRunTimes,
    net_file: str,
    map_interval: tuple[float, float, float],
    *,
    output_dir: str | None = "results",
    sed_file: str = "../../sed/synthetic.sed",
):
    restart = "" if run.index == 0 else (REFERENCE_TIME + timedelta(seconds=run.tstart)).strftime("dflowfm_%Y%m%d_%H%M%S_rst.nc")
    lines = [
        f"# Generated by synthetic_smt {GENERATOR_VERSION}. Values are synthetic.",
        "[General]",
        _fmt("Program", "D-Flow FM"),
        _fmt("Version", "1.2.184"),
        _fmt("FileVersion", "1.09", "File format version (do not edit this)"),
        _fmt("AutoStart", "0"),
        _fmt("PathsRelativeToParent", "1"),
        "",
        "[geometry]",
        _fmt("NetFile", net_file, "Unstructured grid file *_net.nc"),
        _fmt("BedlevType", "1"),
        "",
        "[numerics]",
        _fmt("CFLMax", "0.7"),
        "",
        "[physics]",
        _fmt("UnifFrictCoef", "40.0"),
        _fmt("UnifFrictType", "0"),
        "",
        "[time]",
        _fmt("RefDate", p.mdu_refdate, "Reference date (yyyymmdd); differs from the map time units on purpose"),
        _fmt("Tzone", "1"),
        _fmt("DtUser", "60.0"),
        _fmt("DtMax", "30.0"),
        _fmt("Tunit", "S"),
        _fmt("TStart", f"{run.tstart:.1f}", "Start time w.r.t. RefDate (in TUnit)"),
        _fmt("TStop", f"{run.tstop:.1f}", "Stop  time w.r.t. RefDate (in TUnit)"),
        "",
        "[restart]",
        _fmt("RestartFile", restart, "Restart netcdf-file, either *_rst.nc or *_map.nc"),
        _fmt("RestartDateTime", ""),
        "",
        "[output]",
        _fmt("OutputDir", output_dir or ""),
        _fmt("MapFormat", "4", "Map file format, 4: NetCDF-UGRID"),
        _fmt("MapInterval", " ".join(f"{v:.1f}" for v in map_interval)),
        _fmt("RstInterval", "0.0"),
        _fmt("HisInterval", "0.0"),
        "",
        "[sediment]",
        _fmt("Sedimentmodelnr", "4"),
        _fmt("SedFile", sed_file, "Sediment characteristics file (*.sed)"),
        _fmt("MorFile", "mor.mor", "Morphology settings file (*.mor)"),
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_mor(path: Path, p: Params):
    path.write_text(
        "[MorphologyFileInformation]\n"
        "   FileCreatedBy    = synthetic_smt\n"
        "   FileVersion      = 02.00\n"
        "[Morphology]\n"
        f"   MorFac           = {p.morfac:g}            [-]      Morphological scale factor\n"
        f"   MorStt           = {p.spinup_s:.1f}            [Tunit]  Spin-up interval from TStart till start of morphological changes\n"
        "   BedUpd           = True                     Update bed levels during FLOW simulation\n"
        "[Output]\n"
        "[Numerics]\n"
        "[Underlayer]\n"
        f"   IUnderLyr        = 2\n"
        f"   NLyr             = {p.n_bed_layers}\n",
        encoding="utf-8",
    )


def write_sed(path: Path, p: Params):
    diameters = [0.0005, 0.002, 0.008][: p.n_fractions]
    blocks = [
        "[SedimentFileInformation]\n   FileCreatedBy    = synthetic_smt\n   FileVersion      = 02.00\n"
        "[SedimentOverall]\n   Cref             =  1.6000000e+003      [kg/m3]  CSS reference concentration\n"
    ]
    for k, d in enumerate(diameters, start=1):
        blocks.append(
            f"[Sediment]\n   Name             = #Sediment{k}#\n   SedTyp           = bedload\n"
            f"   RhoSol           =  2.6500000e+003      [kg/m3]\n   SedDia           =  {d:.4e}      [m]\n"
            "   CDryB            =  1.6000000e+003      [kg/m3]\n"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(blocks), encoding="utf-8")


def write_dia(path: Path, run: SubRunTimes, rank: int, n_partitions: int, finished: bool, mdu_name: str):
    """The 'Computation finished' block is placed about 10 lines before the end, as in r209. For partitioned runs,
    only the partition files (dflowfm_0000.dia, ...) contain it; the main dflowfm.dia does not."""
    lines = [
        "** INFO   : no partitioning polygons file: read subdomain numbering from netfiles",
        f"** INFO   : Reading '{mdu_name}' (synthetic_smt {GENERATOR_VERSION})",
        "** INFO   : Not using cache file.",
        "** INFO   : ",
        "** INFO   : nr of netcells               ( )  :                    synthetic",
        "** INFO   : ",
    ]
    if finished:
        period_h = (run.tstop - run.tstart) / 3600
        lines += [
            "** INFO   : ",
            "** INFO   : ",
            "** INFO   : Computation started  at: 09:41:37, 13-07-2026",
            "** INFO   : Computation finished at: 11:54:12, 13-07-2026",
            "** INFO   : ",
            f"** INFO   : simulation period      (h)  :            {period_h:.10f}",
            "** INFO   : total time in timeloop (h)  :             2.1836515692",
            f"** INFO   : MPI    : yes.         #processes   : {n_partitions}, my_rank: {rank}",
            "** INFO   : OpenMP : yes.         #threads max : 1",
            "** INFO   : ",
            "** INFO   : ",
            "** INFO   : ",
            "** INFO   : ",
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_dimr_config(
    path: Path,
    n_partitions: int | None,
    input_file: str = "dflowfm.mdu",
):
    process = " ".join(str(k) for k in range(n_partitions)) if n_partitions else "0"
    path.write_text(
        '<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n'
        '<dimrConfig xmlns="http://schemas.deltares.nl/dimr" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:schemaLocation="http://schemas.deltares.nl/dimr http://content.oss.deltares.nl/schemas/dimr-1.2.xsd">\n'
        "  <documentation>\n    <fileVersion>1.2</fileVersion>\n    <createdBy>synthetic_smt</createdBy>\n"
        "    <creationDate>2026-07-13T09:41:37</creationDate>\n  </documentation>\n"
        '  <control>\n    <start name="myNameDFlowFM" />\n  </control>\n'
        '  <component name="myNameDFlowFM">\n    <library>dflowfm</library>\n    <workingDir>.</workingDir>\n'
        f"    <inputFile>{input_file}</inputFile>\n"
        f"    <process>{process}</process>\n"
        "    <mpiCommunicator>DFM_COMM_DFMWORLD</mpiCommunicator>\n  </component>\n</dimrConfig>\n",
        encoding="utf-8",
    )


def write_smt_yml(path: Path, p: Params, n_subruns: int):
    path.write_text(
        "info:\n  created_by: synthetic_smt\n"
        f"  date: generated by synthetic_smt {GENERATOR_VERSION} ({n_subruns} sub-runs)\n  version: 1\n"
        "application:\n  name: D-FlowFM\n  command:\n    Windows:\n      - run_dimr_parallel.bat\n"
        "    Linux:\n      - ./run_dimr_parallel.sh\n"
        "  input: # These files are not renamed\n    - .mdu\n    - .xml\n    - .mor\n    - .sed\n"
        "model:\n  input: dflowfm\n  simulation_type: quasi-steady-hydrograph\n"
        "  DIMR_dflowfm_workdir: .\n"
        "variables:\n  user:\n    MapOutputCount: 1\n    TUnit: S\n"
        f"    ReferenceDate: '{p.mdu_refdate}'\n    SpinupTime:\n      - {p.spinup_s:.1f}\n",
        encoding="utf-8",
    )
