"""A camera feed in a window of its own.

Opened from the camera buttons on the status bar, so a feed can be looked at
from any page; the same button hides it again. The window owns nothing: the
camera belongs to the session and stays open when the window closes, and
closing is hiding, so the next click brings the same window back where it
was. The view inside is the same `CameraView` as on the pages, zoom and
crosshair toggle included, and it is where a camera is tuned: only here does
the view carry its focus tools (`CameraView.enable_focus_tools`), with "save
to profile" written through `save_controls`.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from .camera_view import CameraView

__all__ = ["FeedWindow"]


class FeedWindow(QWidget):
    def __init__(self, label: str, parent: QWidget | None = None, *,
                 save_controls: Callable[[str], dict] | None = None):
        # A top-level window with the main window as parent: it stays above
        # it, minimises with it and is destroyed with it, and takes no
        # place of its own in the taskbar.
        super().__init__(parent, Qt.WindowType.Window)
        self.label = label
        self._save_controls = save_controls
        self.setWindowTitle(f"camera: {label}")
        self.resize(800, 600)
        self.view = CameraView(self)
        self.view.enable_focus_tools(save_controls is not None)
        self.view.focus_save_requested.connect(self._save)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

    def show_camera(self, camera) -> None:
        self.view.set_camera(camera)
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    @property
    def shown(self) -> bool:
        """On screen: visible and not minimised. A click on its button then
        hides it rather than raising it."""
        return self.isVisible() and not self.isMinimized()

    def _save(self, label: str) -> None:
        try:
            written = self._save_controls(label)
        except Exception as exc:                     # noqa: BLE001
            self.view.say_for_a_moment([f"not saved: {exc}"])
            return
        self.view.say_for_a_moment(
            ["saved to the profile: "
             + ", ".join(f"{k} {v:g}" for k, v in written.items())]
            if written else
            ["not saved: the profile names no focus for this camera"])

    def closeEvent(self, event) -> None:
        """Hide rather than destroy: the view stops reading when hidden, and
        the window keeps its size and place for the next time."""
        event.ignore()
        self.hide()
