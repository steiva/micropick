"""A camera feed in a window of its own.

Opened from the camera buttons on the status bar, so a feed can be looked at
from any page — the Profile page opens a camera and had nowhere to show it.
The window owns nothing: the camera belongs to the session and stays open
when the window closes, and closing is hiding, so the next click brings the
same window back where it was. The view inside is the same `CameraView` as
on the pages, zoom, crosshair toggle and focus slider included.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from .camera_view import CameraView

__all__ = ["FeedWindow"]


class FeedWindow(QWidget):
    def __init__(self, label: str, parent: QWidget | None = None):
        # A top-level window with the main window as parent: it stays above
        # it, minimises with it and is destroyed with it, and takes no
        # place of its own in the taskbar.
        super().__init__(parent, Qt.WindowType.Window)
        self.label = label
        self.setWindowTitle(f"camera: {label}")
        self.resize(800, 600)
        self.view = CameraView(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

    def show_camera(self, camera) -> None:
        self.view.set_camera(camera)
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        """Hide rather than destroy: the view stops reading when hidden, and
        the window keeps its size and place for the next time."""
        event.ignore()
        self.hide()
