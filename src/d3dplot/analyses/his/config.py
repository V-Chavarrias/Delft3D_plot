# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration model for the HIS station time-series analysis (D132)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Literal, TypeAlias

from pydantic import Field, StrictInt, field_validator, model_validator

from d3dplot.config.analysis import AnalysisConfigBase
from d3dplot.config.base import ConfigModel, Duration, FixedUTCOffset
from d3dplot.config.selections import LayersSelection

HisView: TypeAlias = Literal["line", "all_stations", "convergence", "x_t"]
HisFilter: TypeAlias = Literal["none", "godin"]

_SUPPORTED_COMPARISONS = frozenset({"value", "difference_simulation"})


class HisMeasurementsConfig(ConfigModel):
    """Measured station series overlaid on the value and all-stations figures."""

    catalogue: Path = Field(
        description=(
            "CSV catalogue with columns station, variable, file, factor and optional "
            "layer. Each file has time and value columns. Relative paths are resolved "
            "from the working directory, as for map measurements."
        ),
    )
    time_zone: FixedUTCOffset | None = Field(
        default=None,
        description=(
            "Offset for naive measurement times, for example '+01:00'. Defaults to "
            "the project's time_zone, then UTC."
        ),
    )


class HisConfig(AnalysisConfigBase):
    """Station time series of D-Flow FM HIS output and D3D4 trih files."""

    analysis: Literal["his"] = Field(
        default="his",
        description="Discriminator identifying the HIS analysis.",
    )
    stations: list[StrictInt | str] = Field(
        min_length=1,
        description="Stations by one-based index or by exact name.",
    )
    views: list[HisView] = Field(
        default=["line"],
        min_length=1,
        description=(
            "HIS views to generate. 'line' draws one figure per station, variable "
            "and comparison, with one line per simulation. 'all_stations' draws one "
            "figure per simulation and variable with one line per station. "
            "'convergence' draws the standard deviation of each station's raw values "
            "over the last convergence_time, one line per simulation, in the "
            "reference simulation folder. 'x_t' draws a river-km by time colour "
            "figure per simulation and variable, using river_km_file."
        ),
    )
    vertical: Literal["all"] | LayersSelection = Field(
        default="all",
        description=(
            "'all' or 'layers: [...]'. Layers are one-based and counted from the "
            "bottom of the stored layer axis. A variable with one layer has layer 1 "
            "only."
        ),
    )
    filter: HisFilter = Field(
        default="none",
        description=(
            "'godin' applies the Godin tidal filter (24 h, 24 h, 25 h running means) "
            "to values before plotting. Filtered lines are drawn over the raw lines."
        ),
    )
    measurements: HisMeasurementsConfig | None = Field(
        default=None,
        description="Measured station series to overlay in black.",
    )
    convergence_time: Duration = Field(
        default=timedelta(hours=1),
        description=(
            "Window before each simulation's final time over which the standard "
            "deviation of the raw station values is taken for the convergence view."
        ),
    )
    river_km_file: Path | None = Field(
        default=None,
        description=(
            "River-km point file. Each station takes the river km of its nearest "
            "point for the x_t view. Required when x_t is in views; the accuracy is "
            "that of the file's point spacing. Relative paths are resolved from the "
            "working directory."
        ),
    )

    @field_validator("stations")
    @classmethod
    def validate_stations(cls, value: list[StrictInt | str]) -> list[StrictInt | str]:
        stations: list[StrictInt | str] = []
        for station in value:
            if isinstance(station, int):
                if station < 1:
                    raise ValueError("Station indices are one-based and at least 1.")
                stations.append(station)
            else:
                name = station.strip()
                if not name:
                    raise ValueError("Station names must be non-empty.")
                stations.append(name)
        if len(set(stations)) != len(stations):
            raise ValueError("Stations must be unique.")
        return stations

    @model_validator(mode="after")
    def validate_his_options(self) -> HisConfig:
        unsupported = [
            comparison
            for comparison in self.comparisons
            if comparison not in _SUPPORTED_COMPARISONS
        ]
        if unsupported:
            raise ValueError(
                "HIS analysis supports the 'value' and 'difference_simulation' "
                f"comparisons; got {unsupported}."
            )
        if self.time_matching != "reference":
            raise ValueError(
                "HIS analysis compares simulations on the reference times only; "
                "time_matching: own is not supported."
            )
        if self.times.at != "all":
            raise ValueError("HIS analysis supports times.at: all only.")
        if self.fractions != "all":
            raise ValueError("HIS analysis does not support fractions.")
        if self.sum_fractions:
            raise ValueError("HIS analysis does not support sum_fractions.")
        if "x_t" in self.views and self.river_km_file is None:
            raise ValueError("The HIS x_t view needs river_km_file.")
        if "convergence" in self.views and self.convergence_time <= timedelta(0):
            raise ValueError("HIS convergence_time must be positive.")
        return self
