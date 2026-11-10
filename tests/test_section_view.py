# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the distance by elevation section view (draw-once polygons)."""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pytest

from d3dplot.plotting.views.section import SectionView


def _view() -> SectionView:
    # Crossing 0 spans x 0..1 with interfaces 0, 1, 3; crossing 1 spans x 1..2
    # with interfaces 0, 2, 3. Layer 0 is below layer 1 in both crossings.
    return SectionView.create(
        np.array([0.0, 1.0]),
        np.array([1.0, 2.0]),
        np.array([[0.0, 1.0, 3.0], [0.0, 2.0, 3.0]]),
        np.array([[1.0, 2.0], [3.0, 4.0]]),
        colormap="viridis",
        colorbar_label="value",
        x_label="distance",
        y_label="elevation",
        title="section",
    )


@pytest.mark.unit
def test_polygons_follow_crossing_major_layer_order() -> None:
    view = _view()
    try:
        paths = view.collection.get_paths()
        assert len(paths) == 4
        np.testing.assert_allclose(
            paths[0].vertices[:4], [[0, 0], [1, 0], [1, 1], [0, 1]]
        )
        np.testing.assert_allclose(
            paths[1].vertices[:4], [[0, 1], [1, 1], [1, 3], [0, 3]]
        )
        np.testing.assert_allclose(
            paths[2].vertices[:4], [[1, 0], [2, 0], [2, 2], [1, 2]]
        )
        np.testing.assert_allclose(
            paths[3].vertices[:4], [[1, 2], [2, 2], [2, 3], [1, 3]]
        )
        np.testing.assert_allclose(view.collection.get_array(), [1, 2, 3, 4])
        assert view.axes.get_xlim() == pytest.approx((0.0, 2.0))
        assert view.axes.get_ylim() == pytest.approx((0.0, 3.0))
    finally:
        plt.close(view.figure)


@pytest.mark.unit
def test_update_redraws_the_same_artists_with_new_geometry() -> None:
    view = _view()
    collection = view.collection
    try:
        view.update(
            np.array([[0.0, 1.0, 2.0], [0.0, 1.0, 2.0]]),
            np.array([[5.0, 6.0], [7.0, 8.0]]),
            title="updated",
        )
        assert view.collection is collection
        np.testing.assert_allclose(view.collection.get_array(), [5, 6, 7, 8])
        np.testing.assert_allclose(
            view.collection.get_paths()[1].vertices[:4],
            [[0, 1], [1, 1], [1, 2], [0, 2]],
        )
        assert view.axes.get_ylim() == pytest.approx((0.0, 2.0))
        assert view.axes.get_title() == "updated"
    finally:
        plt.close(view.figure)


@pytest.mark.unit
def test_nan_values_are_masked_and_shape_changes_are_rejected() -> None:
    view = SectionView.create(
        np.array([0.0, 1.0]),
        np.array([1.0, 2.0]),
        np.array([[0.0, 1.0], [0.0, 1.0]]),
        np.array([[np.nan], [2.0]]),
        colormap="viridis",
        colorbar_label="value",
        x_label="distance",
        y_label="elevation",
        title="section",
    )
    try:
        assert np.ma.getmaskarray(view.collection.get_array()).tolist() == [
            True,
            False,
        ]
        with pytest.raises(ValueError, match="keeps the drawn shape"):
            view.update(np.zeros((2, 3)), np.zeros((2, 2)))
    finally:
        plt.close(view.figure)


@pytest.mark.unit
def test_create_rejects_values_without_one_row_per_crossing() -> None:
    with pytest.raises(ValueError, match="SectionView values must have shape"):
        SectionView.create(
            np.array([0.0, 1.0]),
            np.array([1.0, 2.0]),
            np.array([[0.0, 1.0], [0.0, 1.0]]),
            np.array([1.0, 2.0]),
            colormap="viridis",
            colorbar_label="value",
            x_label="distance",
            y_label="elevation",
            title="section",
        )
