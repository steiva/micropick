"""Done: a green check at the end of a procedure that has one.

A calibration, a picking run, a liquid handling program: each has a moment
it is finished, and that moment used to be a line of text among others -
"fit complete", "run completed" - that read the same as every line before
it. This says it the way a person would want to hear it: a green check,
"Done", and one line of what was done. Shown only for an ending that went
well; a stop, a failure or a result that has to be redone gets its own
words instead, and the next start takes the check away.

The green is fixed, as the shell's amber for a tip is: it has to read as
"finished, and fine" in either theme, which the palette cannot promise.
"""

from __future__ import annotations

import qtawesome as qta
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

__all__ = ["DoneBanner", "DONE_GREEN"]

DONE_GREEN = "#4caf50"
ICON_PX = 28


class DoneBanner(QWidget):
    """Hidden until `show_done`; `clear` hides it again."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.icon = QLabel(self)
        self.icon.setPixmap(qta.icon("mdi6.check-circle", color=DONE_GREEN)
                            .pixmap(ICON_PX, ICON_PX))
        self.title = QLabel(f"<b style='color:{DONE_GREEN}'>Done</b>", self)
        self.detail = QLabel(self)
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)

        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(0)
        words.addWidget(self.title)
        words.addWidget(self.detail)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 4, 0, 4)
        row.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        row.addLayout(words, 1)
        self.hide()

    @property
    def done(self) -> bool:
        return not self.isHidden()

    def show_done(self, detail: str = "") -> None:
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        self.show()

    def clear(self) -> None:
        self.hide()
