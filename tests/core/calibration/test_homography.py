"""Homography fitting on synthetic points, no cameras.

A known homography maps the upper-camera crosshairs to the lower camera: a
ninety-degree turn and a scale, no mirror, as the module geometry is. The
under neighbours are shuffled to prove the angle matching recovers the
correspondence, and the held-out centre is what the fit is judged by.
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

# +90 degrees in pixel convention (v grows down): (1, 0) goes to (0, 1).
ROTATION = 90.0
H_TRUE = np.array([[0.0, -0.9, 2100.0],
                   [0.9, 0.0, 300.0],
                   [0.0, 0.0, 1.0]])

OVER_RES, UNDER_RES = (2592, 1944), (4000, 3000)


def _project(H, pts):
    p = np.asarray(pts, np.float32).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(p, H).reshape(-1, 2)


def _under():
    return _project(H_TRUE, [OVER_CENTRE])[0], _project(H_TRUE, OVER_NEIGHBOURS)


def test_fit_recovers_homography_despite_shuffle():
    under_c, under_n = _under()
    perm = [2, 0, 3, 1]                              # shuffle the under order
    H, report = fit_homography(OVER_CENTRE, OVER_NEIGHBOURS,
                               under_c, under_n[perm], ROTATION)

    assert report.centre_error_px < 1.0
    assert report.det > 0
    assert abs(report.rotation_deg - ROTATION) < 1.0
    assert not report.warnings
    # H maps the upper points onto the (unshuffled) lower points
    assert np.allclose(_project(H, OVER_NEIGHBOURS), under_n, atol=1.0)
    # the reported order undoes the shuffle
    assert [perm[j] for j in report.order] == [0, 1, 2, 3]


def test_a_moved_centre_is_refused():
    """The centre takes no part in the fit, so it is the check that can fail."""
    under_c, under_n = _under()
    with pytest.raises(HomographyError):
        fit_homography(OVER_CENTRE, OVER_NEIGHBOURS, under_c + [40.0, 0.0],
                       under_n, ROTATION, max_centre_px=15.0)


def test_a_wrong_rotation_that_cannot_match_is_refused():
    """Forty-five degrees off sits on the decision boundary: no guessing."""
    under_c, under_n = _under()
    with pytest.raises(HomographyError):
        fit_homography(OVER_CENTRE, OVER_NEIGHBOURS, under_c, under_n,
                       ROTATION + 45.0)


def test_too_few_neighbours_raise():
    with pytest.raises(HomographyError):
        fit_homography(OVER_CENTRE, OVER_NEIGHBOURS[:3],
                       [0, 0], [[1, 1], [2, 2], [3, 3]], ROTATION)


def test_applied_in_the_modes_it_was_fitted_at():
    under_c, under_n = _under()
    H, _ = fit_homography(OVER_CENTRE, OVER_NEIGHBOURS, under_c, under_n,
                          ROTATION)
    hom = Homography(H, (150.0, 160.0), OVER_RES, UNDER_RES)
    got = hom.over_to_under(OVER_NEIGHBOURS, (150.0, 160.0), OVER_RES, UNDER_RES)
    assert np.allclose(got, under_n, atol=1.0)
    assert hom.drift_mm((153.0, 164.0)) == pytest.approx(5.0)


def test_rescaled_to_a_smaller_recording_mode():
    """Fitted at 4000x3000, recorded at 2000x1500: every lower pixel halves."""
    under_c, under_n = _under()
    H, _ = fit_homography(OVER_CENTRE, OVER_NEIGHBOURS, under_c, under_n,
                          ROTATION)
    hom = Homography(H, (150.0, 160.0), OVER_RES, UNDER_RES)
    got = hom.over_to_under(OVER_NEIGHBOURS, (150.0, 160.0), OVER_RES,
                            (2000, 1500))
    assert np.allclose(got, under_n / 2.0, atol=1.0)


def test_a_crop_cannot_be_rescaled():
    under_c, under_n = _under()
    H, _ = fit_homography(OVER_CENTRE, OVER_NEIGHBOURS, under_c, under_n,
                          ROTATION)
    hom = Homography(H, (150.0, 160.0), OVER_RES, UNDER_RES)
    with pytest.raises(HomographyError):
        hom.for_resolution(OVER_RES, (1920, 1080))


def test_without_stored_modes_it_refuses_to_guess():
    hom = Homography(H_TRUE, (150.0, 160.0))
    with pytest.raises(HomographyError):
        hom.for_resolution(OVER_RES, UNDER_RES)


def test_config_round_trip():
    under_c, under_n = _under()
    H, report = fit_homography(OVER_CENTRE, OVER_NEIGHBOURS, under_c, under_n,
                               ROTATION)

    cfg = Homography(H, (150.0, 160.0), OVER_RES, UNDER_RES).to_config(report)
    back = Homography.from_config(cfg)
    assert np.allclose(back.matrix, H)
    assert back.over_resolution == OVER_RES
    assert back.under_resolution == UNDER_RES
    assert cfg.n_points == 5 and cfg.reproj_max_px < 1.0
