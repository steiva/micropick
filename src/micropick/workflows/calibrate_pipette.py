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
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from ..config.schema import CameraHomography, PipetteOffset, TipTarget
from ..core.calibration.homography import HomographyError, HomographyReport
from ..core.calibration.pixel_map import PixelMap
from ..hardware.protocols import (Camera, Robot, lights_on, move_relative,
                                  move_to, set_lights, xyz)
from ..viz.tip_views import save_view
from .calibrate_homography import homography_from_views

__all__ = ["Detection", "PatternView", "ViewRecord", "TipDetector",
           "OffsetResult", "calibrate_pipette_offset", "TipCalibrationError",
           "HandStart", "start_by_hand", "save_by_hand"]


class TipCalibrationError(RuntimeError):
    pass


class _TooFewCrosshairs(TipCalibrationError):
    """One frame without the pattern: the disc is not on the module, or
    cannot be seen."""


# What to do, said instead of the count when no frame showed the pattern.
NO_DISC = ("Place the crosshair calibration disc onto the calibration module "
           "and retry; check lighting and focus.")

# How far the frames of one reading may disagree, and how far from the
# crosshair the check may find the tip, before the calibration is refused.
# A good reading spreads by a few pixels (0.1 mm); the one that saved a
# 43 mm offset spread by 41 mm.
MAX_SPREAD_MM = 0.5
MAX_RESIDUAL_MM = 0.5


def _save_reading(folder: Path | None, name: str, detector, view, problem,
                  log) -> None:
    """A reading's pictures into `folder` (`viz.tip_views`), its outcome in
    the header. Never fails the calibration: a picture not saved is said."""
    record = getattr(detector, "last_record", None)
    if folder is None or record is None or record.frame is None:
        return
    lines = [f"{name}: {len(record.detections)} frames"]
    if view is not None:
        lines[0] += (f", {view.n_frames} usable, spread "
                     f"{view.spread_px:.1f} px "
                     f"= {view.spread_px * view.mm_per_px:.2f} mm")
        if view.tip is not None:
            lines.append(f"tip measured at {view.tip.round(1)} px (yellow "
                         f"cross), crosshair at {view.center.round(1)} px")
    if problem:
        lines.append(problem[:160])
    try:
        save_view(folder, name, record.frame, record.detections,
                  tip_label=getattr(detector, "tip_label", "tip"),
                  center=view.center if view is not None else None,
                  tip=view.tip if view is not None else None, lines=lines)
        log(f"  pictures saved: {folder / name}_detections.jpg")
    except Exception as exc:              # noqa: BLE001 - only pictures
        log(f"  pictures not saved: {exc}")


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
    box: tuple[float, float, float, float] | None = None   # x0, y0, x1, y1


@dataclass
class ViewRecord:
    """What one reading looked at: its last frame and every frame's
    detections, a frame without a usable reading included. Kept by
    `TipDetector.view` as it goes, so a reading that fails still leaves
    what it saw to be saved (`viz.tip_views`)."""

    frame: np.ndarray | None = None
    detections: list[list[Detection]] = field(default_factory=list)


@dataclass
class PatternView:
    """One reading of the crosshair disc, and possibly the tip."""

    center: np.ndarray                      # central crosshair, pixels
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
        return self.center - self.tip


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
        # The frames and boxes of the reading last taken, filled as it goes.
        self.last_record: ViewRecord | None = None

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
                                     float(box.conf[0]), (x1, y1, x2, y2)))
        return out

    # -- assembling a view --------------------------------------------------

    def _pattern_from(self, dets: list[Detection], image_shape,
                      spacing_mm: float, center_tol_px: float
                      ) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, float, float]:
        """Identify the central crosshair from the shape of the pattern.

        The center is the crosshair whose four nearest neighbours sit at equal
        radii and in opposing directions. Picking it by proximity to the frame
        center instead, as the previous version did, fails as soon as the disc
        is a few millimetres off center, and silently returns an outer
        crosshair when it fails.
        """
        points = np.array([d.xy for d in dets if d.label == self.point_label])
        if len(points) < 5:
            raise _TooFewCrosshairs(
                f"found {len(points)} crosshairs, need the center plus four "
                f"neighbors; check lighting and focus"
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
            # opposing neighbours cancel, so a true center sums to nearly zero
            asymmetry = float(np.linalg.norm(units.sum(axis=0)) / 4)
            score = cv + asymmetry
            if best is None or score < best[0]:
                best = (score, i, others[near], r, cv, asymmetry)

        if best is None:
            raise TipCalibrationError("could not identify the central crosshair")
        _, idx, neighbours, radii, cv, asymmetry = best

        if asymmetry > 0.25:
            raise TipCalibrationError(
                f"no crosshair has four symmetric neighbors (best asymmetry "
                f"{asymmetry:.2f}); the pattern is partly out of frame or a "
                f"crosshair was missed"
            )

        center = points[idx]
        img_center = np.array([image_shape[1] / 2, image_shape[0] / 2])
        if np.linalg.norm(center - img_center) > center_tol_px:
            raise TipCalibrationError(
                f"the disc center is {np.linalg.norm(center - img_center):.0f} px "
                f"from the frame center, beyond the {center_tol_px:.0f} px "
                f"tolerance; re-center the disc under the camera"
            )

        mm_per_px = float(spacing_mm / np.mean(radii))
        tips = [d for d in dets if d.label == self.tip_label]
        tip = (max(tips, key=lambda d: d.conf).xy if tips else None)
        return center, neighbours, tip, mm_per_px, cv

    def view(self, camera: Camera, *, spacing_mm: float, frames: int = 5,
             center_tol_px: float | None = None, need_tip: bool = False,
             max_radius_cv: float = 0.05, settle_s: float = 0.0,
             on_frame=None) -> PatternView:
        """Average several frames into one reading.

        The spread between repeats is reported rather than hidden: it is the
        cheapest indication that the scene moved, that focus drifted, or that
        the model is guessing.

        on_frame, if given, is called as f(index, frame, detections) for each
        frame as soon as it is detected, from 1: what the model sees, shown.
        """
        if settle_s:
            time.sleep(settle_s)
        w, h = camera.resolution
        # Generous by default: identification is by pattern shape, so this only
        # guards against the disc drifting so far that neighbours leave frame.
        center_tol_px = center_tol_px if center_tol_px is not None else 0.35 * w

        centers, tips, ratios, cvs, last, last_dets = [], [], [], [], None, []
        problems, too_few = [], 0
        record = self.last_record = ViewRecord()
        for i in range(frames):
            frame = camera.read_after(time.monotonic())
            last = record.frame = frame
            dets = self.detect(frame)
            record.detections.append(dets)
            if on_frame is not None:
                on_frame(i + 1, frame, dets)
            try:
                c, nb, tip, mmpp, cv = self._pattern_from(
                    dets, frame.shape, spacing_mm, center_tol_px)
            except TipCalibrationError as exc:
                problems.append(str(exc))
                too_few += isinstance(exc, _TooFewCrosshairs)
                continue
            centers.append(c)
            ratios.append(mmpp)
            cvs.append(cv)
            last_dets = dets
            last_neighbours = nb
            if tip is not None:
                tips.append(tip)

        if not centers and too_few == frames:
            raise TipCalibrationError(NO_DISC)
        if not centers:
            raise TipCalibrationError(
                f"no usable reading in {frames} frames. Last problem: "
                f"{problems[-1] if problems else 'unknown'}"
            )

        center = np.median(centers, axis=0)
        spread = float(np.max(np.linalg.norm(np.array(centers) - center, axis=1)))
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
            if len(tips) >= 2:
                tip_spread = np.max(np.linalg.norm(np.array(tips) - tip, axis=1))
                spread = max(spread, float(tip_spread))
        elif need_tip:
            raise TipCalibrationError(
                f"the tip was not detected in any of {frames} frames; it may be "
                f"outside the field of view, which means the stored offset is "
                f"far off"
            )

        return PatternView(center, last_neighbours, tip,
                           float(np.median(ratios)), spread, radius_cv,
                           len(centers), last, last_dets)


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


def _check_lower_mode(under_cam: Camera, under_resolution, profile
                      ) -> tuple[int, int]:
    """The lower camera's mode, refused unless it is the calibration's.

    First, before the upper camera is even read and long before the gantry
    moves. Everything the lower camera measures is in its pixels, so its mode
    sets the scale of the result: the four outer crosshairs rescale mm/px, but
    the detector's center tolerance is a fraction of the frame width and YOLO
    runs at a fixed imgsz either way, so a smaller mode is a quietly coarser
    calibration rather than a failed one. That is the failure this refuses:
    the same camera label is legitimately reopened at 2000x1500 for the
    picking clip, and nothing downstream can tell which mode produced a stored
    offset. Not a warning - a calibration taken at the wrong scale is wrong.
    """
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
    return actual


def _reader(detector: TipDetector, target: TipTarget, frames: int,
            images_to: Path | None, on_frame, log):
    """`look(name, camera, need_tip=)`: one reading, its pictures saved
    whether or not it succeeds, each frame handed on as it is detected
    (`on_frame(name, index, frame, detections)`)."""
    def shown(name):
        if on_frame is None:
            return None

        def each(index, frame, dets):
            try:
                on_frame(name, index, frame, dets)
            except Exception as exc:          # noqa: BLE001 - only showing
                log(f"  frame not shown: {exc}")
        return each

    def look(name: str, camera: Camera, *, need_tip: bool) -> PatternView:
        view, problem = None, ""
        try:
            view = detector.view(camera, spacing_mm=target.spacing_mm,
                                 frames=frames, need_tip=need_tip,
                                 on_frame=shown(name))
            return view
        except TipCalibrationError as exc:
            problem = str(exc)
            raise
        finally:
            _save_reading(images_to, name, detector, view, problem, log)
    return look


def _steady(view: PatternView, what: str, camera: str, max_spread_mm: float,
            images_to: Path | None) -> None:
    """Refuses a reading whose frames disagree (`max_spread_mm`)."""
    spread_mm = view.spread_px * view.mm_per_px
    if spread_mm > max_spread_mm:
        where = (f"; the pictures are in {images_to}"
                 if images_to is not None else "")
        raise TipCalibrationError(
            f"the {what} was found in different places in the "
            f"{view.n_frames} frames ({spread_mm:.1f} mm apart, more than "
            f"{max_spread_mm} mm). Check the {camera} camera's focus and "
            f"light{where}")


def _lights(robot: Robot, settle_s: float, log) -> None:
    """Lights on, and known to be on.

    The upper camera finds the crosshair by the rail lights, and a picking
    run leaves them off. Checked and switched rather than assumed, and left
    on afterwards: the calibration does not own the lights, it needs them.
    """
    before = lights_on(robot)
    if before is None:
        log("warning: the rail lights cannot be read; make sure they are on")
    elif not before:
        log("rail lights were off: switching them on for the calibration")
        set_lights(robot, True)
        if lights_on(robot) is not True:
            raise TipCalibrationError(
                "the rail lights are off and could not be switched on; the "
                "upper camera cannot find the crosshair without them")
        time.sleep(settle_s)             # let the exposure settle


def _locate_crosshair(look, robot: Robot, over_cam: Camera,
                      pixel_map: PixelMap, max_spread_mm: float,
                      images_to: Path | None, log):
    """The upper camera: where the crosshair is, in robot coordinates.
    Returns the reading, the crosshair's XY and the gantry's XY it was
    taken at."""
    log("upper camera: locating the crosshair")
    over_view = look("1_upper", over_cam, need_tip=False)
    _steady(over_view, "crosshair", "upper", max_spread_mm, images_to)
    gx, gy, _gz = xyz(robot)
    if not pixel_map.covers(*over_view.center):
        raise TipCalibrationError(
            f"the crosshair is at {over_view.center.round(0)}, outside the "
            f"calibrated area of the pixel map; center the disc better"
        )
    target_xy = np.asarray(pixel_map.to_robot(over_view.center[0],
                                              over_view.center[1], (gx, gy)))
    log(f"  crosshair at {target_xy.round(3)} mm, "
        f"spread {over_view.spread_px:.1f} px over {over_view.n_frames} frames")
    return over_view, target_xy, (float(gx), float(gy))


def _homography(over_view, under_view, target: TipTarget, gantry, over_cam,
                under_resolution, log):
    """The upper-to-lower homography from the two views of the same disc,
    (None, None) when the fit is too poor: never fatal to the offset."""
    try:
        homography, report = homography_from_views(
            over_view, under_view, target.under_rotation_deg, gantry,
            over_resolution=over_cam.resolution,
            under_resolution=under_resolution)
        log(f"  {report}")
        return homography, report
    except HomographyError as exc:
        log(f"  homography unavailable: {exc}")
        return None, None


def calibrate_pipette_offset(
        robot: Robot, over_cam: Camera, under_cam: Camera,
        detector: TipDetector, pixel_map: PixelMap, *,
        target: TipTarget | None = None,
        current_offset: tuple[float, float],
        height: float | None = None,
        frames: int = 5, settle_s: float = 0.8,
        verify: bool = True, max_correction_mm: float = 40.0,
        max_spread_mm: float = MAX_SPREAD_MM,
        max_residual_mm: float = MAX_RESIDUAL_MM,
        tip_type: str | None = None, profile=None,
        under_resolution: tuple[int, int] | None = None,
        manual_touch_up=None, robot_session: str | None = None,
        tip_pickup: str | None = None, images_to: Path | None = None,
        on_frame=None, on_move=None, log=print) -> OffsetResult:
    """Drive the tip onto the crosshair and record the offset that did it.

    current_offset is the starting estimate. It must be close enough to put the
    tip inside the lower camera's field; on a new installation measure it with
    a ruler first.

    height is the Z the tip is measured at, target.module_height if not given.

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

    robot_session and tip_pickup are recorded with the offset: which robot
    session, and which pick-up in it, put on the tip that was measured.

    max_spread_mm refuses a reading whose frames disagree by more than that
    (the crosshair, or the tip the model kept in each frame), before the
    gantry moves by it: a model unsure what the tip is gives a median of
    unrelated boxes. max_residual_mm refuses a verification that finds the
    tip that far from the crosshair after the correction. Either way nothing
    is saved. A verification that cannot see the pattern (the tip hides the
    central crosshair) is logged and not a refusal.

    images_to, if given, is the folder each reading's last frame is saved
    into, as it came and with every frame's boxes drawn (`viz.tip_views`):
    1_upper, 2_lower, 3_check - a reading that failed included.

    on_frame, if given, is called as f(name, index, frame, detections) for
    each frame of those readings as it is detected, and on_move as f()
    before each move of the gantry: what the cameras see, to be shown, and
    when it stops being where the gantry is.
    """
    actual = _check_lower_mode(under_cam, under_resolution, profile)
    target = target or TipTarget()
    approach = np.asarray(target.approach_offset, dtype=float)
    height = target.module_height if height is None else float(height)
    look = _reader(detector, target, frames, images_to, on_frame, log)
    moving = on_move or (lambda: None)

    def steady(view: PatternView, what: str, camera: str) -> None:
        _steady(view, what, camera, max_spread_mm, images_to)

    _lights(robot, settle_s, log)
    over_view, target_xy, (gx, gy) = _locate_crosshair(
        look, robot, over_cam, pixel_map, max_spread_mm, images_to, log)

    # --- drive the tip there, offset to one side so it does not hide it -----
    aim = target_xy + np.asarray(current_offset, dtype=float) + approach
    moving()
    move_to(robot, (aim[0], aim[1], height), min_z_height=height - 0.1)
    time.sleep(settle_s)

    # --- lower camera: how far is the tip from the crosshair ---------------
    log("lower camera: measuring the tip")
    under_view = look("2_lower", under_cam, need_tip=True)
    axes = np.asarray(target.axes, dtype=float)
    correction = axes @ (under_view.residual_px * under_view.mm_per_px)
    log(f"  residual {under_view.residual_px.round(1)} px "
        f"= {correction.round(3)} mm, spread {under_view.spread_px:.1f} px")
    # Before the gantry moves by it: a median of boxes that disagree is
    # no measurement (the 43 mm calibration of 2026-11-09).
    steady(under_view, "tip", "lower")

    if np.max(np.abs(correction)) > max_correction_mm:
        raise TipCalibrationError(
            f"the correction {correction.round(2)} mm exceeds "
            f"{max_correction_mm} mm. Either the stored offset is far off or a "
            f"detection is wrong; nothing has been saved"
        )

    moving()
    move_relative(robot, "x", float(correction[0]))
    move_relative(robot, "y", float(correction[1]))
    time.sleep(settle_s)

    # --- verify, then optionally let the operator finish by hand -----------
    final_view, residual_mm = None, float("nan")
    if verify:
        try:
            final_view = look("3_check", under_cam, need_tip=True)
            residual_mm = float(np.linalg.norm(
                axes @ (final_view.residual_px * final_view.mm_per_px)))
            log(f"  after correction the tip is {residual_mm*1000:.0f} um "
                f"from the crosshair")
        except TipCalibrationError as exc:
            log(f"  verification unavailable: {exc}")
        # Refused only on a reading that was taken: the operator's touch-up,
        # when there is one, is what corrects it instead.
        if final_view is not None and manual_touch_up is None:
            steady(final_view, "tip", "lower")
            if residual_mm > max_residual_mm:
                where = (f"; the pictures are in {images_to}"
                         if images_to is not None else "")
                raise TipCalibrationError(
                    f"after the correction the tip is {residual_mm:.2f} mm "
                    f"from the crosshair, more than {max_residual_mm} mm: a "
                    f"detection is wrong{where}")

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
        robot_session=robot_session, tip_pickup=tip_pickup,
    )

    # --- free by-product: the upper-to-lower homography --------------------
    # Both views saw the same disc, so the camera-to-camera map costs nothing
    # more. gx, gy is the gantry pose the upper view was taken at, and the
    # resolutions are the modes the two views were taken at; the clip records at
    # another one and the matrix is rescaled to it. A poor fit is logged and
    # dropped, never fatal to the offset.
    homography, homography_report = _homography(
        over_view, under_view, target, (gx, gy), over_cam,
        under_cam.resolution, log)

    result = OffsetResult(offset, tuple(current_offset), correction,
                          residual_mm, over_view, under_view, final_view, nudge,
                          homography=homography,
                          homography_report=homography_report)

    # Saving here rather than in a cell of its own: the homography is a
    # by-product of this measurement and used to be written by a separate step
    # that was easy to forget, leaving the profile with a matrix from an older
    # tip and an older pose.
    _save(profile, offset, homography, log)
    return result


def _save(profile, offset: PipetteOffset, homography, log) -> None:
    """The offset, and the homography measured with it, into the profile."""
    if profile is None:
        return
    profile.calibration.pipette_offset = offset
    wrote = "offset"
    if homography is not None:
        profile.calibration.homography = homography
        wrote += " and homography"
    profile.save_calibration(backup=False)
    log(f"  saved {wrote} to profile {profile.name!r}")


# ---------------------------------------------------------------------------
# by hand
# ---------------------------------------------------------------------------

@dataclass
class HandStart:
    """What `start_by_hand` left for `save_by_hand`: the crosshair in robot
    XY, and the readings the homography is made from (`under_view` None when
    the lower camera did not find the disc)."""

    target_xy: np.ndarray
    over_view: PatternView
    under_view: PatternView | None
    gantry: tuple[float, float]
    over_resolution: tuple[int, int]
    under_resolution: tuple[int, int]
    target: TipTarget


def start_by_hand(
        robot: Robot, over_cam: Camera, under_cam: Camera,
        detector: TipDetector, pixel_map: PixelMap, *,
        target: TipTarget | None = None,
        current_offset: tuple[float, float],
        height: float | None = None,
        frames: int = 5, settle_s: float = 0.8,
        max_spread_mm: float = MAX_SPREAD_MM, profile=None,
        under_resolution: tuple[int, int] | None = None,
        images_to: Path | None = None, on_frame=None, on_move=None,
        log=print) -> HandStart:
    """The tip calibration for a tip the model does not know: the operator
    puts the tip on the crosshair, and `save_by_hand` reads the offset off
    the pose they left it at.

    The upper camera finds the crosshair, as `calibrate_pipette_offset`. The
    tip is then put aside (`approach_offset`) and the lower camera reads the
    crosshair alone, for the homography and to show where it is; a disc it
    does not find costs only the homography. Last the tip goes to the
    crosshair plus `current_offset`, at `height`, and is left there. Nothing
    is saved. The arguments are `calibrate_pipette_offset`'s.
    """
    actual = _check_lower_mode(under_cam, under_resolution, profile)
    target = target or TipTarget()
    approach = np.asarray(target.approach_offset, dtype=float)
    height = target.module_height if height is None else float(height)
    look = _reader(detector, target, frames, images_to, on_frame, log)
    moving = on_move or (lambda: None)

    _lights(robot, settle_s, log)
    over_view, target_xy, gantry = _locate_crosshair(
        look, robot, over_cam, pixel_map, max_spread_mm, images_to, log)

    near = target_xy + np.asarray(current_offset, dtype=float)
    aside = near + approach
    moving()
    move_to(robot, (aside[0], aside[1], height), min_z_height=height - 0.1)
    time.sleep(settle_s)
    log("lower camera: locating the crosshair")
    under_view = None
    try:
        under_view = look("2_lower", under_cam, need_tip=False)
    except TipCalibrationError as exc:
        log(f"  the lower camera did not find the crosshair, so no "
            f"homography: {exc}")

    moving()
    move_to(robot, (near[0], near[1], height), min_z_height=height - 0.1)
    log("  the tip is at the start offset: center it by hand")
    return HandStart(target_xy, over_view, under_view, gantry,
                     tuple(int(v) for v in over_cam.resolution), actual,
                     target)


def save_by_hand(start: HandStart, pose_xy, *, profile=None,
                 tip_type: str | None = None,
                 robot_session: str | None = None,
                 tip_pickup: str | None = None, log=print) -> PipetteOffset:
    """The offset is where the operator left the tip, less the crosshair;
    saved into `profile` with the homography its readings give."""
    from datetime import datetime, timezone
    x, y = (float(v) for v in pose_xy[:2])
    offset = PipetteOffset(
        dx=x - float(start.target_xy[0]), dy=y - float(start.target_xy[1]),
        tip_type=tip_type, measured_at=datetime.now(timezone.utc),
        method="manual", residual_mm=None,
        n_samples=start.over_view.n_frames,
        spread_mm=float(start.over_view.spread_px * start.over_view.mm_per_px),
        under_resolution=list(start.under_resolution),
        robot_session=robot_session, tip_pickup=tip_pickup)
    homography = None
    if start.under_view is not None:
        homography, _report = _homography(
            start.over_view, start.under_view, start.target, start.gantry,
            SimpleNamespace(resolution=start.over_resolution),
            start.under_resolution, log)
    _save(profile, offset, homography, log)
    return offset
