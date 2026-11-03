"""A cross-section of a well with the tip at a height (LIQUID.md §3.4).

Heights are drawn rather than explained: dashed top and bottom lines, the tip
down to the height with a line in the step's color, and under it the height in
words (`core.liquid_plan.height_words`) and where the bottom comes from. The
page gives it a new height as a field changes.

The picture is to scale for the well's depth, so 2 mm above the bottom of a
6-well plate is lower on it than on a 96-well plate. An offset out of the well
stays on the picture, against its edge.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ...core.liquid_plan import Height, height_words
from ..theme import COLORS, SPACING
from ..theme.factory import caption, muted_label

__all__ = ["WellSection"]

# Of the drawing's height: where the rim and the floor of the well are.
RIM = 0.2
FLOOR = 0.88
# The well's width, of the drawing's width.
WIDTH = 0.6
TIP_PX = 14
EDGE_PX = 6
# While the plate's depth is not known.
DEPTH_MM = 10.0


class _Drawing(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.height_ = Height()
        self.depth_mm = DEPTH_MM
        self.color = QColor(COLORS["tips"])
        self.setMinimumSize(160, 200)

    def sizeHint(self) -> QSize:
        return QSize(240, 300)

    def _scale(self) -> float:
        """Pixels per mm."""
        return self.height() * (FLOOR - RIM) / self.depth_mm

    def tip_y(self) -> float:
        """Where the tip's end is drawn."""
        rim, floor = self.height() * RIM, self.height() * FLOOR
        ref = {"top": rim, "middle": (rim + floor) / 2,
               "bottom": floor}[self.height_.ref]
        y = ref - self.height_.offset_mm * self._scale()
        return min(max(y, EDGE_PX), self.height() - EDGE_PX)

    def tip_x(self) -> float:
        """Sideways only in X: the section is cut along it."""
        half = self.width() * WIDTH / 2 - TIP_PX
        x = self.height_.aside_x_mm * self._scale()
        return self.width() / 2 + min(max(x, -half), half)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self.width(), self.height()
        rim, floor = height * RIM, height * FLOOR
        left = width * (1 - WIDTH) / 2
        right = width - left

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLORS["card"]))
        painter.drawRect(QRectF(left, rim, right - left, floor - rim))
        painter.setPen(QPen(QColor(COLORS["text-3"]), 2))
        painter.drawPolyline(QPolygonF([
            QPointF(left, rim), QPointF(left, floor), QPointF(right, floor),
            QPointF(right, rim)]))

        dashed = QPen(QColor(COLORS["text-3"]), 1, Qt.PenStyle.DashLine)
        painter.setPen(dashed)
        for y, name in ((rim, "top"), (floor, "bottom")):
            painter.drawLine(QPointF(0, y), QPointF(width, y))
            painter.drawText(QPointF(2, y - 4), name)

        x, y = self.tip_x(), self.tip_y()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLORS["text-2"]))
        painter.drawPolygon(QPolygonF([
            QPointF(x - TIP_PX, 0), QPointF(x + TIP_PX, 0),
            QPointF(x + 2, y), QPointF(x - 2, y)]))
        painter.setPen(QPen(self.color, 2))
        painter.drawLine(QPointF(left - 12, y), QPointF(right + 12, y))
        painter.end()


class WellSection(QWidget):
    """A caption, the drawing, the height in words and a quiet note."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        self.title = caption("", self)
        column.addWidget(self.title)
        self.drawing = _Drawing(self)
        column.addWidget(self.drawing, 1)
        self.sentence = QLabel(self)
        self.sentence.setObjectName("heading3")
        self.sentence.setWordWrap(True)
        column.addWidget(self.sentence)
        self.note = muted_label("", self)
        column.addWidget(self.note)

    def show_height(self, title: str, color: str, height: Height, note: str,
                    depth_mm: float | None = None) -> None:
        """`color`: the name of a theme color."""
        self.title.setText(title)
        self.title.setStyleSheet(f"color: {COLORS[color]};")
        self.sentence.setText(height_words(height))
        self.note.setText(note)
        self.drawing.height_ = height
        self.drawing.depth_mm = depth_mm or DEPTH_MM
        self.drawing.color = QColor(COLORS[color])
        self.drawing.update()
