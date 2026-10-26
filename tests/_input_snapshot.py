# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only before/after snapshots for simulation input files."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from d3dplot.store.fingerprint import fingerprint_small_input


@dataclass(frozen=True, slots=True)
class InputFileSnapshot:
    size: int
    mtime_ns: int
    content_hash: str


def snapshot_inputs(paths: Iterable[str | Path]) -> dict[Path, InputFileSnapshot]:
    """Capture each input's resolved path, size, modification time, and hash."""
    resolved_paths = sorted(
        {Path(path).expanduser().resolve(strict=True) for path in paths},
        key=str,
    )
    snapshots: dict[Path, InputFileSnapshot] = {}
    for path in resolved_paths:
        stat = path.stat()
        if not path.is_file():
            raise ValueError(f"Input snapshot target is not a file: {path}")
        snapshots[path] = InputFileSnapshot(
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            content_hash=fingerprint_small_input(path),
        )
    return snapshots


def assert_inputs_unchanged(
    before: dict[Path, InputFileSnapshot],
    paths: Iterable[str | Path],
) -> None:
    """Raise an assertion with changed input paths when a snapshot differs."""
    after = snapshot_inputs(paths)
    changed = sorted(
        {
            *(
                path
                for path in before.keys() | after.keys()
                if before.get(path) != after.get(path)
            )
        },
        key=str,
    )
    if changed:
        changed_paths = ", ".join(str(path) for path in changed)
        raise AssertionError(
            f"Input files changed during the operation: {changed_paths}"
        )
