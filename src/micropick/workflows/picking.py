"""The picking session: one transition at a time.

`PickingSession` drives the pick-and-place loop that fills a plate or a set of
deck coordinates with microtissues. It is the orchestration layer, so it uses
both `core` (routine, vision, pixel map) and `hardware` (robot, camera), but it
owns none of the things a caller owns.

No loop of its own
------------------
`step()` performs exactly one state transition and returns a `PickEvent`. The
caller holds the loop, the pause, the stop and the display. This is the decision
recorded in DESIGN section 11: a notebook calls `step()` in a `while`, a future
GUI calls it from a worker thread, and neither has to fight a loop buried in
here.

Pause and stop are `threading.Event` and are checked *between individual robot
moves*, not only between states. A pause raised in the middle of a five-cuboid
pickup takes effect at the next move, because `_gate` sits before every move.

No windows, no keyboard, no printing. Frames leave through the `on_frame`
callback; drawing belongs to `viz/overlays.py`, not here. Everything the session
needs is passed to the constructor; there are no module globals.

The session starts idle and stays there until `start()` (or `resume()`, the same
go-ahead under the name the operator already knows) is called: a run beginning
by itself the moment the cell executes leaves nobody time to look at the dish.
Which key means "go" is the caller's business; all the session knows is that it
has not been told yet.

The head of the picking cycle is `DETECT_FLOATERS`, not `CAPTURE_FRAME`: the
frame a decision is made from has to be taken *after* the floaters are measured,
not 2.5 s before. Every path back into the loop returns there.

`live_view` says which picture the caller should show. During the floater clip
the operator has to watch the dish move, so the display follows the camera;
everywhere else the useful picture is the annotated frame the last decision was
made from, held until the next one. The session only reports the mode; it does
not know what a window is.

What this fixes from the old `TissuePickerFSM` (DESIGN section 11):
- one source of truth for the current target: `routine.current`, never a second
  `self.current_well` that drifts from it;
- `self.routine`, never a module-level `routine`;
- no infinite ANALYZE -> SHAKE loop: `max_shake_retries` hands back to the
  operator (`NEEDS_OPERATOR`);
- the shake pose and the batch cap live in the profile and the config, not in
  the code;
- idle no longer runs YOLO in a tight loop;
- verify checks the *full* detection frame, so a cuboid nudged out of the shape
  window by the pipette is still seen and counted as a miss;
- camera read failures raise instead of being ignored;
- the magic numbers (`+20 mm`, `0.75 s`, `flow_rate=5`) are config.

Behaviour change (requested): on a partial miss the held cuboids are kept and
the dispensed volume is proportional to the count actually held, so per-well
concentration stays constant. `PickingConfig.miss_policy` selects this
(`keep_successful`) or the old return-everything behaviour (`return_all`).

Both the dispensed volume and the trip to the well itself follow the count
actually held, never the count aimed at. A pickup that held nothing returns its
volume to the dish and starts the cycle again without going near the plate;
`max_empty_pickups` of those in a row hand back to the operator.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

import cv2
import numpy as np
import pandas as pd

from ..core.vision import cuboids as vision
from ..core.calibration.homography import Homography
from ..hardware.protocols import (Camera, Robot, move_relative, move_to,
                                   require_ok, xyz)

__all__ = ["RobotState", "PickEvent", "PickingSession", "PickingError"]


class PickingError(RuntimeError):
    """A precondition for picking was not met."""


class _Cancelled(Exception):
    """Internal: a stop event was seen between moves."""


class RobotState(Enum):
    IDLE = "idle"
    CAPTURE_FRAME = "capture_frame"
    DETECT_FLOATERS = "detect_floaters"
    ANALYZE_FRAME = "analyze_frame"
    APPROACH_TARGET = "approach_target"
    PICKUP_SAMPLE = "pickup_sample"
    VERIFY_PICKUP = "verify_pickup"
    DEPOSIT_BACK = "deposit_liquid_back"
    TRANSFER_TO_WELL = "transfer_to_well"
    AUTO_SHAKE = "auto_shake"
    NEEDS_OPERATOR = "needs_operator"
    COMPLETED = "completed"
    CANCELED = "canceled"


_TERMINAL = {RobotState.COMPLETED, RobotState.CANCELED}

# States whose picture is worth watching live rather than frozen: the operator is
# looking at the dish itself, either before the run or while the floater clip
# records the movement that identifies them.
_LIVE = {RobotState.IDLE, RobotState.DETECT_FLOATERS}

# How often the idle state looks to see whether it has been told to start. A step
# of the waiting mechanism, not a parameter of the process, so it is not config.
_IDLE_POLL_S = 0.1


@dataclass
class PickEvent:
    """The result of one `step()`: where the session is now and what happened."""

    state: RobotState
    kind: str
    message: str
    data: dict = field(default_factory=dict)

    def __str__(self) -> str:
        extra = f" {self.data}" if self.data else ""
        return f"[{self.state.value}] {self.kind}: {self.message}{extra}"


class PickingSession:
    """Holds the picking state; advances it one transition per `step()`."""

    def __init__(self, robot: Robot, camera: Camera, pixel_map, profile,
                 routine, detector, *, labware_id: str | None = None,
                 under_cam: Camera | None = None, clip_dir=None,
                 on_frame=None, logger=None):
        self.robot = robot
        self.camera = camera
        self.pixel_map = pixel_map
        self.profile = profile
        self.routine = routine
        self.detector = detector
        self.labware_id = labware_id
        self.on_frame = on_frame
        self.logger = logger

        self.config = profile.picking

        offset = profile.calibration.pipette_offset
        if offset is None:
            raise PickingError(
                "profile has no pipette offset; run the pipette calibration "
                "before picking")
        self._offset = np.array([offset.dx, offset.dy], dtype=float)

        # The observation pose is a taught landmark, like every other deck
        # position; where() raises with a helpful list if it is missing.
        self._observe = np.array(profile.where("observe"), dtype=float)

        # The vision pipeline still takes scalar mm-per-pixel ratios. With the
        # pixel map the scale varies across the frame, so we take the local
        # value at the dish centre as the representative one; it is only used
        # for object sizing and neighbour spacing, not for targeting.
        cx, cy = self.config.circle_center
        mmpp = float(np.mean(self.pixel_map.mm_per_px(cx, cy)))
        self._one_d_ratio = mmpp
        self._size_ratio = mmpp * mmpp

        self.state = RobotState.IDLE
        self._started = False
        self._frame: np.ndarray | None = None
        self._gantry: np.ndarray | None = None
        self.cuboid_df = pd.DataFrame()
        self.pickable = pd.DataFrame()
        self.isolated = pd.DataFrame()
        self._choice: pd.DataFrame | None = None
        self._world: list[tuple[float, float]] = []
        self._shake_retries = 0
        self._empty_pickups = 0
        # due, not zero: the first cycle of a run must measure the floaters, and
        # counting up from zero silently skips the first `interval` cycles.
        self._cycles_since_floater = self.config.floater_check_interval
        self.floater_zones: list[tuple[float, float]] = []
        self._deposit_volume = 0.0
        self._pending_transfer = False
        self._held = 0

        # Lower-camera clip recording is fully optional. The recorder is created
        # and attached only when both a lower camera and a destination folder are
        # given; otherwise nothing is created and no frames are ever accumulated.
        self._under_cam = under_cam
        self._clip_dir = None
        self._recorder = None
        self._homography = None
        if clip_dir is not None:
            if under_cam is None:
                raise PickingError("clip_dir was given but under_cam is None; "
                                   "recording needs the lower camera")
            self._clip_dir = self._prepare_clip_dir(clip_dir)
            self._recorder = under_cam.record(max_frames=self.config.clip_max_frames)
            hcfg = profile.calibration.homography
            if hcfg is not None:
                self._homography = Homography.from_config(hcfg)

        # Everything below happens before the first move: fail on a resumed run
        # that has not been acknowledged, or on a slot that does not hold this
        # routine's plate, rather than mid-plate with aspirate in the tip.
        self._preflight()

    def _preflight(self) -> None:
        routine = self.routine
        from ..core.routine import RoutineError
        try:
            routine.validate()                       # whole plan, before any move
        except RoutineError as exc:
            raise PickingError(str(exc)) from exc
        if routine.needs_confirmation:
            raise PickingError(
                "this routine was resumed from disk with progress already on "
                "it. Review routine.summary() and call routine.confirm_resume() "
                "first, so continuing onto the plate now loaded is a deliberate "
                "choice.\n" + routine.summary())
        if routine.destination.is_plate:
            from ..hardware.labware import loaded_labware
            try:
                routine.check_labware(loaded_labware(self.robot))
            except RoutineError as exc:
                raise PickingError(str(exc)) from exc

    @staticmethod
    def _prepare_clip_dir(clip_dir):
        import os
        path = str(clip_dir)
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as exc:
            raise PickingError(f"clip_dir {path!r} is not usable: {exc}") from exc
        if not os.access(path, os.W_OK):
            raise PickingError(f"clip_dir {path!r} is not writable")
        return path

    def close(self) -> None:
        """Detach the recorder from the camera. Safe to call more than once."""
        if self._recorder is not None and self._under_cam is not None:
            self._under_cam.detach(self._recorder)
            self._recorder = None

    # -- public -------------------------------------------------------------

    def step(self, pause=None, stop=None) -> PickEvent:
        """Run the current state once and return what happened."""
        if self.state is RobotState.COMPLETED:
            return self._event("completed", "already complete")
        if self.state is RobotState.CANCELED:
            return self._event("canceled", "already canceled")
        handler = getattr(self, f"_state_{self.state.value}")
        try:
            return handler(pause, stop)
        except _Cancelled:
            self.state = RobotState.CANCELED
            return self._event("canceled", "stopped between moves")

    def start(self) -> None:
        """The operator's go-ahead: leave the initial idle state.

        Idempotent, and safe to call from another thread than the one inside
        `step()` — that is the normal case, since the display thread reads the
        key while the worker thread sits in IDLE.
        """
        self._started = True

    def resume(self) -> None:
        """Carry on after a state that waits for a person.

        One command covers both: the go-ahead out of IDLE and the retry out of
        NEEDS_OPERATOR, so the caller needs a single key. Both counters are
        cleared, since the operator has just had a chance to fix the dish.
        """
        if self.state is RobotState.IDLE:
            self.start()
            return
        if self.state is RobotState.NEEDS_OPERATOR:
            self._shake_retries = 0
            self._empty_pickups = 0
            self.state = RobotState.DETECT_FLOATERS

    @property
    def done(self) -> bool:
        return self.state in _TERMINAL

    @property
    def live_view(self) -> bool:
        """True while the camera itself is the useful picture, False while the
        last annotated decision frame is. The caller decides what to do with
        that; this only reports which one the state calls for."""
        return self.state in _LIVE

    @property
    def choice(self) -> pd.DataFrame | None:
        """The cuboids chosen for the current pickup, or None between cycles.
        Exposed so overlays can draw them; treat as read-only."""
        return self._choice

    @property
    def verify_radius_px(self) -> float:
        """Radius around a chosen position within which a detection still means
        the cuboid was not picked. `_count_misses` compares against this same
        number, so what is drawn is what is decided."""
        return self.config.failure_threshold / self._one_d_ratio

    # -- helpers ------------------------------------------------------------

    def _event(self, kind: str, message: str, **data) -> PickEvent:
        return PickEvent(self.state, kind, message, data)

    def _log(self, message: str) -> None:
        if self.logger is not None:
            self.logger.log(message)

    def _emit(self, frame) -> None:
        if self.on_frame is not None:
            self.on_frame(frame, self.cuboid_df)

    def _gate(self, pause, stop) -> None:
        """Checkpoint between two robot moves. Blocks while paused, raises on
        stop. This is what makes a pause take effect mid-batch."""
        if stop is not None and stop.is_set():
            raise _Cancelled
        if pause is not None:
            while pause.is_set():
                if stop is not None and stop.is_set():
                    raise _Cancelled
                pause.wait(0.05)

    def _fresh_frame(self):
        frame = self.camera.read_after(time.monotonic())
        if frame is None:
            raise PickingError("camera returned no frame")
        return frame

    def _run_pipeline(self, frame) -> None:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        roi = vision.roi_mask(gray, self.config, self._one_d_ratio)
        boxes, confs = vision.detect_boxes(self.detector, frame, self.config)
        df = vision.build_cuboid_df(gray, boxes, confs, roi_mask=roi,
                                    pad=self.config.otsu_pad,
                                    open_k=self.config.otsu_open_k)
        df = vision.add_derived(df, self._size_ratio, self._one_d_ratio,
                                self.config.circle_center)
        self.cuboid_df = df
        if len(df) == 0:
            self.pickable = df
            self.isolated = df
            return
        self.pickable = df.loc[vision.select_pickable(df, self.config)].copy()
        self.pickable = vision.drop_in_zones(self.pickable, self.floater_zones,
                                             self.config.floater_zone_radius_px)
        self.isolated = self.pickable.loc[
            self.pickable.min_dist_mm > self.config.minimum_distance]

    # -- states -------------------------------------------------------------

    def _state_idle(self, pause, stop) -> PickEvent:
        """Wait for the go-ahead, then retract and enter the cycle.

        The wait blocks inside `step()` rather than returning an event per poll:
        a caller that loops on `step()` would otherwise spin, and there is
        nothing to report until something happens. `_gate` keeps the stop event
        working, so cancelling from idle behaves like cancelling anywhere else.
        """
        while not self._started:
            self._gate(pause, stop)
            time.sleep(_IDLE_POLL_S)

        self._gate(pause, stop)
        require_ok(self.robot.retract_axis("leftZ"), "retract")
        self.state = RobotState.DETECT_FLOATERS
        return self._event("started", "operator started the run")

    def _state_capture_frame(self, pause, stop) -> PickEvent:
        self._gate(pause, stop)
        move_to(self.robot, self._observe, min_z_height=self.config.dish_bottom)
        if self.config.capture_settle_s:
            time.sleep(self.config.capture_settle_s)
        self._frame = self._fresh_frame()
        self._gantry = np.array(xyz(self.robot)[:2])
        self.state = RobotState.ANALYZE_FRAME
        return self._event("captured", "frame taken at the observation pose")

    def _state_detect_floaters(self, pause, stop) -> PickEvent:
        """Head of the cycle. Measures which objects drift, before the frame
        that the pickup decision is made from is taken."""
        if self._cycles_since_floater < self.config.floater_check_interval:
            self._cycles_since_floater += 1
            self.state = RobotState.CAPTURE_FRAME
            return self._event("floaters", "skipped, within interval")

        # The clip is the dish seen from above, so the pose has to be right
        # first: this state is entered from the shake pose and, at the start of
        # a run, from wherever the gantry happened to be left.
        self._gate(pause, stop)
        move_to(self.robot, self._observe, min_z_height=self.config.dish_bottom)
        frames = self._grab_clip(self.config.floater_clip_sec, pause, stop)
        self.floater_zones = vision.detect_floater_zones(
            frames, min_area=self.config.floater_min_area,
            mad_k=self.config.floater_mad_k)
        self._cycles_since_floater = 0
        self._log(f"floater check: {len(self.floater_zones)} zones")
        self.state = RobotState.CAPTURE_FRAME
        return self._event("floaters", "checked",
                           zones=len(self.floater_zones))

    def _grab_clip(self, duration, pause, stop) -> list[np.ndarray]:
        frames: list[np.ndarray] = []
        end = time.monotonic() + duration
        while time.monotonic() < end:
            self._gate(pause, stop)
            ret, frame = self.camera.read()
            if not ret or frame is None:
                raise PickingError("camera returned no frame during the clip")
            frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        return frames

    def _state_analyze_frame(self, pause, stop) -> PickEvent:
        # Dropped before the pipeline runs: the frame emitted below must carry
        # this cycle's choice or none at all, never the last cycle's cuboids
        # drawn over a dish they have already left.
        self._choice = None
        self._run_pipeline(self._frame)

        if self.routine.current is None:
            self.routine.next()
        current = self.routine.current
        if current is None:
            self._emit(self._frame)
            self.state = RobotState.COMPLETED
            return self._event("completed", "routine already satisfied")

        if len(self.isolated) == 0:
            self._shake_retries += 1
            self._log("no isolated cuboids in the working region")
            self._emit(self._frame)
            if self._shake_retries >= self.config.max_shake_retries:
                self.state = RobotState.NEEDS_OPERATOR
                return self._event("needs_operator",
                                   "no isolated cuboids after "
                                   f"{self._shake_retries} shakes",
                                   retries=self._shake_retries)
            self.state = RobotState.AUTO_SHAKE
            return self._event("no_cuboids", "nothing isolated, will shake",
                               retries=self._shake_retries)

        self._shake_retries = 0
        remaining = self.routine.remaining(current)
        want = 1 if self.config.one_by_one else min(remaining, self.config.max_batch)
        want = max(1, min(want, len(self.isolated)))
        self._choice = (self.isolated.sample(n=want)
                        if len(self.isolated) > want else self.isolated)
        self._emit(self._frame)                 # after the choice, so it shows
        self.state = RobotState.APPROACH_TARGET
        return self._event("analyzed", "chose a batch",
                           isolated=len(self.isolated), batch=len(self._choice))

    def _state_auto_shake(self, pause, stop) -> PickEvent:
        shake = self.profile.where("shake")
        self._gate(pause, stop)
        require_ok(self.robot.retract_axis("leftZ"), "retract")
        self._gate(pause, stop)
        move_to(self.robot, shake, min_z_height=self.config.dish_bottom)
        for _ in range(3):
            self._gate(pause, stop)
            move_relative(self.robot, "x", 10)
            self._gate(pause, stop)
            move_relative(self.robot, "x", -10)
        self._gate(pause, stop)
        move_relative(self.robot, "x", -2)
        self._gate(pause, stop)
        move_relative(self.robot, "x", 2)
        # a clip is mandatory after a shake: what floats has just been stirred,
        # and the zones measured before it say nothing about the dish now.
        self._cycles_since_floater = self.config.floater_check_interval
        self.state = RobotState.DETECT_FLOATERS
        return self._event("shaken", "shook the dish")

    def _state_approach_target(self, pause, stop) -> PickEvent:
        self._world = []
        for cX, cY in self._choice[["cX", "cY"]].values:
            xy = np.asarray(self.pixel_map.to_robot(cX, cY, self._gantry))
            xy = xy + self._offset
            self._world.append((float(xy[0]), float(xy[1])))
        self._begin_clip()
        self.state = RobotState.PICKUP_SAMPLE
        return self._event("approached", "computed pickup coordinates",
                           n=len(self._world))

    def _begin_clip(self) -> None:
        """Start a lower-camera clip for this batch, marking where the first
        cuboid will be picked. The ROI box is drawn only if a homography is
        present and valid at the current pose; without one the clip records
        without a box, which is not an error."""
        if self._recorder is None:
            return
        first = self._choice[["cX", "cY"]].values[0]
        under_px = None
        if self._homography is not None:
            under_px = self._homography.over_to_under([first], self._gantry)
        if under_px is not None:
            self._recorder.mark_roi(under_px[0][0], under_px[0][1])
        else:
            self._recorder.clear_roi()
        self._recorder.start()

    def _state_pickup_sample(self, pause, stop) -> PickEvent:
        ph = self.config.pickup_height
        lift = self.config.lift_mm
        for x, y in self._world:
            self._gate(pause, stop)
            move_to(self.robot, (x, y, ph + lift),
                    min_z_height=self.config.dish_bottom, force_direct=True)
            self._gate(pause, stop)
            move_to(self.robot, (x, y, ph),
                    min_z_height=self.config.dish_bottom, force_direct=True)
            self._gate(pause, stop)
            require_ok(self.robot.aspirate_in_place(
                volume=self.config.vol, flow_rate=self.config.flow_rate),
                "aspirate")
            self._gate(pause, stop)
            move_relative(self.robot, "z", lift)
        self._save_clip()
        self.state = RobotState.VERIFY_PICKUP
        return self._event("picked", "aspirated the batch", n=len(self._world))

    def _save_clip(self) -> None:
        """Stop the clip and write it, named for the current target. Encoding
        runs off the picking loop so it does not stall the next pickup."""
        if self._recorder is None or not self._recorder.recording:
            return
        self._recorder.stop()
        stamp = time.strftime("%Y%m%d_%H%M%S")
        target = str(self.routine.current).replace(" ", "")
        path = f"{self._clip_dir}/{target}_{stamp}.mp4"
        try:
            self._recorder.save_async(path, color=True)
        except Exception as exc:                       # a clip is never critical
            self._log(f"clip not saved: {exc}")

    def _state_verify_pickup(self, pause, stop) -> PickEvent:
        self._gate(pause, stop)
        move_to(self.robot, self._observe,
                min_z_height=self.config.dish_bottom, force_direct=True)
        time.sleep(self.config.verify_settle_s)
        self._frame = self._fresh_frame()
        self._run_pipeline(self._frame)
        self._emit(self._frame)

        attempted = len(self._choice)
        misses = self._count_misses()
        held = attempted - misses
        current = self.routine.current
        self._log(f"well {current}: {held} held, {misses} missed")

        # return_all treats a partial miss as a total one, so nothing is held
        # under it either; from here on only `held` decides.
        if self.config.miss_policy == "return_all" and misses > 0:
            held = 0
        self._held = held
        self.routine.record(delivered=held, missed=attempted - held)

        if held == 0:
            # Nothing in the tip, so nothing to deliver and no reason to visit
            # the well. Everything goes back to the dish and the cycle restarts.
            self._empty_pickups += 1
            self._deposit_volume = self.config.vol * attempted
            self._pending_transfer = False
            self.state = RobotState.DEPOSIT_BACK
        else:
            self._empty_pickups = 0
            if misses > 0:
                self._deposit_volume = self.config.vol * misses
                self._pending_transfer = True
                self.state = RobotState.DEPOSIT_BACK
            else:
                self.state = RobotState.TRANSFER_TO_WELL
        return self._event("verified", "checked the pickup",
                           attempted=attempted, held=held, missed=attempted - held)

    def _count_misses(self) -> int:
        """A miss is a chosen position where a cuboid still sits. Checked
        against the full detection frame, not the pickable subset, so a cuboid
        knocked out of the shape window by the tip is not mistaken for a
        success."""
        if self._choice is None or len(self.cuboid_df) == 0:
            return 0
        det = self.cuboid_df[["cX", "cY"]].values
        radius = self.verify_radius_px           # the same circle overlays draw
        misses = 0
        for prev_x, prev_y in self._choice[["cX", "cY"]].values:
            dist_px = np.hypot(det[:, 0] - prev_x, det[:, 1] - prev_y)
            if np.any(dist_px <= radius):
                misses += 1
        return misses

    def _state_deposit_liquid_back(self, pause, stop) -> PickEvent:
        x, y = self._world[0]
        ph = self.config.pickup_height
        lift = self.config.lift_mm
        self._gate(pause, stop)
        move_to(self.robot, (x, y, ph + lift),
                min_z_height=self.config.dish_bottom, force_direct=True)
        self._gate(pause, stop)
        move_to(self.robot, (x, y, ph + 0.5),
                min_z_height=self.config.dish_bottom, force_direct=True)
        self._gate(pause, stop)
        require_ok(self.robot.dispense_in_place(
            volume=self._deposit_volume, flow_rate=self.config.flow_rate),
            "dispense back to dish")
        self._gate(pause, stop)
        move_relative(self.robot, "z", lift)

        if self._pending_transfer:
            self._pending_transfer = False
            self.state = RobotState.TRANSFER_TO_WELL
        elif self._empty_pickups >= self.config.max_empty_pickups:
            # Cuboids keep being detected and keep not being caught: something
            # is wrong with the dish or the tip, and repeating cannot fix it.
            self.state = RobotState.NEEDS_OPERATOR
            return self._event("needs_operator",
                               "returned the volume; "
                               f"{self._empty_pickups} pickups in a row held "
                               "nothing", volume=self._deposit_volume,
                               empty=self._empty_pickups)
        else:
            self.state = RobotState.DETECT_FLOATERS
        return self._event("deposited_back", "returned volume to the dish",
                           volume=self._deposit_volume)

    def _state_transfer_to_well(self, pause, stop) -> PickEvent:
        current = self.routine.current
        volume = self.config.vol * self._held
        cfg = self.config
        if self.routine.destination.is_plate:
            if self.labware_id is None:
                raise PickingError(
                    "the routine has a plate destination but no labware_id was "
                    "given to the session")
            self._gate(pause, stop)
            require_ok(self.robot.move_to_well(
                self.labware_id, current, well_location="top",
                offset=(cfg.well_offset_x, cfg.well_offset_y, 5),
                force_direct=True), "move to well")
            self._gate(pause, stop)
            require_ok(self.robot.dispense(
                self.labware_id, current, well_location="bottom",
                offset=(cfg.well_offset_x, cfg.well_offset_y, cfg.deposit_offset_z),
                volume=volume, flow_rate=cfg.flow_rate), "dispense to well")
            time.sleep(cfg.wait_time_after_deposit)
            self._gate(pause, stop)
            require_ok(self.robot.move_to_well(
                self.labware_id, current, well_location="top",
                offset=(cfg.well_offset_x, cfg.well_offset_y, 5)),
                "retract from well")
            self._gate(pause, stop)
            move_to(self.robot, self._observe,
                    min_z_height=cfg.dish_bottom, force_direct=True)
        else:
            x, y = current
            self._gate(pause, stop)
            move_to(self.robot, (x, y, cfg.deposit_z_optional),
                    min_z_height=cfg.dish_bottom)
            self._gate(pause, stop)
            require_ok(self.robot.dispense_in_place(
                volume=volume, flow_rate=cfg.flow_rate), "dispense")
            time.sleep(cfg.wait_time_after_deposit)
            for axis, step in (("x", 0.1), ("x", -0.2), ("x", 0.1),
                               ("y", 0.1), ("y", -0.2), ("y", 0.1)):
                self._gate(pause, stop)
                move_relative(self.robot, axis, step)
            self._gate(pause, stop)
            move_relative(self.robot, "z", cfg.lift_mm)
            self._gate(pause, stop)
            move_to(self.robot, self._observe,
                    min_z_height=cfg.dish_bottom, force_direct=True)

        nxt = self.routine.next()
        if nxt is None or self.routine.is_done():
            self.state = RobotState.COMPLETED
        else:
            self.state = RobotState.DETECT_FLOATERS
        return self._event("transferred", "deposited into the destination",
                           target=str(current), volume=volume)

    def _state_needs_operator(self, pause, stop) -> PickEvent:
        return self._event("needs_operator",
                           "waiting for the operator; call resume() to retry")

    def _state_completed(self, pause, stop) -> PickEvent:
        self._log("picking finished")
        return self._event("completed", "all targets filled")
