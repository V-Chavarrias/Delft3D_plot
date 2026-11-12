# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only reader for D-Flow FM MDU files.

Only the keywords needed for plotting are read. Unknown sections and keywords
are accepted and ignored; known keywords with unparseable values are errors.
Defaults follow the D-Flow FM kernel defaults used by HYDROLIB-core.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from d3dplot.model_input.errors import ModelInputError
from d3dplot.model_input.ini import optional_float, parse_ini_file
from d3dplot.model_input.mdf import MDF_HINT, read_mdf
from d3dplot.model_input.mdu_keywords import is_known_section, keyword_spec
from d3dplot.paths import model_text_to_path, resolve_path_preserving_drive

_MDF_HINT = MDF_HINT
_MDU_HINT = "Check the MDU syntax and the values of the keywords named in the message."
_NON_KEYWORD_ENTRIES = frozenset({"_header", "datablock", "comments"})

_REFDATE_DEFAULT = 20200101
_TUNIT_DEFAULT = "S"
_TSTOP_DEFAULT = 86400.0
_GRAVITY_DEFAULT = 9.81
_WATER_DENSITY_DEFAULT = 1000.0
_DATETIME_PLACEHOLDER = "yyyymmddhhmmss"


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
    keyword_notes: tuple[str, ...] = ()

    @classmethod
    def from_path(cls, path: Path) -> MduInfo:
        """Load an MDU without recursively reading any referenced model files."""
        mdu_path = resolve_path_preserving_drive(path)
        document = parse_ini_file(mdu_path)
        time = _section(document, "time", mdu_path)
        output = _section(document, "output", mdu_path)
        geometry = _section(document, "geometry", mdu_path)
        sediment = _section(document, "sediment", mdu_path)
        physics = _section(document, "physics", mdu_path)
        processes = _section(document, "processes", mdu_path)

        base_dir = mdu_path.parent
        start_datetime = _datetime_keyword(time, "startdatetime", mdu_path)
        stop_datetime = _datetime_keyword(time, "stopdatetime", mdu_path)
        return cls(
            mdu_path=mdu_path,
            refdate=_int_keyword(time, "refdate", _REFDATE_DEFAULT, mdu_path),
            tunit=_text_keyword(time, "tunit", _TUNIT_DEFAULT, mdu_path),
            tstart=start_datetime or _float_keyword(time, "tstart", 0.0, mdu_path),
            tstop=stop_datetime
            or _float_keyword(time, "tstop", _TSTOP_DEFAULT, mdu_path),
            tzone=_float_keyword(time, "tzone", 0.0, mdu_path),
            tzone_explicit="tzone" in time,
            output_dir=_resolve_output_dir(
                _optional_text(output, "outputdir", mdu_path),
                base_dir=base_dir,
                default_name=f"DFM_OUTPUT_{mdu_path.stem}",
            ),
            map_file=_output_file_path(
                _optional_text(output, "mapfile", mdu_path),
                default_name=f"{mdu_path.stem}_map.nc",
            ),
            net_file=_resolve_input_file(
                _optional_text(geometry, "netfile", mdu_path), base_dir
            ),
            mor_file=_resolve_input_file(
                _optional_text(sediment, "morfile", mdu_path), base_dir
            ),
            sed_file=_resolve_input_file(
                _optional_text(sediment, "sedfile", mdu_path), base_dir
            ),
            gravity=_float_keyword(physics, "ag", _GRAVITY_DEFAULT, mdu_path),
            water_density=_float_keyword(
                physics, "rhomean", _WATER_DENSITY_DEFAULT, mdu_path
            ),
            substance_file=_resolve_input_file(
                _optional_text(processes, "substancefile", mdu_path), base_dir
            ),
            keyword_notes=_keyword_notes(document),
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
    return resolve_path_preserving_drive(base_dir / model_text_to_path(text))


def _section(
    document: Mapping[str, object],
    name: str,
    path: Path,
) -> Mapping[str, object]:
    """Return one section; a missing section has no keywords, so defaults apply."""
    value = document.get(name)
    if value is None:
        return {}
    if isinstance(value, list):
        raise ModelInputError(
            path,
            f"Section [{name}] appears {len(value)} times; it must appear once.",
            _MDU_HINT,
        )
    if not isinstance(value, Mapping):
        raise ModelInputError(path, f"Section [{name}] is not a mapping.", _MDU_HINT)
    return value


def _keyword_notes(document: Mapping[str, object]) -> tuple[str, ...]:
    """Return D123 notes for unknown sections, unknown keywords and old keywords.

    The notes are informational: unknown and obsolete entries are accepted.
    """
    notes: list[str] = []
    for section_key, value in document.items():
        sections = value if isinstance(value, list) else [value]
        for section in sections:
            if not isinstance(section, Mapping):
                continue
            header = _section_name(section)
            if not is_known_section(section_key):
                notes.append(
                    f"Unknown MDU section [{header}] is accepted and not checked."
                )
                continue
            for key in section:
                if key in _NON_KEYWORD_ENTRIES:
                    continue
                spec = keyword_spec(section_key, key)
                if spec is None:
                    notes.append(
                        f"Unknown MDU keyword [{header}] {key} "
                        "is accepted and not used."
                    )
                elif spec.status in ("deprecated", "obsolete"):
                    notes.append(
                        f"MDU keyword [{header}] {key} is {spec.status} "
                        "and is accepted."
                    )
    return tuple(notes)


def _raw_keyword(
    section: Mapping[str, object],
    name: str,
    key: str,
    path: Path,
) -> str | None:
    """Return a keyword's text, or None when the keyword is absent or empty."""
    value = section.get(key)
    if value is None:
        return None
    if isinstance(value, list):
        raise ModelInputError(
            path,
            f"Keyword '{key}' in [{name}] is given more than once.",
            _MDU_HINT,
        )
    if not isinstance(value, str):
        raise ModelInputError(
            path,
            f"Keyword '{key}' in [{name}] must be text.",
            _MDU_HINT,
        )
    return value


def _optional_text(section: Mapping[str, object], key: str, path: Path) -> str | None:
    text = _raw_keyword(section, _section_name(section), key, path)
    return text or None


def _section_name(section: Mapping[str, object]) -> str:
    header = section.get("_header")
    return header if isinstance(header, str) else "unknown"


def _text_keyword(
    section: Mapping[str, object],
    key: str,
    default: str,
    path: Path,
) -> str:
    text = _raw_keyword(section, _section_name(section), key, path)
    return default if text is None else text


def _int_keyword(
    section: Mapping[str, object],
    key: str,
    default: int,
    path: Path,
) -> int:
    text = _raw_keyword(section, _section_name(section), key, path)
    if text is None:
        return default
    try:
        return int(text)
    except ValueError as error:
        raise ModelInputError(
            path,
            f"Keyword '{key}' in [{_section_name(section)}] must be an integer, "
            f"not {text!r}.",
            _MDU_HINT,
        ) from error


def _float_keyword(
    section: Mapping[str, object],
    key: str,
    default: float,
    path: Path,
) -> float:
    name = _section_name(section)
    text = _raw_keyword(section, name, key, path)
    if text is None:
        return default
    parsed = optional_float(text, path=path, field_name=f"Keyword '{key}' in [{name}]")
    return default if parsed is None else parsed


def _datetime_keyword(
    section: Mapping[str, object],
    key: str,
    path: Path,
) -> str:
    """Return a yyyymmdd or yyyymmddhhmmss value as written, or "" when absent.

    The literal placeholder ``yyyymmddhhmmss`` is kept as written, as in
    HYDROLIB-core, so it is returned unchanged.
    """
    name = _section_name(section)
    text = _raw_keyword(section, name, key, path)
    if text is None:
        return ""
    if text == _DATETIME_PLACEHOLDER:
        return text
    if len(text) in (8, 14):
        date_format = "%Y%m%d%H%M%S" if len(text) == 14 else "%Y%m%d"
        try:
            datetime.datetime.strptime(text, date_format)
            return text
        except ValueError:
            pass
    raise ModelInputError(
        path,
        f"Keyword '{key}' in [{name}] must be yyyymmdd or yyyymmddhhmmss, "
        f"not {text!r}.",
        _MDU_HINT,
    )


def _resolve_input_file(
    file_text: str | None,
    base_dir: Path,
) -> Path | None:
    if file_text is None:
        return None
    path = model_text_to_path(file_text)
    if not path.is_absolute():
        path = base_dir / path
    return resolve_path_preserving_drive(path)


def _resolve_output_dir(
    value: str | None,
    *,
    base_dir: Path,
    default_name: str,
) -> Path:
    if value is None:
        path = Path(default_name)
    else:
        path = model_text_to_path(value)
    if not path.is_absolute():
        path = base_dir / path
    return resolve_path_preserving_drive(path)


def _output_file_path(file_text: str | None, *, default_name: str) -> Path:
    if file_text is None:
        return Path(default_name)
    return model_text_to_path(file_text)
