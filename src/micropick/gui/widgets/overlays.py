"""What covers the page: a sheet, a confirmation, the lock (REDESIGN §3).

All three are children of the window that cover it, not separate windows:
the breadcrumb and the status above them stay where the user looks, a
second window cannot get lost behind the first, and a test can find them
among the window's children.

**SheetWindow.** A near-full-screen panel over the dimmed page (24 px
margin) with a title, × (close without saving), and Cancel and Done (save)
at its bottom right. Every Robot setup item opens in one; the breadcrumb
gains its title (the shell's part).

**ConfirmDialog.** Small, centered, the safe choice primary: "Reset the
setup?" with **Keep setup** and **Reset** in `danger-text`. `ask()` waits
for the answer in a local event loop, as a modal dialog would.

**LockOverlay.** While Lock is on: the page under 82 % `bg`, a big lock,
"Robot locked", "Nothing will move. Work with your hands safely." and
Unlock. It only shows the state; the session refuses the commands.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QEventLoop, QObject, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QToolButton,
                               QVBoxLayout, QWidget)

from ..theme import COLORS, SPACING
from ..theme.factory import (danger_text_button, heading, muted_label,
                             primary_button, secondary_button)
from ..theme.icons import icon, pixmap

__all__ = ["ConfirmDialog", "DimCover", "LockOverlay", "SheetWindow"]

SHEET_MARGIN = 24


class _Cover(QWidget):
    """A child that keeps the size of its parent and dims what is under it."""

    def __init__(self, host: QWidget, alpha: float):
        super().__init__(host)
        self._shade = QColor(COLORS["bg"])
        self._shade.setAlphaF(alpha)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        host.installEventFilter(self)
        self.setGeometry(host.rect())
        self.hide()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self.setGeometry(obj.rect())
        return False

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._shade)
        painter.end()

    def open(self) -> None:
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()


class DimCover(_Cover):
    """A step whose needs are not met, shown dimmed (REDESIGN §4): what is
    under it is seen and takes no click. The shell disables the page too,
    so its keys do nothing either."""

    def __init__(self, host: QWidget):
        super().__init__(host, 0.6)


class SheetWindow(_Cover):
    """`done` on Done, `canceled` on Cancel or ×. Either closes it.

    Contents go into `body` (a vertical layout). Done can be held off with
    `done_button.setEnabled(False)` until there is something to save.
    """

    done = Signal()
    canceled = Signal()

    def __init__(self, host: QWidget, title: str, *, done_text: str = "Done",
                 buttons: bool = True):
        """`buttons` False: no Cancel and Done, for a sheet that saves with
        buttons of its own (Settings); × still closes it."""
        super().__init__(host, 0.72)
        self.title = title
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHEET_MARGIN, SHEET_MARGIN, SHEET_MARGIN,
                                 SHEET_MARGIN)
        self.frame = QFrame(self)
        self.frame.setObjectName("sheet")
        outer.addWidget(self.frame)
        column = QVBoxLayout(self.frame)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        header = QFrame(self.frame)
        header.setObjectName("sheetHeader")
        header.setFixedHeight(56)
        row = QHBoxLayout(header)
        row.setContentsMargins(24, 0, 12, 0)
        self.title_label = heading(title, 3, header)
        row.addWidget(self.title_label)
        row.addStretch(1)
        self.close_button = close = QToolButton(header)
        close.setObjectName("barButton")
        close.setIcon(icon("close", COLORS["text-2"], 18))
        close.setToolTip("Close without saving")
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.clicked.connect(self._cancel)
        row.addWidget(close)
        column.addWidget(header)

        self.body_widget = QWidget(self.frame)
        self.body_widget.setObjectName("plain")
        self.body = QVBoxLayout(self.body_widget)
        self.body.setContentsMargins(24, 20, 24, 12)
        self.body.setSpacing(SPACING * 2)
        column.addWidget(self.body_widget, 1)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(24, 8, 24, 20)
        button_row.setSpacing(10)
        button_row.addStretch(1)
        self.cancel_button = secondary_button("Cancel", self.frame)
        self.cancel_button.setMinimumWidth(96)
        self.cancel_button.clicked.connect(self._cancel)
        self.done_button = primary_button(done_text, self.frame)
        self.done_button.setMinimumWidth(110)
        self.done_button.clicked.connect(self._done)
        button_row.addWidget(self.cancel_button)
        button_row.addWidget(self.done_button)
        column.addLayout(button_row)
        if not buttons:
            self.cancel_button.hide()
            self.done_button.hide()
            button_row.setContentsMargins(0, 0, 0, 0)

    def _cancel(self) -> None:
        self.hide()
        self.canceled.emit()

    def _done(self) -> None:
        self.hide()
        self.done.emit()

    def keyPressEvent(self, event) -> None:
        # Esc is the window-wide Stop (CLAUDE.md); a sheet does not take it.
        event.ignore()


class ConfirmDialog(_Cover):
    """A question with a safe and a destructive answer."""

    answered = Signal(bool)   # True: the destructive choice was taken

    def __init__(self, host: QWidget, question: str, *, text: str = "",
                 safe: str = "Cancel", destructive: str = "OK"):
        super().__init__(host, 0.72)
        outer = QVBoxLayout(self)
        outer.addStretch(1)
        frame = QFrame(self)
        frame.setObjectName("dialog")
        frame.setFixedWidth(420)
        column = QVBoxLayout(frame)
        column.setContentsMargins(24, 22, 24, 20)
        column.setSpacing(SPACING)
        column.addWidget(heading(question, 3, frame))
        if text:
            column.addWidget(muted_label(text, frame))
        column.addSpacing(SPACING)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        self.destructive = danger_text_button(destructive, frame)
        self.safe = primary_button(safe, frame)
        self.destructive.clicked.connect(lambda: self._answer(True))
        self.safe.clicked.connect(lambda: self._answer(False))
        row.addWidget(self.destructive)
        row.addWidget(self.safe)
        column.addLayout(row)
        middle = QHBoxLayout()
        middle.addStretch(1)
        middle.addWidget(frame)
        middle.addStretch(1)
        outer.addLayout(middle)
        outer.addStretch(1)
        self._loop: QEventLoop | None = None
        self._result = False

    def _answer(self, result: bool) -> None:
        self._result = result
        self.hide()
        self.answered.emit(result)
        if self._loop is not None:
            self._loop.quit()

    def ask(self) -> bool:
        """Show and wait. True when the destructive choice was taken."""
        self.open()
        self.safe.setFocus()
        self._loop = QEventLoop(self)
        self._loop.exec()
        self._loop = None
        self.deleteLater()
        return self._result

    @classmethod
    def confirm(cls, host: QWidget, question: str, **kwargs) -> bool:
        return cls(host.window(), question, **kwargs).ask()


class LockOverlay(_Cover):
    unlock_clicked = Signal()

    def __init__(self, host: QWidget):
        super().__init__(host, 0.82)
        column = QVBoxLayout(self)
        column.addStretch(1)
        lock = QLabel(self)
        lock.setPixmap(pixmap("lock", COLORS["text"], 64, fill=True,
                              stroke=1.5))
        lock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = heading("Robot locked", 1, self)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        line = QLabel("Nothing will move. Work with your hands safely.", self)
        line.setObjectName("muted")
        line.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.unlock = primary_button("Unlock", self)
        self.unlock.setMinimumWidth(140)
        self.unlock.clicked.connect(self.unlock_clicked)
        for widget in (lock, title, line):
            column.addWidget(widget)
        column.addSpacing(SPACING * 2)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.unlock)
        row.addStretch(1)
        column.addLayout(row)
        column.addStretch(1)

    def set_locked(self, locked: bool) -> None:
        if locked:
            self.open()
        else:
            self.hide()

    def raise_over(self, *_args) -> None:
        """Keep above other covers (a sheet opened while locked)."""
        if self.isVisible():
            self.raise_()
