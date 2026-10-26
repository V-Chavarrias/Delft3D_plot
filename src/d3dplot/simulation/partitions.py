# SPDX-License-Identifier: GPL-3.0-or-later
"""Discover and validate map-output files for one FM sub-run."""

from __future__ import annotations

import os
import re
from contextlib import AbstractContextManager
from dataclasses import dataclass
from importlib import import_module
from io import BufferedReader
from pathlib import Path
from threading import Lock
from typing import BinaryIO, Literal, Protocol, cast

from d3dplot.model_input.mdu import MduInfo
from d3dplot.validate.report import ValidationIssue

_NETCDF3_SIGNATURES = {b"CDF\x01", b"CDF\x02"}
_NETCDF4_READ_LOCK = Lock()


class _NetCDF4Module(Protocol):
    def Dataset(
        self,
        path: Path,
        *,
        mode: Literal["r"],
    ) -> AbstractContextManager[object]: ...


class _SciPyIONetCDFModule(Protocol):
    def netcdf_file(
        self,
        file: BinaryIO,
        *,
        mode: Literal["r"],
        mmap: bool,
    ) -> AbstractContextManager[object]: ...


@dataclass(frozen=True, slots=True)
class MapFiles:
    """The map files belonging to one sub-run, in partition order."""

    paths: tuple[Path, ...]
    partitioned: bool

    @property
    def partition_count(self) -> int:
        """Return the number of map files, including one for plain FM."""
        return len(self.paths)


def discover_map_files(
    mdu: MduInfo,
) -> tuple[MapFiles | None, tuple[ValidationIssue, ...]]:
    """Find map files from an MDU and verify their NetCDF headers read-only."""
    map_path = (
        mdu.map_file if mdu.map_file.is_absolute() else mdu.output_dir / mdu.map_file
    )
    output_dir = map_path.parent
    if not output_dir.is_dir():
        return None, (_missing_maps_issue(output_dir, "Map output folder is missing."),)

    prefix = _partition_prefix(map_path.name)
    partition_pattern = re.compile(
        rf"^{re.escape(prefix)}_([0-9]{{4}})_map\.nc$",
        flags=re.IGNORECASE,
    )
    partition_files: list[tuple[int, Path]] = []
    try:
        with os.scandir(output_dir) as entries:
            for entry in entries:
                match = partition_pattern.fullmatch(entry.name)
                if match is not None and entry.is_file():
                    partition_files.append(
                        (int(match.group(1)), output_dir / entry.name)
                    )
    except OSError as error:
        return None, (
            _missing_maps_issue(
                output_dir,
                f"Could not list map output files: {error}",
            ),
        )

    if partition_files:
        partition_files.sort(key=lambda item: item[0])
        indices = [index for index, _ in partition_files]
        missing = sorted(set(range(indices[-1] + 1)) - set(indices))
        duplicates = sorted(index for index in set(indices) if indices.count(index) > 1)
        if missing or duplicates:
            details: list[str] = []
            if missing:
                details.append(
                    "missing partition(s) "
                    + ", ".join(f"{index:04d}" for index in missing)
                )
            if duplicates:
                details.append(
                    "duplicate partition number(s) "
                    + ", ".join(f"{index:04d}" for index in duplicates)
                )
            return None, (
                _missing_maps_issue(
                    output_dir,
                    "Partition map files are not consecutive from 0000: "
                    + "; ".join(details)
                    + ".",
                ),
            )
        paths = tuple(path for _, path in partition_files)
        partitioned = True
    elif map_path.is_file():
        paths = (map_path,)
        partitioned = False
    else:
        return None, (_missing_maps_issue(output_dir, "Map files are missing."),)

    issues: list[ValidationIssue] = []
    scipy_io: _SciPyIONetCDFModule | None = None
    netcdf4: _NetCDF4Module | None = None
    for path in paths:
        try:
            with path.open("rb", buffering=0) as stream:
                signature = stream.read(4)
                if signature in _NETCDF3_SIGNATURES:
                    if scipy_io is None:
                        # Delay imports because NumPy's stubs require Python 3.12.
                        scipy_io = cast(
                            _SciPyIONetCDFModule,
                            import_module("scipy.io"),
                        )
                    stream.seek(0)
                    with BufferedReader(stream) as buffered_stream:
                        with scipy_io.netcdf_file(
                            buffered_stream,
                            mode="r",
                            mmap=True,
                        ):
                            pass
                    continue
            if netcdf4 is None:
                os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")
                netcdf4 = cast(
                    _NetCDF4Module,
                    import_module("netCDF4"),
                )
            with _NETCDF4_READ_LOCK:
                with netcdf4.Dataset(path, mode="r"):
                    pass
        except (
            EOFError,
            IndexError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as error:
            issues.append(
                ValidationIssue(
                    code="V-OUT-002",
                    level="error",
                    location=str(path),
                    message=f"Map file is unreadable as NetCDF: {error}",
                    hint="Restore or regenerate this D-Flow FM map output.",
                )
            )
    if issues:
        return None, tuple(issues)
    return MapFiles(paths=paths, partitioned=partitioned), ()


def _partition_prefix(map_file_name: str) -> str:
    suffix = "_map.nc"
    if map_file_name.lower().endswith(suffix):
        return map_file_name[: -len(suffix)]
    return Path(map_file_name).stem


def _missing_maps_issue(path: Path, message: str) -> ValidationIssue:
    return ValidationIssue(
        code="V-OUT-001",
        level="error",
        location=str(path),
        message=message,
        hint="Check the MDU OutputDir and MapFile settings and restore missing output.",
    )
