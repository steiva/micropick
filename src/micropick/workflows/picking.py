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

No windows, no keyboard, no printing. Frames leave on the event, as its `view`;
drawing belongs to `viz/overlays.py`, not here. Everything the session needs is
passed to the constructor; there are no module globals.

The session starts idle and stays there until `start()` (or `resume()`, the same
go-ahead under the name the operator already knows) is called: a run beginning
by itself the moment the cell executes leaves nobody time to look at the dish.
Which key means "go" is the caller's business; all the session knows is that it
has not been told yet. `NEEDS_OPERATOR` is that same wait for a different
reason, so it is the same state in every respect that shows: the gantry returns
to the observation pose, the picture is live, and the go-ahead and the stop both
work exactly as they do in idle.

The head of the picking cycle is `DETECT_FLOATERS`, not `CAPTURE_FRAME`: the
frame a decision is made from has to be taken *after* the floaters are measured,
not 2.5 s before. Every path back into the loop returns there.

Every event carries a `PickView`: either "read the camera" or the picture the
last decision was made from, with the tables that were measured on *that* frame.
A caller that draws its own live stream instead cannot hold a frame at all, and
ends up drawing contours over a newer picture than the one they were measured
on. The session says what to show and hands over the picture; it still does not
know what a window is.

The observation pose and the rail light belong to the session for the length of
the run. Both are preparation for photographing the dish rather than steps an
operator takes, the return to `observe` is needed from `NEEDS_OPERATOR` as well
as at the start, and a light left on shifts every detection threshold.

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
from ..core.calibration.homography import Homography, HomographyError
from ..hardware.protocols import (Camera, Robot, move_relative, move_to,
                                   require_ok, set_lights, xyz)

__all__ = ["RobotState", "PickEvent", "PickView", "PickingSession",
           "PickingError"]


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
# looking at the dish itself, either while waiting to be let on with the run or
# while the floater clip records the movement that identifies them. Everywhere
# else the stream shows travel and says nothing, so it does not run.
_LIVE = {RobotState.IDLE, RobotState.NEEDS_OPERATOR, RobotState.DETECT_FLOATERS}

# How often the idle state looks to see whether it has been told to start. A step
# of the waiting mechanism, not a parameter of the process, so it is not config.
_IDLE_POLL_S = 0.1


def _clip_view(camera) -> tuple[tuple[int, int], tuple[int, int], object]:
    """Where the recorded frame sits in the sensor frame, and how to cut it out.

    Returns (origin, size, transform), and returns all three together because
    the origin subtracted from a point drawn on the clip and the crop applied to
    the frame have to be the same thing. They were computed apart, and
    `center_crop_box` answers with the centred *square* even at crop 1.0 — for a
    2000x1500 frame that is an origin of (250, 0) — so at 1.0 the origin was
    subtracted while no transform was attached, and every ROI box landed a
    quarter of a frame height to the left of its cuboid on a clip that had never
    been cropped at all. A crop of 1.0 means the whole frame; it is said here
    once, rather than guarded for at each of the two places that need it.
    """
    frac = float(getattr(camera, "crop", 1.0))
    w, h = camera.resolution
    if frac >= 1.0:
        return (0, 0), (w, h), None
    x0, y0, side = vision.center_crop_box((h, w), frac)
    return (x0, y0), (side, side), lambda f: vision.center_crop(f, frac)[0]


@dataclass
class PickView:
    """What the caller should have on the screen while this state runs.

    `live` means read the camera: the operator is watching the dish itself.
    Otherwise `frame` is the picture the decision was made from and stays up,
    unchanged, until the session hands over the next one. `overlays` are keyword
    arguments for `viz.overlays.annotate`, and they belong to `frame` — the
    tables in it were measured on that picture and no other.

    The frame is handed over, not copied: `annotate` copies before it draws, so
    a caller that goes through it cannot damage what the session is still using,
    and a caller that draws directly must copy first.
    """

    live: bool
    frame: np.ndarray | None = None
    overlays: dict = field(default_factory=dict)


@dataclass
class PickEvent:
    """The result of one `step()`: where the session is now and what happened."""

    state: RobotState
    kind: str
    message: str
    data: dict = field(default_factory=dict)
    view: PickView | None = None

    def __str__(self) -> str:
        # the view is deliberately absent: a frame and three detection tables
        # printed once per transition bury the line that says what happened
        extra = f" {self.data}" if self.data else ""
        return f"[{self.state.value}] {self.kind}: {self.message}{extra}"


class PickingSession:
    """Holds the picking state; advances it one transition per `step()`."""

    def __init__(self, robot: Robot, camera: Camera, pixel_map, profile,
                 routine, detector, *, labware_id: str | None = None,
                 under_cam: Camera | None = None, clip_dir=None,
                 logger=None):
        self.robot = robot
        self.camera = camera
        self.pixel_map = pixel_map
        self.profile = profile
        self.routine = routine
        self.detector = detector
        self.labware_id = labware_id
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
        # the picture the last decision was made from, and the overlays measured
        # on it; what a held view shows until the next decision replaces both
        self._held_frame: np.ndarray | None = None
        self._held_overlays: dict = {}
        self._lights_before: bool | None = None
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
        self._clip_crop = (0, 0)                       # origin of what it records
        self._clip_size = (0, 0)                       # what the clip records
        if clip_dir is not None:
            if under_cam is None:
                raise PickingError("clip_dir was given but under_cam is None; "
                                   "recording needs the lower camera")
            self._clip_dir = self._prepare_clip_dir(clip_dir)
            # The clip is a picture for a person, so it carries the camera's view
            # crop. The transform runs in the grab thread, once per frame, and
            # nothing else sees it: what the session measures stays whole.
            self._clip_crop, self._clip_size, transform = _clip_view(under_cam)
            self._recorder = under_cam.record(
                max_frames=self.config.clip_max_frames, transform=transform)
            hcfg = profile.calibration.homography
            if hcfg is not None:
                self._homography = Homography.from_config(hcfg)

        # Everything below happens before the first move: fail on a resumed run
        # that has not been acknowledged, or on a slot that does not hold this
        # routine's plate, rather than mid-plate with aspirate in the tip.
        self._preflight()

        # Only once the run is going to happen: a session refused above leaves
        # the bench lit as it found it. The rail LEDs put highlights on the
        # meniscus and shift every threshold the detector was tuned at, so they
        # go out for the length of the run and `close()` puts them back.
        self._lights_before = set_lights(self.robot, False)

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
        """Give back everything the session took. Safe to call more than once.

        That is the recorder attached to the lower camera and the rail light it
        darkened. A caller that leaves the window while the run continues does
        *not* call this, and should not: the light belongs to the run, not to
        the window.
        """
        if self._recorder is not None and self._under_cam is not None:
            self._under_cam.detach(self._recorder)
            self._recorder = None
        if self._lights_before:
            set_lights(self.robot, True)
        self._lights_before = None

    def __enter__(self) -> PickingSession:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        """So the light comes back on the way out of an error too."""
        self.close()

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

        The state itself is not assigned here. The transition happens inside
        `step()` like every other one, so a go-ahead arriving from the display
        thread cannot move the session while the worker thread is mid-state.
        """
        if self.state is RobotState.NEEDS_OPERATOR:
            self._shake_retries = 0
            self._empty_pickups = 0
        self.start()

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
    def view(self) -> PickView:
        """What to show while the current state runs.

        Also what every event carries, so a caller that displays `event.view`
        and reads the camera only when it says `live` needs to know nothing
        about the state machine. Before the first frame is taken there is
        nothing to hold, so the answer is the camera whatever the state.
        """
        base = {"floater_zones": self.floater_zones,
                "floater_radius": self.config.floater_zone_radius_px,
                "circle_center": self.config.circle_center,
                "circle_radius": self.config.circle_radius}
        if self.state in _LIVE or self._held_frame is None:
            return PickView(live=True, overlays=base)
        return PickView(live=False, frame=self._held_frame,
                        overlays={**base, **self._held_overlays})

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
        # `self.state` is already the state being entered, so the view on the
        # event is the picture to show while that state runs, not the one that
        # was up while the transition happened.
        return PickEvent(self.state, kind, message, data, self.view)

    def _log(self, message: str) -> None:
        if self.logger is not None:
            self.logger.log(message)

    def _show(self, frame, **overlays) -> None:
        """Hold this frame, with the overlays measured on it, until the next.

        Only the three states that take or decide from a picture call this. What
        is passed here is what a held view shows for as long as the pipette is
        travelling, so the pair has to be consistent: a frame with the previous
        cycle's contours is worse than a bare one.
        """
        self._held_frame = frame
        self._held_overlays = overlays

    def _show_analysis(self, *, verify_radius: float | None = None) -> None:
        """Hold the frame just measured, with everything read off it.

        `verify_radius` is passed only after a pickup, where the circle is the
        decision itself drawn: `_count_misses` calls a cuboid still inside it a
        cuboid that never left. Before the pickup there is nothing to check yet
        and a circle would only claim otherwise.
        """
        self._show(self._frame, cuboid_df=self.cuboid_df,
                   pickable=self.pickable, isolated=self.isolated,
                   chosen=self._choice, verify_radius=verify_radius)

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

    def _wait_for_operator(self, pause, stop) -> None:
        """Stand at the observation pose until told to go on.

        Shared by the two waiting states, which is the point: `NEEDS_OPERATOR`
        is idle with a different reason, and anything either of them does that
        the other does not is a difference the operator has to remember.

        The retract comes before the travel because this is reached from the
        dish as well as from a cold start: with the tip still down, crossing to
        the observation pose drags it through whatever it was standing in.
        """
        self._gate(pause, stop)
        require_ok(self.robot.retract_axis("leftZ"), "retract")
        self._gate(pause, stop)
        move_to(self.robot, self._observe, min_z_height=self.config.dish_bottom)
        while not self._started:
            self._gate(pause, stop)
            time.sleep(_IDLE_POLL_S)

    def _hand_to_operator(self) -> None:
        """Enter the waiting state, clearing the go-ahead it waits for.

        Cleared here rather than in the handler: between this transition and the
        next `step()` the operator can already have pressed the key, and a
        handler that cleared the flag on the way in would swallow it.
        """
        self._started = False
        self.state = RobotState.NEEDS_OPERATOR

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
        """Take up the observation pose, wait for the go-ahead, enter the cycle.

        The move comes first so that what the operator is looking at while they
        decide is the dish, from the pose every later decision is made at.

        The wait blocks inside `step()` rather than returning an event per poll:
        a caller that loops on `step()` would otherwise spin, and there is
        nothing to report until something happens. `_gate` keeps the stop event
        working, so cancelling from idle behaves like cancelling anywhere else.
        """
        self._wait_for_operator(pause, stop)
        self.state = RobotState.DETECT_FLOATERS
        return self._event("started", "operator started the run")

    def _state_capture_frame(self, pause, stop) -> PickEvent:
        self._gate(pause, stop)
        move_to(self.robot, self._observe, min_z_height=self.config.dish_bottom)
        if self.config.capture_settle_s:
            time.sleep(self.config.capture_settle_s)
        self._frame = self._fresh_frame()
        self._gantry = np.array(xyz(self.robot)[:2])
        # Nothing has been measured on this frame yet, and the tables still hold
        # the last cycle's. Dropped rather than kept, so what goes up is a bare
        # picture of the dish instead of contours over cuboids that have left.
        self.cuboid_df = pd.DataFrame()
        self.pickable = pd.DataFrame()
        self.isolated = pd.DataFrame()
        self._choice = None
        self._show(self._frame)
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
        # Dropped before the pipeline runs: the frame shown below must carry
        # this cycle's choice or none at all, never the last cycle's cuboids
        # drawn over a dish they have already left.
        self._choice = None
        self._run_pipeline(self._frame)

        if self.routine.current is None:
            self.routine.next()
        current = self.routine.current
        if current is None:
            self._show_analysis()
            self.state = RobotState.COMPLETED
            return self._event("completed", "routine already satisfied")

        if len(self.isolated) == 0:
            self._shake_retries += 1
            self._log("no isolated cuboids in the working region")
            self._show_analysis()
            if self._shake_retries >= self.config.max_shake_retries:
                self._hand_to_operator()
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
        self._show_analysis()                   # after the choice, so it shows
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
        marked = self._begin_clip()
        self.state = RobotState.PICKUP_SAMPLE
        return self._event("approached", "computed pickup coordinates",
                           n=len(self._world), marked=marked)

    def _begin_clip(self) -> int:
        """Start a lower-camera clip for this batch and box every chosen cuboid.

        Returns how many boxes were placed. A clip with no boxes is not fatal —
        it is a viewing aid — but it is never silent: each way of ending up
        without one says so, because this failing quietly is precisely how the
        box went missing for a whole run of the machine.
        """
        if self._recorder is None:
            return 0
        self._recorder.clear_roi()
        marked = 0
        try:
            marked = self._mark_choice()
        except HomographyError as exc:
            self._log(f"no ROI box on the clip: {exc}")
        self._recorder.start()
        return marked

    def _mark_choice(self) -> int:
        """Chosen cuboids, from upper-camera pixels to boxes on the clip."""
        if self._homography is None:
            raise HomographyError(
                "the profile has no upper-to-lower homography; run the tip "
                "calibration, which fits one from the same two views")

        drift = self._homography.drift_mm(self._gantry)
        if drift > self.config.homography_drift_warn_mm:
            # Not refused: the upper camera has moved with the gantry, so the map
            # is a little stale, and a box slightly off beats no box at all.
            self._log(f"homography was fitted {drift:.1f} mm away from this "
                      f"pose; the ROI boxes are approximate")

        under_px = self._homography.over_to_under(
            self._choice[["cX", "cY"]].values, self._gantry,
            self.camera.resolution, self._under_cam.resolution)

        # The map answers in whole sensor pixels; the clip may be a crop of
        # those. Same origin the recorder's transform cuts at, from `_clip_view`.
        marks = under_px - np.array(self._clip_crop, dtype=float)

        # A box outside the recorded frame is drawn and never seen, which is how
        # the whole thing went unnoticed while the coordinates were in the wrong
        # camera mode. Say it rather than let the clip come back bare.
        w, h = self._clip_size
        outside = int(np.sum((marks[:, 0] < 0) | (marks[:, 0] >= w) |
                             (marks[:, 1] < 0) | (marks[:, 1] >= h)))
        if outside:
            self._log(f"{outside} of {len(marks)} ROI boxes fall outside the "
                      f"{w}x{h} clip frame; check that the homography was "
                      f"fitted on this disc and this lower camera")

        self._recorder.mark_rois(marks)
        return len(under_px)

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
        # the chosen rows are the ones from before the pickup, so on this frame
        # they mark where each cuboid had to have gone from
        self._show_analysis(verify_radius=self.verify_radius_px)

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
            self._hand_to_operator()
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
        """Idle again, for a different reason: stand at the observation pose and
        wait to be told to retry.

        The wait blocks, as idle's does. Returning an event per poll left the
        caller spinning and, worse, never reached `_gate`, so a stop raised here
        was not seen and the run could only be ended by first resuming it.
        """
        self._wait_for_operator(pause, stop)
        # the operator has just had their hands in the dish; whatever was
        # measured to float before that says nothing about it now
        self._cycles_since_floater = self.config.floater_check_interval
        self.state = RobotState.DETECT_FLOATERS
        return self._event("resumed", "operator resumed the run")

    def _state_completed(self, pause, stop) -> PickEvent:
        self._log("picking finished")
        return self._event("completed", "all targets filled")
