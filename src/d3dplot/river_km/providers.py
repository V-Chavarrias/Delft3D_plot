# SPDX-License-Identifier: GPL-3.0-or-later
"""Data-driven river-km providers and branch interpolation."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveFloat

from d3dplot.river_km.file import RiverKmPoint

ProviderName = Literal["generic", "maas", "rijntakken"]


class RiverKmProviderError(ValueError):
    """A river-km provider cannot resolve a requested branch or location."""

    def __init__(self, message: str, *, code: str = "V-RKM-002") -> None:
        super().__init__(message)
        self.code = code


class _TrackRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    above_km: float
    branch: str
    requires_tag: str | None = None


class _TrackDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    rules: tuple[_TrackRule, ...] = ()
    default_branch: str
    valid_range_km: tuple[float, float] | None = None


class _ProviderDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    provider: ProviderName
    tags: tuple[str, ...] = ()
    ds_pol_km: PositiveFloat
    branch_aliases: Mapping[str, str] = Field(default_factory=dict)
    cutoffs: Mapping[str, tuple[tuple[float, float], ...]] = Field(default_factory=dict)
    tracks: Mapping[str, _TrackDefinition] = Field(default_factory=dict)
    maas_switch_km: float | None = None
    cutoff_tolerance_km: PositiveFloat = 0.01

    def canonical_branch(self, branch: str) -> str:
        normalized = branch.strip().casefold()
        return self.branch_aliases.get(normalized, normalized)


@lru_cache(maxsize=3)
def _load_definition(name: ProviderName) -> _ProviderDefinition:
    resource = files("d3dplot.river_km").joinpath("data", f"{name}.json")
    return _ProviderDefinition.model_validate(
        json.loads(resource.read_text(encoding="utf-8"))
    )


@dataclass(frozen=True, slots=True)
class RiverKmProvider:
    """Provider rules and sorted point groups for one river-km input."""

    definition: _ProviderDefinition
    points_by_branch: Mapping[str, tuple[RiverKmPoint, ...]]
    branch_tags: frozenset[str]

    @classmethod
    def from_points(
        cls,
        points: Sequence[RiverKmPoint],
        *,
        track: str | None = None,
    ) -> RiverKmProvider:
        """Detect provider data and index points by canonical branch."""
        if not points:
            raise RiverKmProviderError(
                "River-km input contains no points.", code="V-RKM-001"
            )

        tags = frozenset(point.branch.strip().casefold() for point in points)
        requested_track = track.strip().casefold() if track is not None else ""
        if requested_track in {"ma", "maas"}:
            definition = _load_definition("maas")
        elif requested_track in {
            candidate.casefold() for candidate in _load_definition("rijntakken").tracks
        }:
            definition = _load_definition("rijntakken")
        else:
            matches = [
                definition
                for name in ("rijntakken", "maas")
                if (definition := _load_definition(name)).tags
                and tags.intersection(tag.casefold() for tag in definition.tags)
            ]
            if len(matches) > 1:
                raise RiverKmProviderError(
                    "River-km file contains tags for both Rijntakken and Maas."
                )
            definition = matches[0] if matches else _load_definition("generic")

        grouped: dict[str, list[RiverKmPoint]] = {}
        for point in points:
            if not all(math.isfinite(value) for value in (point.x, point.y, point.km)):
                raise RiverKmProviderError(
                    "River-km points must have finite coordinates and km values.",
                    code="V-RKM-001",
                )
            branch = definition.canonical_branch(point.branch)
            if not branch:
                raise RiverKmProviderError(
                    "River-km point has an empty branch.", code="V-RKM-001"
                )
            grouped.setdefault(branch, []).append(point)

        sorted_groups: dict[str, tuple[RiverKmPoint, ...]] = {}
        for branch, branch_points in grouped.items():
            ordered = tuple(sorted(branch_points, key=lambda point: point.km))
            if any(
                first.km == second.km
                for first, second in zip(ordered, ordered[1:], strict=False)
            ):
                raise RiverKmProviderError(
                    f"River-km branch {branch!r} contains duplicate km values.",
                    code="V-RKM-003",
                )
            sorted_groups[branch] = ordered

        return cls(
            definition=definition,
            points_by_branch=sorted_groups,
            branch_tags=tags,
        )

    @property
    def name(self) -> ProviderName:
        return self.definition.provider

    @property
    def ds_pol_km(self) -> float:
        return float(self.definition.ds_pol_km)

    def cutoff_intervals(self, branch: str) -> tuple[tuple[float, float], ...]:
        """Return provider bend cutoffs for a branch."""
        key = self.definition.canonical_branch(branch)
        return next(
            (
                intervals
                for candidate, intervals in self.definition.cutoffs.items()
                if self.definition.canonical_branch(candidate) == key
            ),
            (),
        )

    def branch_for_km(
        self,
        km: float,
        *,
        track: str | None = None,
        explicit_branch: str | None = None,
    ) -> str:
        """Resolve the branch at a centre or edge km."""
        if not math.isfinite(km):
            raise RiverKmProviderError(
                f"Requested river km must be finite: {km!r}.",
                code="V-RKM-005",
            )

        if self.name == "rijntakken":
            if track is None or not track.strip():
                raise RiverKmProviderError("Rijntakken river-km files require a track.")
            track_key = track.strip().casefold()
            track_definition = next(
                (
                    definition
                    for candidate, definition in self.definition.tracks.items()
                    if candidate.casefold() == track_key
                ),
                None,
            )
            if track_definition is None:
                raise RiverKmProviderError(f"Unknown Rijntakken track {track!r}.")
            if track_definition.valid_range_km is not None:
                lower, upper = track_definition.valid_range_km
                if km < lower or km > upper:
                    raise RiverKmProviderError(
                        f"River km {km:g} is outside track {track!r} "
                        f"range [{lower:g}, {upper:g}].",
                        code="V-RKM-004",
                    )
            for rule in track_definition.rules:
                if km > rule.above_km and (
                    rule.requires_tag is None
                    or rule.requires_tag.casefold() in self.branch_tags
                ):
                    return rule.branch
            return track_definition.default_branch

        if self.name == "maas":
            switch_km = self.definition.maas_switch_km
            if switch_km is None:
                raise RuntimeError("The packaged Maas provider has no branch switch.")
            if km <= switch_km:
                return "MA"
            return "BM"

        selected = explicit_branch if explicit_branch is not None else track
        if selected is not None and selected.strip():
            return selected.strip()
        if len(self.points_by_branch) == 1:
            return self.points_by_branch[next(iter(self.points_by_branch))][0].branch
        raise RiverKmProviderError(
            "A generic river-km file with multiple branches requires an explicit "
            "branch or track."
        )

    def coordinates_at(self, km: float, branch: str) -> tuple[float, float]:
        """Interpolate x/y in km along the selected branch without extrapolating."""
        if not math.isfinite(km):
            raise RiverKmProviderError(
                f"Requested river km must be finite: {km!r}.",
                code="V-RKM-005",
            )
        canonical = self.definition.canonical_branch(branch)
        points = self.points_by_branch.get(canonical)
        if not points:
            raise RiverKmProviderError(
                f"River-km branch {branch!r} is not present in the input file.",
                code="V-RKM-004",
            )

        lower = points[0]
        if km < lower.km or km > points[-1].km:
            raise RiverKmProviderError(
                f"River km {km:g} is outside branch {branch!r} range "
                f"[{lower.km:g}, {points[-1].km:g}].",
                code="V-RKM-004",
            )
        for upper in points[1:]:
            if km <= upper.km:
                if km == upper.km:
                    return upper.x, upper.y
                fraction = (km - lower.km) / (upper.km - lower.km)
                return (
                    lower.x + fraction * (upper.x - lower.x),
                    lower.y + fraction * (upper.y - lower.y),
                )
            lower = upper
        return lower.x, lower.y
