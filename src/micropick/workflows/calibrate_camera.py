"""Camera calibration as a workflow.

Drives the robot over a static marker, collects pixel and pose pairs, and fits
the pixel map. Nothing here talks to a concrete device: it takes anything
satisfying the Robot and Camera protocols, which is what lets the whole
procedure run against mocks.

The sweep extent is measured rather than configured. Small probe moves give the
scale in millimetres per pixel, and the marker's own detected corners give its
size in pixels. From those two the largest safe excursion follows directly, so
changing the marker, the lens or the resolution needs no edits.

Progress and frames are delivered through callbacks. The function is plain
blocking code, so a notebook calls it directly and a GUI runs it in a thread;
making it async would serve the GUI and hurt the notebook.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from ..core.calibration.pixel_map import FitReport, PixelMap, fit_pixel_map
from ..hardware.protocols import Camera, Robot, goto_xy, xyz

__all__ = ["MarkerTracker", "ScaleProbe", "SweepPlan", "SweepData",
           "measure_scale", "plan_sweep", "run_sweep", "calibrate_camera",
           "CalibrationError", "Cancelled"]


class CalibrationError(RuntimeError):
    pass


class Cancelled(RuntimeError):
    """The caller asked to stop."""


# ---------------------------------------------------------------------------
# marker tracking
# ---------------------------------------------------------------------------

class MarkerTracker:
    """Follows one marker across the sweep.

    The id is locked on the first successful detection and never re-chosen. A
    ChArUco board or a stray tag can put several markers in frame, and taking
    whichever the detector listed first would mix different physical points
    into one track, which corrupts the fit without any visible symptom.

    Corner order within a marker is stable across frames, so the four corners
    can be fitted as four independent tracks of the same rigid object.
    """

    def __init__(self, detector, target_id: int | None = None,
                 frames: int = 5, max_jitter_px: float = 3.0,
                 min_hits: int = 3):
        self.detector = detector
        self.track_id = target_id
        self.frames = frames
        self.max_jitter_px = max_jitter_px
        self.min_hits = min_hits
        self.last_frame: np.ndarray | None = None
        self.last_jitter = 0.0
        self.last_wait = 0.0

    def _corners_in(self, frame) -> np.ndarray | None:
        corners, ids, _ = self.detector.detectMarkers(frame)
        if ids is None or len(corners) == 0:
            return None
        ids = ids.ravel()
        if self.track_id is None:
            centre = np.array([frame.shape[1] / 2, frame.shape[0] / 2])
            d = [np.linalg.norm(c.reshape(4, 2).mean(0) - centre) for c in corners]
            self.track_id = int(ids[int(np.argmin(d))])
        hit = np.where(ids == self.track_id)[0]
        if len(hit) != 1:
            return None
        return corners[hit[0]].reshape(4, 2).astype(float)

    def measure(self, camera: Camera, after: float | None = None, *,
                timeout: float = 4.0) -> np.ndarray | None:
        """Wait until the marker stops moving, then return its corners.

        Frames are taken until several consecutive readings agree to within
        max_jitter_px, rather than grabbing a fixed number after a guessed
        pause. A gantry settles in a time that depends on the move, the load
        and the day; measuring that directly is both faster on short moves and
        reliable on long ones, and it removes the tuning parameter that decided
        whether the whole sweep succeeded or failed.

        Returns None if the marker never appears, or never holds still.
        """
        deadline = time.monotonic() + timeout
        window: list[np.ndarray] = []
        seen = False
        t = after if after is not None else time.monotonic()

        while time.monotonic() < deadline:
            try:
                frame = camera.read_after(t, timeout=max(0.2, deadline - time.monotonic()))
            except TimeoutError:
                break
            t = time.monotonic()
            self.last_frame = frame

            corners = self._corners_in(frame)
            if corners is None:
                window.clear()          # a gap means the run of stillness broke
                continue
            seen = True
            window.append(corners)
            if len(window) < self.frames:
                continue

            stack = np.array(window[-self.frames:])
            jitter = float(np.linalg.norm(stack.std(axis=0), axis=1).max())
            self.last_jitter = jitter
            if jitter <= self.max_jitter_px:
                self.last_wait = timeout - (deadline - time.monotonic())
                return stack.mean(axis=0)
            window.pop(0)               # slide, keep waiting for a quiet run

        self.last_jitter = self.last_jitter if seen else float("inf")
        self.last_wait = timeout
        return None


# ---------------------------------------------------------------------------
# scale probe
# ---------------------------------------------------------------------------

@dataclass
class ScaleProbe:
    mm_per_px: float
    marker_px: float           # largest side of the marker, in pixels
    origin: tuple[float, float, float]
    steps: int

    @property
    def um_per_px(self) -> float:
        return self.mm_per_px * 1000

    def fov_mm(self, image_size) -> tuple[float, float]:
        return (image_size[0] * self.mm_per_px, image_size[1] * self.mm_per_px)


def measure_scale(robot: Robot, camera: Camera, tracker: MarkerTracker, *,
                  first_step_mm: float = 1.0, max_step_mm: float = 40.0,
                  target_fraction: tuple[float, float] = (0.10, 0.30),
                  max_attempts: int = 7, settle_s: float = 0.2,
                  log=print) -> ScaleProbe:
    """Find millimetres per pixel by moving a little and watching the marker.

    Starts small and grows the step until the marker shifts a useful fraction
    of the frame width. Beginning large risks pushing the marker out of frame
    on an unknown lens; beginning small and growing works for a wide angle and
    for heavy magnification alike.
    """
    origin = xyz(robot)
    base = tracker.measure(camera)
    if base is None:
        raise CalibrationError(
            "marker not detected at the starting pose; centre it in the frame first"
        )

    width = camera.resolution[0]
    step = first_step_mm
    lo, hi = target_fraction

    for attempt in range(1, max_attempts + 1):
        goto_xy(robot, origin[0] + step, origin[1])
        time.sleep(settle_s)
        t = time.monotonic()
        moved_to = xyz(robot)
        probe = tracker.measure(camera, after=t)

        if probe is None:                       # marker left the frame
            step *= 0.5
            log(f"  probe {attempt}: lost the marker, halving to {step:.2f} mm")
            continue

        shift = float(np.linalg.norm(probe.mean(0) - base.mean(0)))
        moved = float(np.hypot(moved_to[0] - origin[0], moved_to[1] - origin[1]))
        if shift < 1.0:                         # below detection noise
            step = min(step * 4, max_step_mm)
            log(f"  probe {attempt}: {shift:.1f} px is too small, trying {step:.2f} mm")
            continue

        mm_per_px = moved / shift
        if lo * width <= shift <= hi * width:
            goto_xy(robot, origin[0], origin[1])
            marker_px = float(max(
                probe[:, 0].max() - probe[:, 0].min(),
                probe[:, 1].max() - probe[:, 1].min()))
            log(f"  scale: {moved:.3f} mm moved the marker {shift:.1f} px "
                f"= {mm_per_px*1000:.2f} um/px")
            return ScaleProbe(mm_per_px, marker_px, origin, attempt)

        target = 0.5 * (lo + hi) * width
        step = float(np.clip(step * target / shift, 0.05, max_step_mm))
        log(f"  probe {attempt}: {shift:.0f} px, adjusting step to {step:.2f} mm")

    goto_xy(robot, origin[0], origin[1])
    raise CalibrationError(
        "the scale probe did not converge; check that the marker stays visible"
    )


# ---------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------

@dataclass
class SweepPlan:
    half_x: float
    half_y: float
    grid_n: int
    origin: tuple[float, float, float]
    poses: list[tuple[float, float]] = field(default_factory=list)
    expected_coverage: tuple[float, float] = (0.0, 0.0)
    probe: ScaleProbe | None = None

    def __str__(self) -> str:
        return (f"{self.grid_n}x{self.grid_n} poses, "
                f"+/-{self.half_x:.1f} x +/-{self.half_y:.1f} mm, "
                f"expected coverage {self.expected_coverage[0]*100:.0f} % x "
                f"{self.expected_coverage[1]*100:.0f} % of the frame")


def plan_sweep(probe: ScaleProbe, image_size, *, grid_n: int = 7,
               margin_px: float = 20.0, max_offset_mm: float = 60.0,
               centre_px: tuple[float, float] | None = None) -> SweepPlan:
    """Largest grid that keeps the whole marker inside the frame.

    The limit is the marker's own size: its centre cannot approach an edge
    closer than half its extent plus a margin, or the detector loses the quad.
    Both are known by this point, so the extent is derived rather than guessed.
    """
    w, h = image_size
    cu, cv = centre_px if centre_px else (w / 2, h / 2)
    px_per_mm = 1.0 / probe.mm_per_px
    need = probe.marker_px / 2 + margin_px

    half_x = min(cu - need, (w - need) - cu) / px_per_mm
    half_y = min(cv - need, (h - need) - cv) / px_per_mm
    half_x = float(np.clip(np.floor(half_x), 0, max_offset_mm))
    half_y = float(np.clip(np.floor(half_y), 0, max_offset_mm))

    if half_x < 1 or half_y < 1:
        raise CalibrationError(
            f"the marker fills too much of the frame to sweep it "
            f"({probe.marker_px:.0f} px of {w}x{h}); use a smaller marker"
        )

    ox, oy, _ = probe.origin
    gx = np.linspace(-half_x, half_x, grid_n)
    gy = np.linspace(-half_y, half_y, grid_n)
    poses = [(ox + dx, oy + dy) for dy in gy for dx in gx]

    # tracked corners reach half a marker further out than its centre does
    span_u = (2 * half_x * px_per_mm + probe.marker_px) / w
    span_v = (2 * half_y * px_per_mm + probe.marker_px) / h

    return SweepPlan(half_x, half_y, grid_n, probe.origin, poses,
                     (min(span_u, 1.0), min(span_v, 1.0)), probe)


# ---------------------------------------------------------------------------
# sweep
# ---------------------------------------------------------------------------

@dataclass
class SweepData:
    track_px: np.ndarray          # (n_poses, 4, 2)
    gantry: np.ndarray            # (n_poses, 2)
    image_size: tuple[int, int]
    sweep_z: float
    track_id: int
    marker_side_mm: float | None
    skipped: list[tuple[int, str]] = field(default_factory=list)

    @property
    def coverage(self) -> tuple[float, float, float, float]:
        p = self.track_px.reshape(-1, 2)
        return (p[:, 0].min(), p[:, 1].min(), p[:, 0].max(), p[:, 1].max())

    def save(self, path: str) -> str:
        np.savez(path, track_px=self.track_px, gantry=self.gantry,
                 image_size=np.array(self.image_size), sweep_z=self.sweep_z,
                 track_id=self.track_id,
                 marker_side_mm=np.nan if self.marker_side_mm is None
                 else self.marker_side_mm)
        return path

    @classmethod
    def load(cls, path: str) -> "SweepData":
        d = np.load(path)
        side = float(d["marker_side_mm"])
        return cls(d["track_px"], d["gantry"], tuple(int(v) for v in d["image_size"]),
                   float(d["sweep_z"]), int(d["track_id"]),
                   None if np.isnan(side) else side)


def run_sweep(robot: Robot, camera: Camera, tracker: MarkerTracker,
              plan: SweepPlan, *, settle_s: float = 0.0,
              backlash_mm: float = 1.0, marker_side_mm: float | None = None,
              on_progress=None, on_frame=None, cancel=None,
              log=print) -> SweepData:
    """Visit every planned pose and record the marker.

    Two habits here matter more than they look. Every pose is approached from
    the same side, so lost motion is identical everywhere instead of alternating
    with the raster direction. And the pose is read back after the move rather
    than assumed, so the robot's own positioning error never enters the fit.

    Settling is measured rather than waited out: the tracker keeps grabbing
    until consecutive readings of the marker agree, so a pose costs exactly as
    long as the gantry needs. settle_s adds a fixed pause before that starts and
    is normally left at zero.
    """
    # Z is recorded, never commanded: the camera is fixed in height and the
    # sweep is pure XY travel.
    _, _, z = plan.origin
    track_px, gantry, skipped = [], [], []
    previous = np.array(xyz(robot)[:2])

    for i, (tx, ty) in enumerate(plan.poses, 1):
        if cancel is not None and cancel.is_set():
            raise Cancelled(f"stopped after {len(gantry)} poses")

        target = np.array([tx, ty])
        # Arrive travelling in the same direction on every pose, so lost motion
        # is the same everywhere. An axis whose step is already positive needs
        # nothing; only one moving the other way takes a detour below the
        # target first. Doing it unconditionally meant a detour on both axes at
        # every pose, which is most of the motion in a raster where one axis
        # usually does not move at all, and it left the gantry ringing when the
        # picture was taken.
        step = target - previous
        needs_detour = step < -1e-9
        if backlash_mm and needs_detour.any():
            goto_xy(robot, *(target - needs_detour * backlash_mm))
        goto_xy(robot, tx, ty)
        previous = target
        if settle_s:
            time.sleep(settle_s)
        t_settled = time.monotonic()

        corners = tracker.measure(camera, after=t_settled)

        if on_frame is not None and tracker.last_frame is not None:
            on_frame(tracker.last_frame, corners, i, len(plan.poses))
        if on_progress is not None:
            on_progress(i, len(plan.poses))

        if corners is None:
            reason = ("never settled, jitter %.2f px" % tracker.last_jitter
                      if np.isfinite(tracker.last_jitter) else "not detected")
            skipped.append((i, reason))
            log(f"  [{i}/{len(plan.poses)}] skipped, {reason}")
            continue

        x, y, _ = xyz(robot)                 # actual, not commanded
        track_px.append(corners)
        gantry.append([x, y])

    if len(gantry) < 12:
        raise CalibrationError(
            f"only {len(gantry)} usable poses out of {len(plan.poses)}; "
            f"check lighting and that the marker stays in frame"
        )

    return SweepData(np.array(track_px), np.array(gantry),
                     tuple(camera.resolution), float(z),
                     int(tracker.track_id), marker_side_mm, skipped)


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def calibrate_camera(robot: Robot, camera: Camera, detector, *,
                     marker_side_mm: float | None = None,
                     grid_n: int = 7, degree: int = 3,
                     target_id: int | None = None,
                     frames_per_pose: int = 5,
                     settle_s: float = 0.0,
                     backlash_mm: float = 1.0,
                     margin_px: float = 20.0,
                     on_progress=None, on_frame=None, cancel=None,
                     log=print) -> tuple[PixelMap, FitReport, SweepData]:
    """Probe the scale, plan the sweep, run it, and fit.

    The camera must already be at its working configuration: the map is only
    valid for the focus, zoom and working distance it was fitted at. Centre the
    marker in the frame before calling.
    """
    tracker = MarkerTracker(detector, target_id=target_id, frames=frames_per_pose)

    log("measuring scale")
    probe = measure_scale(robot, camera, tracker, settle_s=settle_s, log=log)
    fov = probe.fov_mm(camera.resolution)
    log(f"  field of view {fov[0]:.1f} x {fov[1]:.1f} mm, "
        f"marker {probe.marker_px:.0f} px")

    plan = plan_sweep(probe, camera.resolution, grid_n=grid_n, margin_px=margin_px)
    log(f"planning: {plan}")

    log(f"sweeping {len(plan.poses)} poses")
    sweep = run_sweep(robot, camera, tracker, plan, settle_s=settle_s,
                      backlash_mm=backlash_mm, marker_side_mm=marker_side_mm,
                      on_progress=on_progress, on_frame=on_frame,
                      cancel=cancel, log=log)
    log(f"  collected {len(sweep.gantry)}/{len(plan.poses)} poses, "
        f"tracking id {sweep.track_id}")

    goto_xy(robot, plan.origin[0], plan.origin[1])

    pmap, report = fit_pixel_map(
        sweep.track_px, sweep.gantry, sweep.image_size, degree=degree,
        sweep_z=sweep.sweep_z, marker_side_mm=marker_side_mm)
    return pmap, report, sweep
