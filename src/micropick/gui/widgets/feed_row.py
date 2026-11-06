"""A camera view and the panel beside it (TIP.md §2.1).

The picture's box is 4:3 at the row's height where the width allows. The shape
is the upper camera's whatever is shown (#50), not the frame's: a view that
followed the frame resized the row at each switch between cameras, the panel
jumping with it. Another shape - the lower camera's square crop - is letterboxed
inside by `CameraView`, black bars beside it, never stretched.

The panel keeps the width it was designed for and is centered in what the
picture leaves, its cards never stretched across a free half screen. With too
little width the picture gives way, still 4:3, its box at the top of the row.
What centers is the content: the room a scroll bar keeps at the panel's right
(`bar_room`) is outside it.

A panel of fixed columns (`WorkColumns`) says its own width at the row's
(`width_in`). `side_grows` is the older rule, Picking's until it is redesigned:
the view at the row's whole height, the panel taking the rest, at least the
width it was designed for and at most `MAX_SIDE_FACTOR` times that.

Placed by hand in `resizeEvent` rather than by a layout: the split depends on
the height, and no box layout asks its children for a width given a height.
"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QWidget

from ..theme import SPACING
from .camera_view import DEFAULT_ASPECT, CameraView

__all__ = ["FeedRow"]

MAX_SIDE_FACTOR = 2.0


class FeedRow(QWidget):
    def __init__(self, view: CameraView, side: QWidget,
                 parent: QWidget | None = None, *, spacing: int = SPACING,
                 side_grows: bool = False):
        super().__init__(parent)
        self.view, self.side = view, side
        self._spacing = spacing
        self._side_grows = side_grows
        view.setParent(self)
        side.setParent(self)
        # The width the panel was built for is its minimum here. Only a panel
        # that grows goes by what is in it: a scroll column's hint is that, not
        # the width it was fixed at.
        self._min_side = (max(side.minimumWidth(), side.sizeHint().width())
                          if side_grows
                          else side.minimumWidth() or side.sizeHint().width())
        self._max_side = int(self._min_side * MAX_SIDE_FACTOR)

    def _place(self) -> None:
        width, height = self.width(), self.height()
        if width <= 0 or height <= 0:
            return
        if hasattr(self.side, "width_in"):
            side = self.side.width_in(width)
        elif self._side_grows:
            wanted_view = int(round(height * DEFAULT_ASPECT))
            side = width - self._spacing - wanted_view
            side = max(self._min_side, min(self._max_side, side))
        else:
            side = self._min_side
        if hasattr(self.side, "set_outer_width"):
            self.side.set_outer_width(side)
        else:
            self.side.setFixedWidth(side)
        room = max(0, width - self._spacing - side)
        if self._side_grows:
            view, view_height, free = room, height, 0
        else:
            view = min(room, int(round(height * DEFAULT_ASPECT)))
            view_height = min(height, int(round(view / DEFAULT_ASPECT)))
            free = room - view
        bar = getattr(self.side, "bar_room", 0)
        left = view + self._spacing + min(free, (free + bar) // 2)
        self.view.setGeometry(0, 0, view, view_height)
        self.side.setGeometry(left, 0, side, height)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._place()

    def minimumSizeHint(self) -> QSize:
        view = self.view.minimumSize()
        return QSize(view.width() + self._spacing + self._min_side,
                     view.height())

    def sizeHint(self) -> QSize:
        return QSize(int(700 * DEFAULT_ASPECT) + self._spacing + self._min_side,
                     700)
