"""Calibrating the upper camera: one sweep, on one page.

The sweep is the upper camera's: the chooser offers no other, since the
lower camera looks up at the tip and a map of it is used by nothing.

A tab of its own, on the right with the tools used when needed: on a new
system, or after the camera was moved, refocused or set to another
resolution, and not before every run. It still comes before Tip calibration
(`pages/calibration_pipette.py`) the first time, since the tip offset is
measured through the pixel map this one produces; the Operation checklist
on the Profile page says so.

The picture, and one panel in the order of the work: Camera, Marker, the jog
panel, Sweep parameters, Sweep, Result. It was a wizard of four steps, as the
pipette page was, and became one page for the same reason: the order was
right, but each step hid what the next one needed. The first act is still a
physical one at the bench - centre the marker and set the working height -
and the panel says so first, because the whole sweep is planned around it:
`measure_scale` probes from wherever the gantry is standing. What stops the
sweep is said under Start, and the jog panel is hidden while it runs.

**The profile is written by a button on the last step, never by the sweep.**
That is what makes a cancellation safe rather than nearly safe: there is no
window in which a half-finished run could write. `run_sweep` raises `Cancelled`
before the fit is attempted, so a cancelled run has nothing to write either —
but the guarantee here does not rest on that, it rests on nothing writing
except an operator looking at a report.

The sweep runs in a `Worker`, which passes it the three hooks it already
declares: `on_progress` becomes the bar, `log` becomes lines in the panel and
in the application log, and `cancel` is the same `threading.Event` the workflow
has always taken. No adapter, and `workflows/calibrate_camera` learns nothing
about Qt.

The page watches for the marker while the operator puts it down
---------------------------------------------------------------
Placing the marker is done by eye, and the one thing the eye cannot check is
whether the camera sees it: a marker face down, or printed through the back
of the paper, is mirrored, and a mirrored marker is in no dictionary at all.
Undetected looks the same as badly lit. So while the page is on screen and
no sweep runs, the frame on the widget is passed to `gui.marker_watch` a few
times a second,
and what it finds is drawn over the picture — the outline it was detected
by, its own top edge, its first corner — with the id and which way up it is.
Not detected is reported as loudly as detected, with what it could be.

The detection runs in a `Worker` like any blocking call: 16 ms a frame at
2592x1944, four times that when the marker is not where it was expected and
the other dictionaries are tried. It reads the frame the view is already
showing rather than the camera, so what is drawn belongs to the picture it
is drawn on.

The result is a verdict first
-----------------------------
One line at the top of Result - Good, Acceptable or Redo - judged from the
FitReport on four things, each against two limits (`VERDICT_LIMITS`): the
held-out error, the largest residual, how far the recovered marker side is
from the printed one, and how much of the frame the sweep covered. The
worst of the four is the verdict, and the line names what made it so. The
limits are set from the first real sweep (DESIGN section 3: 24 µm held out,
the side 0.4 % off, the edges of the frame reached). The report and the two
plots that used to fill the step are behind Statistics: what an operator
needs to decide is the line; why is one click away.

The sweep draws what it is tracking
-----------------------------------
`calibrate_camera` already offers `on_frame(frame, corners, i, total)` at
every pose, and those corners are the ones being fitted — not a second
detection run beside it, which could disagree with the fit and would be
worse than nothing. The callback runs on the worker's thread, so it emits a
signal and the drawing happens here; the frame it is handed is dropped,
because the view is showing the live camera and what matters is where the
sweep believes the marker is. A pose where the tracker saw nothing draws
nothing, which is the pose worth noticing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFontDatabase, QPalette
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel,
                               QMessageBox, QPlainTextEdit, QProgressBar,
                               QVBoxLayout, QWidget)

from ...viz import markers
from ..auto_camera import CameraOpener
from ...workflows.calibrate_camera import Cancelled, calibrate_camera
from ..marker_watch import DICTIONARIES, MarkerWatch
from ..session import Session
from ..theme import SPACING
from ..theme.factory import (Section, card, combo_box, double_spin_box,
                             heading, primary_button, scroll_column,
                             secondary_button, spin_box)
from ..widgets.camera_view import CameraView
from ..widgets.card_columns import CardColumns
from ..widgets.done_banner import DoneBanner
from ..widgets.feed_row import FeedRow
from ..widgets.jog_panel import JogPanel
from ..workers import Worker

__all__ = ["CameraCalibration"]

TITLE = "Camera calibration"

log = logging.getLogger(__name__)

PANEL_WIDTH = 420

# The dictionaries live in `gui.marker_watch`, which is the other user of
# them: the live watch tries every one of them, and the parameters' combo
# offers the same list. Re-exported so nothing that imported it from
# here breaks.
__all__ += ["DICTIONARIES"]

# The sweep's usual settings, in one place: the marker printed for this rig
# and the grid and degree the first real sweep was judged at (DESIGN section
# 3). The boxes start at these, and Reset to defaults puts them back.
SWEEP_DEFAULTS = {"marker_side_mm": 6.8, "dictionary": "DICT_6X6_250",
                  "grid_n": 7, "degree": 3}

# How a fit is judged: (what, unit, good, acceptable, higher is better).
# Past `acceptable` is Redo. From the first real sweep, which held out at
# 24 µm with the side 0.4 % off: twice that is still usable, more is a
# sweep to repeat.
VERDICT_LIMITS = {
    "held-out error": ("µm", 40.0, 80.0, False),
    "largest residual": ("µm", 100.0, 200.0, False),
    "marker side": ("% off", 1.0, 2.0, False),
    "frame covered": ("%", 80.0, 60.0, True),
}
GOOD, ACCEPTABLE, REDO = "Good", "Acceptable", "Redo"
_RANK = {GOOD: 0, ACCEPTABLE: 1, REDO: 2}


@dataclass(frozen=True)
class Verdict:
    level: str                       # GOOD, ACCEPTABLE or REDO
    line: str                        # the one line for the top of Result
    checks: tuple                    # (what, value, unit, level) for each


def _grade(what: str, value: float) -> str:
    _unit, good, acceptable, higher = VERDICT_LIMITS[what]
    if higher:
        return GOOD if value >= good else ACCEPTABLE if value >= acceptable \
            else REDO
    return GOOD if value <= good else ACCEPTABLE if value <= acceptable \
        else REDO


def judge(report, printed_side_mm: float | None) -> Verdict:
    """Good, Acceptable or Redo for a fit, and the line that says why.

    The marker side is judged only when both the printed and the recovered
    sides are known; the other three always are."""
    values = {"held-out error": float(report.holdout_mean_um),
              "largest residual": float(report.resid_max_um),
              "frame covered": 100.0 * float(min(report.coverage_frac))}
    if report.track_side_mm and printed_side_mm:
        values["marker side"] = (100.0 * abs(report.track_side_mm
                                             - printed_side_mm)
                                 / printed_side_mm)
    checks = tuple((what, value, VERDICT_LIMITS[what][0], _grade(what, value))
                   for what, value in values.items())
    level = max((c[3] for c in checks), key=_RANK.__getitem__)

    def said(what, value, unit):
        return f"{what} {value:.1f} {unit}" if unit != "%" else \
            f"{value:.0f} % of the frame covered"

    if level == GOOD:
        line = f"{GOOD}: " + ", ".join(said(*c[:3]) for c in checks) + "."
    else:
        worst = [c for c in checks if c[3] == level]
        parts = []
        for what, value, unit, _ in worst:
            _u, good, acceptable, higher = VERDICT_LIMITS[what]
            limit = good if level == ACCEPTABLE else acceptable
            word = ("under" if higher else "over") if level == REDO else \
                ("below" if higher else "above")
            parts.append(f"{said(what, value, unit)} ({word} "
                         f"{limit:g} {'%' if unit == '%' else unit})")
        line = f"{level}: " + "; ".join(parts) + "."
        if level == REDO:
            line += " Centre the marker and sweep again."
    return Verdict(level, line, checks)


# How often the page looks for the marker. Three times a second is faster than
# a hand moves a marker and a twentieth of what the detection costs, so the
# grab loop and the view keep the rest.
WATCH_MS = 330

# Plot colours. A plot is its own surface, like the camera viewport, so these
# are fixed; what is not fixed is the ink, which comes from the palette's text
# colour. That one role does track the theme — measured, unlike the background
# roles qdarktheme leaves as placeholders.
CORNER_PEN = (94, 158, 235)      # blue
LIMIT_PEN = (235, 128, 80)       # orange


class CameraCalibration(QWidget):
    # Emitted from the sweep's thread with the corners it just tracked, or
    # None for a pose where the marker was not seen. A signal, so the
    # drawing happens on the thread that owns the widget.
    pose_tracked = Signal(object)

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._worker: Worker | None = None
        self._result = None                  # (pmap, report, sweep)
        self._watch = MarkerWatch()
        self.opener = CameraOpener(session, self)
        self._watch_worker: Worker | None = None
        self._sighting = None
        self._last_line = ""
        self._verdict: Verdict | None = None
        # How the last sweep ended, under Start until the next one.
        self._outcome = ""

        self.view = CameraView(self)
        # No Home and no Retract here: homing halfway through centring the
        # marker throws away the pose the sweep is about to be planned around.
        # Centring the marker is the D-pad; the stored positions are not
        # part of it, so they start folded.
        self.jog = JogPanel(self.session, shortcut_host=self,
                            machine_controls=False, collapsed=("positions",),
                            parent=self)
        self.jog.show_position_on(self.view)

        panel = CardColumns([self._camera_card(), self._marker_card(),
                             self.jog, self._parameters_card(),
                             self._sweep_card(), self._report_card()], self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2,
                                  SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addWidget(FeedRow(self.view, scroll_column(panel, PANEL_WIDTH)),
                         1)

        self.pose_tracked.connect(self._draw_tracked)

        self._watch_timer = QTimer(self)
        self._watch_timer.setInterval(WATCH_MS)
        self._watch_timer.timeout.connect(self._watch_tick)

        session.camera_opened.connect(self._refresh_cameras)
        session.camera_closed.connect(self._refresh_cameras)
        session.robot_state_changed.connect(lambda _s: self._refresh())
        # The window's Stop: the sweep stops at its next pose.
        session.stop_requested.connect(self._cancel)
        self._refresh_cameras()
        self._refresh()

    # -- construction --------------------------------------------------------

    def _camera_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Camera", 2))
        row = QHBoxLayout()
        self.camera_choice = combo_box(self)
        self.camera_choice.setToolTip("The sweep maps the upper camera, the "
                                      "one that looks down at the deck.")
        self.camera_choice.currentTextChanged.connect(self._show_camera)
        row.addWidget(self.camera_choice, 1)
        box.layout().addLayout(row)
        note = QLabel(
            "First, at the bench: put the marker under the crosshair and set "
            "the working height. The sweep is planned from this pose and this "
            "focus, and the map is only valid for them.")
        note.setWordWrap(True)
        box.layout().addWidget(note)
        return box

    def _marker_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Marker", 2))
        self.marker_state = QLabel("waiting for a frame…")
        self.marker_state.setWordWrap(True)
        self.marker_state.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addWidget(self.marker_state)
        # Appears only when the marker is found under a dictionary other than
        # the one the sweep is set to: one click, and it says which one it is
        # changing to.
        self.use_dictionary_button = secondary_button("", self)
        self.use_dictionary_button.clicked.connect(self._use_found_dictionary)
        self.use_dictionary_button.hide()
        box.layout().addWidget(self.use_dictionary_button)
        return box

    def _parameters_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Sweep parameters", 2))

        self.marker_side = double_spin_box(self)
        self.marker_side.setRange(1.0, 60.0)
        self.marker_side.setDecimals(2)
        self.marker_side.setSingleStep(0.1)
        self.marker_side.setSuffix(" mm")

        self.grid_n = spin_box(self)
        self.grid_n.setRange(4, 15)

        self.degree = spin_box(self)
        # Below 3 is refused by fit_pixel_map rather than silently useless:
        # radial distortion is cubic in image coordinates, so a quadratic
        # reduces exactly to the affine fit it is meant to improve on.
        self.degree.setRange(3, 5)

        self.dictionary = QComboBox(self)
        self.dictionary.addItems(DICTIONARIES)
        # The live watch reports against whatever is chosen here.
        self.dictionary.currentTextChanged.connect(lambda _t: self._refresh())

        for label, widget, hint in (
                ("Marker side", self.marker_side,
                 "the printed size. Never used by the fit — it is the "
                 "independent check on the recovered size."),
                ("Grid", self.grid_n,
                 "poses per axis; the sweep measures its own extent from the "
                 "marker, so this is only how densely it samples it."),
                ("Degree", self.degree,
                 "3 is the minimum that can represent radial distortion at "
                 "all. 4 does not help and is worse at the extremes."),
                ("Dictionary", self.dictionary,
                 "the wrong one detects nothing, which looks like bad "
                 "lighting.")):
            row = QHBoxLayout()
            name = QLabel(label)
            name.setMinimumWidth(110)
            row.addWidget(name)
            row.addWidget(widget)
            row.addStretch(1)
            box.layout().addLayout(row)
            note = QLabel(hint)
            note.setWordWrap(True)
            box.layout().addWidget(note)

        self.defaults_button = secondary_button("Reset to defaults", self)
        self.defaults_button.setToolTip(
            f"Marker side {SWEEP_DEFAULTS['marker_side_mm']:g} mm, "
            f"{SWEEP_DEFAULTS['dictionary']}, grid {SWEEP_DEFAULTS['grid_n']}, "
            f"degree {SWEEP_DEFAULTS['degree']}.")
        self.defaults_button.clicked.connect(self._reset_parameters)
        box.layout().addWidget(self.defaults_button, 0,
                               Qt.AlignmentFlag.AlignLeft)
        # Quietly: the dictionary's change refreshes cards not built yet.
        self.dictionary.blockSignals(True)
        self._reset_parameters()
        self.dictionary.blockSignals(False)
        return box

    def _reset_parameters(self) -> None:
        """The boxes back to SWEEP_DEFAULTS."""
        self.marker_side.setValue(SWEEP_DEFAULTS["marker_side_mm"])
        self.dictionary.setCurrentText(SWEEP_DEFAULTS["dictionary"])
        self.grid_n.setValue(SWEEP_DEFAULTS["grid_n"])
        self.degree.setValue(SWEEP_DEFAULTS["degree"])

    def _sweep_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Sweep", 2))
        # What stops the sweep, in words: the gate the wizard's steps used
        # to be, under the button it holds back.
        self.checks = QLabel()
        self.checks.setWordWrap(True)
        self.checks.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addWidget(self.checks)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        box.layout().addWidget(self.progress)

        row = QHBoxLayout()
        self.start_button = primary_button("Start sweep", self)
        self.start_button.clicked.connect(self._start)
        self.cancel_button = secondary_button("Cancel", self)
        self.cancel_button.clicked.connect(self._cancel)
        row.addWidget(self.start_button)
        row.addWidget(self.cancel_button)
        row.addStretch(1)
        box.layout().addLayout(row)

        self.run_log = QPlainTextEdit(self)
        self.run_log.setReadOnly(True)
        self.run_log.setMaximumBlockCount(2000)
        self.run_log.setMinimumHeight(140)
        self.run_log.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        # Folded, as on the pipette page: the line under Start says what the
        # sweep is doing, and every line also goes to the application log.
        self.details = Section("Details", collapsed=True, parent=self)
        self.details.body.layout().addWidget(self.run_log)
        # A green check when a sweep has ended well, and after Save.
        self.done = DoneBanner(self)
        box.layout().addWidget(self.done)
        box.layout().addWidget(self.details)
        return box

    def _report_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Result", 2))
        self.verdict = QLabel("No sweep on this page yet.")
        self.verdict.setWordWrap(True)
        self.verdict.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addWidget(self.verdict)
        self.stats_button = secondary_button("Statistics", self)
        self.stats_button.setToolTip("The full report and the coverage and "
                                     "residual plots.")
        self.stats_button.clicked.connect(self._show_statistics)
        self.stats_button.hide()
        box.layout().addWidget(self.stats_button, 0, Qt.AlignmentFlag.AlignLeft)

        self.write_note = QLabel()
        self.write_note.setWordWrap(True)
        box.layout().addWidget(self.write_note)
        self.save_button = primary_button("Save to profile", self)
        self.save_button.clicked.connect(self._save)
        box.layout().addWidget(self.save_button)
        self._build_statistics()
        return box

    def _build_statistics(self) -> None:
        """The report and the plots, in a window of their own."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Sweep statistics")
        dialog.resize(760, 860)
        column = QVBoxLayout(dialog)
        self.report_text = QPlainTextEdit(dialog)
        self.report_text.setReadOnly(True)
        self.report_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.report_text.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.report_text.setMaximumHeight(170)
        column.addWidget(self.report_text)
        column.addWidget(heading("Coverage", 3))
        self.coverage_plot = self._plot("u, px", "v, px")
        column.addWidget(self.coverage_plot, 1)
        column.addWidget(heading("Residual per pose", 3))
        self.resid_plot = self._plot("pose", "residual, µm")
        column.addWidget(self.resid_plot, 1)
        self.statistics = dialog

    def _show_statistics(self) -> None:
        self.statistics.show()
        self.statistics.raise_()

    def _plot(self, x_label: str, y_label: str):
        """A transparent plot, inked with the palette's text colour.

        Transparent so the card's surface shows through in either theme, and
        the text role because it is the one palette role qdarktheme actually
        varies between light and dark — the background roles it installs are
        placeholders that are the same grey in both.
        """
        widget = pg.PlotWidget(background=None)
        ink = self.palette().color(QPalette.ColorRole.Text)
        for axis in ("left", "bottom"):
            widget.getAxis(axis).setPen(ink)
            widget.getAxis(axis).setTextPen(ink)
        widget.setLabel("bottom", x_label)
        widget.setLabel("left", y_label)
        widget.showGrid(x=True, y=True, alpha=0.2)
        return widget

    # -- watching for the marker ---------------------------------------------

    def _watch_tick(self) -> None:
        """One look, if there is something to look at and nothing in flight."""
        if not self.isVisible() or self._running():
            return
        if self._watch_worker is not None and self._watch_worker.running:
            return
        frame = self.view.held_frame
        if frame is None:
            return
        watch, wanted = self._watch, self.dictionary.currentText()
        worker = Worker(watch.look, frame, wanted,
                        what="looking for the marker")
        self._watch_worker = worker
        # Bound methods, not lambdas: Qt takes a connection's thread from the
        # receiver, and these touch the view.
        worker.finished.connect(self._watch_done)
        worker.failed.connect(self._watch_failed)
        worker.start()

    def _watch_done(self, sighting) -> None:
        if self.sender() is not self._watch_worker:
            return
        self._watch_worker = None
        self._sighting = sighting
        self.view.set_overlay_items(sighting.items())
        self._show_sighting()

    def _watch_failed(self, reason: str) -> None:
        if self.sender() is not self._watch_worker:
            return
        self._watch_worker = None
        self._sighting = None
        self.view.set_overlay_items([])
        self.marker_state.setText(reason)
        self.use_dictionary_button.hide()

    def _show_sighting(self) -> None:
        sighting = self._sighting
        if sighting is None:
            self.marker_state.setText(
                "Open a camera to see whether the marker is detected."
                if self._camera() is None else "waiting for a frame…")
            self.use_dictionary_button.hide()
            return
        self.marker_state.setText(sighting.describe())
        wrong = sighting.found and not sighting.as_asked
        self.use_dictionary_button.setVisible(wrong)
        if wrong:
            self.use_dictionary_button.setText(f"Use {sighting.dictionary}")

    def _use_found_dictionary(self) -> None:
        """Set the parameters' dictionary to the one the marker was found
        in."""
        if self._sighting is None or not self._sighting.found:
            return
        index = self.dictionary.findText(self._sighting.dictionary)
        if index >= 0:
            self.dictionary.setCurrentIndex(index)
            log.info("dictionary set to %s, where the marker was found",
                     self._sighting.dictionary)

    # -- running -------------------------------------------------------------

    def _camera(self):
        return self.session.camera(self.camera_choice.currentText())

    def _running(self) -> bool:
        return self._worker is not None and self._worker.running

    def _readiness(self) -> list[str]:
        """What stops the sweep from starting, as sentences."""
        out = []
        if self.session.robot is None:
            out.append("no robot: connect it on the Profile page.")
        if self._camera() is None:
            upper = self.session.upper_camera_label
            out.append(f"the upper camera ({upper}) is not open: open it with "
                       f"its button in the status bar." if upper else
                       "no upper camera in the profile.")
        return out

    def _start(self) -> None:
        camera = self._camera()
        if self._readiness() or self._running():
            return

        detector = cv2.aruco.ArucoDetector(
            cv2.aruco.getPredefinedDictionary(
                DICTIONARIES[self.dictionary.currentText()]),
            cv2.aruco.DetectorParameters())

        self.run_log.clear()
        self._outcome = ""
        self._last_line = ""
        self.progress.setRange(0, 0)         # indeterminate until the first pose
        self._result = None
        self._verdict = None
        self.done.clear()
        self.verdict.setText("Sweeping: the result comes when the fit is "
                             "done.")
        self.stats_button.hide()
        self.statistics.hide()
        self.view.set_overlay_items([])
        self._append(f"sweeping with grid {self.grid_n.value()}, "
                     f"degree {self.degree.value()}, marker "
                     f"{self.marker_side.value():g} mm")

        # calibrate_camera declares on_progress, cancel and log, so the worker
        # passes all three without an adapter. on_frame is named outright,
        # because it is this page's and not the convention's.
        worker = Worker(calibrate_camera, self.session.robot, camera, detector,
                        marker_side_mm=self.marker_side.value(),
                        grid_n=self.grid_n.value(),
                        degree=self.degree.value(),
                        on_frame=self._on_pose,
                        what="calibrating the camera")
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.message.connect(self._on_message)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.start()
        self._refresh()

    def _on_pose(self, frame, corners, index, total) -> None:
        """The sweep's own hook, on the sweep's own thread. Emits and returns.

        The frame is not carried across: the view is already showing the
        live camera, and a 15 MB array per pose in Qt's event queue is the
        mistake `CameraView` exists to avoid.
        """
        self.pose_tracked.emit(None if corners is None else np.asarray(corners))

    def _draw_tracked(self, corners) -> None:
        if corners is None:
            self.view.set_overlay_items([])
            return
        self.view.set_overlay_items(
            markers.items(corners, self._sweep_marker_id(),
                          label=f"tracked  ·  {markers.orientation(corners)}"))

    def _sweep_marker_id(self) -> int | None:
        """What the watch saw, if it saw anything: the sweep adopts the marker
        nearest the centre and does not report which id that was."""
        return getattr(self._sighting, "marker_id", None)

    def _cancel(self) -> None:
        if self._worker is None or not self._worker.running:
            return
        # request_cancel is called, never connected: the worker's own event
        # loop is blocked inside the sweep. The workflow sees the event between
        # poses, so the gantry finishes the move it is making first.
        self._worker.request_cancel()
        self._append("cancelling — the sweep stops at the next pose")
        self.cancel_button.setEnabled(False)

    def _on_progress(self, done: int, total: int) -> None:
        if self.progress.maximum() != total:
            self.progress.setRange(0, total)
        self.progress.setValue(done)

    def _on_message(self, text: str) -> None:
        self._append(text)

    def _on_finished(self, result) -> None:
        self._worker = None
        self._result = result
        self.view.set_overlay_items([])
        pmap, report, sweep = result
        self._append("fit complete")
        log.info("camera calibration fitted:\n%s", report)
        self._verdict = judge(report, self.marker_side.value())
        log.info("camera calibration verdict: %s", self._verdict.line)
        self.report_text.setPlainText(f"{self._verdict.line}\n\n{report}")
        self._draw_coverage(report, sweep)
        self._draw_residuals(report)
        self.verdict.setText(f"<b>{self._verdict.line}</b>")
        self.stats_button.show()
        self._outcome = f"Finished: {self._verdict.level}. See Result."
        # A fit to redo is the end of the sweep, not a thing done.
        if self._verdict.level != REDO:
            self.done.show_done(f"Sweep finished - {self._verdict.level}. "
                                f"Save to profile keeps the map.")
        self._describe_write(pmap)
        self._refresh()
        # The sweep moved the gantry with the panel hidden.
        self.jog.refresh_position()

    def _on_failed(self, reason: str) -> None:
        """A sweep that ended early, cancelled or otherwise.

        It always says to centre the marker again, and that is not politeness. A run
        that stops part way leaves the gantry at the pose it reached, and
        `measure_scale` probes from wherever the gantry is standing — so the
        next attempt begins with the marker somewhere near a frame edge or off
        it entirely, and fails with "marker not detected at the starting pose".
        Recentring is a physical act and the operator is the only one who can
        do it.
        """
        self._worker = None
        self.done.clear()
        self.view.set_overlay_items([])
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        # A cancellation is not a failure and reads as one if it is not named.
        cancelled = isinstance(getattr(self.sender(), "error", None), Cancelled)
        kind = "cancelled" if cancelled else "failed"
        detail = reason
        self._append(f"{kind}: {detail}")
        self._append("nothing was written to the profile")
        self._append("the gantry is no longer where this sweep started — "
                     "centre the marker again before the next sweep")
        self._outcome = (f"Sweep {kind}: {detail}\nNothing was written. The "
                         f"gantry is no longer where the sweep started: centre "
                         f"the marker again before the next one.")
        log.info("camera calibration %s: %s", kind, detail)
        self._refresh()
        self.jog.refresh_position()

    # -- the plots -----------------------------------------------------------

    def _draw_coverage(self, report, sweep) -> None:
        """Where in the frame the map was actually fitted.

        Straight out of SweepData: every detected corner of every pose. It
        answers the question the residual numbers cannot — a map with a fine
        residual over a third of the frame is still wrong everywhere else.
        """
        plot = self.coverage_plot
        plot.clear()
        points = np.asarray(sweep.track_px).reshape(-1, 2)
        plot.plot(points[:, 0], points[:, 1], pen=None, symbol="o",
                  symbolSize=4, symbolBrush=CORNER_PEN, symbolPen=None)

        width, height = sweep.image_size
        u0, v0, u1, v1 = report.coverage
        for (x0, y0, x1, y1), colour in (((0, 0, width, height), LIMIT_PEN),
                                         ((u0, v0, u1, v1), CORNER_PEN)):
            plot.plot([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0],
                      pen=pg.mkPen(colour, width=1))
        plot.setTitle(f"{report.coverage_frac[0]*100:.0f} % x "
                      f"{report.coverage_frac[1]*100:.0f} % of the frame")
        # v grows downward in image coordinates.
        plot.getViewBox().invertY(True)

    def _draw_residuals(self, report) -> None:
        """One point per corner per pose.

        The mean and the max cannot tell one pose that flew off from all of
        them drifting a little, and those are different faults: the first is a
        sweep to run again, the second a loose mount or a focus that moved.
        """
        plot = self.resid_plot
        plot.clear()
        resid = report.resid_um
        if resid is None:
            plot.setTitle("this build's fit reports no per-pose residuals")
            return
        resid = np.asarray(resid)
        poses = np.repeat(np.arange(resid.shape[0]), resid.shape[1])
        plot.plot(poses, resid.reshape(-1), pen=None, symbol="o", symbolSize=4,
                  symbolBrush=CORNER_PEN, symbolPen=None)
        plot.plot([0, resid.shape[0] - 1],
                  [report.holdout_mean_um, report.holdout_mean_um],
                  pen=pg.mkPen(LIMIT_PEN, width=1, style=Qt.PenStyle.DashLine))
        plot.setTitle(f"mean {report.resid_mean_um:.1f}  "
                      f"max {report.resid_max_um:.1f} µm   "
                      f"(dashed: held out {report.holdout_mean_um:.1f})")

    # -- writing -------------------------------------------------------------

    def _describe_write(self, pmap) -> None:
        profile = self.session.profile
        config = pmap.to_config()
        if profile is None:
            self.write_note.setText("No profile is loaded, so there is nowhere "
                                    "to save this.")
            return
        self.write_note.setText(
            f"Will write to {profile.path / 'calibration.json'}:\n"
            f"  degree {config.degree}, {config.n_poses} poses, "
            f"image_size {config.image_size[0]}×{config.image_size[1]}, "
            f"sweep_z {config.sweep_z:.2f}\n"
            f"The previous calibration is archived under history/ first.")

    def _save(self) -> None:
        if self._result is None or self.session.profile is None:
            return
        if self._verdict is not None and self._verdict.level == REDO:
            answer = QMessageBox.question(
                self, "Save to profile",
                f"{self._verdict.line}\n\nSave this map anyway? Everything "
                f"that turns a pixel into a deck position will use it.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        pmap, _report, _sweep = self._result
        profile = self.session.profile
        config = pmap.to_config()
        profile.calibration.pixel_map = config
        # save_calibration archives what is there before replacing it: a sweep
        # costs minutes of robot time and yesterday's may have been better.
        profile.save_calibration()
        log.info("pixel map written to %s (degree %d, %s poses, %dx%d)",
                 profile.path, config.degree, config.n_poses,
                 config.image_size[0], config.image_size[1])
        self.write_note.setText(
            f"Written to {profile.path / 'calibration.json'}: degree "
            f"{config.degree}, {config.n_poses} poses, image_size "
            f"{config.image_size[0]}×{config.image_size[1]}.")
        self.save_button.setEnabled(False)
        self.done.show_done("The pixel map is saved to the profile.")
        self.session.profile_changed.emit(profile)

    # -- display -------------------------------------------------------------

    def _refresh(self) -> None:
        running = self._running()
        # Hidden while the sweep runs: its keys are given up, and nothing but
        # the sweep moves the gantry.
        self.jog.setVisible(not running)
        for widget in (self.camera_choice, self.marker_side, self.grid_n,
                       self.degree, self.dictionary, self.defaults_button):
            widget.setEnabled(not running)
        if not running:
            self._show_sighting()

        problems = self._readiness()
        if running:
            self.checks.setText(f"Sweeping: {self._last_line}"
                                if self._last_line else "Sweeping…")
        else:
            text = ("Ready: press Start sweep." if not problems else
                    "Before Start:\n" + "\n".join("• " + p for p in problems))
            self.checks.setText(f"{self._outcome}\n\n{text}" if self._outcome
                                else text)
        self.start_button.setEnabled(not problems and not running)
        self.cancel_button.setEnabled(running)
        self.save_button.setEnabled(self._result is not None
                                    and self.session.profile is not None)
        self.save_button.setVisible(self._result is not None)

    def _append(self, text: str) -> None:
        """A line of the sweep: into Details and the application log, and as
        the latest word under Start while it runs."""
        self.run_log.appendPlainText(text)
        log.info("camera calibration: %s", text)
        self._last_line = text
        if self._running():
            self.checks.setText(f"Sweeping: {text}")

    # -- cameras -------------------------------------------------------------

    def _refresh_cameras(self, _label: str = "") -> None:
        # The upper camera only: the pixel map is that camera's, and a sweep
        # through the lower one, which looks up at the tip, maps nothing the
        # rest of the application uses.
        upper = self.session.upper_camera_label
        labels = [label for label in self.session.open_cameras
                  if label == upper]
        current = self.camera_choice.currentText()
        self.camera_choice.blockSignals(True)
        self.camera_choice.clear()
        self.camera_choice.addItems(labels)
        if current in labels:
            self.camera_choice.setCurrentIndex(labels.index(current))
        self.camera_choice.blockSignals(False)
        self._show_camera(self.camera_choice.currentText())
        self._refresh()

    def _show_camera(self, label: str) -> None:
        camera = self.session.camera(label) if label else None
        self.view.set_camera(camera)
        # Last frame's marker belongs to last frame's camera.
        self._sighting = None
        self.view.set_overlay_items([])
        self._show_sighting()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # The sweep is this camera's, so arriving here is asking for it.
        self.opener.ensure(self.session.upper_camera_label)
        self._refresh_cameras()
        self._watch_timer.start()

    def hideEvent(self, event) -> None:
        """A page nobody is looking at does not detect. The worker in flight
        is left to finish; its result lands on a hidden widget and costs one
        repaint that never happens."""
        self._watch_timer.stop()
        super().hideEvent(event)
