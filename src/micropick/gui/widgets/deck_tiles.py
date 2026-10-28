"""The deck's tiles (REDESIGN §6.1, #40): twelve tiles in the deck's real
layout (10 11 12 / 7 8 9 / 4 5 6 / 1 2 3), the whole map in the real
deck's proportions (`ASPECT`, about 40 wide by 36.5 high) and centered in
whatever room it is given, never stretched sideways.

A tile shows SLOT N in caps, a chip naming the platform the profile puts
there (its border then dashed), and its labware - a framed card of an
icon in the kind's color, the name (wrapped inside the card, however
long) and one line of facts - or "Empty", and a × at its top right while
filled. A slot whose labware was loaded without its platform's height
says so under it, in the warn color. Slot 12 is the fixed trash: a large
faint TRASH on stripes, no border, and it takes no click and no drop; a
tile given as not `valid` (the calibration module's slot) takes neither
either, and keeps its ×.

A click is `slot_clicked(slot)`, a × `remove_clicked(slot)`; a filled
tile's name card dragged onto an empty tile is `moved(source, target)` -
the drag carries that card, not the slot or its platform. What any of
them means is the page's: this owns no session and shows what `set_tiles`
gives it (`mini_deck.DeckTile`). Robot setup's platforms sheet shows the
same tiles without the labware (`show_labware=False`), so both maps read
the same.
"""

from __future__ import annotations

from PySide6.QtCore import QMimeData, QPoint, QRect, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QDrag, QFontMetrics, QPainter,
                           QPixmap)
from PySide6.QtWidgets import (QApplication, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QSizePolicy, QToolButton,
                               QVBoxLayout, QWidget)

from ..theme import COLORS
from ..theme.icons import icon, pixmap
from .mini_deck import DECK_ROWS, KIND_COLORS, DeckTile

__all__ = ["ASPECT", "DeckTiles", "KIND_ICONS", "SLOT_MIME", "TRASH_SLOT",
           "kind_icon", "set_kind_icon"]

TRASH_SLOT = 12
# The OT-2 deck's width over its depth, slot 1's front edge to 10's back.
ASPECT = 40 / 36.5
TILE_MIN = (150, 96)
SPACING = 12
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


_WRAP = Qt.TextFlag.TextWordWrap


def _breakable(name: str) -> str:
    """`name` with a break allowed after each underscore: a load name is
    one long word, and would push its card wider than the tile."""
    return name.replace("_", "_\u200b")


class _Name(QLabel):
    """A labware's name, wrapped in the width it is given and cut with
    "…" where it would overflow the height: the layout may give it less
    than all of it (`minimumSizeHint`, one line), never more than the
    tile has. `shown()` is what is drawn."""

    def setText(self, text: str) -> None:
        super().setText(_breakable(text))
        self.updateGeometry()
        self.update()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return QFontMetrics(self.font()).boundingRect(
            0, 0, max(width, 1), 10_000, _WRAP, self.text()).height()

    def minimumSizeHint(self):
        size = super().minimumSizeHint()
        size.setWidth(0)
        size.setHeight(QFontMetrics(self.font()).height())
        return size

    def shown(self) -> str:
        text = self.text()
        if self.heightForWidth(self.width()) <= self.height():
            return text
        low, high = 0, len(text)
        while low < high:                  # the longest start that fits
            middle = (low + high + 1) // 2
            if self._fits(text[:middle].rstrip() + "…"):
                low = middle
            else:
                high = middle - 1
        return text[:low].rstrip() + "…"

    def _fits(self, text: str) -> bool:
        return QFontMetrics(self.font()).boundingRect(
            0, 0, max(self.width(), 1), 10_000, _WRAP,
            text).height() <= self.height()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(self.rect(), int(_WRAP | Qt.AlignmentFlag.AlignLeft
                                          | Qt.AlignmentFlag.AlignTop),
                         self.shown())
        painter.end()


class _Tile(QFrame):
    def __init__(self, owner: DeckTiles, parent: QWidget, slot: int):
        super().__init__(parent)
        self.owner = owner
        self.slot = slot
        self.tile = DeckTile(slot)
        self.chosen = False
        self._press: QPoint | None = None
        self.setObjectName("deckTile")
        self.setMinimumSize(*TILE_MIN)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Ignored)
        self.setAcceptDrops(owner.show_labware and slot != TRASH_SLOT)
        # The trash is no slot to use, here or in the platforms sheet.
        self.setEnabled(not self.trash)
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
            f"color: {COLORS['text-2']}; font-size: 14px; font-weight: 600; "
            f"border: 1px solid {COLORS['control-border']}; "
            f"border-radius: 11px; padding: 2px 10px;")
        top.addWidget(self.chip)
        top.addStretch(1)
        self.close_button = QToolButton(self)
        self.close_button.setIcon(icon("close", COLORS["text-2"], 16))
        self.close_button.setAutoRaise(True)
        self.close_button.setToolTip(f"Remove what is in slot {slot}")
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_button.clicked.connect(
            lambda: owner.remove_clicked.emit(self.slot))
        self.close_button.hide()
        top.addWidget(self.close_button)
        column.addLayout(top)
        column.addStretch(1)

        if self.trash:
            word = QLabel("TRASH", self)
            word.setAlignment(Qt.AlignmentFlag.AlignCenter)
            word.setStyleSheet(f"color: {COLORS['faint']}; font-size: 26px; "
                               f"font-weight: 700; letter-spacing: 6px;")
            column.addWidget(word)
            column.addStretch(1)
        # The labware's framed name: what a drag carries.
        self.item = QFrame(self)
        self.item.setObjectName("deckName")
        self.item.setStyleSheet(
            f"QFrame#deckName {{ background: {COLORS['card']}; "
            f"border: 1px solid {COLORS['line']}; border-radius: 10px; }}")
        self.item.setSizePolicy(QSizePolicy.Policy.Ignored,
                                QSizePolicy.Policy.Preferred)
        row = QHBoxLayout(self.item)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(10)
        self.icon = kind_icon(None, 20, self.item)
        row.addWidget(self.icon)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.name = _Name(self.item)
        self.name.setObjectName("setupLabel")
        self.name.setSizePolicy(QSizePolicy.Policy.Ignored,
                                QSizePolicy.Policy.Preferred)
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

    @property
    def usable(self) -> bool:
        """Takes a click and a drop."""
        return not self.trash and self.tile.valid

    def text(self) -> str:
        """What the tile says: its slot and what stands or lies there."""
        return f"SLOT {self.slot}\n" + "\n".join(
            t for t in (self.tile.platform, self.tile.name) if t)

    def click(self) -> None:
        """As a click on the tile."""
        if self.usable:
            self.owner.slot_clicked.emit(self.slot)

    def drag_pixmap(self) -> QPixmap:
        return self.item.grab()

    def show_tile(self, tile: DeckTile, chosen: bool) -> None:
        self.tile, self.chosen = tile, chosen
        labware = self.owner.show_labware and not self.trash
        self.chip.setText(tile.platform)
        self.chip.setVisible(bool(tile.platform))
        self.item.setVisible(labware and self.filled)
        self.empty.setVisible(labware and not self.filled)
        self.close_button.setVisible(labware and self.filled)
        self.setCursor(Qt.CursorShape.PointingHandCursor if self.usable
                       else Qt.CursorShape.ArrowCursor)
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
                      COLORS["bar"])
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
        if self.usable and event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (self._press is None or not self.filled
                or not self.owner.show_labware
                or (event.position().toPoint() - self._press).manhattanLength()
                < QApplication.startDragDistance()):
            return
        self._press = None
        data = QMimeData()
        data.setData(SLOT_MIME, str(self.slot).encode())
        drag = QDrag(self)
        drag.setMimeData(data)
        drag.setPixmap(self.drag_pixmap())
        # Held where it was taken, or by its middle when taken beside it.
        at = event.position().toPoint() - self.item.pos()
        drag.setHotSpot(at if self.item.rect().contains(at)
                        else self.item.rect().center())
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
        return (source if source != self.slot and not self.filled
                and self.usable else None)

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
    """`slot_clicked(slot)`, `remove_clicked(slot)`, `moved(source,
    target)`. `tiles` are the tiles by slot, `grid_widget` the map they
    make, sized to `ASPECT`."""

    slot_clicked = Signal(int)
    remove_clicked = Signal(int)
    moved = Signal(int, int)

    def __init__(self, parent: QWidget | None = None, *,
                 show_labware: bool = True):
        super().__init__(parent)
        self.show_labware = show_labware
        self.grid_widget = QWidget(self)
        self.grid_widget.setObjectName("plain")
        grid = QGridLayout(self.grid_widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(SPACING)
        self.tiles: dict[int, _Tile] = {}
        for r, row in enumerate(DECK_ROWS):
            grid.setRowStretch(r, 1)
            for c, slot in enumerate(row):
                grid.setColumnStretch(c, 1)
                tile = _Tile(self, self.grid_widget, slot)
                grid.addWidget(tile, r, c)
                self.tiles[slot] = tile
        width = 3 * TILE_MIN[0] + 2 * SPACING
        height = 4 * TILE_MIN[1] + 3 * SPACING
        self.setMinimumSize(max(width, round(height * ASPECT)),
                            max(height, round(width / ASPECT)))
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

    def resizeEvent(self, event) -> None:
        """The map as large as fits, in the deck's proportions, centered."""
        width, height = self.width(), self.height()
        if width / max(height, 1) > ASPECT:
            width = round(height * ASPECT)
        else:
            height = round(width / ASPECT)
        self.grid_widget.setGeometry(QRect(
            (self.width() - width) // 2, (self.height() - height) // 2,
            width, height))
        super().resizeEvent(event)

    def _show(self) -> None:
        for slot, widget in self.tiles.items():
            widget.show_tile(self._shown[slot], slot == self.selected)
