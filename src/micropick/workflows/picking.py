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
not seconds before. Every path back into the loop returns there. It measures on
a schedule in seconds rather than in cycles, because a cycle lasts however long
the last transfer took while a floater drifts at a speed that has been measured;
`floater_mode` decides whether the answer is only recorded or also acted on, and
a measurement that cannot be believed produces no exclusion at all.

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
from ..core.vision import floaters
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
# while the floater measurement watches for movement. Everywhere else the stream
# shows travel and says nothing, so it does not run.
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

        if self.pixel_map is None:
            raise PickingError(
                "the session has no pixel map; run the calibration sweep. "
                "Said here because everything below reads a scale off it, and "
                "the failure without this is an AttributeError one frame later")

        # The vision pipeline still takes scalar mm-per-pixel ratios. With the
        # pixel map the scale varies across the frame, so we take the local
        # value at the dish centre as the representative one; it is only used
        # for object sizing and neighbour spacing, not for targeting.
        #
        # Taken at the dish centre and nowhere else, the floater detector
        # included: the map is a degree-3 polynomial, so asked outside the
        # calibrated bounds — at pixel (0, 0), say — it extrapolates and answers
        # with a plausible wrong number rather than refusing.
        cx, cy = self.config.circle_center
        mmpp = float(np.mean(self.pixel_map.mm_per_px(cx, cy)))
        self._one_d_ratio = mmpp
        self._size_ratio = mmpp * mmpp
        self._um_per_px = mmpp * 1000.0          # what `floaters` measures in

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
        # how many boxes the detector returned for the current frame. Kept apart
        # from len(cuboid_df) because contouring and the ROI drop rows before the
        # table exists, and a frame where that happened to everything has to be
        # tellable from one the filters emptied.
        self._boxes_seen = 0
        self._choice: pd.DataFrame | None = None
        self._world: list[tuple[float, float]] = []
        self._shake_retries = 0
        self._empty_pickups = 0
        # Which pass round the picking loop we are on, counted from 1 at the head
        # of the cycle. Only ever increases, and it is what names a frame in the
        # log. Bubbles come from pipetting, so a feature row is only
        # interpretable next to the cycle it was measured in.
        self._cycle = 0
        # (x, y, radius_px) circles that must not be picked from. Filled only by
        # a trusted measurement under `floater_mode == "enforce"`: the moment
        # this list is non-empty, `label_rejections` starts rejecting, which is
        # exactly what `observe` must not do. Every consumer downstream — the
        # rejection labels, the overlays — already reads it.
        self.floater_zones: list[tuple[float, float, float]] = []
        # The last floater measurement, and when it was taken. The schedule is a
        # moment, not a count of cycles: a cycle is however long the last pickup
        # took, while a floater crosses `minimum_distance` in a fixed number of
        # seconds. `None` means one is due now.
        self._last_floater_s: float | None = None
        self._floater_df: pd.DataFrame | None = None
        self._floater_verdict: floaters.Verdict | None = None
        self._floater_baseline: floaters.Baseline | None = None
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
        # Here rather than in DETECT_FLOATERS, so a configuration the session
        # cannot honour is refused before anything moves. Raised from the state
        # instead, the gantry would already be parked at the observation pose
        # with the run apparently under way.
        if self.config.floater_mode != "off":
            from ..config.store import ProfileError
            try:
                self._floater_baseline = self.profile.floater_baseline()
            except ProfileError as exc:      # measured at another window geometry
                raise PickingError(str(exc)) from exc
            if self._floater_baseline is None:
                raise PickingError(
                    f"floater_mode is {self.config.floater_mode!r}, but the "
                    f"profile has no floater baseline. The threshold is an "
                    f"absolute number of microns and means nothing without the "
                    f"noise floor it is multiplied from: measure it on still "
                    f"cuboids first, or set floater_mode to 'off'")

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
    def cycle(self) -> int:
        """Which pass round the picking loop this is, counted from 1."""
        return self._cycle

    @property
    def bubbles(self) -> pd.DataFrame:
        """Detections the bubble filter recognised. Read-only.

        The verdict itself, not `reject_reason == "bubble"`. A bubble that also
        drifts is labelled a floater and one seen with the filter off is not
        rejected at all, but in both cases what has to be visible on the dish is
        that the object was recognised as a bubble.
        """
        df = self.cuboid_df
        if len(df) == 0 or "is_bubble" not in df:
            return pd.DataFrame()
        return df[df.is_bubble]

    @property
    def floater_table(self) -> pd.DataFrame | None:
        """The last floater measurement, scored, or None if none has run.

        One row per measurement window, `TABLE_COLUMNS` plus `is_floater` and
        `state`. Here rather than on the event because `PickEvent.__str__`
        prints its whole `data`, and a table of two hundred objects buries the
        line that says what happened - the same reason the view is not in it.
        The event carries the numbers a person reads; this carries the rest.
        """
        return self._floater_df

    @property
    def floater_verdict(self) -> floaters.Verdict | None:
        """Whether the last measurement is to be believed, and why not. Its
        `__str__` is one line and is what the log records."""
        return self._floater_verdict

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
                   bubbles=self.bubbles,
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
        """Measure the frame: one labelled table, and three views of it.

        The three tables used to be a chain of filtered copies, which threw the
        reason away - an object that dropped out was simply absent, and absent
        for one of six reasons. Now every detection is labelled once and the
        tables are selections on that label, so `cuboid_df` carries the whole
        picture and `pickable` and `isolated` mean exactly what they meant
        before: passed the windows and is not a floater (nor a bubble), and that
        plus enough room from its neighbours.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        roi = vision.roi_mask(gray, self.config, self._one_d_ratio)
        boxes, confs = vision.detect_boxes(self.detector, frame, self.config)
        df = vision.build_cuboid_df(
            gray, boxes, confs, roi_mask=roi,
            pad=self.config.otsu_pad, open_k=self.config.otsu_open_k,
            bubble_core_r=self.config.bubble_core_r,
            bubble_ring_window=self.config.bubble_ring_window,
            bubble_min_area_px=self.config.bubble_min_area_px)
        df = vision.add_derived(df, self._size_ratio, self._one_d_ratio,
                                self.config.circle_center)
        self._boxes_seen = len(boxes)

        if len(df) == 0:
            self.cuboid_df = df
            self.pickable = df
            self.isolated = df
            self._log_detections()
            return

        labels = vision.label_rejections(df, self.config, self.floater_zones)
        for col in labels.columns:
            df[col] = labels[col]
        self.cuboid_df = df
        self.pickable = df[df.reject_reason.isin(("", "crowded"))].copy()
        self.isolated = df[df.reject_reason == ""].copy()
        self._log_detections()

    def _detection_summary(self) -> dict:
        """What this frame showed and why each object was not used.

        Goes on every event that follows a measurement, because the report an
        operator reads is the printed event and nothing else - the status panel
        on the frame is built by the caller, so counts cannot reach it.

        `boxes` and `detected` are both here on purpose. Otsu, the contour test
        and the ROI drop objects before the table exists, so the reasons sum to
        `detected`, never to `boxes`; with only one of the two numbers a frame
        where the contouring failed on everything is indistinguishable from a
        frame the filters emptied. `unmeasured` is the same guard one level down:
        if the features stopped being readable, "no bubbles" would otherwise be
        indistinguishable from "the measurement never ran".
        """
        df = self.cuboid_df
        n_bubbles = unmeasured = 0
        if len(df) and "is_bubble" in df:
            n_bubbles = int(df.is_bubble.sum())
        if len(df) and "core_ratio" in df:
            unmeasured = int(df.core_ratio.isna().sum())
        return {"cycle": self._cycle, "boxes": self._boxes_seen,
                "detected": len(df), "rejected": vision.reject_counts(df),
                "bubbles": n_bubbles, "unmeasured": unmeasured}

    def _log_detections(self) -> None:
        """The bubble features, named by the frame they were measured on.

        Bubbles are made by pipetting, so their share moves through a run; a
        feature row is only interpretable next to which cycle and which frame it
        came from and which well was being filled, and that is what makes "do
        they build up after each aspiration?" answerable afterwards. `self.state`
        is still the state being run at this point, so it names the frame without
        anything having to be passed in.

        One line per object, fields in a fixed order and the identity repeated on
        every line: the logger takes a string, so re-setting the thresholds later
        means parsing this back into a table, and a logger that stamps each call
        would otherwise orphan the rows of a multi-line block.
        """
        if self.logger is None:
            return
        s = self._detection_summary()
        stamp = (f"cycle={self._cycle} state={self.state.value} "
                 f"target={self.routine.current}")
        self._log(f"detections {stamp} boxes={s['boxes']} "
                  f"detected={s['detected']} bubbles={s['bubbles']} "
                  f"unmeasured={s['unmeasured']} rejected={s['rejected']}")

        df = self.cuboid_df
        if len(df) == 0 or "core_ratio" not in df:
            return
        # only the rows that were actually measured: an unreadable object
        # contributes nothing to a re-tune, and `yolo_max_det` allows 600 a frame
        for idx, r in df[df.core_ratio.notna()].iterrows():
            self._log(f"bubble {stamp} idx={idx} area={r.area:.0f} "
                      f"core={r.core_ratio:.4f} spec={r.spec_ratio:.4f} "
                      f"med={r.mask_median:.1f} margin={r.margin:+.4f} "
                      f"is_bubble={int(r.is_bubble)} "
                      f"reject={r.reject_reason or '-'}")

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
        self._boxes_seen = 0
        self._choice = None
        self._show(self._frame)
        self.state = RobotState.ANALYZE_FRAME
        return self._event("captured", "frame taken at the observation pose",
                           cycle=self._cycle)

    def _state_detect_floaters(self, pause, stop) -> PickEvent:
        """Head of the cycle: watch the dish move before deciding from it.

        The order is the point — what floats has to be measured before the frame
        the pickup is decided from, not 2.5 s after it — which is why this is a
        state of its own ahead of `CAPTURE_FRAME` rather than a step inside it.

        `off` costs nothing, not even a frame: the state falls straight through.
        `observe` measures and records without acting, which is how the
        threshold earns trust on a real dish; `enforce` also keeps the flagged
        regions out of the candidates. Both are refused at construction unless
        the profile carries a baseline, so reaching here with a mode set means
        the measurement can run.

        A measurement that cannot be believed produces no zones at all. It is
        the absence of information, not an instruction to discard everything:
        marking the whole dish would empty the candidate table, exhaust the
        shake retries and hang the run, while a floater let through costs one
        empty pickup that `verify_pickup` already catches.
        """
        if self.config.floater_mode == "off":
            return self._enter_capture("off")
        if not self._floaters_due():
            age = time.monotonic() - self._last_floater_s
            return self._enter_capture("last measurement still current",
                                       age_s=round(age, 1),
                                       zones=len(self.floater_zones))

        df, verdict = self._measure_floaters(pause, stop)
        if df is None:
            # A pause split the clip in two. Half a clip is not a short clip:
            # the objects had time to move while nothing was watching, so the
            # frames taken are dropped and the state is entered again. The cycle
            # is deliberately not counted - nothing was decided.
            return self._event("floaters", "clip dropped: paused mid-measurement")

        self._last_floater_s = time.monotonic()
        self._floater_df, self._floater_verdict = df, verdict
        zones = self._floater_zones(df) if verdict.trusted else []
        if self.config.floater_mode == "enforce":
            # Assigned even when empty. The radius of a zone is how far its
            # floater could have travelled by the next measurement, so the ones
            # standing here have just expired; keeping them would be a circle
            # aging silently, which is what `exclusion_zones` is written against.
            self.floater_zones = zones
        # The event first, because it is what counts the cycle: the measurement
        # belongs to the cycle it heads, and that is the number the log line has
        # to carry for the two records to be joinable afterwards.
        event = self._enter_capture(
            "measured" if verdict.trusted else "not trusted",
            objects=verdict.n_objects, floaters=verdict.n_floaters,
            unknown=verdict.n_unknown, threshold_um=round(verdict.threshold_um, 1),
            median_rms_um=round(verdict.median_rms_um, 2),
            trusted=verdict.trusted, zones=len(zones),
            enforced=self.config.floater_mode == "enforce")
        self._log_floaters(verdict, zones)
        return event

    def _enter_capture(self, message: str, **data) -> PickEvent:
        """Leave the head of the cycle for the frame the decision is made from.

        The cycle is counted here, in the one place every path out of this state
        goes through; incrementing it anywhere else would create a second
        definition of what a cycle is.
        """
        self._cycle += 1
        self.state = RobotState.CAPTURE_FRAME
        return self._event("floaters", message, cycle=self._cycle, **data)

    def _floaters_due(self) -> bool:
        """Whether enough time has passed to measure again.

        Elapsed time, not cycles: a cycle lasts however long the last pickup and
        transfer took, while a floater covers ground at a speed that has been
        measured — 76 to 140 um/s, so 15 s is one `minimum_distance`. Anything
        that stirs the dish clears the stamp, which makes a measurement due
        immediately.
        """
        return (self._last_floater_s is None
                or time.monotonic() - self._last_floater_s
                >= self.config.floater_interval_s)

    def _measure_floaters(self, pause, stop):
        """Watch the dish for `floater_window_s` and score what moved.

        Returns (scored table, verdict), or (None, None) if a pause split the
        clip and it was thrown away.

        The detector runs exactly once, on the first frame. The list of objects
        does not change over two seconds, and a second inference would cost more
        than the whole rest of the measurement; the windows it produces are
        fixed and deliberately do not follow their objects, since a floater
        leaving its window is the strongest reading available.

        Frames are never accumulated. Each one is reduced to three numbers per
        window and released, so the cost is set by the number of objects rather
        than by the length of the clip.
        """
        cfg = self.config
        # The gantry arrives here from the shake pose and from the dish as well
        # as from the observation pose, so neither the height nor the position
        # can be assumed. Retract before travelling, or a tip still down is
        # dragged through whatever it was standing in.
        self._gate(pause, stop)
        require_ok(self.robot.retract_axis("leftZ"), "retract")
        self._gate(pause, stop)
        move_to(self.robot, self._observe, min_z_height=cfg.dish_bottom)
        if cfg.capture_settle_s:
            # A gantry that is still ringing moves every window's contents
            # together; `classify` sees that as common motion and refuses the
            # whole measurement, so the wait here buys back a whole clip.
            time.sleep(cfg.capture_settle_s)

        frame0 = self._fresh_frame()
        boxes, confs = vision.detect_boxes(self.detector, frame0, cfg)
        keep = floaters.merge_duplicates(boxes, confs, centre_frac=0.5)
        wins = floaters.make_windows(boxes[keep], frame0.shape,
                                     pad_frac=cfg.floater_pad_frac)
        acc = floaters.MotionAccumulator(wins, warmup=3,
                                         sigma_frac=cfg.floater_sigma_frac)

        if wins:
            period = 1.0 / cfg.floater_fps
            t = time.monotonic()
            acc.update(cv2.cvtColor(frame0, cv2.COLOR_BGR2GRAY), t)
            t_end = t + cfg.floater_window_s
            while time.monotonic() < t_end:
                # Between frames, not only between states: two seconds is long
                # enough that a stop checked at the state boundary would look
                # ignored, and `_gate` is what makes it land here.
                paused = pause is not None and pause.is_set()
                self._gate(pause, stop)
                if paused:
                    return None, None
                # Asking for a frame later than the last one read paces the clip
                # and guarantees each one is new: the same frame counted twice
                # reads as an object that did not move.
                frame = self.camera.read_after(t + period)
                t = time.monotonic()
                acc.update(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), t)

        # No windows is not a special case: the empty table classifies to an
        # empty verdict that says so, and produces no zones.
        return floaters.classify(acc.table(self._um_per_px),
                                 self._floater_baseline,
                                 k=cfg.floater_k, floor_um=cfg.floater_floor_um)

    def _floater_zones(self, df: pd.DataFrame) -> list[tuple[float, float, float]]:
        """The scored table -> circles that must not be picked from.

        Two kinds, and they answer different questions. `exclusion_zones` sizes
        a circle from the speed that floater was seen to have, which covers
        where it will have drifted to by the next measurement. On top of that
        every flagged object gets a circle over its own window: a floater is
        rejected as itself and not only through a zone that its own drift may
        have carried it out of.

        Objects in the `unknown` state get the same small circle. They were not
        measurable, so nothing says they are still - they must stay out of the
        candidates, while remaining out of the floater count, which is what the
        verdict reports. Position is the only link between the frame measured
        here and the frame the pickup is decided from: the two detections are
        independent, so a circle is the only thing that carries over.
        """
        zones = floaters.exclusion_zones(
            df, self._um_per_px, horizon_s=self.config.floater_horizon_s)
        if len(df) == 0:
            return zones
        own = df["is_floater"].to_numpy(bool) | (df["state"] == "unknown").to_numpy(bool)
        for r in df[own].itertuples():
            zones.append((float(r.x), float(r.y),
                          float(min(r.win_w, r.win_h)) / 2.0))
        return zones

    def _cycle_stamp(self) -> str:
        """Which pass round the loop a log line belongs to, repeated on every
        line so a multi-line block can be parsed back into a table. No state
        field, unlike `detections`: these records come from one state only."""
        return f"cycle={self._cycle} target={self.routine.current}"

    def _log_floaters(self, verdict, zones) -> None:
        """One line per measurement."""
        if self.logger is None:
            return
        df = self._floater_df
        self._log(
            f"floaters {self._cycle_stamp()} mode={self.config.floater_mode} "
            f"objects={verdict.n_objects} floaters={verdict.n_floaters} "
            f"unknown={verdict.n_unknown} threshold_um={verdict.threshold_um:.1f} "
            f"median_rms_um={verdict.median_rms_um:.2f} "
            f"common_um={verdict.common_um:.1f} drift={verdict.drift_ratio:.2f} "
            f"trusted={int(verdict.trusted)} frames={df.attrs.get('n_frames', 0)} "
            f"window_s={df.attrs.get('window_s', 0.0):.2f} zones={len(zones)} "
            # last, and quoted: the only free-text field, so a naive split on
            # spaces still recovers every number before it
            f"note={verdict.note or '-'!r}")

    def _log_floater_choice(self) -> None:
        """The rms of every cuboid that went into a pickup.

        Half of a pair: `verify_pickup` records whether the tip came back with
        anything, and these lines say how much each of those cuboids had been
        moving beforehand. Together, over enough runs, they are the curve of rms
        against the chance of an empty pickup, which is what would let the
        threshold be set from measurement rather than from `k` times a floor.

        The two frames are detected independently, so the join is by position:
        the nearest measured window, and how far away it was. A cuboid further
        from any window than that window is wide is reported unmatched rather
        than given someone else's number.
        """
        df = self._floater_df
        if self.logger is None or self._choice is None or df is None or len(df) == 0:
            return
        age = time.monotonic() - self._last_floater_s
        stamp = self._cycle_stamp()
        xy = df[["x", "y"]].to_numpy(dtype=float)
        for idx, r in self._choice.iterrows():
            dist = np.hypot(xy[:, 0] - r.cX, xy[:, 1] - r.cY)
            i = int(np.argmin(dist))
            row = df.iloc[i]
            matched = dist[i] <= min(row["win_w"], row["win_h"]) / 2.0
            rms = float(row["rms_um"]) if matched else float("nan")
            self._log(f"floater_pick {stamp} idx={idx} cX={r.cX:.0f} "
                      f"cY={r.cY:.0f} rms_um={rms:.2f} "
                      f"state={row['state'] if matched else 'unmatched'} "
                      f"dist_px={dist[i]:.0f} age_s={age:.1f}")

    def _state_analyze_frame(self, pause, stop) -> PickEvent:
        # Dropped before the pipeline runs: the frame shown below must carry
        # this cycle's choice or none at all, never the last cycle's cuboids
        # drawn over a dish they have already left.
        self._choice = None
        self._run_pipeline(self._frame)
        # On every exit below, not just the one that picks a batch: the case the
        # counts exist to expose - a filter that discarded everything - leaves
        # through the two that shake or hand back, and those are precisely the
        # ones that used to report nothing.
        summary = self._detection_summary()

        if self.routine.current is None:
            self.routine.next()
        current = self.routine.current
        if current is None:
            self._show_analysis()
            self.state = RobotState.COMPLETED
            return self._event("completed", "routine already satisfied",
                               **summary)

        if len(self.isolated) == 0:
            self._shake_retries += 1
            self._log("no isolated cuboids in the working region")
            self._show_analysis()
            if self._shake_retries >= self.config.max_shake_retries:
                self._hand_to_operator()
                return self._event("needs_operator",
                                   "no isolated cuboids after "
                                   f"{self._shake_retries} shakes",
                                   retries=self._shake_retries, **summary)
            self.state = RobotState.AUTO_SHAKE
            return self._event("no_cuboids", "nothing isolated, will shake",
                               retries=self._shake_retries, **summary)

        self._shake_retries = 0
        remaining = self.routine.remaining(current)
        want = 1 if self.config.one_by_one else min(remaining, self.config.max_batch)
        want = max(1, min(want, len(self.isolated)))
        self._choice = (self.isolated.sample(n=want)
                        if len(self.isolated) > want else self.isolated)
        self._log_floater_choice()
        self._show_analysis()                   # after the choice, so it shows
        self.state = RobotState.APPROACH_TARGET
        return self._event("analyzed", "chose a batch",
                           isolated=len(self.isolated),
                           batch=len(self._choice), **summary)

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
        # Whatever was measured before this described a dish that no longer
        # exists, so the next cycle measures again whatever the interval says.
        self._last_floater_s = None
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
                           attempted=attempted, held=held,
                           missed=attempted - held,
                           **self._detection_summary())

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
        # The operator has had their hands in the dish, as they were asked to,
        # so the same rule as after a shake applies: measure again before
        # deciding anything from it.
        self._last_floater_s = None
        self.state = RobotState.DETECT_FLOATERS
        return self._event("resumed", "operator resumed the run")

    def _state_completed(self, pause, stop) -> PickEvent:
        self._log("picking finished")
        return self._event("completed", "all targets filled")
