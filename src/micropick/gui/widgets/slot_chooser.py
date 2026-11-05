"""The mini deck slot chooser: where something stands, by a click on the
deck (#25, #28).

All twelve slots in the deck's real layout (10 11 12 / 7 8 9 / 4 5 6 /
1 2 3), every one clickable, nothing chosen at first: the operator says
where the thing is, the screen does not guess. Robot setup's sheets ask
"Which slot is it in?" with it - the camera calibration, the three
positions, the shake position's tip box - and move the camera over the
slot clicked at once.

The widget only says which slot was clicked (`slot_clicked`); what that
does is the page's. It does not mark the slot by itself either: the page
marks it (`set_marked`) once what the click asked for went, so a refused
move leaves no slot looking chosen. `setEnabled(False)` grays it, and a
click then says nothing - a page grays it while the robot is busy, with
the reason as its tooltip. The page says what stands in a slot (`set_tiles`,
`labware_kinds.deck_tiles`).

`MiniDeckDropdown`'s deck, its `Tile`s and gaps, shown inline in a step card.

    chooser = SlotChooser(parent)
    chooser.slot_clicked.connect(lambda slot: ...)
    chooser.set_marked(8)            # or None
    chooser.setEnabled(not busy)
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGridLayout, QWidget

from .mini_deck import DECK_ROWS, TILE_GAP, DeckTile, Tile

__all__ = ["SlotChooser"]


class SlotChooser(QWidget):
    """`slot_clicked(slot)` on a click; `set_marked`, `marked`, `set_tiles`,
    `slots`, `button(slot)`."""

    slot_clicked = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.marked: int | None = None
        self._buttons: dict[int, Tile] = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(TILE_GAP)
        for r, row in enumerate(DECK_ROWS):
            for c, slot in enumerate(row):
                button = Tile(DeckTile(slot), False, self)
                # A checkable button checks itself on a click; only
                # `set_marked` marks, so the click is undone at once.
                button.clicked.connect(lambda _=False, s=slot: self._clicked(s))
                grid.addWidget(button, r, c)
                self._buttons[slot] = button
        # A wider panel keeps the gaps: the room left is beside the deck.
        grid.setColumnStretch(len(DECK_ROWS[0]), 1)
        self.slots = sorted(self._buttons)

    def button(self, slot: int) -> Tile:
        """The tile of `slot`."""
        return self._buttons[slot]

    def set_marked(self, slot: int | None) -> None:
        """Show `slot` as the one chosen (None: none), without emitting."""
        self.marked = slot
        for n, button in self._buttons.items():
            button.setChecked(n == slot)

    def set_tiles(self, tiles) -> None:
        """What stands in each slot; a slot without a tile is empty."""
        named = {t.slot: t for t in tiles}
        for n, button in self._buttons.items():
            tile = named.get(n, DeckTile(n))
            button.show_tile(tile)
            button.setToolTip(tile.name or tile.platform)

    def _clicked(self, slot: int) -> None:
        self.set_marked(self.marked)
        self.slot_clicked.emit(slot)
