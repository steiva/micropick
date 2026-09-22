"""Measuring the pipette offset, in four steps — the notebook's section 4.

The steps are the notebook's cells in order, and each is a gate on the next:
put the calibration disc where the routine expects it, say what the routine
starts from, run it and finish by hand if the automatic correction is not
good enough, read what was saved.

**The disc position is a profile position, `tip_calib`.** The notebook
teaches it once with `jog(...)` and `profile.remember`, and drives to it with
`profile.where("tip_calib")` before every calibration. Step 1 is those two
acts: with no stored position, the jog panel and a Remember button; with one,
a button to go there and a button to re-teach it from wherever the gantry is
standing now. The routine's own drive to the stored position happens at the
start of the run, as in the notebook, so "go there" on step 1 is for looking,
not a step that can be forgotten.

**The routine needs a tip on the pipette, and asks the robot.** Every tip
seats differently, which is why this is redone after each pick-up; a
calibration taken with no tip measures nothing and a stored offset from it
would send every pick to the wrong place. `Session.tip` is the robot's record,
and the run is refused on anything but "a tip is on".

**The manual touch-up is the notebook's `jog_in_window`, in Qt.** The
workflow calls `manual_touch_up(robot, camera, view)` from its own thread and
carries on when it returns, reading the final pose to compute the offset. Here
the callback raises a signal to the GUI thread and waits on an Event; the
touch-up block appears with the lower camera live and a jog panel at 0.05 mm,
and Accept sets the event. Abort sets it too, with a flag the callback turns
into an exception, so the routine ends before it saves. Between Start and
Accept the routine is not cancellable — it is a handful of moves and a few
seconds of detection — and the log says which of those it is doing.

**The routine ends with the Z axis retracted**, as the notebook's cell does
after printing the result: the tip is at the module height over the disc,
and every next move starts by going somewhere else. An aborted or failed run
leaves the gantry where it stopped, so what happened can be seen.

**The profile is written by the workflow, on Accept.** `calibrate_pipette_offset`
saves the offset and the by-product homography together when given the
profile, and it is given the profile here because Accept is the operator
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
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QSpinBox, QStackedWidget,
                               QVBoxLayout, QWidget)

from ...config.schema import PipetteOffset
from ...core.calibration.pixel_map import PixelMap
from ...hardware.protocols import move_to, xyz
from ...workflows.calibrate_pipette import calibrate_pipette_offset
from ..session import Session
from ..theme import SPACING
from ..theme.factory import (card, combo_box, heading, primary_button,
                             scroll_column, secondary_button)
from ..tip_detector import STANDIN_NOTE, load_tip_detector
from ..widgets.camera_view import CameraView
from ..widgets.jog_panel import JogPanel
from ..workers import Worker

__all__ = ["PipetteCalibration", "POSITION_NAME", "TouchUpAborted"]

TITLE = "Pipette"

log = logging.getLogger(__name__)

PANEL_WIDTH = 420

# The profile position the disc is taught under. The notebook's name, kept:
# a profile taught from the notebook works here and the other way round.
POSITION_NAME = "tip_calib"

STEPS = ("Disc position", "Starting point", "Calibrate", "Result")

# The jog step for the touch-up, as the notebook sets it.
TOUCH_UP_STEP_MM = 0.05


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

        self.stack = QStackedWidget(self)
        self.stack.addWidget(self._position_step())
        self.stack.addWidget(self._parameters_step())
        self.stack.addWidget(self._run_step())
        self.stack.addWidget(self._result_step())

        self.step_label = heading("", 2)
        self.back_button = secondary_button("Back", self)
        self.back_button.clicked.connect(self._back)
        self.next_button = primary_button("Next", self)
        self.next_button.clicked.connect(self._next)

        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(self.back_button)
        footer.addWidget(self.next_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, SPACING, 0, 0)
        layout.setSpacing(SPACING)
        layout.addWidget(self.step_label)
        layout.addWidget(self.stack, 1)
        layout.addLayout(footer)

        self.touch_up_requested.connect(self._begin_touch_up)
        session.camera_opened.connect(self._refresh_cameras)
        session.camera_closed.connect(self._refresh_cameras)
        session.robot_state_changed.connect(lambda _s: self._refresh())
        session.profile_changed.connect(lambda _p: self._refresh())
        session.tip_changed.connect(lambda _t: self._refresh())
        self._refresh_cameras()
        self._refresh()

    # -- step 1: the disc ----------------------------------------------------

    def _position_step(self) -> QWidget:
        page = QWidget(self)
        self.view = CameraView(page)
        # Both folded: the ordinary run is "go to the stored position" and
        # never touches either. They are one click away when the disc has
        # moved and the position has to be taught again.
        self.jog = JogPanel(self.session, shortcut_host=page,
                            machine_controls=False,
                            collapsed=("move", "positions"), parent=page)

        panel = QWidget(page)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)

        box = card(panel)
        box.layout().addWidget(heading("Calibration disc", 2))
        row = QHBoxLayout()
        row.addWidget(QLabel("Upper camera"))
        self.over_choice = combo_box(panel)
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
        self.goto_button = secondary_button("Go there", panel)
        self.goto_button.clicked.connect(self._goto)
        self.remember_button = primary_button("Teach here", panel)
        self.remember_button.clicked.connect(self._remember)
        buttons.addWidget(self.goto_button)
        buttons.addWidget(self.remember_button)
        buttons.addStretch(1)
        box.layout().addLayout(buttons)

        self.position_note = QLabel()
        self.position_note.setWordWrap(True)
        box.layout().addWidget(self.position_note)
        column.addWidget(box)
        column.addWidget(self.jog, 1)

        body = QHBoxLayout(page)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(SPACING)
        body.addWidget(self.view, 1)
        body.addWidget(scroll_column(panel, PANEL_WIDTH))
        return page

    # -- step 2: starting point ----------------------------------------------

    def _parameters_step(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING)

        box = card(page)
        box.layout().addWidget(heading("Starting offset", 2))
        self.offset_note = QLabel()
        self.offset_note.setWordWrap(True)
        box.layout().addWidget(self.offset_note)

        self.dx = QDoubleSpinBox(page)
        self.dy = QDoubleSpinBox(page)
        for spin in (self.dx, self.dy):
            spin.setRange(-300.0, 300.0)
            spin.setDecimals(3)
            spin.setSingleStep(0.1)
            spin.setSuffix(" mm")
        self.tip_type = QLineEdit(page)
        self.tip_type.setPlaceholderText("e.g. vwr_200ul_xl")
        self.frames = QSpinBox(page)
        self.frames.setRange(1, 30)
        self.frames.setValue(7)
        self.verify = QCheckBox("verify after the correction", page)
        self.verify.setChecked(True)
        self.touch_up = QCheckBox("finish by hand: nudge the tip onto the "
                                  "crosshair, then Accept", page)
        self.touch_up.setChecked(True)

        for label, widget, hint in (
                ("dx", self.dx,
                 "camera reference to tip, x. On a new installation, a "
                 "ruler's worth; afterwards the last calibration's."),
                ("dy", self.dy, "the same, y."),
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
        box.layout().addWidget(self.verify)
        box.layout().addWidget(self.touch_up)
        layout.addWidget(box)

        box = card(page)
        box.layout().addWidget(heading("Before it runs", 2))
        row = QHBoxLayout()
        row.addWidget(QLabel("Lower camera"))
        self.under_choice = QComboBox(page)
        row.addWidget(self.under_choice, 1)
        box.layout().addLayout(row)
        self.checks = QLabel()
        self.checks.setWordWrap(True)
        self.checks.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addWidget(self.checks)
        layout.addWidget(box)
        layout.addStretch(1)
        return page

    # -- step 3: run ---------------------------------------------------------

    def _run_step(self) -> QWidget:
        page = QWidget(self)
        self.run_view = CameraView(page)

        panel = QWidget(page)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)

        box = card(panel)
        box.layout().addWidget(heading("Calibration", 2))
        self.run_log = QPlainTextEdit(panel)
        self.run_log.setReadOnly(True)
        self.run_log.setMaximumBlockCount(2000)
        self.run_log.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        box.layout().addWidget(self.run_log, 1)
        row = QHBoxLayout()
        self.start_button = primary_button("Start", panel)
        self.start_button.clicked.connect(self._start)
        row.addWidget(self.start_button)
        row.addStretch(1)
        box.layout().addLayout(row)
        column.addWidget(box, 1)

        # Shown only while the routine is waiting for the operator.
        self.touch_box = card(panel)
        self.touch_box.layout().addWidget(heading("Finish by hand", 2))
        note = QLabel(
            f"The lower camera is live on the left. Nudge the tip onto the "
            f"central crosshair — the step is {TOUCH_UP_STEP_MM:g} mm — and "
            f"press Accept. Whatever you move is part of the offset. Abort "
            f"ends the calibration with nothing saved.")
        note.setWordWrap(True)
        self.touch_box.layout().addWidget(note)
        # Move open: nudging the tip onto the crosshair is the whole of this
        # block. Positions folded: nothing here is a place to return to.
        self.touch_jog = JogPanel(self.session, shortcut_host=page,
                                  machine_controls=False,
                                  collapsed=("positions",),
                                  parent=self.touch_box)
        self.touch_box.layout().addWidget(self.touch_jog)
        row = QHBoxLayout()
        self.accept_button = primary_button("Accept", panel)
        self.accept_button.clicked.connect(self._accept)
        self.abort_button = secondary_button("Abort", panel)
        self.abort_button.clicked.connect(self._abort)
        row.addWidget(self.accept_button)
        row.addWidget(self.abort_button)
        row.addStretch(1)
        self.touch_box.layout().addLayout(row)
        self.touch_box.hide()
        column.addWidget(self.touch_box, 2)

        body = QHBoxLayout(page)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(SPACING)
        body.addWidget(self.run_view, 1)
        body.addWidget(scroll_column(panel, PANEL_WIDTH))
        return page

    # -- step 4: result ------------------------------------------------------

    def _result_step(self) -> QWidget:
        page = QWidget(self)
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING)

        box = card(page)
        box.layout().addWidget(heading("Offset", 2))
        self.result_text = QPlainTextEdit(page)
        self.result_text.setReadOnly(True)
        self.result_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.result_text.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        box.layout().addWidget(self.result_text, 1)
        self.result_note = QLabel()
        self.result_note.setWordWrap(True)
        self.result_note.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addWidget(self.result_note)
        layout.addWidget(box, 1)
        return page

    # -- step 1 actions ------------------------------------------------------

    def _stored_position(self):
        profile = self.session.profile
        if profile is None:
            return None
        return profile.positions.get(POSITION_NAME)

    def _goto(self) -> None:
        stored = self._stored_position()
        if stored is None or self.session.robot is None or self._busy():
            return
        robot, height = self.session.robot, self._module_height()

        def job():
            move_to(robot, stored, min_z_height=height - 0.1)
            return xyz(robot)

        self._run_short(Worker(job), f"going to {POSITION_NAME} {stored}")

    def _remember(self) -> None:
        if self.session.robot is None or self.session.profile is None or self._busy():
            return
        stored = self._stored_position()
        if stored is not None:
            answer = QMessageBox.question(
                self, "Re-teach the disc position",
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
                                f"teach it on step 1.")
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
            problems[-1] += (": pick one up on the Labware page. The offset is "
                             "of the tip, and every tip seats differently.")
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
            move_to(robot, stored, min_z_height=target.module_height - 0.1)
            time.sleep(0.5)
            result = calibrate_pipette_offset(
                robot, over, under, detector, pmap, target=target,
                current_offset=current, frames=frames, verify=verify,
                tip_type=tip_type, profile=profile, manual_touch_up=touch_up,
                log=log)
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
        self.touch_jog.set_step(TOUCH_UP_STEP_MM)
        self.touch_box.show()
        self._append("waiting for you: nudge the tip onto the crosshair, "
                     "then Accept")
        self._refresh()

    def _accept(self) -> None:
        if self.touch_jog.busy:
            self._append("a jog step is still in flight; Accept when it has "
                         "landed")
            return
        self.touch_box.hide()
        self._append("accepted")
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
        log.info("pipette offset calibrated: dx %+.3f dy %+.3f mm (%s)",
                 offset.dx, offset.dy, offset.method)
        text = str(result)
        if result.homography_report is not None:
            text += f"\n\n{result.homography_report}"
        self.result_text.setPlainText(text)
        profile = self.session.profile
        note = (f"Saved to {profile.path / 'calibration.json'}: offset "
                f"({offset.dx:+.3f}, {offset.dy:+.3f}) mm, method "
                f"{offset.method}"
                + (", with the homography" if result.homography is not None
                   else "") + ".")
        if standin:
            note = STANDIN_NOTE + "\n" + note
        self.result_note.setText(note)
        self.session.profile_changed.emit(profile)
        self.dx.setValue(offset.dx)
        self.dy.setValue(offset.dy)
        self.stack.setCurrentIndex(3)
        self._refresh()

    def _on_failed(self, reason: str) -> None:
        self._worker = None
        self.touch_box.hide()
        aborted = reason.startswith(TouchUpAborted.__name__)
        kind = "aborted" if aborted else "failed"
        detail = reason.split(": ", 1)[1] if ": " in reason else reason
        self._append(f"{kind}: {detail}")
        self._append("nothing was written to the profile")
        log.error("pipette calibration %s: %s", kind, detail)
        self._refresh()

    # -- navigation and display ----------------------------------------------

    def _busy(self) -> bool:
        return self._worker is not None and self._worker.running

    def _back(self) -> None:
        self.stack.setCurrentIndex(max(0, self.stack.currentIndex() - 1))
        self._refresh()

    def _next(self) -> None:
        self.stack.setCurrentIndex(
            min(self.stack.count() - 1, self.stack.currentIndex() + 1))
        self._refresh()

    def _append(self, text: str) -> None:
        self.run_log.appendPlainText(text)

    def _refresh(self) -> None:
        index = self.stack.currentIndex()
        busy = self._busy()
        waiting = not self.touch_box.isHidden()
        session = self.session
        profile = session.profile
        connected = session.robot is not None

        self.step_label.setText(f"Step {index + 1} of {len(STEPS)} — {STEPS[index]}")
        self.back_button.setEnabled(index > 0 and not busy)
        self.next_button.setEnabled(index < self.stack.count() - 1 and not busy)
        self.next_button.setVisible(index < self.stack.count() - 1)

        # step 1
        stored = self._stored_position()
        if stored is None:
            self.position_state.setText(
                f"No {POSITION_NAME} in the profile. Jog the disc's central "
                f"crosshair under the camera crosshair at the working height "
                f"(module height {self._module_height():g} mm), then Remember.")
            self.remember_button.setText("Teach this position")
        else:
            self.position_state.setText(
                f"Stored {POSITION_NAME}: "
                f"({stored[0]:.2f}, {stored[1]:.2f}, {stored[2]:.2f}). Every "
                f"calibration drives here first. Go there to check the disc "
                f"is still under the camera, or re-teach it from where the "
                f"gantry stands now.")
            self.remember_button.setText("Re-teach here")
        self.goto_button.setEnabled(connected and stored is not None and not busy)
        self.remember_button.setEnabled(connected and profile is not None and not busy)

        # step 2
        offset = profile.calibration.pipette_offset if profile else None
        if offset is None:
            self.offset_note.setText(
                "No offset in the profile. The routine drives to where it "
                "believes the target is before looking, so a rough value from "
                "a ruler is needed first; tens of millimetres off puts the "
                "tip outside the lower camera's view.")
        else:
            when = (offset.measured_at.strftime("%Y-%m-%d %H:%M")
                    if offset.measured_at else "date unknown")
            self.offset_note.setText(
                f"Profile: ({offset.dx:+.3f}, {offset.dy:+.3f}) mm, "
                f"{offset.method}, {when}"
                + (f", tip {offset.tip_type}" if offset.tip_type else "")
                + ". Used as the starting point unless changed below.")
        if not busy and index == 1:
            self._prefill(offset)
        problems = self._readiness()
        self.checks.setText("Ready." if not problems
                            else "\n".join("• " + p for p in problems))

        # step 3
        self.start_button.setEnabled(not busy and not problems)
        self.accept_button.setEnabled(waiting)
        self.abort_button.setEnabled(waiting)
        if index == 2:
            self.run_view.set_camera(self._under_camera() if (busy or waiting)
                                     else self._over_camera())

    def _prefill(self, offset) -> None:
        """The profile's offset into the spin boxes, once per visit to the
        step when they are still untouched. A value the operator typed stays."""
        if self.dx.value() == 0.0 and self.dy.value() == 0.0 and offset is not None:
            self.dx.setValue(offset.dx)
            self.dy.setValue(offset.dy)
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
        self.view.set_camera(self.session.camera(label) if label else None)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh_cameras()
