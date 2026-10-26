"""The bar along the bottom of a task screen (REDESIGN §3, Footer).

Right-aligned **Back** (outlined) and **Next** (primary, the word only).
Next is enabled when the step is done or optional. No message text: the
response to an action is a toast (§7.6).

On a run's screen (Picking, Liquid Run) the footer carries the run's
status instead (§6.5): a colored CAPS tag - READY, PICKING, PAUSED,
WAITING FOR YOU, DONE, NOT READY - and one sentence of what the robot is
doing, with the run's own buttons on the right (`set_buttons`).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from ..theme import COLORS, SIZES
from ..theme.factory import primary_button, secondary_button
from ..theme.icons import icon

__all__ = ["STATUS_TONES", "Footer"]

# Tag background and ink per tone.
STATUS_TONES = {
    "ready": ("selected-bg", "accent"),
    "busy": ("accent-bg", "accent"),
    "warn": ("warn-bg", "warn"),
    "ok": ("ok-bg", "ok"),
    "off": ("hover", "muted"),
}


class Footer(QFrame):
    back_clicked = Signal()
    next_clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("footer")
        self.setFixedHeight(SIZES["footer"])
        row = QHBoxLayout(self)
        row.setContentsMargins(20, 0, 20, 0)
        row.setSpacing(12)

        self.tag = QLabel(self)
        self.tag.setObjectName("statusTag")
        self.sentence = QLabel(self)
        self.sentence.setObjectName("statusSentence")
        self.sentence.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self.tag, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.sentence)
        row.addStretch(1)

        self._buttons = QHBoxLayout()
        self._buttons.setSpacing(10)
        row.addLayout(self._buttons)

        self.back = secondary_button("Back", self)
        self.back.setMinimumWidth(96)
        self.back.clicked.connect(self.back_clicked)
        self.next = primary_button("Next", self)
        self.next.setMinimumWidth(120)
        self.next.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.next.setIcon(icon("next", COLORS["on-accent"], 16,
                               disabled=COLORS["faint"]))
        self.next.clicked.connect(self.next_clicked)
        self.clear_status()
        self.set_buttons([self.back, self.next])

    def set_buttons(self, buttons: list[QWidget]) -> None:
        """What sits on the right, in order. Back and Next are kept for
        reuse when they are taken out."""
        while self._buttons.count():
            widget = self._buttons.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.setParent(self)
        for widget in buttons:
            self._buttons.addWidget(widget)
            widget.show()

    def set_navigation(self, *, back: bool = True, next: bool = True,
                       next_enabled: bool = True) -> None:
        """Back and Next, as an ordinary step has them."""
        self.set_buttons([b for b, shown in ((self.back, back),
                                             (self.next, next)) if shown])
        self.next.setEnabled(next_enabled)

    def set_status(self, tag: str, sentence: str = "", *,
                   tone: str = "ready") -> None:
        bg, ink = STATUS_TONES[tone]
        self.tag.setText(tag.upper())
        self.tag.setStyleSheet(f"background: {COLORS[bg]}; "
                               f"color: {COLORS[ink]};")
        self.tag.show()
        self.sentence.setText(sentence)
        self.sentence.show()
        self.tone = tone

    def clear_status(self) -> None:
        self.tag.hide()
        self.sentence.hide()
        self.tone = None
