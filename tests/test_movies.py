# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for guarded map-movie output."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from PIL import Image

from d3dplot.plotting import movies
from d3dplot.plotting.movies import MovieWriterError, write_mp4
from d3dplot.store import OutputLayout, initialize_output_root


class _FakeWriter:
    def __init__(self, path: str, captured: list[np.ndarray[Any, Any]]) -> None:
        self.path = Path(path)
        self.captured = captured

    def __enter__(self) -> _FakeWriter:
        return self

    def __exit__(self, *_: object) -> None:
        self.path.write_bytes(b"video")

    def append_data(self, frame: np.ndarray[Any, Any]) -> None:
        self.captured.append(frame.copy())


def _write_frame(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4), color=color).save(path)


def test_write_mp4_preserves_frame_order_and_reuses_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = OutputLayout(tmp_path / "output")
    guard = initialize_output_root(layout, tool_version="test", forbidden_roots=())
    frames = (tmp_path / "first.png", tmp_path / "second.png")
    _write_frame(frames[0], (10, 20, 30))
    _write_frame(frames[1], (100, 110, 120))
    captured: list[np.ndarray[Any, Any]] = []
    writer_arguments: dict[str, Any] = {}

    def get_writer(path: str, **kwargs: Any) -> _FakeWriter:
        writer_arguments.update(kwargs)
        return _FakeWriter(path, captured)

    def fake_import(name: str) -> Any:
        if name == "imageio.v2":
            return SimpleNamespace(get_writer=get_writer)
        if name == "imageio_ffmpeg":
            return SimpleNamespace(get_ffmpeg_exe=lambda: "ffmpeg")
        raise AssertionError(f"Unexpected optional import: {name}")

    monkeypatch.setattr(movies, "import_module", fake_import)
    output = layout.figures / "series" / "subject" / "map.mp4"

    reused = write_mp4(frames, output, 0.5, "fingerprint", guard)

    assert not reused
    assert output.read_bytes() == b"video"
    assert json.loads(output.with_suffix(".mp4.json").read_text()) == {
        "fingerprint": "fingerprint"
    }
    assert writer_arguments["fps"] == 2.0
    assert [tuple(frame[0, 0]) for frame in captured] == [
        (10, 20, 30),
        (100, 110, 120),
    ]

    def should_not_import(_: str) -> Any:
        raise AssertionError("A matching movie cache should not load imageio.")

    monkeypatch.setattr(movies, "import_module", should_not_import)
    assert write_mp4(frames, output, 0.5, "fingerprint", guard)
    monkeypatch.setattr(movies, "import_module", fake_import)
    assert not write_mp4(frames, output, 0.5, "changed-fingerprint", guard)
    assert len(captured) == 4


def test_write_mp4_reports_missing_optional_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = OutputLayout(tmp_path / "output")
    guard = initialize_output_root(layout, tool_version="test", forbidden_roots=())
    frame = tmp_path / "frame.png"
    _write_frame(frame, (1, 2, 3))

    def missing_dependency(_: str) -> Any:
        raise ModuleNotFoundError("imageio is unavailable")

    monkeypatch.setattr(movies, "import_module", missing_dependency)
    output = layout.figures / "series" / "subject" / "map.mp4"

    with pytest.raises(MovieWriterError, match="delft3d-plot\\[movies\\]") as error:
        write_mp4((frame,), output, 1.0, "fingerprint", guard)

    assert error.value.code == "V-ENV-001"
    assert not output.exists()


def test_write_mp4_rejects_mismatched_frame_dimensions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = OutputLayout(tmp_path / "output")
    guard = initialize_output_root(layout, tool_version="test", forbidden_roots=())
    frames = (tmp_path / "first.png", tmp_path / "second.png")
    _write_frame(frames[0], (1, 2, 3))
    frames[1].parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (6, 4), color=(4, 5, 6)).save(frames[1])
    monkeypatch.setattr(
        movies,
        "import_module",
        lambda _: SimpleNamespace(
            get_ffmpeg_exe=lambda: "ffmpeg",
            get_writer=lambda path, **kwargs: _FakeWriter(path, []),
        ),
    )
    output = layout.figures / "series" / "subject" / "map.mp4"

    with pytest.raises(MovieWriterError, match="identical image dimensions") as error:
        write_mp4(frames, output, 1.0, "fingerprint", guard)

    assert error.value.code == "V-PLOT-004"
    assert not output.exists()
