"""Something is happening: a moving bar and a line, at the left of the
status bar, on every page.

Everything slow in this application runs on a `Worker` - a calibration, a
camera opening, the robot's connect, a move across the deck - and the page
that started it only greys its buttons. A pipette calibration is minutes of
that with nothing on screen moving. This watches every running worker and,
once one has run for `SHOW_AFTER_S`, shows it: what it is doing (the worker's
`what`) and the last line it logged, with the bar filling when the job
reports progress and sweeping when it does not.

Polled, not signalled: a worker's signals belong to whoever started it, and
the list it is on (`workers.activity`) is the one place that sees them all.
The delay keeps a jog step or a status read - a tenth of a second each -
from blinking the bar on every key press.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QProgressBar, QSizePolicy,
                               QWidget)

from ..theme import SPACING
from ..workers import activity

__all__ = ["ActivityIndicator", "SHOW_AFTER_S", "describe"]

SHOW_AFTER_S = 0.4
POLL_MS = 150
BAR_WIDTH = 110


def describe(workers) -> str:
    """The line beside the bar: the oldest job, its last word, the others
    counted."""
    if not workers:
        return ""
    first = workers[0]
    text = first.what or "working"
    if first.last_message and first.last_message != first.what:
        text += f" - {first.last_message}"
    if len(workers) > 1:
        text += f"  (+{len(workers) - 1} more)"
    return text


class ActivityIndicator(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(SPACING, 0, SPACING, 0)
        row.setSpacing(SPACING)
        self.bar = QProgressBar(self)
        self.bar.setFixedWidth(BAR_WIDTH)
        self.bar.setMaximumHeight(10)
        self.bar.setTextVisible(False)
        self.label = QLabel(self)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        # Whatever room the status bar has left, never more: a long line must
        # not widen the window.
        self.label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                 QSizePolicy.Policy.Preferred)
        row.addWidget(self.bar)
        row.addWidget(self.label, 1)
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.poll)
        self._timer.start()
        self._show([])

    def poll(self) -> None:
        now = time.monotonic()
        self._show([worker for worker in activity()
                    if worker.started_at is not None
                    and now - worker.started_at >= SHOW_AFTER_S])

    def _show(self, workers) -> None:
        self.bar.setVisible(bool(workers))
        self.label.setVisible(bool(workers))
        if not workers:
            return
        progress = workers[0].last_progress
        if progress is not None and progress[1] > 0:
            self.bar.setRange(0, progress[1])
            self.bar.setValue(min(progress[0], progress[1]))
        else:
            self.bar.setRange(0, 0)             # sweeping: no total known
        text = describe(workers)
        # Elided to the room there is; the whole line is in the tooltip.
        width = max(80, self.label.width())
        self.label.setText(self.label.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideRight, width))
        self.label.setToolTip(text)
