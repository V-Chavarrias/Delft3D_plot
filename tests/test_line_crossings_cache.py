# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for .pli input and the persisted line-crossing index cache (PRF)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

import d3dplot.variables.line_crossings_cache as crossings_cache
from d3dplot import __version__
from d3dplot.simulation.map_reader import MapReadResult
from d3dplot.simulation.time_axis import TimeEntry
from d3dplot.store import OutputLayout, OutputWriteGuard, initialize_output_root
from d3dplot.variables.context import QuantityContext, QuantityRequest
from d3dplot.variables.engine import QuantityEngine
from d3dplot.variables.line_crossings_cache import (
    LineInputError,
    line_crossings_cache_path,
    line_crossings_fingerprint,
    prepare_line_plan,
    read_line_crossings_cache,
    read_pli_polylines,
    write_line_crossings_cache,
)
from d3dplot.variables.line_sampling import LineSampling, LineTopology
from d3dplot.variables.registry import VariableSpec

_TIME = datetime(2021, 1, 1, tzinfo=UTC)
_PLI_TEXT = """section_a
   2   2
   0.25   0.5
   1.75   0.5
second
   3   2
   0.5   0.25
   0.5   1.75
   1.5   1.75
"""


def _square_topology(nx: int, ny: int) -> LineTopology:
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


@pytest.fixture
def guard(tmp_path: Path) -> OutputWriteGuard:
    return initialize_output_root(
        OutputLayout(tmp_path / "d3dplot"),
        tool_version=__version__,
        forbidden_roots=(),
    )


@pytest.fixture
def layout(guard: OutputWriteGuard) -> OutputLayout:
    return OutputLayout(guard.root)


@pytest.fixture
def pli_path(tmp_path: Path) -> Path:
    path = tmp_path / "profiles.pli"
    path.write_text(_PLI_TEXT, encoding="utf-8")
    return path


@pytest.mark.unit
def test_pli_polylines_are_read_with_names_and_points(pli_path: Path) -> None:
    polylines = read_pli_polylines(pli_path)

    assert [polyline.name for polyline in polylines] == ["section_a", "second"]
    np.testing.assert_allclose(polylines[0].xy, [[0.25, 0.5], [1.75, 0.5]])
    assert polylines[1].xy.shape == (3, 2)


@pytest.mark.unit
def test_pli_with_a_single_point_polyline_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.pli"
    path.write_text("bad\n   1   2\n   0.0   0.0\n", encoding="utf-8")

    with pytest.raises(LineInputError):
        read_pli_polylines(path)


@pytest.mark.unit
def test_missing_pli_is_reported_as_line_input_error(tmp_path: Path) -> None:
    with pytest.raises(LineInputError, match="Could not read polyline file"):
        read_pli_polylines(tmp_path / "missing.pli")


@pytest.mark.unit
def test_cache_is_built_once_and_reused_from_disk(
    monkeypatch: pytest.MonkeyPatch,
    layout: OutputLayout,
    guard: OutputWriteGuard,
) -> None:
    calls = 0
    original = crossings_cache.compute_line_crossings

    def counting(*args: object) -> object:
        nonlocal calls
        calls += 1
        return original(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(crossings_cache, "compute_line_crossings", counting)
    sampling = LineSampling([[0.25, 0.5], [1.75, 0.5]], _square_topology(2, 2))
    messages: list[str] = []

    first = prepare_line_plan(sampling, layout, guard, diagnostic=messages.append)
    second = prepare_line_plan(sampling, layout, guard, diagnostic=messages.append)

    assert calls == 1
    assert messages[0].startswith("Built line crossings cache")
    assert messages[1].startswith("Reused line crossings cache")
    assert second.fingerprint == first.fingerprint == sampling.fingerprint()
    np.testing.assert_array_equal(
        second.crossings.face_index, first.crossings.face_index
    )
    np.testing.assert_allclose(
        second.crossings.distance_start_m, first.crossings.distance_start_m
    )
    assert line_crossings_cache_path(
        layout, line_crossings_fingerprint(sampling)
    ).is_file()


@pytest.mark.unit
def test_cached_plan_is_shared_by_every_time_and_variable(
    monkeypatch: pytest.MonkeyPatch,
    layout: OutputLayout,
    guard: OutputWriteGuard,
) -> None:
    sampling = LineSampling([[0.25, 0.5], [1.75, 0.5]], _square_topology(2, 2))
    prepare_line_plan(sampling, layout, guard)

    def forbidden(*args: object) -> object:
        raise AssertionError("cached crossings must not be recomputed")

    monkeypatch.setattr(crossings_cache, "compute_line_crossings", forbidden)
    plan = prepare_line_plan(sampling, layout, guard)
    engine = QuantityEngine()
    engine.register_sampling_plan(sampling, plan)
    faces = np.array([0.0, 10.0, 20.0, 30.0])
    other = np.array([1.0, 2.0, 3.0, 4.0])
    for local_index in range(3):
        session = _MemoryMapSession(
            {"mesh2d_s1": faces * (local_index + 1), "mesh2d_ucx": other}
        )
        quantities = engine.at_time(session, _entry(local_index), QuantityContext())
        sampled = quantities.get(
            QuantityRequest(requested_name="mesh2d_s1", sampling=sampling)
        )
        velocity = quantities.get(
            QuantityRequest(requested_name="mesh2d_ucx", sampling=sampling)
        )
        np.testing.assert_allclose(sampled, faces[[0, 1]] * (local_index + 1))
        np.testing.assert_allclose(velocity, other[[0, 1]])


@pytest.mark.unit
def test_changed_polyline_gets_a_new_cache_file(
    layout: OutputLayout,
    guard: OutputWriteGuard,
) -> None:
    topology = _square_topology(2, 2)
    first = LineSampling([[0.25, 0.5], [1.75, 0.5]], topology)
    second = LineSampling([[0.25, 0.5], [1.5, 0.5]], topology)

    prepare_line_plan(first, layout, guard)
    prepare_line_plan(second, layout, guard)

    assert line_crossings_cache_path(
        layout, line_crossings_fingerprint(first)
    ) != line_crossings_cache_path(layout, line_crossings_fingerprint(second))


@pytest.mark.unit
def test_cache_with_another_fingerprint_is_rejected(
    layout: OutputLayout,
    guard: OutputWriteGuard,
) -> None:
    sampling = LineSampling([[0.25, 0.5], [1.75, 0.5]], _square_topology(2, 2))
    plan = prepare_line_plan(sampling, layout, guard)
    path = line_crossings_cache_path(layout, line_crossings_fingerprint(sampling))

    with pytest.raises(LineInputError, match="fingerprint changed"):
        read_line_crossings_cache(path, expected_fingerprint="line-crossings-other")
    assert plan.crossings.face_index.size == 2


@pytest.mark.unit
def test_corrupt_cache_file_is_rejected(
    layout: OutputLayout, guard: OutputWriteGuard
) -> None:
    sampling = LineSampling([[0.25, 0.5], [1.75, 0.5]], _square_topology(2, 2))
    fingerprint = line_crossings_fingerprint(sampling)
    path = line_crossings_cache_path(layout, fingerprint)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a netcdf file")

    with pytest.raises(LineInputError, match="Could not read line crossings cache"):
        read_line_crossings_cache(path, expected_fingerprint=fingerprint)


@pytest.mark.unit
def test_write_then_read_round_trips_crossings(
    layout: OutputLayout,
    guard: OutputWriteGuard,
) -> None:
    sampling = LineSampling([[0.25, 0.5], [1.75, 0.5]], _square_topology(2, 2))
    crossings = sampling.prepare(None, None).crossings
    fingerprint = line_crossings_fingerprint(sampling)
    path = layout.cache_geometry / "round_trip.nc"

    write_line_crossings_cache(path, guard, crossings, fingerprint=fingerprint)
    restored = read_line_crossings_cache(path, expected_fingerprint=fingerprint)

    np.testing.assert_array_equal(restored.face_index, crossings.face_index)
    np.testing.assert_allclose(restored.direction_xy, crossings.direction_xy)
    np.testing.assert_allclose(restored.distance_stop_m, crossings.distance_stop_m)
