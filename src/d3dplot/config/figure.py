# SPDX-License-Identifier: GPL-3.0-or-later
"""Figure styling and shared map-view configuration."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal, TypeAlias

from pydantic import Field, PositiveFloat, PositiveInt, field_validator

from d3dplot.config.base import ConfigModel

AxisLimit: TypeAlias = tuple[float | None, float | None]
AxisLimits: TypeAlias = list[AxisLimit] | dict[str, dict[str, list[AxisLimit]]]
ColorLimitSet: TypeAlias = AxisLimit | None
ColorLimits: TypeAlias = dict[str, dict[str, list[ColorLimitSet]]]
Extent: TypeAlias = tuple[float, float, float, float]


def _default_formats() -> list[Literal["png"]]:
    return ["png"]


class DaysSinceColorLimits(ConfigModel):
    """Set automatic limits from a date-relative time window."""

    days_since: date = Field(description="Date from which limits are calculated.")


ColorLimitsMode: TypeAlias = Literal["fixed"] | DaysSinceColorLimits


class ExtentsAlongRiverKmConfig(ConfigModel):
    """Generate map windows around points in a river-km file."""

    file: Path = Field(description="River-km file containing window centres.")
    window_x_m: PositiveFloat = Field(
        description="Half-width of each window in metres."
    )
    window_y_m: PositiveFloat = Field(
        description="Half-height of each window in metres."
    )


class RiverKmLabelsConfig(ConfigModel):
    """Draw river-km labels from a river-km file."""

    file: Path = Field(description="River-km file containing label locations.")
    color: str = Field(default="black", description="Text color for river-km labels.")


class BackgroundTilesConfig(ConfigModel):
    """Configure an optional background-tile provider."""

    provider: str = Field(
        default="Esri.WorldImagery",
        description="Contextily provider name.",
    )


class MapViewConfig(ConfigModel):
    """Map settings shared by map2d and river-km diagnostic figures."""

    color_limits: ColorLimits | None = Field(
        default=None,
        description=(
            "Per-variable and per-comparison color-limit sets; null is automatic."
        ),
    )
    color_limits_mode: ColorLimitsMode = Field(
        default="fixed",
        description="Use fixed limits or calculate them relative to a date.",
    )
    colormap: str | dict[str, str] = Field(
        default="viridis",
        description="Matplotlib colormap name, optionally keyed by variable.",
    )
    render: Literal["polygons", "raster", "auto"] = Field(
        default="auto",
        description="Map drawing method; auto selects raster mode for dense views.",
    )
    aspect: Literal["equal", "auto"] = Field(
        default="auto",
        description="Map axes aspect ratio.",
    )
    extents: list[Extent] | None = Field(
        default=None,
        description="Explicit [xmin, xmax, ymin, ymax] windows; null means full model.",
    )
    extents_along_river_km: ExtentsAlongRiverKmConfig | None = Field(
        default=None,
        description="Windows centred on points from a river-km file.",
    )
    river_km_labels: RiverKmLabelsConfig | None = Field(
        default=None,
        description="River-km labels, using a file independent of the window file.",
    )
    land_boundaries: list[Path] = Field(
        default_factory=list,
        description="Land-boundary inputs in .ldb format.",
    )
    background_tiles: BackgroundTilesConfig | Literal[False] = Field(
        default=False,
        description="Optional background tiles, or false to disable them.",
    )
    fixed_weirs: Literal["none", "input", "snapped"] = Field(
        default="none",
        description="Whether to draw fixed weirs and which geometry to use.",
    )
    fixed_weirs_color: str = Field(
        default="black",
        description="Color used for fixed-weir overlays.",
    )

    @field_validator("extents")
    @classmethod
    def validate_extents(cls, value: list[Extent] | None) -> list[Extent] | None:
        if value is None:
            return None
        for xmin, xmax, ymin, ymax in value:
            if xmin >= xmax or ymin >= ymax:
                raise ValueError("each extent must have increasing x and y bounds")
        return value


class FigureConfig(ConfigModel):
    """Global or analysis-level figure style and line-plot settings."""

    font_size: PositiveFloat = Field(
        default=10.0,
        description="Base font size in points.",
    )
    font_family: str = Field(
        default="DejaVu Sans",
        description="Matplotlib font family.",
    )
    size_cm: tuple[PositiveFloat, PositiveFloat] = Field(
        default=(16.0, 10.0),
        description="Figure width and height in centimetres.",
    )
    dpi: PositiveInt = Field(
        default=150, description="Output resolution in dots per inch."
    )
    formats: list[Literal["png"]] = Field(
        default_factory=_default_formats,
        min_length=1,
        description="Output formats supported by the v1 writer registry.",
    )
    hooks_mode: Literal["per_image", "rebuild"] = Field(
        default="rebuild",
        description="Rebuild hooked figures by default; per_image snapshots changes.",
    )
    x_limits: AxisLimits | None = Field(
        default=None,
        description="Optional x-axis limits, one pair per figure.",
    )
    y_limits: AxisLimits | None = Field(
        default=None,
        description="Optional y-axis limits, one pair per figure.",
    )
    show_initial: bool = Field(
        default=True,
        description="Whether line plots include the initial value.",
    )
    staircase: bool = Field(
        default=False,
        description="Draw line plots as staircases.",
    )
    markers: bool = Field(default=False, description="Draw markers on line plots.")
    x_reversed: bool = Field(default=False, description="Reverse the line-plot x-axis.")
    legend_labels: dict[str, str] = Field(
        default_factory=dict,
        description="User-provided replacements for legend labels.",
    )
    display_name: str | None = Field(
        default=None,
        description="Optional display name for the plotted quantity.",
    )
    display_scale: float = Field(
        default=1.0,
        description="Scale factor applied to displayed values.",
    )
