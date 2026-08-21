"""Measuring the offset between the camera reference and the pipette tip.

Every tip sits slightly differently and no two are quite straight, so this has
to be redone whenever one is picked up. The result is one 2D vector, and it is
the only place the robot's own frame enters the picking pipeline; the optical
map knows nothing about it.

How it works
------------
The upper camera looks down at a disc carrying a central crosshair and four
more at a known radius. The pixel map turns the central crosshair into robot
coordinates, the current offset estimate is added, and the gantry drives there.
If the offset were exact the tip would now sit on the crosshair.

The lower camera then looks up and finds both the crosshair and the tip. The
four outer crosshairs give millimetres per pixel, since their spacing is known,
and the residual between tip and crosshair is the correction. Applying it moves
the tip onto the target, and the offset is read off as the difference between
where the gantry ended up and where the crosshair is.

The gantry is deliberately parked a few millimetres to one side before the
lower camera looks. On target the tip hides the crosshair, and neither can be
measured. The shift cancels out because the offset is computed from the final
pose rather than from the commanded one.

First run
---------
The routine drives to where it believes the target is before looking for it, so
a wildly wrong starting offset puts the tip outside the lower camera's field
and there is nothing to recover from. Measure it roughly with a ruler and write
it into the profile before the first automatic run.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from ..config.schema import CameraHomography, PipetteOffset, TipTarget
from ..core.calibration.homography import HomographyError, HomographyReport
from ..core.calibration.pixel_map import PixelMap
from ..hardware.protocols import (Camera, Robot, move_relative,
                                  move_to, xyz)
from .calibrate_homography import homography_from_views

__all__ = ["Detection", "PatternView", "TipDetector", "OffsetResult",
           "calibrate_pipette_offset", "TipCalibrationError"]


class TipCalibrationError(RuntimeError):
    pass


def _expected_under_resolution(under_cam, given, profile) -> tuple[int, int]:
    """The lower camera mode this calibration is defined at.

    From the profile's own `CameraSpec`, never from a constant here: which mode
    the module is calibrated at is an installation fact, and changing it should
    be an edit to cameras.json rather than to this file. `given` overrides, for
    a caller deliberately working at another mode.

    Refuses to guess. Without a number to compare against there is no check, and
    a check that quietly does not run is what let the calibration be taken at
    the wrong mode in the first place.
    """
    if given is not None:
        return tuple(int(v) for v in given)

    label = getattr(under_cam, "label", None)
    cameras = getattr(profile, "cameras", None) or {}
    if profile is None or not cameras:
        raise TipCalibrationError(
            "the lower camera's calibrated resolution is unknown: pass the "
            "profile, whose CameraSpec names it, or under_resolution "
            "explicitly. The scale of everything measured through that camera "
            "follows its mode, so the calibration is not run unchecked")
    if label not in cameras:
        raise TipCalibrationError(
            f"the lower camera calls itself {label!r}, which is not a camera in "
            f"profile {getattr(profile, 'name', '?')!r}; known: "
            f"{', '.join(sorted(cameras)) or 'none'}")
    return tuple(int(v) for v in cameras[label].default_resolution)


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------

@dataclass
class Detection:
    label: str
    xy: np.ndarray
    conf: float


@dataclass
class PatternView:
    """One reading of the crosshair disc, and possibly the tip."""

    centre: np.ndarray                      # central crosshair, pixels
    neighbours: np.ndarray                  # the four outer crosshairs
    tip: np.ndarray | None
    mm_per_px: float
    spread_px: float                        # disagreement between repeat frames
    radius_cv: float                        # scatter of the four radii
    n_frames: int
    frame: np.ndarray | None = None
    detections: list[Detection] = field(default_factory=list)

    @property
    def residual_px(self) -> np.ndarray:
        if self.tip is None:
            raise TipCalibrationError("no tip in this view")
        return self.centre - self.tip


class TipDetector:
    """Runs the crosshair and tip model and turns boxes into points.

    Detections are averaged over several frames rather than taken from one.
    Tip detection in the lower camera is the least reliable part of the
    procedure, and a single bad frame would go straight into the stored offset
    with nothing to flag it.
    """

    def __init__(self, model, *, imgsz: int = 2016, conf: float = 0.25,
                 point_label: str = "point", tip_label: str = "tip"):
        self.model = model
        self.imgsz = imgsz
        self.conf = conf
        self.point_label = point_label
        self.tip_label = tip_label

    def detect(self, frame) -> list[Detection]:
        results = self.model.predict(source=frame[..., ::-1], conf=self.conf,
                                     imgsz=self.imgsz, save=False, show=False,
                                     verbose=False)
        out = []
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
                out.append(Detection(self.model.names[int(box.cls[0])],
                                     np.array([(x1 + x2) / 2, (y1 + y2) / 2]),
                                     float(box.conf[0])))
        return out

    # -- assembling a view --------------------------------------------------

    def _pattern_from(self, dets: list[Detection], image_shape,
                      spacing_mm: float, centre_tol_px: float
                      ) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, float, float]:
        """Identify the central crosshair from the shape of the pattern.

        The centre is the crosshair whose four nearest neighbours sit at equal
        radii and in opposing directions. Picking it by proximity to the frame
        centre instead, as the previous version did, fails as soon as the disc
        is a few millimetres off centre, and silently returns an outer
        crosshair when it fails.
        """
        points = np.array([d.xy for d in dets if d.label == self.point_label])
        if len(points) < 5:
            raise TipCalibrationError(
                f"found {len(points)} crosshairs, need the centre plus four "
                f"neighbours; check lighting and focus"
            )

        best = None
        for i, candidate in enumerate(points):
            others = np.delete(points, i, axis=0)
            radii = np.linalg.norm(others - candidate, axis=1)
            near = np.argsort(radii)[:4]
            r = radii[near]
            if r.min() <= 0:
                continue
            cv = float(np.std(r) / np.mean(r))
            units = (others[near] - candidate) / r[:, None]
            # opposing neighbours cancel, so a true centre sums to nearly zero
            asymmetry = float(np.linalg.norm(units.sum(axis=0)) / 4)
            score = cv + asymmetry
            if best is None or score < best[0]:
                best = (score, i, others[near], r, cv, asymmetry)

        if best is None:
            raise TipCalibrationError("could not identify the central crosshair")
        _, idx, neighbours, radii, cv, asymmetry = best

        if asymmetry > 0.25:
            raise TipCalibrationError(
                f"no crosshair has four symmetric neighbours (best asymmetry "
                f"{asymmetry:.2f}); the pattern is partly out of frame or a "
                f"crosshair was missed"
            )

        centre = points[idx]
        img_centre = np.array([image_shape[1] / 2, image_shape[0] / 2])
        if np.linalg.norm(centre - img_centre) > centre_tol_px:
            raise TipCalibrationError(
                f"the disc centre is {np.linalg.norm(centre - img_centre):.0f} px "
                f"from the frame centre, beyond the {centre_tol_px:.0f} px "
                f"tolerance; re-centre the disc under the camera"
            )

        mm_per_px = float(spacing_mm / np.mean(radii))
        tips = [d for d in dets if d.label == self.tip_label]
        tip = (max(tips, key=lambda d: d.conf).xy if tips else None)
        return centre, neighbours, tip, mm_per_px, cv

    def view(self, camera: Camera, *, spacing_mm: float, frames: int = 5,
             centre_tol_px: float | None = None, need_tip: bool = False,
             max_radius_cv: float = 0.05, settle_s: float = 0.0) -> PatternView:
        """Average several frames into one reading.

        The spread between repeats is reported rather than hidden: it is the
        cheapest indication that the scene moved, that focus drifted, or that
        the model is guessing.
        """
        if settle_s:
            time.sleep(settle_s)
        w, h = camera.resolution
        # Generous by default: identification is by pattern shape, so this only
        # guards against the disc drifting so far that neighbours leave frame.
        centre_tol_px = centre_tol_px if centre_tol_px is not None else 0.35 * w

        centres, tips, ratios, cvs, last, last_dets = [], [], [], [], None, []
        problems = []
        for i in range(frames):
            frame = camera.read_after(time.monotonic())
            last = frame
            dets = self.detect(frame)
            try:
                c, nb, tip, mmpp, cv = self._pattern_from(
                    dets, frame.shape, spacing_mm, centre_tol_px)
            except TipCalibrationError as exc:
                problems.append(str(exc))
                continue
            centres.append(c)
            ratios.append(mmpp)
            cvs.append(cv)
            last_dets = dets
            last_neighbours = nb
            if tip is not None:
                tips.append(tip)

        if not centres:
            raise TipCalibrationError(
                f"no usable reading in {frames} frames. Last problem: "
                f"{problems[-1] if problems else 'unknown'}"
            )

        centre = np.median(centres, axis=0)
        spread = float(np.max(np.linalg.norm(np.array(centres) - centre, axis=1)))
        radius_cv = float(np.median(cvs))
        if radius_cv > max_radius_cv:
            raise TipCalibrationError(
                f"the four outer crosshairs sit at uneven radii "
                f"(scatter {radius_cv*100:.1f} %), so the scale cannot be "
                f"trusted; a crosshair was probably mis-detected"
            )

        tip = None
        if tips:
            tip = np.median(tips, axis=0)
            if len(tips) >= 3:
                tip_spread = np.max(np.linalg.norm(np.array(tips) - tip, axis=1))
                spread = max(spread, float(tip_spread))
        elif need_tip:
            raise TipCalibrationError(
                f"the tip was not detected in any of {frames} frames; it may be "
                f"outside the field of view, which means the stored offset is "
                f"far off"
            )

        return PatternView(centre, last_neighbours, tip,
                           float(np.median(ratios)), spread, radius_cv,
                           len(centres), last, last_dets)


# ---------------------------------------------------------------------------
# the routine
# ---------------------------------------------------------------------------

@dataclass
class OffsetResult:
    offset: PipetteOffset
    previous: tuple[float, float] | None
    correction_mm: np.ndarray
    residual_mm: float
    over_view: PatternView
    under_view: PatternView
    final_view: PatternView | None
    manual_nudge_mm: np.ndarray | None = None
    # By-product: the upper-to-lower homography from the same two views. None if
    # the fit was too poor to trust; a bad homography never fails the offset.
    homography: CameraHomography | None = None
    homography_report: HomographyReport | None = None

    @property
    def change_mm(self) -> float:
        if self.previous is None:
            return float("nan")
        return float(np.hypot(self.offset.dx - self.previous[0],
                              self.offset.dy - self.previous[1]))

    def __str__(self) -> str:
        lines = [
            f"offset          : dx {self.offset.dx:+8.3f}  dy {self.offset.dy:+8.3f} mm",
            f"correction      : {self.correction_mm[0]:+8.3f}, "
            f"{self.correction_mm[1]:+8.3f} mm",
            f"upper camera    : {self.over_view.n_frames} frames, "
            f"spread {self.over_view.spread_px:.1f} px",
            f"lower camera    : {self.under_view.n_frames} frames, "
            f"spread {self.under_view.spread_px:.1f} px, "
            f"{self.under_view.mm_per_px*1000:.2f} um/px",
        ]
        if self.previous is not None:
            lines.append(f"change from last: {self.change_mm:.3f} mm")
        if self.final_view is not None:
            lines.append(f"verified residual: {self.residual_mm*1000:.0f} um")
        if self.manual_nudge_mm is not None:
            lines.append(f"manual touch-up : {self.manual_nudge_mm[0]:+.3f}, "
                         f"{self.manual_nudge_mm[1]:+.3f} mm")
        return "\n".join(lines)


def calibrate_pipette_offset(
        robot: Robot, over_cam: Camera, under_cam: Camera,
        detector: TipDetector, pixel_map: PixelMap, *,
        target: TipTarget | None = None,
        current_offset: tuple[float, float],
        frames: int = 5, settle_s: float = 0.8,
        verify: bool = True, max_correction_mm: float = 40.0,
        tip_type: str | None = None, profile=None,
        under_resolution: tuple[int, int] | None = None,
        manual_touch_up=None, log=print) -> OffsetResult:
    """Drive the tip onto the crosshair and record the offset that did it.

    current_offset is the starting estimate. It must be close enough to put the
    tip inside the lower camera's field; on a new installation measure it with
    a ruler first.

    profile, if given, is written to and saved: the offset, and the homography
    that came with it. Both are products of this one measurement, so keeping
    them together is what stops a profile ending up with one refreshed and the
    other left from a previous tip. It also names the lower camera's calibrated
    resolution, which is checked before anything moves.

    under_resolution overrides that mode, for a caller who means to work at
    another one. Without either, the routine refuses rather than run unchecked.

    manual_touch_up, if given, is called as f(robot, under_cam, view) after the
    automatic correction and should return once the operator is satisfied. Any
    movement it makes is included, since the offset is read from the final pose.
    """
    # First, before the upper camera is even read and long before the gantry
    # moves. Everything the lower camera measures is in its pixels, so its mode
    # sets the scale of the result: the four outer crosshairs rescale mm/px, but
    # the detector's centre tolerance is a fraction of the frame width and YOLO
    # runs at a fixed imgsz either way, so a smaller mode is a quietly coarser
    # calibration rather than a failed one. That is the failure this refuses:
    # the same camera label is legitimately reopened at 2000x1500 for the
    # picking clip, and nothing downstream can tell which mode produced a stored
    # offset. Not a warning - a calibration taken at the wrong scale is wrong.
    expected = _expected_under_resolution(under_cam, under_resolution, profile)
    # The device's own read-back, not what was asked for: this camera is already
    # known to accept settings it then ignores, which is why every control is
    # verified rather than assumed (see ControlReport).
    actual = tuple(int(v) for v in under_cam.resolution)
    if actual != expected:
        raise TipCalibrationError(
            f"the tip calibration is defined at "
            f"{expected[0]}x{expected[1]}, but the lower camera "
            f"{getattr(under_cam, 'label', '?')!r} is running at "
            f"{actual[0]}x{actual[1]}. Reopen it at the calibrated mode "
            f"(cams.open(label) takes the profile's default) and run again; "
            f"nothing has moved and nothing was saved")

    target = target or TipTarget()
    approach = np.asarray(target.approach_offset, dtype=float)

    # --- upper camera: where is the crosshair, in robot coordinates ---------
    log("upper camera: locating the crosshair")
    over_view = detector.view(over_cam, spacing_mm=target.spacing_mm,
                              frames=frames)
    gx, gy, gz = xyz(robot)
    if not pixel_map.covers(*over_view.centre):
        raise TipCalibrationError(
            f"the crosshair is at {over_view.centre.round(0)}, outside the "
            f"calibrated area of the pixel map; centre the disc better"
        )
    target_xy = np.asarray(pixel_map.to_robot(over_view.centre[0],
                                              over_view.centre[1], (gx, gy)))
    log(f"  crosshair at {target_xy.round(3)} mm, "
        f"spread {over_view.spread_px:.1f} px over {over_view.n_frames} frames")

    # --- drive the tip there, offset to one side so it does not hide it -----
    aim = target_xy + np.asarray(current_offset, dtype=float) + approach
    move_to(robot, (aim[0], aim[1], target.module_height),
            min_z_height=target.module_height - 0.1)
    time.sleep(settle_s)

    # --- lower camera: how far is the tip from the crosshair ---------------
    log("lower camera: measuring the tip")
    under_view = detector.view(under_cam, spacing_mm=target.spacing_mm,
                               frames=frames, need_tip=True)
    axes = np.asarray(target.axes, dtype=float)
    correction = axes @ (under_view.residual_px * under_view.mm_per_px)
    log(f"  residual {under_view.residual_px.round(1)} px "
        f"= {correction.round(3)} mm, spread {under_view.spread_px:.1f} px")

    if np.max(np.abs(correction)) > max_correction_mm:
        raise TipCalibrationError(
            f"the correction {correction.round(2)} mm exceeds "
            f"{max_correction_mm} mm. Either the stored offset is far off or a "
            f"detection is wrong; nothing has been saved"
        )

    move_relative(robot, "x", float(correction[0]))
    move_relative(robot, "y", float(correction[1]))
    time.sleep(settle_s)

    # --- verify, then optionally let the operator finish by hand -----------
    final_view, residual_mm = None, float("nan")
    if verify:
        try:
            final_view = detector.view(under_cam, spacing_mm=target.spacing_mm,
                                       frames=frames, need_tip=True)
            residual_mm = float(np.linalg.norm(
                axes @ (final_view.residual_px * final_view.mm_per_px)))
            log(f"  after correction the tip is {residual_mm*1000:.0f} um "
                f"from the crosshair")
        except TipCalibrationError as exc:
            log(f"  verification unavailable: {exc}")

    nudge = None
    if manual_touch_up is not None:
        before = np.array(xyz(robot)[:2])
        manual_touch_up(robot, under_cam, final_view or under_view)
        nudge = np.array(xyz(robot)[:2]) - before
        if np.any(nudge):
            log(f"  manual touch-up moved {nudge.round(3)} mm")
            if verify:
                try:
                    final_view = detector.view(under_cam,
                                               spacing_mm=target.spacing_mm,
                                               frames=frames, need_tip=True)
                    residual_mm = float(np.linalg.norm(
                        axes @ (final_view.residual_px * final_view.mm_per_px)))
                except TipCalibrationError:
                    pass

    # --- the offset is simply where the gantry ended up --------------------
    # Reading it from the final pose rather than accumulating the commanded
    # moves means the approach shift, the correction and any manual nudge all
    # cancel without being tracked individually.
    fx, fy, _ = xyz(robot)
    offset = PipetteOffset(
        dx=float(fx - target_xy[0]), dy=float(fy - target_xy[1]),
        tip_type=tip_type,
        measured_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc),
        method="auto+manual" if nudge is not None and np.any(nudge) else "auto",
        residual_mm=None if np.isnan(residual_mm) else residual_mm,
        n_samples=under_view.n_frames,
        spread_mm=float(under_view.spread_px * under_view.mm_per_px),
        # the mode the measurement was taken at, checked above and recorded here
        # so a stored offset can be told apart from one taken at another scale
        under_resolution=list(actual),
    )

    # --- free by-product: the upper-to-lower homography --------------------
    # Both views saw the same disc, so the camera-to-camera map costs nothing
    # more. gx, gy is the gantry pose the upper view was taken at, and the
    # resolutions are the modes the two views were taken at; the clip records at
    # another one and the matrix is rescaled to it. A poor fit is logged and
    # dropped, never fatal to the offset.
    homography, homography_report = None, None
    try:
        homography, homography_report = homography_from_views(
            over_view, under_view, target.under_rotation_deg, (gx, gy),
            over_resolution=over_cam.resolution,
            under_resolution=under_cam.resolution)
        log(f"  {homography_report}")
    except HomographyError as exc:
        log(f"  homography unavailable: {exc}")

    result = OffsetResult(offset, tuple(current_offset), correction,
                          residual_mm, over_view, under_view, final_view, nudge,
                          homography=homography,
                          homography_report=homography_report)

    # Saving here rather than in a cell of its own: the homography is a
    # by-product of this measurement and used to be written by a separate step
    # that was easy to forget, leaving the profile with a matrix from an older
    # tip and an older pose.
    if profile is not None:
        profile.calibration.pipette_offset = offset
        wrote = "offset"
        if homography is not None:
            profile.calibration.homography = homography
            wrote += " and homography"
        profile.save_calibration(backup=False)
        log(f"  saved {wrote} to profile {profile.name!r}")
    return result
