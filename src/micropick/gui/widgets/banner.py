"""A one-line message in a colored band (REDESIGN §3, Banners).

**Info** (`accent-bg`): "The robot remembers what was on it last time.
Check that it matches the real deck." with Got it.
**Warn** (`warn-bg`): always with its action buttons in the banner -
"Put a tip on first. [Go to Tip]" - so the way out is where the problem
is said. **Ok** (`ok-bg`): a result, "Camera calibrated and saved."

The color comes with an icon, because the screen is read from the side
(§1.4).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from ..theme import COLORS
from ..theme.factory import compact, quiet_button, secondary_button
from ..theme.icons import pixmap

__all__ = ["Banner"]

_ICON = {"info": ("info", "accent"), "warn": ("alert", "warn"),
         "ok": ("check", "ok")}


class Banner(QFrame):
    def __init__(self, text: str = "", tone: str = "info",
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("banner")
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 10, 10)
        row.setSpacing(10)
        self.icon = QLabel(self)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.icon)
        self.label = QLabel(text, self)
        self.label.setWordWrap(True)
        row.addWidget(self.label, 1)
        self._actions = QHBoxLayout()
        self._actions.setSpacing(8)
        row.addLayout(self._actions)
        self.set_tone(tone)

    def set_tone(self, tone: str) -> None:
        self.tone = tone
        self.setProperty("tone", tone)
        name, ink = _ICON[tone]
        self.icon.setPixmap(pixmap(name, COLORS[ink], 18))
        self.style().unpolish(self)
        self.style().polish(self)
        for child in self.findChildren(QLabel):
            child.style().unpolish(child)
            child.style().polish(child)

    def set_text(self, text: str) -> None:
        self.label.setText(text)

    def add_action(self, text: str, *, quiet: bool = False) -> QPushButton:
        """A button in the banner. Quiet for the choice that is not the
        way out ("Use anyway" beside "Measure now")."""
        button = (quiet_button if quiet else secondary_button)(text, self)
        compact(button)
        self._actions.addWidget(button)
        return button

    def clear_actions(self) -> None:
        while self._actions.count():
            widget = self._actions.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
