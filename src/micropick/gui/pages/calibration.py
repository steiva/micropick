"""The calibration sweeps."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..theme import SPACING
from ..theme.factory import heading

__all__ = ["CalibrationPage"]

TITLE = "Calibration"


class CalibrationPage(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2, SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addWidget(heading(TITLE, 1))
        note = QLabel("Not implemented yet. The camera sweep and the pipette "
                      "tip calibration come in a later pass.")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
