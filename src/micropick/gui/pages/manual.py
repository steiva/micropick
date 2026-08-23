"""Driving the robot by hand."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..session import Session
from ..theme import SPACING
from ..theme.factory import heading

__all__ = ["ManualPage"]

TITLE = "Manual control"


class ManualPage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2, SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addWidget(heading(TITLE, 1))
        note = QLabel("Not implemented yet. This becomes a camera view and a "
                      "jog panel over workflows.jog.JogController.")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
