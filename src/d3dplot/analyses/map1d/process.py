# SPDX-License-Identifier: GPL-3.0-or-later
"""map1d branch-chain geometry and quantity reading."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import netCDF4
import numpy as np

from d3dplot.river_km.file import read_river_km_points
from d3dplot.simulation.map_reader import MapReadResult
from d3dplot.simulation.time_axis import TimeEntry
from d3dplot.simulation.time_utils import format_hydrodynamic_timestamp
from d3dplot.store import (
    CACHE_SCHEMA_VERSION,
    OutputLayout,
    OutputWriteGuard,
    atomic_path,
    fingerprint_artifact,
)
from d3dplot.store.paths import OutputArea, build_quantity_output_path
from d3dplot.variables.context import QuantityContext
from d3dplot.variables.engine import QuantityEngine
from d3dplot.variables.registry import (
    VariableSpec,
    get_variable_spec,
)


class Map1DProcessError(ValueError):
    """A map1d grid or quantity cannot be processed."""


_MAP1D_CACHE_ALGORITHM_VERSION = "1"


@dataclass(frozen=True, slots=True)
class Map1DChain:
    """Ordered one-dimensional coordinates and source indices for one branch chain."""

    name: str
    branch_ids: tuple[str, ...]
    indices: np.ndarray
    offsets_m: np.ndarray
    x: np.ndarray
    y: np.ndarray
    location: str


@dataclass(frozen=True, slots=True)
class Map1DGrid:
    """Static network and mesh metadata from a D-Flow FM 1D map file."""

    branch_ids: tuple[str, ...]
    branch_lengths_m: np.ndarray
    node_branch: np.ndarray
    node_offset_m: np.ndarray
    node_x: np.ndarray
    node_y: np.ndarray
    edge_branch: np.ndarray
    edge_offset_m: np.ndarray
    edge_x: np.ndarray
    edge_y: np.ndarray

    @classmethod
    def from_map(cls, path: str | Path) -> Map1DGrid:
        """Read static 1D network metadata without loading time-varying fields."""
        try:
            with netCDF4.Dataset(path, mode="r") as dataset:
                required = {
                    "network1d_branch_id",
                    "network1d_edge_length",
                    "mesh1d_node_branch",
                    "mesh1d_node_offset",
                    "mesh1d_node_x",
                    "mesh1d_node_y",
                    "mesh1d_edge_branch",
                    "mesh1d_edge_offset",
                    "mesh1d_edge_x",
                    "mesh1d_edge_y",
                }
                missing = required.difference(dataset.variables)
                if missing:
                    raise Map1DProcessError(
                        "map1d map is missing variable(s): "
                        + ", ".join(sorted(missing))
                    )
                branch_ids = _decode_strings(
                    dataset.variables["network1d_branch_id"][:]
                )
                edge_lengths = np.asarray(
                    dataset.variables["network1d_edge_length"][:],
                    dtype=np.float64,
                )
                node_branch = np.asarray(
                    dataset.variables["mesh1d_node_branch"][:], dtype=np.int64
                )
                node_offset = np.asarray(
                    dataset.variables["mesh1d_node_offset"][:], dtype=np.float64
                )
                node_x = np.asarray(
                    dataset.variables["mesh1d_node_x"][:], dtype=np.float64
                )
                node_y = np.asarray(
                    dataset.variables["mesh1d_node_y"][:], dtype=np.float64
                )
                edge_branch = np.asarray(
                    dataset.variables["mesh1d_edge_branch"][:], dtype=np.int64
                )
                edge_offset = np.asarray(
                    dataset.variables["mesh1d_edge_offset"][:], dtype=np.float64
                )
                edge_x = np.asarray(
                    dataset.variables["mesh1d_edge_x"][:], dtype=np.float64
                )
                edge_y = np.asarray(
                    dataset.variables["mesh1d_edge_y"][:], dtype=np.float64
                )
        except Map1DProcessError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise Map1DProcessError(
                f"Could not read map1d map {path}: {error}"
            ) from error

        if not branch_ids or any(not value for value in branch_ids):
            raise Map1DProcessError(
                "map1d map contains an empty network branch identifier."
            )
        if edge_lengths.ndim != 1 or edge_lengths.size != len(branch_ids):
            raise Map1DProcessError(
                "map1d branch lengths do not match branch identifiers."
            )
        _validate_geometry_arrays(
            node_branch,
            node_offset,
            node_x,
            node_y,
            len(branch_ids),
            "node",
        )
        _validate_geometry_arrays(
            edge_branch,
            edge_offset,
            edge_x,
            edge_y,
            len(branch_ids),
            "edge",
        )
        return cls(
            branch_ids=branch_ids,
            branch_lengths_m=edge_lengths,
            node_branch=node_branch,
            node_offset_m=node_offset,
            node_x=node_x,
            node_y=node_y,
            edge_branch=edge_branch,
            edge_offset_m=edge_offset,
            edge_x=edge_x,
            edge_y=edge_y,
        )

    def chain(
        self,
        name: str,
        branches: tuple[str, ...] | list[str],
        *,
        location: str = "node",
    ) -> Map1DChain:
        """Build an ordered chain from network branch identifiers."""
        if location not in {"node", "edge"}:
            raise Map1DProcessError(f"Unsupported map1d location: {location!r}.")
        requested = tuple(branches)
        if not requested or any(not branch.strip() for branch in requested):
            raise Map1DProcessError("map1d branch chains must not be empty.")
        lookup = {
            branch.casefold(): index for index, branch in enumerate(self.branch_ids)
        }
        try:
            branch_indices = tuple(lookup[branch.casefold()] for branch in requested)
        except KeyError as error:
            raise Map1DProcessError(
                f"Unknown map1d network branch {error.args[0]!r}; "
                f"available branches: {', '.join(self.branch_ids)}."
            ) from error

        if location == "node":
            branch_values = self.node_branch
            offsets = self.node_offset_m
            x_values = self.node_x
            y_values = self.node_y
        else:
            branch_values = self.edge_branch
            offsets = self.edge_offset_m
            x_values = self.edge_x
            y_values = self.edge_y

        selected_indices: list[int] = []
        selected_offsets: list[float] = []
        selected_x: list[float] = []
        selected_y: list[float] = []
        cumulative = 0.0
        for branch_index in branch_indices:
            local = np.flatnonzero(branch_values == branch_index)
            local = local[np.argsort(offsets[local], kind="stable")]
            if local.size == 0:
                raise Map1DProcessError(
                    f"map1d branch {self.branch_ids[branch_index]!r} has no "
                    f"{location}s."
                )
            selected_indices.extend(int(value) for value in local)
            selected_offsets.extend(
                float(value) for value in offsets[local] + cumulative
            )
            selected_x.extend(float(value) for value in x_values[local])
            selected_y.extend(float(value) for value in y_values[local])
            cumulative += float(self.branch_lengths_m[branch_index])

        indices = np.asarray(selected_indices, dtype=np.int64)
        offsets_array = np.asarray(selected_offsets, dtype=np.float64)
        keep = np.r_[True, np.diff(offsets_array) > 0]
        return Map1DChain(
            name=name,
            branch_ids=requested,
            indices=indices[keep],
            offsets_m=offsets_array[keep],
            x=np.asarray(selected_x, dtype=np.float64)[keep],
            y=np.asarray(selected_y, dtype=np.float64)[keep],
            location=location,
        )


@dataclass(frozen=True, slots=True)
class Map1DMapSession:
    """Read one selected time from a 1D map while reusing its open dataset."""

    dataset: Any
    time_index: int
    indices: np.ndarray | None = None
    subrun_index: int = 0

    def has_variable(self, spec: VariableSpec) -> bool:
        """Return whether a candidate for the quantity exists in this map."""
        return any(
            candidate in self.dataset.variables for candidate in spec.netcdf_candidates
        )

    def read(
        self,
        spec: VariableSpec,
        *,
        local_index: int,
        corrupt_partitions: Sequence[int] = (),
    ) -> MapReadResult:
        """Read a raw one-dimensional quantity at the selected time."""
        del local_index, corrupt_partitions
        candidate = next(
            (name for name in spec.netcdf_candidates if name in self.dataset.variables),
            None,
        )
        if candidate is None:
            raise Map1DProcessError(
                f"Quantity {spec.name!r} is not available in the map1d map."
            )
        variable = self.dataset.variables[candidate]
        dimensions = tuple(variable.dimensions)
        if "time" in dimensions:
            time_axis = dimensions.index("time")
            index: list[slice | int] = [slice(None)] * len(dimensions)
            index[time_axis] = self.time_index
            values = np.asarray(variable[tuple(index)], dtype=np.float64)
        else:
            values = np.asarray(variable[:], dtype=np.float64)
        if self.indices is not None and values.ndim == 1:
            if np.any(self.indices < 0) or np.any(self.indices >= values.size):
                raise Map1DProcessError(
                    f"map1d source indices exceed {spec.name!r} values."
                )
            values = values[self.indices]
        return MapReadResult(values=values, edge_orientation=None, issues=())


def read_map1d_quantity_series(
    path: str | Path,
    variable: str,
    chain: Map1DChain,
    time_indices: tuple[int, ...],
) -> np.ndarray:
    """Read one quantity along a chain for selected map-time indices."""
    if not time_indices:
        raise Map1DProcessError("map1d quantity reading needs at least one time index.")
    engine = QuantityEngine(load_plugins=False)
    values: list[np.ndarray] = []
    with netCDF4.Dataset(path, mode="r") as dataset:
        for time_index in time_indices:
            session = Map1DMapSession(
                dataset,
                time_index=time_index,
                indices=chain.indices,
            )
            entry = TimeEntry(
                time_index=time_index,
                subrun=0,
                local_index=time_index,
                time=datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=time_index),
                morph_time=None,
                corrupt_partitions=(),
            )
            quantity = engine.at_time(session, entry, QuantityContext())
            values.append(np.asarray(quantity.get(variable), dtype=np.float64))
    result = np.stack(values, axis=0)
    if result.ndim != 2 or result.shape[1] != chain.indices.size:
        raise Map1DProcessError(
            f"map1d quantity {variable!r} did not return one value per chain point."
        )
    return result


def read_map1d_river_km_coordinates(
    chain: Map1DChain,
    path: str | Path,
    *,
    tolerance_m: float,
) -> np.ndarray:
    """Map chain coordinates to nearest river-km points."""
    points = read_river_km_points(path)
    branch_names = {branch.casefold() for branch in chain.branch_ids}
    filtered = tuple(
        point for point in points if point.branch.casefold() in branch_names
    )
    candidates = filtered or points
    coordinates = np.asarray([(point.x, point.y) for point in candidates])
    chain_coordinates = np.column_stack((chain.x, chain.y))
    distances = np.sqrt(
        np.sum(
            (chain_coordinates[:, np.newaxis, :] - coordinates[np.newaxis, :, :]) ** 2,
            axis=2,
        )
    )
    nearest = np.argmin(distances, axis=1)
    nearest_distances = distances[np.arange(chain_coordinates.shape[0]), nearest]
    if np.any(nearest_distances > tolerance_m):
        maximum = float(np.max(nearest_distances))
        raise Map1DProcessError(
            f"map1d chain {chain.name!r} has coordinates farther than "
            f"{tolerance_m:g} m from the river-km file (maximum {maximum:g} m)."
        )
    return np.asarray([candidates[index].km for index in nearest], dtype=np.float64)


def map1d_cache_fingerprint(
    *,
    input_fingerprint: str,
    variable: str,
    chain: Map1DChain,
    time: datetime,
) -> str:
    """Build the identity of one persisted map1d time slice."""
    return fingerprint_artifact(
        kind="map1d",
        schema_version=CACHE_SCHEMA_VERSION,
        tool_version="d3dplot",
        algorithm_version=_MAP1D_CACHE_ALGORITHM_VERSION,
        parameters={
            "variable": get_variable_spec(variable).name,
            "chain": chain.name,
            "branch_ids": list(chain.branch_ids),
            "location": chain.location,
        },
        inputs=[input_fingerprint],
        time=time.isoformat(),
    )


def map1d_cache_path(
    layout: OutputLayout,
    *,
    variable: str,
    chain_name: str,
    time: datetime,
) -> Path:
    """Return one guarded per-time map1d cache path."""
    filename = format_hydrodynamic_timestamp(time)
    return build_quantity_output_path(
        layout,
        OutputArea.CACHE_DATA,
        variable,
        prefix=("map1d",),
        suffix=(chain_name,),
        filename=f"{filename}.nc",
    )


def write_map1d_cache(
    path: Path,
    guard: OutputWriteGuard,
    *,
    values: np.ndarray,
    chain: Map1DChain,
    variable: str,
    time: datetime,
    fingerprint: str,
) -> None:
    """Write one atomic, fingerprinted map1d time slice."""
    data = np.asarray(values, dtype=np.float64)
    if data.ndim != 1 or data.size != chain.indices.size:
        raise Map1DProcessError("map1d cache values must match the chain coordinates.")
    with atomic_path(path, guard) as temporary:
        with netCDF4.Dataset(temporary, mode="w", format="NETCDF4") as dataset:
            dataset.createDimension("point", data.size)
            for name, array in (
                ("offset_m", chain.offsets_m),
                ("x", chain.x),
                ("y", chain.y),
                ("values", data),
            ):
                variable_object = dataset.createVariable(name, "f8", ("point",))
                variable_object[:] = array
            dataset.setncattr("d3dplot_fingerprint", fingerprint)
            dataset.setncattr("variable", variable)
            dataset.setncattr("chain", chain.name)
            dataset.setncattr("time", time.isoformat())
            dataset.setncattr("location", chain.location)


def read_map1d_cache(
    path: Path,
    *,
    expected_fingerprint: str,
    expected_size: int,
) -> np.ndarray:
    """Read and verify one persisted map1d time slice."""
    try:
        with netCDF4.Dataset(path, mode="r") as dataset:
            if dataset.getncattr("d3dplot_fingerprint") != expected_fingerprint:
                raise Map1DProcessError(f"map1d cache fingerprint changed: {path}.")
            values = np.asarray(dataset.variables["values"][:], dtype=np.float64)
    except Map1DProcessError:
        raise
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise Map1DProcessError(
            f"Could not read map1d cache {path}: {error}"
        ) from error
    if values.shape != (expected_size,):
        raise Map1DProcessError(f"map1d cache has invalid point count: {path}.")
    return values


def _decode_strings(values: Any) -> tuple[str, ...]:
    array = np.asarray(values)
    if array.ndim == 1:
        return tuple(_decode_scalar(value) for value in array)
    return tuple(
        "".join(_decode_scalar(value) for value in row).strip() for row in array
    )


def _decode_scalar(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, np.bytes_):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value)


def _validate_geometry_arrays(
    branch: np.ndarray,
    offsets: np.ndarray,
    x_values: np.ndarray,
    y_values: np.ndarray,
    branch_count: int,
    location: str,
) -> None:
    if not (branch.ndim == offsets.ndim == x_values.ndim == y_values.ndim == 1) or not (
        branch.size == offsets.size == x_values.size == y_values.size
    ):
        raise Map1DProcessError(
            f"map1d {location} geometry arrays have inconsistent shapes."
        )
    if np.any((branch < 0) | (branch >= branch_count)):
        raise Map1DProcessError(
            f"map1d {location} geometry contains an unknown branch."
        )
    if (
        not np.isfinite(offsets).all()
        or not np.isfinite(x_values).all()
        or not np.isfinite(y_values).all()
    ):
        raise Map1DProcessError(
            f"map1d {location} geometry contains non-finite values."
        )
