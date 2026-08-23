"""Manual control: a camera on the left, the jog panel on the right.

Everything that drives the robot lives in `widgets/jog_panel.py`, because the
first step of the camera calibration needs the same controls. What is left here
is the arrangement and the choice of which open camera to watch.
"""

from __future__ import annotations

from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QVBoxLayout,
                               QWidget)

from ..session import Session
from ..theme import SPACING
from ..theme.factory import card, heading
from ..widgets.camera_view import CameraView
from ..widgets.jog_panel import JogPanel

__all__ = ["ManualPage"]

TITLE = "Manual control"

PANEL_WIDTH = 400


class ManualPage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session

        self.view = CameraView(self)
        # The host is this page, so the arrow keys work while the eye and the
        # focus are on the picture rather than on the buttons.
        self.jog = JogPanel(session, shortcut_host=self, parent=self)

        panel = QWidget(self)
        panel.setFixedWidth(PANEL_WIDTH)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        column.addWidget(self._camera_card())
        column.addWidget(self.jog, 1)

        body = QHBoxLayout()
        body.setSpacing(SPACING)
        body.addWidget(self.view, 1)
        body.addWidget(panel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2, SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addWidget(heading(TITLE, 1))
        layout.addLayout(body, 1)

        session.camera_opened.connect(self._refresh_cameras)
        session.camera_closed.connect(self._refresh_cameras)
        self._refresh_cameras()

    def _camera_card(self) -> QWidget:
        box = card(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Camera"))
        self.camera_choice = QComboBox(self)
        self.camera_choice.currentTextChanged.connect(self._show_camera)
        row.addWidget(self.camera_choice, 1)
        box.layout().addLayout(row)
        return box

    def _refresh_cameras(self, _label: str = "") -> None:
        labels = self.session.open_cameras
        current = self.camera_choice.currentText()
        self.camera_choice.blockSignals(True)
        self.camera_choice.clear()
        self.camera_choice.addItems(labels)
        if current in labels:
            self.camera_choice.setCurrentIndex(labels.index(current))
        self.camera_choice.blockSignals(False)
        self._show_camera(self.camera_choice.currentText())

    def _show_camera(self, label: str) -> None:
        self.view.set_camera(self.session.camera(label) if label else None)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh_cameras()
