"""The application's log.

Not a placeholder: this is where `logging` is pointed in the next commit, and
the widget it writes into has to be bounded before anything starts writing to
it. A `QPlainTextEdit` that grows without limit is the same failure as the
unbounded recorder buffer in `hardware/camera.py` — slower to arrive, since a
line is not a 5 MB frame, but a session that runs all day gets there.

`setMaximumBlockCount` is Qt's own ring buffer: it drops from the top as lines
arrive and costs nothing per line, which matters because the writer is the
logging handler and must not become the slow part of a jog step.
"""

from __future__ import annotations

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit,
                               QVBoxLayout, QWidget)

from ..report import save_report_asking
from ..session import Session
from ..theme import SPACING
from ..theme.factory import secondary_button

__all__ = ["LogPage"]

TITLE = "Log"

# Enough to cover a calibration sweep and its fit report with room to scroll
# back, and small enough that the widget never becomes the memory story.
MAX_LINES = 5000


class LogPage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        # Taken for the uniform page constructor; the log is fed by the
        # logging handler rather than by asking the session for anything.
        self.session = session

        self.view = QPlainTextEdit(self)
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(MAX_LINES)
        # No wrapping: log lines are aligned columns of numbers, and a wrapped
        # one costs the alignment of every line after it on the screen.
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))

        self.clear_button = secondary_button("Clear", self)
        self.clear_button.clicked.connect(self.view.clear)
        # One file with the logs, the profile and the state, to send to
        # whoever can help (`gui.report`).
        self.report_button = secondary_button("Save report for help…", self)
        self.report_button.clicked.connect(
            lambda: save_report_asking(self.session, self))
        where = QLabel("The log is also saved to a file in the Logs folder "
                       "(Settings).")

        header = QHBoxLayout()
        header.addWidget(where)
        header.addStretch(1)
        header.addWidget(self.report_button)
        header.addWidget(self.clear_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2, SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addLayout(header)
        layout.addWidget(self.view, 1)

    def append(self, line: str) -> None:
        """Add one line and follow it, unless the operator has scrolled up.

        Following unconditionally is what makes a log unreadable while anything
        is happening: the moment the operator scrolls back to look at the line
        that mattered, the next record drags them to the bottom again.
        """
        bar = self.view.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        self.view.appendPlainText(line)
        if at_end:
            bar.setValue(bar.maximum())
