"""Manual pick's Run step (REDESIGN §6.6): the robot by hand, from the
picture.

The jog pad drives the axes as before. What this page adds is the picture
as a way of pointing: a **left click moves the camera**, a **right click
marks a point**.

The gantry travels so that the clicked point comes under the pixel map's
reference pixel - the crosshair in the middle of the upper camera's view.
Near the edge of the frame the map is extrapolating, and the move still
happens, with a note that it is approximate. A click on a detected cuboid
is a click like any other: the tip goes down only by Aspirate (#59), since
a click that lowers the tip by surprise is one into whatever is there.

The tip could also be sent over the clicked point (`click_mode`, "Tip":
the pixel map, then the pipette offset, `workflows.manual.tip_target`, at
the travel height). The choice is not shown and stays on Camera, as
Picking's pickup clips switch: one click, one meaning.

There is no way back to the dish center here: the work is often not from
the source dish, so a button aimed at it has no target the operator can
guess. Picking keeps its Camera to dish center.

Every click-move is checked against the jog envelope first
(`Motion.refused`: the soft limits and the deck modules' tops) and refused
on the picture when it falls outside, and goes through the jog panel's
`run_job`, so it claims the robot's lease and cannot overlap a key press or
another page's work. There is no Click to move switch any more (§12.7):
the click itself is the deliberate act. A double click is not a click
(`CameraView`): it moves nothing and, here, does not fit the picture.

Over the top left is the **TipGauge**: what is in the tip, as the session
counts it (`Session.in_tip`, from aspirates and dispenses, emptied when the
tip changes), with the robot's X, Y and Z under it. It is the answer to
Aspirate: there is no "Aspirated 15 µl" besides.

Memo points
-----------
A right click on the picture marks a point where the tip would go down
there (`Session.memo_points`, named with the lowest free number, M1, M2
...); so does Place memo at crosshair, at the spot under the picture's
center where By hand aims, with no Z (gray without a pixel map). They are
listed in the Memo points card, where X, Y and Z are edited and the ×
deletes one (`Session.delete_memo_point`), and last until the program
closes. Save in a row keeps it in the profile (`Session.save_memo_point`,
#62), a point at the travel height with that height as its Z, and moves the
row to the Saved points card under it, where the name is renamed by a
double click and the × asks first (#66). Robot setup's positions are not
points (#61). Marking moves nothing, and a double click only edits.

A point is a place on the deck, the tip's, with no purpose of its own.
Under both cards, for the one point chosen in either: Camera to (the camera
over it at the travel height, its Z unused) and Tip to (the tip down to its
Z, no higher than the travel height; gray while no tip is on), each
checked against the jog envelope and through `JogPanel.run_job`. Aspirate's
and Dispense's "At a point" list both kinds and go to one by aspirating or
dispensing there.

They are drawn on the picture, a saved point with a filled disc, only while
a points card is open or "At a point" is chosen in an open Aspirate or
Dispense card; marking one opens Memo points when nothing shows them.

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

Aspirate
--------
The operator composes the work from Aspirate and Dispense; nothing is a
script. Aspirate's settings are one line, "0.5 mm above the bottom · 15 µl
· 50 µl/s", and Change unfolds them. The height is above the bottom of
where the tip goes down: the measured dish bottom (`PickingConfig.
dish_bottom`) by hand, the well's bottom in a well. From:

* **In a well** - a plate the robot session holds and its well.
* **At a point** - a memo or saved point, at its own Z.
* **By hand** - the spot under the camera's crosshair (#60): the
  picture's center through the pixel map, plus the measured tip offset.
  The operator aims with the picture: a click moves the camera, and with
  Snap to found cuboids on, a click on a detected cuboid moves it so the
  crosshair is on the cuboid's center (a click beside every cuboid moves
  to the point). The height is a Z, or a height above the dish bottom -
  offered only while the dish bottom was measured or reused in this robot
  session (`Session.is_confirmed`), the default then: an older bottom is
  not one to lower a tip to. Aspirate remembers the camera's pose, takes
  the tip to the spot at the travel height (`Motion.tip_to`), lowers it,
  aspirates, and brings the camera back - one job, so nothing comes
  between. With Aspirate as soon as I click, a click aims and does all
  that.

Aspirate is enabled once there is a source - By hand always has one, so
there it is enabled whenever a tip is on and the robot is free. It goes
there (through the jog panel's `run_job`, after the jog envelope's check)
and aspirates (`Session.aspirate`); the spot is a past pick. Refused
unless the robot reports a tip on.

Dispense
--------
Under Aspirate, opening by itself when the tip comes to hold something:
All or Part (µl, no more than is in the tip: `Session.dispense`), where:

* **In a well** - the plate and well, and a mini map of the plate plan's
  plate (filled and still planned wells; a click chooses that well), so
  the wells a run left empty can be filled by hand. The tip goes to the
  well's bottom plus the picking settings' deposit offset, from the
  plate's well center, as a run deposits.
* **At a point** - a memo or saved point.
* **Here** - where the tip is.

Its button says what it does - "Go to A7 and dispense", "Dispense here" -
and is gray while the tip is empty. A and D aspirate and dispense where
the tip is, with Aspirate's volume and speed and Dispense's amount.

The well center
---------------
Dispense into a well goes from the plate's measured well center where
there is one (`config.schema.WellCenter`, per slot and plate type): its x
and y are added to the profile's well offsets, as a picking run adds them
to its deposit, and the level keeps the robot's own heights. It is
measured on the step before, Well center (`pages.well_center`, #63).

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
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QVBoxLayout,
                               QWidget)

from ...config.labware import LabwareError, resolve_definition
from ...core.calibration.pixel_map import PixelMap
from ...core.deck import slot_center
from ...hardware.protocols import xyz
from ...viz import overlays
from ...workflows import manual as moves
from ...workflows import well_center
from .. import keys
from ..auto_camera import CameraOpener
from ..detector import wanted_model
from ..labware_kinds import facts, kind
from ..session import Session
from ..theme import COLORS, SPACING
from ..theme.factory import (Section, card, combo_box, compact,
                             double_spin_box, muted_label, primary_button,
                             scroll_column, secondary_button)
from ..widgets.camera_view import CameraView
from ..widgets.card_columns import CardColumns
from ..widgets.controls import Segmented, Switch
from ..widgets.feed_row import FeedRow
from ..widgets.fold_card import FoldCard
from ..position_marks import PositionMarks
from ..widgets.jog_panel import JogPanel
from ..widgets.mini_deck import DECK_ROWS, DeckTile, MiniDeckDropdown
from ..widgets.plate_grid import PlateGrid
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

# Aspirate's height above the bottom where the tip goes down.
ABOVE_BOTTOM_RANGE = (0.0, 30.0)

# (action in `gui.keys`, what it does, handler name). One list, so the
# jog pad's key list cannot offer a key that is not bound.
KEYS = (("aspirate", "aspirate here", "_aspirate_here"),
        ("dispense", "dispense here", "_dispense_here"))

# Aspirate from, Dispense how much and where: the segmented choices.
FROM = ("In a well", "At a point", "By hand")
AMOUNT = ("All", "Part")
WHERE = ("In a well", "At a point", "Here")

# By hand's height: a Z, or above the dish bottom of this robot session.
HEIGHT = ("Z", "Above the dish bottom")

# What the mouse does on the picture, its row along the bottom (§3).
MOUSE_HELP = ("Right click   mark a point", "Wheel   zoom",
              "Middle-drag   pan")
CLICK_HELP = "Left click   move"

# What a click moves, in this order: never shown, fixed to Camera.
CLICK_MOVES = ("Camera", "Tip")


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


POINTS_TIP = "Double-click X, Y, Z or a saved point's name to change it."


def _memo_z_text(z) -> str:
    return TRAVEL_TEXT if z is None else f"{z:g}"


def _row(box, label: str, widget) -> QWidget:
    """A labeled row added to `box`; returned, to be hidden as one."""
    row = QWidget(box)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    name = QLabel(label, row)
    name.setMinimumWidth(90)
    layout.addWidget(name)
    layout.addWidget(widget)
    layout.addStretch(1)
    box.layout().addWidget(row)
    return row


class _WellPicker(QWidget):
    """Plate and Well: a plate the robot session holds and one of its
    wells. `changed` when either does."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.plate = combo_box(self)
        self.plate.currentIndexChanged.connect(lambda _i: self._show_wells())
        self.well = combo_box(self)
        # Typed as well as chosen: "H12" is quicker than scrolling to it,
        # and the completer the editable box brings finds it either way.
        self.well.setEditable(True)
        self.well.setInsertPolicy(self.well.InsertPolicy.NoInsert)
        self.well.currentTextChanged.connect(lambda _t: self.changed.emit())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for label, widget in (("Plate", self.plate), ("Well", self.well)):
            row = QHBoxLayout()
            name = QLabel(label)
            name.setMinimumWidth(90)
            row.addWidget(name)
            row.addWidget(widget, 1)
            layout.addLayout(row)

    def set_plates(self, plates) -> None:
        """`Session.plates()` in the combo, the one in hand kept."""
        chosen = self.plate.currentData()
        chosen_slot = chosen[0] if chosen else None
        self.plate.blockSignals(True)
        self.plate.clear()
        for slot, entry, definition in plates:
            self.plate.addItem(f"slot {slot} - {definition.display_name}",
                               (slot, entry.labware_id, definition))
        slots = [self.plate.itemData(i)[0] for i in range(self.plate.count())]
        if chosen_slot in slots:
            self.plate.setCurrentIndex(slots.index(chosen_slot))
        self.plate.blockSignals(False)
        self._show_wells()

    def _show_wells(self) -> None:
        """The chosen plate's wells, row by row, the one typed kept."""
        typed = self.well.currentText()
        data = self.plate.currentData()
        wells = []
        if data is not None:
            ordering = data[2].ordering
            for row in range(max((len(c) for c in ordering), default=0)):
                wells += [column[row] for column in ordering
                          if row < len(column)]
        self.well.blockSignals(True)
        self.well.clear()
        self.well.addItems(wells)
        if typed in wells:
            self.well.setCurrentIndex(wells.index(typed))
        self.well.blockSignals(False)
        self.changed.emit()

    def chosen(self):
        """(slot, labware id, definition, well), or None while the well
        typed is not one of the plate's."""
        data = self.plate.currentData()
        if data is None:
            return None
        well = self.well.currentText().strip().upper()
        return (*data, well) if well in data[2].wells else None

    def choose(self, slot, well: str) -> None:
        """The plate in `slot` and its `well`, if the session holds it."""
        for i in range(self.plate.count()):
            if str(self.plate.itemData(i)[0]) == str(slot):
                self.plate.setCurrentIndex(i)
                self.well.setCurrentText(well)
                return


class ManualPage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._worker: Worker | None = None
        # Whether the dish bottom was known in this robot session when last
        # looked: Above the dish bottom becomes the default as it becomes so.
        self._bottom_known = False
        # Where the tip aspirated, (x, y), oldest first: the past picks.
        self._picks: list[tuple[float, float]] = []
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
        # Kept, never shown: see the module's docstring.
        self.click_mode = Segmented(list(CLICK_MOVES), parent=self)
        self.click_mode.hide()
        # The host is this page: its shortcuts are window-wide while the page
        # is showing, and go quiet with it.
        self.jog = JogPanel(session, shortcut_host=self, parent=self)
        self.jog.show_position_on(self.view)
        self.view.set_mouse_help(MOUSE_HELP)
        self.view.set_click_to_move(CLICK_HELP, self._click_reaches)
        self.jog.moved.connect(self._forget_targets)
        # Emitted whenever a job ends: the buttons greyed for the job come
        # back.
        self.jog.position_changed.connect(lambda _lines: self._refresh())
        self.jog.set_page_help(self._page_help())

        self.marks = PositionMarks.on(session, self.view)
        panel = CardColumns([self._tools_card(), self._sizes_card(),
                             self._aspirate_card(), self._dispense_card(),
                             self._points_cards(), self.jog], self)

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
        session.in_tip_changed.connect(self._in_tip_changed)
        session.tip_changed.connect(lambda _t: self._refresh())
        session.routine_changed.connect(lambda _r: self._show_plan())
        session.task_requested.connect(self._task_requested)
        session.robot_state_changed.connect(self._robot_changed)
        session.robot_busy_changed.connect(self._on_robot_busy)
        session.labware_changed.connect(lambda _s: self._reload_plates())
        session.labware_changed.connect(lambda _s: self._show_deck())
        session.profile_changed.connect(lambda _p: self._on_profile_changed())
        session.confirmations_changed.connect(self._bottom_changed)
        self._shortcuts = []
        self._install_shortcuts()
        session.settings_changed.connect(lambda _s: self._rebind())
        self._reload_plates()
        self._on_profile_changed()
        self._show_plan()
        self.gauge.set_volume(session.in_tip)
        self._bottom_changed()
        self._source_changed()
        self._target_changed()
        self._show_camera()

    # -- construction --------------------------------------------------------

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

    def _aspirate_card(self) -> QWidget:
        """The settings as one line, From, and Aspirate; see "Aspirate"."""
        self.aspirate_card = FoldCard("Aspirate", open=True, parent=self)
        self.aspirate_card.toggled.connect(lambda _open: self._source_changed())
        body = self.aspirate_card.body.layout()

        row = QHBoxLayout()
        self.settings_line = muted_label("", self)
        self.settings_line.setWordWrap(True)
        self.change_button = compact(secondary_button("Change", self))
        self.change_button.setCheckable(True)
        self.change_button.toggled.connect(
            lambda on: self.settings_box.setVisible(on))
        row.addWidget(self.settings_line, 1)
        row.addWidget(self.change_button)
        body.addLayout(row)
        self.settings_box = QWidget(self)
        QVBoxLayout(self.settings_box).setContentsMargins(0, 0, 0, 0)
        # By hand's height: Z, or above the dish bottom.
        self.height_mode = Segmented(list(HEIGHT), parent=self)
        self.height_mode.changed.connect(lambda _i: self._source_changed())
        self.height_row = _row(self.settings_box, "Height", self.height_mode)
        self.hand_z = _number_box(self, 70.0, (0.0, Z_TOP_MM), " mm")
        self.hand_z.setToolTip("Z of the tip's end.")
        self.above_bottom = _number_box(self, 0.5, ABOVE_BOTTOM_RANGE, " mm")
        self.above_bottom.setToolTip(
            "Above the dish bottom, or the well's bottom.")
        self.volume = _number_box(self, 10.0, VOLUME_RANGE, " µl", 1, 1.0)
        self.flow_rate = _number_box(self, 50.0, FLOW_RANGE, " µl/s", 1, 1.0)
        rows = {}
        for label, box in (("Z", self.hand_z),
                           ("Above bottom", self.above_bottom),
                           ("Volume", self.volume),
                           ("Speed", self.flow_rate)):
            rows[label] = _row(self.settings_box, label, box)
            box.valueChanged.connect(lambda _v: self._show_settings_line())
        self.hand_z_row = rows["Z"]
        self.above_row = rows["Above bottom"]
        self.settings_box.setVisible(False)
        body.addWidget(self.settings_box)

        self.source = Segmented(list(FROM), current=FROM.index("By hand"),
                                parent=self)
        self.source.changed.connect(lambda _i: self._source_changed())
        body.addWidget(self.source)

        self.source_wells = _WellPicker(self)
        self.source_wells.changed.connect(self._refresh)
        body.addWidget(self.source_wells)

        # Both kinds, to aspirate from; made and moved to in their cards.
        self.source_points = self._points_table(None)
        self.source_points.chosen.connect(lambda _name: self._refresh())
        body.addWidget(self.source_points)

        self.hand_box = QWidget(self)
        hand = QVBoxLayout(self.hand_box)
        hand.setContentsMargins(0, 0, 0, 0)
        self.snap = Switch("Snap to found cuboids", checked=True,
                           parent=self.hand_box)
        self.snap.setToolTip(
            "A click on a found cuboid puts the crosshair on its center.")
        self.aspirate_on_click = Switch("Aspirate as soon as I click",
                                        parent=self.hand_box)
        for widget in (self.snap, self.aspirate_on_click):
            hand.addWidget(widget)
        body.addWidget(self.hand_box)

        self.aspirate_button = primary_button("Aspirate", self)
        self.aspirate_button.clicked.connect(self._aspirate_from_source)
        body.addWidget(self.aspirate_button)
        self._show_settings_line()
        return self.aspirate_card

    def _dispense_card(self) -> QWidget:
        """How much and where, and a button that says it; see "Dispense"."""
        self.dispense_card = FoldCard("Dispense", parent=self)
        self.dispense_card.toggled.connect(lambda _open: self._target_changed())
        body = self.dispense_card.body.layout()

        row = QHBoxLayout()
        self.amount = Segmented(list(AMOUNT), parent=self)
        self.amount.changed.connect(lambda _i: self._target_changed())
        self.part = _number_box(self, 10.0, VOLUME_RANGE, " µl", 1, 1.0)
        self.part.valueChanged.connect(lambda _v: self._refresh())
        row.addWidget(self.amount)
        row.addWidget(self.part)
        row.addStretch(1)
        body.addLayout(row)

        self.target = Segmented(list(WHERE), parent=self)
        self.target.changed.connect(lambda _i: self._target_changed())
        body.addWidget(self.target)

        self.target_wells = _WellPicker(self)
        self.target_wells.changed.connect(self._refresh)
        body.addWidget(self.target_wells)
        # The plate plan's plate: what a run filled and what it left.
        self.plan_box = QWidget(self)
        plan = QVBoxLayout(self.plan_box)
        plan.setContentsMargins(0, 0, 0, 0)
        self.plan_map = PlateGrid(mini=True, parent=self.plan_box)
        self.plan_map.well_clicked.connect(self._plan_well_clicked)
        plan.addWidget(self.plan_map)
        legend = QHBoxLayout()
        for name, color in (("Filled", "plate"), ("Planned", "planned")):
            legend.addWidget(QLabel(f'<span style="color: {COLORS[color]}">'
                                    f'&#9632;</span> {name}', self.plan_box))
        legend.addStretch(1)
        plan.addLayout(legend)
        body.addWidget(self.plan_box)

        self.target_points = self._points_table(None)
        self.target_points.chosen.connect(lambda _name: self._refresh())
        body.addWidget(self.target_points)

        self.dispense_button = primary_button("Dispense here", self)
        self.dispense_button.clicked.connect(self._dispense_to_target)
        body.addWidget(self.dispense_button)
        return self.dispense_card

    def _points_table(self, kind: str | None) -> MemoTable:
        table = MemoTable(self.session, self, kind=kind)
        table.setToolTip(POINTS_TIP)
        table.chosen.connect(self._marks_choose)
        table.z_typed.connect(lambda z: self._memo_z_typed(table, z))
        table.said.connect(self.jog.tell)
        return table

    def _points_cards(self) -> QWidget:
        """Memo points over Saved points, and under both the moves to the
        chosen one; see "Memo points"."""
        block = QWidget(self)
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING)

        self.memo_card = FoldCard("Memo points", parent=self)
        self.memo_card.toggled.connect(lambda _open: self._points_toggled())
        body = self.memo_card.body.layout()
        self.memo_table = self._points_table("memo")
        body.addWidget(self.memo_table)
        body.addWidget(muted_label("Right-click the picture to add a point.",
                                   self))
        self.place_memo_button = secondary_button("Place memo at crosshair",
                                                  self)
        self.place_memo_button.clicked.connect(self._place_memo)
        body.addWidget(self.place_memo_button)
        self.new_z = _number_box(self, 0.0, (0.0, Z_TOP_MM), " mm")
        self.new_z.setSpecialValueText(TRAVEL_TEXT)
        self.new_z.setToolTip("Z of a new point: the last Z set.")
        _row(self.memo_card.body, "New point Z", self.new_z)
        layout.addWidget(self.memo_card)

        self.saved_card = FoldCard("Saved points", parent=self)
        self.saved_card.toggled.connect(lambda _open: self._points_toggled())
        self.saved_table = self._points_table("saved")
        self.saved_card.body.layout().addWidget(self.saved_table)
        layout.addWidget(self.saved_card)

        # One point chosen across the two lists.
        for table, other in ((self.memo_table, self.saved_table),
                             (self.saved_table, self.memo_table)):
            table.chosen.connect(
                lambda name, other=other: name is not None
                and other.clearSelection())
        self.camera_to_button = secondary_button("Camera to", self)
        self.camera_to_button.setToolTip(
            "Move the camera over the chosen point.")
        self.camera_to_button.clicked.connect(self._camera_to_point)
        self.tip_to_button = secondary_button("Tip to", self)
        self.tip_to_button.clicked.connect(self._tip_to_point)
        self.memo_z = secondary_button("Z from tip", self)
        self.memo_z.setToolTip("Set the chosen point's Z to the tip's Z now.")
        self.memo_z.clicked.connect(self._memo_z_from_tip)
        row = QHBoxLayout()
        for button in (self.camera_to_button, self.tip_to_button,
                       self.memo_z):
            row.addWidget(button)
        layout.addLayout(row)
        self.session.memo_points_changed.connect(self._refresh_memo_buttons)
        self.jog.pose_changed.connect(lambda _p: self._refresh_memo_buttons())
        return block

    def _sizes_card(self) -> QWidget:
        """Folded: the sizes are a second look at a detection, not the
        reason to make one."""
        self.sizes = Section("Sizes", collapsed=True, parent=self)
        self.histogram = SizeHistogram(
            "Detect cuboids to see their sizes against the profile's "
            "cuboid_size_threshold, the shaded band.", self)
        self.sizes.body.layout().addWidget(self.histogram)
        return self.sizes

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

    def _show_settings_line(self) -> None:
        height = (f"Z {self.hand_z.value():g} mm" if self._hand_z_chosen()
                  else f"{self.above_bottom.value():g} mm above the bottom")
        self.settings_line.setText(
            f"{height} · {self.volume.value():g} µl · "
            f"{self.flow_rate.value():g} µl/s")

    def _hand_z_chosen(self) -> bool:
        """By hand, at a Z rather than above the dish bottom."""
        return (FROM[self.source.current()] == "By hand"
                and HEIGHT[self.height_mode.current()] == "Z")

    def _bottom_changed(self) -> None:
        """Above the dish bottom only while it was measured or reused in
        this robot session, and the default as it becomes so."""
        known = self.session.is_confirmed("dish_bottom")
        above = self.height_mode.buttons[HEIGHT.index("Above the dish bottom")]
        above.setEnabled(known)
        above.setToolTip("" if known else "Dish bottom: Not set.")
        if known != self._bottom_known:
            self.height_mode.set_current(HEIGHT.index(
                "Above the dish bottom" if known else "Z"))
            self._bottom_known = known
            self._source_changed()

    def _source_changed(self) -> None:
        """From's own controls under it; the points drawn or not."""
        chosen = FROM[self.source.current()]
        self.source_wells.setVisible(chosen == "In a well")
        self.source_points.setVisible(chosen == "At a point")
        self.hand_box.setVisible(chosen == "By hand")
        self.height_row.setVisible(chosen == "By hand")
        self.hand_z_row.setVisible(self._hand_z_chosen())
        self.above_row.setVisible(not self._hand_z_chosen())
        self._show_settings_line()
        self._points_toggled()
        self._refresh()

    def _target_changed(self) -> None:
        """Where's own controls under it, Part's field; the points drawn or
        not."""
        chosen = WHERE[self.target.current()]
        well = chosen == "In a well"
        self.target_wells.setVisible(well)
        self.plan_box.setVisible(well and bool(self.plan_map.wells))
        self.target_points.setVisible(chosen == "At a point")
        self.part.setVisible(AMOUNT[self.amount.current()] == "Part")
        self._points_toggled()
        self._refresh()

    def _by_hand(self) -> bool:
        """Aspirate takes from under the crosshair."""
        return (self.aspirate_card.is_open()
                and FROM[self.source.current()] == "By hand")

    def _source(self):
        """("well", (slot, labware id, definition, well)), ("point",
        MemoPoint) or ("hand", None), under the crosshair: what Aspirate
        takes from; None while nothing is chosen."""
        chosen = FROM[self.source.current()]
        if chosen == "In a well":
            well = self.source_wells.chosen()
            return None if well is None else ("well", well)
        if chosen == "At a point":
            point = self.session.point(self.source_points.chosen_name or "")
            return None if point is None else ("point", point)
        return ("hand", None)

    def _target(self):
        """("well", ...), ("point", MemoPoint) or ("here", None): where
        Dispense gives back; None while nothing is chosen."""
        chosen = WHERE[self.target.current()]
        if chosen == "In a well":
            well = self.target_wells.chosen()
            return None if well is None else ("well", well)
        if chosen == "At a point":
            point = self.session.point(
                self.target_points.chosen_name or "")
            return None if point is None else ("point", point)
        return ("here", None)

    def _dispense_text(self, target) -> str:
        if target is None:
            return ("Go to the well and dispense"
                    if WHERE[self.target.current()] == "In a well"
                    else "Go to the point and dispense")
        kind, what = target
        if kind == "well":
            return f"Go to {what[3]} and dispense"
        if kind == "point":
            return f"Go to {what.name} and dispense"
        return "Dispense here"

    def _well_offset(self, slot, definition, z: float):
        """(x, y, z) from a well's bottom: the plate's well center and the
        profile's well offsets, as a run deposits (`deposit_offset`)."""
        center = self._well_center(slot, definition.load_name)
        dx, dy = well_center.deposit_offset(self.session.profile.picking,
                                            well_center.xy(center))
        return (dx, dy, float(z))

    def _go_to(self, target, z: float | None):
        """The move to `target` - a well (`z` above its bottom), a point
        (its own Z) - as a job's first part,
        and what to call it; (None, "here") for no move. Raises ValueError
        with the reason when it is out of reach."""
        kind, what = target
        motion = self.session.motion
        if kind == "well":
            slot, labware_id, definition, well = what
            offset = self._well_offset(slot, definition, z)

            def go(log):
                motion.to_well(labware_id, well, "bottom", offset, log=log)
            return go, f"{well} of slot {slot}"
        if kind == "point":
            point = what
            xy = np.array([point.x, point.y])
            pose = self.jog.pose
            check_z = point.z if point.z is not None else (
                pose[2] if pose is not None else 0.0)
            why = motion.refused((point.x, point.y, check_z))
            if why:
                raise ValueError(f"UNREACHABLE: tip to {point.name} - {why}")
            # No Z: as high as a move may go, which is the travel height.
            to_z = float("inf") if point.z is None else point.z

            def go(log):
                motion.tip_to(xy, to_z, log=log)
            return go, point.name
        return None, "here"

    def _aspirate_from_source(self) -> None:
        """Aspirate: to the source, then aspirate there."""
        source = self._source()
        if source is None:
            return
        if source[0] == "hand":
            self._aspirate_by_hand()
        else:
            self._aspirate(source, float(self.above_bottom.value()))

    def _aspirate_here(self) -> None:
        self._aspirate(("here", None), None)

    def _aspirate(self, source, z) -> None:
        """Aspirate's volume and speed at `source` (see `_go_to`); the
        spot is a past pick."""
        problem = self._tip_problem("aspirate")
        if problem:
            self.jog.tell(problem)
            return
        try:
            go, where = self._go_to(source, z)
        except ValueError as exc:
            self.jog.tell(str(exc))
            return
        session, robot = self.session, self.session.robot
        volume, rate = self._liquid_amount()

        def job(log):
            if go is not None:
                go(log)
            log(f"aspirate {volume:g} µl at {rate:g} µl/s")
            session.aspirate(volume, rate)
            return xyz(robot)[:2]

        def then(at):
            self.record_pick(at)
            return f"aspirated {volume:g} µl at {rate:g} µl/s, {where}"

        # In place nothing moves: what is detected on the picture stands.
        if not self.jog.run_job(job, moves=go is not None, then=then):
            self.jog.tell(self.session.robot_lease.refusal)

    def _hand_z(self) -> float:
        """By hand's height: the Z, or above the dish bottom."""
        if self._hand_z_chosen():
            return float(self.hand_z.value())
        return (float(self.session.profile.picking.dish_bottom)
                + float(self.above_bottom.value()))

    def _aspirate_by_hand(self, aim=None) -> None:
        """By hand: the camera to `aim` first if given (gantry xy); then,
        from the camera's pose, the tip to the spot under the crosshair,
        down, aspirate, and the camera back to that pose. One job."""
        problem = self._tip_problem("aspirate") or self._crosshair_problem()
        if problem:
            self.jog.tell(problem)
            return
        motion = self.session.motion
        gantry = np.asarray(self.jog.pose[:2] if aim is None else aim,
                            dtype=float)
        spot = self._crosshair_spot(gantry)
        z = self._hand_z()
        what = f"tip to ({spot[0]:.2f}, {spot[1]:.2f}) at z {z:g}"
        why = motion.refused((spot[0], spot[1], z))
        if why:
            self.jog.tell(f"UNREACHABLE: {what} - {why}")
            return
        session, robot = self.session, self.session.robot
        volume, rate = self._liquid_amount()
        log.info("aspirate by hand: %s", what)

        def job(log_):
            if aim is not None:
                motion.camera_to(aim, log=log_)
            camera = np.array(xyz(robot), dtype=float)
            motion.tip_to(spot, z, log=log_)
            log_(f"aspirate {volume:g} µl at {rate:g} µl/s")
            session.aspirate(volume, rate)
            at = xyz(robot)[:2]
            motion.tip_to(camera[:2], camera[2], log=log_)
            return at

        def then(at):
            self.record_pick(at)
            return (f"aspirated {volume:g} µl at {rate:g} µl/s, "
                    f"({spot[0]:.2f}, {spot[1]:.2f})")

        if not self.jog.run_job(job, then=then):
            self.jog.tell(self.session.robot_lease.refusal)

    def _crosshair_problem(self) -> str:
        """Why the spot under the crosshair cannot be known now, or ""."""
        problems = self._map_problems()
        if problems:
            return problems[0]
        if self.session.profile.calibration.pipette_offset is None:
            return "Refused: no tip calibration."
        if self.jog.pose is None:
            return "Robot busy."
        return ""

    def _crosshair_spot(self, gantry) -> np.ndarray:
        """Where the tip goes down under the crosshair with the gantry at
        `gantry`: the picture's center through the pixel map, plus the
        measured tip offset."""
        profile = self.session.profile
        config = profile.calibration.pixel_map
        return moves.tip_target(PixelMap.from_config(config), config.cu,
                                config.cv, np.asarray(gantry, dtype=float),
                                profile.calibration.pipette_offset)

    def _dispense_to_target(self) -> None:
        target = self._target()
        if target is not None:
            self._dispense(target)

    def _dispense_here(self) -> None:
        self._dispense(("here", None))

    def _dispense(self, target) -> None:
        """All or Part at `target` (see `_go_to`); a well is given into at
        the deposit's height above its bottom."""
        problem = self._tip_problem("dispense")
        held = self.session.in_tip
        volume = (None if AMOUNT[self.amount.current()] == "All"
                  else float(self.part.value()))
        if not problem and held <= 0:
            problem = "REFUSED: nothing is in the tip."
        elif not problem and volume is not None and volume > held + 1e-6:
            problem = f"REFUSED: the tip holds {held:g} µl."
        if problem:
            self.jog.tell(problem)
            return
        z = (self.session.profile.picking.deposit_offset_z
             if target[0] == "well" else None)
        try:
            go, where = self._go_to(target, z)
        except ValueError as exc:
            self.jog.tell(str(exc))
            return
        session = self.session
        rate = float(self.flow_rate.value())

        def job(log):
            if go is not None:
                go(log)
            amount = session.dispense(rate, volume)
            return f"dispensed {amount:g} µl at {rate:g} µl/s, {where}"

        if not self.jog.run_job(job, moves=go is not None):
            self.jog.tell(self.session.robot_lease.refusal)

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

    def _in_tip_changed(self, ul: float) -> None:
        """The gauge; Dispense opens once the tip holds something."""
        self.gauge.set_volume(ul)
        if ul > 0 and not self.dispense_card.is_open():
            self.dispense_card.set_open(True)
        self._refresh()

    # -- the plan's plate ------------------------------------------------------

    def _show_plan(self) -> None:
        """Dispense's mini map: the plate plan's plate, its filled and still
        planned wells."""
        routine = self.session.routine
        destination = getattr(routine, "destination", None)
        if destination is None or not getattr(destination, "is_plate", False):
            self.plan_map.set_ordering([])
        else:
            self.plan_map.set_ordering(destination.ordering)
            self.plan_map.set_fills({
                well: COLORS["planned" if routine.remaining(well) else "plate"]
                for well in routine.plan})
        self._target_changed()

    def _task_requested(self, task: str, step: str) -> None:
        """Picking's Finish by hand: Dispense open on In a well, the first
        well the run left short chosen, the plan's map showing the rest."""
        if (task, step) != ("manual", "work"):
            return
        routine = self.session.routine
        destination = getattr(routine, "destination", None)
        if destination is None or not getattr(destination, "is_plate", False):
            return
        self.dispense_card.set_open(True)
        self.target.set_current(WHERE.index("In a well"))
        self._show_plan()
        short = [w for c in destination.ordering for w in c
                 if w in routine.plan and routine.remaining(w)]
        if short:
            self.target_wells.choose(destination.slot, short[0])

    def _plan_well_clicked(self, well: str) -> None:
        destination = getattr(self.session.routine, "destination", None)
        if destination is not None:
            self.target_wells.choose(destination.slot, well)

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

    def _click_reaches(self) -> bool:
        """Whether a left click is sent to the page: it moves, and only
        through the pixel map."""
        return self.session.has_pixel_map()

    def _clicked(self, u: float, v: float) -> None:
        """A left click on the picture, in sensor pixels: the camera there
        (see the module's docstring); By hand, onto a cuboid's center with
        Snap, and the aspirate after it with Aspirate as soon as I click."""
        problems = self._map_problems()
        if problems:
            self.jog.tell("Click: " + problems[0])
            return
        if self._busy() or self.jog.busy:
            return
        profile, robot = self.session.profile, self.session.robot
        motion = self.session.motion
        pmap = PixelMap.from_config(profile.pixel_map)
        gantry = np.array(xyz(robot)[:2])
        hand = self._by_hand()
        cuboid = (self._target_at(u, v)
                  if hand and self.snap.isChecked() else None)
        notes = []

        if cuboid is not None:
            # Its center under the crosshair, the reference pixel.
            xy = np.asarray(cuboid[1], dtype=float)
            what = f"camera to cuboid ({xy[0]:.2f}, {xy[1]:.2f})"
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
            xy = moves.tip_target(pmap, u, v, gantry, offset)
            what = f"tip over ({xy[0]:.2f}, {xy[1]:.2f}) at the travel height"
        else:
            xy = moves.camera_target(pmap, u, v, gantry)
            if not pmap.covers(u, v):
                notes.append("approximate: outside the calibrated area")
            what = f"camera to ({xy[0]:.2f}, {xy[1]:.2f})"

        why = motion.refused((xy[0], xy[1], xyz(robot)[2]))
        if why:
            self.jog.tell(f"UNREACHABLE: {what} - {why}")
            return
        if hand and self.aspirate_on_click.isChecked():
            self._aspirate_by_hand(aim=xy)
            return
        log.info("%s", "; ".join([what] + notes))

        def job(log_):
            # The gantry at the travel height is the tip at it: the same
            # move for the camera's target as for the tip's.
            motion.camera_to(xy, log=log_)
            return "; ".join(notes)

        if not self.jog.run_job(job):
            self.jog.tell(self.session.robot_lease.refusal)

    # -- memo points -------------------------------------------------------------

    @property
    def points_shown(self) -> bool:
        """A points card open, or "At a point" chosen in an open Aspirate
        or Dispense card."""
        return (self.memo_card.is_open() or self.saved_card.is_open()
                or (self.aspirate_card.is_open()
                    and FROM[self.source.current()] == "At a point")
                or (self.dispense_card.is_open()
                    and WHERE[self.target.current()] == "At a point"))

    def _points_toggled(self) -> None:
        # Built in turn: a card's toggle can come before the next exists.
        if hasattr(self, "saved_card") and hasattr(self, "dispense_card"):
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
        self._add_memo(xy, self._new_z())

    def _place_memo(self) -> None:
        """Place memo at crosshair: a point under the picture's center,
        where By hand aims, at the travel height. Moves nothing."""
        problem = self._crosshair_problem()
        if problem:
            self.jog.tell(problem)
            return
        self._add_memo(self._crosshair_spot(self.jog.pose[:2]), None)

    def _add_memo(self, xy, z) -> None:
        """A new memo point, chosen and shown: Memo points opens unless the
        points are shown already."""
        point = self.session.add_memo_point(xy[0], xy[1], z)
        if not self.points_shown:
            self.memo_card.set_open(True)
        self.memo_table.choose(point.name)
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

    def _memo_z_typed(self, table: MemoTable, z) -> None:
        self._set_new_z(z)
        self._say_memo_problem(table.chosen_name)

    def _say_memo_problem(self, name) -> None:
        point = self.session.point(name or "")
        problem = self.session.memo_problem(point) if point else ""
        if problem:
            self.jog.tell(f"{point.name} NOT REACHABLE: {problem}")

    def _marks_choose(self, name) -> None:
        if name is not None:
            self.marks.choose_memo(name)
        self._refresh()

    def chosen_point(self) -> str | None:
        """The point chosen in Memo points or Saved points: one at most."""
        return self.memo_table.chosen_name or self.saved_table.chosen_name

    def _refresh_memo_buttons(self) -> None:
        if not hasattr(self, "saved_table"):
            return
        chosen = self.chosen_point() is not None
        free = (self.session.robot is not None
                and not self.session.robot_lease.busy)
        tip = self.session.tip.attached is True
        self.camera_to_button.setEnabled(chosen and free)
        self.tip_to_button.setEnabled(chosen and free and tip)
        self.tip_to_button.setToolTip(
            "Move the tip to the chosen point." if tip
            else "Put a tip on first.")
        self.memo_z.setEnabled(chosen and self.jog.pose is not None)
        pixel_map = self.session.has_pixel_map()
        self.place_memo_button.setEnabled(pixel_map)
        self.place_memo_button.setToolTip(
            "Mark the spot under the crosshair." if pixel_map
            else "Calibrate the camera first.")

    def _memo_z_from_tip(self) -> None:
        name, pose = self.chosen_point(), self.jog.pose
        if name is None or pose is None:
            return
        z = round(float(pose[2]), 2)
        self.session.change_point(name, z=z)
        self._set_new_z(z)
        self.jog.tell(f"{name}: z {z:g}")
        self._say_memo_problem(name)

    def _camera_to_point(self) -> None:
        """Camera to: the camera over the chosen point at the travel
        height; its Z is not used."""
        point = self.session.point(self.chosen_point() or "")
        robot = self.session.robot
        if point is None or robot is None or self.jog.busy:
            return
        offset = self.session.profile.calibration.pipette_offset
        if offset is None:
            self.jog.tell("Refused: no tip calibration.")
            return
        xy = np.array([point.x - offset.dx, point.y - offset.dy])
        what = f"camera over {point.name} ({xy[0]:.2f}, {xy[1]:.2f})"
        why = self.session.motion.refused((xy[0], xy[1], xyz(robot)[2]))
        if why:
            self.jog.tell(f"UNREACHABLE: {what} - {why}")
            return
        log.info("%s", what)

        def job(log_):
            self.session.motion.camera_to(xy, log=log_)
            return f"camera over {point.name}"

        if not self.jog.run_job(job):
            self.jog.tell(self.session.robot_lease.refusal)

    def _tip_to_point(self) -> None:
        """Tip to: the tip to the chosen point, at its Z."""
        point = self.session.point(self.chosen_point() or "")
        if point is None:
            return
        if self.session.tip.attached is not True:
            self.jog.tell("Put a tip on first.")
            return
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

        if not self.jog.run_job(job):
            self.jog.tell(self.session.robot_lease.refusal)

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
        """The run's plates in every Plate choice, the one in hand kept; the
        deck map with them."""
        self._show_deck()
        plates = self.session.plates()
        for picker in (self.source_wells, self.target_wells):
            picker.set_plates(plates)

    # -- the well center ----------------------------------------------------------

    def _well_center(self, slot, load_name):
        profile = self.session.profile
        return (profile.deck.well_center(slot, load_name)
                if profile is not None else None)

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
        self.view.set_overlay_items(items)

    def _refresh(self) -> None:
        busy = self._busy()
        problems = self._map_problems()
        self.cuboid_button.setEnabled(not busy and not problems)
        self._refresh_memo_buttons()
        has_robot = self.session.robot is not None
        source = self._source()
        if source is not None and source[0] == "hand":
            # Under the crosshair: a tip on and the robot free is enough.
            self.aspirate_button.setEnabled(
                has_robot and self.session.tip.attached is True
                and not self.session.robot_lease.busy)
        else:
            self.aspirate_button.setEnabled(has_robot and source is not None)
        target = self._target()
        self.dispense_button.setText(self._dispense_text(target))
        self.dispense_button.setEnabled(has_robot and target is not None
                                        and self.session.in_tip > 0)
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
                self.above_bottom.setValue(profile.picking.pickup_offset)
                self.hand_z.setValue(profile.picking.pickup_height)
                self.volume.setValue(profile.picking.vol)
                self.flow_rate.setValue(profile.picking.flow_rate)
                self.part.setValue(profile.picking.vol)
            self._forget_targets()
        else:
            self._refresh()

    def _on_robot_busy(self, _holder) -> None:
        self._refresh()

    def _robot_changed(self, _state: str) -> None:
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
        # A run fills the plan's wells without a word to this page.
        self._show_plan()

    def hideEvent(self, event) -> None:
        for shortcut in self._shortcuts:
            shortcut.setEnabled(False)
        super().hideEvent(event)
