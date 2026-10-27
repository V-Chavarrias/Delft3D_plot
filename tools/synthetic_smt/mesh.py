# SPDX-License-Identifier: GPL-3.0-or-later
"""Centreline, curvilinear UGRID mesh and partitioning (with ghost cells) of the synthetic river."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from params import Params


@dataclass
class Centreline:
    s: np.ndarray
    x: np.ndarray
    y: np.ndarray
    theta: np.ndarray

    def at(self, s_query):
        s_query = np.asarray(s_query, dtype=float)
        x = np.interp(s_query, self.s, self.x)
        y = np.interp(s_query, self.s, self.y)
        th = np.interp(s_query, self.s, self.theta)
        return x, y, th

    def xy(self, s_query, n_query):
        """Map along/across coordinates (s, n) to x, y. n > 0 is to the left of the flow direction."""
        x, y, th = self.at(s_query)
        n_query = np.asarray(n_query, dtype=float)
        return x - n_query * np.sin(th), y + n_query * np.cos(th)

    def sn(self, x, y):
        """Approximate inverse mapping: nearest centreline point, then projection on tangent/normal."""
        tree = cKDTree(np.column_stack([self.x, self.y]))
        _, idx = tree.query(np.column_stack([np.ravel(x), np.ravel(y)]))
        dx = np.ravel(x) - self.x[idx]
        dy = np.ravel(y) - self.y[idx]
        th = self.theta[idx]
        s = self.s[idx] + dx * np.cos(th) + dy * np.sin(th)
        n = -dx * np.sin(th) + dy * np.cos(th)
        return s.reshape(np.shape(x)), n.reshape(np.shape(x))


def make_centreline(p: Params, step: float = 0.5) -> Centreline:
    s = np.arange(0.0, p.length + step / 2, step)
    theta = p.theta0 + p.meander_amplitude_rad * np.sin(2 * np.pi * s / p.meander_wavelength)
    cx, cy = np.cos(theta), np.sin(theta)
    x = p.x0 + np.concatenate([[0.0], np.cumsum(0.5 * (cx[1:] + cx[:-1]) * step)])
    y = p.y0 + np.concatenate([[0.0], np.cumsum(0.5 * (cy[1:] + cy[:-1]) * step)])
    return Centreline(s=s, x=x, y=y, theta=theta)


@dataclass
class Mesh:
    node_x: np.ndarray
    node_y: np.ndarray
    face_nodes: np.ndarray  # (F, 4) 0-based, -1 = fill
    face_x: np.ndarray  # centroid
    face_y: np.ndarray
    face_area: np.ndarray
    face_s: np.ndarray  # parametric centre: fields are evaluated here
    face_n: np.ndarray
    face_i: np.ndarray  # along-channel cell index
    face_partition: np.ndarray
    edge_nodes: np.ndarray  # (E, 2) 0-based
    edge_faces: np.ndarray  # (E, 2) 0-based, -1 = none

    @property
    def n_faces(self) -> int:
        return len(self.face_x)


def _polygon_area_centroid(px: np.ndarray, py: np.ndarray):
    """Shoelace area and centroid of polygons given as (F, k) arrays with NaN padding."""
    area = np.zeros(px.shape[0])
    cx = np.zeros(px.shape[0])
    cy = np.zeros(px.shape[0])
    for f in range(px.shape[0]):
        ok = ~np.isnan(px[f])
        x_absolute, y_absolute = px[f, ok], py[f, ok]
        x_origin, y_origin = x_absolute[0], y_absolute[0]
        x, y = x_absolute - x_origin, y_absolute - y_origin
        x1, y1 = np.roll(x, -1), np.roll(y, -1)
        cross = x * y1 - x1 * y
        a = cross.sum() / 2
        area[f] = a
        cx[f] = x_origin + ((x + x1) * cross).sum() / (6 * a)
        cy[f] = y_origin + ((y + y1) * cross).sum() / (6 * a)
    return area, cx, cy


def make_mesh(p: Params, cl: Centreline) -> Mesh:
    n_nodes = np.array(p.n_nodes_across())
    ni = round(p.length / p.ds)  # cells along
    nj = len(n_nodes) - 1  # cells across
    s_nodes = np.arange(ni + 1) * p.ds

    ss, nn = np.meshgrid(s_nodes, n_nodes, indexing="ij")  # (ni+1, nj+1)
    nx, ny = cl.xy(ss.ravel(), nn.ravel())
    node_id = np.arange((ni + 1) * (nj + 1)).reshape(ni + 1, nj + 1)

    faces, f_s, f_n, f_i = [], [], [], []
    tri_lo, tri_hi = p.triangle_i
    for i in range(ni):
        for j in range(nj):
            a, b, c, d = node_id[i, j], node_id[i + 1, j], node_id[i + 1, j + 1], node_id[i, j + 1]
            s_c = (i + 0.5) * p.ds
            if j == 0 and tri_lo <= i < tri_hi:
                # split the outermost right-floodplain quad into two triangles (counterclockwise);
                # parametric centres are the triangle centroids in (s, n)
                s0, s1, n0, n1 = s_nodes[i], s_nodes[i + 1], n_nodes[j], n_nodes[j + 1]
                for tri, ts, tn in (
                    ((a, b, c), (s0 + 2 * s1) / 3, (2 * n0 + n1) / 3),
                    ((a, c, d), (2 * s0 + s1) / 3, (n0 + 2 * n1) / 3),
                ):
                    faces.append([*tri, -1])
                    f_s.append(ts)
                    f_n.append(tn)
                    f_i.append(i)
            else:
                faces.append([a, b, c, d])
                f_s.append(s_c)
                f_n.append(0.5 * (n_nodes[j] + n_nodes[j + 1]))
                f_i.append(i)

    face_nodes = np.array(faces, dtype=np.int64)
    px = np.where(face_nodes >= 0, nx[np.clip(face_nodes, 0, None)], np.nan)
    py = np.where(face_nodes >= 0, ny[np.clip(face_nodes, 0, None)], np.nan)
    area, fx, fy = _polygon_area_centroid(px, py)
    if np.any(area <= 0):
        raise RuntimeError("Mesh has faces with non-positive area (orientation or folding problem).")

    face_s = np.array(f_s)
    face_n = np.array(f_n)
    face_i = np.array(f_i)

    # edges and edge-face connectivity
    edge_index: dict[tuple[int, int], int] = {}
    edge_nodes, edge_faces = [], []
    for f, nodes in enumerate(face_nodes):
        nodes = nodes[nodes >= 0]
        for k in range(len(nodes)):
            key = tuple(sorted((int(nodes[k]), int(nodes[(k + 1) % len(nodes)]))))
            e = edge_index.get(key)
            if e is None:
                edge_index[key] = len(edge_nodes)
                edge_nodes.append(key)
                edge_faces.append([f, -1])
            else:
                edge_faces[e][1] = f

    half = ni // 2
    partition = np.where(face_n > p.channel_half_width, 2, np.where(face_i < half, 0, 1))

    return Mesh(
        node_x=nx, node_y=ny, face_nodes=face_nodes, face_x=fx, face_y=fy, face_area=area,
        face_s=face_s, face_n=face_n, face_i=face_i, face_partition=partition,
        edge_nodes=np.array(edge_nodes, dtype=np.int64), edge_faces=np.array(edge_faces, dtype=np.int64),
    )


@dataclass
class Subset:
    """Part of the global mesh written to one partition file: owned faces + one layer of ghost faces."""

    partition: int
    faces: np.ndarray  # global face indices (sorted)
    nodes: np.ndarray  # global node indices (sorted)
    edges: np.ndarray  # global edge indices (sorted)
    face_nodes: np.ndarray  # local, 0-based, -1 fill
    edge_nodes: np.ndarray  # local, 0-based
    edge_faces: np.ndarray  # local, 0-based, -1 fill


def partition_subset(mesh: Mesh, partition: int | None) -> Subset:
    """`partition=None` returns the whole mesh (non-partitioned output)."""
    if partition is None:
        faces = np.arange(mesh.n_faces)
    else:
        owned = mesh.face_partition == partition
        neighbours = mesh.edge_faces[(mesh.edge_faces >= 0).all(axis=1)]
        a, b = neighbours[:, 0], neighbours[:, 1]
        ghost = np.zeros(mesh.n_faces, dtype=bool)
        ghost[b[owned[a] & ~owned[b]]] = True
        ghost[a[owned[b] & ~owned[a]]] = True
        faces = np.flatnonzero(owned | ghost)

    in_subset = np.zeros(mesh.n_faces, dtype=bool)
    in_subset[faces] = True
    edge_mask = (np.where(mesh.edge_faces >= 0, in_subset[np.clip(mesh.edge_faces, 0, None)], False)).any(axis=1)
    edges = np.flatnonzero(edge_mask)
    fn = mesh.face_nodes[faces]
    nodes = np.unique(np.concatenate([fn[fn >= 0], mesh.edge_nodes[edges].ravel()]))

    node_map = -np.ones(len(mesh.node_x), dtype=np.int64)
    node_map[nodes] = np.arange(len(nodes))
    face_map = -np.ones(mesh.n_faces, dtype=np.int64)
    face_map[faces] = np.arange(len(faces))

    local_fn = np.where(fn >= 0, node_map[np.clip(fn, 0, None)], -1)
    local_en = node_map[mesh.edge_nodes[edges]]
    ef = mesh.edge_faces[edges]
    local_ef = np.where(ef >= 0, face_map[np.clip(ef, 0, None)], -1)
    # keep the first neighbour filled (FM convention: boundary edges have the second entry empty)
    swap = (local_ef[:, 0] < 0) & (local_ef[:, 1] >= 0)
    local_ef[swap] = local_ef[swap][:, ::-1]

    return Subset(
        partition=-1 if partition is None else partition, faces=faces, nodes=nodes, edges=edges,
        face_nodes=local_fn, edge_nodes=local_en, edge_faces=local_ef,
    )
