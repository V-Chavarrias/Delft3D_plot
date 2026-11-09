# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for line sampling of quantities along a polyline (PRF)."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

import d3dplot.variables.line_sampling as line_sampling
from d3dplot.simulation.map_reader import MapReadResult
from d3dplot.simulation.time_axis import TimeEntry
from d3dplot.variables.context import QuantityContext, QuantityRequest
from d3dplot.variables.engine import QuantityEngine
from d3dplot.variables.line_sampling import (
    LineSampling,
    LineTopology,
    compute_line_crossings,
)
from d3dplot.variables.registry import VariableSpec

_TIME = datetime(2021, 1, 1, tzinfo=UTC)


def _square_topology(nx: int, ny: int) -> LineTopology:
    """Return a mesh of unit square faces with nx by ny faces."""
    xs, ys = np.meshgrid(np.arange(nx + 1, dtype=float), np.arange(ny + 1, dtype=float))
    faces = []
    for iy in range(ny):
        for ix in range(nx):
            n00 = iy * (nx + 1) + ix
            faces.append([n00, n00 + 1, n00 + 1 + (nx + 1), n00 + (nx + 1)])
    return LineTopology(
        node_x=xs.ravel(),
        node_y=ys.ravel(),
        face_nodes=np.array(faces, dtype=np.int64),
    )


class _MemoryMapSession:
    subrun_index = 0

    def __init__(self, values: dict[str, np.ndarray]) -> None:
        self._values = values

    def has_variable(self, spec: VariableSpec) -> bool:
        return any(candidate in self._values for candidate in spec.netcdf_candidates)

    def read(
        self,
        spec: VariableSpec,
        *,
        local_index: int,
        corrupt_partitions: tuple[int, ...] = (),
    ) -> MapReadResult:
        del local_index, corrupt_partitions
        candidate = next(
            candidate
            for candidate in spec.netcdf_candidates
            if candidate in self._values
        )
        return MapReadResult(self._values[candidate], None, ())


def _entry(local_index: int) -> TimeEntry:
    return TimeEntry(
        time_index=local_index,
        subrun=0,
        local_index=local_index,
        time=_TIME,
        morph_time=None,
        corrupt_partitions=(),
    )


@pytest.mark.unit
def test_crossings_of_a_straight_line_are_analytic() -> None:
    topology = _square_topology(nx=2, ny=2)

    crossings = compute_line_crossings(topology, [[0.25, 0.5], [1.75, 0.5]])

    np.testing.assert_array_equal(crossings.face_index, [0, 1])
    np.testing.assert_allclose(crossings.distance_start_m, [0.0, 0.75])
    np.testing.assert_allclose(crossings.length_m, [0.75, 0.75])
    np.testing.assert_allclose(crossings.distance_m, [0.375, 1.125])
    np.testing.assert_allclose(crossings.direction_xy, [[1.0, 0.0], [1.0, 0.0]])


@pytest.mark.unit
def test_crossed_lengths_sum_to_the_polyline_length() -> None:
    topology = _square_topology(nx=3, ny=3)
    line = np.array([[0.2, 0.3], [2.7, 1.6], [0.3, 2.8], [2.8, 0.2]])

    crossings = compute_line_crossings(topology, line)

    total = float(np.sum(np.hypot(*np.diff(line, axis=0).T)))
    assert np.sum(crossings.length_m) == pytest.approx(total)
    assert np.all(np.diff(crossings.distance_start_m) >= 0.0)
    assert crossings.distance_start_m[0] == pytest.approx(0.0)


@pytest.mark.unit
def test_polyline_outside_the_grid_is_rejected() -> None:
    with pytest.raises(ValueError, match="does not cross the grid"):
        compute_line_crossings(_square_topology(2, 2), [[5.0, 5.0], [6.0, 6.0]])


@pytest.mark.unit
def test_topology_rejects_connectivity_outside_the_nodes() -> None:
    with pytest.raises(ValueError, match="outside the node arrays"):
        LineTopology(
            node_x=np.zeros(3),
            node_y=np.zeros(3),
            face_nodes=np.array([[0, 1, 2, 3]]),
        )


@pytest.mark.unit
def test_fingerprint_depends_on_polyline_and_topology() -> None:
    topology = _square_topology(2, 2)
    first = LineSampling([[0.25, 0.5], [1.75, 0.5]], topology)
    same = LineSampling(np.array([[0.25, 0.5], [1.75, 0.5]]), topology)
    other = LineSampling([[0.25, 0.5], [1.5, 0.5]], topology)

    assert first.fingerprint() == same.fingerprint()
    assert first.fingerprint() != other.fingerprint()
    assert first.fingerprint().startswith("line-v1-")


@pytest.mark.unit
def test_line_sampling_gathers_face_values_and_prepares_once_across_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    original = line_sampling.compute_line_crossings

    def counting(*args: object) -> object:
        nonlocal calls
        calls += 1
        return original(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(line_sampling, "compute_line_crossings", counting)
    sampling = LineSampling([[0.25, 0.5], [1.75, 0.5]], _square_topology(2, 2))
    engine = QuantityEngine()
    faces = np.array([0.0, 10.0, 20.0, 30.0])

    for local_index in range(3):
        session = _MemoryMapSession({"mesh2d_s1": faces * (local_index + 1)})
        quantities = engine.at_time(session, _entry(local_index), QuantityContext())
        sampled = quantities.get(
            QuantityRequest(requested_name="mesh2d_s1", sampling=sampling)
        )
        np.testing.assert_allclose(sampled, faces[[0, 1]] * (local_index + 1))

    assert calls == 1


@pytest.mark.unit
def test_apply_keeps_trailing_dimensions_such_as_layers() -> None:
    sampling = LineSampling([[0.25, 0.5], [1.75, 0.5]], _square_topology(2, 2))
    plan = sampling.prepare(None, None)
    layered = np.arange(8.0).reshape(4, 2)

    np.testing.assert_allclose(sampling.apply(layered, plan), layered[[0, 1]])


@pytest.mark.unit
def test_apply_rejects_values_that_do_not_match_the_topology() -> None:
    sampling = LineSampling([[0.25, 0.5], [1.75, 0.5]], _square_topology(2, 2))
    plan = sampling.prepare(None, None)

    with pytest.raises(ValueError, match="one value per global face"):
        sampling.apply(np.zeros(3), plan)
