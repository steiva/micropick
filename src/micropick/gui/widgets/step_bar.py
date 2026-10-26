"""The numbered steps of the current task (REDESIGN §3, StepBar).

`Deck — Tip — Plan — Dish bottom — Picking`, centered. A done step shows
a green ✓ on `ok-bg`; the current one is an accent dot with its number on
`selected-bg`; the others a gray number. Every step can be clicked: the
user is led, never locked (§1.5). Whether a step is done is the caller's
to say, computed from the session's state each time (§4), never stored
here as "visited".
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QWidget

from ..theme import COLORS, SIZES, ui_font

__all__ = ["StepBar", "StepChip"]


class StepChip(QPushButton):
    """One step: a round mark and the step's name."""

    MARK = 22

    def __init__(self, number: int, name: str, parent: QWidget | None = None):
        super().__init__(name, parent)
        self.setObjectName("stepChip")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.number = number
        self.done = False
        self.current = False
        self.setProperty("current", False)
        # Room on the left for the painted mark.
        self.setStyleSheet(f"padding-left: {self.MARK + 16}px;")

    def set_state(self, *, done: bool, current: bool) -> None:
        self.done, self.current = done, current
        self.setProperty("current", current)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(hint.width(), 34)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = self.MARK
        rect = QRectF(8, (self.height() - d) / 2, d, d)
        if self.done and not self.current:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(COLORS["ok-bg"]))
            painter.drawEllipse(rect)
            pen = QPen(QColor(COLORS["ok"]), 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawPolyline([rect.topLeft() + QPointF(x * d, y * d)
                                  for x, y in ((.30, .52), (.45, .67),
                                               (.72, .36))])
        else:
            fill = COLORS["accent"] if self.current else COLORS["hover"]
            ink = COLORS["on-accent"] if self.current else COLORS["muted"]
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(fill))
            painter.drawEllipse(rect)
            painter.setPen(QColor(ink))
            painter.setFont(ui_font(12, 700))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                             str(self.number))
        painter.end()


class StepBar(QFrame):
    """`clicked(index)` when a step is chosen."""

    clicked = Signal(int)

    def __init__(self, steps: list[str] | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("stepBar")
        self.setFixedHeight(SIZES["step-bar"])
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(16, 0, 16, 0)
        self._row.setSpacing(8)
        self.chips: list[StepChip] = []
        self.set_steps(steps or [])

    def set_steps(self, steps: list[str]) -> None:
        while self._row.count():
            item = self._row.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        self.chips = []
        self._row.addStretch(1)
        for i, name in enumerate(steps):
            if i:
                line = QFrame(self)
                line.setObjectName("stepLine")
                line.setFixedSize(24, 1)
                self._row.addWidget(line)
            chip = StepChip(i + 1, name, self)
            chip.clicked.connect(lambda _=False, n=i: self.clicked.emit(n))
            self._row.addWidget(chip)
            self.chips.append(chip)
        self._row.addStretch(1)

    def set_state(self, current: int, done: list[bool]) -> None:
        """Which step is shown, and which are done now."""
        for i, chip in enumerate(self.chips):
            chip.set_state(done=bool(done[i]) if i < len(done) else False,
                           current=i == current)
