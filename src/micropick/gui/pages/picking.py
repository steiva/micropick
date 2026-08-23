"""Running the picking session."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..session import Session
from ..theme import SPACING
from ..theme.factory import heading

__all__ = ["PickingPage"]

TITLE = "Picking"


class PickingPage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2, SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addWidget(heading(TITLE, 1))
        note = QLabel("Not implemented yet. The pick-and-place run comes in a "
                      "later pass.")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
