"""Homography from the upper (gantry) camera to the lower one.

The pipette calibration already looks at the same crosshair disc through both
cameras, and each view yields five points: the central crosshair plus its four
neighbours. So the camera-to-camera map comes for free from data already
collected, with a standalone routine for when the pipette is not involved.

Correspondence from the known orientation, not from the residual
----------------------------------------------------------------
The crosshairs are identical and the pattern is four-fold symmetric, so nothing
in the pictures says which neighbour is which. They are matched by angle about
the disc centre, using the one fact that is fixed by the module geometry: the
lower camera is turned ninety degrees from the upper one and does not mirror.
West above is north below, north is east, east is south, south is west. Angles
are measured as atan2(dv, du) with v growing downwards, in which convention that
rotation is a clean `theta_under = theta_over + 90`, and the sign is the single
place a ninety-degree error can hide. It lives in the profile
(`TipTarget.under_rotation_deg`) for that reason.

What the old version got wrong: it matched through `TipTarget.axes`, which maps
lower-camera pixels to *millimetres* and carries a mirror of its own from the v
axis, and then claimed the reprojection error would catch a bad match. It cannot.
Five points against eight degrees of freedom fit almost anything, so a
correspondence rotated by ninety or a hundred and eighty degrees produced a
near-zero residual and a matrix that put the box on the wrong cuboid.

The limit of what any of this can prove
---------------------------------------
The disc is four-fold symmetric and the fifth point sits at its centre of
symmetry, so **no arithmetic on these five points can reveal a wrong assumed
rotation, or a mirror.** Ask for the wrong rotation and the matching pairs each
crosshair with a different neighbour, the four points fit exactly again, the
held-out centre still lands on the centre, and the decomposition reports back
the very rotation that was assumed. This is not a gap to be closed with a better
threshold; it is what symmetry means. The orientation is therefore a hardware
fact kept in the profile rather than a fitted quantity, and the thing that
confirms it is the first clip: the box is on the chosen cuboid or it is not.

What the numbers below do catch is everything else: a mis-detected crosshair, a
degenerate arrangement, a disc that moved between the two views, a correspondence
handed in from outside, and — the failure that actually happened — a matrix used
in the pixels of a mode it was not fitted at.

Four points fit, the centre checks
----------------------------------
Four points determine a homography exactly: their residual is zero by
construction, whatever the correspondence, so it measures nothing. The central
crosshair is therefore held out of the fit and reprojected, and its error is the
only honest number here. The matrix is decomposed as well: a negative
determinant means a mirror crept in and is refused outright, and a rotation far
from the expected ninety degrees is reported.

Pixels belong to a camera mode
------------------------------
A matrix maps the pixels of one pair of camera modes to another. The tip
calibration runs the lower camera at 4000x3000 for precision and the pickup clip
records at 2000x1500; a matrix fitted at the first and applied at the second
places every point at twice its coordinate, outside the frame. Both resolutions
are stored with the matrix and `for_resolution` rescales it to the modes
actually in use. That holds because these modes share a field of view; a mode
that crops the sensor instead scales the axes differently, which is detected and
refused rather than silently applied.

What the homography is good for
-------------------------------
It is valid ONLY for the marker plane, at the marker's height, and ONLY near the
gantry pose the upper frame was taken at, because the upper camera rides on the
gantry while the lower one is fixed. The pose is stored with the matrix and
`drift_mm` reports the distance from it; the caller warns. It used to refuse
silently past two millimetres, which is how the ROI box could vanish with
nothing said. Cuboids sit lower, on the dish bottom, so this maps them only
approximately: it is fine for drawing a coarse ROI box on the lower-camera video
and must NOT be used to position the robot. The old `transform_to_top` radial
`k` fudge that tried to bridge the two planes is deliberately not carried over.

This module imports no hardware and no display code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    """What the fit is worth: the held-out centre, and the shape of the matrix."""

    centre_error_px: float          # the crosshair left out of the fit
    rotation_deg: float             # from the decomposed matrix
    det: float                      # positive means no mirror
    angle_error_deg: float          # worst mismatch in the correspondence
    n_points: int
    order: list[int]                # matched under-neighbour index per upper
    reproj_max_px: float            # of the fitted four: ~0, catches degeneracy
    warnings: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        text = (f"homography: held-out centre {self.centre_error_px:.2f} px, "
                f"rotation {self.rotation_deg:+.1f} deg, det {self.det:+.3f}, "
                f"match within {self.angle_error_deg:.1f} deg")
        for w in self.warnings:
            text += f"\n  warning: {w}"
        return text


def _angles_deg(dirs: np.ndarray) -> np.ndarray:
    """Direction angles in the pixel convention: v grows down, so +90 is
    clockwise on screen."""
    return np.degrees(np.arctan2(dirs[:, 1], dirs[:, 0]))


def _wrap180(deg):
    return (np.asarray(deg) + 180.0) % 360.0 - 180.0


def _match_by_angle(over_dirs: np.ndarray, under_dirs: np.ndarray,
                    rotation_deg: float, max_angle_err_deg: float
                    ) -> tuple[list[int], float]:
    """Match the four neighbours by where the known rotation says they land.

    Returns (order, worst angular error). Refuses rather than guessing: two
    upper crosshairs claiming the same lower one, or a mismatch approaching the
    forty-five degree decision boundary, means the assumed orientation is wrong,
    and every downstream check would pass anyway.
    """
    predicted = _angles_deg(over_dirs) + float(rotation_deg)
    actual = _angles_deg(under_dirs)
    diff = _wrap180(predicted[:, None] - actual[None, :])
    order = [int(i) for i in np.argmin(np.abs(diff), axis=1)]
    worst = float(np.abs(diff[np.arange(len(order)), order]).max())

    if len(set(order)) != len(order):
        raise HomographyError(
            f"the crosshairs could not be matched one to one (got {order} for "
            f"upper angles {np.round(_angles_deg(over_dirs), 1).tolist()} and "
            f"lower {np.round(actual, 1).tolist()}). Either a crosshair was "
            f"mis-detected or under_rotation_deg={rotation_deg:g} is wrong")
    if worst > max_angle_err_deg:
        raise HomographyError(
            f"the best match is {worst:.1f} deg off, beyond {max_angle_err_deg:g}. "
            f"The crosshairs sit ninety degrees apart, so this is close to "
            f"picking a neighbour at random; check under_rotation_deg="
            f"{rotation_deg:g} and its sign (v grows downwards)")
    return order, worst


def _decompose(H: np.ndarray) -> tuple[float, float]:
    """(rotation in degrees, determinant) of the linear part.

    Exact for a similarity and close enough for the mild perspective here; it is
    a check on orientation, not a measurement.
    """
    if abs(H[2, 2]) < 1e-12:
        raise HomographyError("the fitted matrix is not normalisable (h33 ~ 0)")
    A = H[:2, :2] / H[2, 2]
    return (float(np.degrees(np.arctan2(A[1, 0], A[0, 0]))),
            float(np.linalg.det(A)))


def fit_homography(over_centre, over_neighbours, under_centre, under_neighbours,
                   rotation_deg: float = 90.0, *, max_centre_px: float = 15.0,
                   max_angle_err_deg: float = 25.0,
                   rotation_tol_deg: float = 15.0
                   ) -> tuple[np.ndarray, HomographyReport]:
    """Fit the upper-to-lower homography from one disc seen in both cameras.

    over_/under_centre      the central crosshair pixel in each camera.
    over_/under_neighbours  the four surrounding crosshairs, in any order.
    rotation_deg            how the lower view is turned from the upper one, in
                            pixels (`TipTarget.under_rotation_deg`).

    Raises HomographyError if fewer than four neighbours are given, if the
    correspondence cannot be made from the given rotation, if the fit is
    degenerate or mirrored, or if the held-out centre reprojects further than
    max_centre_px.
    """
    over_c = np.asarray(over_centre, dtype=np.float64).reshape(2)
    under_c = np.asarray(under_centre, dtype=np.float64).reshape(2)
    over_n = np.asarray(over_neighbours, dtype=np.float64).reshape(-1, 2)
    under_n = np.asarray(under_neighbours, dtype=np.float64).reshape(-1, 2)

    if len(over_n) < 4 or len(under_n) < 4:
        raise HomographyError(
            f"need four neighbours in each view, got {len(over_n)} and "
            f"{len(under_n)}")

    order, angle_err = _match_by_angle(over_n - over_c, under_n - under_c,
                                       rotation_deg, max_angle_err_deg)

    # The centre stays out: four points fit exactly, so only a point that took
    # no part in the fit can disagree with it.
    src = over_n.astype(np.float32)
    dst = under_n[order].astype(np.float32)
    H, _ = cv2.findHomography(src, dst, method=0)
    if H is None:
        raise HomographyError("the fit was degenerate (collinear crosshairs?)")

    rotation, det = _decompose(H)
    if det <= 0:
        # Unreachable from a correspondence this module chose, since matching by
        # angle can only produce a rotation; it fires on a mis-detected or
        # collinear arrangement, and would fire on a correspondence from outside.
        raise HomographyError(
            f"the fitted matrix mirrors (determinant {det:+.3f}). The two views "
            f"differ by a rotation only, so this means the correspondence is "
            f"wrong; nothing is saved")

    proj = cv2.perspectiveTransform(
        np.vstack([over_c, over_n]).astype(np.float32).reshape(-1, 1, 2),
        H).reshape(-1, 2)
    centre_error = float(np.linalg.norm(proj[0] - under_c))
    fitted_max = float(np.linalg.norm(proj[1:] - dst, axis=1).max())

    # The decomposed rotation agrees with the assumed one by construction, so
    # this disagrees only when the four points are not related by a rotation at
    # all: a crosshair mis-detected, or the disc moved between the two views.
    warnings = []
    off = float(abs(_wrap180(rotation - rotation_deg)))
    if off > rotation_tol_deg:
        warnings.append(
            f"rotation came out {rotation:+.1f} deg, {off:.1f} deg from the "
            f"assumed {rotation_deg:+g}; since the matching assumed that "
            f"rotation, this means the crosshairs are not related by one")

    report = HomographyReport(
        centre_error_px=centre_error, rotation_deg=rotation, det=det,
        angle_error_deg=angle_err, n_points=len(src) + 1, order=order,
        reproj_max_px=fitted_max, warnings=warnings)

    if centre_error > max_centre_px:
        raise HomographyError(
            f"the central crosshair reprojects {centre_error:.1f} px away, "
            f"beyond {max_centre_px:g}. It took no part in the fit, so this is "
            f"the real error: a detection or the correspondence is wrong. "
            f"{report}")
    return H, report


class Homography:
    """Applies a fitted upper-to-lower homography in the pixels of a given mode.

    Holds the matrix, the gantry XY of the upper frame it was fitted at, and the
    resolution of each camera at the time. The pose matters because the upper
    camera moves with the gantry; the resolutions matter because a matrix is in
    the pixels of the modes it was fitted at and the recording uses others.
    """

    def __init__(self, matrix, gantry_xy, over_resolution=None,
                 under_resolution=None):
        self.matrix = np.asarray(matrix, dtype=np.float64).reshape(3, 3)
        self.gantry_xy = np.asarray(gantry_xy, dtype=np.float64).reshape(2)
        self.over_resolution = (tuple(int(v) for v in over_resolution)
                                if over_resolution is not None else None)
        self.under_resolution = (tuple(int(v) for v in under_resolution)
                                 if under_resolution is not None else None)

    @classmethod
    def from_config(cls, config: CameraHomography) -> "Homography":
        return cls(config.matrix, config.gantry_xy,
                   config.over_resolution, config.under_resolution)

    def to_config(self, report: HomographyReport | None = None) -> CameraHomography:
        return CameraHomography(
            matrix=[[float(v) for v in row] for row in self.matrix],
            gantry_xy=[float(v) for v in self.gantry_xy],
            over_resolution=list(self.over_resolution) if self.over_resolution else None,
            under_resolution=list(self.under_resolution) if self.under_resolution else None,
            centre_error_px=report.centre_error_px if report else None,
            rotation_deg=report.rotation_deg if report else None,
            det=report.det if report else None,
            reproj_max_px=report.reproj_max_px if report else None,
            n_points=report.n_points if report else None,
            measured_at=datetime.now(timezone.utc),
        )

    def drift_mm(self, gantry_now) -> float:
        """How far the gantry is from the pose this was fitted at."""
        g = np.asarray(gantry_now, dtype=np.float64).reshape(-1)[:2]
        return float(np.linalg.norm(g - self.gantry_xy))

    @staticmethod
    def _scale(fitted, now, what: str) -> tuple[float, float]:
        sx = float(now[0]) / float(fitted[0])
        sy = float(now[1]) / float(fitted[1])
        if abs(sx - sy) > 1e-3 * max(sx, sy):
            raise HomographyError(
                f"the {what} camera went from {tuple(fitted)} to {tuple(now)}, "
                f"which scales the axes differently ({sx:.4f} vs {sy:.4f}). "
                f"That is a sensor crop, not a change of scale, so the "
                f"homography cannot be rescaled; refit it at the mode in use")
        return sx, sy

    def for_resolution(self, over_resolution, under_resolution) -> np.ndarray:
        """The same map expressed in the pixels of the given camera modes.

        Current upper pixels are scaled back into the fitted mode, the matrix is
        applied there, and the result is scaled into the current lower mode:
        H' = D_under^-1 . H . D_over.
        """
        if self.over_resolution is None or self.under_resolution is None:
            raise HomographyError(
                "this homography does not record the camera modes it was "
                "fitted at, so it cannot be applied to any other; re-run the "
                "tip calibration to store them")
        sox, soy = self._scale(self.over_resolution, over_resolution, "upper")
        sux, suy = self._scale(self.under_resolution, under_resolution, "lower")
        d_over = np.diag([1.0 / sox, 1.0 / soy, 1.0])      # now -> fitted
        d_under = np.diag([sux, suy, 1.0])                 # fitted -> now
        return d_under @ self.matrix @ d_over

    def over_to_under(self, points, gantry_now, over_resolution,
                      under_resolution) -> np.ndarray:
        """Lower-camera pixels for the given upper-camera pixels.

        The resolutions are the modes the caller is working in right now, not
        the ones this was fitted at. Pose drift is not checked here: the caller
        holds `drift_mm` and decides what to say about it, because a coarse
        viewing aid drawn a little off is worth more than one that silently
        never appears.
        """
        H = self.for_resolution(over_resolution, under_resolution)
        pts = np.asarray(points, dtype=np.float32).reshape(-1, 1, 2)
        return cv2.perspectiveTransform(pts, H).reshape(-1, 2)
