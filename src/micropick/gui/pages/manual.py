"""Manual Work (REDESIGN §6.6): the robot by hand, from the picture.

The jog pad drives the axes as before. What this page adds is the picture
as a way of pointing: a **left click moves**, a **right click marks a
point**.

* **A click moves the Camera** - the default, chosen over the picture's top
  right ("A click moves the [Camera | Tip]"). The gantry travels so that
  the clicked point comes under the pixel map's reference pixel - the
  crosshair in the middle of the upper camera's view. Near the edge of the
  frame the map is extrapolating, and the move still happens, with a note
  that it is approximate.
* **Or the Tip**: the tip goes over the clicked point - the pixel map, then
  the pipette offset (`workflows.manual.tip_target`) - and stays at the
  travel height, so it shows where it would go down without going down.
  Only inside the area the map was fitted over: outside it the map is
  extrapolating, and a tip is not something to put somewhere
  approximately.
* **A click on a detected cuboid sends the tip to it**, at the profile's
  pickup height (dish bottom + pickup offset), one number shared with the
  run.

Every click-move is checked against the jog envelope first
(`Motion.refused`: the soft limits and the deck modules' tops) and refused
on the picture when it falls outside, and goes through the jog panel's
`run_job`, so it claims the robot's lease and cannot overlap a key press or
another page's work. There is no Click to move switch any more (§12.7):
the click itself is the deliberate act. A double click is not a click
(`CameraView`): it moves nothing and, here, does not fit the picture.

Beside the segmented choice, **Camera back to center** puts the camera over
the dish again (the position over the dish, `observe`), and **Tip back to
center** puts the tip over that same spot at the travel height.

Over the top left is the **TipGauge**: what is in the tip, counted here
from the page's aspirates and dispenses and emptied when the tip changes,
with the robot's X, Y and Z under it.

Memo points
-----------
A right click on the picture marks a point where the tip would go down
there (`Session.memo_points`, named with the lowest free number, M1, M2
...), listed in the Points card, where X, Y and Z are edited and the ×
deletes one (`Session.delete_memo_point`). They are drawn on the picture
only while the points are shown (the Points card open; REDESIGN §6.6 ties
it to "At a point"), and marking one opens them. The tip goes to one only
by its Go to. They last until the program closes; a point to keep is a
position (the jog panel's Positions). Marking moves nothing.

A point the tip cannot go to is said where it is made and shown in red,
on the picture and in its Z (`Session.memo_problem`): the jog envelope's
checks, the one that matters being a Z below the top of a deck module.
The modules are the profile's, over the deck definition's slots in the
robot's frame, which is the frame a point's X and Y are in - so a point
marked over a 64 mm platform at z 50 is known to be inside it.

The height is the part a click cannot give. A new point takes New point Z,
and New point Z is the last height given: typed into a point's Z, or taken
from the tip with "Z from tip" after jogging it down to where it should
be. Before any height is given New point Z is "travel height": a point
with no Z is gone to at the travel height, so a first click never lowers
the tip into anything.

Past picks
----------
Where the tip aspirated on this page, in the order taken, kept until the
program closes. Show past picks (a toggle, filled while on) draws them: the
last bright, the older ones faint (`position_marks.pick_items`), so the
spots already taken from are visible.

Look at
-------
A MiniDeckDropdown of the whole deck: choosing a slot sends the **camera**
over that slot's center at the travel height (`core.deck.slot_center`, the
deck definition's geometry). The definition is in the robot's frame, the
tip's: the camera goes to it less the pipette offset. Nothing is aspirated.

Only the upper camera is shown: the pixel map is its, and a point on the
lower camera's picture is not a place on the deck.

Aspirate and dispense in place, with a volume and a flow rate, are on the
page and on A and D - the in-place commands the run uses, for trying a
pickup by hand. They are refused unless the robot reports a tip on.

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

After Analyze the dish, the cuboids' sizes against the profile's size
window are in a folded Sizes section: the Picking page's histogram
(`widgets.size_histogram`), the same drawing, so the two pages cannot
disagree about what a run would accept.

The keys - the jog panel's and these - are listed under the jog pad (its
Keys toggle); what the mouse does is the picture's mouse row.
"""

from __future__ import annotations

import logging
import time

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QMessageBox,
                               QVBoxLayout, QWidget)

from ...config.labware import LabwareError, resolve_definition
from ...core.calibration.pixel_map import PixelMap
from ...core.deck import slot_center
from ...hardware.protocols import xyz
from ...viz import overlays
from ...workflows import manual as moves
from ...workflows import well_center
from ...workflows.motion import WELL_LEVELS
from .. import keys
from ..auto_camera import CameraOpener
from ..detector import wanted_model
from ..labware_kinds import facts, kind
from ..readiness import DISH_POSITION
from ..session import Session
from ..theme import SPACING
from ..theme.factory import (Section, card, combo_box, compact,
                             double_spin_box, heading, muted_label,
                             primary_button, scroll_column, secondary_button)
from ..widgets.camera_view import CameraView
from ..widgets.card_columns import CardColumns
from ..widgets.controls import Segmented
from ..widgets.feed_row import FeedRow
from ..widgets.fold_card import FoldCard
from ..position_marks import PositionMarks
from ..widgets.jog_panel import JogPanel
from ..widgets.mini_deck import DECK_ROWS, DeckTile, MiniDeckDropdown
from ..widgets.tip_gauge import TipGauge
from ..widgets.memo_points import TRAVEL_TEXT, MemoTable, Z_TOP_MM
from ..widgets.size_histogram import SizeHistogram
from ..workers import Worker

__all__ = ["ManualPage"]

TITLE = "Manual control"

log = logging.getLogger(__name__)

PANEL_WIDTH = 400

# How far outside a cuboid's contour a click still counts as on it: the
# outline is a line one pixel wide, and a cuboid is a small thing to hit.
CUBOID_SNAP_PX = 6.0

# Liquid handling bounds: wide enough for any pipette on this robot, and a
# typo of an extra digit still stops at the top of the range.
VOLUME_RANGE = (0.1, 1000.0)
FLOW_RANGE = (0.1, 1000.0)

# (action in `gui.keys`, what it does, handler name). One list, so the
# jog pad's key list cannot offer a key that is not bound.
KEYS = (("aspirate", "aspirate", "_aspirate"),
        ("dispense", "dispense", "_dispense"))

# How far the tip may be sent from the chosen well level, either way.
WELL_OFFSET_RANGE = (-50.0, 50.0)

# What the mouse does on the picture, its row along the bottom (§3).
MOUSE_HELP = ("Left click   move", "Right click   mark a point",
              "Wheel   zoom", "Middle-drag   pan")

# "A click moves the [Camera | Tip]", in this order; the button beside it.
CLICK_MOVES = ("Camera", "Tip")
CENTER = ("Camera back to center", "Tip back to center")

# A plate behind the picture's words, as the TipGauge's.
OVER_PICTURE = ("color: #C9CED6; font-weight: 600; padding: 6px 8px; "
                "border-radius: 6px; background: rgba(10, 12, 14, 0.84);")


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
        # What is in the tip, counted from this page's aspirates and
        # dispenses; emptied when the tip changes.
        self._in_tip = 0.0
        self._tip = session.tip              # the tip it is counted for
        # Where the tip aspirated, (x, y), oldest first: the past picks.
        self._picks: list[tuple[float, float]] = []
        # The last Go to well top: the robot's own idea of that well's top
        # center, which Set well center measures the real one against.
        self._measurement = well_center.Measurement()
        self._forget_targets(redraw=False)

        self.opener = CameraOpener(session, self)
        self.view = CameraView(self)
        self.view.marks_default = True      # the memo points are drawn there
        # A double click moves nothing; it does not fit the picture either.
        self.view.double_click_fits = False
        self.view.clicked.connect(self._clicked)
        self.view.right_clicked.connect(self._mark)
        self.gauge = TipGauge(parent=self)
        self.gauge.set_position(None)       # the view's X, Y, Z is under it
        self.view.add_top_left_widget(self.gauge)
        self.view.add_top_widget(self._click_row())
        # The host is this page: its shortcuts are window-wide while the page
        # is showing, and go quiet with it.
        self.jog = JogPanel(session, shortcut_host=self, parent=self)
        self.jog.show_position_on(self.view)
        self.view.set_mouse_help(MOUSE_HELP)
        self.jog.moved.connect(self._forget_targets)
        # Emitted whenever a job ends: Go to well top's nominal is in, and
        # the buttons greyed for the job come back.
        self.jog.position_changed.connect(lambda _lines: self._refresh())
        self.jog.set_page_help(self._page_help())

        self.marks = PositionMarks.on(session, self.view)
        panel = CardColumns([self._tools_card(), self._sizes_card(),
                             self._liquid_card(), self._well_card(),
                             self._memo_card(), self.jog], self)

        body = QHBoxLayout()
        body.setSpacing(SPACING)
        body.addWidget(FeedRow(self.view, scroll_column(panel, PANEL_WIDTH)),
                       1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2, SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addLayout(body, 1)

        session.camera_opened.connect(lambda _l: self._show_camera())
        session.camera_closed.connect(lambda _l: self._show_camera())
        session.tip_changed.connect(self._tip_changed)
        session.robot_state_changed.connect(self._robot_changed)
        session.robot_busy_changed.connect(self._on_robot_busy)
        session.labware_changed.connect(lambda _s: self._reload_plates())
        session.labware_changed.connect(lambda _s: self._show_deck())
        session.profile_changed.connect(lambda _p: self._on_profile_changed())
        self._shortcuts = []
        self._install_shortcuts()
        session.settings_changed.connect(lambda _s: self._rebind())
        self._reload_plates()
        self._on_profile_changed()
        self.show_points(False)
        self._show_camera()

    # -- construction --------------------------------------------------------

    def _click_row(self) -> QWidget:
        """Over the picture's top right: "A click moves the [Camera | Tip]"
        and the way back to the dish center for the one chosen."""
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING)
        label = QLabel("A click moves the", row)
        label.setStyleSheet(OVER_PICTURE)
        self.click_mode = Segmented(list(CLICK_MOVES), parent=row)
        self.click_mode.changed.connect(lambda _i: self._refresh())
        self.center_button = compact(secondary_button(CENTER[0], row))
        self.center_button.clicked.connect(self._back_to_center)
        for widget in (label, self.click_mode, self.center_button):
            layout.addWidget(widget)
        return row

    def _tip_moves(self) -> bool:
        return self.click_mode.current() == CLICK_MOVES.index("Tip")

    def _tools_card(self) -> QWidget:
        """Analyze the dish and Show past picks, where Picking has Analyze
        and Shake; Look at under them."""
        box = card(self)
        row = QHBoxLayout()
        self.cuboid_button = primary_button("Analyze the dish", self)
        self.cuboid_button.clicked.connect(self._detect_cuboids)
        self.past_picks_button = secondary_button("Show past picks", self)
        self.past_picks_button.setCheckable(True)
        self.past_picks_button.toggled.connect(lambda _on: self._show_picks())
        row.addWidget(self.cuboid_button, 1)
        row.addWidget(self.past_picks_button, 1)
        box.layout().addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Look at"))
        self.look_at = MiniDeckDropdown(self, placeholder="Choose a slot")
        self.look_at.setToolTip("Move the camera over the slot.")
        self.look_at.slot_chosen.connect(self._slot_clicked)
        row.addWidget(self.look_at, 1)
        box.layout().addLayout(row)

        self.state = muted_label("", self)
        self.state.setWordWrap(True)
        self.state.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addWidget(self.state)
        return box

    def _memo_card(self) -> QWidget:
        """The memo points; drawn on the picture while this is open."""
        self.points_card = FoldCard("Points", parent=self)
        self.points_card.toggled.connect(lambda _open: self._points_toggled())
        body = self.points_card.body.layout()
        self.memo_table = MemoTable(self.session, self)
        self.memo_table.setToolTip("Double-click X, Y or Z to change it.")
        self.memo_table.chosen.connect(self._memo_chosen)
        self.memo_table.z_typed.connect(self._memo_z_typed)
        body.addWidget(self.memo_table)
        body.addWidget(muted_label("Right-click the picture to add a point.",
                                   self))

        self.new_z = _number_box(self, 0.0, (0.0, Z_TOP_MM), " mm")
        self.new_z.setSpecialValueText(TRAVEL_TEXT)
        self.new_z.setToolTip("Z of a new point: the last Z set.")
        row = QHBoxLayout()
        name = QLabel("New point Z")
        name.setMinimumWidth(90)
        row.addWidget(name)
        row.addWidget(self.new_z)
        row.addStretch(1)
        body.addLayout(row)

        self.memo_go = secondary_button("Go to", self)
        self.memo_go.setToolTip("Move the tip to the chosen point.")
        self.memo_go.clicked.connect(self._go_to_memo)
        self.memo_z = secondary_button("Z from tip", self)
        self.memo_z.setToolTip("Set the chosen point's Z to the tip's Z now.")
        self.memo_z.clicked.connect(self._memo_z_from_tip)
        row = QHBoxLayout()
        for button in (self.memo_go, self.memo_z):
            row.addWidget(button)
        body.addLayout(row)
        self.session.memo_points_changed.connect(self._refresh_memo_buttons)
        self.jog.pose_changed.connect(lambda _p: self._refresh_memo_buttons())
        self._refresh_memo_buttons()
        return self.points_card

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
                 for action, what, _ in KEYS])

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
        self._liquid("aspirate", "aspirated", moves.aspirate, +1)

    def _dispense(self) -> None:
        self._liquid("dispense", "dispensed", moves.dispense, -1)

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

    def _liquid(self, what: str, done: str, command, sign: int) -> None:
        """Aspirate (`sign` +1) or dispense (-1) where the tip is, through
        the jog queue; the gauge follows once it is done."""
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
            return xyz(robot)[:2]

        def then(at):
            self._liquid_done(sign * volume, at)
            return note

        # Nothing moves: what is detected on the picture still stands.
        if not self.jog.run_job(job, moves=False, then=then):
            self.jog.tell(self.session.robot_lease.refusal)

    def _liquid_done(self, change: float, at) -> None:
        """`change` µl more in the tip (less, when negative); an aspirate
        at `at`, the tip's (x, y), is a past pick."""
        self._set_in_tip(self._in_tip + change)
        if change > 0 and at is not None:
            self.record_pick(at)

    def _tip_changed(self, tip) -> None:
        """Another tip, or none: nothing is in it. The tip read again and
        found the same (`Session.read_tip` says so too) keeps the count."""
        same = (tip.attached and self._tip.attached
                and tip.pickup == self._tip.pickup)
        self._tip = tip
        if not same:
            self._set_in_tip(0.0)

    def _set_in_tip(self, ul: float) -> None:
        self._in_tip = max(0.0, ul)
        self.gauge.set_volume(round(self._in_tip, 3))

    # -- past picks ------------------------------------------------------------

    def record_pick(self, xy) -> None:
        """A past pick at the tip's (x, y): drawn, the last bright, while
        Show past picks is on."""
        self._picks.append((float(xy[0]), float(xy[1])))
        self._show_picks()

    def _show_picks(self) -> None:
        self.marks.set_picks(self._picks
                             if self.past_picks_button.isChecked() else [])

    # -- what the picture can be used for -------------------------------------

    def _map_problems(self) -> list[str]:
        """What stops a pixel from meaning a deck position right now."""
        session, out = self.session, []
        profile = session.profile
        if session.robot is None:
            out.append("Robot not connected.")
        if profile is None:
            out.append("No profile loaded.")
            return out
        if profile.pixel_map is None:
            out.append("Camera calibration: Not set.")
        camera = self._camera()
        if camera is None:
            out.append("Upper camera not open.")
        elif profile.pixel_map is not None:
            # A map fitted at one mode and applied at another misplaces
            # everything by a fraction of the field and says nothing.
            out += profile.pixel_map.check_camera(camera.resolution)
        return out

    # -- detecting ------------------------------------------------------------

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
        """A left click on the picture, in sensor pixels: the camera or the
        tip there, or the tip down to a detected cuboid."""
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
            index, world = target
            offset = profile.calibration.pipette_offset
            if offset is None:
                self.jog.tell("Refused: no tip calibration.")
                return
            xy = np.asarray(world) + np.array([offset.dx, offset.dy])
            z = float(profile.picking.pickup_height)
            # Checked before anything moves: a pickup that goes to the
            # cuboid and then cannot aspirate has disturbed the dish for
            # nothing.
            pick = self.auto_aspirate.isChecked()
            if pick and self._tip_problem("aspirate"):
                self.jog.tell(self._tip_problem("aspirate"))
                return
            volume, rate = self._liquid_amount()
            self._chosen = index
            self._redraw()
            what = f"tip to cuboid ({xy[0]:.2f}, {xy[1]:.2f}) at z {z:g}"
            position = (xy[0], xy[1], z)

            def job(log):
                motion.tip_to(xy, z, log=log)
                if pick:
                    log(f"aspirate {volume:g} µl at {rate:g} µl/s")
                    moves.aspirate(robot, volume, rate)
                    return f"aspirated {volume:g} µl at {rate:g} µl/s"
                return None

            def then(done):
                if pick:
                    self._liquid_done(volume, xy)
                return "; ".join(notes + ([done] if done else []))
        elif self._tip_moves():
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
            then = None
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
            then = None

        why = motion.refused(position)
        if why:
            self.jog.tell(f"UNREACHABLE: {what} - {why}")
            return
        log.info("%s", "; ".join([what] + notes))

        def run(log_):
            done = job(log_)
            if then is not None:
                return done
            return "; ".join(notes + ([done] if done else []))

        if not self.jog.run_job(run, then=then):
            self.jog.tell(self.session.robot_lease.refusal)

    def _back_to_center(self) -> None:
        """The camera over the dish again (the position over the dish), or
        the tip over that same spot at the travel height."""
        where = self._stored_dish()
        if where is None or self.jog.busy:
            return
        motion = self.session.motion
        if self._tip_moves():
            profile = self.session.profile
            offset = profile.calibration.pipette_offset
            if offset is None:
                self.jog.tell("Refused: no tip calibration.")
                return
            xy = np.asarray(where[:2], dtype=float) + np.array(
                [offset.dx, offset.dy])
            why = motion.refused((xy[0], xy[1], where[2]))
            if why:
                self.jog.tell(f"UNREACHABLE: tip over the dish center - {why}")
                return

            def job(log):
                motion.camera_to(xy, log=log)
                return "tip over the dish center"
        else:
            def job(log):
                motion.tip_to(where[:2], where[2], log=log)
                return "camera over the dish center"

        if not self.jog.run_job(job):
            self.jog.tell(self.session.robot_lease.refusal)

    def _stored_dish(self):
        profile = self.session.profile
        return None if profile is None else profile.positions.get(DISH_POSITION)

    # -- memo points -------------------------------------------------------------

    @property
    def points_shown(self) -> bool:
        return self.points_card.is_open()

    def show_points(self, shown: bool) -> None:
        """The memo points on the picture, and their list, or neither."""
        self.points_card.set_open(bool(shown))
        self._points_toggled()

    def _points_toggled(self) -> None:
        self.marks.show_memo(self.points_shown)

    def _mark(self, u: float, v: float) -> None:
        """A right click on the picture: a new point where the tip would go
        down at this pixel. Moves nothing."""
        problems = self._map_problems()
        if problems:
            self.jog.tell("Click: " + problems[0])
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
        self.show_points(True)
        if self.view.marks_box.isVisible():
            self.view.marks_box.setChecked(True)
        problem = self.session.memo_problem(point)
        self.jog.tell(f"{point.name} at ({point.x:.2f}, {point.y:.2f}), "
                      f"z {_memo_z_text(point.z)}"
                      + (f" - NOT REACHABLE: {problem}" if problem else ""))

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

    def _memo_z_from_tip(self) -> None:
        name, pose = self.memo_table.chosen_name, self.jog.pose
        if name is None or pose is None:
            return
        z = round(float(pose[2]), 2)
        self.session.change_memo_point(name, z=z)
        self._set_new_z(z)
        self.jog.tell(f"{name}: z {z:g}")
        self._say_memo_problem(name)

    def _go_to_memo(self) -> None:
        """The button: the only way to a point, as the jog panel's Go to."""
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
        """(index, deck xy) of the detected cuboid the click is on, or None.

        A cuboid whose outline the click is inside (give or take
        CUBOID_SNAP_PX), the nearest center if several. Cuboids outside the
        calibrated area are not targets.
        """
        detection = self._cuboids
        if detection is None or not len(detection.df):
            return None
        found = []
        df = detection.df
        for i, (contour, cx, cy) in enumerate(zip(df.contour, df.cX, df.cY)):
            inside = cv2.pointPolygonTest(
                np.asarray(contour, dtype=np.float32), (float(u), float(v)),
                True)
            if inside >= -CUBOID_SNAP_PX and self._cuboid_covered[i]:
                found.append((float(np.hypot(cx - u, cy - v)), i,
                              self._cuboid_worlds[i]))
        if not found:
            return None
        _, index, world = min(found, key=lambda f: f[0])
        return index, world

    # -- the deck ------------------------------------------------------------------

    def _slot_clicked(self, slot) -> None:
        """Look at: the camera over the slot's center, at the travel height.
        The center is in the robot's frame, which is the tip's: the camera
        is over it with the gantry a pipette offset short of it."""
        robot = self.session.robot
        if robot is None or self.jog.busy:
            return
        profile = self.session.profile
        offset = (profile.calibration.pipette_offset
                  if profile is not None else None)
        if offset is None:
            self.jog.tell("Refused: no tip calibration.")
            return
        xy = (np.asarray(slot_center(slot), dtype=float)
              - np.array([offset.dx, offset.dy]))
        what = f"camera over slot {slot} ({xy[0]:.1f}, {xy[1]:.1f})"
        why = self.session.motion.refused((xy[0], xy[1], xyz(robot)[2]))
        if why:
            self.jog.tell(f"UNREACHABLE: {what} - {why}")
            return
        log.info("%s", what)

        def job(log_):
            self.session.motion.camera_to(xy, log=log_)
            return f"over slot {slot}"

        if not self.jog.run_job(job):
            self.jog.tell(self.session.robot_lease.refusal)

    def _show_deck(self) -> None:
        """Look at's tiles: every slot, with what the run holds there."""
        state = self.session.run_state
        held = (state.labware if state is not None
                and self.session.robot is not None else {})
        tiles = []
        for row in DECK_ROWS:
            for slot in row:
                entry = held.get(str(slot))
                if entry is None:
                    tiles.append(DeckTile(slot))
                    continue
                try:
                    definition = resolve_definition(entry.load_name)
                except LabwareError:
                    tiles.append(DeckTile(slot, name=entry.load_name))
                    continue
                tiles.append(DeckTile(slot, name=definition.display_name,
                                      kind=kind(definition),
                                      facts=facts(definition)))
        self.look_at.set_tiles(tiles)

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
            return well_center.AT_TOP.format(well=well)

        log.info("manual control: to the top of %s in slot %s", well, slot)
        if not self.jog.run_job(job, then=reached):
            self.jog.tell(self.session.robot_lease.refusal)

    def _set_well_center(self) -> None:
        nominal, pose = self._shown_nominal(), self.jog.pose
        if (nominal is None or pose is None or self.jog.busy
                or self._run_using(nominal.slot, nominal.load_name)):
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
        running = (self._run_using(data[0], data[2].load_name)
                   if data is not None else None)
        self.go_top_button.setEnabled(idle and has_robot
                                      and self._chosen_well() is not None)
        self.set_center_button.setEnabled(idle and nominal is not None
                                          and self.jog.pose is not None
                                          and running is None)
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
            text += "\n" + well_center.AT_TOP.format(well=nominal.well)
        self.center_state.setText(text)
        self.center_state.setVisible(bool(text))

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
        items = []
        detection = self._cuboids
        if detection is not None and self._frame_shape is not None:
            items += overlays.items(
                self._frame_shape, cuboid_df=detection.df,
                pickable=detection.pickable if detection.classified else None,
                isolated=detection.isolated if detection.classified else None,
                bubbles=detection.bubbles if detection.classified else None)
            if self._chosen is not None:
                row = detection.df.iloc[[self._chosen]]
                items += overlays.items(self._frame_shape, chosen=row)
        self.view.set_overlay_items(items)

    def _refresh(self) -> None:
        busy = self._busy()
        problems = self._map_problems()
        self.cuboid_button.setEnabled(not busy and not problems)
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
        self._refresh_center_button()
        # The found count is on the picture, as on Picking.
        self.view.set_status(
            [f"{len(self._cuboids.df)} cuboids found"]
            if self._cuboids is not None else [])
        if busy:
            return
        if problems:
            self.state.setText("\n".join("• " + p for p in problems))
            return
        text = ""
        if self._cuboids is not None and self._cuboids.standin:
            text = "Stand-in cuboid detector: bright blobs, not the model."
        self.state.setText(text)

    def _refresh_center_button(self) -> None:
        tip = self._tip_moves()
        self.center_button.setText(CENTER[int(tip)])
        if self._stored_dish() is None:
            self.center_button.setEnabled(False)
            self.center_button.setToolTip("Position over the dish: Not set.")
        else:
            self.center_button.setEnabled(self.session.robot is not None)
            self.center_button.setToolTip(
                "Move the tip over the dish center." if tip
                else "Move the camera over the dish center.")

    def _on_profile_changed(self) -> None:
        profile = self.session.profile
        self._show_deck()
        # The same profile saved again - a setting changed - keeps what was
        # detected. A different profile is a different map, offset and
        # model, and nothing measured belongs to it.
        if profile is not getattr(self, "_profile", None):
            self._profile = profile
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
        self._show_deck()

    # -- the camera -------------------------------------------------------------------

    def _camera(self):
        """The upper camera: the pixel map is its."""
        return self.session.camera(self.session.upper_camera_label)

    def _show_camera(self) -> None:
        camera = self._camera()
        if camera is not self.view.camera:
            # What was detected was another picture.
            self._forget_targets()
        self.view.set_camera(camera)
        self._refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        for shortcut in self._shortcuts:
            shortcut.setEnabled(True)
        # Jogging is done by eye through this camera; without it the page is
        # a jog pad over a blank rectangle.
        self.opener.ensure(self.session.upper_camera_label)
        self._show_camera()

    def hideEvent(self, event) -> None:
        for shortcut in self._shortcuts:
            shortcut.setEnabled(False)
        super().hideEvent(event)
