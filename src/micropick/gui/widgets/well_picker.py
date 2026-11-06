"""A plate on the deck and one of its wells, by the mouse: In a well on Manual
pick's Aspirate and Dispense (MANUAL.md §4.2, §4.3).

The mini deck chooses the plate, a click on its map the well. The map is the
plate in its real proportions (`PlateGrid`), any well choosable, the chosen one
in the card's color and ringed. Wells of the page's (`set_marked`: the plate
plan's) are one color on their plate, under the chosen one.

A well is always chosen while a plate is: one the new plate has, else its
first, which the card's button says.

Without `plate_choice` the page has chosen the plate (Well center's list), and
it is the map alone, the well named under the title's row.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..labware_kinds import facts, kind
from ..theme import COLORS, mono_font
from .mini_deck import DeckTile, MiniDeckDropdown
from .plate_grid import PLATE_MM, PlateGrid

__all__ = ["WellPicker"]


class WellPicker(QWidget):
    """`changed` when the plate or the well does. `color` is a token, the
    chosen well's; `width` the map's, in pixels."""

    changed = Signal()

    def __init__(self, color: str, width: int,
                 parent: QWidget | None = None, *, plate_choice: bool = True):
        super().__init__(parent)
        self.setObjectName("plain")
        self._color = QColor(COLORS[color])
        self._plates: dict[str, tuple] = {}
        self._slot: str | None = None
        self._clicked_plate = False         # a default gives way to a click
        self._default: str | None = None
        self._well = ""
        self._marked: tuple[str | None, list[str], str | None] = (
            None, [], None)
        self.plate = MiniDeckDropdown(self, placeholder="Choose a plate",
                                      cut=True)
        self.plate.slot_chosen.connect(self._plate_clicked)
        self.well_label = QLabel(self)
        self.well_label.setFont(mono_font(14, 600))
        self.well_label.setStyleSheet(f"color: {COLORS[color]};")
        self.map = PlateGrid(mini=True, parent=self)
        self.map.setFixedSize(width, round(width * PLATE_MM[1] / PLATE_MM[0]))
        self.map.well_clicked.connect(self._well_clicked)

        self.plate.setVisible(plate_choice)
        row = QHBoxLayout()
        row.addWidget(self.plate, 1 if plate_choice else 0)
        row.addWidget(self.well_label)
        if not plate_choice:
            row.addStretch(1)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        column.addLayout(row)
        column.addWidget(self.map, 0, Qt.AlignmentFlag.AlignHCenter)

    def set_default(self, slot: str | None) -> None:
        """The plate to show until one is clicked, if it is on the deck."""
        self._default = slot

    def set_plates(self, plates) -> None:
        """`Session.plates()`, the one in hand kept."""
        self._plates = {slot: (slot, entry.labware_id, definition)
                        for slot, entry, definition in plates}
        self.plate.set_tiles([
            DeckTile(int(slot), name=definition.display_name,
                     kind=kind(definition), facts=facts(definition))
            for slot, _id, definition in self._plates.values()])
        if self._clicked_plate and self._slot in self._plates:
            slot = self._slot
        elif self._default in self._plates:
            slot = self._default
        elif self._slot in self._plates:
            slot = self._slot
        else:
            slot = next(iter(self._plates), None)
        self._show_plate(slot)

    def _show_plate(self, slot: str | None) -> None:
        """The plate in `slot` on the map, the well in hand kept if it has
        it, else its first."""
        self._slot = slot
        self.plate.set_slot(None if slot is None else int(slot))
        definition = self._definition()
        ordering = definition.ordering if definition is not None else []
        self.map.set_ordering(ordering)
        if definition is not None and self._well not in definition.wells:
            self._well = ordering[0][0]
        self._show_well()
        self.changed.emit()

    def _definition(self):
        held = self._plates.get(self._slot)
        return held[2] if held is not None else None

    def _plate_clicked(self, slot: int) -> None:
        self._clicked_plate = True
        self._show_plate(str(slot))

    def _well_clicked(self, well: str) -> None:
        if well == self._well:
            return
        self._well = well
        self._show_well()
        self.changed.emit()

    def _show_well(self) -> None:
        held = self._definition() is not None
        slot, wells, color = self._marked
        fills = {well: color for well in wells} if slot == self._slot else {}
        if held:
            fills[self._well] = self._color
        self.map.set_fills(fills)
        self.map.set_current(self._well if held else None)
        self.well_label.setText(self._well if held else "")

    def set_marked(self, slot, wells, color) -> None:
        """`wells` in `color` while the plate in `slot` is the one shown."""
        self._marked = (None if slot is None else str(slot), list(wells), color)
        self._show_well()

    def chosen(self):
        """(slot, labware id, definition, well), or None without a plate."""
        held = self._plates.get(self._slot)
        return None if held is None else (*held, self._well)

    def choose(self, slot, well: str) -> None:
        """The plate in `slot` and its `well`, if the session holds it."""
        slot = str(slot)
        if slot in self._plates and well in self._plates[slot][2].wells:
            self._well = well
            self._clicked_plate = True
            self._show_plate(slot)
