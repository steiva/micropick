"""Calibrating the upper camera: one sweep, in four steps.

The steps are the four things an operator actually does, in order, and each one
is a gate on the next: centre the marker and set the working height, choose the
parameters, run it, read the result. A single screen with a Start button hides
that the first of those is a physical act at the bench, and that it is the one
the whole sweep is planned around — `measure_scale` probes from wherever the
gantry is standing.

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
"""

from __future__ import annotations

import logging

import cv2
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase, QPalette
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel,
                               QPlainTextEdit, QProgressBar, QSpinBox,
                               QStackedWidget, QVBoxLayout, QWidget)

from ...workflows.calibrate_camera import Cancelled, calibrate_camera
from ..session import Session
from ..theme import SPACING
from ..theme.factory import card, heading, primary_button, secondary_button
from ..widgets.camera_view import CameraView
from ..widgets.jog_panel import JogPanel
from ..workers import Worker

__all__ = ["CalibrationPage"]

TITLE = "Calibration"

log = logging.getLogger(__name__)

PANEL_WIDTH = 420

# The dictionary the marker was printed from. Offered rather than assumed:
# nothing in the profile records it, `MarkerScene` renders 6X6_250, and a
# detector built on the wrong dictionary finds nothing at all — which looks
# exactly like bad lighting.
DICTIONARIES = {
    "DICT_6X6_250": cv2.aruco.DICT_6X6_250,
    "DICT_4X4_50": cv2.aruco.DICT_4X4_50,
    "DICT_5X5_250": cv2.aruco.DICT_5X5_250,
    "DICT_APRILTAG_36h11": cv2.aruco.DICT_APRILTAG_36h11,
}

STEPS = ("Centre the marker", "Parameters", "Sweep", "Report")

# Plot colours. A plot is its own surface, like the camera viewport, so these
# are fixed; what is not fixed is the ink, which comes from the palette's text
# colour. That one role does track the theme — measured, unlike the background
# roles qdarktheme leaves as placeholders.
CORNER_PEN = (94, 158, 235)      # blue
LIMIT_PEN = (235, 128, 80)       # orange


class CalibrationPage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._worker: Worker | None = None
        self._result = None                  # (pmap, report, sweep)

        self.stack = QStackedWidget(self)
        self.stack.addWidget(self._centre_step())
        self.stack.addWidget(self._parameters_step())
        self.stack.addWidget(self._run_step())
        self.stack.addWidget(self._report_step())

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
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2, SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addWidget(heading(TITLE, 1))
        layout.addWidget(self.step_label)
        layout.addWidget(self.stack, 1)
        layout.addLayout(footer)

        session.camera_opened.connect(self._refresh_cameras)
        session.camera_closed.connect(self._refresh_cameras)
        session.robot_state_changed.connect(lambda _s: self._refresh())
        self._refresh_cameras()
        self._refresh()

    # -- step 1: centre ------------------------------------------------------

    def _centre_step(self) -> QWidget:
        page = QWidget(self)
        self.view = CameraView(page)
        # No Home and no Retract here: homing halfway through centring the
        # marker throws away the pose the sweep is about to be planned around.
        self.jog = JogPanel(self.session, shortcut_host=page,
                            machine_controls=False, parent=page)

        panel = QWidget(page)
        panel.setFixedWidth(PANEL_WIDTH)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)

        box = card(panel)
        box.layout().addWidget(heading("Camera", 2))
        row = QHBoxLayout()
        self.camera_choice = QComboBox(panel)
        self.camera_choice.currentTextChanged.connect(self._show_camera)
        row.addWidget(self.camera_choice, 1)
        box.layout().addLayout(row)
        note = QLabel(
            "Put the marker under the crosshair and set the working height. "
            "The sweep is planned from this pose and this focus, and the map "
            "is only valid for them.")
        note.setWordWrap(True)
        box.layout().addWidget(note)
        column.addWidget(box)
        column.addWidget(self.jog, 1)

        body = QHBoxLayout(page)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(SPACING)
        body.addWidget(self.view, 1)
        body.addWidget(panel)
        return page

    # -- step 2: parameters --------------------------------------------------

    def _parameters_step(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING)

        box = card(page)
        box.layout().addWidget(heading("Sweep parameters", 2))

        self.marker_side = QDoubleSpinBox(page)
        self.marker_side.setRange(1.0, 60.0)
        self.marker_side.setDecimals(2)
        self.marker_side.setSingleStep(0.1)
        self.marker_side.setValue(6.8)
        self.marker_side.setSuffix(" mm")

        self.grid_n = QSpinBox(page)
        self.grid_n.setRange(4, 15)
        self.grid_n.setValue(7)

        self.degree = QSpinBox(page)
        # Below 3 is refused by fit_pixel_map rather than silently useless:
        # radial distortion is cubic in image coordinates, so a quadratic
        # reduces exactly to the affine fit it is meant to improve on.
        self.degree.setRange(3, 5)
        self.degree.setValue(3)

        self.dictionary = QComboBox(page)
        self.dictionary.addItems(DICTIONARIES)

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

        layout.addWidget(box)
        layout.addStretch(1)
        return page

    # -- step 3: run ---------------------------------------------------------

    def _run_step(self) -> QWidget:
        page = QWidget(self)
        self.run_view = CameraView(page)

        panel = QWidget(page)
        panel.setFixedWidth(PANEL_WIDTH)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)

        box = card(panel)
        box.layout().addWidget(heading("Sweep", 2))
        self.progress = QProgressBar(panel)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        box.layout().addWidget(self.progress)

        self.run_log = QPlainTextEdit(panel)
        self.run_log.setReadOnly(True)
        self.run_log.setMaximumBlockCount(2000)
        self.run_log.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        box.layout().addWidget(self.run_log, 1)

        row = QHBoxLayout()
        self.start_button = primary_button("Start sweep", panel)
        self.start_button.clicked.connect(self._start)
        self.cancel_button = secondary_button("Cancel", panel)
        self.cancel_button.clicked.connect(self._cancel)
        row.addWidget(self.start_button)
        row.addWidget(self.cancel_button)
        row.addStretch(1)
        box.layout().addLayout(row)
        column.addWidget(box, 1)

        body = QHBoxLayout(page)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(SPACING)
        body.addWidget(self.run_view, 1)
        body.addWidget(panel)
        return page

    # -- step 4: report ------------------------------------------------------

    def _report_step(self) -> QWidget:
        page = QWidget(self)
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING)

        left = card(page)
        left.setFixedWidth(PANEL_WIDTH)
        left.layout().addWidget(heading("Fit", 2))
        self.report_text = QPlainTextEdit(page)
        self.report_text.setReadOnly(True)
        self.report_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.report_text.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        left.layout().addWidget(self.report_text, 1)

        self.write_note = QLabel()
        self.write_note.setWordWrap(True)
        left.layout().addWidget(self.write_note)
        self.save_button = primary_button("Save to profile", page)
        self.save_button.clicked.connect(self._save)
        left.layout().addWidget(self.save_button)

        right = card(page)
        right.layout().addWidget(heading("Coverage", 3))
        self.coverage_plot = self._plot("u, px", "v, px")
        right.layout().addWidget(self.coverage_plot, 1)
        right.layout().addWidget(heading("Residual per pose", 3))
        self.resid_plot = self._plot("pose", "residual, µm")
        right.layout().addWidget(self.resid_plot, 1)

        layout.addWidget(left)
        layout.addWidget(right, 1)
        return page

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

    # -- running -------------------------------------------------------------

    def _camera(self):
        return self.session.camera(self.camera_choice.currentText())

    def _start(self) -> None:
        camera = self._camera()
        if self.session.robot is None or camera is None:
            self._append("connect the robot and open a camera first")
            return
        if self._worker is not None and self._worker.running:
            return

        detector = cv2.aruco.ArucoDetector(
            cv2.aruco.getPredefinedDictionary(
                DICTIONARIES[self.dictionary.currentText()]),
            cv2.aruco.DetectorParameters())

        self.run_log.clear()
        self.progress.setRange(0, 0)         # indeterminate until the first pose
        self._result = None
        self._append(f"sweeping with grid {self.grid_n.value()}, "
                     f"degree {self.degree.value()}, marker "
                     f"{self.marker_side.value():g} mm")

        # calibrate_camera declares on_progress, cancel and log, so the worker
        # passes all three without an adapter.
        worker = Worker(calibrate_camera, self.session.robot, camera, detector,
                        marker_side_mm=self.marker_side.value(),
                        grid_n=self.grid_n.value(),
                        degree=self.degree.value())
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.message.connect(self._on_message)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.start()
        self._refresh()

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
        pmap, report, sweep = result
        self._append("fit complete")
        log.info("camera calibration fitted:\n%s", report)
        self.report_text.setPlainText(str(report))
        self._draw_coverage(report, sweep)
        self._draw_residuals(report)
        self._describe_write(pmap)
        self.stack.setCurrentIndex(3)
        self._refresh()

    def _on_failed(self, reason: str) -> None:
        """A sweep that ended early, cancelled or otherwise.

        It always says to go back to step 1, and that is not politeness. A run
        that stops part way leaves the gantry at the pose it reached, and
        `measure_scale` probes from wherever the gantry is standing — so the
        next attempt begins with the marker somewhere near a frame edge or off
        it entirely, and fails with "marker not detected at the starting pose".
        Recentring is a physical act and the operator is the only one who can
        do it.
        """
        self._worker = None
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        # A cancellation is not a failure and reads as one if it is not named.
        cancelled = reason.startswith(Cancelled.__name__)
        kind = "cancelled" if cancelled else "failed"
        detail = reason.split(": ", 1)[1] if cancelled and ": " in reason else reason
        self._append(f"{kind}: {detail}")
        self._append("nothing was written to the profile")
        self._append("the gantry is no longer where this sweep started — "
                     "go back to step 1 and centre the marker again")
        log.info("camera calibration %s: %s", kind, detail)
        self._refresh()

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
        self.session.profile_changed.emit(profile)

    # -- navigation ----------------------------------------------------------

    def _back(self) -> None:
        self.stack.setCurrentIndex(max(0, self.stack.currentIndex() - 1))
        self._refresh()

    def _next(self) -> None:
        self.stack.setCurrentIndex(
            min(self.stack.count() - 1, self.stack.currentIndex() + 1))
        self._refresh()

    def _refresh(self) -> None:
        index = self.stack.currentIndex()
        running = self._worker is not None and self._worker.running
        self.step_label.setText(f"Step {index + 1} of {len(STEPS)} — {STEPS[index]}")
        self.back_button.setEnabled(index > 0 and not running)
        self.next_button.setEnabled(index < self.stack.count() - 1 and not running)
        self.next_button.setVisible(index < self.stack.count() - 1)

        ready = self.session.robot is not None and self._camera() is not None
        self.start_button.setEnabled(ready and not running)
        self.cancel_button.setEnabled(running)
        self.save_button.setEnabled(self._result is not None
                                    and self.session.profile is not None)
        if index == 2:
            self.run_view.set_camera(self._camera())

    def _append(self, text: str) -> None:
        self.run_log.appendPlainText(text)

    # -- cameras -------------------------------------------------------------

    def _refresh_cameras(self, _label: str = "") -> None:
        labels = self.session.open_cameras
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
        self.run_view.set_camera(camera)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh_cameras()
