"""The OT-2 deck, drawn to its own proportions, with a clickable slot each.

Twelve slots in three columns and four rows, numbered from the front left the
way the robot numbers them, so the picture on screen is the deck as seen from
where the operator stands:

    10  11  12
     7   8   9
     4   5   6
     1   2   3

Slot 12 is the fixed trash and is drawn but never selectable: nothing can be
loaded there and the robot would refuse it. The geometry is the deck's own —
slot pitch and slot size in millimetres from the OT-2 deck definition — rather
than a grid of equal buttons, because an operator who has looked at the real
deck should recognise this one without reading the numbers.

Like `PlateView`, this owns no session and changes no state. It shows what it
is given by `set_labware` and says which slot was clicked. What that click
means is the page's business.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QGraphicsRectItem, QGraphicsScene,
                               QGraphicsSimpleTextItem, QGraphicsTextItem,
                               QGraphicsView)

__all__ = ["DeckView", "SLOTS", "TRASH_SLOT"]

# One accent, as on the plate map: a deck is its own surface, like the camera
# viewport, so its ink is fixed rather than taken from the palette.
ACCENT = QColor(94, 158, 235)
LOADED_FILL = QColor(94, 158, 235, 40)
# A module slot: a raised floor, drawn as a hatched band along the bottom
# edge so it reads as "something is under here" without a word.
MODULE_EDGE = QColor(170, 172, 180)
MODULE_FILL = QColor(170, 172, 180, 50)
# The hazard: labware in a module slot without the module's offset. The
# same amber the status bar uses for a tip on, for the same reason - it has
# to be seen from across the room.
HAZARD = QColor(240, 160, 48)
HAZARD_FILL = QColor(240, 160, 48, 70)
IDLE_EDGE = QColor(120, 122, 130)
TRASH_EDGE = QColor(80, 82, 88)
LABEL = QColor(210, 212, 218)
DIM_LABEL = QColor(140, 142, 150)
BACKDROP = QColor(24, 24, 26)

# OT-2 deck geometry in millimetres: slot pitch and the slot opening. The
# numbers are the deck definition's; the drawing is in these units and the view
# scales it to fit.
SLOT_PITCH_X = 132.5
SLOT_PITCH_Y = 90.5
SLOT_WIDTH = 127.76
SLOT_HEIGHT = 85.48

TRASH_SLOT = "12"
SLOTS = tuple(str(n) for n in range(1, 12))       # the loadable ones


def _slot_origin(slot: int) -> tuple[float, float]:
    """Top-left of a slot in scene units, y down, slot 1 at the bottom left."""
    column = (slot - 1) % 3
    row = (slot - 1) // 3                          # 0 is the front row
    return column * SLOT_PITCH_X, (3 - row) * SLOT_PITCH_Y


class _Slot(QGraphicsRectItem):
    def __init__(self, name: str, rect: QRectF):
        super().__init__(rect)
        self.name = name
        self.setAcceptHoverEvents(True)


class DeckView(QGraphicsView):
    """Twelve slots; eleven of them clickable. Emits the slot's name."""

    slot_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self.setBackgroundBrush(QBrush(BACKDROP))
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._slots: dict[str, _Slot] = {}
        self._names: dict[str, QGraphicsTextItem] = {}
        self._bands: dict[str, QGraphicsRectItem] = {}
        self._band_labels: dict[str, QGraphicsSimpleTextItem] = {}
        self._labware: dict[str, str] = {}
        self._modules: dict[str, str] = {}          # slot -> text on the band
        self._problems: dict[str, str] = {}         # slot -> tooltip
        self._selected: str | None = None
        self._build()

    # -- building ------------------------------------------------------------

    def _build(self) -> None:
        scene = self.scene()
        number_font = QFont()
        number_font.setPointSizeF(9.0)
        number_font.setBold(True)
        name_font = QFont()
        name_font.setPointSizeF(7.0)

        for number in range(1, 13):
            name = str(number)
            x, y = _slot_origin(number)
            rect = QRectF(x, y, SLOT_WIDTH, SLOT_HEIGHT)
            slot = _Slot(name, rect)
            scene.addItem(slot)
            self._slots[name] = slot

            label = QGraphicsSimpleTextItem(
                f"{name}  trash" if name == TRASH_SLOT else name, slot)
            label.setFont(number_font)
            label.setBrush(QBrush(DIM_LABEL if name == TRASH_SLOT else LABEL))
            label.setPos(rect.left() + 6, rect.top() + 4)
            label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

            if name == TRASH_SLOT:
                continue
            # A rich text item, because a labware's display name does not fit
            # on one line at this size and a plain text item does not wrap.
            text = QGraphicsTextItem(slot)
            text.setFont(name_font)
            text.setDefaultTextColor(LABEL)
            text.setTextWidth(SLOT_WIDTH - 12)
            text.setPos(rect.left() + 6, rect.top() + 22)
            text.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self._names[name] = text

            # The module band: along the bottom of the slot, hidden unless
            # the profile puts a module there.
            band = QGraphicsRectItem(
                QRectF(rect.left(), rect.bottom() - 14, SLOT_WIDTH, 14), slot)
            band.setBrush(QBrush(MODULE_FILL, Qt.BrushStyle.BDiagPattern))
            band.setPen(QPen(MODULE_EDGE, 0.8))
            band.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            band.hide()
            self._bands[name] = band
            band_label = QGraphicsSimpleTextItem("", slot)
            band_label.setFont(name_font)
            band_label.setBrush(QBrush(LABEL))
            band_label.setPos(rect.left() + 6, rect.bottom() - 13)
            band_label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            band_label.hide()
            self._band_labels[name] = band_label

        scene.setSceneRect(scene.itemsBoundingRect().adjusted(-6, -6, 6, 6))
        self._repaint()
        self._fit()

    # -- state ---------------------------------------------------------------

    def set_labware(self, names: dict[str, str]) -> None:
        """What each slot holds, by slot name; a slot not in the map is
        empty. Values are what to write in the slot, typically the display
        name."""
        self._labware = {str(k): v for k, v in (names or {}).items()}
        self._repaint()

    def set_modules(self, modules: dict[str, str]) -> None:
        """Which slots carry a module, and what to write on its band, e.g.
        "module +64.2 mm"."""
        self._modules = {str(k): v for k, v in (modules or {}).items()}
        self._repaint()

    def set_problems(self, problems: dict[str, str]) -> None:
        """Slots whose labware was loaded without the module's offset, with
        the sentence to show for each. Drawn in the hazard colour."""
        self._problems = {str(k): v for k, v in (problems or {}).items()}
        self._repaint()

    def select(self, slot: str | None) -> None:
        self._selected = str(slot) if slot is not None else None
        self._repaint()

    @property
    def selected(self) -> str | None:
        return self._selected

    def _repaint(self) -> None:
        for name, slot in self._slots.items():
            if name == TRASH_SLOT:
                slot.setPen(QPen(TRASH_EDGE, 1, Qt.PenStyle.DashLine))
                slot.setBrush(Qt.BrushStyle.NoBrush)
                slot.setToolTip("slot 12 is the fixed trash")
                continue
            held = self._labware.get(name)
            selected = name == self._selected
            problem = self._problems.get(name)
            module = self._modules.get(name)
            if problem:
                slot.setPen(QPen(HAZARD, 3.0 if selected else 2.0))
                slot.setBrush(QBrush(HAZARD_FILL))
            else:
                slot.setPen(QPen(ACCENT if selected else IDLE_EDGE,
                                 2.5 if selected else 1))
                slot.setBrush(QBrush(LOADED_FILL) if held
                              else Qt.BrushStyle.NoBrush)
            tip = (f"slot {name}: {held}" if held else f"slot {name}: empty")
            if module:
                tip += f"\n{module}"
            if problem:
                tip += f"\n{problem}"
            slot.setToolTip(tip)
            self._names[name].setPlainText(held or "")
            self._names[name].setDefaultTextColor(HAZARD if problem else LABEL)
            band, band_label = self._bands[name], self._band_labels[name]
            band.setVisible(bool(module))
            band_label.setVisible(bool(module))
            band_label.setText(module or "")
            band.setPen(QPen(HAZARD if problem else MODULE_EDGE, 0.8))
            band.setBrush(QBrush(HAZARD if problem else MODULE_FILL,
                                 Qt.BrushStyle.BDiagPattern))

    # -- interaction ---------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        item = self.itemAt(event.position().toPoint())
        while item is not None and not isinstance(item, _Slot):
            item = item.parentItem()
        if item is not None and item.name != TRASH_SLOT:
            self.slot_clicked.emit(item.name)
        super().mousePressEvent(event)

    # -- geometry ------------------------------------------------------------

    def _fit(self) -> None:
        if self.scene().sceneRect().isValid():
            self.fitInView(self.scene().sceneRect(),
                           Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fit()
