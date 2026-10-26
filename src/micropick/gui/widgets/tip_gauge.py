"""What is in the tip, drawn (REDESIGN §3, TipGauge; §6.6).

A tip with its liquid level and "In the tip · 15 µl", the robot's X / Y /
Z under it. The drawing is the response to Aspirate and Dispense: it
replaces the "Aspirated 15 µl" messages. The level is the volume over the
tip's capacity, and an empty tip draws no liquid at all, so "nothing in
it" reads at a glance.

It sits on a translucent plate over the top left of a picture.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..theme import COLORS, mono_font

__all__ = ["TipGauge"]


class _TipDrawing(QWidget):
    W, H = 22, 52

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFixedSize(self.W, self.H)
        self.fraction = 0.0

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # The prototype's outline: wide top, narrow end.
        top, bottom = 2.0, 48.0
        outline = QPainterPath(QPointF(3, top))
        for x, y in ((19, top), (14, bottom), (8, bottom)):
            outline.lineTo(x, y)
        outline.closeSubpath()
        if self.fraction > 0:
            level = bottom - (bottom - top - 4) * min(1.0, self.fraction)
            # The walls at `level`, by the outline's straight sides.
            t = (level - top) / (bottom - top)
            left, right = 3 + 5 * t + 1.5, 19 - 5 * t - 1.5
            liquid = QPainterPath(QPointF(left, level))
            for x, y in ((right, level), (13, bottom - 1), (9, bottom - 1)):
                liquid.lineTo(x, y)
            liquid.closeSubpath()
            painter.fillPath(liquid, QColor(COLORS["accent"]))
        pen = QPen(QColor(COLORS["text-2"]), 2)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPath(outline)
        painter.end()


class TipGauge(QFrame):
    def __init__(self, *, capacity_ul: float = 200.0,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("tipGauge")
        self.setStyleSheet("QFrame#tipGauge { background: rgba(10, 12, 14, "
                           "0.84); border-radius: 12px; }")
        self.capacity = capacity_ul
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(12)
        self.drawing = _TipDrawing(self)
        row.addWidget(self.drawing)
        column = QVBoxLayout()
        column.setSpacing(2)
        caption = QLabel("IN THE TIP", self)
        caption.setObjectName("caption")
        self.volume = QLabel(self)
        self.volume.setFont(mono_font(18))
        self.position = QLabel(self)
        self.position.setFont(mono_font(11))
        self.position.setStyleSheet(f"color: {COLORS['muted']};")
        for widget in (caption, self.volume, self.position):
            column.addWidget(widget)
        row.addLayout(column)
        self.set_volume(0.0)
        self.set_position(None)

    def set_capacity(self, ul: float) -> None:
        self.capacity = max(1.0, ul)
        self.set_volume(self.ul)

    def set_volume(self, ul: float) -> None:
        self.ul = max(0.0, ul)
        self.volume.setText(f"{self.ul:g} µl")
        self.drawing.fraction = self.ul / self.capacity
        self.drawing.update()

    def set_position(self, xyz) -> None:
        """The robot's X, Y, Z, or None to hide the line."""
        if xyz is None:
            self.position.hide()
            return
        x, y, z = xyz
        self.position.setText(f"X {x:.1f}  Y {y:.1f}  Z {z:.1f}")
        self.position.show()

