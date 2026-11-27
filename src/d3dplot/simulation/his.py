# SPDX-License-Identifier: GPL-3.0-or-later
"""Observation-station (HIS) time series from D-Flow FM and Delft3D 4 output.

An FM run writes one ``*_his.nc`` file in its MDU ``OutputDir`` (default
``DFM_OUTPUT_<name>``). A D3D4 run writes one
``trih-<name>.dat`` file with its ``.def`` file, next to ``<name>.mdf``. Both readers
return UTC epoch seconds and float64 values for one station and one variable.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any

import netCDF4
import numpy as np
from numpy.typing import NDArray

from d3dplot.model_input.mdu import MduInfo
from d3dplot.simulation.nefis import NefisFile
from d3dplot.simulation.time_utils import (
    decode_cf_time,
    timezone_from_cf_units,
    timezone_from_hours,
)

FLOAT = np.float64
_TRIH_PREFIX = "trih-"
_CONST_GROUP = "his-const"
_INFO_GROUP = "his-info-series"
_SERIES_GROUP = "his-series"
FM_VARIABLES: dict[str, str] = {
    "water_level": "waterlevel",
    "water_depth": "waterdepth",
    "salinity": "salinity",
}
D3D4_VARIABLES: tuple[str, ...] = (
    "water_level",
    "water_depth",
    "bed_level",
    "velocity_magnitude",
)
_SURFACE_VARIABLES = frozenset({"water_level", "water_depth", "bed_level"})


class HisError(ValueError):
    """A HIS input problem, with the stable validation code of the plan (section 12)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code


@dataclass(frozen=True, slots=True)
class HisSeries:
    """One station series: UTC epoch seconds and float64 values (NaN where masked)."""

    time_seconds: NDArray[np.float64]
    values: NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class FmHisFile:
    """The ``*_his.nc`` file of one plain FM run."""

    data_path: Path


@dataclass(frozen=True, slots=True)
class TrihHisFile:
    """The ``trih-<name>.dat`` file of one D3D4 run with its ``.def`` and ``.mdf``."""

    data_path: Path
    def_path: Path
    mdf_path: Path


def locate_his_file(root: Path) -> FmHisFile | TrihHisFile:
    """Find the single HIS file of a simulation folder.

    A D3D4 ``trih`` file takes precedence over FM output (see ``_fm_output_dir``).
    """
    trih_paths = sorted(root.glob(f"{_TRIH_PREFIX}*.dat"))
    if trih_paths:
        if len(trih_paths) > 1:
            raise HisError(
                "V-HIS-005",
                f"{root} has {len(trih_paths)} trih files; keep one run per folder.",
            )
        data_path = trih_paths[0]
        name = data_path.stem[len(_TRIH_PREFIX) :]
        def_path = root / f"{_TRIH_PREFIX}{name}.def"
        mdf_path = root / f"{name}.mdf"
        if not def_path.is_file() or not mdf_path.is_file():
            raise HisError(
                "V-HIS-005",
                f"{data_path.name} needs {def_path.name} and {mdf_path.name} "
                "beside it.",
            )
        return TrihHisFile(data_path, def_path, mdf_path)

    output_dir = _fm_output_dir(root)
    his_paths = sorted(output_dir.glob("*_his.nc"))
    if len(his_paths) == 1:
        return FmHisFile(his_paths[0])
    if not his_paths:
        raise HisError("V-HIS-005", f"No HIS output found in {output_dir}.")
    raise HisError("V-HIS-005", f"{output_dir} has {len(his_paths)} *_his.nc files.")


def _fm_output_dir(root: Path) -> Path:
    """The FM output folder: the ``OutputDir`` of the single MDU, else ``output``."""
    mdu_paths = sorted(root.glob("*.mdu"))
    if len(mdu_paths) == 1:
        return MduInfo.from_path(mdu_paths[0]).output_dir
    return root / "output"


def open_his_reader(file: FmHisFile | TrihHisFile) -> HisReader:
    """Open the reader for a located HIS file."""
    if isinstance(file, FmHisFile):
        return FmHisReader(file.data_path)
    return TrihHisReader(file.data_path, file.def_path, file.mdf_path)


def resolve_station(names: Sequence[str], station: int | str) -> int:
    """Return the zero-based index of a station: a 1-based index or an exact name."""
    if isinstance(station, bool):
        raise HisError(
            "V-HIS-001", f"Station must be an index or a name, not {station!r}."
        )
    if isinstance(station, int):
        if 1 <= station <= len(names):
            return station - 1
        raise HisError(
            "V-HIS-001",
            f"Station index {station} is outside 1..{len(names)}.",
        )
    matches = [index for index, name in enumerate(names) if name == station]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise HisError("V-HIS-001", f"Station {station!r} is not in the HIS file.")
    raise HisError(
        "V-HIS-001", f"Station name {station!r} is not unique in the HIS file."
    )


def check_layer(variable: str, layer: int, count: int) -> None:
    """Raise V-HIS-003 unless ``layer`` (one-based) is inside ``1..count``."""
    if not 1 <= layer <= count:
        raise HisError(
            "V-HIS-003",
            f"{variable!r} has {count} layer(s); layer {layer} is outside 1..{count}.",
        )


class HisReader(ABC):
    """Reads station series from one HIS file. Use as a context manager.

    Layers are one-based and counted from the bottom, as in the Delft3D and
    D-Flow FM layer numbering. Single-layer variables have one layer.
    """

    station_names: tuple[str, ...]
    zone: timezone

    @abstractmethod
    def layer_count(self, variable: str) -> int:
        """Return the number of stored layers of a variable."""

    @abstractmethod
    def read(self, variable: str, station: int, layer: int = 1) -> HisSeries:
        """Read one variable for a zero-based station index and one-based layer."""

    @abstractmethod
    def station_xy(self, station: int) -> tuple[float, float]:
        """Return the model x and y coordinates of a zero-based station index."""

    @abstractmethod
    def close(self) -> None:
        """Release the file."""

    def __enter__(self) -> HisReader:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class FmHisReader(HisReader):
    """Reads a D-Flow FM ``*_his.nc`` file (``time``, ``stations``, ``laydim``)."""

    def __init__(self, path: Path) -> None:
        self._dataset = netCDF4.Dataset(path, "r")
        try:
            time = self._dataset.variables.get("time")
            if time is None:
                raise HisError("V-HIS-005", f"{path.name} has no time variable.")
            self._time_units = str(getattr(time, "units", ""))
            self._calendar = str(getattr(time, "calendar", "standard"))
            self.zone = timezone_from_cf_units(self._time_units) or UTC
            names_variable = self._dataset.variables.get("station_name")
            if names_variable is None:
                raise HisError(
                    "V-HIS-005", f"{path.name} has no station_name variable."
                )
            names_variable.set_auto_chartostring(True)
            self.station_names = _name_strings(names_variable[:])
            self._time = self._times()
        except Exception:
            self._dataset.close()
            raise

    def close(self) -> None:
        self._dataset.close()

    def layer_count(self, variable: str) -> int:
        if variable == "velocity_magnitude":
            return self._layer_dimension("x_velocity")
        if variable in FM_VARIABLES:
            return self._layer_dimension(FM_VARIABLES[variable])
        available = sorted([*FM_VARIABLES, "velocity_magnitude"])
        raise HisError(
            "V-HIS-002",
            f"Variable {variable!r} is not available in FM HIS files; "
            f"use one of {available}.",
        )

    def read(self, variable: str, station: int, layer: int = 1) -> HisSeries:
        check_layer(variable, layer, self.layer_count(variable))
        if variable == "velocity_magnitude":
            values = np.hypot(
                self._station_values("x_velocity", station, layer),
                self._station_values("y_velocity", station, layer),
            )
        else:
            values = self._station_values(FM_VARIABLES[variable], station, layer)
        return HisSeries(self._time, values)

    def station_xy(self, station: int) -> tuple[float, float]:
        return (
            self._coordinate("station_x_coordinate", station),
            self._coordinate("station_y_coordinate", station),
        )

    def _coordinate(self, name: str, station: int) -> float:
        raw = self._dataset.variables.get(name)
        if raw is None or raw.ndim != 1:
            raise HisError(
                "V-HIS-005", f"FM HIS file has no station coordinate {name!r}."
            )
        return float(_float_values(raw[:])[station])

    def _variable(self, name: str) -> Any:
        raw = self._dataset.variables.get(name)
        if raw is None:
            raise HisError("V-HIS-002", f"FM HIS file has no variable {name!r}.")
        if raw.ndim not in (2, 3):
            raise HisError(
                "V-HIS-002", f"{name!r} has an unsupported shape {raw.shape}."
            )
        return raw

    def _layer_dimension(self, name: str) -> int:
        raw = self._variable(name)
        return int(raw.shape[2]) if raw.ndim == 3 else 1

    def _station_values(
        self, name: str, station: int, layer: int
    ) -> NDArray[np.float64]:
        raw = self._variable(name)
        if raw.ndim == 3:
            return _float_values(raw[:, station, layer - 1])
        return _float_values(raw[:, station])

    def _times(self) -> NDArray[np.float64]:
        values = _float_values(self._dataset.variables["time"][:])
        epoch = np.empty(values.shape, dtype=FLOAT)
        for index, value in enumerate(values):
            decoded = decode_cf_time(
                float(value),
                self._time_units,
                self._calendar,
                fallback_offset_hours=0.0,
            )
            if decoded is None:
                raise HisError(
                    "V-HIS-005",
                    f"Cannot decode FM HIS time {value} with units "
                    f"{self._time_units!r}.",
                )
            epoch[index] = decoded.timestamp()
        return epoch


class TrihHisReader(HisReader):
    """Reads a Delft3D 4 ``trih`` NEFIS file.

    Records are read once per element and cached as stacked arrays, so each station
    series costs no extra NEFIS reads. Layer-resolved elements need ``KMAX == 1``.
    """

    def __init__(self, data_path: Path, def_path: Path, mdf_path: Path) -> None:
        mdu = MduInfo.from_d3d4_mdf(mdf_path)
        self.zone = timezone_from_hours(mdu.tzone)
        self._origin = datetime(
            mdu.refdate // 10000,
            mdu.refdate // 100 % 100,
            mdu.refdate % 100,
            tzinfo=self.zone,
        ).timestamp()
        self._nefis = NefisFile(data_path, def_path)
        self._cache: dict[str, NDArray[np.float64]] = {}
        try:
            self._nostat = _scalar_int(self._const("NOSTAT"))
            self._kmax = _scalar_int(self._const("KMAX"))
            self._lstci = _scalar_int(self._const("LSTCI"))
            self._dt = _scalar_float(self._const("DT"))
            self._tunit = _scalar_float(self._const("TUNIT"))
            self.station_names = _name_strings(self._const("NAMST"))
            if len(self.station_names) != self._nostat:
                raise HisError(
                    "V-HIS-005",
                    f"trih has {len(self.station_names)} station names for "
                    f"{self._nostat} stations.",
                )
            self._constituents = (
                _name_strings(self._const("NAMCON"))[: self._lstci]
                if self._lstci > 0
                else ()
            )
            self._records = int(self._nefis.group_shape(_INFO_GROUP)[0])
            self._time = self._trih_times()
        except Exception:
            self._nefis.close()
            raise

    def close(self) -> None:
        self._nefis.close()

    def _const(self, element: str) -> np.ndarray:
        """Read a constant element; his-const is a variable group, so index 1."""
        return self._nefis.read(_CONST_GROUP, element, index=1)

    def layer_count(self, variable: str) -> int:
        if variable in _SURFACE_VARIABLES:
            return 1
        if variable == "velocity_magnitude":
            return self._kmax
        self._constituent_index(variable)
        return self._kmax

    def read(self, variable: str, station: int, layer: int = 1) -> HisSeries:
        check_layer(variable, layer, self.layer_count(variable))
        if variable == "water_level":
            values = self._surface(station, "ZWL")
        elif variable == "water_depth":
            values = self._surface(station, "ZWL") + self._surface(station, "DPS")
        elif variable == "bed_level":
            values = -self._surface(station, "DPS")
        elif variable == "velocity_magnitude":
            values = np.hypot(
                self._layered(station, "ZCURU", layer),
                self._layered(station, "ZCURV", layer),
            )
        else:
            values = self._constituent(station, variable, layer)
        return HisSeries(self._time, values)

    def station_xy(self, station: int) -> tuple[float, float]:
        coordinates = np.asarray(self._const("XYSTAT"), dtype=FLOAT)
        if coordinates.shape != (2, self._nostat):
            raise HisError(
                "V-HIS-005",
                f"trih XYSTAT has shape {coordinates.shape}; expected "
                f"{(2, self._nostat)}.",
            )
        return float(coordinates[0, station]), float(coordinates[1, station])

    def _trih_times(self) -> NDArray[np.float64]:
        infos = self._stack("ITHISC", (), group=_INFO_GROUP)
        return self._origin + infos * self._dt * self._tunit

    def _surface(self, station: int, element: str) -> NDArray[np.float64]:
        return self._stack(element, (self._nostat,))[:, station]

    def _layered(self, station: int, element: str, layer: int) -> NDArray[np.float64]:
        return self._stack(element, (self._nostat, self._kmax))[:, station, layer - 1]

    def _constituent_index(self, variable: str) -> int:
        matches = [
            index
            for index, name in enumerate(self._constituents)
            if name.casefold() == variable.casefold()
        ]
        if len(matches) != 1:
            raise HisError(
                "V-HIS-004",
                f"Variable {variable!r} is not a trih constituent; available: "
                f"{list(self._constituents)} plus {list(D3D4_VARIABLES)}.",
            )
        if "R1" not in self._nefis.elements:
            raise HisError(
                "V-HIS-005", "The trih file lists constituents but has no R1."
            )
        return matches[0]

    def _constituent(
        self, station: int, variable: str, layer: int
    ) -> NDArray[np.float64]:
        index = self._constituent_index(variable)
        stacked = self._stack("R1", (self._nostat, self._kmax, self._lstci))
        return stacked[:, station, layer - 1, index]

    def _stack(
        self,
        element: str,
        cell_shape: tuple[int, ...],
        *,
        group: str = _SERIES_GROUP,
    ) -> NDArray[np.float64]:
        """Return all records of one element with shape ``(records, *cell_shape)``."""
        if element not in self._cache:
            expected = math.prod(cell_shape)
            records = []
            for record in range(1, self._records + 1):
                array = np.asarray(
                    self._nefis.read(group, element, index=record), dtype=FLOAT
                )
                if array.size != expected:
                    raise HisError(
                        "V-HIS-005",
                        f"trih element {element!r} has {array.size} values in record "
                        f"{record}; expected {expected} for shape {cell_shape}.",
                    )
                records.append(array.reshape(cell_shape))
            self._cache[element] = (
                np.stack(records)
                if records
                else np.empty((0, *cell_shape), dtype=FLOAT)
            )
        return self._cache[element]


def _name_strings(data: Any) -> tuple[str, ...]:
    """Decode a name array; character matrices (name, character) are joined per row."""
    array = np.asarray(data)
    if array.ndim == 2:
        if array.dtype.kind == "S":
            return tuple(_text(b"".join(row.tolist())) for row in array)
        return tuple(_text("".join(row.tolist())) for row in array)
    return tuple(_text(value) for value in array.reshape(-1))


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("latin-1").strip()
    return str(value).strip()


def _float_values(values: object) -> NDArray[np.float64]:
    masked = np.ma.asarray(values, dtype=FLOAT)
    return np.asarray(np.ma.filled(masked, np.nan), dtype=FLOAT)


def _scalar_int(values: np.ndarray) -> int:
    return int(np.asarray(values).reshape(-1)[0])


def _scalar_float(values: np.ndarray) -> float:
    return float(np.asarray(values).reshape(-1)[0])
