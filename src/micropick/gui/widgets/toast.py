"""The response to an action (REDESIGN §3, Toast; §1.1).

One sentence at the window's bottom left, on every screen, over the Footer
- over a run's status while it shows. Two kinds: an answer, green, 3 s
("Tip A3 is on."); a refusal, red, 6 s ("Robot busy."): the operator was
looking at the picture, not here, and a refusal is the one to read. A long
sentence wraps upward, over the page.

It floats over whatever is shown, sheets included, moves nothing and takes
no clicks, so it never covers a button the user is reaching for. A new
toast replaces the one showing, with its own time.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QLabel, QWidget

__all__ = ["Toast", "show_toast", "toast_of"]

ANSWER_MS = 3000
REFUSAL_MS = 6000
# From the window's left edge, and from its bottom: one line stands in the
# middle of the 60 px footer.
LEFT_GAP = 16
BOTTOM_GAP = 9


class Toast(QLabel):
    def __init__(self, host: QWidget):
        super().__init__(host)
        self.setObjectName("toast")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setProperty("tone", "answer")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        host.installEventFilter(self)
        self.hide()

    def show_text(self, text: str, *, refusal: bool = False) -> None:
        tone = "refusal" if refusal else "answer"
        if self.property("tone") != tone:
            self.setProperty("tone", tone)
            self.style().unpolish(self)
            self.style().polish(self)
        self.setText(text)
        self._place()
        self.show()
        self.raise_()
        self._timer.start(REFUSAL_MS if refusal else ANSWER_MS)

    def _place(self) -> None:
        """On one line while it fits in three fifths of the window, clear
        of the Footer's buttons; else wrapped, and taller."""
        host = self.parentWidget()
        self.setWordWrap(False)
        width = min(self.sizeHint().width(), host.width() * 3 // 5)
        self.setWordWrap(True)
        height = self.heightForWidth(width)
        self.setGeometry(LEFT_GAP, host.height() - height - BOTTOM_GAP,
                         width, height)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._place()
        return False


def toast_of(widget: QWidget) -> Toast:
    """The one toast of `widget`'s window."""
    window = widget.window()
    toast = window.findChild(Toast, options=Qt.FindChildOption
                             .FindDirectChildrenOnly)
    return Toast(window) if toast is None else toast


def show_toast(widget: QWidget, text: str, *, refusal: bool = False) -> None:
    """A toast over `widget`'s window: the one way a page answers, or, with
    `refusal`, says why it did nothing."""
    toast_of(widget).show_text(text, refusal=refusal)
