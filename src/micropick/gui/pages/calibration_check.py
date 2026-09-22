"""Checking both calibrations by driving the tip to a crosshair — `click_to_go`.

The notebook's cell of that name, with the cv2 window replaced by the page's
camera view. It answers the question neither calibration can answer about
itself: **does the tip land where the picture says it will.** The map is
fitted against its own held-out poses and the offset against its own
crosshair, and both can be internally excellent and jointly wrong — a map
fitted at one focus and used at another, an offset measured with a different
tip. What settles it is the pipette arriving over a crosshair the operator
picked with the mouse, and the operator looking at it.

The three things it shows, in the order they matter:

**Where the tip is going.** A click snaps to the nearest detected crosshair
rather than taking the cursor's pixel: hitting a pixel with a mouse is not a
thing, and the detector's box centre is sub-pixel. The target is
`pixel_to_robot` exactly as the notebook defines it — `pmap.to_robot(u, v,
gantry)` plus the profile's pipette offset — and it is printed before
anything moves.

**The way back.** The camera sits some sixty millimetres from the tip, so
the move that puts the tip over a crosshair takes the disc out of the
camera's view: after one check there is nothing left to detect and nothing
to click. The jog panel is therefore part of this page, with the profile's
positions open — drive back to the pose the crosshairs were seen from, in
one double click, and detect again. The notebook's `click_to_go` had the
same problem and answered it with a right click bound to
`profile.where("tip_calib")`; this is that, without the binding being
invisible.

The notebook also recomputed the crosshair's deck coordinate after each
move and accumulated the difference as the map's consistency. That is not
here, and for the same reason: from the new pose the camera is not looking
at the disc, so the figure was being computed from whatever the detector
found instead. The map's own held-out error is the number that says how
good it is; this page says whether the tip arrives where the picture
promised.

**That nothing moves by accident.** Clicking the picture moves the gantry,
so it does not until "Click to move" is on, and it is off every time the
page is entered. With it off a click still reports the coordinates, which
is the notebook's `move=False`. The Z the tip travels at is the notebook's
67 mm, on screen and editable, because it is the number that decides
whether the tip clears the module or ploughs into it.

Nothing here writes to the profile. It is a measurement of a calibration
that has already been saved, and if it reads badly the answer is to run the
calibration again, not to record the disagreement.
"""

from __future__ import annotations

import logging
import time

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QHBoxLayout, QLabel,
                               QPlainTextEdit, QVBoxLayout, QWidget)

from ...core.calibration.pixel_map import PixelMap
from ...hardware.protocols import goto_xy, move_to, xyz
from ...viz import markers
from ..auto_camera import CameraOpener
from ..session import Session
from ..theme import SPACING
from ..theme.factory import (card, combo_box, heading, primary_button,
                             scroll_column, secondary_button)
from ..tip_detector import load_tip_detector
from ..widgets.camera_view import CameraView
from ..widgets.jog_panel import JogPanel
from ..workers import Worker

__all__ = ["CalibrationCheck", "SNAP_PX", "DEFAULT_Z_MM"]

TITLE = "Check"

log = logging.getLogger(__name__)

PANEL_WIDTH = 420

# The notebook's snap radius: a click further than this from any crosshair
# was meant for something else and is refused rather than rounded to the
# nearest one.
SNAP_PX = 60.0

# The notebook's z for this check: just under the calibration module's
# height, so the tip travels level with the disc it is being judged against.
DEFAULT_Z_MM = 67.0


class CalibrationCheck(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._worker: Worker | None = None
        self._detector = None
        self._standin = False
        self._points = np.empty((0, 2))       # inside the map, clickable
        self._worlds = np.empty((0, 2))       # their deck coordinates
        self._outside = np.empty((0, 2))      # detected, not covered
        self._chosen: int | None = None
        self.opener = CameraOpener(session, self)

        self.view = CameraView(self)
        self.view.clicked.connect(self._clicked)
        # The way back to where the crosshairs were visible. Positions open
        # and Move folded: the usual act here is returning to a stored pose,
        # not jogging by hand.
        self.jog = JogPanel(session, shortcut_host=self,
                            machine_controls=False, collapsed=("move",),
                            parent=self)

        panel = QWidget(self)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        column.addWidget(self._target_card())
        column.addWidget(self._move_card())
        column.addWidget(self.jog, 1)

        body = QHBoxLayout(self)
        body.setContentsMargins(0, SPACING, 0, 0)
        body.setSpacing(SPACING)
        body.addWidget(self.view, 1)
        body.addWidget(scroll_column(panel, PANEL_WIDTH))

        session.camera_opened.connect(self._refresh_cameras)
        session.camera_closed.connect(self._refresh_cameras)
        session.robot_state_changed.connect(lambda _s: self._refresh())
        session.profile_changed.connect(lambda _p: self._on_profile_changed())
        self._refresh_cameras()
        self._refresh()

    # -- construction --------------------------------------------------------

    def _target_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Crosshairs", 2))
        row = QHBoxLayout()
        row.addWidget(QLabel("Upper camera"))
        self.camera_choice = combo_box(self)
        self.camera_choice.currentTextChanged.connect(self._show_camera)
        row.addWidget(self.camera_choice, 1)
        box.layout().addLayout(row)

        self.detect_button = primary_button("Detect crosshairs", self)
        self.detect_button.clicked.connect(self._detect)
        box.layout().addWidget(self.detect_button)

        self.state = QLabel()
        self.state.setWordWrap(True)
        self.state.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addWidget(self.state)
        return box

    def _move_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Go to", 2))

        row = QHBoxLayout()
        row.addWidget(QLabel("Z"))
        self.z = QDoubleSpinBox(self)
        self.z.setRange(0.5, 150.0)
        self.z.setDecimals(2)
        self.z.setSingleStep(0.5)
        self.z.setValue(DEFAULT_Z_MM)
        self.z.setSuffix(" mm")
        # Wide enough for the value and its suffix: a spin box sized by its
        # layout shows neither.
        self.z.setMinimumWidth(110)
        row.addWidget(self.z)
        row.addStretch(1)
        box.layout().addLayout(row)

        # Off on every entry to the page: a click on a picture that moves the
        # gantry is not something to be halfway into by accident. The label
        # is short because a QCheckBox does not wrap; the sentence that says
        # what it does is under it.
        self.armed = QCheckBox("Click to move", self)
        self.armed.toggled.connect(lambda _on: self._refresh())
        box.layout().addWidget(self.armed)
        # One line. The card below it is the way back to the crosshairs, and
        # a paragraph here pushes that off the bottom of the column.
        self.armed.setToolTip(
            "With this on, a click on a crosshair drives the tip there at "
            "the Z above. With it off a click only reports the coordinates. "
            "It is off again every time this tab is opened.")
        armed_note = QLabel("A click on a crosshair then drives the tip there. "
                            "Off again every time this tab is opened.")
        armed_note.setWordWrap(True)
        box.layout().addWidget(armed_note)

        self.target_note = QLabel()
        self.target_note.setWordWrap(True)
        self.target_note.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.target_note.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        box.layout().addWidget(self.target_note)

        self.retract_button = secondary_button("Retract Z", self)
        self.retract_button.clicked.connect(self._retract)
        box.layout().addWidget(self.retract_button)

        self.log_view = QPlainTextEdit(self)
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        # Bounded both ways: what it has to say is three lines, and left to
        # grow it pushes the jog panel - the way back to the crosshairs -
        # off the bottom of the column.
        self.log_view.setMinimumHeight(70)
        self.log_view.setMaximumHeight(100)
        self.log_view.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        box.layout().addWidget(self.log_view)
        return box

    # -- the calibration under test ------------------------------------------

    def _problems(self) -> list[str]:
        """Everything that stops this page from meaning anything."""
        session, out = self.session, []
        profile = session.profile
        if session.robot is None:
            out.append("no robot: connect it on the Profile page.")
        if profile is None:
            out.append("no profile loaded.")
            return out
        if profile.pixel_map is None:
            out.append("no pixel map: run the sweep on the Camera tab.")
        if profile.calibration.pipette_offset is None:
            out.append("no pipette offset: measure it on the Pipette tab. "
                       "Without it this would drive the camera's reference "
                       "point to the crosshair, not the tip.")
        camera = self._camera()
        if camera is None:
            out.append("the upper camera is not open.")
        elif profile.pixel_map is not None:
            # The same check the notebook makes before using the map: a map
            # fitted at one mode and applied at another misplaces everything
            # by a fraction of the field and says nothing.
            out += profile.pixel_map.check_camera(camera.resolution)
        return out

    def _pixel_to_robot(self, pmap: PixelMap, point, gantry) -> np.ndarray:
        offset = self.session.profile.calibration.pipette_offset
        return (np.asarray(pmap.to_robot(point[0], point[1], gantry))
                + np.array([offset.dx, offset.dy]))

    # -- detecting ------------------------------------------------------------

    def _detect(self) -> None:
        problems = self._problems()
        if problems:
            self.state.setText("\n".join("• " + p for p in problems))
            return
        if self._busy():
            return
        session = self.session
        profile, robot = session.profile, session.robot
        camera = self._camera()
        pmap = PixelMap.from_config(profile.pixel_map)
        mock = session.mock
        detector = self._detector

        def job(log):
            nonlocal detector
            if detector is None:
                log("loading the tip detector")
                detector = load_tip_detector(profile, mock=mock, robot=robot)
            # The pose is read next to the frame, not after it: it is part
            # of the conversion rather than a correction applied later.
            gantry = np.array(xyz(robot)[:2])
            frame = camera.read_after(time.monotonic())
            found = [d.xy for d in detector.detect(frame)
                     if getattr(d, "label", "point") == "point"]
            inside, outside, worlds = [], [], []
            for point in found:
                if pmap.covers(*point):
                    inside.append(point)
                    worlds.append(pmap.to_robot(point[0], point[1], gantry))
                else:
                    outside.append(point)
            return detector, np.array(inside).reshape(-1, 2), \
                np.array(worlds).reshape(-1, 2), np.array(outside).reshape(-1, 2)

        self._run(Worker(job), "detecting crosshairs", self._detected)

    def _detected(self, payload) -> None:
        detector, points, worlds, outside = payload
        self._detector = detector
        self._standin = bool(getattr(detector, "is_standin", False))
        self._points, self._worlds, self._outside = points, worlds, outside
        self._chosen = None
        self._redraw()
        self._refresh()

    # -- clicking and moving ---------------------------------------------------

    def _clicked(self, u: float, v: float) -> None:
        """A click on the picture, in sensor pixels."""
        if self._busy() or not len(self._points):
            return
        click = np.array([u, v])
        distances = np.linalg.norm(self._points - click, axis=1)
        nearest = int(np.argmin(distances))
        if distances[nearest] > SNAP_PX:
            self._say(f"{distances[nearest]:.0f} px from the nearest crosshair; "
                      f"click within {SNAP_PX:g} px of one.")
            return
        self._chosen = nearest
        self._redraw()

        world = self._worlds[nearest]
        profile = self.session.profile
        pmap = PixelMap.from_config(profile.pixel_map)
        gantry = np.array(xyz(self.session.robot)[:2])
        target = self._pixel_to_robot(pmap, self._points[nearest], gantry)
        self.target_note.setText(
            f"crosshair   ({self._points[nearest][0]:7.1f}, "
            f"{self._points[nearest][1]:7.1f}) px\n"
            f"            ({world[0]:8.3f}, {world[1]:8.3f}) mm\n"
            f"tip target  ({target[0]:8.3f}, {target[1]:8.3f}) mm  "
            f"z {self.z.value():g}")
        if not self.armed.isChecked():
            self._say("not moving: switch on \"Click to move\" first.")
            return
        self._go(target)

    def _go(self, target) -> None:
        robot, z = self.session.robot, float(self.z.value())

        def job(log):
            # XY first, as relative travel, then the Z: the robot refuses a
            # moveToCoordinates at the height a long tip already sits at,
            # which is why goto_xy exists (DESIGN section 4).
            log(f"going to ({target[0]:.3f}, {target[1]:.3f}) at z {z:g}")
            goto_xy(robot, float(target[0]), float(target[1]))
            move_to(robot, (float(target[0]), float(target[1]), z),
                    min_z_height=1.0)
            log("  there. Look at the tip; the crosshairs are out of the "
                "camera's view from here, so go back before detecting again.")

        self._run(Worker(job), "moving to the crosshair", self._moved)

    def _moved(self, _payload=None) -> None:
        # Nothing is re-detected: from over the crosshair the camera is
        # looking somewhere else entirely, and a detection from here would
        # be of whatever happened to be under it.
        self._points = self._worlds = self._outside = np.empty((0, 2))
        self._chosen = None
        self._redraw()
        self._refresh()

    def _retract(self) -> None:
        if self._busy() or self.session.robot is None:
            return
        robot = self.session.robot
        self._run(Worker(lambda: robot.retract_axis("leftZ", verbose=False)),
                  "retracting leftZ", lambda _r: self._refresh())

    # -- the worker ------------------------------------------------------------

    def _busy(self) -> bool:
        return self._worker is not None and self._worker.running

    def _run(self, worker: Worker, what: str, done) -> None:
        self._worker = worker
        self._done = done
        worker.message.connect(self._said)
        worker.finished.connect(self._job_done)
        worker.failed.connect(self._job_failed)
        self._append(what)
        log.info("%s", what)
        worker.start()
        self._refresh()

    def _said(self, text: str) -> None:
        self._append(text)
        log.info("%s", text)

    def _job_done(self, payload) -> None:
        if self.sender() is not self._worker:
            return
        self._worker = None
        self._done(payload)

    def _job_failed(self, reason: str) -> None:
        if self.sender() is not self._worker:
            return
        self._worker = None
        self._append(f"failed: {reason}")
        log.error("calibration check: %s", reason)
        self._refresh()

    def _append(self, text: str) -> None:
        self.log_view.appendPlainText(text)

    def _say(self, text: str) -> None:
        self._append(text)

    # -- display ---------------------------------------------------------------

    def _redraw(self) -> None:
        profile = self.session.profile
        reference = (profile.pixel_map.ref
                     if profile is not None and profile.pixel_map is not None
                     else None)
        self.view.set_overlay_items(
            markers.crosshairs(self._points, reference=reference,
                               chosen=self._chosen, outside=self._outside))

    def _on_profile_changed(self) -> None:
        # A different profile is a different map, a different offset and
        # possibly a different model: everything measured so far belonged
        # to the old one.
        self._detector = None
        self._points = self._worlds = self._outside = np.empty((0, 2))
        self._chosen = None
        self._redraw()
        self._refresh()

    def _refresh(self) -> None:
        busy = self._busy()
        problems = self._problems()
        self.detect_button.setEnabled(not busy and not problems)
        self.armed.setEnabled(not busy and not problems)
        self.retract_button.setEnabled(not busy and self.session.robot is not None)
        self.z.setEnabled(not busy)

        if problems:
            self.state.setText("\n".join("• " + p for p in problems))
        elif not len(self._points) and not len(self._outside):
            self.state.setText(
                "Press Detect: every crosshair the model finds is circled, "
                "and clicking one drives the tip to it.")
        else:
            text = (f"{len(self._points)} crosshair"
                    f"{'s' if len(self._points) != 1 else ''} inside the "
                    f"calibrated area")
            if len(self._outside):
                text += (f", {len(self._outside)} outside it (grey: the map "
                         f"would be extrapolating, so they cannot be driven "
                         f"to)")
            text += ". Click one."
            if self._standin:
                text += ("\nStand-in detector: these points are invented from "
                         "the frame's centre, not found on a disc. The "
                         "arithmetic is real; the crosshairs are not.")
            self.state.setText(text)


    # -- cameras ----------------------------------------------------------------

    def _camera(self):
        return self.session.camera(self.camera_choice.currentText())

    def _refresh_cameras(self, _label: str = "") -> None:
        labels = self.session.open_cameras
        current = self.camera_choice.currentText()
        self.camera_choice.blockSignals(True)
        self.camera_choice.clear()
        self.camera_choice.addItems(labels)
        if current in labels:
            self.camera_choice.setCurrentIndex(labels.index(current))
        elif labels:
            over = [i for i, name in enumerate(labels)
                    if "under" not in name.lower()]
            self.camera_choice.setCurrentIndex(over[0] if over else 0)
        self.camera_choice.blockSignals(False)
        self._show_camera(self.camera_choice.currentText())
        self._refresh()

    def _show_camera(self, label: str) -> None:
        self.view.set_camera(self.session.camera(label) if label else None)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Disarmed on every entry, however it was left.
        self.armed.setChecked(False)
        self.opener.ensure(self.session.upper_camera_label)
        self._refresh_cameras()
