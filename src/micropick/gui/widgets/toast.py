"""The response to an action (REDESIGN §3, Toast; §1.1).

Green, bottom center of the window, about 3 s, one sentence: "Plan
ready", "Tip A3 is on.". It floats over whatever page is shown and takes
no clicks, so it never covers a button the user is reaching for. A new
toast replaces the one showing.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QLabel, QWidget

__all__ = ["Toast", "show_toast"]

DURATION_MS = 3000
# Above the footer, which is 60 px.
BOTTOM_GAP = 84


class Toast(QLabel):
    def __init__(self, host: QWidget):
        super().__init__(host)
        self.setObjectName("toast")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        host.installEventFilter(self)
        self.hide()

    def show_text(self, text: str, ms: int = DURATION_MS) -> None:
        self.setText(text)
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        self._timer.start(ms)

    def _place(self) -> None:
        host = self.parentWidget()
        self.move((host.width() - self.width()) // 2,
                  host.height() - self.height() - BOTTOM_GAP)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._place()
        return False


def show_toast(widget: QWidget, text: str) -> None:
    """A toast over `widget`'s window: the one way a page answers."""
    window = widget.window()
    toast = window.findChild(Toast, options=Qt.FindChildOption
                             .FindDirectChildrenOnly)
    if toast is None:
        toast = Toast(window)
    toast.show_text(text)
