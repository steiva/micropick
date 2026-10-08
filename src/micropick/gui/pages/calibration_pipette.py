"""Measuring the tip offset, as numbered steps on one page.

The picture on the left, and on the right the steps in the order they are
done, each a card with a mark - done, to do, or waiting for a step above:

1. Place the calibration disc on the calibration module. The application
   cannot see it, so the operator ticks it; Set position and Start wait for
   the tick.
2. Set the disc position: jog the disc's central crosshair under the camera
   crosshair at the working height, then Set position. With one stored, Go
   to saved position drives there to check it. The jog panel sits right
   under this step.
3. Put a tip on the pipette - on Robot & Deck, or from the tip button in the
   status bar.
4. Calibrate: the starting offset, what still stops the run, and Start.
5. Nudge the tip onto the crosshair - only while the routine waits for it.

How the routine measures - frames per reading, the check after the
correction, the manual adjustment, the tip type recorded - is set in
Settings (`AppSettings.tip_cal_*`), not here: those are chosen once. The
cameras are the profile's upper and lower ones, chosen in Settings too.

**The disc position is a profile position, `tip_calib`.** The notebook
teaches it once with `jog(...)` and `profile.remember`, and drives to it with
`profile.where("tip_calib")` before every calibration. The routine's own
drive to the stored position happens at the start of the run, as in the
notebook, so Go to saved position is for looking, not a step that can be
forgotten.

**One picture.** It shows the upper camera while the disc is set and the
lower one while the routine runs and waits for the operator. The disc's jog
panel is hidden while the routine runs, so nothing but the touch-up can move
the robot then; the touch-up has a jog panel of its own, shown only while it
waits.

**The routine needs a tip on the pipette, and asks the robot.** Every tip
seats differently, which is why this is redone after each pick-up; a
calibration taken with no tip measures nothing and a stored offset from it
would send every pick to the wrong place. `Session.tip` is the robot's record,
and the run is refused on anything but "a tip is on".

**The manual touch-up is the notebook's `jog_in_window`, in Qt**, with one
difference: its arrows follow the lower camera's picture, which is turned
and mirrored, through `TipTarget.axes` (`JogPanel` `view_axes`) - right on
the screen moves the tip right on the picture. The
workflow calls `manual_touch_up(robot, camera, view)` from its own thread and
carries on when it returns, reading the final pose to compute the offset. Here
the callback raises a signal to the GUI thread and waits on an Event; step 5
appears with the lower camera live and a jog panel at 0.05 mm, and Done sets
the event. Abort sets it too, with a flag the callback turns into an
exception, so the routine ends before it saves. Between Start and Done the
routine is not cancellable — it is a handful of moves and a few seconds of
detection — and the log says which of those it is doing.

**The routine ends with the Z axis retracted**, as the notebook's cell does
after printing the result: the tip is at the module height over the disc,
and every next move starts by going somewhere else. An aborted or failed run
leaves the gantry where it stopped, so what happened can be seen.

**The profile is written by the workflow, on Done.** `calibrate_pipette_offset`
saves the offset and the by-product homography together when given the
profile, and it is given the profile here because Done is the operator
looking at the tip on the crosshair and saying so — the same act the camera
page's Save button is. Skipping the touch-up saves on the automatic result,
which is what the notebook does with `manual_touch_up=None`.
"""

from __future__ import annotations

import logging
import threading
import time

import qtawesome as qta
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QMessageBox,
                               QPlainTextEdit, QPushButton, QWidget)

from ...config.schema import PipetteOffset
from ...core.calibration.pixel_map import PixelMap
from ...hardware.protocols import xyz
from ...workflows import manual as moves
from ...workflows.calibrate_pipette import calibrate_pipette_offset
from ..auto_camera import CameraOpener
from ..session import Session
from ..theme import SPACING
from ..theme.factory import (Section, card, double_spin_box, heading,
                             muted_label, primary_button, scroll_column,
                             secondary_button)
from ..tip_detector import STANDIN_NOTE, load_tip_detector
from ..widgets.camera_view import CameraView
from ..widgets.card_columns import CardColumns
from ..widgets.checklist import BLOCKED, COLOURS, ICONS, OK, TODO
from ..widgets.done_banner import DoneBanner
from ..widgets.feed_row import FeedRow
from ..widgets.jog_panel import JogPanel
from ..workers import Worker

__all__ = ["PipetteCalibration", "POSITION_NAME", "TouchUpAborted"]

TITLE = "Tip calibration"

log = logging.getLogger(__name__)

PANEL_WIDTH = 420

# The profile position the disc is taught under. The notebook's name, kept:
# a profile taught from the notebook works here and the other way round.
POSITION_NAME = "tip_calib"

# The jog step for the touch-up, as the notebook sets it.
TOUCH_UP_STEP_MM = 0.05

# Where to start when the profile has no offset yet: notebook 01's manual
# offset for this rig. Only has to land the tip inside the lower camera's
# view; the calibration measures the real one.
DEFAULT_OFFSET_MM = (16.0, 60.0)

# A step's mark, the size of a heading's line.
MARK_PX = 18


class TouchUpAborted(RuntimeError):
    """The operator pressed Abort during the manual touch-up."""


class _Step:
    """One numbered step: a card whose heading carries its number and a
    mark, and whose body the page fills."""

    def __init__(self, page: QWidget, number: int, title: str):
        self.box = card(page)
        row = QHBoxLayout()
        self.mark = QLabel(page)
        row.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignVCenter)
        self.heading = heading(f"{number}. {title}", 2)
        self.heading.setWordWrap(True)
        row.addWidget(self.heading, 1)
        self.box.layout().addLayout(row)
        self.state = None
        self.set_state(TODO)

    def add(self, widget: QWidget) -> None:
        self.box.layout().addWidget(widget)

    def add_layout(self, layout) -> None:
        self.box.layout().addLayout(layout)

    def set_state(self, state: str, why: str = "") -> None:
        """OK, TODO or BLOCKED; `why` is the mark's tooltip."""
        self.mark.setToolTip(why)
        if state == self.state:
            return
        self.state = state
        self.mark.setPixmap(qta.icon(ICONS[state], color=COLOURS[state])
                            .pixmap(MARK_PX, MARK_PX))
        # A step that waits for one above it reads as waiting.
        self.heading.setEnabled(state != BLOCKED)


def _wrapped(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    return label


class PipetteCalibration(QWidget):
    # Emitted from the worker thread when the routine wants the operator.
    # A signal, so that the slot runs on the GUI thread whatever thread asked.
    touch_up_requested = Signal()

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._worker: Worker | None = None
        self._result = None
        self._standin = False
        self._gate = threading.Event()
        self._aborted = False
        self._last_line = ""
        # How the last run ended, shown under Start until the next one.
        self._outcome = ""
        # The profile the offset boxes were last filled for: a new one is a
        # new starting point.
        self._filled_for = None
        self.opener = CameraOpener(session, self)

        self.view = CameraView(self)
        self.jog = JogPanel(self.session, shortcut_host=self,
                            machine_controls=False,
                            collapsed=("positions",), parent=self)
        self.jog.show_position_on(self.view)
        panel = CardColumns([self._disc_step(), self._position_step(),
                             self.jog, self._tip_step(), self._run_step(),
                             self._touch_step(), self._details_card()], self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2,
                                  SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addWidget(FeedRow(self.view, scroll_column(panel, PANEL_WIDTH)),
                         1)

        self.touch_up_requested.connect(self._begin_touch_up)
        session.camera_opened.connect(lambda _l: self._refresh())
        session.camera_closed.connect(lambda _l: self._refresh())
        session.robot_state_changed.connect(lambda _s: self._refresh())
        session.profile_changed.connect(lambda _p: self._refresh())
        session.settings_changed.connect(lambda _s: self._refresh())
        # The window's Stop. The routine has no point to stop at between
        # its moves; only the manual adjustment can be left, which ends it
        # with nothing saved. A second Stop halts the robot.
        session.stop_requested.connect(self._on_stop)
        session.tip_changed.connect(lambda _t: self._refresh())
        self._refresh()

    # -- construction --------------------------------------------------------

    def _disc_step(self) -> QWidget:
        self.disc = _Step(self, 1, "Place the calibration disc on the "
                                   "calibration module")
        self.disc.add(_wrapped(
            "The disc with the crosshairs goes on the calibration module, "
            "flat and centred. It can stay there between calibrations."))
        self.disc_placed = QCheckBox("The disc is on the module", self)
        # The keys over this page are the gantry's.
        self.disc_placed.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.disc_placed.toggled.connect(lambda _on: self._refresh())
        self.disc.add(self.disc_placed)
        return self.disc.box

    def _position_step(self) -> QWidget:
        self.position = _Step(self, 2, "Set the disc position")
        self.position_text = _wrapped()
        self.position.add(self.position_text)
        self.position_state = muted_label()
        self.position.add(self.position_state)
        buttons = QHBoxLayout()
        self.remember_button = primary_button("Set position", self)
        self.remember_button.clicked.connect(self._remember)
        self.goto_button = secondary_button("Go to saved position", self)
        self.goto_button.clicked.connect(self._goto)
        buttons.addWidget(self.remember_button)
        buttons.addWidget(self.goto_button)
        buttons.addStretch(1)
        self.position.add_layout(buttons)
        self.position_note = _wrapped()
        self.position.add(self.position_note)
        return self.position.box

    def _tip_step(self) -> QWidget:
        self.tip = _Step(self, 3, "Put a tip on the pipette")
        self.tip_text = _wrapped()
        self.tip.add(self.tip_text)
        row = QHBoxLayout()
        # "&&": a single & is a mnemonic, and would read "Robot _Deck".
        self.tip_link = QPushButton("Robot && Deck ›", self)
        self.tip_link.setFlat(True)
        self.tip_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tip_link.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tip_link.clicked.connect(
            lambda: self.session.page_requested.emit("labware"))
        row.addWidget(self.tip_link)
        row.addStretch(1)
        self.tip.add_layout(row)
        return self.tip.box

    def _run_step(self) -> QWidget:
        self.run = _Step(self, 4, "Calibrate")
        self.run.add(_wrapped(
            "The robot drives to the disc position, finds the tip with the "
            "lower camera and measures where it is from the camera's centre. "
            "It ends with step 5, unless the manual adjustment is off in "
            "Settings."))

        # The starting offset: where the routine first looks for the tip.
        self.offset_note = muted_label()
        self.run.add(self.offset_note)
        self.dx = double_spin_box(self)
        self.dy = double_spin_box(self)
        for spin in (self.dx, self.dy):
            spin.setRange(-300.0, 300.0)
            spin.setDecimals(3)
            spin.setSingleStep(0.1)
            spin.setSuffix(" mm")
            spin.setToolTip("Camera centre to tip, roughly: it only has to "
                            "land the tip in the lower camera's view.")
        self.reset_offset_button = secondary_button("Reset", self)
        self.reset_offset_button.setToolTip(
            f"dx {DEFAULT_OFFSET_MM[0]:g} mm, dy {DEFAULT_OFFSET_MM[1]:g} mm: "
            f"the usual offset on this rig.")
        self.reset_offset_button.clicked.connect(self._reset_offset)
        row = QHBoxLayout()
        row.addWidget(QLabel("dx"))
        row.addWidget(self.dx)
        row.addWidget(QLabel("dy"))
        row.addWidget(self.dy)
        row.addWidget(self.reset_offset_button)
        row.addStretch(1)
        self.run.add_layout(row)

        # What stops the run, in words, under the button it holds back.
        self.checks = QLabel()
        self.checks.setWordWrap(True)
        self.checks.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.run.add(self.checks)
        row = QHBoxLayout()
        self.start_button = primary_button("Start", self)
        self.start_button.clicked.connect(self._start)
        row.addWidget(self.start_button)
        self.settings_link = QPushButton("Settings ›", self)
        self.settings_link.setFlat(True)
        self.settings_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_link.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.settings_link.setToolTip("Frames per reading, the check after "
                                      "the correction, the manual adjustment "
                                      "and the tip type are set in Settings.")
        self.settings_link.clicked.connect(
            lambda: self.session.page_requested.emit("settings"))
        row.addStretch(1)
        row.addWidget(self.settings_link)
        self.run.add_layout(row)
        self.method_note = muted_label()
        self.run.add(self.method_note)
        self.done = DoneBanner(self)
        self.run.add(self.done)
        return self.run.box

    def _touch_step(self) -> QWidget:
        """Shown only while the routine is waiting for the operator."""
        self.touch = _Step(self, 5, "Nudge the tip onto the crosshair")
        self.touch_box = self.touch.box
        self.touch.add(_wrapped(
            f"The lower camera is live in the picture. Nudge the tip onto the "
            f"central crosshair — the step is {TOUCH_UP_STEP_MM:g} mm — and "
            f"press Done. Whatever you move is part of the offset. Abort "
            f"ends the calibration with nothing saved."))
        # Move open: nudging the tip onto the crosshair is the whole of this
        # block. Positions folded: nothing here is a place to return to.
        # XY only: a Z step here pushes the tip into the disc.
        self.touch_jog = JogPanel(self.session, shortcut_host=self,
                                  machine_controls=False,
                                  collapsed=("positions",), xy_only=True,
                                  parent=self.touch_box)
        self.touch_jog.show_position_on(self.view)
        self.touch.add(self.touch_jog)
        row = QHBoxLayout()
        self.accept_button = primary_button("Done", self)
        self.accept_button.clicked.connect(self._accept)
        self.abort_button = secondary_button("Abort", self)
        self.abort_button.clicked.connect(self._abort)
        row.addWidget(self.accept_button)
        row.addWidget(self.abort_button)
        row.addStretch(1)
        self.touch.add_layout(row)
        self.touch_box.hide()
        return self.touch_box

    def _details_card(self) -> QWidget:
        """Everything the routine wrote, in one text: the run's lines, then
        the result and where it was saved. A card of its own and folded: the
        line under Start says what the routine is doing, the banner what it
        saved, and every line also goes to the application log. The full
        text is for checking a result or seeing what went wrong."""
        self.details = Section("Details", collapsed=True, parent=self)
        self.run_log = QPlainTextEdit(self)
        self.run_log.setReadOnly(True)
        self.run_log.setMaximumBlockCount(2000)
        # Not wrapped: the result's tables line up in columns.
        self.run_log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.run_log.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.run_log.setMinimumHeight(260)
        self.details.body.layout().addWidget(self.run_log)
        return self.details

    def _reset_offset(self) -> None:
        self.dx.setValue(DEFAULT_OFFSET_MM[0])
        self.dy.setValue(DEFAULT_OFFSET_MM[1])

    # -- the disc ------------------------------------------------------------

    def _stored_position(self):
        profile = self.session.profile
        if profile is None:
            return None
        return profile.positions.get(POSITION_NAME)

    def _goto(self) -> None:
        stored = self._stored_position()
        if stored is None or self.session.robot is None or self._busy():
            return
        robot = self.session.robot

        def job():
            moves.drive_tip(robot, stored[:2], stored[2], None)
            return xyz(robot)

        self._run_short(Worker(job), f"going to {POSITION_NAME} {stored}")

    def _remember(self) -> None:
        if self.session.robot is None or self.session.profile is None or self._busy():
            return
        stored = self._stored_position()
        if stored is not None:
            answer = QMessageBox.question(
                self, "Set the disc position",
                f"Replace the saved disc position "
                f"{tuple(round(v, 2) for v in stored)} with where the gantry "
                f"is now? Every calibration after this one drives here first.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        robot, session = self.session.robot, self.session

        def job():
            session.remember(POSITION_NAME, xyz(robot))

        self._run_short(Worker(job), f"remembering {POSITION_NAME}")

    def _module_height(self) -> float:
        profile = self.session.profile
        return float(profile.calibration.tip_target.module_height) if profile else 67.1

    def _run_short(self, worker: Worker, what: str) -> None:
        """A move or a save: no log panel, just the buttons greyed out."""
        self._worker = worker
        worker.what = worker.what or what
        worker.finished.connect(self._short_done)
        worker.failed.connect(self._short_failed)
        log.info("%s", what)
        self.position_note.setText("")
        worker.start()
        self._refresh()

    def _short_done(self, _result=None) -> None:
        if self.sender() is not self._worker:
            return
        self._worker = None
        self._refresh()

    def _short_failed(self, reason: str) -> None:
        if self.sender() is not self._worker:
            return
        self._worker = None
        self.position_note.setText(reason)
        log.error("%s", reason)
        self._refresh()

    # -- running -------------------------------------------------------------

    def _readiness(self) -> list[str]:
        """Everything that stops the routine from starting, as sentences,
        in the order of the steps. Empty means go."""
        session, problems = self.session, []
        profile = session.profile
        if session.robot is None:
            problems.append("Connect the robot on the Profile page.")
        if profile is None:
            problems.append("Load a profile on the Profile page.")
        elif profile.pixel_map is None:
            problems.append("Calibrate the camera first (Camera calibration): "
                            "the tip is found through its pixel map.")
        if not self.disc_placed.isChecked():
            problems.append("Step 1: place the disc and tick it.")
        if profile is not None and self._stored_position() is None:
            problems.append("Step 2: set the disc position.")
        tip = session.tip
        if session.robot is not None and tip.attached is not True:
            problems.append("Step 3: put a tip on the pipette. The offset is "
                            "of the tip, and every tip seats differently.")
        over, under = self._over_camera(), self._under_camera()
        if over is None:
            problems.append("The upper camera is not open yet.")
        if under is None:
            problems.append("The lower camera is not open yet.")
        else:
            mode = self._calibration_mode(self.session.lower_camera_label)
            if mode is not None and tuple(under.resolution) != mode:
                problems.append(
                    f"The lower camera is open at {under.resolution[0]}x"
                    f"{under.resolution[1]} (the clips' mode); the "
                    f"calibration is measured at {mode[0]}x{mode[1]}, and it "
                    f"is being opened again in that.")
        if over is not None and over is under:
            problems.append("The upper and lower camera are the same camera: "
                            "choose them in Settings.")
        if self.dx.value() == 0.0 and self.dy.value() == 0.0 \
                and (profile is None or profile.calibration.pipette_offset is None):
            problems.append("No starting offset: measure it roughly with a "
                            "ruler and enter dx, dy.")
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
        problems = self._readiness()
        if problems:
            for line in problems:
                self._append("not started: " + line)
            return
        if self._busy():
            return
        session = self.session
        settings = session.settings
        robot, profile = session.robot, session.profile
        over, under = self._over_camera(), self._under_camera()
        target = profile.calibration.tip_target
        pmap = PixelMap.from_config(profile.pixel_map)
        stored = self._stored_position()
        current = (self.dx.value(), self.dy.value())
        frames, verify = settings.tip_cal_frames, settings.tip_cal_verify
        tip_type = self._tip_type()
        touch_up = self._touch_up if settings.tip_cal_touch_up else None
        mock = session.mock

        self.run_log.clear()
        self.done.clear()
        self._outcome = ""
        self._last_line = ""
        self._result = None
        self._standin = False
        self._append(f"starting from ({current[0]:+.3f}, {current[1]:+.3f}) mm, "
                     f"{frames} frames per reading"
                     + (f", tip {tip_type}" if tip_type else ""))

        def job(log):
            log("loading the tip detector")
            detector = load_tip_detector(profile, mock=mock, robot=robot)
            standin = bool(getattr(detector, "is_standin", False))
            if standin:
                log(STANDIN_NOTE)
            log(f"driving to {POSITION_NAME} {tuple(round(v, 2) for v in stored)}")
            moves.drive_tip(robot, stored[:2], stored[2], None, log=log)
            time.sleep(0.5)
            try:
                result = calibrate_pipette_offset(
                    robot, over, under, detector, pmap, target=target,
                    current_offset=current, frames=frames, verify=verify,
                    tip_type=tip_type, profile=profile,
                    manual_touch_up=touch_up, log=log)
            finally:
                # It switches the lights on when it finds them off.
                session.refresh_lights()
            # As the notebook does once the offset is saved: the tip is at
            # the module height over the disc, and the next thing anyone
            # does is drive somewhere else.
            log("retracting leftZ")
            robot.retract_axis("leftZ", verbose=False)
            return result, standin

        worker = Worker(job, what="calibrating the pipette")
        self._worker = worker
        worker.message.connect(self._on_message)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.start()
        self._refresh()

    # The touch-up, from the worker's side: ask, then wait.
    def _touch_up(self, robot, camera, view) -> None:
        self._gate.clear()
        self._aborted = False
        worker = self._worker
        if worker is not None:
            # The activity line, and the log: the calibration is not
            # working now, it is waiting for the operator.
            worker._on_message("waiting for you: nudge the tip onto the "
                               "crosshair, then Done")
        self.touch_up_requested.emit()
        self._gate.wait()
        if self._aborted:
            raise TouchUpAborted("aborted during the manual touch-up; nothing "
                                 "was saved")

    # ...and from the GUI's side.
    def _begin_touch_up(self) -> None:
        # The lower camera is turned and mirrored: through TipTarget.axes the
        # arrows move the tip the way they point on its picture.
        profile = self.session.profile
        if profile is not None:
            self.touch_jog.set_view_axes(profile.calibration.tip_target.axes)
        self.touch_jog.set_step(TOUCH_UP_STEP_MM)
        self.touch_box.show()
        self._append("waiting for you: nudge the tip onto the crosshair, "
                     "then Done")
        self._refresh()

    def _accept(self) -> None:
        if self.touch_jog.busy:
            self._append("a jog step is still in flight; press Done when it "
                         "has landed")
            return
        self.touch_box.hide()
        self._append("done adjusting")
        self._gate.set()
        self._refresh()

    def _on_stop(self) -> None:
        if not self.touch_box.isHidden():
            self._abort()

    def _abort(self) -> None:
        self.touch_box.hide()
        self._aborted = True
        self._gate.set()
        self._refresh()

    def _on_message(self, text: str) -> None:
        self._append(text)

    def _on_finished(self, payload) -> None:
        self._worker = None
        result, standin = payload
        self._result, self._standin = result, standin
        offset: PipetteOffset = result.offset
        self._append("done")
        self._outcome = "Finished: the full result is under Details."
        self.done.show_done(f"Offset ({offset.dx:+.3f}, {offset.dy:+.3f}) mm "
                            f"saved to the profile.")
        log.info("pipette offset calibrated: dx %+.3f dy %+.3f mm (%s)",
                 offset.dx, offset.dy, offset.method)
        profile = self.session.profile
        note = (f"Saved to {profile.path / 'calibration.json'}: offset "
                f"({offset.dx:+.3f}, {offset.dy:+.3f}) mm, method "
                f"{offset.method}"
                + (", with the homography" if result.homography is not None
                   else "") + ".")
        if standin:
            note = STANDIN_NOTE + "\n" + note
        # The result after the run's lines, in the same text; not through
        # _append, which would log line by line what the log already has.
        text = f"\n{note}\n\n{result}"
        if result.homography_report is not None:
            text += f"\n\n{result.homography_report}"
        self.run_log.appendPlainText(text)
        self.session.profile_changed.emit(profile)
        self.dx.setValue(offset.dx)
        self.dy.setValue(offset.dy)
        self._refresh()

    def _on_failed(self, reason: str) -> None:
        self._worker = None
        self.touch_box.hide()
        self.done.clear()
        error = getattr(self.sender(), "error", None)
        aborted = isinstance(error, TouchUpAborted)
        kind = "aborted" if aborted else "failed"
        detail = reason
        self._append(f"{kind}: {detail}")
        self._append("nothing was written to the profile")
        log.error("pipette calibration %s: %s", kind, detail)
        self._outcome = (f"Calibration {kind}: {detail}\nNothing was written "
                         f"to the profile.")
        self._refresh()

    # -- display -------------------------------------------------------------

    def _busy(self) -> bool:
        return self._worker is not None and self._worker.running

    def _append(self, text: str) -> None:
        """A line of the run: into Details and the application log, and as
        the latest word under Start while it runs."""
        self.run_log.appendPlainText(text)
        log.info("pipette calibration: %s", text)
        self._last_line = text
        if self._busy():
            self.checks.setText(f"Running: {text}")

    def _refresh(self) -> None:
        busy = self._busy()
        waiting = not self.touch_box.isHidden()
        session = self.session
        profile = session.profile
        connected = session.robot is not None
        running = busy or waiting
        placed = self.disc_placed.isChecked()

        # The disc's jog panel only while nothing runs: hidden, its keys are
        # given up, and the touch-up's panel is the only thing that moves the
        # robot during a calibration.
        self.jog.setVisible(not running)
        # The key box is the panel's that has the keys now.
        keys = self.touch_jog if waiting else self.jog
        self.view.set_help(keys.help_lines)

        # 1. the disc
        self.disc.set_state(OK if placed else TODO)
        self.disc_placed.setEnabled(not running)

        # 2. its position
        stored = self._stored_position()
        self.position_text.setText(
            f"Jog the disc's central crosshair under the camera crosshair at "
            f"the working height (module height {self._module_height():g} "
            f"mm), then Set position.")
        self.position_state.setText(
            f"Saved: ({stored[0]:.2f}, {stored[1]:.2f}, {stored[2]:.2f}). "
            f"Every calibration drives here first." if stored is not None
            else "No disc position saved in this profile yet.")
        if stored is not None:
            self.position.set_state(OK)
        elif not placed:
            self.position.set_state(BLOCKED, "First: step 1.")
        elif not connected:
            self.position.set_state(BLOCKED, "First: connect the robot.")
        else:
            self.position.set_state(TODO)
        self.goto_button.setVisible(stored is not None)
        self.goto_button.setEnabled(connected and not busy and not running)
        self.remember_button.setEnabled(connected and profile is not None
                                        and placed and not running)
        self.remember_button.setToolTip(
            "" if placed else "Place the disc first (step 1).")

        # 3. the tip
        tip = session.tip
        if not connected:
            self.tip.set_state(BLOCKED, "First: connect the robot.")
            self.tip_text.setText("Connect the robot on the Profile page.")
        elif tip.attached is True:
            self.tip.set_state(OK)
            self.tip_text.setText("The robot reports a tip on the pipette"
                                  + (f", from slot {tip.slot} {tip.well}"
                                     if tip.returnable else "") + ".")
        else:
            self.tip.set_state(TODO)
            self.tip_text.setText(
                ("The robot reports no tip on the pipette."
                 if tip.attached is False else
                 "The robot's tip state is unknown.")
                + " Pick one up on Robot & Deck, or with the tip button in "
                  "the status bar.")
        self.tip_link.setVisible(connected and tip.attached is not True)

        # 4. the calibration
        offset = profile.calibration.pipette_offset if profile else None
        if offset is None:
            self.offset_note.setText(
                f"No offset in the profile yet: starting from the usual "
                f"values, dx {DEFAULT_OFFSET_MM[0]:g} mm and dy "
                f"{DEFAULT_OFFSET_MM[1]:g} mm.")
        else:
            when = (offset.measured_at.strftime("%Y-%m-%d %H:%M")
                    if offset.measured_at else "date unknown")
            self.offset_note.setText(
                f"Last offset: ({offset.dx:+.3f}, {offset.dy:+.3f}) mm, "
                f"{offset.method}, {when}"
                + (f", tip {offset.tip_type}" if offset.tip_type else "")
                + ". The calibration starts from it.")
        for widget in (self.dx, self.dy, self.reset_offset_button):
            widget.setEnabled(not running)
        if not running:
            self._prefill(offset)
        settings = session.settings
        tip_type = self._tip_type()
        self.method_note.setText(
            f"{settings.tip_cal_frames} frames per reading, "
            + ("verified after the correction, " if settings.tip_cal_verify
               else "not verified, ")
            + ("manual adjustment at the end" if settings.tip_cal_touch_up
               else "no manual adjustment")
            + (f"; recorded as tip {tip_type}" if tip_type else "") + ".")

        problems = self._readiness()
        if running:
            self.checks.setText(f"Running: {self._last_line}"
                                if self._last_line else
                                "Running. The lower camera is in the picture.")
            self.run.set_state(TODO)
        else:
            text = ("Ready: press Start." if not problems else
                    "Before Start:\n" + "\n".join("• " + p for p in problems))
            self.checks.setText(f"{self._outcome}\n\n{text}" if self._outcome
                                else text)
            self.run.set_state(OK if self._result is not None else
                               TODO if not problems else BLOCKED,
                               "" if not problems else problems[0])
        self.start_button.setEnabled(not running and not problems)

        # 5. the touch-up
        self.touch.set_state(TODO)
        self.accept_button.setEnabled(waiting)
        self.abort_button.setEnabled(waiting)
        self.view.set_camera(self._under_camera() if running
                             else self._over_camera())

    def _prefill(self, offset) -> None:
        """The profile's offset into the boxes, or the defaults when it has
        none - once per profile, so a value the operator typed stays."""
        profile = self.session.profile
        if profile is not None and profile is not self._filled_for:
            self._filled_for = profile
            dx, dy = ((offset.dx, offset.dy) if offset is not None
                      else DEFAULT_OFFSET_MM)
            self.dx.setValue(dx)
            self.dy.setValue(dy)

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
