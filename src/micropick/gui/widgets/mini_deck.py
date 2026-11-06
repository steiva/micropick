"""Choosing a slot on a small deck (REDESIGN §3, MiniDeckDropdown).

A button showing `Slot 5  Corning 384 well plate  112 µl ⌄` opens a
popover with the 3×4 deck in its real layout (10 11 12 / 7 8 9 / 4 5 6 /
1 2 3). The slots that can be chosen here - the plates, or any slot for
"Look at" - are lit; the rest are dimmed and do nothing. A tile may carry
a note ("Measured", "Not measured"). The same control on every screen a
plate or a slot is chosen on (§1.7), and `Tile` the same tile there and in
`widgets.slot_chooser`, the deck shown inline.

The page says what is on the deck (`set_tiles`); this knows no labware.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from ..theme import COLORS
from ..theme.icons import icon
from .controls import CutLabel, closed_by

__all__ = ["DECK_ROWS", "KIND_COLORS", "DeckTile", "MiniDeckDropdown", "Tile",
           "TILE_GAP"]

# Top row first, as the deck stands in front of the user.
DECK_ROWS = ((10, 11, 12), (7, 8, 9), (4, 5, 6), (1, 2, 3))

# Three tiles and their gaps fit a step card in a panel of 420 px.
TILE_SIZE = (124, 74)
TILE_GAP = 8

# A kind of labware: its ink and its tile's background.
KIND_COLORS = {"plate": ("plate", "plate-bg"), "tips": ("tips", "tips-bg"),
               "dish": ("dish", "dish-bg")}


@dataclass(frozen=True)
class DeckTile:
    """What a slot shows. `kind` is "plate", "tips", "dish", "trash" or
    None; `name` the labware's, empty for an empty slot."""

    slot: int
    name: str = ""
    kind: str | None = None
    facts: str = ""            # beside the name on the button ("112 µl")
    note: str = ""             # under it on the tile ("Measured")
    note_tone: str = ""        # "ok" or "warn" colors the note
    valid: bool = True         # can be chosen here
    platform: str = ""         # what stands in the slot ("Picking platform")


class Tile(QPushButton):
    """A slot on a small deck, checked while it is the one chosen. Grayed by
    `setEnabled`, whoever says so."""

    def __init__(self, tile: DeckTile, chosen: bool, parent: QWidget):
        super().__init__(parent)
        self.tile: DeckTile | None = None
        self.setFixedSize(*TILE_SIZE)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCheckable(True)
        self.setChecked(chosen)
        self.setEnabled(tile.valid)
        column = QVBoxLayout(self)
        # Tight enough for a two-line name over a note.
        column.setContentsMargins(10, 4, 10, 4)
        column.setSpacing(0)
        self.slot_label = QLabel(self)
        self.slot_label.setObjectName("caption")
        self.name = QLabel(self)
        self.name.setWordWrap(True)
        self.name.setAlignment(Qt.AlignmentFlag.AlignLeft
                               | Qt.AlignmentFlag.AlignTop)
        self.note = QLabel(self)
        column.addWidget(self.slot_label)
        column.addWidget(self.name)
        column.addWidget(self.note)
        column.addStretch(1)
        for label in self.findChildren(QLabel):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.show_tile(tile)

    def show_tile(self, tile: DeckTile) -> None:
        if tile == self.tile:
            return
        self.tile = tile
        ink, bg = KIND_COLORS.get(tile.kind, ("text-2", "bar"))
        self.setStyleSheet(
            f"QPushButton {{ background: {COLORS[bg]};"
            f" border: 1px solid {COLORS['line']}; border-radius: 10px;"
            f" text-align: left; padding: 6px 10px; }}"
            f"QPushButton:hover {{ border-color: {COLORS['accent-hover']}; }}"
            f"QPushButton:checked {{ background: {COLORS[bg]};"
            f" border: 2px solid {COLORS['accent']}; }}"
            f"QPushButton:disabled {{ background: {COLORS['bg']}; }}")
        self.slot_label.setText(f"SLOT {tile.slot}")
        self.name.setText(tile.name or tile.platform
                          or ("Trash" if tile.kind == "trash" else "Empty"))
        self.name.setStyleSheet(
            f"QLabel {{ color: {COLORS[ink]}; font-size: 12px;"
            f" font-weight: 600; }}"
            f"QLabel:disabled {{ color: {COLORS['faint']}; }}")
        tone = {"ok": "ok", "warn": "warn-text"}.get(tile.note_tone, "muted")
        self.note.setText(tile.note)
        self.note.setStyleSheet(f"color: {COLORS[tone]}; font-size: 11px;")
        self.note.setVisible(bool(tile.note))


class _Popover(QFrame):
    def __init__(self, owner: MiniDeckDropdown):
        # The window's child, not the button's: a stylesheet on the button
        # (`CameraView.add_top_row_widget`, a height for a button over the
        # picture) would reach the tiles, which are buttons too, and the deck
        # is to look the same wherever it is opened.
        super().__init__(owner.window(), Qt.WindowType.Popup)
        self.setObjectName("raisedCard")
        closed_by(self, owner)
        grid = QGridLayout(self)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setSpacing(TILE_GAP)
        for r, row in enumerate(DECK_ROWS):
            for c, slot in enumerate(row):
                tile = owner.tiles.get(slot, DeckTile(slot, valid=False))
                button = Tile(tile, slot == owner.slot, self)
                button.clicked.connect(lambda _=False, s=slot: owner._chosen(s))
                grid.addWidget(button, r, c)


class MiniDeckDropdown(QPushButton):
    """`slot_chosen(slot)` when a lit tile is clicked."""

    slot_chosen = Signal(int)

    def __init__(self, parent: QWidget | None = None, *,
                 placeholder: str = "Choose a slot", prefix: str = "",
                 cut: bool = False):
        """`cut`: the name cut to the room there is, for a card's width."""
        super().__init__(parent)
        self.setObjectName("secondary")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMinimumHeight(40)
        if cut:
            self.setSizePolicy(QSizePolicy.Policy.Ignored,
                               QSizePolicy.Policy.Fixed)
        self.tiles: dict[int, DeckTile] = {}
        self.slot: int | None = None
        self.placeholder = placeholder
        self._popover: _Popover | None = None
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 0, 12, 0)
        row.setSpacing(8)
        # What it is for, inside the button: on a picture it has no row of
        # its own to be named in.
        self.prefix = QLabel(prefix, self)
        self.prefix.setStyleSheet(f"color: {COLORS['muted']};")
        self.prefix.setVisible(bool(prefix))
        self._slot = QLabel(self)
        self._slot.setStyleSheet(f"color: {COLORS['accent']}; font-weight: 600;")
        self._name = CutLabel(self) if cut else QLabel(self)
        self._name.setStyleSheet("font-weight: 600;")
        self._facts = QLabel(self)
        self._facts.setStyleSheet(f"color: {COLORS['muted']};")
        self._facts.setVisible(not cut)     # the name tells plates apart
        arrow = QLabel(self)
        arrow.setPixmap(icon("chevron-down", COLORS["text-2"], 16)
                        .pixmap(16, 16))
        for widget in (self.prefix, self._slot, self._name, self._facts):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            row.addWidget(widget, 1 if cut and widget is self._name else 0)
        if not cut:
            row.addStretch(1)
        row.addWidget(arrow)
        arrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.clicked.connect(self.open_popover)
        self._refresh()

    def set_tiles(self, tiles) -> None:
        self.tiles = {t.slot: t for t in tiles}
        self._refresh()

    def set_slot(self, slot: int | None) -> None:
        """Show `slot` as chosen, without emitting."""
        self.slot = slot
        self._refresh()

    def _refresh(self) -> None:
        tile = self.tiles.get(self.slot) if self.slot is not None else None
        if tile is None:
            self._slot.setText("")
            self._show_name(self.placeholder)
            self._facts.setText("")
        else:
            self._slot.setText(f"Slot {tile.slot}")
            self._show_name(tile.name or "Empty")
            self._facts.setText(tile.facts)
        self.updateGeometry()

    def _show_name(self, name: str) -> None:
        if isinstance(self._name, CutLabel):
            self._name.set_full(name)
        else:
            self._name.setText(name)

    def sizeHint(self) -> QSize:
        width = sum(label.sizeHint().width() for label in
                    (self.prefix, self._slot, self._name, self._facts)) + 80
        return QSize(width, 40)

    def open_popover(self) -> None:
        if self._popover is not None:
            self._popover.deleteLater()     # the window's, it would stay
        popover = _Popover(self)
        popover.adjustSize()
        popover.move(self.mapToGlobal(QPoint(0, self.height() + 4)))
        popover.show()
        self._popover = popover

    def _chosen(self, slot: int) -> None:
        if self._popover is not None:
            self._popover.close()
        self.set_slot(slot)
        self.slot_chosen.emit(slot)
