"""Choosing a profile, connecting the robot, opening cameras."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..theme import SPACING
from ..theme.factory import heading

__all__ = ["ProfilePage"]

TITLE = "Profile"


class ProfilePage(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2, SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addWidget(heading(TITLE, 1))
        note = QLabel("Not implemented yet. Loading a profile, connecting the "
                      "robot and opening cameras arrive with the session.")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
