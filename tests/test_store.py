# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for guarded output paths, atomic writes, and fingerprints."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from _input_snapshot import assert_inputs_unchanged, snapshot_inputs
from d3dplot.config.project import OutputConfig, SimulationConfig
from d3dplot.store import (
    OUTPUT_MARKER_NAME,
    OutputLayout,
    OutputWriteError,
    OutputWriteGuard,
    atomic_path,
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
    canonical_json,
    fingerprint_artifact,
    fingerprint_model_output,
    fingerprint_small_input,
    initialize_output_root,
    resolve_output_root,
)


@pytest.mark.unit
def test_output_layout_uses_documented_precedence(tmp_path: Path) -> None:
    simulation = SimulationConfig(path=tmp_path / "model", label="reference")
    default_layout = OutputLayout.for_simulation(simulation, OutputConfig())
    shared_layout = OutputLayout.for_simulation(
        simulation,
        OutputConfig(root=tmp_path / "shared"),
    )
    overridden_layout = OutputLayout.for_simulation(
        SimulationConfig(
            path=tmp_path / "model",
            label="reference",
            output_dir=tmp_path / "override",
        ),
        OutputConfig(root=tmp_path / "shared"),
    )

    assert default_layout.root == (tmp_path / "model" / "d3dplot").resolve()
    assert (
        shared_layout.root == (tmp_path / "shared" / "reference" / "d3dplot").resolve()
    )
    assert overridden_layout.root == (tmp_path / "override" / "d3dplot").resolve()
    assert default_layout.cache_inputs == default_layout.cache / "inputs"


@pytest.mark.unit
def test_output_root_rejects_a_label_that_escapes_project_root(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="stay inside output.root"):
        resolve_output_root(
            tmp_path / "model",
            "../outside",
            project_output_root=tmp_path / "shared",
        )


@pytest.mark.unit
def test_initialize_output_root_marks_only_a_new_or_empty_folder(
    tmp_path: Path,
) -> None:
    layout = OutputLayout(tmp_path / "new" / "d3dplot")
    guard = initialize_output_root(layout, tool_version="0.1.0")
    marker = json.loads(layout.marker.read_text(encoding="utf-8"))
    created_at = datetime.fromisoformat(marker["created_at"])

    assert marker["tool_version"] == "0.1.0"
    assert created_at.tzinfo is not None
    assert guard.root == layout.root
    assert not list(layout.root.glob("*.tmp"))

    repeated_guard = initialize_output_root(layout, tool_version="0.2.0")

    assert repeated_guard.root == guard.root
    assert json.loads(layout.marker.read_text(encoding="utf-8")) == marker

    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    existing_file = nonempty / "keep.txt"
    existing_file.write_text("preserve", encoding="utf-8")
    with pytest.raises(OutputWriteError, match="non-empty unmarked"):
        initialize_output_root(nonempty, tool_version="0.1.0")
    assert existing_file.read_text(encoding="utf-8") == "preserve"
    assert not (nonempty / OUTPUT_MARKER_NAME).exists()

    invalid_marker = tmp_path / "invalid-marker"
    invalid_marker.mkdir()
    (invalid_marker / OUTPUT_MARKER_NAME).write_text("{}", encoding="utf-8")
    with pytest.raises(OutputWriteError, match="no tool version"):
        initialize_output_root(invalid_marker, tool_version="0.1.0")


@pytest.mark.unit
def test_write_guard_rejects_unmarked_outside_and_forbidden_targets(
    tmp_path: Path,
) -> None:
    unmarked = tmp_path / "unmarked"
    with pytest.raises(OutputWriteError, match="marker"):
        atomic_write_text(
            unmarked / "result.txt",
            "not allowed",
            OutputWriteGuard(unmarked),
        )
    assert not unmarked.exists()

    root = tmp_path / "output"
    blocked = root / "inputs"
    guard = initialize_output_root(
        root,
        tool_version="0.1.0",
        forbidden_roots=(blocked,),
    )
    with pytest.raises(OutputWriteError, match="outside output root"):
        atomic_write_text(tmp_path / "outside.txt", "not allowed", guard)
    with pytest.raises(OutputWriteError, match="forbidden root"):
        atomic_write_text(blocked / "copy.txt", "not allowed", guard)
    assert not (tmp_path / "outside.txt").exists()
    assert not blocked.exists()


@pytest.mark.unit
def test_write_guard_rejects_roots_inside_forbidden_directories(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "protected" / "d3dplot"

    with pytest.raises(OutputWriteError, match="inside forbidden root"):
        initialize_output_root(
            output_root,
            tool_version="0.1.0",
            forbidden_roots=(tmp_path / "protected",),
        )

    assert not output_root.exists()


@pytest.mark.unit
def test_atomic_writers_replace_files_and_remove_failed_temporaries(
    tmp_path: Path,
) -> None:
    root = tmp_path / "output"
    guard = initialize_output_root(root, tool_version="0.1.0")
    text_path = root / "tables" / "status.txt"
    bytes_path = root / "cache" / "raw.bin"
    json_path = root / "config" / "resolved.json"
    atomic_write_text(text_path, "first\nsecond\n", guard)
    atomic_write_bytes(bytes_path, b"\x00\x01", guard)
    atomic_write_json(json_path, {"status": "finished", "count": 2}, guard)

    assert text_path.read_bytes() == b"first\nsecond\n"
    assert bytes_path.read_bytes() == b"\x00\x01"
    assert json.loads(json_path.read_text(encoding="utf-8")) == {
        "status": "finished",
        "count": 2,
    }

    with pytest.raises(RuntimeError, match="write failed"):
        with atomic_path(text_path, guard) as temporary:
            temporary.write_text("partial", encoding="utf-8")
            raise RuntimeError("write failed")

    assert text_path.read_text(encoding="utf-8") == "first\nsecond\n"
    assert not list(root.rglob("*.tmp"))


@pytest.mark.unit
def test_guarded_writes_leave_input_snapshots_unchanged(tmp_path: Path) -> None:
    input_file = tmp_path / "model.mdu"
    input_file.write_text("[Time]\nTStop = 1\n", encoding="utf-8")
    inputs = (input_file,)
    before = snapshot_inputs(inputs)

    root = tmp_path / "output"
    guard = initialize_output_root(root, tool_version="0.1.0")
    atomic_write_text(root / "config" / "resolved.yaml", "status: {}\n", guard)

    assert_inputs_unchanged(before, inputs)


@pytest.mark.unit
def test_input_snapshot_helper_detects_changes(tmp_path: Path) -> None:
    input_file = tmp_path / "measurement.csv"
    input_file.write_text("value\n1\n", encoding="utf-8")
    before = snapshot_inputs((input_file,))
    input_file.write_text("value\n2\n", encoding="utf-8")

    with pytest.raises(AssertionError, match="measurement.csv"):
        assert_inputs_unchanged(before, (input_file,))


@pytest.mark.unit
def test_input_snapshot_helper_detects_metadata_only_changes(
    tmp_path: Path,
) -> None:
    input_file = tmp_path / "constant.ini"
    input_file.write_text("setting=1\n", encoding="utf-8")
    before = snapshot_inputs((input_file,))
    stat = input_file.stat()
    os.utime(input_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))

    with pytest.raises(AssertionError, match="constant.ini"):
        assert_inputs_unchanged(before, (input_file,))


@pytest.mark.unit
def test_fingerprints_are_canonical_recursive_and_content_sensitive(
    tmp_path: Path,
) -> None:
    assert canonical_json({"z": 2, "a": 1}) == '{"a":1,"z":2}'
    kwargs = {
        "kind": "grid",
        "schema_version": 1,
        "tool_version": "0.1.0",
        "algorithm_version": "2",
        "inputs": ["input-a", "input-b"],
        "time": "2021-01-01T00:00:00+00:00",
    }
    first = fingerprint_artifact(
        parameters={"nested": {"z": 2, "a": [1, 3]}},
        **kwargs,
    )
    same = fingerprint_artifact(
        parameters={"nested": {"a": [1, 3], "z": 2}},
        **kwargs,
    )
    changed = fingerprint_artifact(
        parameters={"nested": {"a": [1, 4], "z": 2}},
        **kwargs,
    )

    assert first == same
    assert first != changed

    small_input = tmp_path / "settings.ini"
    small_input.write_text("setting=1\n", encoding="utf-8")
    first_content_hash = fingerprint_small_input(small_input)
    small_input.write_text("setting=2\n", encoding="utf-8")
    assert fingerprint_small_input(small_input) != first_content_hash


@pytest.mark.unit
def test_model_output_fingerprint_can_hash_first_and_last_megabyte(
    tmp_path: Path,
) -> None:
    output_file = tmp_path / "map.nc"
    original = bytearray(b"a" * (2 * 1024 * 1024 + 16))
    original[1024 * 1024] = ord("m")
    output_file.write_bytes(original)
    os.utime(output_file, ns=(1_000_000_000, 1_000_000_000))
    stat = output_file.stat()
    stat_fingerprint = fingerprint_model_output(output_file)
    edge_fingerprint = fingerprint_model_output(output_file, hash_content=True)

    for index in (0, -1):
        changed = bytearray(original)
        changed[index] = ord("z")
        output_file.write_bytes(changed)
        os.utime(output_file, ns=(stat.st_atime_ns, stat.st_mtime_ns))

        assert fingerprint_model_output(output_file) == stat_fingerprint
        assert (
            fingerprint_model_output(output_file, hash_content=True) != edge_fingerprint
        )
        edge_fingerprint = fingerprint_model_output(output_file, hash_content=True)
