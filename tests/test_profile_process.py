# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the PRF profile process core: selection, sampling and slice caches."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

import d3dplot.variables.line_crossings_cache as crossings_cache
from d3dplot import __version__
from d3dplot.analyses.profile.process import (
    ProfileProcessError,
    ProfileSelection,
    prepare_profile_line,
    profile_cache_fingerprint,
    profile_cache_path,
    profile_values_at_time,
    read_profile_cache,
    read_profile_values,
    select_polyline,
    write_profile_cache,
)
from d3dplot.simulation.map_reader import MapReadResult
from d3dplot.simulation.time_axis import TimeEntry
from d3dplot.store import OutputLayout, OutputWriteGuard, initialize_output_root
from d3dplot.variables.context import QuantityContext
from d3dplot.variables.engine import QuantityEngine
from d3dplot.variables.line_crossings_cache import Polyline
from d3dplot.variables.line_sampling import LineTopology
from d3dplot.variables.registry import VariableSpec

_TIMES = [datetime(2021, 1, 1, hour, tzinfo=UTC) for hour in (0, 1, 2)]
_LINE_XY = np.array([[0.25, 0.5], [1.75, 0.5]])


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
        corrupt_partitions: Sequence[int] = (),
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
        time=_TIMES[local_index],
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


@pytest.mark.unit
def test_selection_label_separates_layers_and_is_stable() -> None:
    all_layers = ProfileSelection(variable="mesh2d_ucx")
    top_layer = ProfileSelection(variable="mesh2d_ucx", layers=(2,))

    assert all_layers.label() == ProfileSelection(variable="mesh2d_ucx").label()
    assert all_layers.label() != top_layer.label()
    assert len(all_layers.label()) == 12


@pytest.mark.unit
def test_polyline_without_name_is_used_when_file_holds_one_polyline() -> None:
    polylines = [Polyline(name="only", xy=_LINE_XY)]

    selected = select_polyline(polylines, line_name="a", polyline_name=None)

    assert selected is polylines[0]


@pytest.mark.unit
def test_polyline_name_is_required_when_file_holds_several_polylines() -> None:
    polylines = [
        Polyline(name="first", xy=_LINE_XY),
        Polyline(name="second", xy=_LINE_XY),
    ]

    with pytest.raises(ProfileProcessError, match="needs a polyline name"):
        select_polyline(polylines, line_name="a", polyline_name=None)


@pytest.mark.unit
def test_polyline_name_match_is_case_insensitive_and_missing_name_fails() -> None:
    polylines = [
        Polyline(name="First", xy=_LINE_XY),
        Polyline(name="second", xy=_LINE_XY),
    ]

    assert (
        select_polyline(polylines, line_name="a", polyline_name="SECOND")
        is polylines[1]
    )
    with pytest.raises(ProfileProcessError, match="is not in the file"):
        select_polyline(polylines, line_name="a", polyline_name="third")


@pytest.mark.unit
def test_section_values_keep_the_layer_axis(
    layout: OutputLayout, guard: OutputWriteGuard
) -> None:
    topology = _square_topology(2, 2)
    line = prepare_profile_line("a", _LINE_XY, topology, layout, guard)
    layered = np.arange(12, dtype=float).reshape(4, 3)
    engine = QuantityEngine()
    session = _MemoryMapSession({"mesh2d_ucx": layered})

    values = read_profile_values(
        engine,
        session,
        _entry(0),
        line,
        ProfileSelection(variable="mesh2d_ucx"),
        QuantityContext(),
    )

    assert values.shape == (2, 3)
    np.testing.assert_allclose(values, layered[[0, 1]])


@pytest.mark.unit
def test_cache_round_trip_keeps_one_and_two_dimensional_values(
    layout: OutputLayout, guard: OutputWriteGuard
) -> None:
    line = prepare_profile_line("a", _LINE_XY, _square_topology(2, 2), layout, guard)
    selection = ProfileSelection(variable="mesh2d_s1")
    path = profile_cache_path(
        layout, selection=selection, line_name="a", time=_TIMES[0]
    )
    profiles = {
        "flat": np.array([0.1, 0.2]),
        "section": np.array([[0.1, 0.2, 0.3], [1.1, 1.2, 1.3]]),
    }
    for label, values in profiles.items():
        target = path.with_name(f"{label}.nc")
        write_profile_cache(
            target,
            guard,
            values=values,
            line=line,
            selection=selection,
            time=_TIMES[0],
            fingerprint="fp-1",
        )
        restored = read_profile_cache(
            target, expected_fingerprint="fp-1", expected_crossings=2
        )
        assert restored.shape == values.shape
        np.testing.assert_allclose(restored, values, rtol=1e-6)


@pytest.mark.unit
def test_cache_with_another_fingerprint_or_crossing_count_is_rejected(
    layout: OutputLayout, guard: OutputWriteGuard
) -> None:
    line = prepare_profile_line("a", _LINE_XY, _square_topology(2, 2), layout, guard)
    selection = ProfileSelection(variable="mesh2d_s1")
    path = profile_cache_path(
        layout, selection=selection, line_name="a", time=_TIMES[0]
    )
    write_profile_cache(
        path,
        guard,
        values=np.array([1.0, 2.0]),
        line=line,
        selection=selection,
        time=_TIMES[0],
        fingerprint="fp-1",
    )

    with pytest.raises(ProfileProcessError, match="fingerprint changed"):
        read_profile_cache(path, expected_fingerprint="fp-2", expected_crossings=2)
    with pytest.raises(ProfileProcessError, match="invalid crossing count"):
        read_profile_cache(path, expected_fingerprint="fp-1", expected_crossings=3)


@pytest.mark.unit
def test_stale_profile_cache_is_rebuilt_instead_of_failing(
    layout: OutputLayout, guard: OutputWriteGuard
) -> None:
    line = prepare_profile_line("a", _LINE_XY, _square_topology(2, 2), layout, guard)
    selection = ProfileSelection(variable="mesh2d_ucx")
    path = profile_cache_path(
        layout, selection=selection, line_name="a", time=_TIMES[0]
    )
    write_profile_cache(
        path,
        guard,
        values=np.full((2, 3), -999.0),
        line=line,
        selection=selection,
        time=_TIMES[0],
        fingerprint="stale-input",
    )
    layered = np.arange(12, dtype=float).reshape(4, 3)
    messages: list[str] = []

    values = profile_values_at_time(
        engine=QuantityEngine(),
        session=_MemoryMapSession({"mesh2d_ucx": layered}),
        entry=_entry(0),
        line=line,
        selection=selection,
        layout=layout,
        guard=guard,
        input_fingerprint="fresh-input",
        diagnostic=messages.append,
    )

    np.testing.assert_allclose(values, layered[[0, 1]])
    assert any("Rebuilding profile cache" in message for message in messages)
    restored = read_profile_cache(
        path,
        expected_fingerprint=profile_cache_fingerprint(
            input_fingerprint="fresh-input",
            selection=selection,
            line=line,
            time=_TIMES[0],
        ),
        expected_crossings=2,
    )
    np.testing.assert_allclose(restored, layered[[0, 1]])


@pytest.mark.unit
def test_crossings_are_computed_once_for_every_time_and_variable(
    monkeypatch: pytest.MonkeyPatch,
    layout: OutputLayout,
    guard: OutputWriteGuard,
) -> None:
    topology = _square_topology(2, 2)
    line = prepare_profile_line("a", _LINE_XY, topology, layout, guard)

    def forbidden(*args: object) -> object:
        raise AssertionError("crossings must come from the cache, not be recomputed")

    monkeypatch.setattr(crossings_cache, "compute_line_crossings", forbidden)
    faces = np.array([0.0, 10.0, 20.0, 30.0])
    other = np.array([1.0, 2.0, 3.0, 4.0])
    engine = QuantityEngine()
    messages: list[str] = []
    for local_index in range(len(_TIMES)):
        session = _MemoryMapSession(
            {"mesh2d_s1": faces * (local_index + 1), "mesh2d_ucx": other}
        )
        for variable, expected in (
            ("mesh2d_s1", faces[[0, 1]] * (local_index + 1)),
            ("mesh2d_ucx", other[[0, 1]]),
        ):
            values = profile_values_at_time(
                engine=engine,
                session=session,
                entry=_entry(local_index),
                line=line,
                selection=ProfileSelection(variable=variable),
                layout=layout,
                guard=guard,
                input_fingerprint="input-1",
                diagnostic=messages.append,
            )
            np.testing.assert_allclose(values, expected)

    assert not any(message.startswith("Reused") for message in messages)
    # A session with different data proves the slice is read back from the cache.
    cached = profile_values_at_time(
        engine=engine,
        session=_MemoryMapSession({"mesh2d_s1": faces * 100.0, "mesh2d_ucx": other}),
        entry=_entry(0),
        line=line,
        selection=ProfileSelection(variable="mesh2d_s1"),
        layout=layout,
        guard=guard,
        input_fingerprint="input-1",
        diagnostic=messages.append,
    )
    np.testing.assert_allclose(cached, faces[[0, 1]])
    assert messages[-1].startswith("Reused profile cache")
