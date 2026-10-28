"""The Tip step (REDESIGN §6.2): put a tip on, then calibrate it.

The two cards are blocks another page or sheet embeds (`TipOnBlock`,
`TipCalibrationBlock`: each a `box` for the host's layout, run through the
host's jog panel and picture, `done` and `changed` to read where they
are); the page is the picture, the two blocks and its jog panel.

The picture on the left - the upper camera, the lower one while the
routine measures the tip - and on the right one column of two cards, read
top to bottom:

**Put a tip on.** A Select of the tip boxes on the deck ("<name> · slot
10"), the rack's wells (a well taken here and not put back gray,
`Session.used_tips`; the one to take ringed, the first free one until
another is clicked) and **Take tip A3**, fixed in width so that "H12" does
not move it. Once one is on: "Tip A3 is on." with Drop in trash and
Drop in place (the robot's `dropTipInPlace` where the pipette is, nothing
moved). No Put back: the robot's return to the rack did not work. With
no tip box on the deck, a warning and Back to Deck. Which tip is on is the robot's answer (`Session.read_tip`), never a note of what
was asked: no pick-up while a tip is on, no drop while there is none,
nothing while it could not be read. These go through the page's jog panel
(`JogPanel.run_job`), as every robot command from a page with one.

**Calibrate the tip**, its steps shown from the start and dimmed until a
tip is on - the robot never moves while the disc is being placed:

1. Start tip calibration: the camera goes to the calibration position
   (the profile's `tip_calib`, set in Robot setup).
2. The disc is there: checked on the picture once the camera is over it.
3. Calibrate: the automatic routine, about 30 s, a spinner while it runs.
   Its result is saved as it ends.

Then a green "Tip calibrated." with Check: tip to crosshair, which sends
the tip to the crosshair the routine found, at the module height, with
the offset now saved, and Redo calibration, which runs it again. With no
calibration since the program started (the tip calibrated in an earlier
start, same tip and robot session), the crosshair is the pixel map's at
the middle of the picture from the calibration position. Liquid's Tip
step has no calibration (`set_task`): it is done once a tip is on.

Under it, folded and optional (when Settings has the manual adjustment
on), **Fine-tune by hand**: the lower camera and the pad, XY only, while
the tip is over the crosshair - else "Check: tip to crosshair" first. Its
Save is gray until a step was taken, and stores the offset the tip is at
now (`method` "auto+manual") as a short job, not a run.

**The calibration is one run.** Start claims the robot's lease as a run
(STOP and "Calibrating the tip" in the top bar) and one worker goes
through every step: it drives there, waits for Calibrate, measures,
saves. The wait is an Event the GUI sets; Stop (Esc, STOP) during it ends
the run with nothing saved. After it the routine is a handful of moves
and a few seconds of detection, with no point to stop at; a second Stop
holds the robot.

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

**The fine-tune is the notebook's `jog_in_window`, in Qt**, with one
difference: its arrows follow the lower camera's picture, which is turned
and mirrored, through `TipTarget.axes` (`JogPanel` `view_axes`) - right on
the screen moves the tip right on the picture. The offset is where the
tip is less the crosshair, as the routine reads it from its final pose.
"Over the crosshair" is the pose within `TUNE_RANGE_MM` of the crosshair
plus the saved offset, at the module height: the pose says it, whatever
moved the tip since.

**The routine ends with the tip over the crosshair**, so the fine-tune
can start at once; the next move across the deck raises it
(`Motion.tip_to`). An aborted or failed run leaves the gantry where it
stopped, so what happened can be seen.

**The profile is written by the workflow**, which saves the offset and the
by-product homography together when given the profile.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime, timezone

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QHBoxLayout, QProgressBar, QVBoxLayout,
                               QWidget)

from ...config.labware import LabwareError, resolve_definition
from ...config.schema import PipetteOffset
from ...core.calibration.pixel_map import PixelMap
from ...hardware.protocols import xyz
from ...workflows.calibrate_pipette import calibrate_pipette_offset
from ..auto_camera import CameraOpener
from ..readiness import CALIBRATION_POSITION, tip_calibration_status
from ..session import Session
from ..theme import COLORS, SPACING
from ..theme.factory import (Section, card, heading, muted_label,
                             primary_button, scroll_column, secondary_button,
                             select)
from ..tip_detector import STANDIN_NOTE, load_tip_detector
from ..widgets.banner import Banner
from ..widgets.camera_view import CameraView
from ..widgets.checklist import BLOCKED, OK, TODO
from ..widgets.feed_row import FeedRow
from ..widgets.jog_panel import JogPanel
from ..widgets.plate_grid import PlateGrid
from ..widgets.step_card import StepCard, wrapped
from ..widgets.toast import show_toast
from ..workers import Worker

__all__ = ["PipetteCalibration", "POSITION_NAME", "RUN_HOLDER",
           "TipCalibrationBlock", "TipOnBlock", "TouchUpAborted"]

TITLE = "Tip"

log = logging.getLogger(__name__)

PANEL_WIDTH = 420

# The profile position the calibration starts at: Robot setup's
# calibration position. The notebook's name, kept: a profile taught from
# the notebook works here and the other way round.
POSITION_NAME = CALIBRATION_POSITION

# The jog step for the fine-tune, as the notebook sets it.
TOUCH_UP_STEP_MM = 0.05
# The tip is over the crosshair while this close to it, XY and Z: a
# fine-tune is a few steps of `TOUCH_UP_STEP_MM`.
TUNE_RANGE_MM = 1.0

NO_RACK = "There is no tip box on the deck. Add it on the Deck step."
CALIBRATED = "Tip calibrated."
NOTHING_SAVED = "Nothing was saved."
FIRST_A_TIP = "Put a tip on first."

# What the calibration run holds the robot for (`shell.RUNS`): a host
# with a pill of its own gives the block another name.
RUN_HOLDER = "pipette calibration"

# Where a run is: driving to the calibration position, waiting for the
# disc to be checked and Calibrate, measuring.
MOVING, DISC, MEASURING = "moving", "disc", "measuring"


class TouchUpAborted(RuntimeError):
    """The operator stopped the calibration while it waited for them."""


class TipOnBlock(QObject):
    """The "Put a tip on" card, for any page or sheet with a session and a
    jog panel: `box` goes into the host's layout.

    Its pick-up and drops go through the host's `jog`
    (`JogPanel.run_job`), as every robot command from a page with one.
    With `calibration`, nothing here changes the tip while that one runs.
    `done` is a tip on (the robot's answer); `changed` is emitted after
    every refresh, so a host re-reads it.
    """

    changed = Signal()

    def __init__(self, session: Session, jog: JogPanel, *,
                 calibration: TipCalibrationBlock | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.jog = jog
        self.calibration = calibration
        # The well to take, by rack slot: the first free one until another
        # is clicked.
        self._chosen: dict[str, str] = {}
        self.box = self._build(parent)
        session.robot_state_changed.connect(lambda _s: self.refresh())
        session.robot_busy_changed.connect(lambda _h: self.refresh())
        session.labware_changed.connect(self.refresh)
        session.tip_changed.connect(lambda _t: self.refresh())
        if calibration is not None:
            calibration.changed.connect(self.refresh)
        self.refresh()

    def _build(self, parent) -> QWidget:
        box = card(parent)
        # A host whose own step card names it hides this one.
        self.heading = heading("Put a tip on", 2)
        box.layout().addWidget(self.heading)
        self.no_rack = Banner(NO_RACK, tone="warn", parent=box)
        self.back_to_deck = self.no_rack.add_action("Back to Deck")
        self.back_to_deck.clicked.connect(
            lambda: self.session.page_requested.emit("labware"))
        box.layout().addWidget(self.no_rack)

        self.racks = select(box)
        # The keys over this page are the gantry's.
        self.racks.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.racks.currentIndexChanged.connect(lambda _i: self.refresh())
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
        self.drop_here_button = secondary_button("Drop in place", box)
        self.drop_here_button.clicked.connect(self._drop_in_place)
        row.addWidget(self.drop_button)
        row.addWidget(self.drop_here_button)
        row.addStretch(1)
        box.layout().addLayout(row)
        self.tip_note = muted_label("", box)
        self.tip_note.setWordWrap(True)
        box.layout().addWidget(self.tip_note)
        return box

    @property
    def done(self) -> bool:
        """A tip is on, by the robot's answer."""
        return self.session.tip.attached is True

    def _running(self) -> bool:
        return self.calibration is not None and self.calibration.running

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
        self.refresh()

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

    def _drop_in_place(self) -> None:
        self._tip_job(lambda _log: self.session.drop_tip_in_place(),
                      "dropping the tip in place", moves=False)

    def _tip_job(self, job, what: str, done: str = "", *,
                 moves: bool = True) -> None:
        if self._running():
            return

        def then(_result):
            if done:
                show_toast(self.box, done)
            self.refresh()

        if not self.jog.run_job(job, what=what, then=then, moves=moves):
            self.tip_note.setText(self.session.robot_lease.refusal
                                  if self.session.robot_lease.busy
                                  else "Robot busy.")
        self.refresh()

    def refresh(self, *_args) -> None:
        session = self.session
        running = self._running()
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
        for button in (self.drop_button, self.drop_here_button):
            button.setVisible(on)
            button.setEnabled(known and on)
        self.tip_note.setVisible(bool(self.tip_note.text()))
        self.changed.emit()


class TipCalibrationBlock(QObject):
    """The "Calibrate the tip" card and its run, for any page or sheet with
    a session, a jog panel and a picture: `box` goes into the host's layout.

    The run claims the robot's lease as `holder` (`run=True`: STOP, and the
    pill by `shell.RUNS[holder]`, whose page is asked `run_activity`). While
    it runs the host's `jog` is hidden - its keys given up, the touch-up's
    panel the only thing that moves the robot - and `view` shows the camera
    the run looks through. `done` is the tip on calibrated in this robot
    session (`readiness.tip_calibration_status`); `calibrated` is emitted
    when a run or the fine-tune has saved, `changed` after every refresh.
    The cameras are opened by `open_cameras`, which the host calls when it
    is shown.
    """

    # Emitted from the worker thread when the routine waits for the
    # operator at the disc. A signal, so that the slot runs on the GUI
    # thread whatever thread asked.
    disc_requested = Signal()
    changed = Signal()
    calibrated = Signal()

    def __init__(self, session: Session, jog: JogPanel, view: CameraView, *,
                 holder: str = RUN_HOLDER, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.jog = jog
        self.view = view
        self.holder = holder
        self._worker: Worker | None = None
        self._phase: str | None = None
        self._disc_seen = False
        self._gate = threading.Event()
        self._aborted = False
        self._last_line = ""
        # How the last run ended, shown under Start until the next one.
        self._outcome = ""
        # The crosshair the last calibration found, robot XY: where Check
        # sends the tip (else `_crosshair_xy`'s guess).
        self._crosshair: tuple[float, float] | None = None
        # Where the gantry is, by whichever panel read it last.
        self._pose: tuple[float, float, float] | None = None
        # A fine-tune step was taken since the tip was put on the crosshair.
        self._nudged = False
        self.opener = CameraOpener(session, self)
        self.box = self._build(parent)

        self.disc_requested.connect(self._at_disc)
        for panel in (self.jog, self.touch_jog):
            panel.pose_changed.connect(self._on_pose)
        self.touch_jog.moved.connect(self._on_nudge)
        session.camera_opened.connect(lambda _l: self.refresh())
        session.camera_closed.connect(lambda _l: self.refresh())
        session.robot_state_changed.connect(lambda _s: self.refresh())
        session.robot_busy_changed.connect(lambda _h: self.refresh())
        session.profile_changed.connect(lambda _p: self.refresh())
        session.settings_changed.connect(lambda _s: self.refresh())
        session.labware_changed.connect(self.refresh)
        # The window's Stop: a wait for the operator ends the run with
        # nothing saved. A second Stop halts the robot.
        session.stop_requested.connect(self._on_stop)
        session.tip_changed.connect(lambda _t: self.refresh())
        self.refresh()

    def _build(self, parent) -> QWidget:
        """The three steps under one heading, the result and the fine-tune,
        in one widget: dimmed as one until a tip is on, hidden as one on
        Liquid's Tip step."""
        box = QWidget(parent)
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        self.heading = heading("Calibrate the tip", 2)
        column.addWidget(self.heading)

        self.start_step = StepCard(box, 1, "Start")
        self.start_step.add(wrapped(
            "The camera goes to the calibration position."))
        self.start_button = primary_button("Start tip calibration", box)
        self.start_button.clicked.connect(self._start)
        self.start_step.add_button(self.start_button)
        column.addWidget(self.start_step.box)

        self.disc_step = StepCard(box, 2, "Disc on the calibration module")
        self.disc_step.add(wrapped(
            "Check on the picture that the crosshair disc is there. If not, "
            "put it on now."))
        self.disc_button = secondary_button("The disc is there", box)
        self.disc_button.clicked.connect(self._disc_there)
        self.disc_step.add_button(self.disc_button)
        column.addWidget(self.disc_step.box)

        self.calibrate_step = StepCard(box, 3, "Calibrate")
        self.calibrate_step.add(wrapped(
            "The robot finds the tip under the camera by itself. About "
            "30 s."))
        self.calibrate_button = primary_button("Calibrate", box)
        self.calibrate_button.clicked.connect(self._calibrate)
        self.calibrate_step.add_button(self.calibrate_button)
        self.spinner = QProgressBar(box)
        self.spinner.setRange(0, 0)                  # turning, no amount
        self.spinner.setTextVisible(False)
        self.spinner.setFixedHeight(6)
        self.spinner.hide()
        self.calibrate_step.add(self.spinner)
        column.addWidget(self.calibrate_step.box)

        # What stops Start, what the run is doing, or how it ended.
        self.checks = muted_label("", box)
        self.checks.setWordWrap(True)
        column.addWidget(self.checks)
        self.calibrated_banner = Banner(CALIBRATED, tone="ok", parent=box)
        self.check_button = self.calibrated_banner.add_action(
            "Check: tip to crosshair")
        self.check_button.clicked.connect(self._check)
        column.addWidget(self.calibrated_banner)
        self.redo_button = secondary_button("Redo calibration", box)
        self.redo_button.clicked.connect(self._start)
        column.addWidget(self.redo_button, 0, Qt.AlignmentFlag.AlignLeft)

        self.tune_fold = Section("Fine-tune by hand", collapsed=True,
                                 parent=box)
        self.tune_fold.toggle.toggled.connect(lambda _on: self.refresh())
        body = self.tune_fold.body.layout()
        body.addWidget(wrapped(
            f"Nudge the tip onto the central crosshair "
            f"({TOUCH_UP_STEP_MM:g} mm steps), then Save."))
        self.tune_away = wrapped("The tip is not over the crosshair.")
        body.addWidget(self.tune_away)
        self.tune_check_button = secondary_button("Check: tip to crosshair",
                                                  box)
        self.tune_check_button.clicked.connect(self._check)
        body.addWidget(self.tune_check_button, 0, Qt.AlignmentFlag.AlignLeft)
        # XY only: a Z step here pushes the tip into the disc. Its pad is
        # over the picture; the panel itself shows nothing.
        self.touch_jog = JogPanel(self.session, shortcut_host=parent,
                                  machine_controls=False,
                                  xy_only=True, parent=self.tune_fold.body)
        self.touch_jog.show_position_on(self.view)
        self.touch_jog.hide()
        body.addWidget(self.touch_jog)
        self.tune_save_button = primary_button("Save", box)
        self.tune_save_button.clicked.connect(self._save_tune)
        body.addWidget(self.tune_save_button, 0, Qt.AlignmentFlag.AlignLeft)
        column.addWidget(self.tune_fold)
        return box

    @property
    def done(self) -> bool:
        """The tip on is calibrated, in this robot session."""
        return tip_calibration_status(self.session)[0]

    @property
    def running(self) -> bool:
        """A calibration run is under way, its waits included."""
        return self._phase is not None

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
            self.refresh()
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
                    manual_touch_up=None, robot_session=robot_session,
                    tip_pickup=tip_pickup, log=log)
            finally:
                # It switches the lights on when it finds them off.
                session.refresh_lights()
            # The tip is on the crosshair, offset by what was measured, and
            # left there for the fine-tune.
            pose = tuple(float(v) for v in xyz(robot))
            offset = result.offset
            crosshair = (pose[0] - offset.dx, pose[1] - offset.dy)
            return result, standin, crosshair, pose

        # The robot for the whole calibration, the wait included.
        lease = session.robot_lease.claim(self.holder, run=True)
        if lease is None:
            self._outcome = session.robot_lease.refusal
            self.refresh()
            return
        self._outcome = self._last_line = ""
        self._disc_seen = False
        self._phase = MOVING
        worker = Worker(job, what="calibrating the pipette", lease=lease)
        self._worker = worker
        worker.message.connect(self._on_message)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        log.info("pipette calibration: started")
        worker.start()
        self.refresh()

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

    # ...and from the GUI's side.
    def _at_disc(self) -> None:
        self._phase = DISC
        self.refresh()

    def _disc_there(self) -> None:
        if self._phase == DISC:
            self._disc_seen = True
            self.refresh()

    def _calibrate(self) -> None:
        if self._phase == DISC and self._disc_seen:
            self._phase = MEASURING
            self._gate.set()
            self.refresh()

    def _on_pose(self, pose) -> None:
        if pose is not None:
            self._pose = tuple(float(v) for v in pose)
            self.refresh()

    def _on_nudge(self) -> None:
        self._nudged = True
        self.refresh()

    def _over_crosshair(self) -> bool:
        """The tip is over the crosshair, by the pose last read: within
        `TUNE_RANGE_MM` of it plus the saved offset, at the module height."""
        profile = self.session.profile
        offset = profile.calibration.pipette_offset if profile else None
        crosshair = self._crosshair_xy()
        if self._pose is None or offset is None or crosshair is None:
            return False
        x, y, z = self._pose
        module = float(profile.calibration.tip_target.module_height)
        return (math.hypot(x - crosshair[0] - offset.dx,
                           y - crosshair[1] - offset.dy) <= TUNE_RANGE_MM
                and abs(z - module) <= TUNE_RANGE_MM)

    def _tuning(self) -> bool:
        """The fine-tune's pad is up: unfolded, nothing running, the tip
        over the crosshair."""
        return (not self.tune_fold.collapsed and not self.running
                and self.done and self._over_crosshair())

    def _save_tune(self) -> None:
        """The offset the tip is at now, read and saved: a short job."""
        profile = self.session.profile
        crosshair = self._crosshair_xy()
        if (profile is None or crosshair is None or not self._nudged
                or profile.calibration.pipette_offset is None):
            return
        robot = self.session.robot

        def saved(pose) -> str:
            old = profile.calibration.pipette_offset
            x, y, _z = (float(v) for v in pose)
            offset = old.model_copy(update={
                "dx": x - crosshair[0], "dy": y - crosshair[1],
                "method": "auto+manual",
                "measured_at": datetime.now(timezone.utc)})
            profile.calibration.pipette_offset = offset
            profile.save_calibration(backup=False)
            log.info("pipette offset fine-tuned: dx %+.3f dy %+.3f mm",
                     offset.dx, offset.dy)
            self._nudged = False
            self.session.profile_changed.emit(profile)
            show_toast(self.box, "Fine-tune saved.")
            self.refresh()
            self.calibrated.emit()
            return ""

        if not self.touch_jog.run_job(lambda _log: xyz(robot), moves=False,
                                      what="reading the tip's position",
                                      then=saved):
            self.touch_jog.tell(self.session.robot_lease.refusal)
        self.refresh()

    def _on_stop(self) -> None:
        if self._phase == DISC:
            self._abort()

    def _abort(self) -> None:
        self._aborted = True
        self._gate.set()
        self.refresh()

    def _on_message(self, text: str) -> None:
        log.info("pipette calibration: %s", text)
        self._last_line = text
        self.refresh()

    def _on_finished(self, payload) -> None:
        self._worker = None
        self._phase = None
        result, standin, crosshair, pose = payload
        offset: PipetteOffset = result.offset
        self._crosshair = crosshair
        self._pose = pose
        self._nudged = False
        self._outcome = STANDIN_NOTE if standin else ""
        log.info("pipette offset calibrated: dx %+.3f dy %+.3f mm (%s)",
                 offset.dx, offset.dy, offset.method)
        profile = self.session.profile
        if profile is not None:
            log.info("pipette calibration: saved to %s\n%s",
                     profile.path / "calibration.json", result)
            self.session.profile_changed.emit(profile)
        show_toast(self.box, CALIBRATED)
        self.refresh()
        self.calibrated.emit()

    def _on_failed(self, reason: str) -> None:
        self._worker = None
        self._phase = None
        error = getattr(self.sender(), "error", None)
        if isinstance(error, TouchUpAborted):
            log.info("pipette calibration stopped: %s", reason)
            self._outcome = f"Calibration stopped. {NOTHING_SAVED}"
        else:
            log.error("pipette calibration failed: %s", reason)
            self._outcome = f"Calibration failed: {reason} {NOTHING_SAVED}"
        self.refresh()

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

        def job(log):
            motion.tip_to((x, y), z, log=log)

        def there(_result):
            # Back on the crosshair: a fine-tune starts from here.
            self._nudged = False

        # The fine-tune's pad is hidden while the tip is away, so the
        # page's panel takes it there.
        self.jog.run_job(job, what=f"tip to the crosshair "
                         f"({x:.2f}, {y:.2f}) at z {z:g}", then=there)
        self.refresh()

    # -- display -------------------------------------------------------------

    def _running(self) -> bool:
        return self._worker is not None and self._worker.running

    def run_activity(self) -> str | None:
        """For the top bar's pill: "waiting" while the run waits for the
        operator at the disc."""
        return "waiting" if self._phase == DISC else None

    def refresh(self, *_args) -> None:
        self.session.run_activity_changed.emit()
        running = self.running
        tuning = self._tuning()
        # The host's jog panel only while nothing runs and the fine-tune's
        # pad is down: hidden, its keys are given up - one pad, one set.
        self.touch_jog.setVisible(tuning)
        self.jog.setVisible(not running and not tuning)
        if tuning:
            profile = self.session.profile
            # The lower camera is turned and mirrored: through TipTarget.axes
            # the arrows move the tip the way they point on its picture.
            self.touch_jog.set_view_axes(profile.calibration.tip_target.axes)
            self.touch_jog.set_step(TOUCH_UP_STEP_MM)
        self._show(running, tuning)
        # The upper camera finds the disc; the lower one sees the tip.
        lower = (self._phase == MEASURING
                 or not running and self.done and self._over_crosshair())
        self.view.set_camera(self._under_camera() if lower
                             else self._over_camera())
        self.changed.emit()

    def _show(self, running: bool, tuning: bool) -> None:
        session = self.session
        on = session.tip.attached is True
        # Dimmed until a tip is on; never while its own run goes on.
        self.box.setEnabled(on or running)
        measured, why, _stale = tip_calibration_status(session)
        problems = [] if running else self._readiness()
        phase = self._phase

        # The step open now: Start, the disc, Calibrate, or all done.
        if running:
            at = (0 if phase == MOVING else
                  1 if phase == DISC and not self._disc_seen else 2)
        else:
            at = 3 if measured else 0
        for number, step in enumerate((self.start_step, self.disc_step,
                                       self.calibrate_step)):
            if not on and not running:
                step.set_state(BLOCKED, FIRST_A_TIP)
            else:
                step.set_state(OK if number < at else
                               TODO if number == at else BLOCKED)
        free = not running and not session.robot_lease.busy
        self.start_button.setEnabled(free and not problems)
        self.disc_button.setEnabled(phase == DISC and not self._disc_seen)
        self.calibrate_button.setEnabled(phase == DISC and self._disc_seen)
        self.spinner.setVisible(phase == MEASURING)

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
        result = measured and not running
        self.calibrated_banner.setVisible(result)
        self.check_button.setEnabled(
            result and self._crosshair_xy() is not None and free)
        self.redo_button.setVisible(result)
        self.redo_button.setEnabled(free and not problems)

        self.tune_fold.setVisible(result
                                  and session.settings.tip_cal_touch_up)
        away = not tuning
        self.tune_away.setVisible(away)
        self.tune_check_button.setVisible(away)
        self.tune_check_button.setEnabled(self.check_button.isEnabled())
        self.tune_save_button.setEnabled(
            tuning and self._nudged and not self.touch_jog.busy
            and not session.robot_lease.busy)

    # -- cameras -------------------------------------------------------------

    def _over_camera(self):
        label = self.session.upper_camera_label
        return self.session.camera(label) if label else None

    def _under_camera(self):
        label = self.session.lower_camera_label
        return self.session.camera(label) if label else None

    def open_cameras(self) -> None:
        """Both: the upper camera finds the disc and the lower one measures
        the tip, and the routine refuses to start without either. The lower
        one in the profile's mode, which is what the routine checks: the
        Picking page's clips may have left it in a faster one."""
        self.opener.ensure(self.session.upper_camera_label)
        lower = self.session.lower_camera_label
        self.opener.ensure(lower, self._calibration_mode(lower))
        self.refresh()

    def _calibration_mode(self, label: str | None):
        """The mode the routine measures the lower camera in: its profile
        default (`calibrate_pipette._expected_under_resolution`)."""
        profile = self.session.profile
        spec = profile.cameras.get(label) if profile and label else None
        return (tuple(int(v) for v in spec.default_resolution)
                if spec is not None else None)


def _to_calibration(name: str) -> property:
    """A page attribute that is the calibration block's, read and set."""
    return property(lambda page: getattr(page.calibration, name),
                    lambda page, value: setattr(page.calibration, name, value))


class PipetteCalibration(QWidget):
    """The Tip step: the picture, the two blocks and the page's jog panel."""

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._task: str | None = None

        self.view = CameraView(self)
        self.jog = JogPanel(self.session, shortcut_host=self,
                            machine_controls=False,
                            parent=self)
        self.jog.show_position_on(self.view)
        self.calibration = TipCalibrationBlock(session, self.jog, self.view,
                                               parent=self)
        self.tip = TipOnBlock(session, self.jog, calibration=self.calibration,
                              parent=self)
        # One column: the steps read top to bottom.
        panel = QWidget(self)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        for widget in (self.tip.box, self.calibration.box, self.jog):
            column.addWidget(widget)
        column.addStretch(1)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2,
                                  SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addWidget(FeedRow(self.view, scroll_column(panel, PANEL_WIDTH)),
                         1)
        self.calibration.changed.connect(self._show_task)

        # The blocks' widgets, by the names the page has always had.
        self.opener = self.calibration.opener
        self.calibration_box = self.calibration.box
        self.calibrated = self.calibration.calibrated_banner
        for name in ("no_rack", "back_to_deck", "racks", "rack_hint",
                     "rack_grid", "take_button", "tip_state", "drop_button",
                     "drop_here_button", "tip_note"):
            setattr(self, name, getattr(self.tip, name))
        for name in ("start_step", "start_button", "disc_step", "disc_button",
                     "calibrate_step", "calibrate_button", "spinner",
                     "checks", "check_button", "redo_button", "tune_fold",
                     "tune_check_button", "touch_jog", "tune_save_button"):
            setattr(self, name, getattr(self.calibration, name))
        self._refresh()

    _worker = _to_calibration("_worker")
    _crosshair = _to_calibration("_crosshair")
    _readiness = _to_calibration("_readiness")

    def set_task(self, task: str | None) -> None:
        """The task the step is shown in: Liquid's has no calibration."""
        self._task = task
        self._refresh()

    def _show_task(self) -> None:
        self.calibration.box.setVisible(self._task != "liquid")

    def run_activity(self) -> str | None:
        return self.calibration.run_activity()

    def _refresh(self, *_args) -> None:
        self.calibration.refresh()

    def _abort(self) -> None:
        self.calibration._abort()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.calibration.open_cameras()
