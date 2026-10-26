# SPDX-License-Identifier: GPL-3.0-or-later
"""Inspect model-output topology and cache checked face merge plans."""

from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager, redirect_stdout
from dataclasses import dataclass
from hashlib import blake2b
from importlib import import_module
from io import StringIO
from numbers import Integral
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, Protocol, TypeAlias, cast

from d3dplot import __version__
from d3dplot.simulation.discover import Simulation
from d3dplot.simulation.partitions import _NETCDF4_READ_LOCK
from d3dplot.simulation.subrun import SubRun
from d3dplot.store import (
    OutputLayout,
    OutputWriteError,
    OutputWriteGuard,
    atomic_path,
    fingerprint_artifact,
    fingerprint_model_output,
    initialize_output_root,
)
from d3dplot.validate.report import ValidationIssue, ValidationReport

_PARTITION_NAME = re.compile(r"(.+)_(\d{4})(_map\.nc)", flags=re.IGNORECASE)
_TIME_TOLERANCE_S = 1.0
_COORDINATE_TOLERANCE_M = 0.001
_CACHE_SCHEMA_VERSION = 1
_ALGORITHM_VERSION = "1"
_FACE_DOMAIN = "mesh2d_flowelem_domain"
_FACE_GLOBALNR = "mesh2d_flowelem_globalnr"
_FACE_NODES = "mesh2d_face_nodes"
_TIME = "time"
_MORPH_TIME = "morft"

# NumPy stubs require Python 3.12; the package still supports Python 3.11.
_Array: TypeAlias = Any
_XarrayDataset: TypeAlias = Any
_XarrayDataArray: TypeAlias = Any
np: Any = import_module("numpy")
xr: Any = import_module("xarray")


class _NetCDFVariable(Protocol):
    dimensions: tuple[str, ...]
    shape: tuple[int, ...]

    def __getitem__(self, key: object) -> object: ...

    def ncattrs(self) -> list[str]: ...

    def getncattr(self, name: str) -> object: ...


class _NetCDFDimension(Protocol):
    def __len__(self) -> int: ...


class _NetCDFDataset(Protocol):
    variables: Mapping[str, _NetCDFVariable]
    dimensions: Mapping[str, _NetCDFDimension]

    def ncattrs(self) -> list[str]: ...

    def getncattr(self, name: str) -> object: ...


class _NetCDF4Module(Protocol):
    def Dataset(
        self,
        path: Path,
        *,
        mode: Literal["r"],
    ) -> AbstractContextManager[_NetCDFDataset]: ...


class _HashDigest(Protocol):
    def update(self, data: bytes, /) -> None: ...


class _KDTree(Protocol):
    def query(
        self,
        x: _Array,
        *,
        k: int,
        distance_upper_bound: float,
        workers: int,
    ) -> tuple[_Array, _Array]: ...


class _Grid(Protocol):
    name: str
    face_dimension: str
    edge_dimension: str
    n_face: int
    n_edge: int
    crs: object

    @property
    def face_coordinates(self) -> _Array: ...

    @property
    def face_node_connectivity(self) -> _Array: ...

    @property
    def face_edge_connectivity(self) -> _Array: ...

    @property
    def edge_node_connectivity(self) -> _Array: ...

    @property
    def edge_length(self) -> _Array: ...

    def set_crs(
        self,
        crs: str | None = None,
        epsg: int | None = None,
        allow_override: bool = False,
    ) -> None: ...

    def to_dataset(self) -> _XarrayDataset: ...


class _UgridDataset(Protocol):
    obj: _XarrayDataset
    grid: _Grid

    def close(self) -> None: ...


class _OpenPartitionedDataset(Protocol):
    def __call__(
        self,
        file_nc: str,
        **kwargs: object,
    ) -> _UgridDataset: ...


class _ScipySpatialModule(Protocol):
    def cKDTree(self, data: _Array) -> _KDTree: ...


class _DfmToolsModule(Protocol):
    open_partitioned_dataset: _OpenPartitionedDataset


@dataclass(frozen=True, slots=True)
class InspectionResult:
    """Paths and summary for one successfully inspected simulation."""

    merge_plan_path: Path
    grid_paths: tuple[Path, ...]
    plan_count: int
    reference_subrun: int
    cache_reused: bool
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class _SubRunInspection:
    """Bounded metadata retained after scanning one sub-run's partitions."""

    signatures: tuple[str, ...]
    owned_counts: tuple[int, ...]
    has_globalnr: bool
    time_consistent: bool
    corrupt_partitions: tuple[tuple[int, tuple[int, ...]], ...]


@dataclass(frozen=True, slots=True)
class _MergePlan:
    """Face ownership and placement for one topology group."""

    subrun_indices: tuple[int, ...]
    reference_subrun: int
    matching: str
    grid_file: str
    n_faces: int
    n_edges: int
    partition_offset: _Array
    partition_n_owned: _Array
    owned_local: _Array
    global_pos: _Array


@dataclass(frozen=True, slots=True)
class _IntegerArray:
    values: _Array
    mask: _Array


@dataclass(frozen=True, slots=True)
class _NumericSeries:
    values: _Array
    mask: _Array
    units: str | None


class _InspectionFailure(Exception):
    def __init__(self, issue: ValidationIssue) -> None:
        super().__init__(issue.message)
        self.issue = issue


def inspect_simulation(
    simulation: Simulation,
    layout: OutputLayout,
    *,
    crs: str | None = None,
    forbidden_roots: Sequence[str | Path] = (),
) -> tuple[InspectionResult | None, ValidationReport]:
    """Build or reuse the checked topology cache for a discovered simulation."""
    started = perf_counter()
    issues: list[ValidationIssue] = []
    subrun_metadata: list[_SubRunInspection] = []

    for subrun in simulation.subruns:
        metadata, subrun_issues = _inspect_subrun_metadata(subrun)
        issues.extend(subrun_issues)
        if metadata is None:
            continue
        subrun_metadata.append(metadata)

    if len(subrun_metadata) != len(simulation.subruns) or _has_errors(issues):
        return None, ValidationReport(tuple(issues))

    topology_groups: dict[tuple[str, ...], list[int]] = {}
    for position, metadata in enumerate(subrun_metadata):
        topology_groups.setdefault(metadata.signatures, []).append(position)

    if len(topology_groups) > 1:
        group_text = "; ".join(
            ", ".join(str(simulation.subruns[position].index) for position in group)
            for group in topology_groups.values()
        )
        issues.append(
            ValidationIssue(
                code="V-OUT-021",
                level="warning",
                location=str(simulation.root),
                message=(
                    f"Topology signatures differ between sub-runs; "
                    f"{len(topology_groups)} merge plans are needed "
                    f"(sub-runs: {group_text})."
                ),
                hint=(
                    "Each topology group is inspected separately; cross-time "
                    "comparisons between different grids require regridding."
                ),
            )
        )

    references: list[tuple[tuple[str, ...], tuple[int, ...], int]] = []
    for signatures, positions in topology_groups.items():
        reference_position = next(
            (
                position
                for position in positions
                if subrun_metadata[position].time_consistent
            ),
            None,
        )
        if reference_position is None:
            affected = ", ".join(
                str(simulation.subruns[position].index) for position in positions
            )
            issues.append(
                ValidationIssue(
                    code="V-OUT-024",
                    level="error",
                    location=str(simulation.root),
                    message=(
                        "No time-consistent sub-run can serve as the reference "
                        f"for topology group [{affected}]."
                    ),
                    hint=(
                        "Restore consistent finite time and morft values in at "
                        "least one sub-run of each topology group."
                    ),
                )
            )
            continue
        references.append((signatures, tuple(positions), reference_position))

    if _has_errors(issues):
        return None, ValidationReport(tuple(issues))

    for _, _, reference_position in references:
        if not subrun_metadata[reference_position].has_globalnr:
            reference = simulation.subruns[reference_position]
            issues.append(
                ValidationIssue(
                    code="V-OUT-023",
                    level="warning",
                    location=str(reference.folder),
                    message=(
                        "Global face numbers are absent; face centres will be "
                        "matched to the merged grid within 1 mm."
                    ),
                    hint=(
                        "Use unique face centres in every partition; ambiguous "
                        "or missing coordinate matches are errors."
                    ),
                )
            )

    reference_subrun = next(
        simulation.subruns[position].index
        for position, metadata in enumerate(subrun_metadata)
        if metadata.time_consistent
    )
    try:
        input_fingerprints = [
            fingerprint_model_output(path)
            for subrun in simulation.subruns
            for path in subrun.map_files
        ]
    except (OSError, RuntimeError, ValueError) as error:
        issues.append(
            _issue(
                "V-OUT-002",
                "error",
                simulation.root,
                f"Could not fingerprint map output: {error}",
                "Check that every discovered map file is readable.",
            )
        )
        return None, ValidationReport(tuple(issues))

    parameters: dict[str, object] = {
        "crs": crs,
        "plans": [
            {
                "subruns": [
                    simulation.subruns[position].index for position in positions
                ],
                "reference_subrun": simulation.subruns[reference_position].index,
                "signatures": list(signatures),
            }
            for signatures, positions, reference_position in references
        ],
    }
    fingerprint = fingerprint_artifact(
        kind="inspect",
        schema_version=_CACHE_SCHEMA_VERSION,
        tool_version=__version__,
        algorithm_version=_ALGORITHM_VERSION,
        parameters=parameters,
        inputs=input_fingerprints,
    )

    try:
        guard = initialize_output_root(
            layout,
            tool_version=__version__,
            forbidden_roots=forbidden_roots,
        )
    except (OSError, OutputWriteError, ValueError) as error:
        issues.append(
            _issue(
                "V-OUT-041",
                "error",
                layout.root,
                f"Could not initialize the inspection output root: {error}",
                "Choose a new or empty writable output root outside forbidden roots.",
            )
        )
        return None, ValidationReport(tuple(issues))

    merge_plan_path = layout.cache / "merge_plan.nc"
    grid_paths = tuple(
        layout.cache / _grid_file_name(plan_index)
        for plan_index in range(len(references))
    )
    try:
        cache_reused, cache_problem = _cache_is_current(
            merge_plan_path,
            grid_paths,
            fingerprint,
            guard,
        )
    except OutputWriteError as error:
        issues.append(
            _issue(
                "V-OUT-041",
                "error",
                layout.cache,
                f"Could not access the inspection cache: {error}",
                "Choose a writable output root outside forbidden roots.",
            )
        )
        return None, ValidationReport(tuple(issues))

    if cache_problem is not None:
        issues.append(
            ValidationIssue(
                code="V-OUT-025",
                level="warning",
                location=str(merge_plan_path),
                message=(
                    "An incomplete or unreadable inspection cache will be rebuilt: "
                    f"{cache_problem}"
                ),
                hint="The generated cache is safe to remove and will be recreated.",
            )
        )

    if cache_reused:
        return (
            InspectionResult(
                merge_plan_path=merge_plan_path,
                grid_paths=grid_paths,
                plan_count=len(references),
                reference_subrun=reference_subrun,
                cache_reused=True,
                duration_seconds=perf_counter() - started,
            ),
            ValidationReport(tuple(issues)),
        )

    plans: list[_MergePlan] = []
    for plan_index, (_signatures, plan_positions, reference_position) in enumerate(
        references
    ):
        reference = simulation.subruns[reference_position]
        ugrid: _UgridDataset | None = None
        try:
            ugrid = _open_merged_dataset(reference)
            plan, grid_dataset = _build_merge_plan(
                reference,
                subrun_metadata[reference_position],
                tuple(
                    simulation.subruns[position].index for position in plan_positions
                ),
                grid_paths[plan_index].name,
                ugrid,
                crs,
                fingerprint,
                layout.cache / _grid_file_name(plan_index),
            )
            _write_netcdf(grid_paths[plan_index], grid_dataset, guard)
            plans.append(plan)
        except _InspectionFailure as error:
            issues.append(error.issue)
            return None, ValidationReport(tuple(issues))
        except OutputWriteError as error:
            issues.append(
                _issue(
                    "V-OUT-041",
                    "error",
                    grid_paths[plan_index],
                    f"Could not write the grid cache: {error}",
                    "Choose a writable output root outside forbidden roots.",
                )
            )
            return None, ValidationReport(tuple(issues))
        except (
            EOFError,
            IndexError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as error:
            issues.append(
                _issue(
                    "V-OUT-002",
                    "error",
                    reference.folder,
                    f"Could not build the merged grid: {error}",
                    "Restore the reference sub-run's readable map output and topology.",
                )
            )
            return None, ValidationReport(tuple(issues))
        finally:
            if ugrid is not None:
                ugrid.close()

    try:
        merge_plan_dataset = _merge_plan_dataset(
            simulation,
            subrun_metadata,
            plans,
            reference_subrun,
            fingerprint,
        )
        _write_netcdf(merge_plan_path, merge_plan_dataset, guard)
    except _InspectionFailure as error:
        issues.append(error.issue)
        return None, ValidationReport(tuple(issues))
    except OutputWriteError as error:
        issues.append(
            _issue(
                "V-OUT-041",
                "error",
                merge_plan_path,
                f"Could not write the merge-plan cache: {error}",
                "Choose a writable output root outside forbidden roots.",
            )
        )
        return None, ValidationReport(tuple(issues))
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        issues.append(
            _issue(
                "V-OUT-002",
                "error",
                merge_plan_path,
                f"Could not serialize the merge-plan cache: {error}",
                "Check the output filesystem and available disk space.",
            )
        )
        return None, ValidationReport(tuple(issues))

    return (
        InspectionResult(
            merge_plan_path=merge_plan_path,
            grid_paths=grid_paths,
            plan_count=len(plans),
            reference_subrun=reference_subrun,
            cache_reused=False,
            duration_seconds=perf_counter() - started,
        ),
        ValidationReport(tuple(issues)),
    )


def _inspect_subrun_metadata(
    subrun: SubRun,
) -> tuple[_SubRunInspection | None, tuple[ValidationIssue, ...]]:
    netcdf4 = _netcdf4_module()
    issues: list[ValidationIssue] = []
    signatures: list[str] = []
    owned_counts: list[int] = []
    domain_values: set[int] = set()
    global_numbers: list[_Array] = []
    globalnr_presence: list[bool] = []
    times: list[_NumericSeries | None] = []
    morph_times: list[_NumericSeries | None] = []

    for partition, path in enumerate(subrun.map_files):
        if subrun.partitioned:
            match = _PARTITION_NAME.fullmatch(path.name)
            if match is None or int(match.group(2)) != partition:
                issues.append(
                    _issue(
                        "V-OUT-022",
                        "error",
                        path,
                        (
                            "Partition file number does not match expected "
                            f"partition {partition}."
                        ),
                        "Use consecutive map-file numbers starting at 0000.",
                    )
                )

        try:
            with _NETCDF4_READ_LOCK, netcdf4.Dataset(path, mode="r") as dataset:
                face_dimension = dataset.dimensions.get("mesh2d_nFaces")
                if face_dimension is None:
                    raise ValueError("required dimension 'mesh2d_nFaces' is missing")
                n_faces = len(face_dimension)
                if n_faces <= 0:
                    raise ValueError("dimension 'mesh2d_nFaces' is empty")

                domain = _read_integer_variable(dataset, _FACE_DOMAIN)
                globalnr = _read_integer_variable(dataset, _FACE_GLOBALNR)
                face_nodes = _read_integer_variable(dataset, _FACE_NODES)
                if face_nodes is None:
                    issues.append(
                        _issue(
                            "V-OUT-002",
                            "error",
                            path,
                            f"Required topology variable {_FACE_NODES!r} is missing.",
                            "Restore a complete D-Flow FM map file.",
                        )
                    )
                    continue
                if face_nodes.values.ndim != 2 or face_nodes.values.shape[0] != n_faces:
                    issues.append(
                        _issue(
                            "V-OUT-002",
                            "error",
                            path,
                            (
                                f"Topology variable {_FACE_NODES!r} does not "
                                "match the face dimension."
                            ),
                            "Restore a complete D-Flow FM map file.",
                        )
                    )
                    continue

                signature = _topology_signature(
                    n_faces,
                    domain,
                    globalnr,
                    face_nodes,
                )
                signatures.append(signature)

                if subrun.partitioned:
                    if domain is None:
                        issues.append(
                            _issue(
                                "V-OUT-022",
                                "error",
                                path,
                                f"Partitioned map file is missing {_FACE_DOMAIN!r}.",
                                (
                                    "Restore the domain-number variable in every "
                                    "partition."
                                ),
                            )
                        )
                        owned_counts.append(0)
                    elif (
                        domain.values.ndim != 1
                        or domain.values.size != n_faces
                        or domain.mask.any()
                    ):
                        issues.append(
                            _issue(
                                "V-OUT-022",
                                "error",
                                path,
                                (
                                    f"Partition ownership in {_FACE_DOMAIN!r} is "
                                    "masked or has an invalid shape."
                                ),
                                "Restore valid domain numbers for every face.",
                            )
                        )
                        owned_counts.append(0)
                    else:
                        domain_for_faces = domain.values
                        domain_values.update(
                            int(value) for value in np.unique(domain_for_faces)
                        )
                        owned = np.flatnonzero(domain_for_faces == partition)
                        if owned.size == 0:
                            issues.append(
                                _issue(
                                    "V-OUT-022",
                                    "error",
                                    path,
                                    f"Partition {partition} owns no faces.",
                                    "Restore consistent D-Flow FM domain numbers.",
                                )
                            )
                        owned_counts.append(int(owned.size))
                else:
                    owned = np.arange(n_faces, dtype=np.int64)
                    owned_counts.append(n_faces)
                    if domain is not None and (
                        domain.values.ndim != 1
                        or domain.values.size != n_faces
                        or domain.mask.any()
                        or np.any(domain.values != 0)
                    ):
                        issues.append(
                            _issue(
                                "V-OUT-022",
                                "error",
                                path,
                                (
                                    "An unpartitioned map file has inconsistent "
                                    "domain numbers."
                                ),
                                (
                                    "Use zero domain numbers or omit the "
                                    "partition-domain variable."
                                ),
                            )
                        )

                if globalnr is not None and owned_counts[-1] > 0:
                    if (
                        globalnr.values.ndim != 1
                        or globalnr.values.size != n_faces
                        or globalnr.mask[owned].any()
                    ):
                        issues.append(
                            _issue(
                                "V-OUT-022",
                                "error",
                                path,
                                (
                                    f"Owned-face values in {_FACE_GLOBALNR!r} are "
                                    "masked or have an invalid shape."
                                ),
                                (
                                    "Restore valid global face numbers for every "
                                    "owned face."
                                ),
                            )
                        )
                    else:
                        global_numbers.append(globalnr.values[owned])
                globalnr_presence.append(globalnr is not None)

                time_series = _read_numeric_series(dataset, _TIME)
                if time_series is None:
                    issues.append(
                        _issue(
                            "V-OUT-002",
                            "error",
                            path,
                            "Required map-time variable 'time' is missing.",
                            "Restore a complete D-Flow FM map file.",
                        )
                    )
                times.append(time_series)
                morph_times.append(_read_numeric_series(dataset, _MORPH_TIME))
        except (
            EOFError,
            IndexError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as error:
            issues.append(
                _issue(
                    "V-OUT-002",
                    "error",
                    path,
                    f"Map topology metadata could not be read: {error}",
                    "Restore or regenerate this D-Flow FM map output.",
                )
            )

    if len(signatures) != subrun.partition_count:
        return None, tuple(issues)

    if subrun.partitioned and domain_values != set(range(subrun.partition_count)):
        issues.append(
            _issue(
                "V-OUT-022",
                "error",
                subrun.folder,
                (
                    "Domain numbers across partition files do not equal the "
                    "partition numbers."
                ),
                "Restore domain values that match file numbers 0 through N-1.",
            )
        )

    any_globalnr = any(globalnr_presence)
    all_globalnr = bool(globalnr_presence) and all(globalnr_presence)
    if any_globalnr and not all_globalnr:
        issues.append(
            _issue(
                "V-OUT-022",
                "error",
                subrun.folder,
                "The global-face-number variable is present in only some partitions.",
                (
                    "Restore the variable in every partition or omit it from "
                    "every partition."
                ),
            )
        )
    if all_globalnr:
        owned_globalnr = (
            np.concatenate(global_numbers)
            if global_numbers
            else np.empty(0, dtype=np.int64)
        )
        expected = np.arange(1, owned_globalnr.size + 1, dtype=np.int64)
        if (
            owned_globalnr.size == 0
            or np.any(owned_globalnr < 1)
            or np.unique(owned_globalnr).size != owned_globalnr.size
            or not np.array_equal(np.sort(owned_globalnr), expected)
        ):
            issues.append(
                _issue(
                    "V-OUT-022",
                    "error",
                    subrun.folder,
                    (
                        "Owned global face numbers are not unique and complete "
                        "from 1 through n_faces."
                    ),
                    "Restore 1-based global numbering for all owned faces.",
                )
            )

    if any(issue.level == "error" for issue in issues):
        return (
            _SubRunInspection(
                signatures=tuple(signatures),
                owned_counts=tuple(owned_counts),
                has_globalnr=all_globalnr,
                time_consistent=False,
                corrupt_partitions=(),
            ),
            tuple(issues),
        )

    corrupt_map_indices, time_available = _inconsistent_map_times(
        times,
        morph_times,
        subrun,
    )
    if corrupt_map_indices:
        description = "; ".join(
            f"partition {partition}: map indices {', '.join(map(str, indices))}"
            for partition, indices in sorted(corrupt_map_indices.items())
        )
        issues.append(
            ValidationIssue(
                code="V-OUT-030",
                level="warning",
                location=str(subrun.folder),
                message=f"Inconsistent or corrupt time/morft values ({description}).",
                hint=(
                    "The inspect phase will use another time-consistent sub-run "
                    "as the merge-plan reference."
                ),
            )
        )

    return (
        _SubRunInspection(
            signatures=tuple(signatures),
            owned_counts=tuple(owned_counts),
            has_globalnr=all_globalnr,
            time_consistent=time_available and not corrupt_map_indices,
            corrupt_partitions=tuple(
                (partition, tuple(indices))
                for partition, indices in sorted(corrupt_map_indices.items())
            ),
        ),
        tuple(issues),
    )


def _inconsistent_map_times(
    times: Sequence[_NumericSeries | None],
    morph_times: Sequence[_NumericSeries | None],
    subrun: SubRun,
) -> tuple[dict[int, set[int]], bool]:
    corrupt: dict[int, set[int]] = {}
    time_present = all(series is not None for series in times)
    if not time_present:
        return corrupt, False

    numeric_times = cast(tuple[_NumericSeries, ...], tuple(times))
    time_ok = _mark_inconsistent_series(numeric_times, corrupt)
    _mark_mdu_time_bounds(numeric_times, subrun, corrupt)

    present_morph = [series is not None for series in morph_times]
    if any(present_morph) and not all(present_morph):
        expected_length = max(
            (series.values.size for series in morph_times if series is not None),
            default=0,
        )
        for partition, present in enumerate(present_morph):
            if not present:
                corrupt.setdefault(partition, set()).update(range(expected_length))
        morph_ok = False
    elif all(present_morph):
        morph_ok = _mark_inconsistent_series(
            cast(tuple[_NumericSeries, ...], tuple(morph_times)),
            corrupt,
        )
    else:
        morph_ok = True

    return corrupt, time_ok and morph_ok


def _mark_inconsistent_series(
    series: tuple[_NumericSeries, ...],
    corrupt: dict[int, set[int]],
) -> bool:
    lengths = [item.values.size for item in series]
    most_common_length, length_count = Counter(lengths).most_common(1)[0]
    if length_count <= len(series) // 2:
        for partition in range(len(series)):
            corrupt.setdefault(partition, set()).add(0)
        return False

    for partition, length in enumerate(lengths):
        if length != most_common_length:
            corrupt.setdefault(partition, set()).update(
                range(max(length, most_common_length))
            )

    unit_counts = Counter(item.units for item in series)
    common_unit, unit_count = unit_counts.most_common(1)[0]
    if unit_count <= len(series) // 2:
        for partition in range(len(series)):
            corrupt.setdefault(partition, set()).update(range(most_common_length))
        return False
    for partition, item in enumerate(series):
        if item.units != common_unit:
            corrupt.setdefault(partition, set()).update(range(item.values.size))

    values = np.full((len(series), most_common_length), np.nan, dtype=np.float64)
    for partition, item in enumerate(series):
        length = min(item.values.size, most_common_length)
        values[partition, :length] = item.values[:length]
        values[partition, :length][item.mask[:length]] = np.nan

    majority_available = True
    for index in range(most_common_length):
        column = values[:, index]
        finite = np.isfinite(column)
        if not finite.any():
            majority_available = False
            for partition in range(len(series)):
                corrupt.setdefault(partition, set()).add(index)
            continue

        median = float(np.median(column[finite]))
        within_tolerance = finite & (np.abs(column - median) <= _TIME_TOLERANCE_S)
        if int(within_tolerance.sum()) <= len(series) // 2:
            majority_available = False
            for partition in range(len(series)):
                corrupt.setdefault(partition, set()).add(index)
            continue
        for partition, is_consistent in enumerate(within_tolerance):
            if not is_consistent:
                corrupt.setdefault(partition, set()).add(index)

    return majority_available


def _mark_mdu_time_bounds(
    series: tuple[_NumericSeries, ...],
    subrun: SubRun,
    corrupt: dict[int, set[int]],
) -> None:
    start = _numeric_time_bound(subrun.mdu.tstart)
    stop = _numeric_time_bound(subrun.mdu.tstop)
    if start is None and stop is None:
        return
    for partition, item in enumerate(series):
        values = item.values
        outside = np.zeros(values.shape, dtype=np.bool_)
        if start is not None:
            outside |= values < start - _TIME_TOLERANCE_S
        if stop is not None:
            outside |= values > stop + _TIME_TOLERANCE_S
        outside |= item.mask | ~np.isfinite(values)
        invalid_indices = np.flatnonzero(outside)
        if invalid_indices.size:
            corrupt.setdefault(partition, set()).update(
                int(index) for index in invalid_indices
            )


def _numeric_time_bound(value: float | str) -> float | None:
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
    else:
        parsed = float(value)
    return parsed if np.isfinite(parsed) else None


def _read_integer_variable(
    dataset: _NetCDFDataset,
    name: str,
) -> _IntegerArray | None:
    variable = dataset.variables.get(name)
    if variable is None:
        return None
    raw = np.ma.asarray(variable[...])
    if not np.issubdtype(raw.dtype, np.integer):
        raise ValueError(f"topology variable {name!r} must contain integers")
    values = np.asarray(raw.data, dtype=np.int64)
    mask = np.asarray(np.ma.getmaskarray(raw), dtype=np.bool_)
    return _IntegerArray(values=values, mask=mask)


def _read_numeric_series(
    dataset: _NetCDFDataset,
    name: str,
) -> _NumericSeries | None:
    variable = dataset.variables.get(name)
    if variable is None:
        return None
    raw = np.ma.asarray(variable[...])
    if not np.issubdtype(raw.dtype, np.number):
        raise ValueError(f"time variable {name!r} must contain numeric values")
    if raw.ndim != 1:
        raise ValueError(f"time variable {name!r} must be one-dimensional")
    values = np.asarray(raw.data, dtype=np.float64)
    mask = np.asarray(np.ma.getmaskarray(raw), dtype=np.bool_)
    return _NumericSeries(
        values=values,
        mask=mask,
        units=_attribute_text(variable, "units"),
    )


def _topology_signature(
    n_faces: int,
    domain: _IntegerArray | None,
    globalnr: _IntegerArray | None,
    face_nodes: _IntegerArray,
) -> str:
    digest = blake2b(digest_size=32)
    digest.update(b"mesh2d_nFaces\0")
    digest.update(int(n_faces).to_bytes(8, byteorder="little", signed=False))
    _hash_integer_array(digest, _FACE_DOMAIN, domain)
    _hash_integer_array(digest, _FACE_GLOBALNR, globalnr)
    _hash_integer_array(digest, _FACE_NODES, face_nodes)
    return digest.hexdigest()


def _hash_integer_array(
    digest: _HashDigest,
    name: str,
    array: _IntegerArray | None,
) -> None:
    digest.update(name.encode("utf-8") + b"\0")
    if array is None:
        digest.update(b"missing\0")
        return
    digest.update(b"present\0")
    shape = np.asarray(array.values.shape, dtype="<u8")
    digest.update(shape.tobytes())
    digest.update(np.ascontiguousarray(array.values, dtype="<i8").tobytes())
    digest.update(np.ascontiguousarray(array.mask, dtype=np.uint8).tobytes())


def _build_merge_plan(
    subrun: SubRun,
    metadata: _SubRunInspection,
    subrun_indices: tuple[int, ...],
    grid_file: str,
    ugrid: _UgridDataset,
    crs: str | None,
    fingerprint: str,
    grid_path: Path,
) -> tuple[_MergePlan, _XarrayDataset]:
    grid = ugrid.grid
    merged_dataset = ugrid.obj
    n_faces = int(grid.n_face)
    if n_faces <= 0:
        raise _failure(
            "V-OUT-020",
            grid_path,
            "The merged grid has no faces.",
            "Restore a valid two-dimensional UGRID mesh in the map files.",
        )

    local_faces: list[_Array] = []
    local_global_numbers: list[_Array] = []
    local_centres: list[_Array] = []
    for partition, path in enumerate(subrun.map_files):
        try:
            with (
                _NETCDF4_READ_LOCK,
                _netcdf4_module().Dataset(
                    path,
                    mode="r",
                ) as dataset,
            ):
                face_dimension = dataset.dimensions.get("mesh2d_nFaces")
                if face_dimension is None:
                    raise ValueError("required dimension 'mesh2d_nFaces' is missing")
                local_n_faces = len(face_dimension)
                domain = _read_integer_variable(dataset, _FACE_DOMAIN)
                if subrun.partitioned:
                    if domain is None or domain.values.shape != (local_n_faces,):
                        raise _failure(
                            "V-OUT-022",
                            path,
                            f"Could not read valid ownership from {_FACE_DOMAIN!r}.",
                            "Restore valid domain numbers for every partition face.",
                        )
                    owned = np.flatnonzero(domain.values == partition).astype(
                        np.int64,
                        copy=False,
                    )
                else:
                    owned = np.arange(local_n_faces, dtype=np.int64)
                local_faces.append(owned)

                if metadata.has_globalnr:
                    globalnr = _read_integer_variable(dataset, _FACE_GLOBALNR)
                    if globalnr is None or globalnr.values.shape != (local_n_faces,):
                        raise _failure(
                            "V-OUT-022",
                            path,
                            f"Could not read valid values from {_FACE_GLOBALNR!r}.",
                            "Restore global face numbers for every partition face.",
                        )
                    local_global_numbers.append(globalnr.values[owned])

                if not metadata.has_globalnr:
                    local_centres.append(
                        _read_face_centres(
                            dataset,
                            local_n_faces,
                            owned,
                            path,
                        )
                    )
        except _InspectionFailure:
            raise
        except (
            EOFError,
            IndexError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as error:
            raise _failure(
                "V-OUT-002",
                path,
                f"Could not read face identity for merge-plan construction: {error}",
                "Restore or regenerate this D-Flow FM map output.",
            ) from error

    if metadata.has_globalnr:
        merged_globalnr = _array_as_integer(
            merged_dataset.get(_FACE_GLOBALNR),
            _FACE_GLOBALNR,
            grid_path,
        )
        if merged_globalnr.shape != (n_faces,):
            raise _failure(
                "V-OUT-020",
                grid_path,
                "Merged global face numbers do not match the merged face dimension.",
                "Restore consistent global numbering in every map partition.",
            )
        expected_numbers = np.arange(1, n_faces + 1, dtype=np.int64)
        if not np.array_equal(np.sort(merged_globalnr), expected_numbers):
            raise _failure(
                "V-OUT-020",
                grid_path,
                (
                    "Merged global face numbers are not unique and complete "
                    "from 1 through n_faces."
                ),
                "Restore consistent global numbering in every map partition.",
            )
        position_by_number = np.full(n_faces + 1, -1, dtype=np.int64)
        position_by_number[merged_globalnr] = np.arange(n_faces, dtype=np.int64)
        global_positions = [
            position_by_number[numbers] for numbers in local_global_numbers
        ]
        matching = "globalnr"
    else:
        merged_centres = _merged_face_centres(
            merged_dataset,
            n_faces,
            grid_path,
        )
        spatial = cast(_ScipySpatialModule, import_module("scipy.spatial"))
        tree = spatial.cKDTree(merged_centres)
        global_positions = []
        for path, centres in zip(subrun.map_files, local_centres, strict=True):
            distances, candidates = tree.query(
                centres,
                k=2,
                distance_upper_bound=_COORDINATE_TOLERANCE_M,
                workers=1,
            )
            distances = np.asarray(distances, dtype=np.float64)
            candidates = np.asarray(candidates, dtype=np.int64)
            if distances.ndim == 1:
                distances = distances.reshape((-1, 2))
                candidates = candidates.reshape((-1, 2))
            missing = distances[:, 0] > _COORDINATE_TOLERANCE_M
            ambiguous = distances[:, 1] <= _COORDINATE_TOLERANCE_M
            if missing.any() or ambiguous.any():
                raise _failure(
                    "V-OUT-020",
                    path,
                    (
                        "Coordinate-based face matching is not one-to-one "
                        f"({int(missing.sum())} missing, "
                        f"{int(ambiguous.sum())} ambiguous)."
                    ),
                    "Restore unique face centres within 1 mm of the merged grid.",
                )
            global_positions.append(candidates[:, 0])
        matching = "coordinates"

    partition_n_owned = np.asarray(
        [faces.size for faces in local_faces],
        dtype=np.int64,
    )
    partition_offset = np.zeros(len(local_faces), dtype=np.int64)
    if len(local_faces) > 1:
        partition_offset[1:] = np.cumsum(partition_n_owned[:-1])
    owned_local = (
        np.concatenate(local_faces).astype(np.int64, copy=False)
        if local_faces
        else np.empty(0, dtype=np.int64)
    )
    global_pos = (
        np.concatenate(global_positions).astype(np.int64, copy=False)
        if global_positions
        else np.empty(0, dtype=np.int64)
    )
    if (
        owned_local.size != n_faces
        or global_pos.size != n_faces
        or np.any(global_pos < 0)
        or np.any(global_pos >= n_faces)
    ):
        raise _failure(
            "V-OUT-020",
            grid_path,
            "Owned partition faces do not have a valid one-to-one merged-grid mapping.",
            "Restore consistent face ownership and global numbering or coordinates.",
        )
    coverage = np.bincount(global_pos, minlength=n_faces)
    if coverage.size != n_faces or not np.all(coverage == 1):
        missing = int(np.count_nonzero(coverage == 0))
        duplicated = int(np.count_nonzero(coverage > 1))
        raise _failure(
            "V-OUT-020",
            grid_path,
            (
                "Merge plan does not cover every merged face exactly once "
                f"({missing} missing, {duplicated} duplicated)."
            ),
            "Restore consistent partition ownership and face identity.",
        )

    grid_dataset = _grid_dataset(ugrid, crs, fingerprint, grid_file)
    plan = _MergePlan(
        subrun_indices=subrun_indices,
        reference_subrun=subrun.index,
        matching=matching,
        grid_file=grid_file,
        n_faces=n_faces,
        n_edges=int(grid.n_edge),
        partition_offset=partition_offset,
        partition_n_owned=partition_n_owned,
        owned_local=owned_local,
        global_pos=global_pos,
    )
    return plan, grid_dataset


def _read_face_centres(
    dataset: _NetCDFDataset,
    n_faces: int,
    owned: _Array,
    path: Path,
) -> _Array:
    x_variable = dataset.variables.get("mesh2d_face_x")
    y_variable = dataset.variables.get("mesh2d_face_y")
    if x_variable is None or y_variable is None:
        raise _failure(
            "V-OUT-020",
            path,
            "Coordinate-based matching requires mesh2d_face_x and mesh2d_face_y.",
            "Restore valid face-centre coordinates in every map file.",
        )
    centres = _combine_face_centres(
        x_variable[...],
        y_variable[...],
        n_faces,
        path,
    )
    return centres[owned]


def _merged_face_centres(
    dataset: _XarrayDataset,
    n_faces: int,
    path: Path,
) -> _Array:
    x_variable = dataset.get("mesh2d_face_x")
    y_variable = dataset.get("mesh2d_face_y")
    if x_variable is None or y_variable is None:
        raise _failure(
            "V-OUT-020",
            path,
            "Merged face-centre coordinates are missing.",
            "Restore valid face-centre coordinates in the merged grid.",
        )
    return _combine_face_centres(
        x_variable.values,
        y_variable.values,
        n_faces,
        path,
    )


def _combine_face_centres(
    x_values: object,
    y_values: object,
    n_faces: int,
    path: Path,
) -> _Array:
    x_raw = np.ma.asarray(x_values)
    y_raw = np.ma.asarray(y_values)
    if (
        x_raw.shape != (n_faces,)
        or y_raw.shape != (n_faces,)
        or np.ma.getmaskarray(x_raw).any()
        or np.ma.getmaskarray(y_raw).any()
    ):
        raise _failure(
            "V-OUT-020",
            path,
            "Coordinate-based matching found masked or invalid face centres.",
            "Restore finite face-centre coordinates for every face.",
        )
    centres = np.column_stack(
        (
            np.asarray(x_raw.data, dtype=np.float64),
            np.asarray(y_raw.data, dtype=np.float64),
        )
    )
    if not np.isfinite(centres).all():
        raise _failure(
            "V-OUT-020",
            path,
            "Coordinate-based matching found non-finite face centres.",
            "Restore finite face-centre coordinates for every face.",
        )
    return centres


def _grid_dataset(
    ugrid: _UgridDataset,
    crs: str | None,
    fingerprint: str,
    grid_file: str,
) -> _XarrayDataset:
    grid = ugrid.grid
    current_crs = grid.crs
    if crs is not None and _is_epsg_zero(current_crs):
        grid.set_crs(crs=crs, allow_override=True)
    effective_crs = (
        crs
        if crs is not None and _is_epsg_zero(current_crs)
        else _crs_label(current_crs)
    )
    result = grid.to_dataset()

    for name in ("mesh2d_flowelem_ba", "mesh2d_face_x", "mesh2d_face_y"):
        variable = ugrid.obj.get(name)
        if variable is None:
            if name == "mesh2d_flowelem_ba":
                raise _failure(
                    "V-OUT-002",
                    grid_file,
                    f"Required face-area variable {name!r} is missing.",
                    "Restore face areas in the map files.",
                )
            centres = np.asarray(grid.face_coordinates, dtype=np.float64)
            coordinate_index = 0 if name.endswith("_x") else 1
            result[name] = xr.DataArray(
                centres[:, coordinate_index],
                dims=(grid.face_dimension,),
            )
        else:
            result[name] = variable.copy(deep=True)

    face_nodes = np.asarray(grid.face_node_connectivity, dtype=np.int64)
    face_edges = np.asarray(grid.face_edge_connectivity, dtype=np.int64)
    edge_nodes = np.asarray(grid.edge_node_connectivity, dtype=np.int64)
    if face_nodes.shape != face_edges.shape:
        raise _failure(
            "V-OUT-020",
            grid_file,
            "Face-node and face-edge connectivity have different shapes.",
            "Restore a valid UGRID topology in the map files.",
        )
    face_edge_sign = _face_edge_sign(face_nodes, face_edges, edge_nodes, grid_file)
    max_edges_dimension = "max_edges"
    result["face_edges"] = xr.DataArray(
        face_edges.astype(np.int32, copy=False),
        dims=(grid.face_dimension, max_edges_dimension),
    )
    result["face_edge_sign"] = xr.DataArray(
        face_edge_sign,
        dims=(grid.face_dimension, max_edges_dimension),
    )
    result["edge_length"] = xr.DataArray(
        np.asarray(grid.edge_length, dtype=np.float64),
        dims=(grid.edge_dimension,),
        attrs={"units": "m"},
    )
    result.attrs.update(
        {
            "crs": effective_crs,
            "d3dplot_fingerprint": fingerprint,
            "d3dplot_parameters": f'{{"grid_file": "{grid_file}"}}',
            "d3dplot_version": __version__,
            "d3dplot_cache_schema": _CACHE_SCHEMA_VERSION,
        }
    )
    return result


def _face_edge_sign(
    face_nodes: _Array,
    face_edges: _Array,
    edge_nodes: _Array,
    location: str | Path,
) -> _Array:
    signs = np.zeros(face_edges.shape, dtype=np.int8)
    for face_index, nodes in enumerate(face_nodes):
        valid_slots = np.flatnonzero(nodes >= 0)
        n_nodes = valid_slots.size
        if n_nodes < 3:
            raise _failure(
                "V-OUT-020",
                location,
                f"Face {face_index} has fewer than three valid nodes.",
                "Restore a valid UGRID face-node connectivity array.",
            )
        for slot in range(n_nodes):
            edge_slot = int(valid_slots[slot])
            edge_index = int(face_edges[face_index, edge_slot])
            if edge_index < 0 or edge_index >= edge_nodes.shape[0]:
                raise _failure(
                    "V-OUT-020",
                    location,
                    f"Face {face_index} has an invalid edge index.",
                    "Restore a valid UGRID face-edge connectivity array.",
                )
            start_node = int(nodes[valid_slots[slot]])
            end_node = int(nodes[valid_slots[(slot + 1) % n_nodes]])
            edge_start, edge_end = (int(value) for value in edge_nodes[edge_index])
            if (edge_start, edge_end) == (start_node, end_node):
                signs[face_index, edge_slot] = 1
            elif (edge_start, edge_end) == (end_node, start_node):
                signs[face_index, edge_slot] = -1
            else:
                raise _failure(
                    "V-OUT-020",
                    location,
                    (
                        f"Face {face_index} edge {edge_index} does not agree "
                        "with its face-node connectivity."
                    ),
                    "Restore a valid UGRID edge orientation.",
                )
    return signs


def _merge_plan_dataset(
    simulation: Simulation,
    subrun_metadata: Sequence[_SubRunInspection],
    plans: Sequence[_MergePlan],
    reference_subrun: int,
    fingerprint: str,
) -> _XarrayDataset:
    n_subruns = len(simulation.subruns)
    n_partitions = simulation.subruns[0].partition_count
    max_owned_faces = max(plan.n_faces for plan in plans)
    plan_of_subrun = np.full(n_subruns, -1, dtype=np.int32)
    owned_local = np.full((len(plans), max_owned_faces), -1, dtype=np.int64)
    global_pos = np.full_like(owned_local, -1)
    partition_offset = np.zeros((len(plans), n_partitions), dtype=np.int64)
    partition_n_owned = np.zeros_like(partition_offset)
    subrun_indices = np.asarray(
        [subrun.index for subrun in simulation.subruns],
        dtype=np.int32,
    )
    signatures = np.asarray(
        [metadata.signatures for metadata in subrun_metadata],
        dtype=str,
    )
    plan_n_faces = np.asarray([plan.n_faces for plan in plans], dtype=np.int64)
    plan_n_edges = np.asarray([plan.n_edges for plan in plans], dtype=np.int64)
    reference_by_plan = np.asarray(
        [plan.reference_subrun for plan in plans],
        dtype=np.int32,
    )
    matching_by_plan = np.asarray([plan.matching for plan in plans], dtype=str)
    grid_files = np.asarray([plan.grid_file for plan in plans], dtype=str)

    for plan_index, plan in enumerate(plans):
        for subrun_index in plan.subrun_indices:
            try:
                position = subrun_indices.tolist().index(subrun_index)
            except ValueError as error:
                raise _failure(
                    "V-OUT-020",
                    simulation.root,
                    f"Merge plan references unknown sub-run {subrun_index}.",
                    "Restore the simulation's contiguous sub-run numbering.",
                ) from error
            plan_of_subrun[position] = plan_index
        partition_offset[plan_index] = plan.partition_offset
        partition_n_owned[plan_index] = plan.partition_n_owned
        owned_local[plan_index, : plan.owned_local.size] = plan.owned_local
        global_pos[plan_index, : plan.global_pos.size] = plan.global_pos

    if np.any(plan_of_subrun < 0):
        raise _failure(
            "V-OUT-020",
            simulation.root,
            "At least one sub-run is not assigned to a merge plan.",
            "Restore valid topology signatures for every sub-run.",
        )

    dataset = xr.Dataset(
        data_vars={
            "plan_of_subrun": (("subrun",), plan_of_subrun),
            "partition_offset": (("plan", "partition"), partition_offset),
            "partition_n_owned": (("plan", "partition"), partition_n_owned),
            "owned_local": (("plan", "owned_face"), owned_local),
            "global_pos": (("plan", "owned_face"), global_pos),
            "signature": (("subrun", "partition"), signatures),
            "subrun_index": (("subrun",), subrun_indices),
            "plan_n_faces": (("plan",), plan_n_faces),
            "plan_n_edges": (("plan",), plan_n_edges),
            "reference_subrun_plan": (("plan",), reference_by_plan),
            "matching_plan": (("plan",), matching_by_plan),
            "grid_file": (("plan",), grid_files),
        },
        coords={
            "plan": np.arange(len(plans), dtype=np.int32),
            "partition": np.arange(n_partitions, dtype=np.int32),
            "subrun": np.arange(n_subruns, dtype=np.int32),
            "owned_face": np.arange(max_owned_faces, dtype=np.int64),
        },
        attrs={
            "n_faces": plans[0].n_faces,
            "n_edges": plans[0].n_edges,
            "reference_subrun": reference_subrun,
            "matching": (plans[0].matching),
            "d3dplot_fingerprint": fingerprint,
            "d3dplot_parameters": '{"kind": "merge_plan"}',
            "d3dplot_version": __version__,
            "d3dplot_cache_schema": _CACHE_SCHEMA_VERSION,
        },
    )
    return dataset


def _cache_is_current(
    merge_plan_path: Path,
    grid_paths: Sequence[Path],
    fingerprint: str,
    guard: OutputWriteGuard,
) -> tuple[bool, str | None]:
    cache_paths = (merge_plan_path, *grid_paths)
    existing = tuple(path.exists() for path in cache_paths)
    if not any(existing):
        return False, None
    if not all(existing):
        return False, "one or more cache files are missing"

    for path in cache_paths:
        target = guard.validate_target(path)
        try:
            with (
                _NETCDF4_READ_LOCK,
                _netcdf4_module().Dataset(
                    target,
                    mode="r",
                ) as dataset,
            ):
                cached_fingerprint = _attribute_text(dataset, "d3dplot_fingerprint")
                schema = dataset.getncattr("d3dplot_cache_schema")
        except (
            EOFError,
            IndexError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as error:
            return False, f"{path.name} could not be read ({error})"
        if not isinstance(schema, Integral):
            return False, f"{path.name} has an invalid cache-schema attribute"
        cache_schema = int(schema)
        if cached_fingerprint != fingerprint or cache_schema != _CACHE_SCHEMA_VERSION:
            return False, None
    return True, None


def _write_netcdf(
    path: Path,
    dataset: _XarrayDataset,
    guard: OutputWriteGuard,
) -> None:
    with atomic_path(path, guard) as temporary:
        dataset.to_netcdf(
            temporary,
            engine="netcdf4",
            format="NETCDF4",
        )


def _open_merged_dataset(subrun: SubRun) -> _UgridDataset:
    module = cast(_DfmToolsModule, import_module("dfm_tools.xugrid_helpers"))
    open_dataset = module.open_partitioned_dataset
    pattern = _partition_glob(subrun)
    with redirect_stdout(StringIO()):
        return open_dataset(
            pattern,
            drop_variables=["morft"],
            decode_times=False,
        )


def _partition_glob(subrun: SubRun) -> str:
    if not subrun.partitioned:
        return str(subrun.map_files[0])
    first_file = subrun.map_files[0]
    match = _PARTITION_NAME.fullmatch(first_file.name)
    if match is None:
        raise ValueError(f"Unexpected partition map-file name: {first_file.name}")
    prefix = match.group(1)
    suffix = match.group(3)
    return str(first_file.parent / f"{prefix}_????{suffix}")


def _array_as_integer(
    variable: _XarrayDataArray | None,
    name: str,
    location: str | Path,
) -> _Array:
    if variable is None:
        raise _failure(
            "V-OUT-020",
            location,
            f"Merged grid is missing {name!r}.",
            "Restore global face numbers in every map partition.",
        )
    raw = np.ma.asarray(variable.values)
    if (
        not np.issubdtype(raw.dtype, np.number)
        or np.ma.getmaskarray(raw).any()
        or not np.isfinite(np.asarray(raw.data, dtype=np.float64)).all()
    ):
        raise _failure(
            "V-OUT-020",
            location,
            f"Merged variable {name!r} contains invalid values.",
            "Restore valid global face numbers in every map partition.",
        )
    values = np.asarray(raw.data, dtype=np.float64)
    if np.any(values != np.floor(values)):
        raise _failure(
            "V-OUT-020",
            location,
            f"Merged variable {name!r} contains non-integer values.",
            "Restore valid global face numbers in every map partition.",
        )
    return values.astype(np.int64)


def _grid_file_name(plan_index: int) -> str:
    return "grid.nc" if plan_index == 0 else f"grid_plan_{plan_index:04d}.nc"


def _netcdf4_module() -> _NetCDF4Module:
    os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")
    return cast(_NetCDF4Module, import_module("netCDF4"))


def _attribute_text(
    target: _NetCDFDataset | _NetCDFVariable,
    name: str,
) -> str | None:
    available = {attribute.casefold(): attribute for attribute in target.ncattrs()}
    actual_name = available.get(name.casefold())
    if actual_name is None:
        return None
    value = target.getncattr(actual_name)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return str(value).strip()


def _is_epsg_zero(crs: object) -> bool:
    code = getattr(crs, "EPSG_code", None)
    if code is not None and str(code).strip().casefold() == "epsg:0":
        return True
    to_epsg = getattr(crs, "to_epsg", None)
    return callable(to_epsg) and to_epsg() == 0


def _crs_label(crs: object) -> str:
    if crs is None:
        return ""
    code = getattr(crs, "EPSG_code", None)
    if code is not None:
        return str(code)
    to_string = getattr(crs, "to_string", None)
    if callable(to_string):
        return str(to_string())
    return str(crs)


def _issue(
    code: str,
    level: Literal["error", "warning"],
    location: str | Path,
    message: str,
    hint: str,
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        level=level,
        location=str(location),
        message=message,
        hint=hint,
    )


def _failure(
    code: str,
    location: str | Path,
    message: str,
    hint: str,
) -> _InspectionFailure:
    return _InspectionFailure(
        ValidationIssue(
            code=code,
            level="error",
            location=str(location),
            message=message,
            hint=hint,
        )
    )


def _has_errors(issues: Sequence[ValidationIssue]) -> bool:
    return any(issue.level == "error" for issue in issues)
