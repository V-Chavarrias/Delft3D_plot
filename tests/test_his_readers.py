# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the HIS readers: file location, station resolution and FM/trih series."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import netCDF4
import numpy as np
import pytest

from d3dplot.simulation import his
from d3dplot.simulation.his import (
    FmHisFile,
    FmHisReader,
    HisError,
    TrihHisFile,
    TrihHisReader,
    locate_his_file,
    resolve_station,
)

# 2021-01-01 00:00 at UTC+01:00 is 2020-12-31 23:00 UTC; both fixtures use it.
_ORIGIN_EPOCH = datetime(2020, 12, 31, 23, tzinfo=UTC).timestamp()
_NAMST = [b"s01                 ", b"s02                 "]
_NAMCON = [b"salinity            ", b"Dye                 "]


def _write_fm_his(path: Path, *, layers: int = 1) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", 3)
        dataset.createDimension("stations", 2)
        dataset.createDimension("laydim", layers)
        dataset.createDimension("name_len", 8)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "seconds since 2021-01-01 00:00:00 +01:00"
        time[:] = [0.0, 3600.0, 7200.0]
        names = dataset.createVariable("station_name", "S1", ("stations", "name_len"))
        names[:] = netCDF4.stringtochar(np.array([b"s01", b"s02"], dtype="S8"))
        level = dataset.createVariable(
            "waterlevel", "f8", ("time", "stations"), fill_value=-999.0
        )
        level[:] = [[-999.0, 2.0], [1.5, 2.5], [2.0, 3.0]]
        for name, value in (
            ("x_velocity", 3.0),
            ("y_velocity", 4.0),
            ("salinity", 30.0),
        ):
            variable = dataset.createVariable(
                name, "f8", ("time", "stations", "laydim")
            )
            variable[:] = np.full((3, 2, layers), value)


def _fake_nefis(kmax: int = 1) -> type:
    """A NEFIS stand-in with three records of two stations (ZWL = record + 0 or 1)."""
    constants = {
        "NOSTAT": np.array([2]),
        "KMAX": np.array([kmax]),
        "LSTCI": np.array([2]),
        "DT": np.array([60.0]),
        "TUNIT": np.array([1.0]),
        "NAMST": np.array(_NAMST),
        "NAMCON": np.array(_NAMCON),
        "XYSTAT": np.array([[10.0, 20.0], [30.0, 40.0]]),
    }

    def series(record: int, element: str) -> np.ndarray:
        if element == "ZWL":
            return np.array([record, record + 1.0])
        if element == "DPS":
            return np.array([-5.0, -6.0])
        if element in ("ZCURU", "ZCURV"):
            value = 3.0 if element == "ZCURU" else 4.0
            return np.full((2, kmax), value)
        if element == "R1":
            return np.array(
                [
                    [[record * 100 + s * 10 + c for c in range(2)] for _ in range(kmax)]
                    for s in range(2)
                ],
                dtype=float,
            )
        raise KeyError(element)

    class FakeNefis:
        elements = {name: None for name in ("ZWL", "DPS", "ZCURU", "ZCURV", "R1")}

        def __init__(self, data_path: Path, def_path: Path | None = None) -> None:
            self.data_path = data_path

        def read(
            self, group: str, element: str, index: int | None = None
        ) -> np.ndarray:
            if (
                group in ("his-const", "his-info-series", "his-series")
                and index is None
            ):
                raise ValueError(f"group {group!r} is variable; an index is required")
            if group == "his-const":
                return constants[element]
            if group == "his-info-series":
                assert index is not None
                return np.array([float(index - 1)])
            assert index is not None
            return series(index, element)

        def group_shape(self, group: str) -> tuple[int, ...]:
            assert group == "his-info-series"
            return (3,)

        def close(self) -> None:
            pass

    return FakeNefis


def _open_trih(
    monkeypatch: pytest.MonkeyPatch, folder: Path, *, kmax: int = 1
) -> TrihHisReader:
    for name in ("trih-r001.dat", "trih-r001.def", "r001.mdf"):
        (folder / name).write_bytes(b"")
    monkeypatch.setattr(his, "NefisFile", _fake_nefis(kmax))
    monkeypatch.setattr(
        his.MduInfo,
        "from_d3d4_mdf",
        staticmethod(lambda path: SimpleNamespace(refdate=20210101, tzone=1.0)),
    )
    return TrihHisReader(
        folder / "trih-r001.dat", folder / "trih-r001.def", folder / "r001.mdf"
    )


def test_resolve_station_accepts_one_based_index_and_exact_name() -> None:
    names = ("s01", "s02")

    assert resolve_station(names, 2) == 1
    assert resolve_station(names, "s01") == 0


@pytest.mark.parametrize("station", [0, 3, "nope"])
def test_resolve_station_rejects_unknown_station(station: int | str) -> None:
    with pytest.raises(HisError, match=r"\[V-HIS-001\]"):
        resolve_station(("s01", "s02"), station)


def test_locate_prefers_trih_over_fm_output(tmp_path: Path) -> None:
    (tmp_path / "output").mkdir()
    _write_fm_his(tmp_path / "output" / "run_his.nc")
    for name in ("trih-r001.dat", "trih-r001.def", "r001.mdf"):
        (tmp_path / name).write_bytes(b"")

    located = locate_his_file(tmp_path)

    assert located == TrihHisFile(
        tmp_path / "trih-r001.dat", tmp_path / "trih-r001.def", tmp_path / "r001.mdf"
    )


def test_locate_finds_single_fm_his_file(tmp_path: Path) -> None:
    (tmp_path / "output").mkdir()
    _write_fm_his(tmp_path / "output" / "run_his.nc")

    assert locate_his_file(tmp_path) == FmHisFile(tmp_path / "output" / "run_his.nc")


def test_locate_uses_mdu_output_dir(tmp_path: Path) -> None:
    (tmp_path / "r001.mdu").write_text(
        "[time]\nrefdate = 20000101\n[output]\nOutputDir = DFM_OUTPUT_r001\n"
        "[geometry]\n[sediment]\n[physics]\n[processes]\n",
        encoding="utf-8",
    )
    output = tmp_path / "DFM_OUTPUT_r001"
    output.mkdir()
    _write_fm_his(output / "r001_his.nc")

    assert locate_his_file(tmp_path) == FmHisFile(output / "r001_his.nc")


def test_locate_rejects_trih_without_definition(tmp_path: Path) -> None:
    (tmp_path / "trih-r001.dat").write_bytes(b"")
    (tmp_path / "r001.mdf").write_bytes(b"")

    with pytest.raises(HisError, match=r"\[V-HIS-005\]"):
        locate_his_file(tmp_path)


def test_locate_rejects_folder_without_his_output(tmp_path: Path) -> None:
    with pytest.raises(HisError, match=r"\[V-HIS-005\]"):
        locate_his_file(tmp_path)


def test_fm_reader_reads_station_series_with_masked_values(tmp_path: Path) -> None:
    path = tmp_path / "run_his.nc"
    _write_fm_his(path)

    with FmHisReader(path) as reader:
        series = reader.read("water_level", station=0)
        other = reader.read("water_level", station=1)

    assert reader.station_names == ("s01", "s02")
    np.testing.assert_allclose(
        series.time_seconds, _ORIGIN_EPOCH + np.array([0.0, 3600.0, 7200.0])
    )
    np.testing.assert_allclose(series.values, [np.nan, 1.5, 2.0], equal_nan=True)
    np.testing.assert_allclose(other.values, [2.0, 2.5, 3.0])


def test_fm_station_xy_reads_station_coordinates(tmp_path: Path) -> None:
    path = tmp_path / "run_his.nc"
    _write_fm_his(path)
    with netCDF4.Dataset(path, "a") as dataset:
        for name, values in (
            ("station_x_coordinate", [10.0, 20.0]),
            ("station_y_coordinate", [30.0, 40.0]),
        ):
            dataset.createVariable(name, "f8", ("stations",))[:] = values

    with FmHisReader(path) as reader:
        assert reader.station_xy(1) == (20.0, 40.0)


def test_fm_velocity_magnitude_combines_components(tmp_path: Path) -> None:
    path = tmp_path / "run_his.nc"
    _write_fm_his(path)

    with FmHisReader(path) as reader:
        series = reader.read("velocity_magnitude", station=1)

    np.testing.assert_allclose(series.values, [5.0, 5.0, 5.0])


def test_fm_layers_are_selectable_and_range_checked(tmp_path: Path) -> None:
    path = tmp_path / "run_his.nc"
    _write_fm_his(path, layers=2)

    with FmHisReader(path) as reader:
        assert reader.layer_count("salinity") == 2
        second = reader.read("salinity", station=0, layer=2)
        with pytest.raises(HisError, match=r"\[V-HIS-003\]"):
            reader.read("salinity", station=0, layer=3)

    np.testing.assert_allclose(second.values, [30.0, 30.0, 30.0])


def test_fm_unsupported_variable_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "run_his.nc"
    _write_fm_his(path)

    with FmHisReader(path) as reader, pytest.raises(HisError, match=r"\[V-HIS-002\]"):
        reader.read("bed_level", station=0)


def test_trih_water_level_and_depth_use_stored_elements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _open_trih(monkeypatch, tmp_path) as reader:
        level = reader.read("water_level", station=1)
        depth = reader.read("water_depth", station=0)
        bed = reader.read("bed_level", station=1)

    assert reader.station_names == ("s01", "s02")
    assert reader.zone.utcoffset(None).total_seconds() == 3600
    np.testing.assert_allclose(
        level.time_seconds, _ORIGIN_EPOCH + np.array([0.0, 60.0, 120.0])
    )
    np.testing.assert_allclose(level.values, [2.0, 3.0, 4.0])
    np.testing.assert_allclose(depth.values, [-4.0, -3.0, -2.0])
    np.testing.assert_allclose(bed.values, [6.0, 6.0, 6.0])


def test_trih_station_xy_reads_xystat_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _open_trih(monkeypatch, tmp_path) as reader:
        assert reader.station_xy(0) == (10.0, 30.0)
        assert reader.station_xy(1) == (20.0, 40.0)


def test_trih_velocity_magnitude_from_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _open_trih(monkeypatch, tmp_path) as reader:
        series = reader.read("velocity_magnitude", station=0)

    np.testing.assert_allclose(series.values, [5.0, 5.0, 5.0])


def test_trih_constituent_is_matched_by_name_case_insensitively(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _open_trih(monkeypatch, tmp_path) as reader:
        series = reader.read("dye", station=0)

    np.testing.assert_allclose(series.values, [101.0, 201.0, 301.0])


def test_trih_unknown_constituent_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with (
        _open_trih(monkeypatch, tmp_path) as reader,
        pytest.raises(HisError, match=r"\[V-HIS-004\]"),
    ):
        reader.read("nitrate", station=0)


def test_trih_layers_are_selectable_and_range_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _open_trih(monkeypatch, tmp_path, kmax=2) as reader:
        assert reader.layer_count("velocity_magnitude") == 2
        assert reader.layer_count("water_level") == 1
        second = reader.read("velocity_magnitude", station=0, layer=2)
        with pytest.raises(HisError, match=r"\[V-HIS-003\]"):
            reader.read("velocity_magnitude", station=0, layer=3)

    np.testing.assert_allclose(second.values, [5.0, 5.0, 5.0])
