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

Under X, Y and Z, and on no other page, is the **height line** (#95): the
slot the camera is over (`camera_moves.camera_slot`, so before a tip
calibration too), "Slot 8 · top 85.10 · bottom 67.77 · measured", for the
operator choosing a Z. Measured is what this page itself goes by: a plate
measured and confirmed in this robot session, as In a well and By clicking; any
other plate is its drawing, a measurement not confirmed included, since a
line saying measured over a height no aspirate uses would mislead. No plate,
no line; the source dish has only a bottom, once confirmed, and in the
dish's slot it goes before a drawing, as `_slot_bottom`.

Memo points
-----------
A right click on the picture marks a point where the tip would go down
there (`Session.memo_points`, named with the lowest free number, M1, M2
...); so does Point at crosshair, at the spot under the picture's
center where By clicking aims, with no Z (gray without a pixel map); beside it
Tip to crosshair sends the tip there at the travel height, no memo point
needed, and leaves it there (gray without a tip or a pixel map). They are
listed in the Points card, which never folds, with the saved points in one
list (`widgets.point_list`, #112): X, Y and Z are edited by a double click,
and a memo point lasts until the program closes. ☆ keeps it in the profile
(`Session.save_memo_point`, #62), a point at the travel height with that
height as its Z; a saved point's name is renamed by a double click, ★ makes
it a memo point again and Delete asks first (#66). The chosen row unfolds
Camera to, Tip to, Z from tip and Delete. Robot setup's positions are not
points (#61). Marking moves nothing, it chooses the new point, and a double
click only edits. The Points chip alone says whether they are drawn: on at
start, and never turned on by a new point.

Undo and Redo on the card's title row go back over the last 3 moves, of
both kinds (`Session.undo_points`, `gui.point_history`, #96); there is no
key for them. A saved point brought back is written to the profile again.

A point is a place on the deck, the tip's, with no purpose of its own.
Under the chosen row: Camera to (the camera
over it at the travel height, its Z unused) and Tip to (the tip down to its
Z, no higher than the travel height; gray while no tip is on), each
checked against the jog envelope and through `JogPanel.run_job`. Aspirate's
and Dispense's "At a point" list both kinds and go to one by aspirating or
dispensing there.

They are drawn on the picture, a saved point with a filled disc.

A point the tip cannot go to is said where it is made and shown in red,
on the picture and in its Z (`Session.memo_problem`): the jog envelope's
checks, the one that matters being a Z below the top of a deck module.
The modules are the profile's, over the deck definition's slots in the
robot's frame, which is the frame a point's X and Y are in - so a point
marked over a 64 mm platform at z 50 is known to be inside it.

The height is the part a click cannot give. New points get Z chooses it:
the travel height, the tip's Z now, or, at start, the last Z given: typed
into a point's Z, or taken from the tip with "Z from tip" after jogging it
down to where it should be. Before any Z is given that is the travel height:
a point with no Z is gone to at the travel height, so a first click never
lowers the tip into anything.

Past picks
----------
Where the tip aspirated on this page, in the order taken, kept until the
program closes. The Past picks chip on the picture draws them: the
last bright, the older ones faint (`position_marks.pick_items`), so the
spots already taken from are visible.

Look at
-------
A MiniDeckDropdown of the whole deck, on the picture beside Detect cuboids
(`CameraView.add_top_row_widget`): choosing a slot sends the **camera**
over that slot's center at the travel height (`core.deck.slot_center`, the
deck definition's geometry). The definition is in the robot's frame, the
tip's: the camera goes to it less the pipette offset. Nothing is aspirated.

Only the upper camera is shown: the pixel map is its, and a point on the
lower camera's picture is not a place on the deck.

Aspirate
--------
The operator composes the work from Aspirate and Dispense; nothing is a
script. The card's body holds only settings, in the open (MANUAL.md §4.2): what
From asks, Amount, Speed and the height, then the lifting row. The height is
above the bottom of where the tip goes down: by clicking the slot's (below); in
a well the plate's measured bottom (rim less depth,
`Motion.to_measured_bottom`) once confirmed in this robot session
(`Session.plate_bottom_confirmed`), else the drawing's, and the summary and the
field's tooltip say which (#89). From:

* **By clicking** - the spot under the camera's crosshair (#60): the
  picture's center through the pixel map, plus the measured tip offset.
  The operator aims with the picture: a click moves the camera, and with
  Snap to found cuboids on, a click on a detected cuboid moves it so the
  crosshair is on the cuboid's center (a click beside every cuboid moves
  to the point). The size window on Snap's row opens the sizes in a popover
  (`_SizesPopover`). The height is a Z, From tip the tip's now, or a height
  above the bottom of the slot the spot is in (#90), whatever the source
  chosen: a plate confirmed in this robot session gives its measured bottom,
  the source dish's slot the dish bottom when that is confirmed
  (`Session.is_confirmed`), and anywhere else only Z is offered: an older
  bottom is not one to lower a tip to. It is the default where there is one,
  and the summary names the slot. Over a plate a spot not in a well is refused
  (`well_center.in_a_well`), and so is a click aiming where there is no
  bottom; a Z is the operator's, in a well or not. Aspirate remembers the
  camera's pose, takes the tip to the spot at the travel height
  (`Motion.tip_to`), lowers it, aspirates, and brings the camera back -
  one job, so nothing comes between. With Aspirate as soon as I click, a
  click aims and does all that.
* **At a point** - a memo or saved point as a chip (`PointChips`), at its own
  Z, so no height here.
* **In a well** - a plate the robot session holds, the Source step's first
  (`WellPicker`), and its well, a click on the plate in real proportions.

Aspirate is enabled once there is a source - By clicking always has one, so
there it is enabled whenever a tip is on and the robot is free. It goes
there (through the jog panel's `run_job`, after the jog envelope's check)
and aspirates (`Session.aspirate`); the spot is a past pick. Refused
unless the robot reports a tip on.

How far down
------------
A Z given for a target - Tip to, At a point in Aspirate and Dispense, By
clicking's Z - is checked against the bottom of the target's slot, the one the
height line gives (`_heights_in`): more than 1 mm under a measured bottom
or 2 mm under a drawing's is refused, nothing moved
(`moves.under_the_bottom`, #101). So a mistyped Z is stopped where no module
would stop it, and pressing lightly on the bottom stays possible. A slot
with no bottom known has nothing to go by. In a well and Above the bottom
are heights above a bottom, never under it. The check is on the move, not
on a point's Z as typed: `Session.memo_problem` stays the jog envelope's. It
is no part of the jog envelope either, since a jog step, and Dish bottom's
lowering, go down to a touch.

Dispense
--------
Where, then Part (µl, no more than is in the tip: `Session.dispense`; chosen at
start, the run's volume) or All, its own speed, starting from the picking
settings' flow rate and kept by the page, never in the profile, and the lifting
row:

* **In a well** - a plate and its well (`WellPicker`), the plate plan's plate
  first, any well choosable, the plan's wells in one color so the wells a run
  left empty can be filled by hand; nothing says what a run filled (§10.10).
  The tip goes to the well's bottom (as Aspirate's, measured or the drawing's)
  plus the picking settings' deposit offset, from the plate's well center.
* **At a point** - a memo or saved point, as a chip.

Its button says what it does - "Dispense into A7", "Dispense 10 µl" - and,
gray while the tip is empty, only "Dispense".

Aspirate and Dispense fold
--------------------------
`ActionCard`s, one open at a time, each with its button and a summary of the
whole action in the header, so a button works with its card folded (#114,
MANUAL.md §4.1). What the tip holds says which button is primary, and only that:
with the tip empty Aspirate, and Dispense's summary says so. A card opens by the
operator's click or as the next thing to do: Dispense after an Aspirate, by the
button or by a click on the picture, Aspirate after a Dispense that empties the
tip. So it is said where those two jobs end, not on `in_tip_changed`: In place's
buttons change what the tip holds too, and switch no card.

In place
--------
The card that aspirates or dispenses where the tip is (#111): no move and
no lift, whatever Aspirate's and Dispense's cards say; the tip is raised with
Retract in the top bar. Its amount and speed start as Aspirate's fields do and
then stay in the page's memory, apart from the cards. A and D do what its
buttons do, the keys drawn on them as bound, and nothing while a field has
the keyboard. A dispense above what the tip holds is refused: the plunger
would push out air.

How the tip leaves
------------------
Each card has its own, a row of words that unfolds (#102, `_LiftRow`,
MANUAL.md §4.4): Right away, After a wait or Stay, to the travel height where
the tip stands (`moves.retract`), slowly at first if asked (#103). Only By
clicking then brings the camera back to where it aimed from, since only there
did the camera choose the spot. The wait is part of the job, so it holds the
lease and every button that moves stays gray; a stop ends it with the tip down.

The well center
---------------
Dispense into a well goes from the plate's measured well center where
there is one (`config.schema.WellCenter`, per slot and plate type): its x
and y are added to the profile's well offsets, as a picking run adds them
to its deposit. Its height counts once the plate is confirmed in this
robot session (see Aspirate); until then the level keeps the robot's own
heights. It is measured on the step before, Well center
(`pages.well_center`, #63).

Every robot command goes through the jog panel's worker (`run_job`), so a
click-move and a key press cannot overlap. When anything moves, the
detections are dropped: they were measured from where the camera was.

After Detect cuboids, what was found is a line under the chips, "23 cuboids
found · 9 in size", and the cuboids' sizes against the profile's size window
are the picture's Sizes chip: the bins of `widgets.size_histogram`, the
Picking page's, so the two pages cannot disagree about what a run would accept.

The frame (MANUAL.md §1): the picture, then `WorkColumns`, the work and Points.
What holds while it is true is a line on the picture (`_status_lines`), and an
answer is a toast: this page has no status of its own to move a card.

The keys - the jog panel's and these - are listed under the jog pad (its
Keys toggle); what the mouse does is the picture's mouse row.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import NamedTuple

import cv2
import numpy as np
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from ...config.labware import LabwareError, resolve_definition
from ...core.calibration.pixel_map import PixelMap
from ...core.deck import nearest_slot, slot_center
from ...hardware.protocols import xyz
from ...viz import overlays
from ...workflows import manual as moves
from ...workflows import well_center
from ...workflows.jog import Reached
from ...workflows.motion import SLOW_RISE_MM, SLOW_RISE_MM_S
from .. import keys
from ..auto_camera import CameraOpener
from ..camera_moves import (NO_TIP_CALIBRATION, OUTSIDE_THE_MAP, camera_slot,
                            pose_slot)
from ..detector import wanted_model
from ..labware_kinds import facts, kind
from ..readiness import DISH_POSITION
from ..session import Session
from ..theme import COLORS, SPACING
from ..theme.factory import (card, heading, labeled, laid_out_at_once,
                             muted_label, number_field, quiet_button, reserve,
                             secondary_button, select, set_primary,
                             tool_button)
from ..theme.icons import icon
from ..widgets.camera_view import CameraView
from ..widgets.controls import Segmented, Switch
from ..widgets.feed_row import FeedRow
from ..widgets.fold_card import ActionCard, FoldCard, FoldGroup
from ..position_marks import PositionMarks
from ..widgets.jog_pad import KeycapButton
from ..widgets.jog_panel import JogPanel
from ..widgets.mini_deck import DECK_ROWS, DeckTile, MiniDeckDropdown
from ..widgets.point_chips import PointChips
from ..widgets.point_list import PointList, TRAVEL_TEXT, Z_TOP_MM
from ..widgets.tip_gauge import TipGauge
from ..widgets.work_columns import COLUMN_GAP, WorkColumns
from ..widgets.size_histogram import SizeHistogram, size_bins
from ..widgets.well_picker import WellPicker
from ..workers import Worker

__all__ = ["ManualPage"]

TITLE = "Manual control"

log = logging.getLogger(__name__)

# Left, top, right, bottom: the frame's (MANUAL.md §1).
PAGE_MARGINS = (20, 16, 20, 16)

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
KEYS = (("aspirate", "aspirate in place", "_aspirate_in_place"),
        ("dispense", "dispense in place", "_dispense_in_place"))

# Aspirate from, Dispense how much and where: the segmented choices.
FROM = ("By clicking", "At a point", "In a well")
AMOUNT = ("Part", "All")
WHERE = ("In a well", "At a point")

# By clicking's height: a Z, or above the bottom of the spot's slot.
HEIGHT = ("Z", "Above the bottom")

# How the tip leaves, each card its own; the wait's bounds and start.
LIFT = ("Right away", "After a wait", "Stay")
WAIT_RANGE = (0.5, 60.0)
WAIT_S = 5.0

# Said when a stop ended the delay.
STOPPED_DOWN = "Stopped: the tip stays down."

# What the mouse does on the picture, its row along the bottom (§3).
MOUSE_HELP = ("Right click   mark a point", "Wheel   zoom",
              "Middle-drag   pan")
CLICK_HELP = "Left click   move"

# What a click moves, in this order: never shown, fixed to Camera.
CLICK_MOVES = ("Camera", "Tip")


TIP_EMPTY = "The tip is empty. Aspirate first."
POINTS_TIP = "Double-click X, Y, Z or a saved point's name to change it."
# The Points card's list, and three rows of chips under "At a point".
POINTS_LIST_PX = 280
NO_LIMIT_PX = 16777215      # Qt's QWIDGETSIZE_MAX, which PySide does not export
AT_A_POINT_PX = 124
# The plate drawn under In a well (MANUAL.md §4.2, §4.3).
SOURCE_MAP_PX = 320
TARGET_MAP_PX = 340
POPOVER_PX = 360
# The fields' widths: room for the range's longest number.
NUMBER_PX = 72
Z_PX = 80
# What Z a new point gets.
TRAVEL_Z, TIP_Z, LAST_Z = NEW_Z = ("Travel height", "Z of the tip now",
                                   "Last Z given")


def _memo_z_text(z) -> str:
    return TRAVEL_TEXT if z is None else f"{z:g}"


def _well_words(well) -> str:
    """`_WellPicker.chosen()` as a summary says it."""
    return f"Slot {well[0]} · {well[3]}"


def _point_words(point) -> tuple[str, str]:
    """A point as a summary says it: it has its own height."""
    return (point.name,
            "travel height" if point.z is None else f"Z {point.z:g} mm")


def _in_place_button(text: str, color: str, parent: QWidget) -> KeycapButton:
    """A button of In place's, in its color while it can be pressed."""
    button = KeycapButton(text, parent)
    button.setObjectName("secondary")
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setStyleSheet(f"QPushButton:enabled {{ color: {COLORS[color]}; "
                         f"border: 1px solid {COLORS[color]}; }}")
    return button


def _small(choice: Segmented) -> Segmented:
    """A choice inside a card, 32 px high (MANUAL.md §4.2)."""
    choice.setProperty("small", True)
    return choice


def _fields(*widgets: QWidget) -> QHBoxLayout:
    """Fields side by side from the left, their tops level."""
    row = QHBoxLayout()
    row.setSpacing(10)
    for widget in widgets:
        row.addWidget(widget, 0, Qt.AlignmentFlag.AlignBottom)
    row.addStretch(1)
    return row


class _LiftRow(QWidget):
    """How the tip leaves (MANUAL.md §4.4): a row in words that unfolds the
    choice, the wait, shown only with After a wait, and Lift slowly, hidden
    with Stay. `after` names the row: "After aspirating"."""

    def __init__(self, after: str, parent: QWidget, changed):
        super().__init__(parent)
        self.setObjectName("plain")
        self._after = after
        self._changed = lambda: None
        self.header = QPushButton(self)
        self.header.setObjectName("liftRow")
        self.header.setCheckable(True)
        self.header.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.line = QLabel(self.header)
        self.line.setTextFormat(Qt.TextFormat.RichText)
        self._arrow = QLabel(self.header)
        row = QHBoxLayout(self.header)
        row.setContentsMargins(0, 0, 0, 0)
        for widget in (self.line, self._arrow):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(self.line, 1)
        row.addWidget(self._arrow)
        self.header.toggled.connect(self._folded)

        self.box = QWidget(self)
        self.box.setObjectName("plain")
        self.choice = _small(Segmented(list(LIFT), parent=self.box))
        self.choice.changed.connect(lambda _i: self._chosen())
        self.delay = number_field(self.box, WAIT_S, WAIT_RANGE, decimals=1,
                                  step=0.5, width=NUMBER_PX)
        self.delay.valueChanged.connect(lambda _v: self._said())
        self.wait_row = labeled("Wait, s", self.delay, self.box)
        self.slowly = Switch("Lift slowly", parent=self.box)
        self.slowly.setToolTip(
            f"First {SLOW_RISE_MM:g} mm at {SLOW_RISE_MM_S:g} mm/s.")
        self.slowly.toggled.connect(lambda _on: self._said())
        inner = QVBoxLayout(self.box)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.addLayout(_fields(labeled("Lift the tip", self.choice, self.box),
                                self.wait_row))
        inner.addWidget(self.slowly)

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.header)
        column.addWidget(self.box)
        self._folded(False)
        self._chosen()
        self._changed = changed

    def _folded(self, open: bool) -> None:
        self._arrow.setPixmap(icon("chevron-down" if open else "chevron-right",
                                   COLORS["text-2"], 16).pixmap(16, 16))
        focus = QApplication.focusWidget()
        if not open and self.box.isAncestorOf(focus):
            focus.clearFocus()
        with laid_out_at_once(self):
            self.box.setVisible(open)

    def _rises(self) -> bool:
        return LIFT[self.choice.current()] != "Stay"

    def _delayed(self) -> bool:
        return LIFT[self.choice.current()] == "After a wait"

    def _chosen(self) -> None:
        with laid_out_at_once(self):
            self.wait_row.setVisible(self._delayed())
            self.slowly.setVisible(self._rises())
        self._said()

    def _said(self) -> None:
        self.line.setText(f"{self._after}: <b>{self.words()}</b>")
        self._changed()

    def how(self) -> moves.Retract:
        return moves.Retract(
            rises=self._rises(),
            delay_s=float(self.delay.value()) if self._delayed() else 0.0,
            slowly=self.slowly.isChecked())

    def words(self) -> str:
        """As said on the row and at the end of the card's summary."""
        if not self._rises():
            return "stay"
        how = (f"lift after {self.delay.value():g} s" if self._delayed()
               else "lift right away")
        return f"{how}, slowly" if self.slowly.isChecked() else how


class _SizesPopover(QFrame):
    """The size window's histogram, anchored to Snap's row: what the Sizes
    card showed, the window read-only as it was on this page."""

    def __init__(self, parent: QWidget):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("raisedCard")
        self.histogram = SizeHistogram(parent=self)
        self.setFixedWidth(POPOVER_PX)
        column = QVBoxLayout(self)
        column.setContentsMargins(12, 12, 12, 12)
        column.addWidget(self.histogram)


class SlotBottom(NamedTuple):
    """The bottom By clicking goes above in `slot`, at Z `z`. `in_a_well` says
    whether a spot is one to go down at: over a plate, in one of its wells;
    in the dish, anywhere."""

    slot: int
    z: float
    in_a_well: Callable


class SlotHeights(NamedTuple):
    """What the page knows of a slot's heights. `top` None for the source
    dish, which has only a bottom; `measured` False for a drawing's."""

    top: float | None
    bottom: float
    measured: bool


class ManualPage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._worker: Worker | None = None
        # The bottom under the crosshair when last looked, or None: Above
        # the bottom becomes the default as there comes to be one.
        self._bottom: SlotBottom | None = None
        # Where the tip aspirated, (x, y), oldest first: the past picks.
        self._picks: list[tuple[float, float]] = []
        # Set by a stop: ends a job's wait before the tip goes up.
        self._last_z: float | None = None   # see _new_z
        self.sizes_popover: _SizesPopover | None = None     # made when opened
        self._working = ""                  # the worker's job, for the picture
        self._stop = threading.Event()
        self._forget_targets(redraw=False)

        self.opener = CameraOpener(session, self)
        self.view = CameraView(self)
        self.view.marks_default = True      # the memo points are drawn there
        # A double click moves nothing; it does not fit the picture either.
        self.view.double_click_fits = False
        self.view.marks_default = True      # the chip alone says (§10.14)
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
        self.view.set_height_line("")
        self.view.set_mouse_help(MOUSE_HELP)
        self.view.set_click_to_move(CLICK_HELP, self._click_reaches)
        self.jog.moved.connect(self._forget_targets)
        # Emitted whenever a job ends: the buttons greyed for the job come
        # back.
        self.jog.position_changed.connect(lambda _lines: self._refresh())
        self.jog.pose_changed.connect(lambda _pose: self._bottom_changed())
        self.jog.set_page_help(self._page_help())

        self.marks = PositionMarks.on(session, self.view)
        self._picture_tools()
        self.in_place_card = self._in_place_card()
        columns = WorkColumns([self.in_place_card, self._aspirate_card(),
                               self._dispense_card()],
                              self._points_card(), self, last=self.jog)
        self.work_cards = FoldGroup([self.aspirate_card, self.dispense_card])
        columns.two_columns_changed.connect(self._columns_changed)

        body = QHBoxLayout()
        body.setSpacing(SPACING)
        body.addWidget(FeedRow(self.view, columns, spacing=COLUMN_GAP), 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*PAGE_MARGINS)
        layout.setSpacing(SPACING)
        layout.addLayout(body, 1)

        session.stop_requested.connect(self._stop.set)
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
        session.labware_changed.connect(lambda _s: self._bottom_changed())
        session.profile_changed.connect(lambda _p: self._on_profile_changed())
        session.confirmations_changed.connect(self._bottom_changed)
        self._shortcuts = []
        self._install_shortcuts()
        self._name_keys()
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

    def _picture_tools(self) -> None:
        """Look at and Detect cuboids in a row over the chips, Past picks
        among them."""
        self.look_at = MiniDeckDropdown(self, placeholder="Choose a slot",
                                        prefix="Look at")
        self.look_at.setToolTip("Move the camera over the slot.")
        self.look_at.slot_chosen.connect(self._slot_clicked)
        self.cuboid_button = secondary_button("Detect cuboids", self)
        self.cuboid_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cuboid_button.clicked.connect(self._detect_cuboids)
        for widget in (self.look_at, self.cuboid_button):
            self.view.add_top_row_widget(widget)
            widget.show()
        self.past_picks_button = self.view.add_chip("Past picks")
        self.past_picks_button.toggled.connect(lambda _on: self._show_picks())

    def _in_place_card(self) -> QWidget:
        """Amount and speed of its own, and the two buttons; see "In
        place"."""
        box = card(self)
        title = heading("In place", 3)
        self.in_place_amount = self._amount_field()
        self.in_place_rate = self._speed_field()
        fields = QHBoxLayout()
        fields.setSpacing(10)
        fields.addWidget(title, 1, Qt.AlignmentFlag.AlignVCenter)
        fields.addWidget(labeled("Amount, µl", self.in_place_amount, self))
        fields.addWidget(labeled("Speed, µl/s", self.in_place_rate, self))
        box.layout().addLayout(fields)
        self.aspirate_in_place = _in_place_button(
            "Aspirate in place", "tips", self)
        self.aspirate_in_place.clicked.connect(self._aspirate_in_place)
        self.dispense_in_place = _in_place_button(
            "Dispense in place", "plate", self)
        self.dispense_in_place.clicked.connect(self._dispense_in_place)
        row = QHBoxLayout()
        row.addWidget(self.aspirate_in_place, 1)
        row.addWidget(self.dispense_in_place, 1)
        box.layout().addLayout(row)
        return box

    def _aspirate_card(self) -> QWidget:
        """From and what it asks, the fields, the lifting row, Aspirate in
        the header; see "Aspirate"."""
        self.aspirate_card = ActionCard("Aspirate", 1, "tips", open=True,
                                        parent=self)
        self.aspirate_button = self.aspirate_card.button
        self.aspirate_button.clicked.connect(self._aspirate_from_source)
        body = self.aspirate_card.body.layout()

        self.source = _small(Segmented(
            list(FROM), current=FROM.index("By clicking"), parent=self))
        self.source.changed.connect(lambda _i: self._source_changed())
        body.addWidget(self.source)

        self.hand_box = self._hand_box()
        body.addWidget(self.hand_box)

        # Both kinds, to aspirate from; made and moved to in their card.
        self.source_points = self._point_chips()
        body.addWidget(self.source_points)

        self.source_wells = WellPicker("tips", SOURCE_MAP_PX, self)
        self.source_wells.changed.connect(self._show_aspirate_summary)
        self.source_wells.changed.connect(self._refresh)
        body.addWidget(self.source_wells)

        # By clicking's height: Z, or above the bottom.
        self.height_mode = _small(Segmented(list(HEIGHT), parent=self))
        self.height_mode.changed.connect(lambda _i: self._source_changed())
        self.height_row = QWidget(self)
        self.height_row.setObjectName("plain")
        row = QHBoxLayout(self.height_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel("Height", self.height_row))
        row.addWidget(self.height_mode, 1)
        body.addWidget(self.height_row)

        self.volume = self._amount_field()
        self.flow_rate = self._speed_field()
        self.hand_z = number_field(self, 70.0, (0.0, Z_TOP_MM), width=Z_PX)
        self.hand_z.setToolTip("Z of the tip's end.")
        self.from_tip = secondary_button("From tip", self)
        self.from_tip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.from_tip.setToolTip("Set Z to the tip's Z now.")
        self.from_tip.clicked.connect(self._hand_z_from_tip)
        self.above_bottom = number_field(self, 0.5, ABOVE_BOTTOM_RANGE,
                                         width=Z_PX)
        for box in (self.volume, self.flow_rate, self.hand_z,
                    self.above_bottom):
            box.valueChanged.connect(lambda _v: self._show_aspirate_summary())
        z = QWidget(self)
        z.setObjectName("plain")
        field = QHBoxLayout(z)
        field.setContentsMargins(0, 0, 0, 0)
        field.setSpacing(4)
        field.addWidget(self.hand_z)
        field.addWidget(self.from_tip)
        self.hand_z_row = labeled("Height Z, mm", z, self)
        self.above_row = labeled("Above the bottom, mm", self.above_bottom,
                                 self)
        body.addLayout(_fields(labeled("Amount, µl", self.volume, self),
                               labeled("Speed, µl/s", self.flow_rate, self),
                               self.hand_z_row, self.above_row))

        self.aspirate_retract = _LiftRow("After aspirating", self,
                                         self._show_aspirate_summary)
        body.addWidget(self.aspirate_retract)

        self._show_aspirate_summary()
        return self.aspirate_card

    def _hand_box(self) -> QWidget:
        """By clicking's switches, the size window on Snap's row."""
        box = QWidget(self)
        box.setObjectName("plain")
        hand = QVBoxLayout(box)
        hand.setContentsMargins(0, 0, 0, 0)
        self.snap = Switch("Snap to found cuboids", checked=True, parent=box)
        self.snap.setToolTip(
            "A click on a found cuboid puts the crosshair on its center.")
        self.sizes_link = quiet_button("", box)
        self.sizes_link.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sizes_link.setStyleSheet(f"color: {COLORS['accent']};")
        self.sizes_link.setToolTip("Show the size histogram.")
        self.sizes_link.clicked.connect(self._open_sizes)
        row = QHBoxLayout()
        row.addWidget(self.snap)
        row.addStretch(1)
        row.addWidget(self.sizes_link)
        hand.addLayout(row)
        self.aspirate_on_click = Switch("Aspirate as soon as I click",
                                        parent=box)
        hand.addWidget(self.aspirate_on_click)
        return box

    def _dispense_card(self) -> QWidget:
        """Where and how much, a button that says it in the header; see
        "Dispense"."""
        self.dispense_card = ActionCard("Dispense", 2, "plate", parent=self)
        self.dispense_button = self.dispense_card.button
        self.dispense_button.clicked.connect(self._dispense_to_target)
        body = self.dispense_card.body.layout()

        self.target = _small(Segmented(list(WHERE), parent=self))
        self.target.changed.connect(lambda _i: self._target_changed())
        body.addWidget(self.target)

        self.target_wells = WellPicker("accent", TARGET_MAP_PX, self)
        self.target_wells.changed.connect(self._refresh)
        body.addWidget(self.target_wells)

        self.target_points = self._point_chips()
        body.addWidget(self.target_points)

        self.amount = _small(Segmented(list(AMOUNT), parent=self))
        self.amount.changed.connect(lambda _i: self._target_changed())
        self.part = self._amount_field()
        self.part.valueChanged.connect(lambda _v: self._refresh())
        self.part_row = labeled("µl", self.part, self)
        reserve(self.part_row)
        self.dispense_rate = self._speed_field()
        self.dispense_rate.valueChanged.connect(
            lambda _v: self._show_dispense_summary())
        body.addLayout(_fields(labeled("How much", self.amount, self),
                               self.part_row,
                               labeled("Speed, µl/s", self.dispense_rate,
                                       self)))

        self.dispense_retract = _LiftRow("After dispensing", self,
                                         self._show_dispense_summary)
        body.addWidget(self.dispense_retract)

        self._show_dispense_summary()
        return self.dispense_card

    def _amount_field(self):
        return number_field(self, 10.0, VOLUME_RANGE, decimals=1, step=1.0,
                            width=NUMBER_PX)

    def _speed_field(self):
        return number_field(self, 50.0, FLOW_RANGE, decimals=1, step=1.0,
                            width=NUMBER_PX)

    def _point_chips(self) -> PointChips:
        """As tall as three rows whatever it lists: more rows scroll."""
        chips = PointChips(self.session, self)
        chips.setFixedHeight(AT_A_POINT_PX)
        chips.chosen.connect(self._marks_choose)
        return chips

    def _point_list(self, height: int, *, actions: bool = False) -> PointList:
        """As tall as `height` whatever it lists: more rows scroll."""
        points = PointList(self.session, self, actions=actions)
        points.setFixedHeight(height)
        points.setToolTip(POINTS_TIP)
        points.chosen.connect(self._marks_choose)
        points.z_typed.connect(lambda z: self._memo_z_typed(points, z))
        points.said.connect(self.jog.refuse)
        points.told.connect(self.jog.tell)
        return points

    def _points_card(self) -> QWidget:
        """The one Points card: the list, the moves unfolding under the
        chosen point, and what makes a point; see "Memo points"."""
        self.points_card = FoldCard("Points", parent=self)
        self.undo_button = tool_button("Undo", "undo",
                                       self.session.undo_points, self)
        self.redo_button = tool_button("Redo", "redo",
                                       self.session.redo_points, self)
        self.points_card.header_row.addWidget(self.undo_button)
        self.points_card.header_row.addWidget(self.redo_button)
        layout = self.points_card.body.layout()

        self.points_list = self._point_list(POINTS_LIST_PX, actions=True)
        self.points_list.chosen.connect(
            lambda _name: self._refresh_memo_buttons())
        layout.addWidget(self.points_list)
        self.camera_to_button = self.points_list.camera_to
        self.camera_to_button.setToolTip(
            "Move the camera over the chosen point.")
        self.camera_to_button.clicked.connect(self._camera_to_point)
        self.tip_to_button = self.points_list.tip_to
        self.tip_to_button.clicked.connect(self._tip_to_point)
        self.memo_z = self.points_list.z_from_tip
        self.memo_z.setToolTip("Set the chosen point's Z to the tip's Z now.")
        self.memo_z.clicked.connect(self._memo_z_from_tip)
        self.delete_point_button = self.points_list.delete

        divider = QFrame(self)
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {COLORS['line']};")
        layout.addWidget(divider)
        self.place_memo_button = secondary_button("Point at crosshair", self)
        self.place_memo_button.clicked.connect(self._place_memo)
        self.tip_crosshair_button = secondary_button("Tip to crosshair", self)
        self.tip_crosshair_button.clicked.connect(self._tip_to_crosshair)
        row = QHBoxLayout()
        for button in (self.place_memo_button, self.tip_crosshair_button):
            row.addWidget(button)
        layout.addLayout(row)
        self.new_z = select(self)
        self.new_z.addItems(NEW_Z)
        self.new_z.setCurrentText(LAST_Z)
        self.new_z.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row = QHBoxLayout()
        row.addWidget(muted_label("New points get Z", self), 1)
        row.addWidget(self.new_z)
        layout.addLayout(row)

        self.session.memo_points_changed.connect(self._refresh)
        self.jog.pose_changed.connect(lambda _p: self._refresh_memo_buttons())
        return self.points_card

    def _columns_changed(self, two: bool) -> None:
        """A column of its own is the list's to fill; under the work it is
        as tall as it was built."""
        points = self.points_list
        if two:
            points.setMinimumHeight(POINTS_LIST_PX)
            points.setMaximumHeight(NO_LIMIT_PX)
            points.setSizePolicy(QSizePolicy.Policy.Preferred,
                                 QSizePolicy.Policy.Expanding)
        else:
            points.setFixedHeight(POINTS_LIST_PX)
            points.setSizePolicy(QSizePolicy.Policy.Preferred,
                                 QSizePolicy.Policy.Preferred)

    def _install_shortcuts(self) -> None:
        """A and D, window-wide while this page is showing - the jog panel's
        arrangement, and for its reason: enabled only on screen, so no
        hidden page claims the key."""
        bindings = self.session.settings.keys
        for action, _what, handler in KEYS:
            for sequence in keys.sequences(bindings, action):
                shortcut = QShortcut(sequence, self)
                shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
                shortcut.activated.connect(
                    lambda h=getattr(self, handler): self._key(h))
                shortcut.setEnabled(self.isVisible())
                self._shortcuts.append(shortcut)

    def _key(self, handler) -> None:
        """A or D, unless a field has the keyboard: a key rebound to one a
        field does not edit with would reach here."""
        if not self.jog.typing:
            handler()

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
        self._name_keys()

    def _name_keys(self) -> None:
        """In place's buttons carry the keys that do them."""
        bindings = self.session.settings.keys
        self.aspirate_in_place.set_keycap(keys.key_text(bindings, "aspirate"))
        self.dispense_in_place.set_keycap(keys.key_text(bindings, "dispense"))

    # -- liquid ------------------------------------------------------------------

    def _show_aspirate_summary(self) -> None:
        """The header's summary, and which bottom the field is above."""
        self.aspirate_card.set_summary(" · ".join((
            *self._source_words(), f"{self.volume.value():g} µl",
            f"{self.flow_rate.value():g} µl/s",
            self.aspirate_retract.words())))
        self.above_bottom.setToolTip(self._height_words())

    def _show_dispense_summary(self) -> None:
        """The header's summary: where, how much, how."""
        if self.session.in_tip <= 0:
            self.dispense_card.set_summary(TIP_EMPTY, warn=True)
            return
        part = self._part()
        self.dispense_card.set_summary(" · ".join((
            *self._target_words(),
            "everything" if part is None else f"{part:g} µl",
            f"{self.dispense_rate.value():g} µl/s",
            self.dispense_retract.words())))

    def _height_words(self) -> str:
        if self._hand_z_chosen():
            return f"Z {self.hand_z.value():g} mm"
        return f"{self.above_bottom.value():g} mm above {self._bottom_words()}"

    def _source_words(self) -> tuple[str, ...]:
        """Where Aspirate takes from and at what height, for the summary."""
        source = self._source()
        if source is None:
            return (FROM[self.source.current()],)
        kind, what = source
        if kind == "point":
            return _point_words(what)
        return ("By clicking" if kind == "hand" else _well_words(what),
                self._height_words())

    def _target_words(self) -> tuple[str, ...]:
        """Where Dispense gives back, for the summary."""
        target = self._target()
        if target is None:
            return (WHERE[self.target.current()],)
        kind, what = target
        return _point_words(what) if kind == "point" else (_well_words(what),)

    def _part(self) -> float | None:
        """Dispense's volume; None for All."""
        return (None if AMOUNT[self.amount.current()] == "All"
                else float(self.part.value()))

    def _bottom_words(self) -> str:
        """Which bottom Aspirate's height is above: in a well, the one it
        will go to; by clicking, the slot's."""
        source = self._source()
        if (source is not None and source[0] == "hand"
                and self._bottom is not None):
            return f"the bottom of slot {self._bottom.slot}"
        if source is None or source[0] != "well":
            return "the bottom"
        slot, _labware_id, definition, well = source[1]
        return ("the drawing's bottom"
                if self._measured_bottom(slot, definition, well) is None
                else "the measured bottom")

    def _hand_z_chosen(self) -> bool:
        """By clicking, at a Z rather than above the bottom."""
        return (FROM[self.source.current()] == "By clicking"
                and HEIGHT[self.height_mode.current()] == "Z")

    def _bottom_changed(self) -> None:
        """Above the bottom only while the spot under the crosshair has
        one, and the default as it comes to; the line, as the slot or a
        plate's bottom may have changed. During a move there is no pose,
        and what was shown stands: passing through it changes no choice."""
        self._show_heights()
        self.from_tip.setEnabled(self.jog.pose is not None)
        if self.jog.pose is None and self._bottom is not None:
            self._show_aspirate_summary()
            return
        before, self._bottom = self._bottom, self._bottom_here()
        known = self._bottom is not None
        above = self.height_mode.buttons[HEIGHT.index("Above the bottom")]
        above.setEnabled(known)
        above.setToolTip("" if known else "Bottom of this slot: Not set.")
        if known != (before is not None):
            self.height_mode.set_current(HEIGHT.index(
                "Above the bottom" if known else "Z"))
            self._source_changed()
        self._show_aspirate_summary()

    def _bottom_here(self) -> SlotBottom | None:
        """The bottom under the crosshair now, if the spot can be known."""
        if self._crosshair_problem():
            return None
        return self._slot_bottom(self._crosshair_spot(self.jog.pose[:2]))

    def _slot_bottom(self, spot) -> SlotBottom | None:
        """The bottom of the slot `spot` is in: a plate's confirmed in this
        robot session, else the source dish's when it is confirmed, else
        None. Whatever the source chosen."""
        session = self.session
        slot = nearest_slot(spot[0], spot[1])
        plate = self._plate_in(slot)
        if plate is not None and self._plate_confirmed(plate):
            held, entry, definition = plate
            center = self._well_center(held, definition.load_name)
            shift = entry.offset or (0.0, 0.0, 0.0)
            return SlotBottom(
                slot, well_center.measured_bottom(
                    center, definition,
                    session.profile.deck.module_height(held)),
                lambda at: well_center.in_a_well(
                    definition, held, well_center.xy(center), at,
                    labware_offset=shift))
        dish = self._dish_bottom_in(slot)
        return None if dish is None else SlotBottom(slot, dish,
                                                    lambda _at: True)

    def _plate_in(self, slot) -> tuple | None:
        """The session's (slot, entry, definition) of the plate in `slot`."""
        for plate in self.session.plates():
            if plate[0] == str(slot):
                return plate
        return None

    def _plate_confirmed(self, plate) -> bool:
        held, _entry, definition = plate
        return self.session.plate_bottom_confirmed(held, definition.load_name)

    def _dish_bottom_in(self, slot) -> float | None:
        """The source dish's bottom, if the dish is in `slot` and its bottom
        is confirmed."""
        session = self.session
        observe = session.profile.positions.get(DISH_POSITION)
        if (session.is_confirmed("dish_bottom") and observe is not None
                and pose_slot(session, DISH_POSITION, observe[:2]) == slot):
            return float(session.profile.picking.dish_bottom)
        return None

    def _show_heights(self) -> None:
        """The height line, for where the camera is. During a move there is
        no pose and what was shown stands; without a robot there is none."""
        if self.jog.pose is not None:
            self.view.set_height_line(self._heights_here())
        elif self.session.robot is None:
            self.view.set_height_line("")

    def _heights_here(self) -> str:
        """The height line for the slot under the camera, "" for none."""
        session = self.session
        if session.profile is None:
            return ""
        slot = camera_slot(session, self.jog.pose[:2])
        heights = self._heights_in(slot)
        if heights is None:
            return ""
        if heights.top is None:
            return f"Slot {slot} · bottom {heights.bottom:.2f} · measured"
        return (f"Slot {slot} · top {heights.top:.2f} · "
                f"bottom {heights.bottom:.2f} · "
                f"{'measured' if heights.measured else 'drawing'}")

    def _heights_in(self, slot) -> SlotHeights | None:
        """The heights known in `slot`, None for none: a plate confirmed in
        this robot session, else the source dish's bottom when confirmed,
        else a plate's drawing."""
        session = self.session
        if session.profile is None:
            return None
        plate = self._plate_in(slot)
        measured = plate is not None and self._plate_confirmed(plate)
        dish = self._dish_bottom_in(slot)
        if dish is not None and not measured:
            return SlotHeights(None, dish, True)
        if plate is None:
            return None
        held, _entry, definition = plate
        center = (self._well_center(held, definition.load_name)
                  if measured else None)
        top, bottom = well_center.plate_heights(
            center, definition, session.profile.deck.module_height(held))
        return SlotHeights(top, bottom, measured)

    def _under_the_bottom(self, x: float, y: float, z) -> str:
        """Why a target at (x, y, z) is too far under the bottom of its
        slot, or "": the words of a sentence, as `Motion.refused`. A slot
        with no bottom known has nothing to go by, and no Z is the travel
        height, over everything."""
        slot = nearest_slot(x, y)
        heights = self._heights_in(slot)
        if z is None or heights is None:
            return ""
        return moves.under_the_bottom(z, heights.bottom, slot,
                                      measured=heights.measured)

    def _source_changed(self) -> None:
        """From's own controls under it; the points drawn or not."""
        chosen = FROM[self.source.current()]
        with laid_out_at_once(self.aspirate_card):
            self.source_wells.setVisible(chosen == "In a well")
            self.source_points.setVisible(chosen == "At a point")
            self.hand_box.setVisible(chosen == "By clicking")
            self.height_row.setVisible(chosen == "By clicking")
            self.hand_z_row.setVisible(self._hand_z_chosen())
            self.above_row.setVisible(chosen != "At a point"
                                      and not self._hand_z_chosen())
        self._show_aspirate_summary()
        self._refresh()

    def _target_changed(self) -> None:
        """Where's own controls under it, Part's field; the points drawn or
        not."""
        chosen = WHERE[self.target.current()]
        well = chosen == "In a well"
        with laid_out_at_once(self.dispense_card):
            self.target_wells.setVisible(well)
            self.target_points.setVisible(chosen == "At a point")
        self.part_row.setVisible(AMOUNT[self.amount.current()] == "Part")
        self._refresh()

    def _by_hand(self) -> bool:
        """Aspirate takes from under the crosshair."""
        return FROM[self.source.current()] == "By clicking"

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
        """("well", ...) or ("point", MemoPoint): where Dispense gives
        back; None while nothing is chosen."""
        chosen = WHERE[self.target.current()]
        if chosen == "In a well":
            well = self.target_wells.chosen()
            return None if well is None else ("well", well)
        point = self.session.point(self.target_points.chosen_name or "")
        return None if point is None else ("point", point)

    def _dispense_text(self, target) -> str:
        # Gray, the word alone: the room is the empty tip's sentence's.
        if target is None or self.session.in_tip <= 0:
            return "Dispense"
        kind, what = target
        if kind == "well":
            return f"Dispense into {what[3]}"
        part = self._part()
        return (f"Dispense "
                f"{self.session.in_tip if part is None else part:g} µl")

    def _well_offset(self, slot, definition, z: float):
        """(x, y, z) from a well's bottom: the plate's well center and the
        profile's well offsets, as a run deposits (`deposit_offset`)."""
        center = self._well_center(slot, definition.load_name)
        dx, dy = well_center.deposit_offset(self.session.profile.picking,
                                            well_center.xy(center))
        return (dx, dy, float(z))

    def _measured_bottom(self, slot, definition, well: str):
        """(rim, depth) of the plate's measured bottom, from the robot's
        well top; None for the drawing's: a plate not measured, or measured
        but not confirmed in this robot session."""
        load_name = definition.load_name
        if not self.session.plate_bottom_confirmed(slot, load_name):
            return None
        center = self._well_center(slot, load_name)
        depth = (center.depth_mm if center.depth_mm is not None
                 else well_center.definition_depth(definition, well))
        return None if depth is None else (float(center.offset[2]),
                                           float(depth))

    def _go_to(self, target, z: float | None, *, prepare: bool = False):
        """The move to `target` - a well (`z` above its measured bottom,
        else the drawing's), a point (its own Z) - as a job's first part.
        `prepare`: for an aspirate, as the measured bottom asks (`motion.descend`). Raises
        ValueError with the reason when it is out of reach."""
        kind, what = target
        motion = self.session.motion
        if kind == "well":
            slot, labware_id, definition, well = what
            offset = self._well_offset(slot, definition, z)
            measured = self._measured_bottom(slot, definition, well)

            def go(log):
                if measured is None:
                    motion.to_well(labware_id, well, "bottom", offset, log=log)
                else:
                    motion.to_measured_bottom(
                        labware_id, well, offset[:2], *measured, offset[2],
                        prepare=prepare, log=log)
            return go
        point = what
        xy = np.array([point.x, point.y])
        pose = self.jog.pose
        check_z = point.z if point.z is not None else (
            pose[2] if pose is not None else 0.0)
        why = (motion.refused((point.x, point.y, check_z))
               or self._under_the_bottom(point.x, point.y, point.z))
        if why:
            raise ValueError(f"Not reachable: {why}.")
        # No Z: as high as a move may go, which is the travel height.
        to_z = float("inf") if point.z is None else point.z

        def go(log):
            motion.tip_to(xy, to_z, log=log)
        return go

    def _aspirate_from_source(self) -> None:
        """Aspirate: to the source, then aspirate there."""
        source = self._source()
        if source is None:
            return
        if source[0] == "hand":
            self._aspirate_by_hand()
        else:
            self._aspirate(source, float(self.above_bottom.value()))

    def _aspirate_in_place(self) -> None:
        """In place's amount where the tip is, the tip left there; the
        spot is a past pick."""
        problem = self._tip_problem("aspirate")
        if problem:
            self.jog.refuse(problem)
            return
        session, robot = self.session, self.session.robot
        volume = float(self.in_place_amount.value())
        rate = float(self.in_place_rate.value())

        def job(log):
            log(f"aspirate {volume:g} µl at {rate:g} µl/s")
            session.aspirate(volume, rate)
            return xyz(robot)[:2]

        def then(at):
            self.record_pick(at)
            return f"Aspirated {volume:g} µl here."

        # The gantry stays: what is detected on the picture stands.
        if not self.jog.run_job(job, moves=False, then=then):
            self.jog.refuse(self.session.robot_lease.refusal)

    def _aspirate(self, source, z) -> None:
        """Aspirate's volume and speed at `source` (see `_go_to`); the
        spot is a past pick."""
        problem = self._tip_problem("aspirate")
        if problem:
            self.jog.refuse(problem)
            return
        try:
            go = self._go_to(source, z, prepare=True)
        except ValueError as exc:
            self.jog.refuse(str(exc))
            return
        session, robot = self.session, self.session.robot
        volume, rate = self._liquid_amount()
        how = self.aspirate_retract.how()

        def job(log):
            self._stop.clear()
            go(log)
            log(f"aspirate {volume:g} µl at {rate:g} µl/s")
            session.aspirate(volume, rate)
            at = xyz(robot)[:2]
            return at, self._leave(how, log)

        def then(result):
            at, rose = result
            self.record_pick(at)
            self.work_cards.open(self.dispense_card)
            if how.rises and not rose:
                return STOPPED_DOWN
            return f"Aspirated {volume:g} µl."

        if not self.jog.run_job(job, then=then):
            self.jog.refuse(self.session.robot_lease.refusal)

    def _leave(self, how: moves.Retract, log) -> bool:
        """The tip up as the card says, at a job's end; True if it went."""
        return moves.retract(self.session.motion, how, self._stop, log=log)

    def _hand_z_from_tip(self) -> None:
        """From tip: By clicking's Z, the tip's now. Not a read: see
        _clicked."""
        pose = self.jog.pose
        if pose is not None:
            self.hand_z.setValue(round(float(pose[2]), 2))

    def _hand_z(self, spot) -> float:
        """By clicking's height at `spot`: the Z, or above the bottom of its
        slot. Raises ValueError with the reason where it may not go down:
        a click can aim past what the line says."""
        if self._hand_z_chosen():
            return float(self.hand_z.value())
        bottom = self._slot_bottom(spot)
        if bottom is None:
            slot = nearest_slot(spot[0], spot[1])
            raise ValueError(f"No bottom is confirmed in slot {slot}.")
        if not bottom.in_a_well(spot):
            raise ValueError("The spot is not in a well.")
        return bottom.z + float(self.above_bottom.value())

    def _aspirate_by_hand(self, aim=None) -> None:
        """By clicking: the camera to `aim` first if given (gantry xy); then,
        from the camera's pose, the tip to the spot under the crosshair,
        down, aspirate, up as the card says and, once up, the camera back to
        that pose. One job."""
        problem = self._tip_problem("aspirate") or self._crosshair_problem()
        if problem:
            self.jog.refuse(problem)
            return
        motion = self.session.motion
        gantry = np.asarray(self.jog.pose[:2] if aim is None else aim,
                            dtype=float)
        spot = self._crosshair_spot(gantry)
        try:
            z = self._hand_z(spot)
        except ValueError as exc:
            self.jog.refuse(str(exc))
            return
        what = f"tip to ({spot[0]:.2f}, {spot[1]:.2f}) at z {z:g}"
        why = (motion.refused((spot[0], spot[1], z))
               or self._under_the_bottom(spot[0], spot[1], z))
        if why:
            self.jog.refuse(f"Not reachable: {why}.")
            return
        session, robot = self.session, self.session.robot
        volume, rate = self._liquid_amount()
        how = self.aspirate_retract.how()
        log.info("aspirate by hand: %s", what)

        def job(log_):
            self._stop.clear()
            if aim is not None:
                motion.camera_to(aim, log=log_)
            camera = np.array(xyz(robot), dtype=float)
            motion.tip_to(spot, z, log=log_)
            log_(f"aspirate {volume:g} µl at {rate:g} µl/s")
            session.aspirate(volume, rate)
            at = xyz(robot)[:2]
            rose = self._leave(how, log_)
            if rose:
                motion.tip_to(camera[:2], camera[2], log=log_)
            return at, rose

        def then(result):
            at, rose = result
            self.record_pick(at)
            self.work_cards.open(self.dispense_card)
            if how.rises and not rose:
                return STOPPED_DOWN
            return f"Aspirated {volume:g} µl."

        if not self.jog.run_job(job, then=then):
            self.jog.refuse(self.session.robot_lease.refusal)

    def _crosshair_problem(self) -> str:
        """Why the spot under the crosshair cannot be known now, or ""."""
        problems = self._map_problems()
        if problems:
            return problems[0]
        if self.session.profile.calibration.pipette_offset is None:
            return NO_TIP_CALIBRATION
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

    def _dispense_in_place(self) -> None:
        """In place's amount where the tip is, the tip left there. Above
        what it holds is refused, nothing moved."""
        problem = self._tip_problem("dispense")
        held = self.session.in_tip
        volume = float(self.in_place_amount.value())
        if not problem and held <= 0:
            problem = "Nothing is in the tip."
        elif not problem and volume > held + 1e-6:
            problem = f"Only {held:g} µl in the tip."
        if problem:
            self.jog.refuse(problem)
            return
        session = self.session
        rate = float(self.in_place_rate.value())

        def job(log):
            amount = session.dispense(rate, volume)
            return f"Dispensed {amount:g} µl here."

        if not self.jog.run_job(job, moves=False):
            self.jog.refuse(self.session.robot_lease.refusal)

    def _dispense(self, target) -> None:
        """Part or All at `target` (see `_go_to`); a well is given into at
        the deposit's height above its bottom."""
        problem = self._tip_problem("dispense")
        held = self.session.in_tip
        volume = self._part()
        if not problem and held <= 0:
            problem = "Nothing is in the tip."
        elif not problem and volume is not None and volume > held + 1e-6:
            problem = f"The tip holds {held:g} µl."
        if problem:
            self.jog.refuse(problem)
            return
        z = (self.session.profile.picking.deposit_offset_z
             if target[0] == "well" else None)
        try:
            go = self._go_to(target, z)
        except ValueError as exc:
            self.jog.refuse(str(exc))
            return
        session = self.session
        rate = float(self.dispense_rate.value())
        how = self.dispense_retract.how()
        where = (f"into {target[1][3]}" if target[0] == "well"
                 else f"at {target[1].name}")

        def job(log):
            self._stop.clear()
            go(log)
            amount = session.dispense(rate, volume)
            if not self._leave(how, log) and how.rises:
                return STOPPED_DOWN
            return f"Dispensed {amount:g} µl {where}."

        def then(said):
            if session.in_tip <= 0:
                self.work_cards.open(self.aspirate_card)
            return said

        if not self.jog.run_job(job, then=then):
            self.jog.refuse(self.session.robot_lease.refusal)

    def _tip_problem(self, what: str) -> str:
        """Why `what` (aspirate, dispense) cannot happen now, or ""."""
        session = self.session
        if session.robot is None:
            return "Robot not connected."
        if session.tip.attached is not True:
            return "Put a tip on first."
        return ""

    def _liquid_amount(self) -> tuple[float, float]:
        return float(self.volume.value()), float(self.flow_rate.value())

    def _in_tip_changed(self, ul: float) -> None:
        self.gauge.set_volume(ul)
        self._refresh()

    # -- the plan's plate ------------------------------------------------------

    def _show_plan(self) -> None:
        """The plate plan's plate first on Dispense's map, its wells in one
        color there."""
        routine = self.session.routine
        destination = getattr(routine, "destination", None)
        picker = self.target_wells
        if destination is None or not getattr(destination, "is_plate", False):
            picker.set_default(None)
            picker.set_marked(None, [], None)
        else:
            picker.set_default(str(destination.slot))
            picker.set_marked(destination.slot, routine.plan,
                              COLORS["planned"])
        self._reload_plates()
        self._target_changed()

    def _task_requested(self, task: str, step: str) -> None:
        """Picking's Finish by hand: Dispense on In a well, the first well
        the run left short chosen, the map showing the plan's."""
        if (task, step) != ("manual", "work"):
            return
        routine = self.session.routine
        destination = getattr(routine, "destination", None)
        if destination is None or not getattr(destination, "is_plate", False):
            return
        self.work_cards.open(self.dispense_card)
        self.target.set_current(WHERE.index("In a well"))
        self._show_plan()
        short = [w for c in destination.ordering for w in c
                 if w in routine.plan and routine.remaining(w)]
        if short:
            self.target_wells.choose(destination.slot, short[0])

    # -- past picks ------------------------------------------------------------

    def record_pick(self, xy) -> None:
        """A past pick at the tip's (x, y): drawn, the last bright, while
        the Past picks chip is on."""
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
            self.jog.refuse("No cuboid model chosen (Settings).")
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

        self._run(Worker(job), "Detecting cuboids", self._cuboids_found)

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
        count = len(detection.df)
        self.jog.tell(f"{count} cuboid{'' if count == 1 else 's'} found.")

    def _show_sizes(self) -> None:
        profile = self.session.profile
        bins = size_bins(
            self._cuboids,
            profile.picking.cuboid_size_threshold if profile else None)
        # A string says why there are none, which the found line covers.
        self._bins = None if isinstance(bins, str) else bins
        self.view.set_histogram(self._bins)
        self._show_size_window()

    def _size_window(self):
        profile = self.session.profile
        return profile.picking.cuboid_size_threshold if profile else None

    def _show_size_window(self) -> None:
        """Snap's row: the window a found cuboid is measured against."""
        window = self._size_window()
        self.sizes_link.setText("" if window is None
                                else f"{window[0]:g}–{window[1]:g} µm")

    def _open_sizes(self) -> None:
        """The sizes against the window, under the link."""
        if self.sizes_popover is None:
            self.sizes_popover = _SizesPopover(self)
        popover, link = self.sizes_popover, self.sizes_link
        popover.histogram.show_detection(self._cuboids, self._size_window())
        popover.adjustSize()
        popover.move(link.mapToGlobal(QPoint(
            link.width() - popover.width(), link.height() + 4)))
        popover.show()

    # -- clicking ---------------------------------------------------------------

    def _click_reaches(self) -> bool:
        """Whether a left click is sent to the page: it moves, and only
        through the pixel map."""
        return self.session.has_pixel_map()

    def _clicked(self, u: float, v: float) -> None:
        """A left click on the picture, in sensor pixels: the camera there
        (see the module's docstring); By clicking, onto a cuboid's center with
        Snap, and the aspirate after it with Aspirate as soon as I click."""
        problems = self._map_problems()
        if problems:
            self.jog.refuse(problems[0])
            return
        # The pose the panel shows, not one read here: a read is a robot
        # command, and the window - the picture with it - would wait on it
        # (#36).
        pose = self.jog.pose
        if self._busy() or self.jog.busy or pose is None:
            return
        profile = self.session.profile
        motion = self.session.motion
        pmap = PixelMap.from_config(profile.pixel_map)
        gantry = np.array(pose[:2])
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
                self.jog.refuse(OUTSIDE_THE_MAP)
                return
            offset = profile.calibration.pipette_offset
            if offset is None:
                self.jog.refuse(NO_TIP_CALIBRATION)
                return
            xy = moves.tip_target(pmap, u, v, gantry, offset)
            what = f"tip over ({xy[0]:.2f}, {xy[1]:.2f}) at the travel height"
        else:
            xy = moves.camera_target(pmap, u, v, gantry)
            if not pmap.covers(u, v):
                notes.append("Approximate: outside the calibrated area.")
            what = f"camera to ({xy[0]:.2f}, {xy[1]:.2f})"

        why = motion.refused((xy[0], xy[1], pose[2]))
        if why:
            self.jog.refuse(f"Not reachable: {why}.")
            return
        if hand and self.aspirate_on_click.isChecked():
            self._aspirate_by_hand(aim=xy)
            return
        log.info("%s", "; ".join([what] + notes))

        def job(log_):
            # The gantry at the travel height is the tip at it: the same
            # move for the camera's target as for the tip's.
            return Reached(motion.camera_to(xy, log=log_), "; ".join(notes))

        if not self.jog.run_job(job):
            self.jog.refuse(self.session.robot_lease.refusal)

    # -- memo points -------------------------------------------------------------

    def _mark(self, u: float, v: float) -> None:
        """A right click on the picture: a new point where the tip would go
        down at this pixel. Moves nothing."""
        problems = self._map_problems()
        if problems:
            self.jog.refuse(problems[0])
            return
        profile = self.session.profile
        offset = profile.calibration.pipette_offset
        if offset is None:
            self.jog.refuse(NO_TIP_CALIBRATION)
            return
        pose = self.jog.pose
        if pose is None:
            self.jog.refuse("Robot busy.")
            return
        pmap = PixelMap.from_config(profile.pixel_map)
        if not pmap.covers(u, v):
            self.jog.refuse(OUTSIDE_THE_MAP)
            return
        xy = moves.tip_target(pmap, u, v, np.asarray(pose[:2]), offset)
        self._add_memo(xy, self._new_z())

    def _place_memo(self) -> None:
        """Point at crosshair: a point under the picture's center,
        where By clicking aims, at the travel height. Moves nothing."""
        problem = self._crosshair_problem()
        if problem:
            self.jog.refuse(problem)
            return
        self._add_memo(self._crosshair_spot(self.jog.pose[:2]), None)

    def _tip_to_crosshair(self) -> None:
        """Tip to crosshair: the tip at the spot under the picture's center,
        where By clicking aims, at the travel height. It stays there: the
        camera's way back is By clicking's, not this."""
        problem = self._tip_problem("move") or self._crosshair_problem()
        if problem:
            self.jog.refuse(problem)
            return
        motion = self.session.motion
        spot = self._crosshair_spot(self.jog.pose[:2])
        travel = motion.travel_z
        why = motion.refused((spot[0], spot[1], motion.envelope.z[1]
                              if travel is None else travel))
        if why:
            self.jog.refuse(f"Not reachable: {why}.")
            return

        def job(log_):
            # No Z: as high as a move may go, which is the travel height.
            return Reached(motion.tip_to(spot, float("inf"), log=log_),
                           f"Tip at ({spot[0]:.2f}, {spot[1]:.2f}).")

        if not self.jog.run_job(job):
            self.jog.refuse(self.session.robot_lease.refusal)

    def _add_memo(self, xy, z) -> None:
        """A new memo point, chosen. It opens nothing: the operator's
        folding stays."""
        point = self.session.add_memo_point(xy[0], xy[1], z)
        self.points_list.choose(point.name)
        self.jog.tell(f"{point.name} added.")
        self._say_memo_problem(point.name)

    def _new_z(self) -> float | None:
        """A new point's Z, as New points get Z says; see "Memo points"."""
        chosen = self.new_z.currentText()
        if chosen == LAST_Z:
            return self._last_z
        pose = self.jog.pose
        if chosen == TIP_Z and pose is not None:
            return round(float(pose[2]), 2)
        return None

    def _set_new_z(self, z) -> None:
        """The last Z given; see "Memo points"."""
        self._last_z = None if z is None else float(z)

    def _memo_z_typed(self, points: PointList, z) -> None:
        self._set_new_z(z)
        self._say_memo_problem(points.chosen_name)

    def _say_memo_problem(self, name) -> None:
        point = self.session.point(name or "")
        problem = self.session.memo_problem(point) if point else ""
        if problem:
            self.jog.refuse(f"{point.name} is not reachable: {problem}.")

    def _marks_choose(self, name) -> None:
        self.marks.choose_memo(name)
        self._refresh()

    def chosen_point(self) -> str | None:
        """The point chosen in the Points card's list."""
        return self.points_list.chosen_name

    def _refresh_memo_buttons(self) -> None:
        if not hasattr(self, "new_z"):
            return
        self.undo_button.setEnabled(self.session.point_history.can_undo)
        self.redo_button.setEnabled(self.session.point_history.can_redo)
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
        lease = self.session.robot_lease
        self.tip_crosshair_button.setEnabled(pixel_map and free and tip)
        self.tip_crosshair_button.setToolTip(
            "Calibrate the camera first." if not pixel_map
            else "Put a tip on first." if not tip
            else f"Robot busy: {lease.holder}." if lease.busy
            else "Move the tip to the spot under the crosshair.")

    def _memo_z_from_tip(self) -> None:
        name, pose = self.chosen_point(), self.jog.pose
        if name is None or pose is None:
            return
        z = round(float(pose[2]), 2)
        self.session.change_point(name, z=z)
        self._set_new_z(z)
        self.jog.tell(f"{name}: Z {z:g}.")
        self._say_memo_problem(name)

    def _camera_to_point(self) -> None:
        """Camera to: the camera over the chosen point at the travel
        height; its Z is not used."""
        point = self.session.point(self.chosen_point() or "")
        pose = self.jog.pose                # not a read: see _clicked
        if point is None or pose is None or self.jog.busy:
            return
        offset = self.session.profile.calibration.pipette_offset
        if offset is None:
            self.jog.refuse(NO_TIP_CALIBRATION)
            return
        xy = np.array([point.x - offset.dx, point.y - offset.dy])
        what = f"camera over {point.name} ({xy[0]:.2f}, {xy[1]:.2f})"
        why = self.session.motion.refused((xy[0], xy[1], pose[2]))
        if why:
            self.jog.refuse(f"Not reachable: {why}.")
            return
        log.info("%s", what)

        def job(log_):
            return Reached(self.session.motion.camera_to(xy, log=log_),
                           f"Camera over {point.name}.")

        if not self.jog.run_job(job):
            self.jog.refuse(self.session.robot_lease.refusal)

    def _tip_to_point(self) -> None:
        """Tip to: the tip to the chosen point, at its Z."""
        point = self.session.point(self.chosen_point() or "")
        if point is None:
            return
        if self.session.tip.attached is not True:
            self.jog.refuse("Put a tip on first.")
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
        why = (self.session.motion.refused((point.x, point.y, check_z))
               or self._under_the_bottom(point.x, point.y, point.z))
        if why:
            self.jog.refuse(f"Not reachable: {why}.")
            return
        log.info("%s", what)
        # No Z: as high as a move may go, which is the travel height.
        z = float("inf") if point.z is None else point.z

        def job(log_):
            return Reached(self.session.motion.tip_to(xy, z, log=log_),
                           f"Tip at {point.name}.")

        if not self.jog.run_job(job):
            self.jog.refuse(self.session.robot_lease.refusal)

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
        pose = self.jog.pose                # not a read: see _clicked
        if pose is None or self.jog.busy:
            return
        profile = self.session.profile
        offset = (profile.calibration.pipette_offset
                  if profile is not None else None)
        if offset is None:
            self.jog.refuse(NO_TIP_CALIBRATION)
            return
        xy = (np.asarray(slot_center(slot), dtype=float)
              - np.array([offset.dx, offset.dy]))
        what = f"camera over slot {slot} ({xy[0]:.1f}, {xy[1]:.1f})"
        why = self.session.motion.refused((xy[0], xy[1], pose[2]))
        if why:
            self.jog.refuse(f"Not reachable: {why}.")
            return
        log.info("%s", what)

        def job(log_):
            return Reached(self.session.motion.camera_to(xy, log=log_),
                           f"Camera moved to slot {slot}.")

        if not self.jog.run_job(job):
            self.jog.refuse(self.session.robot_lease.refusal)

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
        source = self.session.source_plate
        self.source_wells.set_default(None if source is None else source.slot)
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
            self.jog.refuse(problems[0])
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
        self._working = what
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
        self.jog.refuse(reason)
        log.error("manual control: %s", reason)
        self._refresh()

    # -- display ---------------------------------------------------------------------

    def _forget_targets(self, redraw: bool = True) -> None:
        self._cuboids = None
        self._bins = None
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
        free = not self.session.robot_lease.busy
        source = self._source()
        if source is not None and source[0] == "hand":
            # Nothing to choose: a tip on and the robot free is enough.
            self.aspirate_button.setEnabled(
                has_robot and self.session.tip.attached is True and free)
        else:
            self.aspirate_button.setEnabled(has_robot and source is not None
                                            and free)
        ready = has_robot and self.session.tip.attached is True and free
        self.aspirate_in_place.setEnabled(ready)
        self.dispense_in_place.setEnabled(ready and self.session.in_tip > 0)
        target = self._target()
        self.dispense_button.setText(self._dispense_text(target))
        self.dispense_button.setEnabled(has_robot and target is not None
                                        and self.session.in_tip > 0 and free)
        set_primary(self.aspirate_button, self.session.in_tip <= 0)
        set_primary(self.dispense_button, self.session.in_tip > 0)
        self._show_aspirate_summary()
        self._show_dispense_summary()
        self.view.set_status(self._status_lines(busy, problems))

    def _status_lines(self, busy: bool, problems: list[str]) -> list[str]:
        """What holds while it is true, on the picture under the chips."""
        if busy:
            return [self._working + "…"]
        lines = []
        if self._cuboids is not None:
            found = f"{len(self._cuboids.df)} cuboids found"
            if self._bins is not None:
                found += f" · {self._bins.inside} in size"
            lines.append(found)
            if self._cuboids.standin:
                lines.append(
                    "Stand-in cuboid detector: bright blobs, not the model.")
        return lines + problems

    def _on_profile_changed(self) -> None:
        profile = self.session.profile
        self._show_deck()
        self._show_size_window()
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
                self.in_place_amount.setValue(profile.picking.vol)
                self.in_place_rate.setValue(profile.picking.flow_rate)
                self.dispense_rate.setValue(profile.picking.flow_rate)
                self.part.setValue(profile.picking.vol)
            self._forget_targets()
        else:
            self._refresh()
        # A module's height decides whether a measurement is the plate's.
        self._bottom_changed()

    def _on_robot_busy(self, _holder) -> None:
        self._refresh()

    def _robot_changed(self, _state: str) -> None:
        self._reload_plates()
        self._show_deck()
        self._show_heights()

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
        self._bottom_changed()
        self._refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        for shortcut in self._shortcuts:
            shortcut.setEnabled(True)
        # Jogging is done by eye through this camera; without it the page is
        # a jog pad over a blank rectangle.
        self.opener.ensure(self.session.upper_camera_label)
        self._show_camera()
        # The Source step and a plan made elsewhere say nothing to this page;
        # nor does the size window dragged on Picking.
        self._show_plan()
        self._show_size_window()

    def hideEvent(self, event) -> None:
        for shortcut in self._shortcuts:
            shortcut.setEnabled(False)
        super().hideEvent(event)
