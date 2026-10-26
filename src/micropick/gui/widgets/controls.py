"""Two small controls of the redesign (REDESIGN §3): Segmented and Switch.

**Segmented** is two or three options, one chosen, the chosen one a
lighter surface ("In a well | At a point | By hand"). Exclusive buttons in
a framed row, so it reads as one control and not as three actions.

**Switch** is an on/off option with its label ("Snap to found cuboids"):
a pill with a knob, painted, because a check box reads as a form field
and the design asks for the pill. The whole row - pill and label - is
the click target.
"""

from __future__ import annotations

from PySide6.QtCore import (Property, QPropertyAnimation, QRectF, QSize, Qt,
                            Signal)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QFrame,
                               QHBoxLayout, QPushButton, QSizePolicy, QWidget)

from ..theme import COLORS, ui_font

__all__ = ["Segmented", "Switch"]


class Segmented(QFrame):
    """One of a few options. `changed` carries the chosen index."""

    changed = Signal(int)

    def __init__(self, options: list[str], *, current: int = 0,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("segmented")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: list[QPushButton] = []
        for i, text in enumerate(options):
            button = QPushButton(text, self)
            button.setObjectName("segment")
            button.setCheckable(True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setSizePolicy(QSizePolicy.Policy.Expanding,
                                 QSizePolicy.Policy.Fixed)
            self._group.addButton(button, i)
            layout.addWidget(button)
            self.buttons.append(button)
        self.buttons[current].setChecked(True)
        self._group.idClicked.connect(self.changed)

    def current(self) -> int:
        return self._group.checkedId()

    def set_current(self, index: int) -> None:
        """Choose without emitting `changed`: the caller set it."""
        self.buttons[index].setChecked(True)

    def set_text(self, index: int, text: str) -> None:
        self.buttons[index].setText(text)


class Switch(QAbstractButton):
    """A labeled on/off pill. Checkable; `toggled(bool)` as any button."""

    _TRACK_W, _TRACK_H = 34, 20

    def __init__(self, text: str = "", *, checked: bool = False,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFont(ui_font())
        self._knob = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(120)
        self.toggled.connect(self._slide)

    def _get_knob(self) -> float:
        return self._knob

    def _set_knob(self, value: float) -> None:
        self._knob = value
        self.update()

    knob = Property(float, _get_knob, _set_knob)

    def _slide(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._knob)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def sizeHint(self) -> QSize:
        text_w = self.fontMetrics().horizontalAdvance(self.text())
        gap = 10 if self.text() else 0
        return QSize(self._TRACK_W + gap + text_w + 2, 28)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled = self.isEnabled()
        top = (self.height() - self._TRACK_H) / 2
        track = QRectF(0, top, self._TRACK_W, self._TRACK_H)
        on = QColor(COLORS["accent"])
        off = QColor(COLORS["control-border"])
        fill = on if self.isChecked() else off
        if not enabled:
            fill = QColor(COLORS["line"])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(track, self._TRACK_H / 2, self._TRACK_H / 2)
        d = self._TRACK_H - 6
        x = 3 + self._knob * (self._TRACK_W - d - 6)
        painter.setBrush(QColor(COLORS["on-accent"] if self.isChecked()
                                else COLORS["text-2"]))
        painter.drawEllipse(QRectF(x, top + 3, d, d))
        if self.text():
            painter.setPen(QColor(COLORS["text"] if enabled
                                  else COLORS["faint"]))
            painter.setFont(self.font())
            painter.drawText(QRectF(self._TRACK_W + 10, 0,
                                    self.width() - self._TRACK_W - 10,
                                    self.height()),
                             Qt.AlignmentFlag.AlignVCenter, self.text())
        painter.end()
