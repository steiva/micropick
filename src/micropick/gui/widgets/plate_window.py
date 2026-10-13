"""A plate map in a window of its own, as large as the window is made.

The map on a page has the width of its panel, and a 384 plate there is
wells of a dozen pixels with names too small to read. This window holds a
`PlateMirror` of that map: the same scene, so the same wells, groups and
selection, and the mouse selects here as it does there. Under it, the line
the page writes about the well under the pointer (`say`).

Like a camera's window: closing it hides it, so it comes back the same size
and in the same place, and it belongs to the main window.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..theme import SPACING
from .plate_view import PlateMirror, PlateView

__all__ = ["PlateWindow"]


class PlateWindow(QWidget):
    def __init__(self, source: PlateView, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("plate")
        self.resize(960, 680)
        self.view = PlateMirror(source, self)
        self.readout = QLabel(self)
        self.readout.setContentsMargins(SPACING, SPACING // 2, SPACING,
                                        SPACING // 2)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.readout)

    def say(self, text: str) -> None:
        self.readout.setText(text)

    def show_window(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    @property
    def shown(self) -> bool:
        return self.isVisible() and not self.isMinimized()

    def closeEvent(self, event) -> None:
        """Hide rather than destroy: the window keeps its size and place."""
        event.ignore()
        self.hide()
