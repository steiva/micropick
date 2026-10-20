"""The cuboids' sizes in a small card over the picture's bottom-left corner.

Where the size histogram used to be drawn into the camera view, a widget
of its own instead: it takes the mouse, so the size window is set on the
histogram itself - the band dragged, its edges pulled (`SizeHistogram`,
"The window can be dragged") - rather than typed into the picking settings
while looking at another window. The expand button opens the figures in
their own window (`widgets.cuboid_stats`); the cross hides the card until
the dish is analysed again or Sizes asks for it.

It sits over the picture as a child of the camera view and keeps to its
bottom-left corner as the view is resized (`follow`); the keys box is the
bottom-right one, so the two do not meet.
"""

from __future__ import annotations

import qtawesome as qta
from PySide6.QtCore import QEvent, QObject, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QToolButton, QVBoxLayout,
                               QWidget)

from ..theme import SPACING
from ..theme.factory import card
from .size_histogram import SizeHistogram

__all__ = ["SizePanel", "PANEL_SIZE"]

# The card, in widget pixels: room for a readable plot, small beside the
# picture.
PANEL_SIZE = (380, 270)
MARGIN = 12


class SizePanel(QWidget):
    """The histogram card. Emits what its buttons ask; the page decides."""

    expand_clicked = Signal()
    window_moved = Signal(int, int)

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFixedSize(*PANEL_SIZE)
        frame = card(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        layout = frame.layout()
        layout.setContentsMargins(SPACING, SPACING, SPACING, SPACING)
        layout.setSpacing(SPACING // 2)

        row = QHBoxLayout()
        title = QLabel("<b>Sizes</b>", self)
        row.addWidget(title)
        row.addStretch(1)
        self.expand_button = QToolButton(self)
        self.expand_button.setIcon(qta.icon("mdi6.arrow-expand"))
        self.expand_button.setToolTip("All the measures in a window.")
        self.expand_button.setAutoRaise(True)
        self.expand_button.clicked.connect(self.expand_clicked)
        self.close_button = QToolButton(self)
        self.close_button.setIcon(qta.icon("mdi6.close"))
        self.close_button.setToolTip("Hide.")
        self.close_button.setAutoRaise(True)
        self.close_button.clicked.connect(self.hide)
        row.addWidget(self.expand_button)
        row.addWidget(self.close_button)
        layout.addLayout(row)

        self.histogram = SizeHistogram("Not analyzed yet.", self)
        self.histogram.plot.setMinimumHeight(140)
        self.histogram.plot.setMaximumHeight(170)
        self.histogram.window_moved.connect(self.window_moved)
        layout.addWidget(self.histogram, 1)
        self.hide()

    def follow(self, view: QWidget) -> None:
        """Keep to `view`'s bottom-left corner as it is resized."""
        self._follower = _Follower(self, view)
        self._follower.place()


class _Follower(QObject):
    def __init__(self, panel: SizePanel, view: QWidget):
        super().__init__(panel)
        self._panel, self._view = panel, view
        view.installEventFilter(self)

    def place(self) -> None:
        self._panel.move(MARGIN, max(MARGIN, self._view.height()
                                     - self._panel.height() - MARGIN))
        self._panel.raise_()

    def eventFilter(self, obj, event) -> bool:
        if obj is self._view and event.type() in (QEvent.Type.Resize,
                                                  QEvent.Type.Show):
            self.place()
        return False
