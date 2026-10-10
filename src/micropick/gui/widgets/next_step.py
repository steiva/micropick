"""The next step of the Operation checklist, floating over the page.

A small card at the top centre of every page but Profile (which shows the
whole list), under the tab row: the first step that can be done now, and a
button to the page where it is done. It is dragged by any part of it but
its buttons, and stays where it was put, on every page, until the program
is closed; the window keeps it inside itself (`place`). Closed with its cross, it stays closed until the next
step is another one - a hint, not a nag: it comes back only with something
new to say. With every step done it says the bench is ready, once.

It owns nothing: the window gives it the checklist (`show_checks`) and the
page on screen, and its button asks for a page (`go`) as the checklist's do.
"""

from __future__ import annotations

import qtawesome as qta
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QToolButton, QVBoxLayout, QWidget)

from ..theme import SPACING, accent
from .checklist import TODO, Check

__all__ = ["NextStepHint"]

WIDTH = 340
ICON_PX = 20
READY = "Ready for a picking run"


class NextStepHint(QFrame):
    go = Signal(str)                       # a page name

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setFrameShape(QFrame.Shape.Panel)
        self.setFixedWidth(WIDTH)
        self._shown_for: str | None = None     # the step's text, or READY
        self._closed_for: str | None = None
        self._page: str | None = None
        # Where the operator put it, in the parent's coordinates; None for
        # the window's default (`place`). `_grab` is the press point in the
        # card while it is dragged.
        self.moved_to: QPoint | None = None
        self._grab: QPoint | None = None
        # The highest it may go: the window keeps it off the tab row.
        self.top_limit = 0
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        self.icon = QLabel(self)
        self.title = QLabel("Next step", self)
        self.title.setObjectName("heading3")
        self.text = QLabel(self)
        self.text.setWordWrap(True)
        self.link = QPushButton(self)
        self.link.setFlat(True)
        self.link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.link.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.link.clicked.connect(lambda: self.go.emit(self._page or ""))
        self.close_button = QToolButton(self)
        self.close_button.setAutoRaise(True)
        self.close_button.setIcon(qta.icon("mdi6.close"))
        self.close_button.setToolTip("Hide until the next step.")
        self.close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_button.setCursor(Qt.CursorShape.ArrowCursor)
        self.close_button.clicked.connect(self._close)

        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(2)
        words.addWidget(self.title)
        words.addWidget(self.text)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(self.link)
        words.addLayout(row)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING, SPACING, SPACING)
        layout.setSpacing(SPACING)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(words, 1)
        layout.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)
        self._ink()
        self.hide()

    def _ink(self) -> None:
        """The accent: on the outline and the bulb, so the card reads as a
        hint and not as part of the page under it."""
        colour = accent()
        self.icon.setPixmap(qta.icon("mdi6.lightbulb-on-outline",
                                     color=colour).pixmap(ICON_PX, ICON_PX))
        self.setStyleSheet(f"QFrame#card {{ border: 1px solid {colour}; }}")

    def changeEvent(self, event) -> None:
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Type.StyleChange:
            self._ink()
        super().changeEvent(event)

    def show_checks(self, checks: list[Check], page: str | None,
                    allowed: bool = True) -> None:
        """The first step to do now, for the page `page` on screen; hidden
        where `allowed` is False, or once closed for this step."""
        step = next((c for c in checks if c.state == TODO
                     and not c.optional), None)
        if step is None and any(c.blocking for c in checks):
            step = None                            # waiting: nothing to do now
            key = None
        else:
            key = step.text if step is not None else READY
        if key is None or not allowed or key == self._closed_for:
            self.hide()
            return
        if key != self._shown_for:
            self._shown_for = key
            if step is None:
                self.text.setText(READY + ".")
                self._page, place = "picking", "Picking"
            else:
                self.text.setText(step.text)
                self._page, place = step.page, step.place
            self.link.setText(f"{place.replace('&', '&&')} ›" if place
                              else "")
        # No way to the page that is already on screen.
        self.link.setVisible(bool(self._page) and self._page != page)
        self.adjustSize()
        self.show()
        self.raise_()

    # -- moved with the mouse -------------------------------------------------

    def place(self, default: QPoint) -> None:
        """At `default`, or where it was dragged to; inside the parent and
        under `top_limit` either way, so a smaller window keeps it."""
        self.move(self._inside(self.moved_to if self.moved_to is not None
                               else default))

    def _inside(self, point: QPoint) -> QPoint:
        parent = self.parentWidget()
        if parent is None:
            return point
        x = max(0, min(point.x(), parent.width() - self.width()))
        y = max(self.top_limit, min(point.y(),
                                    parent.height() - self.height()))
        return QPoint(x, y)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._grab = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._grab is not None:
            where = self.mapToParent(event.position().toPoint()) - self._grab
            self.moved_to = self._inside(where)
            self.move(self.moved_to)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._grab is not None and \
                event.button() == Qt.MouseButton.LeftButton:
            self._grab = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _close(self) -> None:
        self._closed_for = self._shown_for
        self.hide()

    def reopen(self) -> None:
        """Forget that it was closed: the status bar's question mark."""
        self._closed_for = None
        self._shown_for = None
