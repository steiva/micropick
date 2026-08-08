"""Standalone homography workflow on a fake detector and a MockRobot, no cameras.

The detector is stubbed to return canned views for the two cameras; the robot is
only read for its pose. This checks the plumbing: views in, a CameraHomography
carrying the gantry pose out, and no robot moves.
"""
from __future__ import annotations

import cv2
import numpy as np

from micropick.hardware.mock import MockRobot
from micropick.workflows.calibrate_homography import calibrate_homography

AXES = np.array([[0.0, 1.0], [1.0, 0.0]])
H_TRUE = np.array([[0.0, 0.9, 100.0], [0.9, 0.0, 50.0], [0.0, 0.0, 1.0]])
OVER_C = np.array([1300.0, 950.0])
OVER_N = OVER_C + np.array([[200.0, 0.0], [0.0, 200.0], [-200.0, 0.0], [0.0, -200.0]])


def _project(pts):
    p = np.asarray(pts, np.float32).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(p, H_TRUE).reshape(-1, 2)


class _View:
    def __init__(self, centre, neighbours):
        self.centre = np.asarray(centre, float)
        self.neighbours = np.asarray(neighbours, float)


class FakeDetector:
    """Returns the upper view for the first camera it is asked about, the lower
    for the second, regardless of the camera object."""

    def __init__(self):
        self.over = _View(OVER_C, OVER_N)
        self.under = _View(_project([OVER_C])[0], _project(OVER_N))
        self._calls = 0

    def view(self, camera, **kw):
        self._calls += 1
        return self.over if self._calls == 1 else self.under


def test_calibrate_homography_returns_config_with_pose():
    robot = MockRobot(position=(150.0, 160.0, 100.0), noise_mm=0.0)
    before = robot.moves
    result = calibrate_homography(robot, over_cam=object(), under_cam=object(),
                                  detector=FakeDetector(),
                                  target=type("T", (), {"spacing_mm": 20.25,
                                                        "axes": AXES})(),
                                  log=lambda *a: None)

    assert robot.moves == before                       # nothing was commanded
    assert result.report.reproj_max_px < 1.0
    assert result.homography.n_points == 5
    assert np.allclose(result.homography.gantry_xy, [150.0, 160.0])
    assert np.array(result.homography.matrix).shape == (3, 3)
