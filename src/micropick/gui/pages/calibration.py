"""The calibrations, as tabs of one page: the camera, then the pipette.

The camera sweep and the pipette offset are different measurements with a
fixed order between them: the offset is measured through the pixel map the
sweep produces, so the camera comes first and the tab order says so. The
check on both together - the tip driven to a crosshair the operator clicks
- is on Manual control, where every other move by hand already is.

Each tab is a widget of its own with its own steps, and this page is only
the frame they sit in. Both hold a jog panel, and both cannot be on screen
at once: the tab widget hides the other, which is what keeps their
shortcuts apart.

The tab widget's own bar is hidden. In its place are two choices, one
centred in each half of the page, each with a line on when it is due: the
question at this page is which of the two needs doing, and a tab title in
the corner does not answer it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QPushButton,
                               QTabWidget, QVBoxLayout, QWidget)

from ..session import Session
from ..theme import SPACING
from . import calibration_camera, calibration_pipette

__all__ = ["CalibrationPage"]

TITLE = "Calibration"

# When each is due, under its choice.
WHEN = {
    calibration_camera.TITLE: "On a new system, or after the camera was "
                              "moved, refocused or set to another resolution.",
    calibration_pipette.TITLE: "After each pipette change, or if it was "
                               "bumped.",
}
CHOICE_WIDTH = 300


class CalibrationPage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session

        self.camera = calibration_camera.CameraCalibration(session, self)
        self.pipette = calibration_pipette.PipetteCalibration(session, self)

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self.camera, calibration_camera.TITLE)
        self.tabs.addTab(self.pipette, calibration_pipette.TITLE)
        self.tabs.tabBar().hide()

        # Stretch 1 : choice : 2 : choice : 1, the choices of one width: each
        # is centred in its half of the page.
        self.choices = QButtonGroup(self)
        self.choices.setExclusive(True)
        row = QHBoxLayout()
        row.addStretch(1)
        for index in range(self.tabs.count()):
            if index:
                row.addStretch(2)
            row.addWidget(self._choice(index))
        row.addStretch(1)
        self.choices.idClicked.connect(self.tabs.setCurrentIndex)
        self.choices.button(0).setChecked(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2, SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addLayout(row)
        layout.addWidget(self.tabs, 1)

    def _choice(self, index: int) -> QWidget:
        """A tab's title as a button, and when it is due under it."""
        title = self.tabs.tabText(index)
        box = QWidget(self)
        box.setFixedWidth(CHOICE_WIDTH)
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING // 2)
        button = QPushButton(title, box)
        button.setCheckable(True)
        # The keys over these pages are the robot's.
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.choices.addButton(button, index)
        column.addWidget(button)
        when = QLabel(WHEN.get(title, ""), box)
        when.setWordWrap(True)
        when.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(when)
        return box
