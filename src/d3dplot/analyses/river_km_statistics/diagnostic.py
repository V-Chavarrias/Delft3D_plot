# SPDX-License-Identifier: GPL-3.0-or-later
"""Diagnostic figures for river-km geometry failures."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import numpy as np
from matplotlib import pyplot

from d3dplot.config.figure import FigureConfig
from d3dplot.plotting.finalize import FigureContext, finalize_figure
from d3dplot.plotting.writers import get_figure_writer
from d3dplot.river_km.geometry import RiverKmSegmentGeometry
from d3dplot.store import OutputLayout, OutputWriteGuard


def write_invalid_segment_diagnostic(
    geometry: RiverKmSegmentGeometry,
    *,
    segment_index: int,
    region_name: str,
    km_set_name: str,
    layout: OutputLayout,
    guard: OutputWriteGuard,
    style: FigureConfig,
    fingerprint: str,
) -> Path:
    """Write a diagnostic figure highlighting one invalid segment polygon."""
    if segment_index < 0 or segment_index >= len(geometry.polygons):
        raise ValueError(f"Invalid river-km segment index: {segment_index}.")

    polygon = np.asarray(geometry.polygons[segment_index], dtype=np.float64)
    closed_polygon = np.vstack((polygon, polygon[0]))
    edge_start = max(0, segment_index - 1)
    edge_stop = min(len(geometry.edges_xy), segment_index + 3)
    centre_start = max(0, segment_index - 1)
    centre_stop = min(len(geometry.centres_xy), segment_index + 2)
    local_edges = geometry.edges_xy[edge_start:edge_stop]
    local_centres = geometry.centres_xy[centre_start:centre_stop]
    local_left = geometry.left_cut_lines[edge_start:edge_stop]
    local_right = geometry.right_cut_lines[edge_start:edge_stop]
    all_coordinates = np.vstack(
        (
            closed_polygon,
            local_edges,
            local_centres,
            local_left,
            local_right,
        )
    )
    lower = np.nanmin(all_coordinates, axis=0)
    upper = np.nanmax(all_coordinates, axis=0)
    span = np.maximum(upper - lower, 1.0)
    margin = span * 0.08
    bounds = (
        float(lower[0] - margin[0]),
        float(upper[0] + margin[0]),
        float(lower[1] - margin[1]),
        float(upper[1] + margin[1]),
    )

    figure, axes = pyplot.subplots()
    axes.fill(
        closed_polygon[:, 0],
        closed_polygon[:, 1],
        color="tab:red",
        alpha=0.25,
        label="invalid segment",
    )
    axes.plot(
        closed_polygon[:, 0],
        closed_polygon[:, 1],
        color="tab:red",
        linewidth=2.0,
    )
    axes.plot(
        local_edges[:, 0],
        local_edges[:, 1],
        "--",
        color="0.35",
        label="river-km edges",
    )
    axes.plot(
        local_left[:, 0],
        local_left[:, 1],
        color="tab:blue",
        alpha=0.8,
        label="left cut line",
    )
    axes.plot(
        local_right[:, 0],
        local_right[:, 1],
        color="tab:green",
        alpha=0.8,
        label="right cut line",
    )
    axes.scatter(
        local_centres[:, 0],
        local_centres[:, 1],
        color="black",
        marker=".",
        label="river-km centres",
    )
    axes.set_xlim(bounds[0], bounds[1])
    axes.set_ylim(bounds[2], bounds[3])
    axes.set_aspect("equal")
    axes.set_xlabel("x")
    axes.set_ylabel("y")
    axes.set_title(
        f"Invalid river-km segment {segment_index} ({region_name}, {km_set_name})"
    )
    axes.legend()

    output_path = (
        layout.figures
        / "river_km_statistics"
        / "diagnostics"
        / (
            "invalid_segment_"
            f"{quote(region_name, safe='')}_"
            f"{quote(km_set_name, safe='')}_"
            f"{segment_index:04d}.png"
        )
    )
    context = FigureContext(
        analysis="river_km_statistics",
        view="invalid_segment",
        comparison="diagnostic",
        variable="river_km_geometry",
        simulation=region_name,
        time=datetime.now(UTC),
        extent=bounds,
        axes={"geometry": axes},
        data={
            "segment_index": segment_index,
            "polygon": polygon,
            "geometry": geometry,
        },
        labels={"title": axes.get_title()},
        language="en",
        output_path=output_path,
    )
    finalize_figure(
        figure,
        context,
        style,
        (),
        get_figure_writer("png"),
        metadata={
            "diagnostic": "invalid_river_km_segment",
            "geometry_fingerprint": fingerprint,
            "segment_index": str(segment_index),
        },
        guard=guard,
    )
    return output_path
