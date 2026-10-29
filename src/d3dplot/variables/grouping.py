# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared grouping operations for quantity component axes."""

from __future__ import annotations

from collections.abc import Sequence
from hashlib import blake2b
import json

import numpy as np
from numpy.typing import ArrayLike, NDArray

FloatArray = NDArray[np.float64]
ClassMembership = tuple[str, tuple[int, ...]]


class SedimentGroupingError(ValueError):
    """Sediment components cannot be assigned to the configured size classes."""


def sediment_class_membership(
    component_diameters_m: Sequence[float | None],
    limits_m: Sequence[float],
    labels: Sequence[str],
) -> tuple[ClassMembership, ...]:
    """Return non-empty classes and their zero-based component indices."""
    raw_diameters = tuple(component_diameters_m)
    limits = np.asarray(limits_m, dtype=np.float64)
    class_labels = tuple(labels)
    if not raw_diameters:
        raise SedimentGroupingError("No sediment fraction diameters are available.")
    if any(value is None for value in raw_diameters):
        raise SedimentGroupingError(
            "Every selected sediment fraction needs a grain diameter in the .sed file."
        )
    diameters = np.asarray(raw_diameters, dtype=np.float64)
    if not np.isfinite(diameters).all() or np.any(diameters <= 0):
        raise SedimentGroupingError(
            "Sediment fraction diameters must be finite and positive."
        )
    if (
        limits.ndim != 1
        or limits.size == 0
        or not np.isfinite(limits).all()
        or np.any(limits <= 0)
        or np.any(np.diff(limits) <= 0)
    ):
        raise SedimentGroupingError(
            "Grain-size limits must be finite, positive, and strictly increasing."
        )
    if (
        len(class_labels) != limits.size + 1
        or any(not label or label != label.strip() for label in class_labels)
        or len({label.casefold() for label in class_labels})
        != len(class_labels)
    ):
        raise SedimentGroupingError(
            "Provide one unique, non-empty trimmed label per sediment-size class."
        )

    masks = [diameters < limits[0]]
    if limits.size == 1:
        masks.append(diameters >= limits[0])
    else:
        for index in range(1, limits.size):
            upper_mask = (
                diameters <= limits[index]
                if index == limits.size - 1
                else diameters < limits[index]
            )
            masks.append((diameters >= limits[index - 1]) & upper_mask)
        masks.append(diameters > limits[-1])

    memberships: list[ClassMembership] = []
    for label, mask in zip(class_labels, masks, strict=True):
        indices = tuple(int(index) for index in np.flatnonzero(mask))
        if indices:
            memberships.append((label, indices))
    return tuple(memberships)


def group_sediment_fraction_components(
    values: ArrayLike,
    component_diameters_m: Sequence[float | None],
    limits_m: Sequence[float],
    labels: Sequence[str],
) -> tuple[FloatArray, tuple[str, ...]]:
    """Sum the final component axis into non-empty sediment-size classes."""
    data = np.asarray(values, dtype=np.float64)
    if data.ndim == 0:
        raise SedimentGroupingError("Grouped values need a component axis.")
    if data.shape[-1] != len(component_diameters_m):
        raise SedimentGroupingError(
            "The number of component values does not match the sediment diameters."
        )
    memberships = sediment_class_membership(
        component_diameters_m,
        limits_m,
        labels,
    )
    grouped = np.stack(
        [
            np.sum(data[..., indices], axis=-1, dtype=np.float64)
            for _, indices in memberships
        ],
        axis=-1,
    )
    return grouped, tuple(label for label, _ in memberships)


def sediment_class_grouping_key(
    limits_m: Sequence[float],
    labels: Sequence[str],
) -> str:
    """Build a stable cache-selection key for one sediment-class definition."""
    payload = json.dumps(
        {"limits_m": list(limits_m), "labels": list(labels)},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    digest = blake2b(payload.encode("utf-8"), digest_size=8).hexdigest()
    return f"sediment-classes-{digest}"


__all__ = [
    "ClassMembership",
    "SedimentGroupingError",
    "group_sediment_fraction_components",
    "sediment_class_grouping_key",
    "sediment_class_membership",
]
