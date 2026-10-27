# SPDX-License-Identifier: GPL-3.0-or-later
"""Guarded output layout, atomic writers, and recursive fingerprints."""

from d3dplot.store.atomic import (
    atomic_path,
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
    guarded_text_stream,
    initialize_output_root,
)
from d3dplot.store.fingerprint import (
    CACHE_SCHEMA_VERSION,
    canonical_json,
    fingerprint_artifact,
    fingerprint_model_output,
    fingerprint_small_input,
)
from d3dplot.store.guard import OutputWriteError, OutputWriteGuard
from d3dplot.store.layout import (
    OUTPUT_MARKER_NAME,
    OutputLayout,
    is_path_within,
    resolve_output_root,
)

__all__ = [
    "OUTPUT_MARKER_NAME",
    "OutputLayout",
    "OutputWriteError",
    "OutputWriteGuard",
    "CACHE_SCHEMA_VERSION",
    "atomic_path",
    "atomic_write_bytes",
    "atomic_write_json",
    "atomic_write_text",
    "canonical_json",
    "fingerprint_artifact",
    "fingerprint_model_output",
    "fingerprint_small_input",
    "guarded_text_stream",
    "initialize_output_root",
    "is_path_within",
    "resolve_output_root",
]
