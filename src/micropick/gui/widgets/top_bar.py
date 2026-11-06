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
**Park** ("Parking…" on `accent-bg` until the move ends), **Retract**
("Retracting…" the same way), a divider, and **Settings**. A button is as wide
as what it shows, its icon and word centered. The busy word goes in the icon's
place, so Park and Retract keep one width and their neighbors stand still.

A widget only: it emits what was clicked and is told what to show. What a
click does - the robot's lease, the session's Lock - is the shell's.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from ..theme import COLORS, SIZES
from ..theme.icons import icon

__all__ = ["PILL_TONES", "StatusPill", "TopBar"]

# A bar button: its icon, the gap after it, and `padding` of #barButton.
ICON_PX = 18
ICON_GAP_PX = 6
PADDING_PX = 12

# The pill's colors per tone: background, text and dot.
PILL_TONES = {
    "busy": ("accent-bg", "accent"),
    "paused": ("warn-bg", "warn"),
    "waiting": ("warn-bg", "warn"),
    "done": ("ok-bg", "ok"),
}


class _BarButton(QPushButton):
    """An icon and a word, centered, in as little room as they take. With
    `busy_text` it is wide enough for both looks, so it never changes width."""

    def __init__(self, text: str, name: str, parent: QWidget, *,
                 checkable: bool = False, busy_text: str = ""):
        super().__init__(text, parent)
        self.setObjectName("barButton")
        self.setCheckable(checkable)
        self.setIconSize(QSize(ICON_PX, ICON_PX))
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.idle_text, self.busy_text = text, busy_text
        self._icon_name = name

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        if self.busy_text:
            words = self.fontMetrics().horizontalAdvance
            hint.setWidth(2 * PADDING_PX + max(
                ICON_PX + ICON_GAP_PX + words(self.idle_text),
                words(self.busy_text)))
        return hint

    def show_busy(self, busy: bool) -> None:
        self.setText(self.busy_text if busy else self.idle_text)
        self.setProperty("busy", busy)


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
    retract_clicked = Signal()
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

        self.light = _BarButton("Light", "light", self, checkable=True)
        self.light.toggled.connect(self._restyle)
        self.light.clicked.connect(lambda: self.light_toggled.emit(
            self.light.isChecked()))
        self.lock = _BarButton("Lock", "lock", self, checkable=True)
        self.lock.toggled.connect(self._restyle)
        self.lock.clicked.connect(lambda: self.lock_toggled.emit(
            self.lock.isChecked()))
        self.park = _BarButton("Park", "park", self, busy_text="Parking…")
        self.park.clicked.connect(self.park_clicked)
        self.retract = _BarButton("Retract", "retract", self,
                                  busy_text="Retracting…")
        self.retract.clicked.connect(self.retract_clicked)
        self.settings = _BarButton("Settings", "settings", self)
        self.settings.clicked.connect(self.settings_clicked)
        divider = QFrame(self)
        divider.setObjectName("barDivider")
        divider.setFixedSize(1, 24)
        for widget in (self.light, self.lock, self.park, self.retract):
            row.addWidget(widget)
        row.addSpacing(4)
        row.addWidget(divider)
        row.addSpacing(4)
        row.addWidget(self.settings)
        for button in (self.light, self.lock, self.park, self.retract,
                       self.settings):
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
        self.park.show_busy(busy)
        self._restyle_one(self.park)

    def set_park_enabled(self, enabled: bool, reason: str = "") -> None:
        self.park.setEnabled(enabled)
        self.park.setToolTip(reason if not enabled else
                             "Move the head to the back right corner")

    def set_retracting(self, busy: bool) -> None:
        self.retract.show_busy(busy)
        self._restyle_one(self.retract)

    def set_retract_enabled(self, enabled: bool, reason: str = "") -> None:
        self.retract.setEnabled(enabled)
        self.retract.setToolTip(reason if not enabled else
                                "Raise the tip to the travel height")

    # -- looks -----------------------------------------------------------

    def _restyle(self) -> None:
        self._restyle_one(self.sender())

    def _restyle_one(self, button: _BarButton) -> None:
        on = button.isChecked()
        busy = bool(button.property("busy"))
        ink = (COLORS["on-accent"] if on else
               COLORS["accent"] if busy else COLORS["text-2"])
        button.setIcon(QIcon() if busy
                       else icon(button._icon_name, ink, ICON_PX, fill=on))
        button.style().unpolish(button)
        button.style().polish(button)

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), SIZES["top-bar"])

