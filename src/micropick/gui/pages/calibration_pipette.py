"""Measuring the pipette offset — the notebook's section 4, on one page.

The picture on the left and one panel on the right, in the notebook's order:
Disc position, Starting offset, XY calibration, Details. It used to be a wizard
of four pages with Back and Next; the order was right, but the pages hid
what the next one needed, and going back to fix the disc meant leaving the
camera that showed it. Now everything is in view, and what stops the run is
said in words under Start and in which buttons are enabled - the gate is
still there, it is just not a page.

**The disc position is a profile position, `tip_calib`.** The notebook
teaches it once with `jog(...)` and `profile.remember`, and drives to it with
`profile.where("tip_calib")` before every calibration. The Disc position
section is those two acts: with no stored position, the jog panel and Set
position; with one, Go to last saved and Set position again from wherever
the gantry is standing now. The routine's own drive to the stored position
happens at the start of the run, as in the notebook, so Go to last saved is
for looking, not a step that can be forgotten.

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
the callback raises a signal to the GUI thread and waits on an Event; the
touch-up block - Manual adjustment - appears with the lower camera live and
a jog panel at 0.05 mm, and Done sets the event. Abort sets it too, with a
flag the callback turns into an exception, so the routine ends before it
saves. Between Start and Done the routine is not cancellable — it is a
handful of moves and a few seconds of detection — and the log says which of
those it is doing.

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

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit,
                               QVBoxLayout, QWidget)

from ...config.schema import PipetteOffset
from ...core.calibration.pixel_map import PixelMap
from ...hardware.protocols import xyz
from ...workflows import manual as moves
from ...workflows.calibrate_pipette import calibrate_pipette_offset
from ..auto_camera import CameraOpener
from ..session import Session
from ..theme import SPACING
from ..theme.factory import (Section, card, combo_box, double_spin_box,
                             heading, primary_button, scroll_column,
                             secondary_button, spin_box)
from ..tip_detector import STANDIN_NOTE, load_tip_detector
from ..widgets.camera_view import CameraView
from ..widgets.card_columns import CardColumns
from ..widgets.done_banner import DoneBanner
from ..widgets.feed_row import FeedRow
from ..widgets.jog_panel import JogPanel
from ..workers import Worker

__all__ = ["PipetteCalibration", "POSITION_NAME", "TouchUpAborted"]

TITLE = "Pipette"

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


class TouchUpAborted(RuntimeError):
    """The operator pressed Abort during the manual touch-up."""


def _guess(labels: list[str], word: str) -> int:
    """Index of the first label containing `word`, or 0."""
    for index, label in enumerate(labels):
        if word in label.lower():
            return index
    return 0


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
        panel = CardColumns([self._position_card(), self.jog,
                             self._offset_card(), self._calibration_card(),
                             self._touch_card(), self._details_card()], self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, SPACING, 0, 0)
        layout.setSpacing(SPACING)
        layout.addWidget(FeedRow(self.view, scroll_column(panel, PANEL_WIDTH)),
                         1)

        self.touch_up_requested.connect(self._begin_touch_up)
        session.camera_opened.connect(self._refresh_cameras)
        session.camera_closed.connect(self._refresh_cameras)
        session.robot_state_changed.connect(lambda _s: self._refresh())
        session.profile_changed.connect(lambda _p: self._refresh())
        session.tip_changed.connect(lambda _t: self._refresh())
        self._refresh_cameras()
        self._refresh()

    # -- construction --------------------------------------------------------

    def _position_card(self) -> QWidget:
        # Both folded: the ordinary run is "go to the stored position" and
        # never touches either. They are one click away when the disc has
        # moved and the position has to be taught again.
        self.jog = JogPanel(self.session, shortcut_host=self,
                            machine_controls=False,
                            collapsed=("move", "positions"), parent=self)
        self.jog.show_position_on(self.view)

        box = card(self)
        box.layout().addWidget(heading("Disc position", 2))
        row = QHBoxLayout()
        row.addWidget(QLabel("Upper camera"))
        self.over_choice = combo_box(self)
        self.over_choice.currentTextChanged.connect(self._show_over)
        row.addWidget(self.over_choice, 1)
        box.layout().addLayout(row)

        self.position_state = QLabel()
        self.position_state.setWordWrap(True)
        self.position_state.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addWidget(self.position_state)

        buttons = QHBoxLayout()
        # Short: three buttons' worth of words do not fit a panel this
        # wide, and the sentence above already says what is stored.
        self.goto_button = secondary_button("Go to last saved", self)
        self.goto_button.clicked.connect(self._goto)
        self.remember_button = primary_button("Set position", self)
        self.remember_button.clicked.connect(self._remember)
        buttons.addWidget(self.goto_button)
        buttons.addWidget(self.remember_button)
        buttons.addStretch(1)
        box.layout().addLayout(buttons)

        self.position_note = QLabel()
        self.position_note.setWordWrap(True)
        box.layout().addWidget(self.position_note)
        return box

    def _offset_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Starting offset", 2))
        box.layout().addWidget(self._wrapped(
            "A rough estimate of where the tip is from the camera's centre. "
            "A ruler is enough: it only has to land the tip inside the lower "
            "camera's view, and the calibration measures the real offset."))
        self.offset_note = QLabel()
        self.offset_note.setWordWrap(True)
        box.layout().addWidget(self.offset_note)

        self.dx = double_spin_box(self)
        self.dy = double_spin_box(self)
        for spin in (self.dx, self.dy):
            spin.setRange(-300.0, 300.0)
            spin.setDecimals(3)
            spin.setSingleStep(0.1)
            spin.setSuffix(" mm")
        self.tip_type = QLineEdit(self)
        self.tip_type.setPlaceholderText("e.g. vwr_200ul_xl")
        self.frames = spin_box(self)
        self.frames.setRange(1, 30)
        self.frames.setValue(7)
        self.verify = QCheckBox("verify after the correction", self)
        self.verify.setChecked(True)
        self.touch_up = QCheckBox("manual adjustment: nudge the tip onto the "
                                  "crosshair, then Done", self)
        self.touch_up.setChecked(True)

        self.reset_offset_button = secondary_button("Reset to defaults", self)
        self.reset_offset_button.setToolTip(
            f"dx {DEFAULT_OFFSET_MM[0]:g} mm, dy {DEFAULT_OFFSET_MM[1]:g} mm: "
            f"the usual offset on this rig.")
        self.reset_offset_button.clicked.connect(self._reset_offset)

        for label, widget, hint in (
                ("dx", self.dx, "camera centre to tip, in x."),
                ("dy", self.dy, "the same, in y."),
                ("Tip type", self.tip_type,
                 "recorded with the offset. Taken from the rack the tip came "
                 "from when the robot's record names one."),
                ("Frames", self.frames,
                 "per reading; detections are averaged and their spread is "
                 "reported.")):
            row = QHBoxLayout()
            name = QLabel(label)
            name.setMinimumWidth(90)
            row.addWidget(name)
            row.addWidget(widget)
            row.addStretch(1)
            box.layout().addLayout(row)
            note = QLabel(hint)
            note.setWordWrap(True)
            box.layout().addWidget(note)
            if widget is self.dy:
                box.layout().addWidget(self.reset_offset_button, 0,
                                       Qt.AlignmentFlag.AlignLeft)
        box.layout().addWidget(self.verify)
        box.layout().addWidget(self.touch_up)
        return box

    @staticmethod
    def _wrapped(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        return label

    def _reset_offset(self) -> None:
        self.dx.setValue(DEFAULT_OFFSET_MM[0])
        self.dy.setValue(DEFAULT_OFFSET_MM[1])

    def _calibration_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("XY calibration", 2))
        row = QHBoxLayout()
        row.addWidget(QLabel("Lower camera"))
        self.under_choice = QComboBox(self)
        row.addWidget(self.under_choice, 1)
        box.layout().addLayout(row)
        # What stops the run, in words: the gate the wizard's pages used to
        # be, now under the button it holds back.
        self.checks = QLabel()
        self.checks.setWordWrap(True)
        self.checks.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addWidget(self.checks)
        row = QHBoxLayout()
        self.start_button = primary_button("Start", self)
        self.start_button.clicked.connect(self._start)
        row.addWidget(self.start_button)
        row.addStretch(1)
        box.layout().addLayout(row)
        self.done = DoneBanner(self)
        box.layout().addWidget(self.done)
        return box

    def _touch_card(self) -> QWidget:
        """Shown only while the routine is waiting for the operator."""
        self.touch_box = card(self)
        self.touch_box.layout().addWidget(heading("Manual adjustment", 2))
        note = QLabel(
            f"The lower camera is live in the picture. Nudge the tip onto the "
            f"central crosshair — the step is {TOUCH_UP_STEP_MM:g} mm — and "
            f"press Done. Whatever you move is part of the offset. Abort "
            f"ends the calibration with nothing saved.")
        note.setWordWrap(True)
        self.touch_box.layout().addWidget(note)
        # Move open: nudging the tip onto the crosshair is the whole of this
        # block. Positions folded: nothing here is a place to return to.
        # XY only: a Z step here pushes the tip into the disc.
        self.touch_jog = JogPanel(self.session, shortcut_host=self,
                                  machine_controls=False,
                                  collapsed=("positions",), xy_only=True,
                                  parent=self.touch_box)
        self.touch_jog.show_position_on(self.view)
        self.touch_box.layout().addWidget(self.touch_jog)
        row = QHBoxLayout()
        self.accept_button = primary_button("Done", self)
        self.accept_button.clicked.connect(self._accept)
        self.abort_button = secondary_button("Abort", self)
        self.abort_button.clicked.connect(self._abort)
        row.addWidget(self.accept_button)
        row.addWidget(self.abort_button)
        row.addStretch(1)
        self.touch_box.layout().addLayout(row)
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
                f"Replace the stored {POSITION_NAME} {tuple(round(v, 2) for v in stored)} "
                f"with where the gantry is now? Every calibration after this "
                f"one drives here first.",
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
        worker.finished.connect(self._short_done)
        worker.failed.connect(self._short_failed)
        log.info("%s", what)
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
        """Everything that stops the routine from starting, as sentences.
        Empty means go."""
        session, problems = self.session, []
        profile = session.profile
        if session.robot is None:
            problems.append("no robot: connect it on the Profile page.")
        if profile is None:
            problems.append("no profile loaded.")
        else:
            if profile.pixel_map is None:
                problems.append("no pixel map in the profile: run the camera "
                                "sweep on the Camera tab first.")
            if self._stored_position() is None:
                problems.append(f"no {POSITION_NAME} position in the profile: "
                                f"set it under Disc position.")
        over, under = self._over_camera(), self._under_camera()
        if over is None:
            problems.append("the upper camera is not open.")
        if under is None:
            problems.append("the lower camera is not open (open it on the "
                            "Profile page; the routine checks its mode).")
        if over is not None and over is under:
            problems.append("the upper and lower camera are the same camera.")
        tip = session.tip
        if session.robot is not None and tip.attached is not True:
            problems.append(
                "the robot reports no tip on the pipette" if tip.attached is False
                else "the robot's tip state is unknown")
            problems[-1] += (": pick one up on the Robot & Deck page. The "
                             "offset is of the tip, and every tip seats "
                             "differently.")
        if self.dx.value() == 0.0 and self.dy.value() == 0.0 \
                and (profile is None or profile.calibration.pipette_offset is None):
            problems.append("no starting offset: measure it roughly with a "
                            "ruler and enter dx, dy.")
        return problems

    def _start(self) -> None:
        problems = self._readiness()
        if problems:
            for line in problems:
                self._append("not started: " + line)
            return
        if self._busy():
            return
        session = self.session
        robot, profile = session.robot, session.profile
        over, under = self._over_camera(), self._under_camera()
        target = profile.calibration.tip_target
        pmap = PixelMap.from_config(profile.pixel_map)
        stored = self._stored_position()
        current = (self.dx.value(), self.dy.value())
        frames, verify = self.frames.value(), self.verify.isChecked()
        tip_type = self.tip_type.text().strip() or None
        touch_up = self._touch_up if self.touch_up.isChecked() else None
        mock = session.mock

        self.run_log.clear()
        self.done.clear()
        self._outcome = ""
        self._last_line = ""
        self._result = None
        self._standin = False
        self._append(f"starting from ({current[0]:+.3f}, {current[1]:+.3f}) mm, "
                     f"{frames} frames per reading")

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

        worker = Worker(job)
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
        aborted = reason.startswith(TouchUpAborted.__name__)
        kind = "aborted" if aborted else "failed"
        detail = reason.split(": ", 1)[1] if ": " in reason else reason
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

        # The disc's jog panel only while nothing runs: hidden, its keys are
        # given up, and the touch-up's panel is the only thing that moves the
        # robot during a calibration.
        self.jog.setVisible(not running)
        # The key box is the panel's that has the keys now.
        keys = self.touch_jog if waiting else self.jog
        self.view.set_help(keys.help_lines)

        # the disc
        stored = self._stored_position()
        if stored is None:
            self.position_state.setText(
                f"No {POSITION_NAME} in the profile. Jog the disc's central "
                f"crosshair under the camera crosshair at the working height "
                f"(module height {self._module_height():g} mm), then Set "
                f"position.")
        else:
            self.position_state.setText(
                f"Stored {POSITION_NAME}: "
                f"({stored[0]:.2f}, {stored[1]:.2f}, {stored[2]:.2f}). Every "
                f"calibration drives here first. Go to last saved to check "
                f"the disc is still under the camera, or Set position again "
                f"from where the gantry stands now.")
        self.goto_button.setEnabled(connected and stored is not None and not busy)
        self.remember_button.setEnabled(connected and profile is not None and not busy)

        # the starting offset
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
                f"Profile: ({offset.dx:+.3f}, {offset.dy:+.3f}) mm, "
                f"{offset.method}, {when}"
                + (f", tip {offset.tip_type}" if offset.tip_type else "")
                + ". Used as the starting point unless changed below.")
        for widget in (self.dx, self.dy, self.reset_offset_button,
                       self.tip_type, self.frames, self.verify, self.touch_up,
                       self.under_choice, self.over_choice):
            widget.setEnabled(not running)
        if not running:
            self._prefill(offset)

        # the calibration
        problems = self._readiness()
        if running:
            self.checks.setText(f"Running: {self._last_line}"
                                if self._last_line else
                                "Running. The lower camera is in the picture.")
        else:
            text = ("Ready: press Start." if not problems else
                    "Before Start:\n" + "\n".join("• " + p for p in problems))
            self.checks.setText(f"{self._outcome}\n\n{text}" if self._outcome
                                else text)
        self.start_button.setEnabled(not running and not problems)
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
        if not self.tip_type.text():
            tip = self.session.tip
            name = None
            if tip.returnable and self.session.run_state is not None:
                entry = self.session.run_state.labware.get(tip.slot)
                name = entry.load_name if entry else None
            self.tip_type.setText(name or (offset.tip_type if offset else "") or "")

    # -- cameras -------------------------------------------------------------

    def _over_camera(self):
        return self.session.camera(self.over_choice.currentText())

    def _under_camera(self):
        return self.session.camera(self.under_choice.currentText())

    def _refresh_cameras(self, _label: str = "") -> None:
        labels = self.session.open_cameras
        for chooser, word in ((self.over_choice, "over"), (self.under_choice, "under")):
            current = chooser.currentText()
            chooser.blockSignals(True)
            chooser.clear()
            chooser.addItems(labels)
            # A choice that names its role is kept; one that was only the
            # fallback while the right camera was not open yet is replaced
            # by the right camera as soon as it is.
            named = any(word in label.lower() for label in labels)
            keep = current in labels and (word in current.lower() or not named)
            chooser.setCurrentIndex(labels.index(current) if keep
                                    else _guess(labels, word))
            chooser.blockSignals(False)
        self._show_over(self.over_choice.currentText())
        self._refresh()

    def _show_over(self, label: str) -> None:
        if not (self._busy() or not self.touch_box.isHidden()):
            self.view.set_camera(self.session.camera(label) if label else None)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Both: the upper camera finds the disc and the lower one measures
        # the tip, and the routine refuses to start without either.
        self.opener.ensure(self.session.upper_camera_label)
        self.opener.ensure(self.session.lower_camera_label)
        self._refresh_cameras()
