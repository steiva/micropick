"""Homography fitting on synthetic points, no cameras.

A known homography maps the upper-camera crosshairs to the lower camera; the
under neighbours are shuffled to prove the axes-based matching recovers the
correspondence without a shift/reverse search.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from micropick.core.calibration.homography import (Homography, HomographyError,
                                                   fit_homography)

# Upper-camera crosshairs: a centre and four neighbours at the compass points.
OVER_CENTRE = np.array([1300.0, 950.0])
OVER_NEIGHBOURS = OVER_CENTRE + np.array([[200.0, 0.0], [0.0, 200.0],
                                          [-200.0, 0.0], [0.0, -200.0]])

# A homography that includes a mirror, like the real over->under geometry.
AXES = np.array([[0.0, 1.0], [1.0, 0.0]])          # swap x/y, det = -1
H_TRUE = np.array([[0.0, 0.9, 100.0],
                   [0.9, 0.0, 50.0],
                   [0.0, 0.0, 1.0]])


def _project(H, pts):
    p = np.asarray(pts, np.float32).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(p, H).reshape(-1, 2)


def test_fit_recovers_homography_despite_shuffle():
    under_c = _project(H_TRUE, [OVER_CENTRE])[0]
    under_n = _project(H_TRUE, OVER_NEIGHBOURS)
    perm = [2, 0, 3, 1]                              # shuffle the under order
    under_shuffled = under_n[perm]

    H, report = fit_homography(OVER_CENTRE, OVER_NEIGHBOURS,
                               under_c, under_shuffled, AXES)

    assert report.reproj_max_px < 1.0
    # H maps the upper points onto the (unshuffled) lower points
    got = _project(H, OVER_NEIGHBOURS)
    assert np.allclose(got, under_n, atol=1.0)
    # the reported order undoes the shuffle
    assert [perm[j] for j in report.order] == [0, 1, 2, 3]


def test_inconsistent_points_raise():
    rng = np.random.default_rng(0)
    under_c = np.array([100.0, 100.0])
    under_n = rng.uniform(0, 2000, size=(4, 2))     # unrelated to the over points
    with pytest.raises(HomographyError):
        fit_homography(OVER_CENTRE, OVER_NEIGHBOURS, under_c, under_n, AXES,
                       max_reproj_px=3.0)


def test_too_few_neighbours_raise():
    with pytest.raises(HomographyError):
        fit_homography(OVER_CENTRE, OVER_NEIGHBOURS[:3],
                       [0, 0], [[1, 1], [2, 2], [3, 3]], AXES)


def test_over_to_under_pose_gated():
    under_c = _project(H_TRUE, [OVER_CENTRE])[0]
    under_n = _project(H_TRUE, OVER_NEIGHBOURS)
    H, report = fit_homography(OVER_CENTRE, OVER_NEIGHBOURS, under_c, under_n, AXES)

    hom = Homography(H, gantry_xy=(150.0, 160.0))
    # at the stored pose it applies and matches the direct projection
    got = hom.over_to_under(OVER_NEIGHBOURS, (150.0, 160.02), tol_mm=2.0)
    assert got is not None and np.allclose(got, under_n, atol=1.0)
    # far from it, it refuses rather than returning a wrong ROI
    assert hom.over_to_under(OVER_NEIGHBOURS, (200.0, 160.0), tol_mm=2.0) is None


def test_config_round_trip():
    under_c = _project(H_TRUE, [OVER_CENTRE])[0]
    under_n = _project(H_TRUE, OVER_NEIGHBOURS)
    H, report = fit_homography(OVER_CENTRE, OVER_NEIGHBOURS, under_c, under_n, AXES)

    cfg = Homography(H, (150.0, 160.0)).to_config(report)
    back = Homography.from_config(cfg)
    assert np.allclose(back.matrix, H)
    assert cfg.n_points == 5 and cfg.reproj_max_px < 1.0
