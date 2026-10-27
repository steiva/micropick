"""The Tip step (REDESIGN §6.2): put a tip on, then calibrate it.

The picture on the left - the upper camera, the lower one while the
routine measures the tip - and on the right two cards:

**Put a tip on.** A Select of the tip boxes on the deck ("<name> · slot
10"), the rack's wells (a well taken here and not put back gray,
`Session.used_tips`; the one to take ringed, the first free one until
another is clicked) and **Take tip A3**, fixed in width so that "H12" does
not move it. Once one is on: "Tip A3 is on." with Drop in trash and Put
back. With no tip box on the deck, a warning and Back to Deck. Which tip
is on is the robot's answer (`Session.read_tip`), never a note of what
was asked: no pick-up while a tip is on, no drop while there is none,
nothing while it could not be read. These go through the page's jog panel
(`JogPanel.run_job`), as every robot command from a page with one.

**Calibrate the tip**, its steps shown from the start and dimmed until a
tip is on - the robot never moves while the disc is being placed:

1. Start tip calibration: the camera goes to the calibration position
   (the profile's `tip_calib`, set in Robot setup).
2. The disc is there: checked on the picture once the camera is over it.
3. Calibrate: the automatic routine, about 30 s.
4. Fine-tune by hand (when Settings has the manual adjustment on): nudge,
   then Save, or Skip - the tip goes back to where the routine put it.

Then "Tip calibrated." with Check: tip to crosshair, which sends the tip to
the crosshair the routine found, at the module height, with the offset
now saved. With no calibration since the program started (the tip
calibrated in an earlier start, same tip and robot session), the
crosshair is the pixel map's at the middle of the picture from the
calibration position. Liquid's Tip step has no calibration (`set_task`): it is done
once a tip is on.

**The calibration is one run.** Start claims the robot's lease as a run
(STOP and "Calibrating the tip" in the top bar) and one worker goes
through every step: it drives there, waits for Calibrate, measures, waits
for the fine-tune, saves. A wait is an Event the GUI sets; Stop (Esc,
STOP) during one ends the run with nothing saved. Between the waits the
routine is a handful of moves and a few seconds of detection, with no
point to stop at; a second Stop holds the robot.

How the routine measures - frames per reading, the check after the
correction, the manual adjustment, the tip type recorded, the start
offset with no offset in the profile - is set in Settings
(`AppSettings.tip_cal_*`). The cameras are the profile's upper and lower.

**The routine needs a tip on the pipette, and asks the robot.** Every tip
seats differently, which is why this is redone after each pick-up; a
calibration taken with no tip measures nothing and a stored offset from it
would send every pick to the wrong place. The step is done while the tip
it measured is on, in the robot session it was measured in
(`readiness.tip_calibration_status`).

**The manual touch-up is the notebook's `jog_in_window`, in Qt**, with one
difference: its arrows follow the lower camera's picture, which is turned
and mirrored, through `TipTarget.axes` (`JogPanel` `view_axes`) - right on
the screen moves the tip right on the picture. The workflow calls
`manual_touch_up(robot, camera, view)` from its own thread and carries on
when it returns, reading the final pose to compute the offset; Skip moves
the tip back to the pose it was called at, so the offset is the
automatic one. Its jog panel is lent the run's lease (`JogPanel.lend`).

**The routine ends with the Z axis retracted**, as the notebook's cell does
after printing the result. An aborted or failed run leaves the gantry
where it stopped, so what happened can be seen.

**The profile is written by the workflow**, which saves the offset and the
by-product homography together when given the profile: Save, or Skip, is
the operator looking at the tip on the crosshair and saying so.
"""

from __future__ import annotations

import logging
import threading
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ...config.labware import LabwareError, resolve_definition
from ...config.schema import PipetteOffset
from ...core.calibration.pixel_map import PixelMap
from ...hardware.protocols import move_to, xyz
from ...workflows.calibrate_pipette import calibrate_pipette_offset
from ..auto_camera import CameraOpener
from ..readiness import CALIBRATION_POSITION, tip_calibration_status
from ..session import Session
from ..theme import COLORS, SPACING
from ..theme.factory import (card, heading, muted_label, primary_button,
                             scroll_column, secondary_button, select)
from ..tip_detector import STANDIN_NOTE, load_tip_detector
from ..widgets.banner import Banner
from ..widgets.camera_view import CameraView
from ..widgets.card_columns import CardColumns
from ..widgets.checklist import BLOCKED, OK, TODO
from ..widgets.feed_row import FeedRow
from ..widgets.jog_panel import JogPanel
from ..widgets.plate_grid import PlateGrid
from ..widgets.step_card import StepCard, wrapped
from ..widgets.toast import show_toast
from ..workers import Worker

__all__ = ["PipetteCalibration", "POSITION_NAME", "TouchUpAborted"]

TITLE = "Tip"

log = logging.getLogger(__name__)

PANEL_WIDTH = 420

# The profile position the calibration starts at: Robot setup's
# calibration position. The notebook's name, kept: a profile taught from
# the notebook works here and the other way round.
POSITION_NAME = CALIBRATION_POSITION

# The jog step for the touch-up, as the notebook sets it.
TOUCH_UP_STEP_MM = 0.05

NO_RACK = "There is no tip box on the deck. Add it on the Deck step."
CALIBRATED = "Tip calibrated."
NOTHING_SAVED = "Nothing was saved."
FIRST_A_TIP = "Put a tip on first."

# Where a run is: driving to the calibration position, waiting for the
# disc to be checked and Calibrate, measuring, waiting for the fine-tune.
MOVING, DISC, MEASURING, TUNING = "moving", "disc", "measuring", "tuning"


class TouchUpAborted(RuntimeError):
    """The operator stopped the calibration while it waited for them."""


class PipetteCalibration(QWidget):
    # Emitted from the worker thread when the routine waits for the
    # operator: at the disc, and for the fine-tune. Signals, so that the
    # slot runs on the GUI thread whatever thread asked.
    disc_requested = Signal()
    touch_up_requested = Signal()

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._worker: Worker | None = None
        self._phase: str | None = None
        self._disc_seen = False
        self._gate = threading.Event()
        self._aborted = False
        self._skip = False
        self._last_line = ""
        # How the last run ended, shown under Start until the next one.
        self._outcome = ""
        # The crosshair the last calibration found, robot XY: where Check
        # sends the tip (else `_crosshair_xy`'s guess).
        self._crosshair: tuple[float, float] | None = None
        # The lower camera after a check, until the next refresh that
        # changes something: the tip is under it.
        self._looking_at_tip = False
        self._task: str | None = None
        # The well to take, by rack slot: the first free one until another
        # is clicked.
        self._chosen: dict[str, str] = {}
        self.opener = CameraOpener(session, self)

        self.view = CameraView(self)
        self.jog = JogPanel(self.session, shortcut_host=self,
                            machine_controls=False,
                            parent=self)
        self.jog.show_position_on(self.view)
        panel = CardColumns([self._tip_card(), self._calibration_card(),
                             self.jog], self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2,
                                  SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addWidget(FeedRow(self.view, scroll_column(panel, PANEL_WIDTH)),
                         1)

        self.disc_requested.connect(self._at_disc)
        self.touch_up_requested.connect(self._begin_touch_up)
        session.camera_opened.connect(lambda _l: self._refresh())
        session.camera_closed.connect(lambda _l: self._refresh())
        session.robot_state_changed.connect(lambda _s: self._refresh())
        session.robot_busy_changed.connect(lambda _h: self._refresh())
        session.profile_changed.connect(lambda _p: self._refresh())
        session.settings_changed.connect(lambda _s: self._refresh())
        session.labware_changed.connect(self._refresh)
        # The window's Stop: a wait for the operator ends the run with
        # nothing saved. A second Stop halts the robot.
        session.stop_requested.connect(self._on_stop)
        session.tip_changed.connect(lambda _t: self._refresh())
        self._refresh()

    # -- construction --------------------------------------------------------

    def _tip_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Put a tip on", 2))
        self.no_rack = Banner(NO_RACK, tone="warn", parent=box)
        self.back_to_deck = self.no_rack.add_action("Back to Deck")
        self.back_to_deck.clicked.connect(
            lambda: self.session.page_requested.emit("labware"))
        box.layout().addWidget(self.no_rack)

        self.racks = select(box)
        # The keys over this page are the gantry's.
        self.racks.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.racks.currentIndexChanged.connect(lambda _i: self._refresh())
        box.layout().addWidget(self.racks)
        self.rack_hint = muted_label("Click the tip to take.", box)
        box.layout().addWidget(self.rack_hint)
        self.rack_grid = PlateGrid(mini=True, parent=box)
        self.rack_grid.well_clicked.connect(self._choose_tip)
        box.layout().addWidget(self.rack_grid)
        self.take_button = primary_button("Take tip", box)
        metrics = self.take_button.fontMetrics()
        self.take_button.setFixedWidth(
            metrics.horizontalAdvance("Take tip H12") + 6 * SPACING)
        self.take_button.clicked.connect(self._take)
        box.layout().addWidget(self.take_button)

        self.tip_state = wrapped()
        box.layout().addWidget(self.tip_state)
        row = QHBoxLayout()
        self.drop_button = secondary_button("Drop in trash", box)
        self.drop_button.clicked.connect(self._drop_in_trash)
        self.put_back_button = secondary_button("Put back", box)
        self.put_back_button.clicked.connect(self._put_back)
        row.addWidget(self.drop_button)
        row.addWidget(self.put_back_button)
        row.addStretch(1)
        box.layout().addLayout(row)
        self.tip_note = muted_label("", box)
        self.tip_note.setWordWrap(True)
        box.layout().addWidget(self.tip_note)
        return box

    def _calibration_card(self) -> QWidget:
        """The four steps under one heading, in one widget: dimmed as one
        until a tip is on, hidden as one on Liquid's Tip step."""
        self.calibration_box = QWidget(self)
        column = QVBoxLayout(self.calibration_box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        column.addWidget(heading("Calibrate the tip", 2))

        self.start_step = StepCard(self, 1, "Start")
        self.start_step.add(wrapped(
            "The camera goes to the calibration position."))
        self.start_button = primary_button("Start tip calibration", self)
        self.start_button.clicked.connect(self._start)
        self.start_step.add(self.start_button)
        column.addWidget(self.start_step.box)

        self.disc_step = StepCard(self, 2, "Disc on the calibration module")
        self.disc_step.add(wrapped(
            "Check on the picture that the crosshair disc is there. If not, "
            "put it on now."))
        self.disc_button = secondary_button("The disc is there", self)
        self.disc_button.clicked.connect(self._disc_there)
        self.disc_step.add(self.disc_button)
        column.addWidget(self.disc_step.box)

        self.calibrate_step = StepCard(self, 3, "Calibrate")
        self.calibrate_step.add(wrapped(
            "The robot finds the tip under the camera by itself. About "
            "30 s."))
        self.calibrate_button = primary_button("Calibrate", self)
        self.calibrate_button.clicked.connect(self._calibrate)
        self.calibrate_step.add(self.calibrate_button)
        column.addWidget(self.calibrate_step.box)

        self.tune_step = StepCard(self, 4, "Fine-tune by hand")
        self.tune_text = wrapped()
        self.tune_step.add(self.tune_text)
        # XY only: a Z step here pushes the tip into the disc.
        self.touch_jog = JogPanel(self.session, shortcut_host=self,
                                  machine_controls=False,
                                  xy_only=True,
                                  parent=self.tune_step.box)
        self.touch_jog.show_position_on(self.view)
        self.touch_jog.hide()
        self.tune_step.add(self.touch_jog)
        row = QHBoxLayout()
        self.save_button = primary_button("Save", self)
        self.save_button.clicked.connect(self._accept)
        self.skip_button = secondary_button("Skip", self)
        self.skip_button.clicked.connect(self._skip_tune)
        row.addWidget(self.save_button)
        row.addWidget(self.skip_button)
        row.addStretch(1)
        self.tune_step.add_layout(row)
        column.addWidget(self.tune_step.box)

        # What stops Start, what the run is doing, or how it ended.
        self.checks = muted_label("", self)
        self.checks.setWordWrap(True)
        column.addWidget(self.checks)
        self.calibrated = Banner(CALIBRATED, tone="ok", parent=self)
        self.check_button = self.calibrated.add_action(
            "Check: tip to crosshair")
        self.check_button.clicked.connect(self._check)
        column.addWidget(self.calibrated)
        return self.calibration_box

    def set_task(self, task: str | None) -> None:
        """The task the step is shown in: Liquid's has no calibration."""
        self._task = task
        self._refresh()

    # -- the tip -------------------------------------------------------------

    def _racks(self) -> list[tuple[str, object]]:
        """(slot, definition) of every tip box the robot session holds,
        by slot number."""
        state = self.session.run_state
        if self.session.robot is None or state is None:
            return []
        out = []
        for slot, entry in state.labware.items():
            try:
                definition = resolve_definition(entry.load_name)
            except LabwareError:
                continue
            if definition.is_tiprack:
                out.append((slot, definition))
        return sorted(out, key=lambda r: int(r[0]) if r[0].isdigit() else 99)

    def _rack(self) -> tuple[str, object] | None:
        slot = self.racks.currentData()
        return next((r for r in self._racks() if r[0] == slot), None)

    def _free_wells(self, slot: str, definition) -> list[str]:
        used = self.session.used_tips(slot)
        return [w for column in definition.ordering for w in column
                if w not in used]

    def _well_to_take(self) -> str | None:
        rack = self._rack()
        if rack is None:
            return None
        slot, definition = rack
        chosen = self._chosen.get(slot)
        free = self._free_wells(slot, definition)
        if chosen in free:
            return chosen
        return free[0] if free else None

    def _choose_tip(self, well: str) -> None:
        rack = self._rack()
        if rack is not None:
            self._chosen[rack[0]] = well
        self._refresh()

    def _take(self) -> None:
        rack, well = self._rack(), self._well_to_take()
        if rack is None or well is None or self._running():
            return
        slot, session = rack[0], self.session
        self.tip_note.setText("")

        def job(_log):
            session.pick_up_tip(slot, well)

        self._tip_job(job, f"picking up a tip from slot {slot} {well}",
                      f"Tip {well} is on.")

    def _drop_in_trash(self) -> None:
        self._tip_job(lambda _log: self.session.drop_tip_in_trash(),
                      "dropping the tip in the trash")

    def _put_back(self) -> None:
        self._tip_job(lambda _log: self.session.return_tip(),
                      "putting the tip back")

    def _tip_job(self, job, what: str, done: str = "") -> None:
        if self._running():
            return

        def then(_result):
            if done:
                show_toast(self, done)
            self._refresh()

        if not self.jog.run_job(job, what=what, then=then):
            self.tip_note.setText(self.session.robot_lease.refusal
                                  if self.session.robot_lease.busy
                                  else "Robot busy.")
        self._refresh()

    # -- the calibration -----------------------------------------------------

    def _stored_position(self):
        profile = self.session.profile
        if profile is None:
            return None
        return profile.positions.get(POSITION_NAME)

    def _crosshair_xy(self) -> tuple[float, float] | None:
        """Where the crosshair is, robot XY: the last calibration's, else
        the middle of the picture from the calibration position through the
        pixel map - the camera is set over the disc there."""
        if self._crosshair is not None:
            return self._crosshair
        profile, stored = self.session.profile, self._stored_position()
        if profile is None or profile.pixel_map is None or stored is None:
            return None
        pmap = PixelMap(profile.pixel_map)
        x, y = pmap.to_robot(pmap.config.cu, pmap.config.cv, stored[:2])
        return float(x), float(y)

    def _start_offset(self) -> tuple[float, float]:
        """Where the routine first looks for the tip: the profile's offset,
        else the start offset in Settings."""
        profile = self.session.profile
        offset = profile.calibration.pipette_offset if profile else None
        if offset is not None:
            return offset.dx, offset.dy
        return tuple(self.session.settings.tip_cal_start_offset)

    def _readiness(self) -> list[str]:
        """What stops the routine from starting, as sentences. The step's
        own needs - the robot, the camera calibration, the calibration
        position - are the step banner's; these are what is left."""
        session, problems = self.session, []
        profile = session.profile
        if session.robot is None:
            problems.append("The robot is not connected.")
        if profile is None:
            problems.append("No profile loaded.")
        elif profile.pixel_map is None:
            problems.append("The camera is not calibrated.")
        if profile is not None and self._stored_position() is None:
            problems.append("The calibration position is not set.")
        if session.robot is not None and session.tip.attached is not True:
            problems.append(FIRST_A_TIP)
        over, under = self._over_camera(), self._under_camera()
        if over is None:
            problems.append("The upper camera is not open yet.")
        if under is None:
            problems.append("The lower camera is not open yet.")
        else:
            mode = self._calibration_mode(self.session.lower_camera_label)
            if mode is not None and tuple(under.resolution) != mode:
                problems.append(f"The lower camera is opening at {mode[0]}x"
                                f"{mode[1]}.")
        if over is not None and over is under:
            problems.append("The upper and lower camera are the same camera.")
        if self._start_offset() == (0.0, 0.0):
            problems.append("The start offset in Settings is not set.")
        return problems

    def _tip_type(self) -> str | None:
        """What the offset is recorded against: the tip type set in Settings,
        else the rack the tip came from, else the last offset's."""
        typed = self.session.settings.tip_cal_tip_type.strip()
        if typed:
            return typed
        tip, state = self.session.tip, self.session.run_state
        if tip.returnable and state is not None:
            entry = state.labware.get(tip.slot)
            if entry is not None:
                return entry.load_name
        profile = self.session.profile
        offset = profile.calibration.pipette_offset if profile else None
        return offset.tip_type if offset is not None else None

    def _start(self) -> None:
        if self._running():
            return
        problems = self._readiness()
        if problems:
            self._outcome = problems[0]
            self._refresh()
            return
        session = self.session
        settings = session.settings
        robot, profile = session.robot, session.profile
        motion = session.motion
        over, under = self._over_camera(), self._under_camera()
        target = profile.calibration.tip_target
        pmap = PixelMap.from_config(profile.pixel_map)
        stored = self._stored_position()
        current = self._start_offset()
        frames, verify = settings.tip_cal_frames, settings.tip_cal_verify
        tip_type = self._tip_type()
        touch_up = self._touch_up if settings.tip_cal_touch_up else None
        mock = session.mock
        device = settings.inference_device
        # Which tip is measured: the offset holds for it alone
        # (`readiness.tip_calibration_status`).
        robot_session = (session.run_state.run_id
                         if session.run_state is not None else None)
        tip_pickup = session.tip.pickup
        wait = self._wait

        def job(log):
            log("loading the tip detector")
            detector = load_tip_detector(profile, mock=mock, robot=robot,
                                         device=device)
            standin = bool(getattr(detector, "is_standin", False))
            if standin:
                log(STANDIN_NOTE)
            log(f"driving to {POSITION_NAME} "
                f"{tuple(round(v, 2) for v in stored)}")
            motion.tip_to(stored[:2], stored[2], log=log)
            wait(self.disc_requested, "waiting for you: check the disc, "
                 "then Calibrate")
            time.sleep(0.5)
            log(f"starting from ({current[0]:+.3f}, {current[1]:+.3f}) mm, "
                f"{frames} frames per reading"
                + (f", tip {tip_type}" if tip_type else ""))
            try:
                result = calibrate_pipette_offset(
                    robot, over, under, detector, pmap, target=target,
                    current_offset=current, frames=frames, verify=verify,
                    tip_type=tip_type, profile=profile,
                    manual_touch_up=touch_up, robot_session=robot_session,
                    tip_pickup=tip_pickup, log=log)
            finally:
                # It switches the lights on when it finds them off.
                session.refresh_lights()
            # The tip is on the crosshair, offset by what was measured.
            fx, fy, _fz = xyz(robot)
            offset = result.offset
            crosshair = (fx - offset.dx, fy - offset.dy)
            # As the notebook does once the offset is saved: the next thing
            # anyone does is drive somewhere else.
            log("retracting leftZ")
            robot.retract_axis("leftZ", verbose=False)
            return result, standin, crosshair

        # The robot for the whole calibration, the waits included; the
        # touch-up's panel is lent it (`_begin_touch_up`).
        lease = session.robot_lease.claim("pipette calibration", run=True)
        if lease is None:
            self._outcome = session.robot_lease.refusal
            self._refresh()
            return
        self._outcome = self._last_line = ""
        self._disc_seen = False
        self._looking_at_tip = False
        self._phase = MOVING
        worker = Worker(job, what="calibrating the pipette", lease=lease)
        self._worker = worker
        worker.message.connect(self._on_message)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        log.info("pipette calibration: started")
        worker.start()
        self._refresh()

    # A wait, from the worker's side: ask, then wait for the GUI.
    def _wait(self, signal, line: str) -> None:
        self._gate.clear()
        self._aborted = False
        worker = self._worker
        if worker is not None:
            # The activity line, and the log: the calibration is not
            # working now, it is waiting for the operator.
            worker._on_message(line)
        signal.emit()
        self._gate.wait()
        if self._aborted:
            raise TouchUpAborted("stopped while waiting for you")

    def _touch_up(self, robot, camera, view) -> None:
        start = xyz(robot)
        self._skip = False
        self._wait(self.touch_up_requested, "waiting for you: nudge the tip "
                   "onto the crosshair, then Save or Skip")
        if self._skip:
            now = xyz(robot)
            if any(abs(a - b) > 1e-3 for a, b in zip(now, start)):
                move_to(robot, start, force_direct=True)

    # ...and from the GUI's side.
    def _at_disc(self) -> None:
        self._phase = DISC
        self._refresh()

    def _disc_there(self) -> None:
        if self._phase == DISC:
            self._disc_seen = True
            self._refresh()

    def _calibrate(self) -> None:
        if self._phase == DISC and self._disc_seen:
            self._phase = MEASURING
            self._gate.set()
            self._refresh()

    def _begin_touch_up(self) -> None:
        # The lower camera is turned and mirrored: through TipTarget.axes the
        # arrows move the tip the way they point on its picture.
        profile = self.session.profile
        if profile is not None:
            self.touch_jog.set_view_axes(profile.calibration.tip_target.axes)
        self.touch_jog.set_step(TOUCH_UP_STEP_MM)
        # The calibration holds the robot and waits; its steps are taken
        # under that lease.
        lease = getattr(self._worker, "lease", None)
        if lease is not None:
            self.touch_jog.lend(lease)
        self._phase = TUNING
        self.touch_jog.show()
        self._refresh()

    def _end_touch_up(self) -> bool:
        """The fine-tune's panel let go; False while a step still moves:
        the step took no lease of its own, and the calibration's ends
        after this - the robot would be free while the step still moves."""
        if self.touch_jog.busy:
            self.touch_jog.tell("A step is still moving.")
            return False
        self.touch_jog.hide()
        self.touch_jog.end_loan()
        return True

    def _accept(self) -> None:
        if self._phase == TUNING and self._end_touch_up():
            self._phase = MEASURING
            self._gate.set()
            self._refresh()

    def _skip_tune(self) -> None:
        if self._phase == TUNING and self._end_touch_up():
            self._skip = True
            self._phase = MEASURING
            self._gate.set()
            self._refresh()

    def _on_stop(self) -> None:
        if self._phase in (DISC, TUNING):
            self._abort()

    def _abort(self) -> None:
        if not self._end_touch_up():
            return
        self._aborted = True
        self._gate.set()
        self._refresh()

    def _on_message(self, text: str) -> None:
        log.info("pipette calibration: %s", text)
        self._last_line = text
        self._refresh()

    def _on_finished(self, payload) -> None:
        self._worker = None
        self._phase = None
        self.touch_jog.end_loan()
        self.touch_jog.hide()
        result, standin, crosshair = payload
        offset: PipetteOffset = result.offset
        self._crosshair = crosshair
        self._outcome = STANDIN_NOTE if standin else ""
        log.info("pipette offset calibrated: dx %+.3f dy %+.3f mm (%s)",
                 offset.dx, offset.dy, offset.method)
        profile = self.session.profile
        if profile is not None:
            log.info("pipette calibration: saved to %s\n%s",
                     profile.path / "calibration.json", result)
            self.session.profile_changed.emit(profile)
        show_toast(self, CALIBRATED)
        self._refresh()

    def _on_failed(self, reason: str) -> None:
        self._worker = None
        self._phase = None
        self.touch_jog.end_loan()
        self.touch_jog.hide()
        error = getattr(self.sender(), "error", None)
        if isinstance(error, TouchUpAborted):
            log.info("pipette calibration stopped: %s", reason)
            self._outcome = f"Calibration stopped. {NOTHING_SAVED}"
        else:
            log.error("pipette calibration failed: %s", reason)
            self._outcome = f"Calibration failed: {reason} {NOTHING_SAVED}"
        self._refresh()

    def _check(self) -> None:
        """The tip to the crosshair the calibration found, at the module
        height, through the offset now saved."""
        profile = self.session.profile
        offset = profile.calibration.pipette_offset if profile else None
        crosshair = self._crosshair_xy()
        if crosshair is None or offset is None or self._running():
            return
        x = crosshair[0] + offset.dx
        y = crosshair[1] + offset.dy
        z = float(profile.calibration.tip_target.module_height)
        motion = self.session.motion
        self._looking_at_tip = True

        def job(log):
            motion.tip_to((x, y), z, log=log)

        if not self.jog.run_job(job, what=f"tip to the crosshair "
                                f"({x:.2f}, {y:.2f}) at z {z:g}"):
            self._looking_at_tip = False
        self._refresh()

    # -- display -------------------------------------------------------------

    def _running(self) -> bool:
        return self._worker is not None and self._worker.running

    def run_activity(self) -> str | None:
        """For the top bar's pill: "waiting" while the run waits for the
        operator - at the disc, and for the fine-tune."""
        return "waiting" if self._phase in (DISC, TUNING) else None

    def _refresh(self, *_args) -> None:
        self.session.run_activity_changed.emit()
        running = self._phase is not None
        # The page's jog panel only while nothing runs: hidden, its keys are
        # given up, and the touch-up's panel is the only thing that moves the
        # robot during a calibration.
        self.jog.setVisible(not running)
        self._show_tip(running)
        self._show_calibration(running)
        # The upper camera finds the disc; the lower one sees the tip.
        lower = self._phase in (MEASURING, TUNING) or self._looking_at_tip
        self.view.set_camera(self._under_camera() if lower
                             else self._over_camera())

    def _show_tip(self, running: bool) -> None:
        session = self.session
        connected = session.robot is not None
        tip = session.tip
        racks = self._racks()
        current = self.racks.currentData()
        wanted = [(f"{d.display_name} · slot {slot}", slot)
                  for slot, d in racks]
        shown = [(self.racks.itemText(i), self.racks.itemData(i))
                 for i in range(self.racks.count())]
        if wanted != shown:
            self.racks.blockSignals(True)
            self.racks.clear()
            for text, slot in wanted:
                self.racks.addItem(text, slot)
            index = self.racks.findData(current)
            self.racks.setCurrentIndex(max(index, 0) if wanted else -1)
            self.racks.blockSignals(False)

        on = tip.attached is True
        offering = connected and not on and bool(racks)
        self.no_rack.setVisible(connected and not on and not racks)
        for widget in (self.racks, self.rack_hint, self.rack_grid,
                       self.take_button):
            widget.setVisible(offering)
        rack = self._rack()
        if rack is not None:
            slot, definition = rack
            if self.rack_grid.wells != [w for c in definition.ordering
                                        for w in c]:
                self.rack_grid.set_ordering(definition.ordering)
            used = session.used_tips(slot)
            self.rack_grid.set_fills(
                {w: COLORS["faint"] if w in used else COLORS["tips"]
                 for c in definition.ordering for w in c})
        well = self._well_to_take()
        self.rack_grid.set_current(well)
        self.take_button.setText(f"Take tip {well}" if well else "Take tip")
        free = not session.robot_lease.busy and not running
        known = connected and free and tip.attached is not None
        self.take_button.setEnabled(known and not on and well is not None)

        if not connected:
            self.tip_state.setText("")
        elif tip.attached is None:
            self.tip_state.setText("Tip state unknown.")
        elif on:
            self.tip_state.setText(f"Tip {tip.well} is on." if tip.returnable
                                   else "A tip is on.")
        else:
            self.tip_state.setText("")
        self.tip_state.setVisible(bool(self.tip_state.text()))
        self.drop_button.setVisible(on)
        self.put_back_button.setVisible(on)
        self.drop_button.setEnabled(known and on)
        self.put_back_button.setEnabled(known and tip.returnable)
        self.tip_note.setVisible(bool(self.tip_note.text()))

    def _show_calibration(self, running: bool) -> None:
        session = self.session
        self.calibration_box.setVisible(self._task != "liquid")
        on = session.tip.attached is True
        # Dimmed until a tip is on; never while its own run goes on.
        self.calibration_box.setEnabled(on or running)
        measured, why, _stale = tip_calibration_status(session)
        problems = [] if running else self._readiness()
        phase = self._phase

        if not on and not running:
            self.start_step.set_state(BLOCKED, FIRST_A_TIP)
        else:
            self.start_step.set_state(OK if running and phase != MOVING
                                      or measured and not running else TODO)
        self.start_button.setEnabled(not running and not problems
                                     and not session.robot_lease.busy)

        self.disc_step.set_state(OK if self._disc_seen and running
                                 or measured and not running else TODO)
        self.disc_button.setEnabled(phase == DISC and not self._disc_seen)

        self.calibrate_step.set_state(OK if measured and not running
                                      else TODO)
        self.calibrate_button.setEnabled(phase == DISC and self._disc_seen)

        tuning = phase == TUNING
        if session.settings.tip_cal_touch_up:
            self.tune_text.setText(
                f"Nudge the tip onto the central crosshair "
                f"({TOUCH_UP_STEP_MM:g} mm steps), then Save or Skip.")
        else:
            self.tune_text.setText("Off in Settings.")
        self.tune_step.set_state(OK if measured and not running else TODO)
        self.save_button.setEnabled(tuning)
        self.skip_button.setEnabled(tuning)

        if running:
            self.checks.setText(f"Running: {self._last_line}"
                                if self._last_line else "Running.")
        else:
            lines = [self._outcome] if self._outcome else []
            if problems and on:
                lines.append(problems[0])
            elif not measured and on and not self._outcome:
                lines.append(why)
            self.checks.setText("\n".join(lines))
        self.checks.setVisible(bool(self.checks.text()))
        self.calibrated.setVisible(measured and not running)
        self.check_button.setEnabled(
            measured and not running and self._crosshair_xy() is not None
            and not session.robot_lease.busy)
        if not running and not self.jog.busy:
            self._looking_at_tip = self._looking_at_tip and measured

    # -- cameras -------------------------------------------------------------

    def _over_camera(self):
        label = self.session.upper_camera_label
        return self.session.camera(label) if label else None

    def _under_camera(self):
        label = self.session.lower_camera_label
        return self.session.camera(label) if label else None

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Both: the upper camera finds the disc and the lower one measures
        # the tip, and the routine refuses to start without either.
        # The lower one in the profile's mode, which is what the routine
        # checks: the Picking page's clips may have left it in a faster one.
        self.opener.ensure(self.session.upper_camera_label)
        lower = self.session.lower_camera_label
        self.opener.ensure(lower, self._calibration_mode(lower))
        self._refresh()

    def _calibration_mode(self, label: str | None):
        """The mode the routine measures the lower camera in: its profile
        default (`calibrate_pipette._expected_under_resolution`)."""
        profile = self.session.profile
        spec = profile.cameras.get(label) if profile and label else None
        return (tuple(int(v) for v in spec.default_resolution)
                if spec is not None else None)
