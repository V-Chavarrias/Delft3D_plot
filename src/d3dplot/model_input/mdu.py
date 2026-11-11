# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only adapter for D-Flow FM MDU files."""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from hydrolib.core.base.models import FileModel  # type: ignore[import-untyped]
from hydrolib.core.dflowfm.research.models import (  # type: ignore[import-untyped]
    ResearchFMModel,
)
from pydantic import ValidationError

from d3dplot.model_input.errors import ModelInputError
from d3dplot.model_input.mdf import MDF_HINT, read_mdf
from d3dplot.paths import resolve_path_preserving_drive

_MDF_HINT = MDF_HINT
_MDU_HINT = (
    "Check the MDU syntax; if its keywords are valid for a newer kernel, update "
    "HYDROLIB-core or report them upstream."
)


@dataclass(frozen=True, slots=True)
class MduInfo:
    """Read-only, typed values needed from one MDU file."""

    mdu_path: Path
    refdate: int
    tunit: str
    tstart: float | str
    tstop: float | str
    tzone: float
    output_dir: Path
    map_file: Path
    net_file: Path | None
    mor_file: Path | None
    sed_file: Path | None
    gravity: float
    water_density: float
    tzone_explicit: bool = False
    substance_file: Path | None = None

    @classmethod
    def from_path(cls, path: Path) -> MduInfo:
        """Load an MDU without recursively reading any referenced model files."""
        mdu_path = resolve_path_preserving_drive(path)
        try:
            model = ResearchFMModel(mdu_path, recurse=False)
        except ValidationError as error:
            raise ModelInputError(
                mdu_path,
                _format_validation_error(error),
                _MDU_HINT,
            ) from error
        except (OSError, ValueError) as error:
            raise ModelInputError(
                mdu_path,
                f"HYDROLIB-core could not read the MDU: {error}",
                _MDU_HINT,
            ) from error

        base_dir = mdu_path.parent
        start_datetime = model.time.startdatetime
        stop_datetime = model.time.stopdatetime
        return cls(
            mdu_path=mdu_path,
            refdate=model.time.refdate,
            tunit=model.time.tunit,
            tstart=start_datetime or model.time.tstart,
            tstop=stop_datetime or model.time.tstop,
            tzone=model.time.tzone,
            tzone_explicit="tzone" in model.time.model_fields_set,
            output_dir=_resolve_output_dir(
                model.output.outputdir,
                base_dir=base_dir,
                default_name=f"DFM_OUTPUT_{mdu_path.stem}",
            ),
            map_file=_output_file_path(
                model.output.mapfile,
                default_name=f"{mdu_path.stem}_map.nc",
            ),
            net_file=_resolve_input_file(model.geometry.netfile, base_dir),
            mor_file=_resolve_input_file(model.sediment.morfile, base_dir),
            sed_file=_resolve_input_file(model.sediment.sedfile, base_dir),
            gravity=model.physics.ag,
            water_density=model.physics.rhomean,
            substance_file=(
                _resolve_input_file(model.processes.substancefile, base_dir)
                if model.processes is not None
                else None
            ),
        )

    @classmethod
    def from_d3d4_mdf(cls, path: Path) -> MduInfo:
        """Load the time, output and physics values of a Delft3D 4 MDF file."""
        mdf_path = resolve_path_preserving_drive(path)
        values = read_mdf(mdf_path)
        base_dir = mdf_path.parent
        return cls(
            mdu_path=mdf_path,
            refdate=_mdf_refdate(values, mdf_path),
            tunit=_mdf_text(values, "Tunit", mdf_path),
            tstart=_mdf_float(values, "Tstart", mdf_path),
            tstop=_mdf_float(values, "Tstop", mdf_path),
            tzone=_mdf_float(values, "Tzone", mdf_path),
            tzone_explicit="Tzone" in values,
            output_dir=base_dir,
            map_file=Path(f"trim-{mdf_path.stem}.dat"),
            net_file=_mdf_input_file(values, "Filcco", base_dir),
            mor_file=_mdf_input_file(values, "Filmor", base_dir),
            sed_file=_mdf_input_file(values, "Filsed", base_dir),
            gravity=_mdf_float(values, "Ag", mdf_path),
            water_density=_mdf_float(values, "Rhow", mdf_path),
        )


def _mdf_text(values: Mapping[str, str], key: str, path: Path) -> str:
    text = values.get(key, "").strip()
    if not text:
        raise ModelInputError(
            path,
            f"MDF keyword '{key}' is missing or empty.",
            _MDF_HINT,
        )
    return text


def _mdf_float(values: Mapping[str, str], key: str, path: Path) -> float:
    text = _mdf_text(values, key, path)
    try:
        return float(text)
    except ValueError as error:
        raise ModelInputError(
            path,
            f"MDF keyword '{key}' is not a number: {text!r}.",
            _MDF_HINT,
        ) from error


def _mdf_refdate(values: Mapping[str, str], path: Path) -> int:
    text = _mdf_text(values, "Itdate", path)
    try:
        date = datetime.date.fromisoformat(text)
    except ValueError as error:
        raise ModelInputError(
            path,
            f"MDF keyword 'Itdate' must be yyyy-mm-dd: {text!r}.",
            _MDF_HINT,
        ) from error
    return date.year * 10000 + date.month * 100 + date.day


def _mdf_input_file(values: Mapping[str, str], key: str, base_dir: Path) -> Path | None:
    text = values.get(key, "").strip()
    if not text:
        return None
    return resolve_path_preserving_drive(base_dir / text)


def _resolve_input_file(
    file_model: FileModel | None,
    base_dir: Path,
) -> Path | None:
    if file_model is None:
        return None
    file_path = file_model.filepath
    if file_path is None:
        return None
    path = Path(file_path)
    if not path.is_absolute():
        path = base_dir / path
    return resolve_path_preserving_drive(path)


def _resolve_output_dir(
    value: Path | str | None,
    *,
    base_dir: Path,
    default_name: str,
) -> Path:
    if value is None or not str(value).strip():
        path = Path(default_name)
    else:
        path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    return resolve_path_preserving_drive(path)


def _output_file_path(file_model: FileModel | None, *, default_name: str) -> Path:
    if file_model is None or file_model.filepath is None:
        return Path(default_name)
    path = Path(file_model.filepath)
    if not str(path).strip():
        return Path(default_name)
    return path


def _format_validation_error(error: ValidationError) -> str:
    details: list[str] = []
    metadata_keys = {"_header", "datablock", "comments"}

    for detail in error.errors():
        location_parts = [
            str(part) for part in detail["loc"] if str(part) != "model.mdu"
        ]
        input_value = detail.get("input")
        if detail["type"] == "extra_forbidden" and isinstance(input_value, Mapping):
            header = input_value.get("_header")
            section = (
                str(header)
                if isinstance(header, str)
                else location_parts[-1]
                if location_parts
                else "unknown"
            )
            keywords = sorted(
                str(key) for key in input_value if str(key) not in metadata_keys
            )
            if keywords:
                details.append(
                    f"unknown section [{section}] contains keyword(s): "
                    f"{', '.join(keywords)}"
                )
                continue

        location = ".".join(location_parts) or "MDU"
        details.append(f"{location}: {detail['msg']}")

    return f"HYDROLIB-core could not read the MDU: {'; '.join(details)}"
