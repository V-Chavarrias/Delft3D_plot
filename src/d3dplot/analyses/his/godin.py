# SPDX-License-Identifier: GPL-3.0-or-later
"""Godin tidal filter for station series, ported from the Matlab tool (``godin.m``).

The filter applies running means of 24 h, 24 h and 25 h in sequence. Each mean is
taken in valid mode, so the output is shorter than the input by the window lengths.
Times are filtered with the same windows. A window that contains a missing value
gives a missing value.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

GODIN_WINDOW_HOURS: tuple[float, float, float] = (24.0, 24.0, 25.0)
_UNIFORM_TOLERANCE_S = 1.0


def godin_filter(
    times_seconds: NDArray[np.float64],
    values: NDArray[np.float64],
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Return filtered times (seconds) and values for one station series.

    Non-uniform time steps are first resampled to a uniform step (the first step)
    by linear interpolation. A grid point between two samples where either sample
    is missing stays missing, so gaps are not bridged.
    """
    times = np.asarray(times_seconds, dtype=np.float64)
    series = np.asarray(values, dtype=np.float64)
    if times.size < 2:
        return times.copy(), series.copy()
    origin = times[0]
    times, series = _uniform(times - origin, series)
    windows = [
        max(1, round(hours * 3600.0 / (times[1] - times[0])))
        for hours in GODIN_WINDOW_HOURS
    ]
    length = times.size - sum(window - 1 for window in windows)
    if length <= 0:
        return np.empty(0), np.empty(0)
    for window in windows:
        times = _running_mean(times, window)
        series = _running_mean(series, window)
    return times + origin, series


def _uniform(
    times: NDArray[np.float64],
    values: NDArray[np.float64],
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    steps = np.diff(times)
    step = float(steps[0])
    if step <= 0.0:
        raise ValueError("Filter times must be strictly increasing.")
    if float(np.max(np.abs(steps - step))) <= _UNIFORM_TOLERANCE_S:
        return times, values
    grid = times[0] + step * np.arange(round((times[-1] - times[0]) / step) + 1)
    right = np.clip(np.searchsorted(times, grid, side="right") - 1, 0, times.size - 2)
    usable = np.isfinite(values[right]) & np.isfinite(values[right + 1])
    interpolated = np.interp(grid, times, np.where(np.isfinite(values), values, 0.0))
    return grid, np.where(usable, interpolated, np.nan)


def _running_mean(values: NDArray[np.float64], window: int) -> NDArray[np.float64]:
    missing = ~np.isfinite(values)
    sums = np.concatenate(([0.0], np.cumsum(np.where(missing, 0.0, values))))
    counts = np.concatenate(([0], np.cumsum(missing)))
    means = (sums[window:] - sums[:-window]) / window
    means[(counts[window:] - counts[:-window]) > 0] = np.nan
    return means
