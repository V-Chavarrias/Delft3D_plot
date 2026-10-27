# SPDX-License-Identifier: GPL-3.0-or-later
"""Cache reuse decisions for fingerprinted task outputs."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Literal, Protocol, cast

from d3dplot.config.analysis import ProcessingPolicy

CacheAction = Literal["reuse", "rebuild"]
CacheReason = Literal["missing", "always", "match", "mismatch", "never"]


class CacheFingerprintReadError(RuntimeError):
    """Raised when an existing cache file cannot be inspected safely."""


class _NetCDFDataset(Protocol):
    def ncattrs(self) -> list[str]: ...

    def getncattr(self, name: str) -> object: ...


class _NetCDF4Module(Protocol):
    def Dataset(
        self,
        path: Path,
        *,
        mode: Literal["r"],
    ) -> AbstractContextManager[_NetCDFDataset]: ...


@dataclass(frozen=True, slots=True)
class CacheDecision:
    """Whether an existing artefact can be reused under a processing policy."""

    action: CacheAction
    reason: CacheReason
    warning: str | None = None


def decide_cache(
    *,
    exists: bool,
    existing_fingerprint: str | None,
    expected_fingerprint: str,
    policy: ProcessingPolicy,
) -> CacheDecision:
    """Choose whether to reuse or rebuild one output."""
    if not expected_fingerprint:
        raise ValueError("An expected fingerprint must be non-empty.")
    if policy not in {"if_changed", "never", "always"}:
        raise ValueError(f"Unsupported reprocess policy: {policy!r}.")
    if not exists:
        return CacheDecision(action="rebuild", reason="missing")
    if policy == "always":
        return CacheDecision(action="rebuild", reason="always")
    if policy == "never":
        if existing_fingerprint != expected_fingerprint:
            return CacheDecision(
                action="reuse",
                reason="never",
                warning=(
                    "Reusing an existing output despite a missing or mismatched "
                    "fingerprint because reprocess is set to never."
                ),
            )
        return CacheDecision(action="reuse", reason="never")
    if existing_fingerprint == expected_fingerprint:
        return CacheDecision(action="reuse", reason="match")
    return CacheDecision(action="rebuild", reason="mismatch")


def read_cache_fingerprints(
    paths: Sequence[str | Path],
) -> dict[Path, str | None]:
    """Read each existing NetCDF output fingerprint at most once."""
    netcdf4 = cast(_NetCDF4Module, import_module("netCDF4"))
    fingerprints: dict[Path, str | None] = {}
    for path in paths:
        cache_path = Path(path).expanduser().resolve(strict=False)
        if cache_path in fingerprints:
            continue
        if not cache_path.exists():
            fingerprints[cache_path] = None
            continue
        if not cache_path.is_file():
            raise CacheFingerprintReadError(
                f"Cache fingerprint target is not a regular file: {cache_path}"
            )
        try:
            with netcdf4.Dataset(cache_path, mode="r") as dataset:
                if "d3dplot_fingerprint" not in dataset.ncattrs():
                    fingerprints[cache_path] = None
                    continue
                value = dataset.getncattr("d3dplot_fingerprint")
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise CacheFingerprintReadError(
                f"Could not read cache fingerprint from {cache_path}: {error}"
            ) from error
        if not isinstance(value, str) or not value:
            raise CacheFingerprintReadError(
                f"Cache fingerprint in {cache_path} must be a non-empty string."
            )
        fingerprints[cache_path] = value
    return fingerprints
