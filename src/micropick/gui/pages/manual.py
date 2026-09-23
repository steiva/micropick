"""Manual control: the robot by hand, from the keys and from the picture.

The jog panel drives the axes as before. What this page adds is the picture
as a way of pointing:

* **A click sends the camera there.** The gantry travels so that the clicked
  point comes under the pixel map's reference pixel - the crosshair in the
  middle of the upper camera's view. Near the edge of the frame the map is
  extrapolating, and the move still happens, with a note that it is
  approximate.
* **A click on a detected crosshair sends the tip to it**, at the Crosshair
  Z. This is what the Calibration tab's Check used to be, and it belongs
  here: it is the same act as any other move by hand, and it needed the
  same jog panel to get back to the disc afterwards.
* **A click on a detected cuboid sends the tip to it**, at the Cuboid Z -
  the profile's pickup height, one number shared with the run.

Tip moves are only made inside the area the map was fitted over; a camera
move outside it is only imprecise, a tip move outside it could put the tip
into the wrong thing.

Every move across the deck starts with the tip at the top of its travel,
measured once by retracting and checked before each move after that
(`workflows.manual.raise_tip`). A target past the soft limits is refused on
the picture, as a jog step past them is. And nothing moves at all until
"Click to move" is on; it is off each time this tab is opened, and with it
off a click only says where it would go.

Aspirate and dispense in place, with a volume and a flow rate, are on the
page and on A and D - the in-place commands the run uses, for trying a
pickup by hand. They are refused unless the robot reports a tip on.

A cuboid picked by hand needs somewhere to go, so a well of any plate the
run holds can be driven to: the robot's own `move_to_well`, to the top,
centre or bottom of the well with an offset from it. Two switches make the
pair a pickup: aspirate as soon as the tip reaches a clicked cuboid, and
dispense as soon as it reaches the well.

Every robot command goes through the jog panel's worker (`run_job`), so a
click-move and a key press cannot overlap. When anything moves, the
detections are dropped: they were measured from where the camera was.

The keys - the jog panel's and these - and what the mouse does are listed
on the picture, bottom right; H hides them.
"""

from __future__ import annotations

import logging
import time

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QVBoxLayout,
                               QWidget)

from ...core.calibration.pixel_map import PixelMap
from ...hardware.protocols import xyz
from ...viz import markers, overlays
from ...workflows import manual as moves
from ..auto_camera import CameraOpener
from ..detector import wanted_model
from ..session import Session
from ..theme import SPACING
from ..theme.factory import (card, combo_box, double_spin_box, heading,
                             scroll_column, secondary_button)
from ..tip_detector import load_tip_detector
from ..widgets.camera_view import CameraView
from ..widgets.card_columns import CardColumns
from ..widgets.feed_row import FeedRow
from ..widgets.jog_panel import JogPanel
from ..workers import Worker

__all__ = ["ManualPage", "SNAP_PX", "CROSSHAIR_Z_MM"]

TITLE = "Manual control"

log = logging.getLogger(__name__)

PANEL_WIDTH = 400

# A click further than this from any crosshair was meant for something else
# and is a camera move, not a rounding to the nearest crosshair.
SNAP_PX = 60.0

# How far outside a cuboid's contour a click still counts as on it: the
# outline is a line one pixel wide, and a cuboid is a small thing to hit.
CUBOID_SNAP_PX = 6.0

# The calibration check's z for a crosshair: just under the calibration
# module's height, so the tip travels level with the disc.
CROSSHAIR_Z_MM = 67.0

Z_RANGE = (0.5, 150.0)

# Liquid handling bounds: wide enough for any pipette on this robot, and a
# typo of an extra digit still stops at the top of the range.
VOLUME_RANGE = (0.1, 1000.0)
FLOW_RANGE = (0.1, 1000.0)

# (key, what it does, handler name). One list, so the picture's key box
# cannot offer a key that is not bound.
KEYS = (("A", "aspirate", "_aspirate"),
        ("D", "dispense", "_dispense"))

# How far the tip may be sent from the chosen well level, either way.
WELL_OFFSET_RANGE = (-50.0, 50.0)

# What the mouse does on the picture, for the same box.
MOUSE_HELP = ("click   move there (with Click to move on)",
              "wheel   zoom",
              "middle-drag   pan",
              "double-click   fit the picture")


def _number_box(parent: QWidget, value: float, span, suffix: str,
                decimals: int = 2, step: float = 0.1):
    box = double_spin_box(parent)
    box.setRange(*span)
    box.setDecimals(decimals)
    box.setSingleStep(step)
    box.setSuffix(suffix)
    # Wide enough for the value and its suffix: a spin box sized by its
    # layout shows neither.
    box.setMinimumWidth(110)
    box.setValue(value)
    return box


def _z_box(parent: QWidget, value: float):
    return _number_box(parent, value, Z_RANGE, " mm")


def _row(box, label: str, widget) -> None:
    row = QHBoxLayout()
    name = QLabel(label)
    name.setMinimumWidth(90)
    row.addWidget(name)
    row.addWidget(widget)
    row.addStretch(1)
    box.layout().addLayout(row)


class ManualPage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._worker: Worker | None = None
        self._tip_detector = None
        # The top of the Z travel with the tip that is on; see raise_tip.
        self._z_top: float | None = None
        self._forget_targets(redraw=False)

        self.opener = CameraOpener(session, self)
        self.view = CameraView(self)
        self.view.clicked.connect(self._clicked)
        # The host is this page: its shortcuts are window-wide while the page
        # is showing, and go quiet with it.
        self.jog = JogPanel(session, shortcut_host=self, parent=self)
        self.jog.show_position_on(self.view)
        self.jog.moved.connect(self._forget_targets)
        self.jog.add_help([f"{key.lower()}   {what}" for key, what, _ in KEYS]
                          + list(MOUSE_HELP))

        panel = CardColumns([self._camera_card(), self._click_card(),
                             self._targets_card(), self._liquid_card(),
                             self._well_card(), self.jog], self)

        body = QHBoxLayout()
        body.setSpacing(SPACING)
        body.addWidget(FeedRow(self.view, scroll_column(panel, PANEL_WIDTH)),
                       1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2, SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addLayout(body, 1)

        session.camera_opened.connect(self._refresh_cameras)
        session.camera_closed.connect(self._refresh_cameras)
        session.robot_state_changed.connect(self._robot_changed)
        session.labware_changed.connect(lambda _s: self._reload_plates())
        session.tip_changed.connect(lambda _t: self._lose_top())
        session.profile_changed.connect(lambda _p: self._on_profile_changed())
        self._install_shortcuts()
        self._reload_plates()
        self._on_profile_changed()
        self._refresh_cameras()

    # -- construction --------------------------------------------------------

    def _camera_card(self) -> QWidget:
        box = card(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Camera"))
        self.camera_choice = combo_box(self)
        self.camera_choice.currentTextChanged.connect(self._show_camera)
        row.addWidget(self.camera_choice, 1)
        box.layout().addLayout(row)
        return box

    def _click_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Click on the picture", 2))
        # Off on every entry to the page: a click on a picture that moves
        # the gantry is not something to be halfway into by accident.
        self.armed = QCheckBox("Click to move", self)
        self.armed.toggled.connect(lambda _on: self._refresh())
        box.layout().addWidget(self.armed)
        note = QLabel("The camera goes where you click; a click on a detected "
                      "crosshair or cuboid sends the tip to it instead. The "
                      "tip is raised first. Off again every time this tab is "
                      "opened.")
        note.setWordWrap(True)
        box.layout().addWidget(note)
        return box

    def _targets_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Targets", 2))

        # One above the other: side by side they are cut off in a column
        # half the panel wide.
        self.cross_button = secondary_button("Detect crosshairs", self)
        self.cross_button.clicked.connect(self._detect_crosshairs)
        self.cuboid_button = secondary_button("Detect cuboids", self)
        self.cuboid_button.clicked.connect(self._detect_cuboids)
        box.layout().addWidget(self.cross_button)
        box.layout().addWidget(self.cuboid_button)

        self.cross_z = _z_box(self, CROSSHAIR_Z_MM)
        self.cuboid_z = _z_box(self, 0.5)
        self.cuboid_z.setToolTip(
            "The profile's pickup height (dish_bottom + pickup_offset). "
            "Changing it here changes pickup_offset, which the run uses too.")
        self.cuboid_z.editingFinished.connect(self._cuboid_z_edited)
        _row(box, "Crosshair Z", self.cross_z)
        _row(box, "Cuboid Z", self.cuboid_z)

        self.clear_button = secondary_button("Clear", self)
        self.clear_button.clicked.connect(lambda: self._forget_targets())
        box.layout().addWidget(self.clear_button)

        self.state = QLabel()
        self.state.setWordWrap(True)
        self.state.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addWidget(self.state)
        return box

    def _liquid_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Liquid", 2))
        self.volume = _number_box(self, 10.0, VOLUME_RANGE, " µl", 1, 1.0)
        self.flow_rate = _number_box(self, 50.0, FLOW_RANGE, " µl/s", 1, 1.0)
        _row(box, "Volume", self.volume)
        _row(box, "Flow rate", self.flow_rate)
        row = QHBoxLayout()
        # The keys are in the box on the picture; in the label they cut the
        # button's text off in a column half the panel wide.
        self.aspirate_button = secondary_button("Aspirate", self)
        self.aspirate_button.setToolTip("Aspirate where the tip is. Key: A")
        self.aspirate_button.clicked.connect(self._aspirate)
        self.dispense_button = secondary_button("Dispense", self)
        self.dispense_button.setToolTip("Dispense where the tip is. Key: D")
        self.dispense_button.clicked.connect(self._dispense)
        row.addWidget(self.aspirate_button)
        row.addWidget(self.dispense_button)
        box.layout().addLayout(row)

        # The two halves of a pickup, made automatic. Both use the volume
        # and flow rate above.
        self.auto_aspirate = QCheckBox("Aspirate at a cuboid", self)
        self.auto_aspirate.setToolTip(
            "Aspirate as soon as the tip reaches a cuboid clicked on the "
            "picture, with the volume and flow rate above.")
        self.auto_dispense = QCheckBox("Dispense at a well", self)
        self.auto_dispense.setToolTip(
            "Dispense as soon as the tip reaches the well chosen below, with "
            "the volume and flow rate above.")
        box.layout().addWidget(self.auto_aspirate)
        box.layout().addWidget(self.auto_dispense)
        return box

    def _well_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Well", 2))
        self.plate_choice = combo_box(self)
        self.plate_choice.currentIndexChanged.connect(lambda _i: self._show_wells())
        self.well_choice = combo_box(self)
        # Typed as well as chosen: "H12" is quicker than scrolling to it,
        # and the completer the editable box brings finds it either way.
        self.well_choice.setEditable(True)
        self.well_choice.setInsertPolicy(self.well_choice.InsertPolicy.NoInsert)
        self.well_level = combo_box(self)
        self.well_level.addItems(moves.WELL_LEVELS)
        self.well_offset = _number_box(self, 0.0, WELL_OFFSET_RANGE, " mm")
        self.well_offset.setToolTip(
            "Z from the chosen level: + is up. X and Y come from the "
            "profile's well_offset_x and well_offset_y, as in the run.")
        for label, widget in (("Plate", self.plate_choice),
                              ("Well", self.well_choice),
                              ("Level", self.well_level)):
            row = QHBoxLayout()
            name = QLabel(label)
            name.setMinimumWidth(90)
            row.addWidget(name)
            row.addWidget(widget, 1)
            box.layout().addLayout(row)
        _row(box, "Offset Z", self.well_offset)

        self.well_button = secondary_button("Go to well", self)
        self.well_button.clicked.connect(self._go_to_well)
        box.layout().addWidget(self.well_button)
        self.well_state = QLabel()
        self.well_state.setWordWrap(True)
        box.layout().addWidget(self.well_state)
        return box

    def _install_shortcuts(self) -> None:
        """A and D, window-wide while this page is showing - the jog panel's
        arrangement, and for its reason: enabled only on screen, so no
        hidden page claims the key."""
        self._shortcuts = []
        for key, _what, handler in KEYS:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(getattr(self, handler))
            shortcut.setEnabled(False)
            self._shortcuts.append(shortcut)

    # -- liquid ------------------------------------------------------------------

    def _aspirate(self) -> None:
        self._liquid("aspirate", "aspirated", moves.aspirate)

    def _dispense(self) -> None:
        self._liquid("dispense", "dispensed", moves.dispense)

    def _tip_problem(self, what: str) -> str:
        """Why `what` (aspirate, dispense) cannot happen now, or ""."""
        session = self.session
        if session.robot is None:
            return f"REFUSED: no robot to {what} with."
        if session.tip.attached is not True:
            state = ("none" if session.tip.attached is False
                     else "that it cannot tell")
            return (f"REFUSED: {what} needs a tip on the pipette, and the "
                    f"robot reports {state}.")
        return ""

    def _liquid_amount(self) -> tuple[float, float]:
        return float(self.volume.value()), float(self.flow_rate.value())

    def _liquid(self, what: str, done: str, command) -> None:
        """Aspirate or dispense where the tip is, through the jog queue."""
        problem = self._tip_problem(what)
        if problem:
            self.jog.tell(problem)
            return
        robot = self.session.robot
        volume, rate = self._liquid_amount()
        note = f"{done} {volume:g} µl at {rate:g} µl/s"

        def job(log):
            log(f"{what} {volume:g} µl at {rate:g} µl/s")
            command(robot, volume, rate)
            return note

        # Nothing moves: what is detected on the picture still stands.
        if not self.jog.run_job(job, moves=False):
            self.jog.tell(f"not now: the robot is busy; {what} again when "
                          f"it is done.")

    # -- what the picture can be used for -------------------------------------

    def _map_problems(self) -> list[str]:
        """What stops a pixel from meaning a deck position right now."""
        session, out = self.session, []
        profile = session.profile
        if session.robot is None:
            out.append("no robot: connect it on the Profile page.")
        if profile is None:
            out.append("no profile loaded.")
            return out
        if profile.pixel_map is None:
            out.append("no pixel map: run the camera calibration.")
        label = self.camera_choice.currentText()
        camera = self._camera()
        if camera is None:
            out.append("the camera is not open.")
        elif label != session.upper_camera_label:
            out.append(f"the pixel map is the upper camera's "
                       f"({session.upper_camera_label}); this is {label}.")
        elif profile.pixel_map is not None:
            # A map fitted at one mode and applied at another misplaces
            # everything by a fraction of the field and says nothing.
            out += profile.pixel_map.check_camera(camera.resolution)
        return out

    # -- detecting ------------------------------------------------------------

    def _detect_crosshairs(self) -> None:
        if self._blocked():
            return
        session = self.session
        profile, robot, camera = session.profile, session.robot, self._camera()
        pmap = PixelMap.from_config(profile.pixel_map)
        mock, detector = session.mock, self._tip_detector

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
            inside = [p for p in found if pmap.covers(*p)]
            outside = [p for p in found if not pmap.covers(*p)]
            worlds = [pmap.to_robot(p[0], p[1], gantry) for p in inside]
            return (detector, np.array(inside).reshape(-1, 2),
                    np.array(worlds).reshape(-1, 2),
                    np.array(outside).reshape(-1, 2))

        self._run(Worker(job), "detecting crosshairs", self._crosshairs_found)

    def _crosshairs_found(self, payload) -> None:
        detector, points, worlds, outside = payload
        self._tip_detector = detector
        self._cross = points
        self._cross_worlds = worlds
        self._cross_outside = outside
        self._cross_standin = bool(getattr(detector, "is_standin", False))
        self._chosen = None
        self._redraw()
        self._refresh()

    def _detect_cuboids(self) -> None:
        if self._blocked():
            return
        session = self.session
        profile, robot, camera = session.profile, session.robot, self._camera()
        wanted = wanted_model(profile, session.mock)
        if not wanted:
            self.state.setText("no cuboid model named in the profile: choose "
                               "one on the Profile page.")
            return
        service, cfg = session.detector, profile.picking
        pmap = PixelMap.from_config(profile.pixel_map)

        def job(log):
            if service.name != wanted:
                log(f"loading {wanted}")
                service.load(wanted)
            gantry = np.array(xyz(robot)[:2])
            frame = camera.read_after(time.monotonic())
            detection = service.detect(frame, cfg, profile.pixel_map)
            df = detection.df
            worlds = (np.array([pmap.to_robot(x, y, gantry)
                                for x, y in zip(df.cX, df.cY)]).reshape(-1, 2)
                      if len(df) else np.empty((0, 2)))
            covered = np.array([pmap.covers(x, y)
                                for x, y in zip(df.cX, df.cY)], dtype=bool)
            return frame.shape, detection, worlds, covered

        self._run(Worker(job), "detecting cuboids", self._cuboids_found)

    def _cuboids_found(self, payload) -> None:
        shape, detection, worlds, covered = payload
        self._frame_shape = shape
        self._cuboids = detection
        self._cuboid_worlds = worlds
        self._cuboid_covered = covered
        self._chosen = None
        log.info("cuboids: %s", detection.summary.replace("\n", " | "))
        self._redraw()
        self._refresh()

    # -- clicking ---------------------------------------------------------------

    def _clicked(self, u: float, v: float) -> None:
        """A click on the picture, in sensor pixels."""
        problems = self._map_problems()
        if problems:
            self.jog.tell("Click: " + problems[0])
            return
        if self._busy() or self.jog.busy:
            return
        profile, robot = self.session.profile, self.session.robot
        pmap = PixelMap.from_config(profile.pixel_map)
        target = self._target_at(u, v)
        notes = []

        if target is not None:
            kind, index, world = target
            offset = profile.calibration.pipette_offset
            if offset is None:
                self.jog.tell(f"REFUSED: no pipette offset, so the tip cannot "
                              f"be sent to the {kind}. Run the pipette "
                              f"calibration.")
                return
            xy = np.asarray(world) + np.array([offset.dx, offset.dy])
            z = float((self.cross_z if kind == "crosshair"
                       else self.cuboid_z).value())
            # Checked before anything moves: a pickup that goes to the
            # cuboid and then cannot aspirate has disturbed the dish for
            # nothing.
            pick = kind == "cuboid" and self.auto_aspirate.isChecked()
            if pick and self._tip_problem("aspirate"):
                self.jog.tell(self._tip_problem("aspirate"))
                return
            volume, rate = self._liquid_amount()
            self._chosen = (kind, index)
            self._redraw()
            what = f"tip to {kind} ({xy[0]:.2f}, {xy[1]:.2f}) at z {z:g}"
            position = (xy[0], xy[1], z)

            def job(log):
                self._z_top = moves.drive_tip(robot, xy, z, self._z_top,
                                              log=log)
                if pick:
                    log(f"aspirate {volume:g} µl at {rate:g} µl/s")
                    moves.aspirate(robot, volume, rate)
                    return f"aspirated {volume:g} µl at {rate:g} µl/s"
                return None
        else:
            gantry = np.array(xyz(robot)[:2])
            xy = moves.camera_target(pmap, u, v, gantry)
            if not pmap.covers(u, v):
                notes.append("approximate: outside the calibrated area")
            what = f"camera to ({xy[0]:.2f}, {xy[1]:.2f})"
            position = (xy[0], xy[1], xyz(robot)[2])

            def job(log):
                self._z_top = moves.drive_camera(robot, xy, self._z_top,
                                                 log=log)
                return None

        why = moves.unreachable(self.session.jog_limits, position)
        if why:
            self.jog.tell(f"UNREACHABLE: {what} - {why}")
            return
        if not self.armed.isChecked():
            self.jog.tell(f"{what} - switch on Click to move to go there")
            return
        log.info("%s", "; ".join([what] + notes))

        def run(log_):
            done = job(log_)
            return "; ".join(notes + ([done] if done else []))

        self.jog.run_job(run)

    def _target_at(self, u: float, v: float):
        """(kind, index, deck xy) of the detection the click is on, or None.

        A crosshair within SNAP_PX, or a cuboid whose outline the click is
        inside (give or take CUBOID_SNAP_PX); the nearer centre if both.
        Detections outside the calibrated area are not targets.
        """
        click = np.array([u, v])
        found = []
        if len(self._cross):
            distances = np.linalg.norm(self._cross - click, axis=1)
            i = int(np.argmin(distances))
            if distances[i] <= SNAP_PX:
                found.append((float(distances[i]), "crosshair", i,
                              self._cross_worlds[i]))
        detection = self._cuboids
        if detection is not None and len(detection.df):
            df = detection.df
            for i, (contour, cx, cy) in enumerate(zip(df.contour, df.cX, df.cY)):
                inside = cv2.pointPolygonTest(
                    np.asarray(contour, dtype=np.float32), (float(u), float(v)),
                    True)
                if inside >= -CUBOID_SNAP_PX and self._cuboid_covered[i]:
                    found.append((float(np.hypot(cx - u, cy - v)), "cuboid", i,
                                  self._cuboid_worlds[i]))
        if not found:
            return None
        _, kind, index, world = min(found, key=lambda f: f[0])
        return kind, index, world

    # -- the well ------------------------------------------------------------------

    def _reload_plates(self) -> None:
        """The run's plates in the combo, the one in hand kept."""
        chosen = self.plate_choice.currentData()
        chosen_slot = chosen[0] if chosen else None
        self.plate_choice.blockSignals(True)
        self.plate_choice.clear()
        for slot, entry, definition in self.session.plates():
            self.plate_choice.addItem(f"slot {slot} - {definition.display_name}",
                                      (slot, entry.labware_id, definition))
        slots = [self.plate_choice.itemData(i)[0]
                 for i in range(self.plate_choice.count())]
        if chosen_slot in slots:
            self.plate_choice.setCurrentIndex(slots.index(chosen_slot))
        self.plate_choice.blockSignals(False)
        self._show_wells()

    def _show_wells(self) -> None:
        """The chosen plate's wells, row by row, the one typed kept."""
        typed = self.well_choice.currentText()
        data = self.plate_choice.currentData()
        wells = []
        if data is not None:
            ordering = data[2].ordering
            for row in range(max((len(c) for c in ordering), default=0)):
                wells += [column[row] for column in ordering
                          if row < len(column)]
        self.well_choice.blockSignals(True)
        self.well_choice.clear()
        self.well_choice.addItems(wells)
        if typed in wells:
            self.well_choice.setCurrentIndex(wells.index(typed))
        self.well_choice.blockSignals(False)
        self._refresh()

    def _go_to_well(self) -> None:
        data = self.plate_choice.currentData()
        robot = self.session.robot
        if data is None or robot is None or self.jog.busy:
            return
        slot, labware_id, definition = data
        well = self.well_choice.currentText().strip().upper()
        if well not in definition.wells:
            self.jog.tell(f"REFUSED: {definition.display_name} in slot {slot} "
                          f"has no well {well!r}.")
            return
        drop = self.auto_dispense.isChecked()
        if drop and self._tip_problem("dispense"):
            self.jog.tell(self._tip_problem("dispense"))
            return
        cfg = self.session.profile.picking
        level = self.well_level.currentText()
        offset = (cfg.well_offset_x, cfg.well_offset_y,
                  float(self.well_offset.value()))
        volume, rate = self._liquid_amount()

        def job(log):
            self._z_top = moves.drive_to_well(robot, labware_id, well, level,
                                              offset, self._z_top, log=log)
            said = f"tip in {well} of slot {slot} ({level} {offset[2]:+g} mm)"
            if drop:
                log(f"dispense {volume:g} µl at {rate:g} µl/s")
                moves.dispense(robot, volume, rate)
                said += f"; dispensed {volume:g} µl"
            return said

        log.info("going to well %s in slot %s", well, slot)
        self.jog.run_job(job)

    # -- the profile's number -----------------------------------------------------

    def _cuboid_z_edited(self) -> None:
        """Cuboid Z is pickup_height, which is derived: keep dish_bottom and
        move pickup_offset, so the run picks at the height set here."""
        profile = self.session.profile
        if profile is None:
            return
        cfg = profile.picking
        offset = round(float(self.cuboid_z.value()) - cfg.dish_bottom, 3)
        if abs(offset - cfg.pickup_offset) < 1e-6:
            return
        cfg.pickup_offset = offset
        profile.save_picking()
        log.info("pickup_offset for %r: %g (pickup height %g)", profile.name,
                 offset, cfg.pickup_height)
        self.session.profile_changed.emit(profile)

    # -- the worker ----------------------------------------------------------------

    def _blocked(self) -> bool:
        problems = self._map_problems()
        if problems:
            self.state.setText("\n".join("• " + p for p in problems))
            return True
        return self._busy() or self.jog.busy

    def _busy(self) -> bool:
        return self._worker is not None and self._worker.running

    def _run(self, worker: Worker, what: str, done) -> None:
        self._worker = worker
        self._done = done
        worker.message.connect(self._said)
        worker.finished.connect(self._job_done)
        worker.failed.connect(self._job_failed)
        self.state.setText(what + "…")
        log.info("%s", what)
        worker.start()
        self._refresh()

    def _said(self, text: str) -> None:
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
        self.state.setText(f"failed: {reason}")
        log.error("manual control: %s", reason)
        self._refresh()

    # -- display ---------------------------------------------------------------------

    def _forget_targets(self, redraw: bool = True) -> None:
        self._cross = self._cross_worlds = np.empty((0, 2))
        self._cross_outside = np.empty((0, 2))
        self._cross_standin = False
        self._cuboids = None
        self._cuboid_worlds = np.empty((0, 2))
        self._cuboid_covered = np.empty(0, dtype=bool)
        self._frame_shape = None
        self._chosen = None
        if redraw:
            self._redraw()
            self._refresh()

    def _redraw(self) -> None:
        profile = self.session.profile
        items = []
        detection = self._cuboids
        if detection is not None and self._frame_shape is not None:
            items += overlays.items(
                self._frame_shape, cuboid_df=detection.df,
                pickable=detection.pickable if detection.classified else None,
                isolated=detection.isolated if detection.classified else None,
                bubbles=detection.bubbles if detection.classified else None)
        if len(self._cross) or len(self._cross_outside):
            reference = (profile.pixel_map.ref
                         if profile is not None and profile.pixel_map is not None
                         else None)
            chosen = (self._chosen[1] if self._chosen is not None
                      and self._chosen[0] == "crosshair" else None)
            items += markers.crosshairs(self._cross, reference=reference,
                                        chosen=chosen,
                                        outside=self._cross_outside)
        if self._chosen is not None and self._chosen[0] == "cuboid":
            row = detection.df.iloc[[self._chosen[1]]]
            items += overlays.items(self._frame_shape, chosen=row)
        self.view.set_overlay_items(items)

    def _refresh(self) -> None:
        busy = self._busy()
        problems = self._map_problems()
        for button in (self.cross_button, self.cuboid_button):
            button.setEnabled(not busy and not problems)
        self.clear_button.setEnabled(
            len(self._cross) > 0 or len(self._cross_outside) > 0
            or self._cuboids is not None)
        self.cuboid_z.setEnabled(self.session.profile is not None)
        has_robot = self.session.robot is not None
        self.aspirate_button.setEnabled(has_robot)
        self.dispense_button.setEnabled(has_robot)
        has_plate = self.plate_choice.count() > 0
        self.well_button.setEnabled(has_robot and has_plate
                                    and self.well_choice.count() > 0)
        self.well_state.setText(
            "" if has_plate else
            "No plate in the run: load one on the Labware page.")
        self.well_state.setVisible(not has_plate)
        if busy:
            return
        if problems:
            self.state.setText("\n".join("• " + p for p in problems))
            return
        parts = []
        if len(self._cross) or len(self._cross_outside):
            text = f"{len(self._cross)} crosshairs"
            if len(self._cross_outside):
                text += (f" (+{len(self._cross_outside)} outside the "
                         f"calibrated area, grey)")
            parts.append(text)
        if self._cuboids is not None:
            parts.append(self._cuboids.summary.replace("\n", "; "))
        if not parts:
            self.state.setText("Detect, then click a crosshair or a cuboid "
                               "to send the tip to it.")
            return
        text = "\n".join(parts)
        if self._cross_standin:
            text += ("\nStand-in tip detector: the crosshairs are invented "
                     "from the frame's centre.")
        if self._cuboids is not None and self._cuboids.standin:
            text += "\nStand-in cuboid detector: bright blobs, not the model."
        self.state.setText(text)

    def _on_profile_changed(self) -> None:
        profile = self.session.profile
        self.cuboid_z.blockSignals(True)
        if profile is not None:
            self.cuboid_z.setValue(profile.picking.pickup_height)
        self.cuboid_z.blockSignals(False)
        # The same profile saved again - a setting changed, Cuboid Z among
        # them - keeps what was detected. A different profile is a different
        # map, offset and model, and nothing measured belongs to it.
        if profile is not getattr(self, "_profile", None):
            self._profile = profile
            self._tip_detector = None
            if profile is not None:
                # The run's own numbers as the starting point; what is typed
                # here afterwards stays while this profile is loaded.
                self.volume.setValue(profile.picking.vol)
                self.flow_rate.setValue(profile.picking.flow_rate)
            self._forget_targets()
        else:
            self._refresh()

    def _robot_changed(self, _state: str) -> None:
        self._lose_top()
        self._reload_plates()

    def _lose_top(self) -> None:
        """The top of the travel depends on the tip and on the run; measure it
        again before the next move."""
        self._z_top = None

    # -- cameras ----------------------------------------------------------------------

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
        elif self.session.upper_camera_label in labels:
            self.camera_choice.setCurrentIndex(
                labels.index(self.session.upper_camera_label))
        self.camera_choice.blockSignals(False)
        self._show_camera(self.camera_choice.currentText())

    def _show_camera(self, label: str) -> None:
        camera = self.session.camera(label) if label else None
        if camera is not self.view.camera:
            # What was detected was the other camera's picture.
            self._forget_targets()
        self.view.set_camera(camera)
        self._refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        for shortcut in self._shortcuts:
            shortcut.setEnabled(True)
        # Disarmed on every entry, however it was left.
        self.armed.setChecked(False)
        # Jogging is done by eye through this camera; without it the page is
        # a D-pad and a blank rectangle.
        self.opener.ensure(self.session.upper_camera_label)
        self._refresh_cameras()

    def hideEvent(self, event) -> None:
        for shortcut in self._shortcuts:
            shortcut.setEnabled(False)
        super().hideEvent(event)
