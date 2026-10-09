"""The tip, in the status bar: what the robot reports, and the basic acts.

A button rather than a line of text: what is on the pipette is the first
thing to fix before anything moves, and fixing it should not mean finding
the Robot & Deck page first. A click opens a small panel that grows up out
of the button - a popup window, not a menu, so the well can be typed - with
the three acts: drop the tip where it is, drop it in the trash, and take a
new one from a rack in a slot of the robot session.

Like the rest of the status bar the button does not act itself: it says
what was asked (`drop_in_place`, `drop_in_trash`, `pick_up`) and the window
runs it, through the shown page's jog panel when it has one, as Home is
run. What the panel offers comes from `source`, a function the window
gives it, read each time it opens: the tip record, the racks in the robot
session with their wells, and whether anything is running.

The acts are gated on the robot's record both ways, as on Robot & Deck: no
pick-up while a tip is on, no drop while there is none, and nothing while
the record could not be read - "unknown" is not "none".
"""

from __future__ import annotations

from dataclasses import dataclass, field

import qtawesome as qta
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QCompleter, QFrame, QHBoxLayout,
                               QLabel, QToolButton, QVBoxLayout, QWidget)

from ..theme import SPACING
from ..theme.factory import combo_box, heading, primary_button, secondary_button

__all__ = ["TipButton", "TipSource", "Rack"]

# The colour a tip is shown in; see `shell.TIP_ON`.
TIP_ON = "#f0a030"
ICON_PX = 18
PANEL_WIDTH = 400


@dataclass
class Rack:
    """A tip rack in the robot session: its slot, what it is, its wells in
    the order they are taken."""

    slot: str
    name: str
    wells: list[str] = field(default_factory=list)


@dataclass
class TipSource:
    """What the panel shows when it opens."""

    tip: object | None                    # session.Tip, or None: no robot
    racks: list[Rack] = field(default_factory=list)
    busy: bool = False


class _Panel(QFrame):
    """The popup: a card that closes when clicked outside."""

    def __init__(self, button: "TipButton"):
        # No parent: a child of the button would be inside the status bar,
        # and qdarktheme lights everything there under the mouse.
        super().__init__(None, Qt.WindowType.Popup)
        self.button = button
        button.destroyed.connect(self.deleteLater)
        # The click that closes the popup is not passed on: on the button it
        # would open the popup again, where a second click should close it.
        self.setAttribute(Qt.WidgetAttribute.WA_NoMouseReplay)
        self.setObjectName("card")
        self.setFrameShape(QFrame.Shape.Panel)
        self.setFixedWidth(PANEL_WIDTH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2,
                                  SPACING * 2)
        layout.setSpacing(SPACING)

        layout.addWidget(heading("Tip", 2))
        self.state = QLabel(self)
        self.state.setWordWrap(True)
        layout.addWidget(self.state)

        row = QHBoxLayout()
        self.drop_place = secondary_button("Drop in place", self)
        self.drop_place.setToolTip("Let go of the tip where the pipette is.")
        self.drop_trash = secondary_button("Drop in trash", self)
        self.drop_trash.setToolTip("Carry the tip to the trash in slot 12 "
                                   "and drop it there. The gantry moves.")
        row.addWidget(self.drop_place)
        row.addWidget(self.drop_trash)
        row.addStretch(1)
        layout.addLayout(row)

        layout.addWidget(heading("Pick up a new tip", 3))
        row = QHBoxLayout()
        row.addWidget(QLabel("Slot", self))
        self.slot = combo_box(self)
        self.slot.currentIndexChanged.connect(lambda _i: self._fill_wells())
        row.addWidget(self.slot, 1)
        row.addWidget(QLabel("Well", self))
        self.well = combo_box(self)
        self.well.setMinimumWidth(70)
        # Typed to find a well, never to make one: whatever is left in the
        # box when editing ends is a well of the rack, or back to the last.
        self.well.setEditable(True)
        self.well.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        completer = self.well.completer()
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.well.lineEdit().editingFinished.connect(self._well_typed)
        row.addWidget(self.well)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.pick = primary_button("Pick up", self)
        self.pick.setToolTip("Take a tip from this well. The gantry moves.")
        row.addWidget(self.pick)
        row.addStretch(1)
        layout.addLayout(row)
        self.racks_note = QLabel(self)
        self.racks_note.setWordWrap(True)
        layout.addWidget(self.racks_note)

        self.drop_place.clicked.connect(self._drop_place)
        self.drop_trash.clicked.connect(self._drop_trash)
        self.pick.clicked.connect(self._pick)
        self._racks: list[Rack] = []

    # -- filling ---------------------------------------------------------------

    def fill(self, source: TipSource, next_well: dict[str, str]) -> None:
        tip, racks, busy = source.tip, source.racks, source.busy
        self._racks = racks
        if tip is None:
            self.state.setText("No robot session: connect the robot on the "
                               "Profile page.")
        elif tip.attached is None:
            self.state.setText("The robot's tip state could not be read. "
                               "Nothing here moves until it is: connect "
                               "again on the Profile page.")
        elif tip.attached:
            self.state.setText("The robot reports a tip on the pipette"
                               + (f", from slot {tip.slot} {tip.well}"
                                  if tip.returnable else "") + ".")
        else:
            self.state.setText("The robot reports no tip on the pipette.")
        if busy:
            # Said, not a reason to grey the buttons: what the robot is doing
            # may be over by the click, and the click is refused if not.
            self.state.setText(self.state.text() + " The robot is busy now.")

        chosen = self.slot.currentData()
        self.slot.blockSignals(True)
        self.slot.clear()
        for rack in racks:
            self.slot.addItem(f"{rack.slot}: {rack.name}", rack.slot)
        index = self.slot.findData(chosen)
        if index < 0 and racks:
            index = 0
        self.slot.setCurrentIndex(index)
        self.slot.blockSignals(False)
        self._fill_wells(next_well)
        self.racks_note.setText(
            "" if racks or tip is None else
            "No tip rack in the robot session: load one on the Robot & Deck "
            "page.")
        self.racks_note.setVisible(bool(self.racks_note.text()))

        known = tip is not None and tip.attached is not None
        self.drop_place.setEnabled(known and bool(tip.attached))
        self.drop_trash.setEnabled(known and bool(tip.attached))
        self.pick.setEnabled(known and not tip.attached and bool(racks)
                             and self.well.count() > 0)
        self.slot.setEnabled(bool(racks))
        self.well.setEnabled(bool(racks))

    def _rack(self) -> Rack | None:
        slot = self.slot.currentData()
        return next((r for r in self._racks if r.slot == slot), None)

    def _fill_wells(self, next_well: dict[str, str] | None = None) -> None:
        rack = self._rack()
        wells = rack.wells if rack is not None else []
        current = [self.well.itemText(i) for i in range(self.well.count())]
        if wells != current:
            self.well.clear()
            self.well.addItems(wells)
        wanted = (next_well or {}).get(rack.slot) if rack is not None else None
        if wanted is not None and wanted in wells:
            self.well.setCurrentIndex(wells.index(wanted))

    def _well_typed(self) -> None:
        text = self.well.currentText().strip()
        index = self.well.findText(text, Qt.MatchFlag.MatchFixedString)
        if index < 0:
            index = self.well.currentIndex()
        self.well.setCurrentIndex(index)
        self.well.setEditText(self.well.itemText(index))

    # -- acting ----------------------------------------------------------------

    def _drop_place(self) -> None:
        self.hide()
        self.button.drop_in_place.emit()

    def _drop_trash(self) -> None:
        self.hide()
        self.button.drop_in_trash.emit()

    def _pick(self) -> None:
        rack = self._rack()
        # The chosen item, not the text in the box: that may still be a
        # half-typed name.
        well = self.well.itemText(self.well.currentIndex())
        if rack is None or not well:
            return
        self.hide()
        self.button.pick_up.emit(rack.slot, well)


class TipButton(QToolButton):
    drop_in_place = Signal()
    drop_in_trash = Signal()
    pick_up = Signal(str, str)                   # slot, well

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAutoRaise(True)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        # The keys belong to the robot's axes.
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.source = lambda: TipSource(None)
        # A rack is used in order: after a pick-up the panel offers the
        # next well of that rack, so the next one is a fresh tip.
        self._next_well: dict[str, str] = {}
        self._panel = _Panel(self)
        self.clicked.connect(self._open)
        self.show_tip(None)

    def show_tip(self, tip) -> None:
        """None: no robot session. Otherwise the robot's record."""
        if tip is None:
            self.setIcon(qta.icon("mdi6.eyedropper-off"))
            self.setText("tip")
            self.setToolTip("Tip: connect the robot first.")
            return
        if tip.attached:
            icon = qta.icon("mdi6.eyedropper", color=TIP_ON)
            text = tip.describe()
            hint = ("The robot reports a tip on the pipette. Every move is "
                    "that much lower than it looks.")
            if tip.returnable:
                self._remember(tip.slot, tip.well)
        elif tip.attached is None:
            icon = qta.icon("mdi6.help-circle-outline", color=TIP_ON)
            text = "tip: unknown"
            hint = ("The robot's tip state could not be read. Connect again "
                    "on the Profile page before moving anything.")
        else:
            icon = qta.icon("mdi6.eyedropper-off")
            text = "no tip"
            hint = "The robot reports no tip on the pipette."
        self.setIcon(icon)
        self.setText(text)
        # The amber of a tip on, or of not knowing, carries to the words.
        self.setStyleSheet(f"QToolButton {{ color: {TIP_ON}; font-weight: "
                           f"600; }}" if tip.attached is not False else "")
        self.setToolTip(hint + " Click for drop and pick-up.")

    def _remember(self, slot, well: str) -> None:
        """The well after `well` in its rack, for the next pick-up."""
        source = self.source()
        rack = next((r for r in source.racks if r.slot == str(slot)), None)
        if rack is None or well not in rack.wells:
            return
        index = rack.wells.index(well)
        if index + 1 < len(rack.wells):
            self._next_well[rack.slot] = rack.wells[index + 1]

    def _open(self) -> None:
        """The panel over the button, its bottom edge on the button's top,
        right edges together when it would leave the screen. A click while
        it is open closes it: the button toggles it."""
        if self._panel.isVisible():
            self._panel.hide()
            return
        self._panel.fill(self.source(), self._next_well)
        # A wrapped line is given the height its text needs at the panel's
        # width: a popup is sized once, from hints that assume one line.
        inner = PANEL_WIDTH - 4 * SPACING
        for label in (self._panel.state, self._panel.racks_note):
            label.setMinimumHeight(label.heightForWidth(inner)
                                   if label.text() else 0)
        self._panel.adjustSize()
        top_left = self.mapToGlobal(QPoint(0, 0))
        x = top_left.x()
        y = top_left.y() - self._panel.height()
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen is not None:
            x = min(x, screen.right() - self._panel.width())
            x = max(x, screen.left())
            y = max(y, screen.top())
        self._panel.move(x, y)
        self._panel.show()
