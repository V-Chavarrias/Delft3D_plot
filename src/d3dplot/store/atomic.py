# SPDX-License-Identifier: GPL-3.0-or-later
"""Atomic, guarded output writers."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from d3dplot.store.guard import OutputWriteError, OutputWriteGuard
from d3dplot.store.layout import OutputLayout


def initialize_output_root(
    root: OutputLayout | str | Path,
    *,
    tool_version: str,
    forbidden_roots: Iterable[str | Path] = (),
) -> OutputWriteGuard:
    """Create an output marker only in a new or empty root and return its guard."""
    if not tool_version or "\n" in tool_version or "\r" in tool_version:
        raise ValueError("tool_version must be a non-empty single-line value")
    root_path = root.root if isinstance(root, OutputLayout) else Path(root)
    guard = OutputWriteGuard(root_path, forbidden_roots)
    guard.validate_root_location()
    output_root = guard.root
    if output_root.exists() and not output_root.is_dir():
        raise OutputWriteError(f"Output root is not a directory: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)

    if guard.marker.is_symlink():
        raise OutputWriteError(f"Output marker cannot be a symlink: {guard.marker}")
    if guard.marker.exists():
        if not guard.marker.is_file():
            raise OutputWriteError(
                f"Output marker is not a regular file: {guard.marker}"
            )
        _validate_marker(guard.marker)
        return guard
    if any(output_root.iterdir()):
        raise OutputWriteError(
            f"Refusing to mark non-empty unmarked output root: {output_root}"
        )

    marker_text = json.dumps(
        {
            "tool_version": tool_version,
            "created_at": datetime.now(UTC).isoformat(),
        },
        ensure_ascii=False,
        indent=2,
    )
    _write_new_marker(guard, marker_text + "\n")
    return guard


@contextmanager
def atomic_path(
    path: str | Path,
    guard: OutputWriteGuard,
    *,
    temporary_suffix: str = ".tmp",
) -> Iterator[Path]:
    """Yield a same-directory temporary path and atomically replace its target."""
    destination = guard.validate_target(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination = guard.validate_target(path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=temporary_suffix,
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        yield temporary
        if temporary.is_symlink() or not temporary.is_file():
            raise OutputWriteError(
                f"Atomic writer did not produce a regular temporary file: {temporary}"
            )
        guard.validate_target(temporary)
        destination = guard.validate_target(path)
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def atomic_write_bytes(
    path: str | Path,
    content: bytes,
    guard: OutputWriteGuard,
) -> None:
    """Write bytes through a same-directory temporary file and atomic replace."""
    with atomic_path(path, guard) as temporary:
        with temporary.open("wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())


def atomic_write_text(
    path: str | Path,
    content: str,
    guard: OutputWriteGuard,
) -> None:
    """Write UTF-8 text with LF line endings and atomic replace."""
    with atomic_path(path, guard) as temporary:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())


@contextmanager
def guarded_text_stream(
    path: str | Path,
    guard: OutputWriteGuard,
    *,
    exclusive: bool = False,
) -> Iterator[TextIO]:
    """Open a guarded UTF-8 stream for line-flushed diagnostic output."""
    destination = guard.validate_target(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination = guard.validate_target(path)
    with destination.open(
        "x" if exclusive else "w",
        encoding="utf-8",
        newline="\n",
        buffering=1,
    ) as stream:
        yield stream


def atomic_write_json(
    path: str | Path,
    data: Mapping[str, object],
    guard: OutputWriteGuard,
) -> None:
    """Serialize JSON and write it atomically beneath a marked output root."""
    content = json.dumps(
        data,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    )
    atomic_write_text(path, content + "\n", guard)


def _write_new_marker(guard: OutputWriteGuard, content: str) -> None:
    guard.validate_root_location()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".d3dplot-output.",
        suffix=".tmp",
        dir=guard.root,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if guard.marker.exists() or guard.marker.is_symlink():
            raise OutputWriteError(
                f"Output marker was created concurrently: {guard.marker}"
            )
        os.replace(temporary, guard.marker)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _validate_marker(path: Path) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OutputWriteError(
            f"Could not read output marker {path}: {error}"
        ) from error
    if not isinstance(data, dict):
        raise OutputWriteError(f"Output marker must contain a JSON object: {path}")
    version = data.get("tool_version")
    created_at = data.get("created_at")
    if not isinstance(version, str) or not version:
        raise OutputWriteError(f"Output marker has no tool version: {path}")
    if not isinstance(created_at, str):
        raise OutputWriteError(f"Output marker has no creation time: {path}")
    try:
        timestamp = datetime.fromisoformat(created_at)
    except ValueError as error:
        raise OutputWriteError(
            f"Output marker has an invalid creation time: {path}"
        ) from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise OutputWriteError(
            f"Output marker creation time must be time-zone-aware: {path}"
        )
