# SPDX-License-Identifier: GPL-3.0-or-later
"""Recursive content fingerprints for inputs and generated artefacts."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from hashlib import blake2b
from pathlib import Path

_HASH_BLOCK_SIZE = 1024 * 1024
_DIGEST_SIZE = 32
CACHE_SCHEMA_VERSION = 2


def canonical_json(value: Mapping[str, object]) -> str:
    """Serialize JSON-compatible values deterministically."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def fingerprint_artifact(
    *,
    kind: str,
    schema_version: int,
    tool_version: str,
    algorithm_version: str,
    parameters: Mapping[str, object],
    inputs: Sequence[str],
    time: str | None = None,
) -> str:
    """Fingerprint one artefact from its semantics and recursive input hashes."""
    payload: dict[str, object] = {
        "kind": kind,
        "schema": schema_version,
        "tool": tool_version,
        "algorithm": algorithm_version,
        "parameters": dict(parameters),
        "inputs": list(inputs),
    }
    if time is not None:
        payload["time"] = time
    return _digest(canonical_json(payload).encode("utf-8"))


def fingerprint_small_input(path: str | Path) -> str:
    """Return the BLAKE2b content hash of a small input file."""
    file_path = Path(path).expanduser().resolve(strict=True)
    if not file_path.is_file():
        raise ValueError(f"Input fingerprint target is not a file: {file_path}")
    digest = blake2b(digest_size=_DIGEST_SIZE)
    with file_path.open("rb") as stream:
        while block := stream.read(_HASH_BLOCK_SIZE):
            digest.update(block)
    return digest.hexdigest()


def fingerprint_model_output(
    path: str | Path,
    *,
    hash_content: bool = False,
) -> str:
    """Fingerprint a large model output by stat, optionally hashing its edges."""
    file_path = Path(path).expanduser().resolve(strict=True)
    if not file_path.is_file():
        raise ValueError(f"Model-output fingerprint target is not a file: {file_path}")
    stat = file_path.stat()
    payload: dict[str, object] = {
        "path": _normalised_path(file_path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }
    if hash_content:
        payload["edge_hash"] = _fingerprint_file_edges(file_path, stat.st_size)
    return _digest(canonical_json(payload).encode("utf-8"))


def _fingerprint_file_edges(path: Path, size: int) -> str:
    digest = blake2b(digest_size=_DIGEST_SIZE)
    digest.update(size.to_bytes(8, byteorder="big", signed=False))
    with path.open("rb") as stream:
        digest.update(stream.read(_HASH_BLOCK_SIZE))
        if size > _HASH_BLOCK_SIZE:
            stream.seek(-_HASH_BLOCK_SIZE, os.SEEK_END)
            digest.update(stream.read(_HASH_BLOCK_SIZE))
    return digest.hexdigest()


def _normalised_path(path: Path) -> str:
    value = os.path.normcase(str(path)).replace("\\", "/")
    return value


def _digest(value: bytes) -> str:
    return blake2b(value, digest_size=_DIGEST_SIZE).hexdigest()
