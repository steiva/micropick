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
the reason as its tooltip. A tile can name what stands in its slot
(`set_tiles`, the `DeckTile`s of `widgets.mini_deck`).

Unlike `MiniDeckDropdown` it is shown inline, small enough for a step card.

    chooser = SlotChooser(parent)
    chooser.slot_clicked.connect(lambda slot: ...)
    chooser.set_marked(8)            # or None
    chooser.setEnabled(not busy)
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QPushButton, QWidget

from ..theme import COLORS
from .mini_deck import DECK_ROWS, DeckTile

__all__ = ["SlotChooser"]

TILE_W, TILE_H = 92, 44


class SlotChooser(QWidget):
    """`slot_clicked(slot)` on a click; `set_marked`, `marked`, `set_tiles`,
    `slots`, `button(slot)`."""

    slot_clicked = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.marked: int | None = None
        self._buttons: dict[int, QPushButton] = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        for r, row in enumerate(DECK_ROWS):
            for c, slot in enumerate(row):
                button = QPushButton(str(slot), self)
                button.setCheckable(True)
                button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                button.setFixedSize(TILE_W, TILE_H)
                button.setStyleSheet(_STYLE)
                # A checkable button checks itself on a click; only
                # `set_marked` marks, so the click is undone at once.
                button.clicked.connect(lambda _=False, s=slot: self._clicked(s))
                grid.addWidget(button, r, c)
                self._buttons[slot] = button
        self.slots = sorted(self._buttons)

    def button(self, slot: int) -> QPushButton:
        """The tile of `slot`."""
        return self._buttons[slot]

    def set_marked(self, slot: int | None) -> None:
        """Show `slot` as the one chosen (None: none), without emitting."""
        self.marked = slot
        for n, button in self._buttons.items():
            button.setChecked(n == slot)

    def set_tiles(self, tiles) -> None:
        """Name what stands in a slot: a tile's platform, else its labware.
        Slots without a tile show their number only."""
        named = {t.slot: t for t in tiles}
        for n, button in self._buttons.items():
            tile: DeckTile | None = named.get(n)
            what = (tile.platform or tile.name) if tile is not None else ""
            button.setText(f"{n}\n{what}" if what else str(n))
            button.setToolTip(what)

    def _clicked(self, slot: int) -> None:
        self.set_marked(self.marked)
        self.slot_clicked.emit(slot)


_STYLE = (
    f"QPushButton {{ background: {COLORS['bar']}; color: {COLORS['text-2']};"
    f" border: 1px solid {COLORS['line']}; border-radius: 8px;"
    f" font-size: 11px; font-weight: 600; padding: 2px; }}"
    f"QPushButton:hover {{ border-color: {COLORS['accent-hover']}; }}"
    f"QPushButton:checked {{ border: 2px solid {COLORS['accent']};"
    f" color: {COLORS['accent']}; }}"
    f"QPushButton:disabled {{ background: {COLORS['bg']};"
    f" color: {COLORS['faint']}; }}")
