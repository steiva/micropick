"""Upper-to-lower camera homography.

Two looks at the crosshair disc, one per camera, are enough: each view carries a
centre and four neighbours, and five points fit a homography. This runs either
as a by-product of the pipette calibration, which already takes both views, or
on its own with no pipette and no robot moves.

The result is only valid for the marker plane and the gantry pose the upper
frame was taken at; see `core.calibration.homography` for what that means and
why it is a viewing aid, not a positioning tool.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..config.schema import CameraHomography, TipTarget
from ..core.calibration.homography import (Homography, HomographyReport,
                                           fit_homography)
from ..hardware.protocols import Camera, Robot, xyz

__all__ = ["HomographyResult", "homography_from_views", "calibrate_homography"]


@dataclass
class HomographyResult:
    homography: CameraHomography
    report: HomographyReport
    gantry_xy: tuple[float, float]
    over_view: object = None
    under_view: object = None

    def __str__(self) -> str:
        return str(self.report)


def homography_from_views(over_view, under_view, rotation_deg, gantry_xy, *,
                          over_resolution, under_resolution,
                          max_centre_px: float = 15.0
                          ) -> tuple[CameraHomography, HomographyReport]:
    """Fit from two `PatternView`-like objects (each with centre and neighbours).

    Duck-typed on `.centre` and `.neighbours` so this stays independent of the
    pipette workflow that also produces those views.

    The resolutions are those the two views were taken at. They are not optional:
    a matrix without them cannot be applied to the modes the clip records at, and
    a matrix applied at the wrong mode is worse than none, because it places the
    box off frame and says nothing.
    """
    H, report = fit_homography(
        over_view.centre, over_view.neighbours,
        under_view.centre, under_view.neighbours,
        float(rotation_deg), max_centre_px=max_centre_px)
    config = Homography(H, gantry_xy, over_resolution,
                        under_resolution).to_config(report)
    return config, report


def calibrate_homography(robot: Robot, over_cam: Camera, under_cam: Camera,
                         detector, *, target: TipTarget | None = None,
                         frames: int = 7, max_centre_px: float = 15.0,
                         log=print) -> HomographyResult:
    """Fit the homography from one disc seen in both cameras. No pipette, no moves.

    Reads a view from each camera and the gantry pose the upper view was taken
    at, then fits. The robot is only read, never commanded, so this works with
    no tip fitted.
    """
    target = target or TipTarget()

    log("upper camera: locating the crosshair")
    over_view = detector.view(over_cam, spacing_mm=target.spacing_mm,
                              frames=frames)
    gx, gy, _ = xyz(robot)
    log("lower camera: locating the crosshair")
    under_view = detector.view(under_cam, spacing_mm=target.spacing_mm,
                               frames=frames)

    config, report = homography_from_views(
        over_view, under_view, target.under_rotation_deg, (gx, gy),
        over_resolution=over_cam.resolution,
        under_resolution=under_cam.resolution,
        max_centre_px=max_centre_px)
    log(str(report))
    return HomographyResult(config, report, (float(gx), float(gy)),
                            over_view, under_view)
