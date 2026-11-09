# SPDX-License-Identifier: GPL-3.0-or-later
"""Line sampling: quantities read at the faces crossed by a polyline (PRF, D55)."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any

import numpy as np

from d3dplot.variables.geometry import GridGeometry
from d3dplot.variables.sampling import SamplingPlan

xu: Any = import_module("xugrid")
_FILL_VALUE = -1


def _digest(*parts: bytes) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part)
    return digest.hexdigest()


def _array_bytes(array: np.ndarray) -> bytes:
    return str(array.shape).encode() + np.ascontiguousarray(array).tobytes()


@dataclass(frozen=True, eq=False)
class LineTopology:
    """Global node coordinates and face connectivity of one merged grid."""

    node_x: np.ndarray
    node_y: np.ndarray
    face_nodes: np.ndarray
    is_geographic: bool = False
    fingerprint: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        node_x = np.asarray(self.node_x, dtype=np.float64)
        node_y = np.asarray(self.node_y, dtype=np.float64)
        face_nodes = np.asarray(self.face_nodes, dtype=np.int64)
        if node_x.ndim != 1 or node_x.shape != node_y.shape:
            raise ValueError("Node coordinates must be one-dimensional and paired.")
        if not (np.all(np.isfinite(node_x)) and np.all(np.isfinite(node_y))):
            raise ValueError("Node coordinates must be finite.")
        if face_nodes.ndim != 2 or face_nodes.shape[0] == 0:
            raise ValueError("Face-node connectivity must be a non-empty 2D array.")
        valid = face_nodes >= 0
        if np.any(face_nodes[valid] >= node_x.size):
            raise ValueError("Face-node connectivity indexes outside the node arrays.")
        object.__setattr__(self, "node_x", node_x)
        object.__setattr__(self, "node_y", node_y)
        object.__setattr__(self, "face_nodes", face_nodes)
        object.__setattr__(
            self,
            "fingerprint",
            _digest(
                str(self.is_geographic).encode(),
                _array_bytes(node_x),
                _array_bytes(node_y),
                _array_bytes(face_nodes),
            ),
        )

    @property
    def n_faces(self) -> int:
        return int(self.face_nodes.shape[0])


@dataclass(frozen=True, eq=False)
class LineCrossings:
    """Faces crossed by a polyline, in order of distance along the polyline."""

    face_index: np.ndarray
    distance_start_m: np.ndarray
    distance_stop_m: np.ndarray
    direction_xy: np.ndarray

    @property
    def length_m(self) -> np.ndarray:
        """Length of each crossing segment inside its face."""
        return np.asarray(
            self.distance_stop_m - self.distance_start_m, dtype=np.float64
        )

    @property
    def distance_m(self) -> np.ndarray:
        """Distance along the polyline at the middle of each crossing."""
        return np.asarray(
            0.5 * (self.distance_start_m + self.distance_stop_m), dtype=np.float64
        )


def compute_line_crossings(
    topology: LineTopology,
    polyline_xy: Sequence[Sequence[float]] | np.ndarray,
) -> LineCrossings:
    """Intersect a polyline with the faces of a Cartesian grid, as dfm_tools does."""
    if topology.is_geographic:
        raise ValueError("Line sampling on geographic grids is not supported yet.")

    line = np.asarray(polyline_xy, dtype=np.float64)
    if line.ndim != 2 or line.shape[1] != 2 or line.shape[0] < 2:
        raise ValueError("A polyline needs at least two (x, y) points.")
    if not np.all(np.isfinite(line)):
        raise ValueError("Polyline coordinates must be finite.")

    edges = np.stack([line[:-1], line[1:]], axis=1)
    segment = edges[:, 1] - edges[:, 0]
    segment_length = np.hypot(segment[:, 0], segment[:, 1])
    if np.any(segment_length == 0.0):
        raise ValueError("Polyline segments must have non-zero length.")

    grid = xu.Ugrid2d(
        topology.node_x,
        topology.node_y,
        _FILL_VALUE,
        topology.face_nodes,
    )
    segment_index, face_index, intersections = grid.intersect_edges(edges=edges)
    if len(segment_index) == 0:
        raise ValueError("Polyline does not cross the grid.")

    segment_start = np.cumsum(segment_length) - segment_length
    entry = intersections[:, 0, :]
    exit_ = intersections[:, 1, :]
    along_entry = segment_start[segment_index] + np.hypot(
        *(entry - edges[segment_index, 0]).T
    )
    along_exit = segment_start[segment_index] + np.hypot(
        *(exit_ - edges[segment_index, 0]).T
    )
    order = np.argsort(np.minimum(along_entry, along_exit), kind="stable")
    return LineCrossings(
        face_index=np.asarray(face_index, dtype=np.int64)[order],
        distance_start_m=np.minimum(along_entry, along_exit)[order],
        distance_stop_m=np.maximum(along_entry, along_exit)[order],
        direction_xy=(segment[segment_index] / segment_length[segment_index, None])[
            order
        ],
    )


@dataclass(frozen=True, slots=True)
class LinePlan:
    """Prepared crossings for one polyline and topology."""

    fingerprint: str
    crossings: LineCrossings


@dataclass(frozen=True, eq=False)
class LineSampling:
    """Sampling that keeps the global faces crossed by a polyline."""

    polyline_xy: np.ndarray
    topology: LineTopology
    kind: str = "line"
    fingerprint_value: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        line = np.asarray(self.polyline_xy, dtype=np.float64).copy()
        line.setflags(write=False)
        object.__setattr__(self, "polyline_xy", line)
        object.__setattr__(
            self,
            "fingerprint_value",
            "line-v1-"
            + _digest(_array_bytes(line), self.topology.fingerprint.encode()),
        )

    def fingerprint(self) -> str:
        """Return the stable identity of this polyline and topology."""
        return self.fingerprint_value

    def prepare(
        self,
        grid: GridGeometry | None,
        merge_plan: object | None,
    ) -> LinePlan:
        """Intersect the polyline once with the faces of the inspected grid."""
        del merge_plan
        if grid is not None and grid.n_faces != self.topology.n_faces:
            raise ValueError(
                "Line sampling topology does not match the inspected grid "
                f"({self.topology.n_faces} faces versus {grid.n_faces})."
            )
        return LinePlan(
            fingerprint=self.fingerprint_value,
            crossings=compute_line_crossings(self.topology, self.polyline_xy),
        )

    def apply(self, values: Any, plan: SamplingPlan) -> Any:
        """Gather global face values at the crossed faces, in distance order."""
        if not isinstance(plan, LinePlan) or plan.fingerprint != self.fingerprint_value:
            raise ValueError("LineSampling received an incompatible sampling plan.")
        array = np.asarray(values)
        if array.shape[0] != self.topology.n_faces:
            raise ValueError(
                "Line sampling expects one value per global face "
                f"({self.topology.n_faces}), received {array.shape[0]}."
            )
        return np.take(array, plan.crossings.face_index, axis=0)


__all__ = [
    "LineCrossings",
    "LinePlan",
    "LineSampling",
    "LineTopology",
    "compute_line_crossings",
]
