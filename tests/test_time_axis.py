# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for time-axis selection, matching, and UTC conversion."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from d3dplot.config.selections import IndicesSelection, TimeRange, TimesConfig
from d3dplot.simulation.time_axis import TimeAxis, TimeEntry
from d3dplot.simulation.time_utils import (
    decode_cf_time,
    time_unit_seconds,
    timezone_from_cf_units,
    timezone_from_offset,
)

_BASE_TIME = datetime(2021, 2, 8, tzinfo=UTC)


def _entry(
    time_index: int,
    seconds: float,
    *,
    subrun: int = 0,
    local_index: int | None = None,
    morph_seconds: float | None = None,
    corrupt_partitions: tuple[int, ...] = (),
) -> TimeEntry:
    return TimeEntry(
        time_index=time_index,
        subrun=subrun,
        local_index=time_index if local_index is None else local_index,
        time=_BASE_TIME + timedelta(seconds=seconds),
        morph_time=(
            None
            if morph_seconds is None
            else _BASE_TIME + timedelta(seconds=morph_seconds)
        ),
        corrupt_partitions=corrupt_partitions,
    )


def test_selects_all_last_indices_and_inclusive_range() -> None:
    entries = tuple(_entry(index, index * 10) for index in range(3))
    axis = TimeAxis(entries, "+00:00", morphology_available=False)

    all_outputs = axis.select(TimesConfig(at="all"))
    last_output = axis.select(TimesConfig(at="last"))
    indexed_outputs = axis.select(TimesConfig(at=IndicesSelection(indices=[0, 2])))
    ranged_outputs = axis.select(
        TimesConfig(
            at=TimeRange(
                start=_BASE_TIME,
                end=_BASE_TIME + timedelta(seconds=20),
                step=timedelta(seconds=10),
            )
        )
    )

    assert all_outputs.entries == entries
    assert last_output.entries == (entries[-1],)
    assert indexed_outputs.entries == (entries[0], entries[2])
    assert ranged_outputs.entries == entries
    assert all(entry.time.tzinfo is UTC for entry in all_outputs.entries)


def test_explicit_times_use_source_offset_and_unbounded_nearest_matching() -> None:
    entries = (_entry(0, 0), _entry(1, 60))
    axis = TimeAxis(entries, "+01:00", morphology_available=False)
    naive_local_time = datetime(2021, 2, 8, 1)

    exact_local_match = axis.select(
        TimesConfig(at=[naive_local_time], tolerance=timedelta(0))
    )
    unbounded_match = axis.select(TimesConfig(at=[datetime(2030, 1, 1, tzinfo=UTC)]))
    bounded_miss = axis.select(
        TimesConfig(
            at=[datetime(2030, 1, 1, tzinfo=UTC)],
            tolerance=timedelta(seconds=1),
        )
    )

    assert exact_local_match.entries == (entries[0],)
    assert unbounded_match.entries == (entries[1],)
    assert not bounded_miss.entries
    assert "V-TIME-010" in {issue.code for issue in bounded_miss.issues}
    assert "Nearest available:" in bounded_miss.issues[0].message


def test_duplicate_resolution_uses_last_except_morphological_first_option() -> None:
    entries = (
        _entry(0, 0, subrun=0, local_index=1, morph_seconds=0),
        _entry(1, 0.5, subrun=1, local_index=0, morph_seconds=30),
        _entry(2, 5, subrun=1, local_index=1, morph_seconds=120),
    )
    axis = TimeAxis(entries, "+00:00", morphology_available=True)

    hydrodynamic = axis.select(
        TimesConfig(at="all"),
        duplicate_morphological_times="first",
    )
    morphological_last = axis.select(TimesConfig(at="all", axis="morphological"))
    morphological_first = axis.select(
        TimesConfig(at="all", axis="morphological"),
        duplicate_morphological_times="first",
    )

    assert hydrodynamic.entries == (entries[1], entries[2])
    assert morphological_last.entries == (entries[1], entries[2])
    assert morphological_first.entries == (entries[0], entries[2])
    assert morphological_last.duplicates[0].chosen == entries[1]
    assert morphological_first.duplicates[0].chosen == entries[0]


def test_configured_tolerance_applies_to_duplicate_choice() -> None:
    entries = (_entry(0, 0), _entry(1, 0.8))
    axis = TimeAxis(entries, "+00:00", morphology_available=False)
    requested = _BASE_TIME + timedelta(seconds=0.1)

    bounded = axis.select(
        TimesConfig(
            at=[requested],
            tolerance=timedelta(seconds=0.2),
        )
    )
    unbounded = axis.select(TimesConfig(at=[requested]))

    assert bounded.entries == (entries[0],)
    assert not bounded.duplicates
    assert unbounded.entries == (entries[1],)
    assert unbounded.duplicates[0].candidates == entries


def test_corrupt_output_policy_masks_skips_or_rejects() -> None:
    corrupt_entry = _entry(0, 0, corrupt_partitions=(1,))
    axis = TimeAxis((corrupt_entry,), "+00:00", morphology_available=False)
    config = TimesConfig(at="all")

    masked = axis.select(config, on_corrupt_output="mask")
    skipped = axis.select(config, on_corrupt_output="skip")
    rejected = axis.select(config, on_corrupt_output="error")

    assert masked.entries == (corrupt_entry,)
    assert masked.issues[0].code == "V-TIME-011"
    assert masked.issues[0].level == "warning"
    assert not skipped.entries
    assert "V-TIME-011" in {issue.code for issue in skipped.issues}
    assert "V-TIME-010" in {issue.code for issue in skipped.issues}
    assert not rejected.entries
    assert rejected.issues[0].code == "V-TIME-011"
    assert rejected.issues[0].level == "error"


def test_morphological_axis_requires_morphology() -> None:
    axis = TimeAxis((_entry(0, 0),), "+00:00", morphology_available=False)

    result = axis.select(TimesConfig(at="all", axis="morphological"))

    assert not result.entries
    assert result.issues[0].code == "V-TIME-012"
    assert not result.is_valid


def test_matches_reference_times_across_simulations() -> None:
    local_entries = (_entry(0, 0), _entry(1, 10))
    local_axis = TimeAxis(local_entries, "+00:00", morphology_available=False)
    reference_entry = _entry(0, 9, subrun=0, local_index=0)

    matched = local_axis.match(
        (reference_entry,),
        axis="hydrodynamic",
        tolerance=timedelta(seconds=2),
    )
    unmatched = local_axis.match(
        (reference_entry,),
        axis="hydrodynamic",
        tolerance=timedelta(0),
    )

    assert matched.entries == (local_entries[1],)
    assert not unmatched.entries
    assert unmatched.issues[0].code == "V-TIME-010"


def test_cf_time_uses_encoded_or_fallback_offset_and_decodes_to_utc() -> None:
    encoded_zone = timezone_from_cf_units("seconds since 2021-02-08 00:00:00 +01:00")
    encoded_time = decode_cf_time(
        0,
        "seconds since 2021-02-08 00:00:00 +01:00",
        "proleptic_gregorian",
        fallback_offset_hours=0,
    )
    fallback_time = decode_cf_time(
        0,
        "seconds since 2021-02-08 00:00:00",
        "standard",
        fallback_offset_hours=2,
    )

    assert encoded_zone == timezone_from_offset("+01:00")
    assert encoded_time == datetime(2021, 2, 7, 23, tzinfo=UTC)
    assert fallback_time == datetime(2021, 2, 7, 22, tzinfo=UTC)
    assert time_unit_seconds("minutes since 2021-02-08 00:00:00") == 60
    assert (
        decode_cf_time(
            0,
            "days since 2021-02-08",
            "noleap",
            fallback_offset_hours=0,
        )
        is None
    )


@pytest.mark.parametrize("value", ["Europe/Amsterdam", "+14:01", "+01:60"])
def test_timezone_parser_rejects_named_or_out_of_range_offsets(value: str) -> None:
    with pytest.raises(ValueError):
        timezone_from_offset(value)
