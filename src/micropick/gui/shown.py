"""A page's answer to a session signal, kept for when the page is seen.

Every page is built once and lives in the window's stack, hidden while
another is shown, and each one listens to the session. A change of the
robot's lease - twice per jog step - refreshed every page, hidden ones
included: the Tip step and Well center laid out their columns and read
labware definitions from disk for nobody, 7.6 ms of the GUI thread inside
each claim of the robot (GUI-10).

`when_shown(widget, fn)` is the slot to connect instead of `fn`: it calls
`fn` at once while `widget` is visible, and otherwise once, when it is next
shown. Only for what a page shows. A run's own state, Stop, and anything
another part of the program reads from the page stay connected directly.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QWidget

__all__ = ["when_shown"]


class _OnShow(QObject):
    """Runs `fn` on the widget's next Show, if a signal came while it was
    hidden."""

    def __init__(self, widget: QWidget, fn):
        super().__init__(widget)
        self._widget = widget
        self._fn = fn
        self.pending = False
        widget.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if (watched is self._widget and event.type() == QEvent.Type.Show
                and self.pending):
            self.pending = False
            self._fn()
        return False

    def slot(self, *_args) -> None:
        if self._widget.isVisible():
            self.pending = False
            self._fn()
        else:
            self.pending = True


def when_shown(widget: QWidget, fn):
    """A slot that calls `fn()` now if `widget` is visible, else on its next
    Show. The signal's arguments are dropped: `fn` reads the state itself."""
    return _OnShow(widget, fn).slot
