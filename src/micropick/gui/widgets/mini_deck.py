"""Choosing a slot on a small deck (REDESIGN §3, MiniDeckDropdown).

A button showing `Slot 5  Corning 384 well plate  112 µl ⌄` opens a
popover with the 3×4 deck in its real layout (10 11 12 / 7 8 9 / 4 5 6 /
1 2 3). The slots that can be chosen here - the plates, or any slot for
"Look at" - are lit; the rest are dimmed and do nothing. A tile may carry
a note ("Measured", "Not measured"). The same control on every screen a
plate or a slot is chosen on (§1.7).

The page says what is on the deck (`set_tiles`); this knows no labware.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

from ..theme import COLORS
from ..theme.icons import icon

__all__ = ["DECK_ROWS", "KIND_COLORS", "DeckTile", "MiniDeckDropdown"]

# Top row first, as the deck stands in front of the user.
DECK_ROWS = ((10, 11, 12), (7, 8, 9), (4, 5, 6), (1, 2, 3))

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


class _Tile(QPushButton):
    def __init__(self, tile: DeckTile, chosen: bool, parent: QWidget):
        super().__init__(parent)
        self.tile = tile
        self.setFixedSize(132, 74)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setEnabled(tile.valid)
        ink, bg = KIND_COLORS.get(tile.kind, ("text-2", "bar"))
        border = COLORS["accent"] if chosen else COLORS["line"]
        self.setStyleSheet(
            f"QPushButton {{ background: {COLORS[bg] if tile.valid else COLORS['bg']};"
            f" border: {2 if chosen else 1}px solid {border}; border-radius: 10px;"
            f" text-align: left; padding: 6px 10px; }}"
            f"QPushButton:hover {{ border-color: {COLORS['accent-hover']}; }}")
        column = QVBoxLayout(self)
        column.setContentsMargins(10, 6, 10, 6)
        column.setSpacing(2)
        slot = QLabel(f"SLOT {tile.slot}", self)
        slot.setObjectName("caption")
        name = QLabel(tile.name or ("Trash" if tile.kind == "trash"
                                    else "Empty"), self)
        name.setWordWrap(True)
        ink_color = COLORS[ink] if tile.valid else COLORS["faint"]
        name.setStyleSheet(f"color: {ink_color}; font-size: 12px; "
                           "font-weight: 600;")
        column.addWidget(slot)
        column.addWidget(name)
        if tile.note:
            note = QLabel(tile.note, self)
            tone = {"ok": "ok", "warn": "warn-text"}.get(tile.note_tone,
                                                         "muted")
            note.setStyleSheet(f"color: {COLORS[tone]}; font-size: 11px;")
            column.addWidget(note)
        column.addStretch(1)
        for label in self.findChildren(QLabel):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


class _Popover(QFrame):
    def __init__(self, owner: MiniDeckDropdown):
        super().__init__(owner, Qt.WindowType.Popup)
        self.setObjectName("raisedCard")
        grid = QGridLayout(self)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setSpacing(8)
        for r, row in enumerate(DECK_ROWS):
            for c, slot in enumerate(row):
                tile = owner.tiles.get(slot, DeckTile(slot, valid=False))
                button = _Tile(tile, slot == owner.slot, self)
                button.clicked.connect(lambda _=False, s=slot: owner._chosen(s))
                grid.addWidget(button, r, c)


class MiniDeckDropdown(QPushButton):
    """`slot_chosen(slot)` when a lit tile is clicked."""

    slot_chosen = Signal(int)

    def __init__(self, parent: QWidget | None = None, *,
                 placeholder: str = "Choose a slot"):
        super().__init__(parent)
        self.setObjectName("secondary")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMinimumHeight(40)
        self.tiles: dict[int, DeckTile] = {}
        self.slot: int | None = None
        self.placeholder = placeholder
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 0, 12, 0)
        row.setSpacing(8)
        self._slot = QLabel(self)
        self._slot.setStyleSheet(f"color: {COLORS['accent']}; font-weight: 600;")
        self._name = QLabel(self)
        self._name.setStyleSheet("font-weight: 600;")
        self._facts = QLabel(self)
        self._facts.setStyleSheet(f"color: {COLORS['muted']};")
        arrow = QLabel(self)
        arrow.setPixmap(icon("chevron-down", COLORS["text-2"], 16)
                        .pixmap(16, 16))
        for widget in (self._slot, self._name, self._facts):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            row.addWidget(widget)
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
            self._name.setText(self.placeholder)
            self._facts.setText("")
        else:
            self._slot.setText(f"Slot {tile.slot}")
            self._name.setText(tile.name or "Empty")
            self._facts.setText(tile.facts)
        self.updateGeometry()

    def sizeHint(self) -> QSize:
        width = sum(label.sizeHint().width() for label in
                    (self._slot, self._name, self._facts)) + 80
        return QSize(width, 40)

    def open_popover(self) -> None:
        popover = _Popover(self)
        popover.adjustSize()
        popover.move(self.mapToGlobal(QPoint(0, self.height() + 4)))
        popover.show()
        self._popover = popover

    def _chosen(self, slot: int) -> None:
        if getattr(self, "_popover", None) is not None:
            self._popover.close()
            self._popover = None
        self.set_slot(slot)
        self.slot_chosen.emit(slot)
