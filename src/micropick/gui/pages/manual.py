"""Manual control: the robot by hand, from the keys and from the picture.

The jog panel drives the axes as before. What this page adds is the picture
as a way of pointing. A click marks a memo point (below) while Mark points
is ticked; a **double click** moves:

* **A double click sends the camera there** - in Camera mode, the default. The
  gantry travels so that the clicked point comes under the pixel map's
  reference pixel - the crosshair in the middle of the upper camera's view.
  Near the edge of the frame the map is extrapolating, and the move still
  happens, with a note that it is approximate.
* **Or the tip**, in Tip mode: the tip goes over the double-clicked point - the
  pixel map, then the pipette offset (`workflows.manual.tip_target`) - and
  stays at the travel height, so it shows where it would go down without
  going down. Only inside the area the map was fitted over: outside it the
  map is extrapolating, and a tip is not something to put somewhere
  approximately.
* **A double click on a detected crosshair sends the tip to it**, at the Crosshair
  Z. This is what the Calibration tab's Check used to be, and it belongs
  here: it is the same act as any other move by hand, and it needed the
  same jog panel to get back to the disc afterwards.
* **A double click on a detected cuboid sends the tip to it**, at the Cuboid Z -
  the profile's pickup height, one number shared with the run.

Memo points
-----------
With Mark points ticked (off to begin with), a click on the picture marks
a point where the tip would go down there (`Session.memo_points`, named
with the lowest free number, M1, M2 ...), drawn on the "points" layer -
ticked on this page unless it was cleared - and listed in the Memo points
card, where X, Y and Z are edited and a point is deleted. The tip goes to
one only by its Go to: a double click on a point is a move like any other,
to the pixel. They last until the program closes; a point to keep is a
position (the jog panel's Positions). A click near a point chooses it
instead of marking another one, ticked or not. Marking moves nothing, so
it does not need Click to move.

A point the tip cannot go to is said where it is made and shown in red,
on the picture and in its Z (`Session.memo_problem`): the jog envelope's
checks, the one that matters being a Z below the top of a deck module.
The modules are the profile's, over the deck definition's slots in the
robot's frame, which is the frame a point's X and Y are in - so a point
marked over a 64 mm platform at z 50 is known to be inside it.

The height is the part a click cannot give. A new point takes New point Z,
and New point Z is the last height given: typed into a point's Z, or taken
from the tip with "Z from tip" after jogging it down to where it should
be. A row of points at one depth is so one height set and the rest
clicked; a point at another depth gets its own Z, and the points after it
follow that one. Before any height is given New point Z is "travel
height": a point with no Z is gone to at the travel height, so a first
click never lowers the tip into anything.

**On the lower camera a click never marks or moves anything**, and the picture says
so for as long as it is shown. The pixel map is the upper camera's; the
lower one looks up at the tip, so a point on its picture is not a place on
the deck, and a click there that moved the gantry would move it somewhere
unrelated to what was clicked.

Tip moves are only made inside the area the map was fitted over; a camera
move outside it is only imprecise, a tip move outside it could put the tip
into the wrong thing.

Every move across the deck starts with the tip at the top of its travel,
measured once by retracting and checked before each move after that
(`workflows.motion`). A target past the soft limits is refused on
the picture, as a jog step past them is. And nothing moves at all until
"Click to move" is on; it is off each time this tab is opened, and with it
off a double click only says where it would go. Since a double click is a
move here, it does not fit the picture: the wheel zooms back out.

Aspirate and dispense in place, with a volume and a flow rate, are on the
page and on A and D - the in-place commands the run uses, for trying a
pickup by hand. They are refused unless the robot reports a tip on.

A small deck map is the way across the deck: a click on a slot sends the
camera or the tip, as "A click moves" says, over that slot's center at the
travel height (`core.deck.slot_center`, the deck definition's geometry). The
definition is in the robot's frame, the tip's: the tip goes to the center
itself, the camera to it less the pipette offset. It obeys Click to move
like the picture does, and goes through the same queue.

A cuboid picked by hand needs somewhere to go, so a well of any plate the
run holds can be driven to: the robot's own `move_to_well`, to the top,
center or bottom of the well with an offset from it. Two switches make the
pair a pickup: aspirate as soon as the tip reaches a clicked cuboid, and
dispense as soon as it reaches the well.

The well center
---------------
Go to well is measured from the plate's measured well center where there
is one (`config.schema.WellCenter`, per slot and plate type): its x and y
are added to the profile's well offsets, as a picking run adds them to its
deposit, and the level keeps the robot's own heights. It is the same
measurement as the Liquid handling page's, and is taken here the same
way: Go to well top puts the tip where the robot thinks the chosen well's
top center is, the jog panel walks it onto the real center level with the
rim, and Set well center stores the difference. Forget well center drops
it, and the wells are where the labware definition puts them again.

Every robot command goes through the jog panel's worker (`run_job`), so a
click-move and a key press cannot overlap. When anything moves, the
detections are dropped: they were measured from where the camera was.

After Detect cuboids, their sizes against the profile's size window are in
a folded Sizes section: the Picking page's histogram (`widgets.size_histogram`),
the same drawing, so the two pages cannot disagree about what a run would
accept.

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
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QHBoxLayout, QLabel,
                               QMessageBox, QRadioButton, QVBoxLayout,
                               QWidget)

from ...core.calibration.pixel_map import PixelMap
from ...core.deck import slot_center
from ...hardware.protocols import xyz
from ...viz import markers, overlays
from ...workflows import manual as moves
from ...workflows import well_center
from ...workflows.motion import WELL_LEVELS
from .. import keys
from ..auto_camera import CameraOpener
from ..detector import wanted_model
from ..session import Session
from ..theme import SPACING
from ..theme.factory import (Section, card, combo_box, double_spin_box,
                             heading, primary_button, scroll_column,
                             secondary_button)
from ..tip_detector import load_tip_detector
from ..widgets.camera_view import CameraView
from ..widgets.card_columns import CardColumns
from ..widgets.deck_view import DeckView
from ..widgets.feed_row import FeedRow
from ..position_marks import PositionMarks
from ..widgets.jog_panel import JogPanel
from ..widgets.memo_points import TRAVEL_TEXT, MemoTable, Z_TOP_MM
from ..widgets.size_histogram import SizeHistogram
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

# (action in `gui.keys`, what it does, handler name). One list, so the
# picture's key box cannot offer a key that is not bound.
KEYS = (("aspirate", "aspirate", "_aspirate"),
        ("dispense", "dispense", "_dispense"))

# How far the tip may be sent from the chosen well level, either way.
WELL_OFFSET_RANGE = (-50.0, 50.0)

# The deck map: enough to tell the slots apart, not a second Robot & Deck.
DECK_HEIGHT = 170

# On the picture while the lower camera is shown; see the module docstring.
LOWER_CAMERA_NOTE = "lower camera: clicks do not mark or move"

# What the mouse does on the picture, for the same box.
MOUSE_HELP = ("click   mark a memo point (Mark points on)",
              "double-click   move there (Click to move on)",
              "wheel   zoom",
              "middle-drag   pan")


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


def _memo_z_text(z) -> str:
    return TRAVEL_TEXT if z is None else f"{z:g}"


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
        self._tip_device = None             # the setting it was loaded with
        # The last Go to well top: the robot's own idea of that well's top
        # center, which Set well center measures the real one against.
        self._measurement = well_center.Measurement()
        self._forget_targets(redraw=False)

        self.opener = CameraOpener(session, self)
        self.view = CameraView(self)
        self.view.marks_default = True      # the memo points are drawn there
        self.view.double_click_fits = False
        self.view.clicked.connect(self._mark)
        self.view.double_clicked.connect(self._clicked)
        # The host is this page: its shortcuts are window-wide while the page
        # is showing, and go quiet with it.
        self.jog = JogPanel(session, shortcut_host=self, parent=self)
        self.jog.show_position_on(self.view)
        self.jog.moved.connect(self._forget_targets)
        # Emitted whenever a job ends: Go to well top's nominal is in, and
        # the buttons greyed for the job come back.
        self.jog.position_changed.connect(lambda _lines: self._refresh())
        self.jog.set_page_help(self._page_help())

        self.marks = PositionMarks.on(session, self.view)
        panel = CardColumns([self._camera_card(), self._click_card(),
                             self._memo_card(), self._deck_card(),
                             self._targets_card(), self._sizes_card(),
                             self._liquid_card(), self._well_card(), self.jog],
                            self)

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
        session.robot_busy_changed.connect(self._on_robot_busy)
        session.labware_changed.connect(lambda _s: self._reload_plates())
        session.profile_changed.connect(lambda _p: self._on_profile_changed())
        self._shortcuts = []
        self._install_shortcuts()
        session.settings_changed.connect(lambda _s: self._rebind())
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

        row = QHBoxLayout()
        row.addWidget(QLabel("A double click moves"))
        self.click_camera = QRadioButton("Camera", self)
        self.click_camera.setToolTip("Move the clicked point under the "
                                     "crosshair.")
        self.click_tip = QRadioButton("Tip", self)
        self.click_tip.setToolTip(
            "Move the tip over the clicked point (calibrated area only).")
        self.click_camera.setChecked(True)
        self._click_mode = QButtonGroup(self)
        for button in (self.click_camera, self.click_tip):
            # The keys over the picture are the robot's.
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._click_mode.addButton(button)
            row.addWidget(button)
        row.addStretch(1)
        box.layout().addLayout(row)

        note = QLabel("Camera: the double-clicked point moves under the "
                      "crosshair. Tip: the tip moves over it at travel "
                      "height. A double click on a detected crosshair or "
                      "cuboid lowers the tip to it. Turns off when the tab "
                      "is opened.")
        note.setWordWrap(True)
        box.layout().addWidget(note)
        return box

    def _memo_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Memo points", 2))
        self.memo_on = QCheckBox("Mark points", self)
        self.memo_on.setToolTip("A click on the picture marks a memo point.")
        # The keys over the picture are the robot's.
        self.memo_on.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        box.layout().addWidget(self.memo_on)
        note = QLabel("Kept until the program closes.")
        note.setWordWrap(True)
        box.layout().addWidget(note)
        self.memo_table = MemoTable(self.session, self)
        self.memo_table.setToolTip("Double-click X, Y or Z to change it.")
        self.memo_table.chosen.connect(self._memo_chosen)
        self.memo_table.z_typed.connect(self._memo_z_typed)
        box.layout().addWidget(self.memo_table)

        self.new_z = _number_box(self, 0.0, (0.0, Z_TOP_MM), " mm")
        self.new_z.setSpecialValueText(TRAVEL_TEXT)
        self.new_z.setToolTip("Z of a new point: the last Z set.")
        _row(box, "New point Z", self.new_z)

        self.memo_go = secondary_button("Go to", self)
        self.memo_go.setToolTip("Move the tip to the chosen point.")
        self.memo_go.clicked.connect(self._go_to_memo)
        self.memo_z = secondary_button("Z from tip", self)
        self.memo_z.setToolTip("Set the chosen point's Z to the tip's Z now.")
        self.memo_z.clicked.connect(self._memo_z_from_tip)
        self.memo_delete = secondary_button("Delete", self)
        self.memo_delete.clicked.connect(self._delete_memo)
        self.memo_clear = secondary_button("Delete all", self)
        self.memo_clear.clicked.connect(self._delete_all_memos)
        for pair in ((self.memo_go, self.memo_z),
                     (self.memo_delete, self.memo_clear)):
            row = QHBoxLayout()
            for button in pair:
                row.addWidget(button)
            box.layout().addLayout(row)
        self.session.memo_points_changed.connect(self._refresh_memo_buttons)
        self.jog.pose_changed.connect(lambda _p: self._refresh_memo_buttons())
        self._refresh_memo_buttons()
        return box

    def _deck_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Deck", 2))
        note = QLabel("Click a slot to move the camera or tip over its "
                      "center (with Click to move on).")
        note.setWordWrap(True)
        box.layout().addWidget(note)
        self.deck = DeckView(self)
        self.deck.setFixedHeight(DECK_HEIGHT)
        self.deck.setToolTip("Move over the clicked slot's center.")
        self.deck.slot_clicked.connect(self._slot_clicked)
        box.layout().addWidget(self.deck)
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
            "Pickup height (dish_bottom + pickup_offset). Changes "
            "pickup_offset.")
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

    def _sizes_card(self) -> QWidget:
        """Folded: the sizes are a second look at a detection, not the
        reason to make one."""
        self.sizes = Section("Sizes", collapsed=True, parent=self)
        self.histogram = SizeHistogram(
            "Detect cuboids to see their sizes against the profile's "
            "cuboid_size_threshold, the shaded band.", self)
        self.sizes.body.layout().addWidget(self.histogram)
        return self.sizes

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
        self.aspirate_button.setToolTip("Aspirate at the tip position.")
        self.aspirate_button.clicked.connect(self._aspirate)
        self.dispense_button = secondary_button("Dispense", self)
        self.dispense_button.setToolTip("Dispense at the tip position.")
        self.dispense_button.clicked.connect(self._dispense)
        row.addWidget(self.aspirate_button)
        row.addWidget(self.dispense_button)
        box.layout().addLayout(row)

        # The two halves of a pickup, made automatic. Both use the volume
        # and flow rate above.
        self.auto_aspirate = QCheckBox("Aspirate at a cuboid", self)
        self.auto_aspirate.setToolTip(
            "Aspirate when the tip reaches a clicked cuboid.")
        self.auto_dispense = QCheckBox("Dispense at a well", self)
        self.auto_dispense.setToolTip(
            "Dispense when the tip reaches the chosen well.")
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
        self.well_choice.currentTextChanged.connect(lambda _t: self._refresh())
        self.well_level = combo_box(self)
        self.well_level.addItems(WELL_LEVELS)
        self.well_offset = _number_box(self, 0.0, WELL_OFFSET_RANGE, " mm")
        self.well_offset.setToolTip(
            "Z from the chosen level (+ up). X and Y: the profile's well "
            "offsets.")
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
        self.well_button.setToolTip(
            "Move the tip into the chosen well (measured center if set).")
        self.well_button.clicked.connect(self._go_to_well)
        box.layout().addWidget(self.well_button)
        self.well_state = QLabel()
        self.well_state.setWordWrap(True)
        box.layout().addWidget(self.well_state)

        # The plate's measured well center, the same one the Liquid handling
        # page sets: see "The well center" in the module docstring.
        self.center_state = QLabel()
        self.center_state.setWordWrap(True)
        box.layout().addWidget(self.center_state)
        self.go_top_button = secondary_button("Go to well top", self)
        self.go_top_button.setToolTip(
            "Move the tip to the chosen well's top center from the labware "
            "definition.")
        self.go_top_button.clicked.connect(self._go_to_well_top)
        self.set_center_button = primary_button("Set well center", self)
        self.set_center_button.setToolTip(
            "Save the current tip position as this plate's well center and "
            "rim.")
        self.set_center_button.clicked.connect(self._set_well_center)
        self.forget_center_button = secondary_button("Forget well center",
                                                     self)
        self.forget_center_button.setToolTip(
            well_center.FORGET_TIP)
        self.forget_center_button.clicked.connect(self._forget_well_center)
        row = QHBoxLayout()
        for button in (self.go_top_button, self.set_center_button):
            row.addWidget(button, 1)
        box.layout().addLayout(row)
        box.layout().addWidget(self.forget_center_button)
        return box

    def _install_shortcuts(self) -> None:
        """A and D, window-wide while this page is showing - the jog panel's
        arrangement, and for its reason: enabled only on screen, so no
        hidden page claims the key."""
        bindings = self.session.settings.keys
        for action, _what, handler in KEYS:
            for sequence in keys.sequences(bindings, action):
                shortcut = QShortcut(sequence, self)
                shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
                shortcut.activated.connect(getattr(self, handler))
                shortcut.setEnabled(self.isVisible())
                self._shortcuts.append(shortcut)

    def _page_help(self) -> list[str]:
        bindings = self.session.settings.keys
        return ([f"{keys.key_text(bindings, action)}   {what}"
                 for action, what, _ in KEYS] + list(MOUSE_HELP))

    def _rebind(self) -> None:
        """A and D again, after Settings changed them."""
        for shortcut in self._shortcuts:
            shortcut.setEnabled(False)
            shortcut.setParent(None)
            shortcut.deleteLater()
        self._shortcuts = []
        self._install_shortcuts()
        self.jog.set_page_help(self._page_help())

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
            self.jog.tell("Robot busy.")

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
        device = session.settings.inference_device
        if device != self._tip_device:
            detector = None                  # loaded for another device

        def job(log):
            nonlocal detector
            if detector is None:
                log("loading the tip detector")
                detector = load_tip_detector(profile, mock=mock, robot=robot,
                                             device=device)
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
        self._tip_device = self.session.settings.inference_device
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
            self.state.setText("No cuboid model chosen (Settings).")
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
            # A frame with nothing on it gives a table without columns.
            if not len(df):
                return (frame.shape, detection, np.empty((0, 2)),
                        np.empty(0, dtype=bool))
            worlds = np.array([pmap.to_robot(x, y, gantry)
                               for x, y in zip(df.cX, df.cY)]).reshape(-1, 2)
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
        self._show_sizes()
        self._redraw()
        self._refresh()

    def _show_sizes(self) -> None:
        profile = self.session.profile
        bins = self.histogram.show_detection(
            self._cuboids,
            profile.picking.cuboid_size_threshold if profile else None)
        # The same bins over the picture, small: what the plot counts.
        self.view.set_histogram(bins)

    # -- clicking ---------------------------------------------------------------

    def _clicked(self, u: float, v: float) -> None:
        """A click on the picture, in sensor pixels."""
        if self._on_lower_camera():
            self.jog.tell(LOWER_CAMERA_NOTE)
            return
        problems = self._map_problems()
        if problems:
            self.jog.tell("Click: " + problems[0])
            return
        if self._busy() or self.jog.busy:
            return
        profile, robot = self.session.profile, self.session.robot
        motion = self.session.motion
        pmap = PixelMap.from_config(profile.pixel_map)
        target = self._target_at(u, v)
        notes = []

        if target is not None:
            kind, index, world = target
            offset = profile.calibration.pipette_offset
            if offset is None:
                self.jog.tell("Refused: no tip calibration.")
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
                motion.tip_to(xy, z, log=log)
                if pick:
                    log(f"aspirate {volume:g} µl at {rate:g} µl/s")
                    moves.aspirate(robot, volume, rate)
                    return f"aspirated {volume:g} µl at {rate:g} µl/s"
                return None
        elif self.click_tip.isChecked():
            # Inside the map only: outside it the map extrapolates, and a
            # camera a few hundred microns off is imprecise where a tip there
            # is in the wrong thing.
            if not pmap.covers(u, v):
                self.jog.tell("Refused: outside the calibrated area.")
                return
            offset = profile.calibration.pipette_offset
            if offset is None:
                self.jog.tell("Refused: no tip calibration.")
                return
            gantry = np.array(xyz(robot)[:2])
            xy = moves.tip_target(pmap, u, v, gantry, offset)
            what = f"tip over ({xy[0]:.2f}, {xy[1]:.2f}) at the travel height"
            position = (xy[0], xy[1], xyz(robot)[2])

            def job(log):
                # The gantry at the travel height is the tip at it: the same
                # move as a camera move, to the tip's target.
                motion.camera_to(xy, log=log)
                return None
        else:
            gantry = np.array(xyz(robot)[:2])
            xy = moves.camera_target(pmap, u, v, gantry)
            if not pmap.covers(u, v):
                notes.append("approximate: outside the calibrated area")
            what = f"camera to ({xy[0]:.2f}, {xy[1]:.2f})"
            position = (xy[0], xy[1], xyz(robot)[2])

            def job(log):
                motion.camera_to(xy, log=log)
                return None

        why = motion.refused(position)
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

    # -- memo points -------------------------------------------------------------

    def _mark(self, u: float, v: float) -> None:
        """A click on the picture: choose the memo point there, or mark a
        new one where the tip would go down at this pixel."""
        if self._on_lower_camera():
            self.jog.tell(LOWER_CAMERA_NOTE)
            return
        problems = self._map_problems()
        if problems:
            self.jog.tell("Click: " + problems[0])
            return
        near = self._memo_near(u, v)
        if near is not None:
            self.memo_table.choose(near)
            return
        if not self.memo_on.isChecked():
            return
        profile = self.session.profile
        offset = profile.calibration.pipette_offset
        if offset is None:
            self.jog.tell("Refused: no tip calibration.")
            return
        pose = self.jog.pose
        if pose is None:
            self.jog.tell("Robot busy.")
            return
        pmap = PixelMap.from_config(profile.pixel_map)
        if not pmap.covers(u, v):
            self.jog.tell("Refused: outside the calibrated area.")
            return
        xy = moves.tip_target(pmap, u, v, np.asarray(pose[:2]), offset)
        point = self.session.add_memo_point(xy[0], xy[1], self._new_z())
        self.memo_table.choose(point.name)
        # Marked to be seen.
        if self.view.marks_box.isVisible():
            self.view.marks_box.setChecked(True)
        problem = self.session.memo_problem(point)
        self.jog.tell(f"{point.name} at ({point.x:.2f}, {point.y:.2f}), "
                      f"z {_memo_z_text(point.z)}"
                      + (f" - NOT REACHABLE: {problem}" if problem else ""))

    def _memo_pixels(self) -> list[tuple[str, float, float]]:
        """(name, u, v) of each memo point on the picture now."""
        profile, pose = self.session.profile, self.jog.pose
        if (profile is None or pose is None or profile.pixel_map is None
                or profile.calibration.pipette_offset is None):
            return []
        pmap = PixelMap.from_config(profile.pixel_map)
        offset = profile.calibration.pipette_offset
        out = []
        for point in self.session.memo_points:
            pixel = pmap.to_pixel(point.x - offset.dx, point.y - offset.dy,
                                  np.asarray(pose[:2]))
            if pixel is not None:
                out.append((point.name, float(pixel[0]), float(pixel[1])))
        return out

    def _memo_near(self, u: float, v: float) -> str | None:
        """The memo point within SNAP_PX of the pixel, the nearest."""
        near = [(np.hypot(pu - u, pv - v), name)
                for name, pu, pv in self._memo_pixels()]
        near = [n for n in near if n[0] <= SNAP_PX]
        return min(near)[1] if near else None

    def _new_z(self) -> float | None:
        value = float(self.new_z.value())
        return None if value <= self.new_z.minimum() else value

    def _set_new_z(self, z) -> None:
        """The last Z given is the next point's; see "Memo points"."""
        self.new_z.setValue(self.new_z.minimum() if z is None else float(z))

    def _memo_z_typed(self, z) -> None:
        self._set_new_z(z)
        self._say_memo_problem(self.memo_table.chosen_name)

    def _say_memo_problem(self, name) -> None:
        point = self.session.memo_point(name or "")
        problem = self.session.memo_problem(point) if point else ""
        if problem:
            self.jog.tell(f"{point.name} NOT REACHABLE: {problem}")

    def _memo_chosen(self, name) -> None:
        self.marks.choose_memo(name)
        self._refresh_memo_buttons()

    def _refresh_memo_buttons(self) -> None:
        chosen = self.memo_table.chosen_name is not None
        has_robot = self.session.robot is not None
        self.memo_go.setEnabled(chosen and has_robot)
        self.memo_z.setEnabled(chosen and self.jog.pose is not None)
        self.memo_delete.setEnabled(chosen)
        self.memo_clear.setEnabled(bool(self.session.memo_points))

    def _memo_z_from_tip(self) -> None:
        name, pose = self.memo_table.chosen_name, self.jog.pose
        if name is None or pose is None:
            return
        z = round(float(pose[2]), 2)
        self.session.change_memo_point(name, z=z)
        self._set_new_z(z)
        self.jog.tell(f"{name}: z {z:g}")
        self._say_memo_problem(name)

    def _delete_memo(self) -> None:
        name = self.memo_table.chosen_name
        if name is not None:
            self.session.remove_memo_points([name])

    def _delete_all_memos(self) -> None:
        points = self.session.memo_points
        if not points:
            return
        answer = QMessageBox.question(
            self, "Delete all memo points",
            f"Delete all {len(points)} memo points?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.session.remove_memo_points([p.name for p in points])

    def _go_to_memo(self) -> None:
        """The button: no Click to move, as the jog panel's Go to."""
        point = self.session.memo_point(self.memo_table.chosen_name or "")
        if point is not None:
            self._drive_to_memo(point)

    def _drive_to_memo(self, point) -> None:
        robot = self.session.robot
        if robot is None or self._busy() or self.jog.busy:
            return
        xy = np.array([point.x, point.y])
        pose = self.jog.pose
        check_z = point.z if point.z is not None else (
            pose[2] if pose is not None else 0.0)
        what = (f"tip to {point.name} ({point.x:.2f}, {point.y:.2f}) "
                f"at z {_memo_z_text(point.z)}")
        why = self.session.motion.refused((point.x, point.y, check_z))
        if why:
            self.jog.tell(f"UNREACHABLE: {what} - {why}")
            return
        log.info("%s", what)
        # No Z: as high as a move may go, which is the travel height.
        z = float("inf") if point.z is None else point.z

        def job(log_):
            self.session.motion.tip_to(xy, z, log=log_)
            return f"at {point.name}"

        self.jog.run_job(job)

    def _target_at(self, u: float, v: float):
        """(kind, index, deck xy) of the detection the click is on, or None.

        A crosshair within SNAP_PX, or a cuboid whose outline the click is
        inside (give or take CUBOID_SNAP_PX); the nearer center if both.
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

    # -- the deck ------------------------------------------------------------------

    def _slot_clicked(self, slot: str) -> None:
        """The camera or the tip over the slot's center, at the travel
        height. The center is in the robot's frame, which is the tip's: the
        camera is over it with the gantry a pipette offset short of it."""
        robot = self.session.robot
        if robot is None or self.jog.busy:
            return
        xy = np.asarray(slot_center(slot), dtype=float)
        if self.click_tip.isChecked():
            what = f"tip over slot {slot} ({xy[0]:.1f}, {xy[1]:.1f})"
        else:
            profile = self.session.profile
            offset = (profile.calibration.pipette_offset
                      if profile is not None else None)
            if offset is None:
                self.jog.tell("Refused: no tip calibration.")
                return
            xy = xy - np.array([offset.dx, offset.dy])
            what = f"camera over slot {slot} ({xy[0]:.1f}, {xy[1]:.1f})"
        why = self.session.motion.refused((xy[0], xy[1], xyz(robot)[2]))
        if why:
            self.jog.tell(f"UNREACHABLE: {what} - {why}")
            return
        if not self.armed.isChecked():
            self.jog.tell(f"{what} - switch on Click to move to go there")
            return
        self.deck.select(slot)
        log.info("%s", what)

        def job(log_):
            self.session.motion.camera_to(xy, log=log_)
            return f"over slot {slot}"

        self.jog.run_job(job)

    def _show_deck(self) -> None:
        state = self.session.run_state
        held = (state.labware if state is not None
                and self.session.robot is not None else {})
        self.deck.set_labware({slot: entry.load_name
                               for slot, entry in held.items()})
        profile = self.session.profile
        modules = list(profile.deck.modules) if profile is not None else []
        self.deck.set_modules({str(slot): f"+{m.height_mm:g} mm · "
                                          f"{m.name or 'module'}"
                               for m in modules for slot in m.slots})

    # -- the well ------------------------------------------------------------------

    def _reload_plates(self) -> None:
        """The run's plates in the combo, the one in hand kept; the deck map
        with them."""
        self._show_deck()
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
        level = self.well_level.currentText()
        # From the measured center where there is one, as a picking run
        # deposits: its x and y only, the level keeps the robot's heights.
        center = self._well_center(slot, definition.load_name)
        dx, dy = well_center.deposit_offset(self.session.profile.picking,
                                            well_center.xy(center))
        offset = (dx, dy, float(self.well_offset.value()))
        volume, rate = self._liquid_amount()

        def job(log):
            self.session.motion.to_well(labware_id, well, level, offset,
                                        log=log)
            said = f"tip in {well} of slot {slot} ({level} {offset[2]:+g} mm)"
            if center is not None:
                said += ", from the measured center"
            if drop:
                log(f"dispense {volume:g} µl at {rate:g} µl/s")
                moves.dispense(robot, volume, rate)
                said += f"; dispensed {volume:g} µl"
            return said

        log.info("going to well %s in slot %s", well, slot)
        self.jog.run_job(job)

    # -- the well center ----------------------------------------------------------

    def _well_center(self, slot, load_name):
        profile = self.session.profile
        return (profile.deck.well_center(slot, load_name)
                if profile is not None else None)

    def _chosen_well(self):
        """(slot, labware id, definition, well) chosen in the Well card, or
        None while the well typed is not one of the plate's."""
        data = self.plate_choice.currentData()
        if data is None:
            return None
        well = self.well_choice.currentText().strip().upper()
        return (*data, well) if well in data[2].wells else None

    def _run_using(self, slot, load_name: str) -> str | None:
        """The run going into this plate now, if any
        (`well_center.run_using`)."""
        return well_center.run_using(self.session.robot_lease, slot,
                                     load_name)

    def _shown_nominal(self):
        """The last Go to well top, while its plate is still the chosen one."""
        data = self.plate_choice.currentData()
        if data is None:
            return None
        return self._measurement.for_plate(data[0], data[2].load_name)

    def _go_to_well_top(self) -> None:
        chosen, robot = self._chosen_well(), self.session.robot
        if chosen is None or robot is None or self.jog.busy:
            return
        slot, labware_id, definition, well = chosen
        load_name, motion = definition.load_name, self.session.motion

        def job(log):
            return well_center.go_to_top(motion, labware_id, well, log=log)

        def reached(pose):
            # On the GUI thread, once the job has ended.
            self._measurement.started(slot, load_name, well, pose)
            self._refresh()
            return well_center.REACHED.format(well=well)

        log.info("manual control: to the top of %s in slot %s", well, slot)
        if not self.jog.run_job(job, then=reached):
            self.jog.tell("Robot busy.")

    def _set_well_center(self) -> None:
        nominal, pose = self._shown_nominal(), self.jog.pose
        if nominal is None or pose is None or self.jog.busy:
            return
        slot, load_name, well = nominal.slot, nominal.load_name, nominal.well
        offset = self._measurement.offset(pose)
        if self._well_center(slot, load_name) is not None:
            answer = QMessageBox.question(
                self, "Set well center",
                f"Replace the measured center of {load_name} in slot "
                f"{slot}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        depth = well_center.definition_depth(
            self.plate_choice.currentData()[2], well)
        try:
            self.session.set_well_center(slot, load_name, offset, well,
                                         depth_mm=depth)
        except Exception as exc:                 # noqa: BLE001
            self.jog.tell(f"not saved: {exc}")
            return
        # Measured: measuring again starts from Go to well top.
        self._measurement.forget()
        self._refresh()
        self.jog.tell(f"well center of slot {slot} saved: ({offset[0]:+.2f}, "
                      f"{offset[1]:+.2f}, {offset[2]:+.2f}) mm")

    def _forget_well_center(self) -> None:
        data = self.plate_choice.currentData()
        if data is None:
            return
        slot, load_name = data[0], data[2].load_name
        if (self._well_center(slot, load_name) is None
                or self._run_using(slot, load_name)):
            return
        answer = QMessageBox.question(
            self, "Forget well center",
            f"Forget the measured center of {load_name} in slot {slot}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.session.forget_well_center(slot, load_name)

    def _show_center(self, has_robot: bool) -> None:
        """The well center line and its buttons, for the chosen plate."""
        data = self.plate_choice.currentData()
        idle = not self.jog.busy
        center = (self._well_center(data[0], data[2].load_name)
                  if data is not None else None)
        nominal = self._shown_nominal()
        self.go_top_button.setEnabled(idle and has_robot
                                      and self._chosen_well() is not None)
        self.set_center_button.setEnabled(idle and nominal is not None
                                          and self.jog.pose is not None)
        running = (self._run_using(data[0], data[2].load_name)
                   if data is not None else None)
        self.forget_center_button.setEnabled(idle and center is not None
                                             and running is None)
        self.forget_center_button.setToolTip(
            well_center.FORGET_TIP if running is None
            else well_center.in_use_tip(running))
        if data is None:
            text = ""
        elif center is not None:
            text = f"Well center {center.describe()}."
        else:
            text = "Well center not measured."
        if nominal is not None:
            text += "\n" + well_center.IN_HAND.format(well=nominal.well)
        self.center_state.setText(text)
        self.center_state.setVisible(bool(text))

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
        worker.what = worker.what or what
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
            self._show_sizes()
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
        self._refresh_memo_buttons()
        has_robot = self.session.robot is not None
        self.aspirate_button.setEnabled(has_robot)
        self.dispense_button.setEnabled(has_robot)
        has_plate = self.plate_choice.count() > 0
        self.well_button.setEnabled(has_robot and has_plate
                                    and self.well_choice.count() > 0)
        self.well_state.setText(
            "" if has_plate else
            "No plate loaded.")
        self.well_state.setVisible(not has_plate)
        self._show_center(has_robot)
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
                         f"calibrated area, gray)")
            parts.append(text)
        if self._cuboids is not None:
            parts.append(self._cuboids.summary.replace("\n", "; "))
        if not parts:
            self.state.setText("Detect, then click a crosshair or cuboid.")
            return
        text = "\n".join(parts)
        if self._cross_standin:
            text += ("\nStand-in tip detector: the crosshairs are invented "
                     "from the frame's center.")
        if self._cuboids is not None and self._cuboids.standin:
            text += "\nStand-in cuboid detector: bright blobs, not the model."
        self.state.setText(text)

    def _on_profile_changed(self) -> None:
        profile = self.session.profile
        self._show_deck()                     # the modules are the profile's
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

    def _on_robot_busy(self, _holder) -> None:
        self._refresh()

    def _robot_changed(self, _state: str) -> None:
        self._measurement.forget()
        self._reload_plates()

    # -- cameras ----------------------------------------------------------------------

    def _camera(self):
        return self.session.camera(self.camera_choice.currentText())

    def _on_lower_camera(self) -> bool:
        label = self.camera_choice.currentText()
        return bool(label) and label == self.session.lower_camera_label

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
        # Said on the picture, not only when a click is refused: the
        # operator should know before clicking.
        self.view.set_status([LOWER_CAMERA_NOTE] if self._on_lower_camera()
                             else [])
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
