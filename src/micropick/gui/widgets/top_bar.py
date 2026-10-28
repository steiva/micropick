"""The bar across the top of every screen (REDESIGN §3, TopBar).

Left, where the user is: `⌂ Home › Auto pick` (`› <sheet>` while a sheet
is open). Every crumb but the last is a button back to it; Home is the
same button on Home too, where it goes nowhere. No app name -
the window's title bar has it - and no back arrow.

Center right, the **StatusPill**: only while the robot is doing something
("Picking", "Paused", "Waiting for you"), visible from every screen, a
click on it goes back to the task that runs. Beside it, a red **STOP**,
shown only while a run has the robot (§12.3); it does what Esc does.

Right, in this order: **Light** and **Lock** (toggles, filled while on),
**Park** ("Parking…" on `accent-bg` until the move ends), a divider, and
**Settings**.

A widget only: it emits what was clicked and is told what to show. What a
click does - the robot's lease, the session's Lock - is the shell's.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QToolButton, QWidget)

from ..theme import COLORS, SIZES
from ..theme.icons import icon

__all__ = ["PILL_TONES", "StatusPill", "TopBar"]

# The pill's colors per tone: background, text and dot.
PILL_TONES = {
    "busy": ("accent-bg", "accent"),
    "paused": ("warn-bg", "warn"),
    "waiting": ("warn-bg", "warn"),
    "done": ("ok-bg", "ok"),
}


def _bar_button(text: str, name: str, parent: QWidget, *,
                checkable: bool = False) -> QToolButton:
    button = QToolButton(parent)
    button.setObjectName("barButton")
    button.setText(text)
    button.setCheckable(checkable)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    button.setIconSize(QSize(18, 18))
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button._icon_name = name
    return button


class StatusPill(QLabel):
    """What the robot is doing, in a colored pill with a dot. Clickable."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("statusPill")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.tone = "busy"
        self.hide()

    def show_status(self, text: str | None, tone: str = "busy") -> None:
        """`None` hides it: the robot is idle."""
        if not text:
            self.hide()
            return
        self.tone = tone
        bg, ink = PILL_TONES[tone]
        self.setStyleSheet(f"background: {COLORS[bg]}; color: {COLORS[ink]};")
        self.setText(f'<span style="color:{COLORS[ink]}">●</span>&nbsp;&nbsp;'
                     f"{text}")
        self.show()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class TopBar(QFrame):
    home_clicked = Signal()
    crumb_clicked = Signal(int)   # index into the path, 0 the first after Home
    light_toggled = Signal(bool)
    lock_toggled = Signal(bool)
    park_clicked = Signal()
    settings_clicked = Signal()
    stop_clicked = Signal()
    status_clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("topBar")
        self.setFixedHeight(SIZES["top-bar"])
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(4)

        self._crumbs = QHBoxLayout()
        self._crumbs.setSpacing(2)
        row.addLayout(self._crumbs)
        row.addStretch(1)

        self.status = StatusPill(self)
        self.status.clicked.connect(self.status_clicked)
        row.addWidget(self.status)
        self.stop = QPushButton("STOP", self)
        self.stop.setObjectName("stopButton")
        self.stop.setIcon(icon("stop", "#FFFFFF", 16, fill=True))
        self.stop.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.stop.setToolTip("Stop the run (Esc)")
        self.stop.clicked.connect(self.stop_clicked)
        self.stop.hide()
        row.addWidget(self.stop)
        row.addSpacing(8)

        self.light = _bar_button("Light", "light", self, checkable=True)
        self.light.toggled.connect(self._restyle)
        self.light.clicked.connect(lambda: self.light_toggled.emit(
            self.light.isChecked()))
        self.lock = _bar_button("Lock", "lock", self, checkable=True)
        self.lock.toggled.connect(self._restyle)
        self.lock.clicked.connect(lambda: self.lock_toggled.emit(
            self.lock.isChecked()))
        self.park = _bar_button("Park", "park", self)
        self.park.setMinimumWidth(104)
        self.park.clicked.connect(self.park_clicked)
        self.settings = _bar_button("Settings", "settings", self)
        self.settings.clicked.connect(self.settings_clicked)
        divider = QFrame(self)
        divider.setObjectName("barDivider")
        divider.setFixedSize(1, 24)
        for widget in (self.light, self.lock, self.park):
            row.addWidget(widget)
        row.addSpacing(4)
        row.addWidget(divider)
        row.addSpacing(4)
        row.addWidget(self.settings)
        for button in (self.light, self.lock, self.park, self.settings):
            self._restyle_one(button)
        self.set_path([])

    # -- what it shows -------------------------------------------------

    def set_path(self, path: list[str]) -> None:
        """The crumbs after Home: `["Auto pick"]`, or with a sheet's title."""
        while self._crumbs.count():
            item = self._crumbs.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        names = ["Home", *path]
        for i, name in enumerate(names):
            last = i == len(names) - 1
            if i:
                arrow = QLabel(self)
                arrow.setPixmap(icon("chevron-right", COLORS["faint"], 14)
                                .pixmap(14, 14))
                self._crumbs.addWidget(arrow)
            if i == 0:
                # Home is the same button on every screen, so the gap
                # between the house and "Home" never changes; on Home itself
                # it is where you are, and goes nowhere.
                button = QPushButton(name, self)
                button.setObjectName("crumb")
                button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                button.setProperty("here", last)
                button.setIcon(icon("home", COLORS["text" if last
                                                  else "text-2"], 18))
                if not last:
                    button.setCursor(Qt.CursorShape.PointingHandCursor)
                    button.clicked.connect(self.home_clicked)
                self._crumbs.addWidget(button)
                continue
            if last:
                label = QLabel(name, self)
                label.setObjectName("crumbHere")
                self._crumbs.addWidget(label)
                continue
            button = QPushButton(name, self)
            button.setObjectName("crumb")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda _=False, n=i - 1: self.crumb_clicked.emit(n))
            self._crumbs.addWidget(button)
        self.path = list(path)

    def set_status(self, text: str | None, tone: str = "busy") -> None:
        self.status.show_status(text, tone)

    def set_stop_visible(self, shown: bool) -> None:
        self.stop.setVisible(shown)

    def set_light(self, on: bool | None) -> None:
        """The robot's rail light; `None` while it is not known."""
        self.light.setChecked(bool(on))
        self.light.setEnabled(on is not None)

    def set_lock(self, on: bool, *, allowed: bool = True,
                 reason: str = "") -> None:
        """Lock is grayed while a run has the robot (§12.4)."""
        self.lock.setChecked(on)
        self.lock.setEnabled(on or allowed)
        self.lock.setToolTip(reason if not allowed and not on else
                             "Nothing moves the robot while locked")

    def set_parking(self, busy: bool) -> None:
        self.park.setText("Parking…" if busy else "Park")
        self.park.setProperty("busy", busy)
        self._restyle_one(self.park)

    def set_park_enabled(self, enabled: bool, reason: str = "") -> None:
        self.park.setEnabled(enabled)
        self.park.setToolTip(reason if not enabled else
                             "Move the head to the back right corner")

    # -- looks -----------------------------------------------------------

    def _restyle(self) -> None:
        self._restyle_one(self.sender())

    def _restyle_one(self, button: QToolButton) -> None:
        on = button.isChecked()
        busy = bool(button.property("busy"))
        ink = (COLORS["on-accent"] if on else
               COLORS["accent"] if busy else COLORS["text-2"])
        button.setIcon(icon(button._icon_name, ink, 18, fill=on))
        button.style().unpolish(button)
        button.style().polish(button)

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), SIZES["top-bar"])

