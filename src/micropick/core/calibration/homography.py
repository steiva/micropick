"""Homography from the upper (gantry) camera to the lower one.

The pipette calibration already looks at the same crosshair disc through both
cameras, and each view yields five points: the central crosshair plus its four
neighbours. Five points are enough for a homography, so the camera-to-camera map
comes for free from data already collected, with a standalone routine for when
the pipette is not involved.

Correspondence without guessing
-------------------------------
The two cameras see the disc in different orientations. The rotation-and-mirror
between them is already known and validated: it is `TipTarget.axes`, whose
determinant is -1 because the lower camera is turned ninety degrees and views
from the opposite side. So the four neighbours are matched by predicting the
lower-camera direction of each upper-camera neighbour with `inv(axes)` and
taking the nearest by angle, rather than searching over a cyclic shift and a
reverse flag as the old `add_ordering` did. A wrong match would blow up the
reprojection error, which is checked, so the correspondence is self-verifying.

No RANSAC
---------
Five points do not support outlier rejection; RANSAC on them is theatre. This
fits with ordinary least squares and reports the honest reprojection error,
refusing if it is above a threshold.

What the homography is good for
-------------------------------
It is valid ONLY for the marker plane, at the marker's height, and ONLY for the
gantry pose the upper frame was taken at, because the upper camera rides on the
gantry while the lower one is fixed. The gantry pose is therefore stored with
the matrix, and `Homography.over_to_under` refuses to apply it from a different
pose. Cuboids sit lower, on the dish bottom, so this maps them only
approximately: it is fine for drawing a coarse ROI box on the lower-camera video
and must NOT be used to position the robot. The old `transform_to_top` radial
`k` fudge that tried to bridge the two planes is deliberately not carried over.

This module imports no hardware and no display code.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import cv2
import numpy as np

from ...config.schema import CameraHomography

__all__ = ["Homography", "HomographyReport", "HomographyError",
           "fit_homography"]


class HomographyError(RuntimeError):
    """The two views could not be turned into a trustworthy homography."""


@dataclass
class HomographyReport:
    """Reprojection error of the fit, in pixels of the lower camera."""

    reproj_mean_px: float
    reproj_max_px: float
    n_points: int
    per_point_px: list[float]
    order: list[int]                    # matched under-neighbour index per upper

    def __str__(self) -> str:
        return (f"homography from {self.n_points} points: reprojection "
                f"mean {self.reproj_mean_px:.2f} px, max {self.reproj_max_px:.2f} px")


def _match_neighbours(over_dirs: np.ndarray, under_dirs: np.ndarray,
                      axes: np.ndarray) -> list[int]:
    """Return, for each upper neighbour, the index of the lower neighbour it
    corresponds to. Uses axes to rotate/mirror upper directions into the lower
    camera's orientation, then matches by angle."""
    predicted = over_dirs @ np.linalg.inv(axes).T      # upper dirs in lower frame
    pn = predicted / np.linalg.norm(predicted, axis=1, keepdims=True)
    un = under_dirs / np.linalg.norm(under_dirs, axis=1, keepdims=True)
    cost = -(pn @ un.T)                                 # smaller is better
    try:
        from scipy.optimize import linear_sum_assignment
        _, cols = linear_sum_assignment(cost)
        return list(cols)
    except Exception:
        # greedy fallback: four points, so this is fine
        order, used = [], set()
        for row in cost:
            j = int(min((c for c in range(len(row)) if c not in used),
                        key=lambda c: row[c]))
            order.append(j)
            used.add(j)
        return order


def fit_homography(over_centre, over_neighbours, under_centre, under_neighbours,
                   axes, *, max_reproj_px: float = 3.0
                   ) -> tuple[np.ndarray, HomographyReport]:
    """Fit the upper-to-lower homography from one disc seen in both cameras.

    over_/under_centre     the central crosshair pixel in each camera.
    over_/under_neighbours  the four surrounding crosshairs, in any order.
    axes                    the 2x2 rotation-and-mirror from TipTarget.

    Raises HomographyError if fewer than four neighbours are given, if the fit
    is degenerate, or if the reprojection error exceeds max_reproj_px, which is
    also what catches a wrong point correspondence.
    """
    over_c = np.asarray(over_centre, dtype=np.float64).reshape(2)
    under_c = np.asarray(under_centre, dtype=np.float64).reshape(2)
    over_n = np.asarray(over_neighbours, dtype=np.float64).reshape(-1, 2)
    under_n = np.asarray(under_neighbours, dtype=np.float64).reshape(-1, 2)
    axes = np.asarray(axes, dtype=np.float64).reshape(2, 2)

    if len(over_n) < 4 or len(under_n) < 4:
        raise HomographyError(
            f"need four neighbours in each view, got {len(over_n)} and "
            f"{len(under_n)}")
    if abs(np.linalg.det(axes)) < 1e-6:
        raise HomographyError("axes is singular; cannot orient the cameras")

    order = _match_neighbours(over_n - over_c, under_n - under_c, axes)
    src = np.vstack([over_c, over_n]).astype(np.float32)
    dst = np.vstack([under_c, under_n[order]]).astype(np.float32)

    H, _ = cv2.findHomography(src, dst, method=0)        # least squares, no RANSAC
    if H is None:
        raise HomographyError("the fit was degenerate (collinear points?)")

    proj = cv2.perspectiveTransform(src.reshape(-1, 1, 2), H).reshape(-1, 2)
    err = np.linalg.norm(proj - dst, axis=1)
    report = HomographyReport(
        reproj_mean_px=float(err.mean()), reproj_max_px=float(err.max()),
        n_points=len(src), per_point_px=[float(e) for e in err], order=order)
    if report.reproj_max_px > max_reproj_px:
        raise HomographyError(
            f"reprojection error {report.reproj_max_px:.2f} px exceeds "
            f"{max_reproj_px:.2f} px; the correspondence or a detection is "
            f"probably wrong. {report}")
    return H, report


class Homography:
    """Applies a fitted upper-to-lower homography, pose-checked.

    Holds the matrix and the gantry XY of the upper frame it was fitted at. The
    upper camera moves with the gantry, so the map is only correct at that pose;
    over_to_under refuses (returns None) when asked from elsewhere.
    """

    def __init__(self, matrix, gantry_xy):
        self.matrix = np.asarray(matrix, dtype=np.float64).reshape(3, 3)
        self.gantry_xy = np.asarray(gantry_xy, dtype=np.float64).reshape(2)

    @classmethod
    def from_config(cls, config: CameraHomography) -> "Homography":
        return cls(config.matrix, config.gantry_xy)

    def to_config(self, report: HomographyReport | None = None) -> CameraHomography:
        return CameraHomography(
            matrix=[[float(v) for v in row] for row in self.matrix],
            gantry_xy=[float(v) for v in self.gantry_xy],
            reproj_mean_px=report.reproj_mean_px if report else None,
            reproj_max_px=report.reproj_max_px if report else None,
            n_points=report.n_points if report else None,
            measured_at=datetime.now(timezone.utc),
        )

    def over_to_under(self, points, gantry_now, *, tol_mm: float = 2.0):
        """Lower-camera pixels for the given upper-camera pixels, or None.

        Returns None when the current gantry pose is more than tol_mm from the
        pose the homography was fitted at: the map does not hold there, and a
        coarse ROI is not worth compensating for across two calibration planes.
        """
        g = np.asarray(gantry_now, dtype=np.float64).reshape(-1)[:2]
        if float(np.linalg.norm(g - self.gantry_xy)) > tol_mm:
            return None
        pts = np.asarray(points, dtype=np.float32).reshape(-1, 1, 2)
        return cv2.perspectiveTransform(pts, self.matrix).reshape(-1, 2)
