"""A camera view and the panel beside it, the picture given only what it fills.

A stretch factor gave the view every pixel the panel did not take, and the
picture - 4:3 from the upper camera, square from the lower one's crop - was
then letterboxed inside a wide black box while the panel beside it scrolled.
Here the view is as wide as its frame's shape needs at the height there is,
and the panel takes the rest: at least the width it was designed for, at most
`MAX_SIDE_FACTOR` times that, since cards stretched across half a screen read
worse than a picture with a margin.

Placed by hand in `resizeEvent` rather than by a layout: the split depends on
the height, and no box layout asks its children for a width given a height.
"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QWidget

from ..theme import SPACING
from .camera_view import CameraView

__all__ = ["FeedRow"]

MAX_SIDE_FACTOR = 2.0


class FeedRow(QWidget):
    def __init__(self, view: CameraView, side: QWidget,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.view, self.side = view, side
        view.setParent(self)
        side.setParent(self)
        # The width the panel was built for is its minimum here.
        self._min_side = max(side.minimumWidth(), side.sizeHint().width())
        self._max_side = int(self._min_side * MAX_SIDE_FACTOR)
        view.aspect_changed.connect(self._place)

    def _place(self) -> None:
        width, height = self.width(), self.height()
        if width <= 0 or height <= 0:
            return
        wanted_view = int(round(height * self.view.aspect))
        side = width - SPACING - wanted_view
        side = max(self._min_side, min(self._max_side, side))
        view = max(0, width - SPACING - side)
        if hasattr(self.side, "set_outer_width"):
            self.side.set_outer_width(side)
        else:
            self.side.setFixedWidth(side)
        self.view.setGeometry(0, 0, view, height)
        self.side.setGeometry(view + SPACING, 0, side, height)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._place()

    def minimumSizeHint(self) -> QSize:
        view = self.view.minimumSize()
        return QSize(view.width() + SPACING + self._min_side, view.height())

    def sizeHint(self) -> QSize:
        return QSize(int(700 * self.view.aspect) + SPACING + self._min_side, 700)
