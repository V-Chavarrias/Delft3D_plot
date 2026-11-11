# SPDX-License-Identifier: GPL-3.0-or-later
"""Delft3D 4 (D3D4) trim output, presented to the FM-based map pipeline.

A D3D4 run is one sub-run. Its map file is a small FM-format topology file in the
output cache, written once from the trim ``.def`` and ``.dat`` files. Data quantities
are read from the trim NEFIS file on request, one time step at a time.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

import netCDF4
import numpy as np
from numpy.typing import NDArray

from d3dplot import __version__
from d3dplot.model_input.mdu import MduInfo
from d3dplot.simulation.nefis import NefisFile
from d3dplot.store.atomic import atomic_path
from d3dplot.store.fingerprint import (
    fingerprint_artifact,
    fingerprint_model_output,
    fingerprint_small_input,
)
from d3dplot.store.guard import OutputWriteGuard
from d3dplot.store.layout import OutputLayout

_TRIM_PREFIX = "trim-"
_TOPOLOGY_SCHEMA_VERSION = 1
_TOPOLOGY_ALGORITHM_VERSION = "4"
_CONST_GROUP = "map-const"
_SERIES_GROUP = "map-series"
_SED_SERIES_GROUP = "map-sed-series"
_INFO_SERIES_GROUP = "map-info-series"
_INFSED_SERIES_GROUP = "map-infsed-serie"
_NODE_DIM = "mesh2d_nNodes"
_FACE_DIM = "mesh2d_nFaces"
_EDGE_DIM = "mesh2d_nEdges"
_VERTEX_DIM = "mesh2d_nMax_face_nodes"
_TIME_DIM = "time"
_EDGE_VERTEX_DIM = "mesh2d_nNodes_per_edge"
_VERTICES_PER_FACE = 4
_VERTICES_PER_EDGE = 2
_SECONDS_PER_DAY = 86400.0
_FRAME_CACHE_SIZE = 16
_MORPH_BED = "mesh2d_mor_bl"
_MESH_NAME = "mesh2d"
_PROJECTED_X = {"standard_name": "projection_x_coordinate"}
_PROJECTED_Y = {"standard_name": "projection_y_coordinate"}
_SPEED_X = "mesh2d_ucx"
_SPEED_Y = "mesh2d_ucy"
_WATER_LEVEL = "mesh2d_s1"
_WATER_DEPTH = "mesh2d_waterdepth"
_SPEED_MAGNITUDE = "mesh2d_ucmag"
_EDGE_VELOCITY = "mesh2d_u1"


@dataclass(frozen=True, slots=True)
class D3D4Run:
    """The three input files of one D3D4 trim run."""

    mdf_path: Path
    def_path: Path
    data_path: Path


def find_d3d4_runs(folder: Path) -> tuple[D3D4Run, ...]:
    """Return every ``trim-<name>.def`` run whose ``<name>.mdf`` and data file exist."""
    runs: list[D3D4Run] = []
    for def_path in sorted(folder.glob(f"{_TRIM_PREFIX}*.def")):
        name = def_path.stem[len(_TRIM_PREFIX) :]
        mdf_path = folder / f"{name}.mdf"
        data_path = folder / f"{_TRIM_PREFIX}{name}.dat"
        if mdf_path.is_file() and data_path.is_file():
            runs.append(
                D3D4Run(mdf_path=mdf_path, def_path=def_path, data_path=data_path)
            )
    return tuple(runs)


def is_morphodynamic(mdu: MduInfo) -> bool:
    """A D3D4 run is morphodynamic when both sediment and morphology files are set."""
    return mdu.mor_file is not None and mdu.sed_file is not None


def topology_path(layout: OutputLayout, run: D3D4Run, mdu: MduInfo) -> Path:
    """Return the cache path of the topology file, keyed by the trim inputs."""
    digest = fingerprint_artifact(
        kind="d3d4-topology",
        schema_version=_TOPOLOGY_SCHEMA_VERSION,
        tool_version=__version__,
        algorithm_version=_TOPOLOGY_ALGORITHM_VERSION,
        parameters={"morphodynamic": is_morphodynamic(mdu)},
        inputs=[
            fingerprint_model_output(run.def_path),
            fingerprint_model_output(run.data_path),
            fingerprint_small_input(run.mdf_path),
        ],
    )
    return layout.cache_inputs / f"d3d4-topology-{digest[:24]}.nc"


def write_topology(
    run: D3D4Run,
    mdu: MduInfo,
    path: Path,
    guard: OutputWriteGuard,
) -> None:
    """Write the FM-format mesh, bed and time axis of a D3D4 run to ``path``."""
    if mdu.tunit.strip().upper() != "S":
        raise ValueError(f"D3D4 time unit {mdu.tunit!r} is not supported; use seconds")
    morphodynamic = is_morphodynamic(mdu)
    with NefisFile(run.data_path, run.def_path) as nefis:
        trim = _Trim(nefis, morphodynamic=morphodynamic)
        mesh = _build_mesh(trim)
        times = trim.itmapc() * trim.timestep()
        morft = trim.morph_seconds() if morphodynamic else None
        bed_series = (
            np.stack([trim.morph_bed(t) for t in range(trim.time_count)]).astype(
                np.float32
            )
            if morphodynamic
            else None
        )
    with atomic_path(path, guard) as temporary:
        with netCDF4.Dataset(str(temporary), mode="w") as dataset:
            _write_topology_dataset(
                dataset, mesh, times, _time_units(mdu), morft, bed_series
            )


class D3D4MapDataset:
    """Map-output view of a D3D4 run with the same variable names as FM map files."""

    def __init__(
        self,
        topology: Path,
        run: D3D4Run,
        *,
        morphodynamic: bool,
    ) -> None:
        self._topology = netCDF4.Dataset(str(topology), mode="r")
        self._nefis: NefisFile | None = None
        try:
            self._nefis = NefisFile(run.data_path, run.def_path)
            trim = _Trim(self._nefis, morphodynamic=morphodynamic)
            frames = _FrameCache(trim)
            self.variables: dict[str, Any] = dict(self._topology.variables)
            for name in (
                _WATER_LEVEL,
                _WATER_DEPTH,
                _SPEED_X,
                _SPEED_Y,
                _SPEED_MAGNITUDE,
            ):
                self.variables[name] = _FrameVariable(
                    name,
                    frames,
                    time_count=trim.time_count,
                    item_count=trim.face_count,
                )
            self.variables[_EDGE_VELOCITY] = _EdgeNormalVariable(
                frames,
                time_count=trim.time_count,
                topology=self._topology,
            )
        except Exception:
            self.close()
            raise

    @property
    def dimensions(self) -> Any:
        return self._topology.dimensions

    def getncattr(self, name: str) -> Any:
        return self._topology.getncattr(name)

    def close(self) -> None:
        self._topology.close()
        if self._nefis is not None:
            self._nefis.close()

    def __enter__(self) -> D3D4MapDataset:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class _Trim:
    """Grid-shaped reads of the trim NEFIS groups, with the active-face selection."""

    def __init__(self, nefis: NefisFile, *, morphodynamic: bool) -> None:
        self._nefis = nefis
        self.morphodynamic = morphodynamic
        self.shape: tuple[int, int] = _grid2d(
            nefis.read(_CONST_GROUP, "XCOR", index=1), name="XCOR"
        ).shape
        self.time_count = int(nefis.group_shape(_INFO_SERIES_GROUP)[0])
        for group in (_SERIES_GROUP, _SED_SERIES_GROUP, _INFSED_SERIES_GROUP):
            if morphodynamic or group == _SERIES_GROUP:
                count = int(nefis.group_shape(group)[0])
                if count != self.time_count:
                    raise ValueError(
                        f"NEFIS group {group!r} has {count} outputs; "
                        f"{_INFO_SERIES_GROUP!r} has {self.time_count}"
                    )
        kcs = self.constant("KCS")
        self.face_mask: NDArray[np.bool_] = kcs[1:-1, 1:-1] != 0
        self.face_count = int(np.count_nonzero(self.face_mask))
        self._alfas = self.constant("ALFAS")
        self._static_bed: NDArray[np.float64] | None = (
            None if morphodynamic else -self.centre(self.constant("DPS0"))
        )

    def constant(self, element: str) -> NDArray[Any]:
        return self._grid(_CONST_GROUP, element, index=1)

    def timestep(self) -> float:
        return self._scalar(_CONST_GROUP, "DT", index=1)

    def itmapc(self) -> NDArray[np.float64]:
        return np.array(
            [
                self._scalar(_INFO_SERIES_GROUP, "ITMAPC", index=t + 1)
                for t in range(self.time_count)
            ],
            dtype=np.float64,
        )

    def morph_seconds(self) -> NDArray[np.float64]:
        # MORFT in the trim output is elapsed morphological time in days.
        return (
            np.array(
                [
                    self._scalar(_INFSED_SERIES_GROUP, "MORFT", index=t + 1)
                    for t in range(self.time_count)
                ],
                dtype=np.float64,
            )
            * _SECONDS_PER_DAY
        )

    def morph_bed(self, time_index: int) -> NDArray[np.float64]:
        """Return the current bed (negative DPS) on active faces for one output time."""
        dps = self._grid(_SED_SERIES_GROUP, "DPS", index=time_index + 1)
        return -self.centre(dps)

    def frame(self, time_index: int) -> dict[str, NDArray[np.float64]]:
        """Return the face values of every quantity for one output time."""
        if not 0 <= time_index < self.time_count:
            raise IndexError(f"time index {time_index} is outside the trim output")
        index = time_index + 1
        s1 = self._grid(_SERIES_GROUP, "S1", index=index)
        ucx, ucy = self._velocity(index)
        water_level = self.centre(s1)
        bed = (
            self._static_bed
            if self._static_bed is not None
            else self.morph_bed(time_index)
        )
        return {
            _WATER_LEVEL: water_level,
            _WATER_DEPTH: water_level - bed,
            _SPEED_X: ucx,
            _SPEED_Y: ucy,
            _SPEED_MAGNITUDE: np.hypot(ucx, ucy),
        }

    def _velocity(self, index: int) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        # Centre velocities average the two staggered values that bound each cell,
        # as the Matlab tool does: U1 with its left and right edges, V1 with its
        # lower and upper edges. Each edge contributes only if its KFU/KFV is set.
        u_edge = self._grid(_SERIES_GROUP, "U1", index=index).astype(np.float64)
        v_edge = self._grid(_SERIES_GROUP, "V1", index=index).astype(np.float64)
        kfu = self._grid(_SERIES_GROUP, "KFU", index=index).astype(np.float64)
        kfv = self._grid(_SERIES_GROUP, "KFV", index=index).astype(np.float64)
        active_u = (kfu > 0).astype(np.float64)
        active_v = (kfv > 0).astype(np.float64)
        u = (u_edge * kfu)[1:-1, :-2] + (u_edge * kfu)[1:-1, 1:-1]
        u /= np.maximum(1.0, active_u[1:-1, :-2] + active_u[1:-1, 1:-1])
        v = (v_edge * kfv)[:-2, 1:-1] + (v_edge * kfv)[1:-1, 1:-1]
        v /= np.maximum(1.0, active_v[:-2, 1:-1] + active_v[1:-1, 1:-1])
        u_face = u[self.face_mask]
        v_face = v[self.face_mask]
        angle = np.deg2rad(self.centre(self._alfas))
        cos_angle = np.cos(angle)
        sin_angle = np.sin(angle)
        return (
            u_face * cos_angle - v_face * sin_angle,
            u_face * sin_angle + v_face * cos_angle,
        )

    def centre(self, field: NDArray[Any]) -> NDArray[np.float64]:
        return np.asarray(field[1:-1, 1:-1][self.face_mask], dtype=np.float64)

    def _grid(
        self,
        group: str,
        element: str,
        *,
        index: int | None = None,
    ) -> NDArray[Any]:
        values = _grid2d(self._nefis.read(group, element, index=index), name=element)
        if values.shape != self.shape:
            raise ValueError(
                f"NEFIS element {element!r} has shape {values.shape}; "
                f"expected {self.shape}"
            )
        return values

    def _scalar(self, group: str, element: str, *, index: int | None = None) -> float:
        return float(self._nefis.read(group, element, index=index).reshape(-1)[0])


@dataclass(frozen=True, slots=True)
class _Mesh:
    node_x: NDArray[np.float64]
    node_y: NDArray[np.float64]
    face_nodes: NDArray[np.int32]
    edge_nodes: NDArray[np.int32]
    face_x: NDArray[np.float64]
    face_y: NDArray[np.float64]
    face_x_bnd: NDArray[np.float64]
    face_y_bnd: NDArray[np.float64]
    area: NDArray[np.float64]
    bed_level: NDArray[np.float64]


def _build_mesh(trim: _Trim) -> _Mesh:
    # Corner nodes are XCOR/YCOR[0:N-1, 0:M-1]; the active cell (i, j) with 0-based
    # indices 1..N-2 and 1..M-2 is the quadrilateral of nodes (i-1, j-1), (i, j-1),
    # (i, j), (i-1, j), counter-clockwise.
    xcor = trim.constant("XCOR").astype(np.float64)[:-1, :-1]
    ycor = trim.constant("YCOR").astype(np.float64)[:-1, :-1]
    node_x = xcor.ravel(order="F")
    node_y = ycor.ravel(order="F")
    node_index = np.arange(node_x.size, dtype=np.int32).reshape(xcor.shape, order="F")
    corners = np.stack(
        (
            node_index[:-1, :-1],
            node_index[1:, :-1],
            node_index[1:, 1:],
            node_index[:-1, 1:],
        ),
        axis=-1,
    )
    face_nodes = corners[trim.face_mask]
    face_x_bnd = node_x[face_nodes]
    face_y_bnd = node_y[face_nodes]
    cross = (
        face_x_bnd * np.roll(face_y_bnd, -1, axis=1)
        - np.roll(face_x_bnd, -1, axis=1) * face_y_bnd
    )
    area = 0.5 * np.abs(cross.sum(axis=1))
    # Each quad contributes its four sides; a side shared by two faces is listed once.
    sides = np.concatenate(
        (
            face_nodes[:, [0, 1]],
            face_nodes[:, [1, 2]],
            face_nodes[:, [2, 3]],
            face_nodes[:, [3, 0]],
        )
    )
    edge_nodes = np.unique(np.sort(sides, axis=1), axis=0).astype(np.int32)
    return _Mesh(
        node_x=node_x,
        node_y=node_y,
        face_nodes=face_nodes,
        edge_nodes=edge_nodes,
        face_x=trim.centre(trim.constant("XZ")),
        face_y=trim.centre(trim.constant("YZ")),
        face_x_bnd=face_x_bnd,
        face_y_bnd=face_y_bnd,
        area=area,
        bed_level=-trim.centre(trim.constant("DPS0")),
    )


def _write_topology_dataset(
    dataset: Any,
    mesh: _Mesh,
    times: NDArray[np.float64],
    time_units: str,
    morft: NDArray[np.float64] | None,
    bed_series: NDArray[np.float32] | None,
) -> None:
    dataset.createDimension(_NODE_DIM, mesh.node_x.size)
    dataset.createDimension(_FACE_DIM, mesh.face_x.size)
    dataset.createDimension(_EDGE_DIM, mesh.edge_nodes.shape[0])
    dataset.createDimension(_VERTEX_DIM, _VERTICES_PER_FACE)
    dataset.createDimension(_EDGE_VERTEX_DIM, _VERTICES_PER_EDGE)
    dataset.createDimension(_TIME_DIM, times.size)
    _write_variable(
        dataset,
        "mesh2d_node_x",
        (_NODE_DIM,),
        mesh.node_x,
        units="m",
        attributes=_PROJECTED_X,
    )
    _write_variable(
        dataset,
        "mesh2d_node_y",
        (_NODE_DIM,),
        mesh.node_y,
        units="m",
        attributes=_PROJECTED_Y,
    )
    _write_variable(
        dataset,
        "mesh2d_face_nodes",
        (_FACE_DIM, _VERTEX_DIM),
        mesh.face_nodes,
        start_index=0,
    )
    _write_variable(
        dataset,
        "mesh2d_edge_nodes",
        (_EDGE_DIM, _EDGE_VERTEX_DIM),
        mesh.edge_nodes,
        start_index=0,
    )
    _write_mesh_topology(dataset)
    _write_variable(
        dataset,
        "mesh2d_face_x",
        (_FACE_DIM,),
        mesh.face_x,
        units="m",
        attributes={**_PROJECTED_X, "bounds": "mesh2d_face_x_bnd"},
    )
    _write_variable(
        dataset,
        "mesh2d_face_y",
        (_FACE_DIM,),
        mesh.face_y,
        units="m",
        attributes={**_PROJECTED_Y, "bounds": "mesh2d_face_y_bnd"},
    )
    _write_variable(
        dataset,
        "mesh2d_face_x_bnd",
        (_FACE_DIM, _VERTEX_DIM),
        mesh.face_x_bnd,
        units="m",
    )
    _write_variable(
        dataset,
        "mesh2d_face_y_bnd",
        (_FACE_DIM, _VERTEX_DIM),
        mesh.face_y_bnd,
        units="m",
    )
    _write_variable(
        dataset,
        "mesh2d_flowelem_ba",
        (_FACE_DIM,),
        mesh.area,
        units="m2",
    )
    _write_variable(
        dataset,
        "mesh2d_flowelem_bl",
        (_FACE_DIM,),
        mesh.bed_level,
        units="m",
    )
    _write_variable(dataset, _TIME_DIM, (_TIME_DIM,), times, units=time_units)
    if morft is not None:
        _write_variable(dataset, "morft", (_TIME_DIM,), morft, units="s")
    if bed_series is not None:
        _write_variable(
            dataset,
            _MORPH_BED,
            (_TIME_DIM, _FACE_DIM),
            bed_series,
            units="m",
        )


def _write_variable(
    dataset: Any,
    name: str,
    dimensions: tuple[str, ...],
    values: NDArray[Any],
    *,
    units: str | None = None,
    start_index: int | None = None,
    attributes: Mapping[str, str] | None = None,
) -> None:
    variable = dataset.createVariable(name, values.dtype, dimensions)
    if units is not None:
        variable.units = units
    if start_index is not None:
        variable.start_index = np.int32(start_index)
    for key, value in (attributes or {}).items():
        setattr(variable, key, value)
    variable[...] = values


def _write_mesh_topology(dataset: Any) -> None:
    # Carries the CF mesh_topology attributes that FM output uses, so that readers
    # such as xugrid build the same grid from the topology file.
    topology = dataset.createVariable(_MESH_NAME, np.int32, ())
    topology.cf_role = "mesh_topology"
    topology.long_name = "Topology data of 2D mesh"
    topology.topology_dimension = np.int32(2)
    topology.node_coordinates = "mesh2d_node_x mesh2d_node_y"
    topology.node_dimension = _NODE_DIM
    topology.max_face_nodes_dimension = _VERTEX_DIM
    topology.edge_node_connectivity = "mesh2d_edge_nodes"
    topology.edge_dimension = _EDGE_DIM
    topology.face_node_connectivity = "mesh2d_face_nodes"
    topology.face_dimension = _FACE_DIM
    topology.face_coordinates = "mesh2d_face_x mesh2d_face_y"


def _time_units(mdu: MduInfo) -> str:
    refdate = mdu.refdate
    year, month, day = refdate // 10000, refdate // 100 % 100, refdate % 100
    minutes = round(abs(mdu.tzone) * 60)
    sign = "+" if mdu.tzone >= 0 else "-"
    return (
        f"seconds since {year:04d}-{month:02d}-{day:02d} 00:00:00 "
        f"{sign}{minutes // 60:02d}:{minutes % 60:02d}"
    )


class _FrameCache:
    """Keep the quantities of the most recent output times, so that a time read once for
    one variable is not read again for the next variable.
    """

    def __init__(self, trim: _Trim) -> None:
        self._trim = trim
        self._frames: dict[int, dict[str, NDArray[np.float64]]] = {}

    def get(self, time_index: int, name: str) -> NDArray[np.float64]:
        frame = self._frames.get(time_index)
        if frame is None:
            frame = self._trim.frame(time_index)
            self._frames[time_index] = frame
            if len(self._frames) > _FRAME_CACHE_SIZE:
                del self._frames[next(iter(self._frames))]
        return frame[name]


class _FrameVariable:
    """A time-by-item variable computed from the trim output on request."""

    def __init__(
        self,
        name: str,
        frames: _FrameCache,
        *,
        time_count: int,
        item_count: int,
        dimension: str = _FACE_DIM,
    ) -> None:
        self._name = name
        self._frames = frames
        self.dimensions: tuple[str, ...] = (_TIME_DIM, dimension)
        self.shape: tuple[int, int] = (time_count, item_count)
        self.dtype = np.dtype(np.float64)

    def ncattrs(self) -> list[str]:
        return []

    def getncattr(self, name: str) -> Any:
        raise AttributeError(name)

    def _values(self, time_index: int) -> NDArray[np.float64]:
        return self._frames.get(time_index, self._name)

    def __getitem__(self, key: Any) -> NDArray[np.float64]:
        time_key, item_key = _split_key(key)
        times = np.arange(self.shape[0])[time_key]
        single = np.ndim(times) == 0
        rows = [
            np.asarray(self._values(int(time)))[item_key]
            for time in np.atleast_1d(times)
        ]
        if rows:
            values = np.stack(rows)
        else:
            empty = np.zeros(self.shape[1], dtype=np.float64)[item_key]
            values = np.empty((0, *np.shape(empty)), dtype=np.float64)
        return values[0] if single else values


class _EdgeNormalVariable(_FrameVariable):
    """Normal velocity on each mesh edge, averaged from the cells on either side.

    The sign follows the FM convention: the normal is the right-hand normal of the
    edge direction from its first to its second node, so that a flow in +x through
    a vertical edge with dy > 0 is positive.
    """

    def __init__(
        self,
        frames: _FrameCache,
        *,
        time_count: int,
        topology: netCDF4.Dataset,
    ) -> None:
        geometry = _edge_geometry(topology)
        super().__init__(
            _EDGE_VELOCITY,
            frames,
            time_count=time_count,
            item_count=geometry.normal_x.size,
            dimension=_EDGE_DIM,
        )
        self._geometry = geometry

    def _values(self, time_index: int) -> NDArray[np.float64]:
        geometry = self._geometry
        speed_x = self._frames.get(time_index, _SPEED_X)
        speed_y = self._frames.get(time_index, _SPEED_Y)
        total_x = np.zeros(geometry.normal_x.size, dtype=np.float64)
        total_y = np.zeros(geometry.normal_x.size, dtype=np.float64)
        count = np.zeros(geometry.normal_x.size, dtype=np.float64)
        for face in (geometry.left_face, geometry.right_face):
            has_face = face >= 0
            safe_face = np.where(has_face, face, 0)
            total_x += np.where(has_face, speed_x[safe_face], 0.0)
            total_y += np.where(has_face, speed_y[safe_face], 0.0)
            count += has_face
        count = np.maximum(count, 1.0)
        return (total_x / count) * geometry.normal_x + (
            total_y / count
        ) * geometry.normal_y


@dataclass(frozen=True, slots=True)
class _EdgeGeometry:
    left_face: NDArray[np.int64]
    right_face: NDArray[np.int64]
    normal_x: NDArray[np.float64]
    normal_y: NDArray[np.float64]


def _edge_geometry(topology: netCDF4.Dataset) -> _EdgeGeometry:
    """Return the faces on either side of each edge and the edge's unit normal."""
    face_nodes = np.asarray(topology.variables["mesh2d_face_nodes"][:], dtype=np.int64)
    edge_nodes = np.asarray(topology.variables["mesh2d_edge_nodes"][:], dtype=np.int64)
    node_x = np.asarray(topology.variables["mesh2d_node_x"][:], dtype=np.float64)
    node_y = np.asarray(topology.variables["mesh2d_node_y"][:], dtype=np.float64)
    node_count = np.int64(node_x.size)
    edge_count = edge_nodes.shape[0]

    edge_key = edge_nodes.min(axis=1) * node_count + edge_nodes.max(axis=1)
    edge_order = np.argsort(edge_key)
    sides = np.concatenate(
        [
            face_nodes[:, [k, (k + 1) % _VERTICES_PER_FACE]]
            for k in range(_VERTICES_PER_FACE)
        ]
    )
    side_face = np.tile(
        np.arange(face_nodes.shape[0], dtype=np.int64), _VERTICES_PER_FACE
    )
    side_key = sides.min(axis=1) * node_count + sides.max(axis=1)
    side_edge = edge_order[np.searchsorted(edge_key[edge_order], side_key)]
    if not np.array_equal(edge_key[side_edge], side_key):
        raise ValueError("A face side does not match any mesh edge in the topology.")

    by_edge = np.argsort(side_edge, kind="stable")
    sorted_edge = side_edge[by_edge]
    sorted_face = side_face[by_edge]
    position = np.arange(sorted_edge.size)
    first = np.ones(sorted_edge.size, dtype=bool)
    first[1:] = sorted_edge[1:] != sorted_edge[:-1]
    rank = position - np.maximum.accumulate(np.where(first, position, 0))
    if np.any(rank > 1):
        raise ValueError("A mesh edge is shared by more than two faces.")
    left_face = np.full(edge_count, -1, dtype=np.int64)
    right_face = np.full(edge_count, -1, dtype=np.int64)
    left_face[sorted_edge[rank == 0]] = sorted_face[rank == 0]
    right_face[sorted_edge[rank == 1]] = sorted_face[rank == 1]

    dx = node_x[edge_nodes[:, 1]] - node_x[edge_nodes[:, 0]]
    dy = node_y[edge_nodes[:, 1]] - node_y[edge_nodes[:, 0]]
    length = np.hypot(dx, dy)
    length = np.where(length > 0.0, length, 1.0)
    return _EdgeGeometry(
        left_face=left_face,
        right_face=right_face,
        normal_x=dy / length,
        normal_y=-dx / length,
    )


def _split_key(key: Any) -> tuple[Any, Any]:
    keys = key if isinstance(key, tuple) else (key,)
    if len(keys) > 2:
        raise IndexError("D3D4 variables have two dimensions: time and face")
    time_key = keys[0] if keys else slice(None)
    face_key = keys[1] if len(keys) > 1 else slice(None)
    return time_key, face_key


def _grid2d(values: NDArray[Any], *, name: str) -> NDArray[Any]:
    squeezed = values
    while squeezed.ndim > 2 and squeezed.shape[-1] == 1:
        squeezed = squeezed[..., 0]
    if squeezed.ndim != 2:
        raise ValueError(f"NEFIS element {name!r} is not a two-dimensional grid")
    return squeezed
