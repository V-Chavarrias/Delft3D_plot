# SPDX-License-Identifier: GPL-3.0-or-later
"""Grid geometry required by face-edge quantity operators."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol, cast

_Array = Any
np: Any = import_module("numpy")


class _NetCDFVariable(Protocol):
    def __getitem__(self, key: object) -> object: ...

    def ncattrs(self) -> list[str]: ...

    def getncattr(self, name: str) -> object: ...


class _NetCDFDataset(Protocol):
    variables: Mapping[str, _NetCDFVariable]


class _NetCDF4Module(Protocol):
    def Dataset(
        self,
        path: Path,
        *,
        mode: str,
    ) -> AbstractContextManager[_NetCDFDataset]: ...


class GridGeometryError(RuntimeError):
    """Grid cache data cannot support a spatial quantity."""

    code = "V-OUT-020"


@dataclass(frozen=True)
class GridGeometry:
    """Global face-edge topology and geometry loaded from one inspected grid."""

    face_edges: _Array
    face_edge_sign: _Array
    edge_length: _Array
    face_area: _Array
    face_x: _Array
    face_y: _Array
    edge_faces: _Array = field(init=False)
    _streamfunction_solver: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        face_edges = np.asarray(self.face_edges, dtype=np.int64)
        face_edge_sign = np.asarray(self.face_edge_sign, dtype=np.int8)
        edge_length = np.asarray(self.edge_length, dtype=np.float64)
        face_area = np.asarray(self.face_area, dtype=np.float64)
        face_x = np.asarray(self.face_x, dtype=np.float64)
        face_y = np.asarray(self.face_y, dtype=np.float64)
        if face_edges.ndim != 2 or face_edge_sign.shape != face_edges.shape:
            raise GridGeometryError(
                "Face-edge connectivity and signs have invalid shapes."
            )
        if edge_length.ndim != 1 or face_area.ndim != 1:
            raise GridGeometryError(
                "Edge lengths and face areas must be one-dimensional."
            )
        n_faces = face_area.size
        n_edges = edge_length.size
        if (
            face_edges.shape[0] != n_faces
            or face_x.shape != (n_faces,)
            or face_y.shape != (n_faces,)
        ):
            raise GridGeometryError("Grid geometry arrays do not match the face count.")
        if np.any(~np.isfinite(edge_length)) or np.any(edge_length <= 0):
            raise GridGeometryError("Edge lengths must be finite and positive.")
        if np.any(~np.isfinite(face_area)) or np.any(face_area <= 0):
            raise GridGeometryError("Face areas must be finite and positive.")
        if np.any(~np.isfinite(face_x)) or np.any(~np.isfinite(face_y)):
            raise GridGeometryError("Face-centre coordinates must be finite.")

        valid_edges = face_edges >= 0
        if np.any(face_edges[valid_edges] >= n_edges):
            raise GridGeometryError(
                "Face-edge connectivity indexes outside the edge array."
            )
        if np.any(
            (face_edge_sign[valid_edges] != 1) & (face_edge_sign[valid_edges] != -1)
        ):
            raise GridGeometryError(
                "Every face edge must have an orientation sign of ±1."
            )

        edge_faces = np.full((n_edges, 2), -1, dtype=np.int64)
        for face_index, edges in enumerate(face_edges):
            signs = face_edge_sign[face_index]
            for edge_index, sign in zip(
                edges[edges >= 0], signs[edges >= 0], strict=True
            ):
                side = 0 if sign > 0 else 1
                if edge_faces[edge_index, side] != -1:
                    raise GridGeometryError(
                        f"Edge {edge_index} has multiple faces on the same side."
                    )
                edge_faces[edge_index, side] = face_index

        object.__setattr__(self, "face_edges", face_edges)
        object.__setattr__(self, "face_edge_sign", face_edge_sign)
        object.__setattr__(self, "edge_length", edge_length)
        object.__setattr__(self, "face_area", face_area)
        object.__setattr__(self, "face_x", face_x)
        object.__setattr__(self, "face_y", face_y)
        object.__setattr__(self, "edge_faces", edge_faces)

    @classmethod
    def from_path(cls, path: Path) -> GridGeometry:
        """Read the face-edge arrays persisted by the inspection phase."""
        from d3dplot.simulation.partitions import _NETCDF4_READ_LOCK

        try:
            with _NETCDF4_READ_LOCK:
                with _netcdf4_module().Dataset(path, mode="r") as dataset:
                    required = {
                        "face_edges",
                        "face_edge_sign",
                        "edge_length",
                        "mesh2d_flowelem_ba",
                        "mesh2d_face_x",
                        "mesh2d_face_y",
                    }
                    missing = required - set(dataset.variables)
                    if missing:
                        raise GridGeometryError(
                            "Grid cache is missing variable(s): "
                            f"{', '.join(sorted(missing))}."
                        )
                    face_edges = _read_connectivity(dataset.variables["face_edges"])
                    face_edge_sign = _read_integer_array(
                        dataset.variables["face_edge_sign"],
                        fill_value=0,
                    )
                    edge_length = _read_float_array(dataset.variables["edge_length"])
                    face_area = _read_float_array(
                        dataset.variables["mesh2d_flowelem_ba"]
                    )
                    face_x = _read_float_array(dataset.variables["mesh2d_face_x"])
                    face_y = _read_float_array(dataset.variables["mesh2d_face_y"])
        except GridGeometryError:
            raise
        except (
            EOFError,
            IndexError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as error:
            raise GridGeometryError(
                f"Could not load grid geometry from {path}: {error}"
            ) from error
        return cls(
            face_edges=face_edges,
            face_edge_sign=face_edge_sign,
            edge_length=edge_length,
            face_area=face_area,
            face_x=face_x,
            face_y=face_y,
        )

    @property
    def n_faces(self) -> int:
        return int(self.face_area.size)

    @property
    def n_edges(self) -> int:
        return int(self.edge_length.size)

    def vorticity(self, edge_velocity: _Array) -> _Array:
        """Compute CCW-positive face circulation per unit area."""
        velocity = np.asarray(edge_velocity, dtype=np.float64)
        if velocity.shape != (self.n_edges,):
            raise GridGeometryError(
                f"Expected {self.n_edges} edge velocities, got shape {velocity.shape}."
            )
        valid = self.face_edges >= 0
        safe_edges = np.where(valid, self.face_edges, 0)
        values = velocity[safe_edges] * self.edge_length[safe_edges]
        circulation = np.sum(np.where(valid, self.face_edge_sign * values, 0.0), axis=1)
        return circulation / self.face_area

    def solve_streamfunction(self, edge_flux: _Array) -> _Array:
        """Solve the Neumann face Laplacian with a zero gauge at face zero."""
        flux = np.asarray(edge_flux, dtype=np.float64)
        if flux.shape != (self.n_edges,):
            raise GridGeometryError(
                f"Expected {self.n_edges} edge fluxes, got shape {flux.shape}."
            )
        if self.n_faces == 0:
            raise GridGeometryError("Cannot solve a streamfunction on an empty grid.")

        left = self.edge_faces[:, 0]
        right = self.edge_faces[:, 1]
        interior = (left >= 0) & (right >= 0)
        right_faces = right[interior]
        left_faces = left[interior]
        rhs = np.zeros(self.n_faces, dtype=np.float64)
        np.add.at(rhs, left_faces, -flux[interior])
        np.add.at(rhs, right_faces, flux[interior])
        rhs[0] = 0.0
        return self._get_streamfunction_solver().solve(rhs)

    def _get_streamfunction_solver(self) -> Any:
        solver = self._streamfunction_solver
        if solver is not None:
            return solver

        left = self.edge_faces[:, 0]
        right = self.edge_faces[:, 1]
        interior = (left >= 0) & (right >= 0)
        left_faces = left[interior]
        right_faces = right[interior]
        distances = np.hypot(
            self.face_x[right_faces] - self.face_x[left_faces],
            self.face_y[right_faces] - self.face_y[left_faces],
        )
        if np.any(~np.isfinite(distances)) or np.any(distances <= 0):
            raise GridGeometryError(
                "Interior edge neighbours must have distinct finite face centres."
            )
        weights = self.edge_length[interior] / distances
        rows = np.concatenate((left_faces, left_faces, right_faces, right_faces))
        columns = np.concatenate((left_faces, right_faces, right_faces, left_faces))
        values = np.concatenate((weights, -weights, weights, -weights))
        sparse: Any = import_module("scipy.sparse")
        sparse_linalg: Any = import_module("scipy.sparse.linalg")
        matrix = sparse.coo_matrix(
            (values, (rows, columns)),
            shape=(self.n_faces, self.n_faces),
        ).tolil()
        matrix[0, :] = 0.0
        matrix[0, 0] = 1.0
        try:
            solver = sparse_linalg.splu(matrix.tocsc())
        except RuntimeError as error:
            raise GridGeometryError(
                f"Could not factorize the face Laplacian: {error}"
            ) from error
        object.__setattr__(self, "_streamfunction_solver", solver)
        return solver


def _read_connectivity(variable: _NetCDFVariable) -> _Array:
    raw = np.ma.asarray(variable[:])
    mask = np.asarray(np.ma.getmaskarray(raw), dtype=np.bool_)
    values = np.asarray(np.ma.getdata(raw), dtype=np.int64).copy()
    attributes = {name.casefold(): name for name in variable.ncattrs()}
    start_attribute = attributes.get("start_index")
    start_index = 0
    if start_attribute is not None:
        start_value = variable.getncattr(start_attribute)
        if not isinstance(start_value, (int, np.integer)):
            raise GridGeometryError("Grid connectivity start_index must be an integer.")
        start_index = int(start_value)
    if start_index:
        values[values >= start_index] -= start_index
    values[mask | (values < 0)] = -1
    return values


def _read_integer_array(variable: _NetCDFVariable, *, fill_value: int) -> _Array:
    raw = np.ma.asarray(variable[:])
    values = np.asarray(np.ma.getdata(raw), dtype=np.int64).copy()
    values[np.ma.getmaskarray(raw)] = fill_value
    return values


def _read_float_array(variable: _NetCDFVariable) -> _Array:
    raw = np.ma.asarray(variable[:])
    values = np.asarray(np.ma.getdata(raw), dtype=np.float64).copy()
    values[np.ma.getmaskarray(raw)] = np.nan
    return values


def _netcdf4_module() -> _NetCDF4Module:
    return cast(_NetCDF4Module, import_module("netCDF4"))
