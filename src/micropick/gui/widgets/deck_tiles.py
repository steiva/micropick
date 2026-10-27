"""The Deck step's deck (REDESIGN §6.1): twelve tiles in the deck's real
layout (10 11 12 / 7 8 9 / 4 5 6 / 1 2 3).

A tile shows SLOT N in caps, a chip naming the platform the profile puts
there (its border then dashed), and its labware - an icon in the kind's
color, the name and one line of facts - or "Empty". A slot whose labware
was loaded without its platform's height says so under it, in the warn
color. Slot 12 is the fixed trash: a large faint TRASH on stripes, no
border, and it takes no click and no drop.

A click is `slot_clicked(slot)`; a filled tile dragged onto an empty one
is `moved(source, target)`. What either means is the page's: this owns no
session and shows what `set_tiles` gives it (`mini_deck.DeckTile`).
"""

from __future__ import annotations

from PySide6.QtCore import QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QDrag, QPainter
from PySide6.QtWidgets import (QApplication, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QVBoxLayout, QWidget)

from ..theme import COLORS
from ..theme.icons import pixmap
from .mini_deck import DECK_ROWS, KIND_COLORS, DeckTile

__all__ = ["DeckTiles", "KIND_ICONS", "SLOT_MIME", "TRASH_SLOT", "kind_icon",
           "set_kind_icon"]

TRASH_SLOT = 12
# What a dragged tile carries: its slot number.
SLOT_MIME = "application/x-micropick-slot"
KIND_ICONS = {"plate": "plate", "tips": "tip", "dish": "dish"}


def kind_icon(kind: str | None, size: int, parent: QWidget) -> QLabel:
    """The kind's icon on its own tinted square, as tiles and lists show
    it."""
    label = QLabel(parent)
    box = size + 14
    label.setFixedSize(box, box)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    set_kind_icon(label, kind, size)
    return label


def set_kind_icon(label: QLabel, kind: str | None, size: int) -> None:
    ink, bg = KIND_COLORS.get(kind, ("text-2", "line"))
    label.setPixmap(pixmap(KIND_ICONS.get(kind, "ring"), COLORS[ink], size))
    label.setStyleSheet(f"background: {COLORS[bg]}; border-radius: 9px;")


class _Tile(QFrame):
    def __init__(self, owner: DeckTiles, slot: int):
        super().__init__(owner)
        self.owner = owner
        self.slot = slot
        self.tile = DeckTile(slot)
        self.chosen = False
        self._press: QPoint | None = None
        self.setObjectName("deckTile")
        self.setMinimumSize(150, 96)
        self.setAcceptDrops(slot != TRASH_SLOT)
        self.setCursor(Qt.CursorShape.ArrowCursor if self.trash
                       else Qt.CursorShape.PointingHandCursor)
        column = QVBoxLayout(self)
        column.setContentsMargins(14, 12, 14, 12)
        column.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(8)
        number = QLabel(f"SLOT {slot}", self)
        number.setObjectName("caption")
        top.addWidget(number)
        self.chip = QLabel(self)
        self.chip.setStyleSheet(
            f"color: {COLORS['text-3']}; font-size: 11px; font-weight: 600; "
            f"border: 1px solid {COLORS['control-border']}; "
            f"border-radius: 9px; padding: 1px 8px;")
        top.addWidget(self.chip)
        top.addStretch(1)
        column.addLayout(top)
        column.addStretch(1)

        if self.trash:
            word = QLabel("TRASH", self)
            word.setAlignment(Qt.AlignmentFlag.AlignCenter)
            word.setStyleSheet(f"color: {COLORS['faint']}; font-size: 26px; "
                               f"font-weight: 700; letter-spacing: 6px;")
            column.addWidget(word)
            column.addStretch(1)
        self.item = QWidget(self)
        self.item.setObjectName("plain")
        row = QHBoxLayout(self.item)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.icon = kind_icon(None, 20, self.item)
        row.addWidget(self.icon)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.name = QLabel(self.item)
        self.name.setWordWrap(True)
        self.name.setObjectName("setupLabel")
        text.addWidget(self.name)
        self.facts = QLabel(self.item)
        self.facts.setStyleSheet(f"color: {COLORS['text-3']}; "
                                 f"font-size: 13px;")
        text.addWidget(self.facts)
        row.addLayout(text, 1)
        column.addWidget(self.item)
        self.empty = QLabel("Empty", self)
        self.empty.setStyleSheet(f"color: {COLORS['faint']}; "
                                 f"font-size: 14px;")
        column.addWidget(self.empty)
        self.note = QLabel(self)
        self.note.setWordWrap(True)
        column.addWidget(self.note)
        for label in self.findChildren(QLabel):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    @property
    def trash(self) -> bool:
        return self.slot == TRASH_SLOT

    @property
    def filled(self) -> bool:
        return bool(self.tile.name)

    def show_tile(self, tile: DeckTile, chosen: bool) -> None:
        self.tile, self.chosen = tile, chosen
        self.chip.setText(tile.platform)
        self.chip.setVisible(bool(tile.platform))
        self.item.setVisible(self.filled and not self.trash)
        self.empty.setVisible(not self.filled and not self.trash)
        if self.filled:
            set_kind_icon(self.icon, tile.kind, 20)
        self.name.setText(tile.name)
        self.facts.setText(tile.facts)
        self.facts.setVisible(bool(tile.facts))
        tone = {"ok": "ok", "warn": "warn-text"}.get(tile.note_tone, "muted")
        self.note.setStyleSheet(f"color: {COLORS[tone]}; font-size: 12px;")
        self.note.setText(tile.note)
        self.note.setVisible(bool(tile.note))
        self._style()
        self.setToolTip(f"Slot {self.slot}: {tile.name or 'empty'}"
                        + (f"\n{tile.platform}" if tile.platform else "")
                        + (f"\n{tile.note}" if tile.note else ""))

    def _style(self, dropping: bool = False) -> None:
        if self.trash:
            self.setStyleSheet("QFrame#deckTile { border: none; "
                               "border-radius: 14px; }")
            return
        if self.chosen or dropping:
            border = f"2px solid {COLORS['accent']}"
        elif self.tile.note_tone == "warn":
            border = f"2px solid {COLORS['warn']}"
        else:
            style = "dashed" if self.tile.platform else "solid"
            line = (COLORS["control-border"] if self.tile.platform
                    else COLORS["line"])
            border = f"1px {style} {line}"
        background = (COLORS["selected-bg"] if self.chosen else
                      COLORS["card"] if self.filled else COLORS["bar"])
        self.setStyleSheet(f"QFrame#deckTile {{ background: {background}; "
                           f"border: {border}; border-radius: 14px; }}")

    def paintEvent(self, event) -> None:
        if self.trash:
            # Stripes, not a surface: it must not look like a slot to use.
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(COLORS["bar"])))
            painter.drawRoundedRect(self.rect(), 14, 14)
            painter.setBrush(QBrush(QColor(COLORS["steps-bg"]),
                                    Qt.BrushStyle.BDiagPattern))
            painter.drawRoundedRect(self.rect(), 14, 14)
            painter.end()
        super().paintEvent(event)

    # -- the mouse -----------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if not self.trash and event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (self._press is None or not self.filled
                or (event.position().toPoint() - self._press).manhattanLength()
                < QApplication.startDragDistance()):
            return
        self._press = None
        data = QMimeData()
        data.setData(SLOT_MIME, str(self.slot).encode())
        drag = QDrag(self)
        drag.setMimeData(data)
        drag.setPixmap(self.grab())
        drag.setHotSpot(event.position().toPoint())
        drag.exec(Qt.DropAction.MoveAction)

    def mouseReleaseEvent(self, event) -> None:
        if self._press is not None:
            self._press = None
            self.owner.slot_clicked.emit(self.slot)
        super().mouseReleaseEvent(event)

    def _source(self, event) -> int | None:
        data = event.mimeData()
        if not data.hasFormat(SLOT_MIME):
            return None
        source = int(bytes(data.data(SLOT_MIME)).decode())
        return source if source != self.slot and not self.filled else None

    def dragEnterEvent(self, event) -> None:
        if self._source(event) is not None:
            event.acceptProposedAction()
            self._style(dropping=True)

    def dragLeaveEvent(self, event) -> None:
        self._style()

    def dropEvent(self, event) -> None:
        source = self._source(event)
        self._style()
        if source is not None:
            event.acceptProposedAction()
            self.owner.moved.emit(source, self.slot)


class DeckTiles(QWidget):
    """`slot_clicked(slot)`, `moved(source, target)`."""

    slot_clicked = Signal(int)
    moved = Signal(int, int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)
        self.tiles: dict[int, _Tile] = {}
        for r, row in enumerate(DECK_ROWS):
            grid.setRowStretch(r, 1)
            for c, slot in enumerate(row):
                grid.setColumnStretch(c, 1)
                tile = _Tile(self, slot)
                grid.addWidget(tile, r, c)
                self.tiles[slot] = tile
        self.selected: int | None = None
        self._shown: dict[int, DeckTile] = {}
        self.set_tiles([])

    def set_tiles(self, tiles) -> None:
        """What each slot shows; a slot not given is empty."""
        given = {t.slot: t for t in tiles}
        shown = {slot: given.get(slot, DeckTile(
            slot, kind="trash" if slot == TRASH_SLOT else None))
            for slot in self.tiles}
        if shown == self._shown:
            return
        self._shown = shown
        self._show()

    def select(self, slot: int | None) -> None:
        if slot == self.selected:
            return
        self.selected = slot
        self._show()

    def tile(self, slot: int) -> DeckTile:
        return self._shown[slot]

    def _show(self) -> None:
        for slot, widget in self.tiles.items():
            widget.show_tile(self._shown[slot], slot == self.selected)
