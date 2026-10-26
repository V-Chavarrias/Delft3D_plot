# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only adapter for D-Flow FM MDU files."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from hydrolib.core.base.models import FileModel  # type: ignore[import-untyped]
from hydrolib.core.dflowfm.research.models import (  # type: ignore[import-untyped]
    ResearchFMModel,
)
from pydantic import ValidationError

from d3dplot.model_input.errors import ModelInputError

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

    @classmethod
    def from_path(cls, path: Path) -> MduInfo:
        """Load an MDU without recursively reading any referenced model files."""
        mdu_path = path.resolve(strict=False)
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
        )


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
    return path.resolve(strict=False)


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
    return path.resolve(strict=False)


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
