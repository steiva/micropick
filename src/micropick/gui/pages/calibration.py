"""The two calibrations, as two tabs of one page.

The camera sweep and the pipette offset are different measurements with a
fixed order between them: the offset is measured through the pixel map the
sweep produces, so the camera comes first and the tab order says so. Each tab
is a widget of its own with its own steps, and this page is only the frame
they sit in. Each holds a jog panel, and both cannot be on screen at once:
the tab widget hides the other, which is what keeps their shortcuts apart.
"""

from __future__ import annotations

from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from ..session import Session
from ..theme import SPACING
from ..theme.factory import heading
from . import calibration_camera, calibration_pipette

__all__ = ["CalibrationPage"]

TITLE = "Calibration"


class CalibrationPage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session

        self.camera = calibration_camera.CameraCalibration(session, self)
        self.pipette = calibration_pipette.PipetteCalibration(session, self)

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self.camera, calibration_camera.TITLE)
        self.tabs.addTab(self.pipette, calibration_pipette.TITLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2, SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addWidget(heading(TITLE, 1))
        layout.addWidget(self.tabs, 1)
