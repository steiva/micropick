"""Robot setup: one page of rows (REDESIGN §5).

The title, and **Reset setup** on its right; no description. Three
blocks of rows - Cameras (the upper camera, the camera calibration, a
divider, the lower camera), Platforms, Positions (over the dish, shake,
calibration) - each row a mark (a green ✓, or a yellow ring while not
set), its label, its value and a ›. A row is clicked as a whole and
`item_chosen(item)` names it (`tasks.SETUP`); the shell opens where it is
set, until each has a sheet of its own.

A position reads as the slot it is nearest to and its coordinates
(`≈ slot 8 · X 245.1 Y 132.4 Z 80.0`), never the name it is stored under.

Reset setup asks "Reset the setup?" with Keep setup as the safe, primary
choice; on Reset the session archives the profile and clears the setup
(`Session.reset_setup`). `said(text)` is the answer, for the window's
toast.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QVBoxLayout,
                               QWidget)

from ...core.deck import nearest_slot
from ..readiness import CALIBRATION_POSITION, DISH_POSITION, SHAKE_POSITION
from ..session import Session
from ..tasks import SETUP_TITLES, setup_done
from ..theme import COLORS
from ..theme.factory import caption, card, heading, secondary_button
from ..theme.icons import pixmap
from ..widgets.overlays import ConfirmDialog
from ..workers import describe_error

__all__ = ["BLOCKS", "RobotSetupPage", "SetupRow", "TITLE"]

log = logging.getLogger(__name__)

TITLE = "Robot setup"

COLUMN_WIDTH = 820
MARK_PX = 24
NOT_SET = "Not set."

# The blocks, in order: their caption and their rows; None is a divider.
BLOCKS = (
    ("Cameras", ("upper_camera", "camera_calibration", None,
                 "lower_camera")),
    ("Platforms", ("platforms",)),
    ("Positions", ("dish_position", "shake_position",
                   "calibration_position")),
)

_POSITIONS = {"dish_position": DISH_POSITION,
              "shake_position": SHAKE_POSITION,
              "calibration_position": CALIBRATION_POSITION}


class SetupRow(QFrame):
    """A mark, a label, a value and a ›; the whole row is clicked."""

    clicked = Signal()

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("setupRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.done = False
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(14)
        self.mark = QLabel(self)
        self.mark.setFixedSize(MARK_PX, MARK_PX)
        self.mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.mark)
        self.label = QLabel(title, self)
        self.label.setObjectName("setupLabel")
        self.label.setMinimumWidth(220)
        row.addWidget(self.label)
        self.value = QLabel(self)
        self.value.setObjectName("setupValue")
        row.addWidget(self.value, 1)
        more = QLabel(self)
        more.setPixmap(pixmap("chevron-right", COLORS["faint"], 18))
        row.addWidget(more)

    def show_state(self, done: bool, value: str) -> None:
        self.done = done
        if done:
            self.mark.setPixmap(pixmap("check", COLORS["ok"], 14))
            self.mark.setStyleSheet(f"background: {COLORS['ok-bg']}; "
                                    f"border-radius: {MARK_PX // 2}px;")
        else:
            self.mark.setPixmap(pixmap("ring", COLORS["warn"], 18))
            self.mark.setStyleSheet("")
        self.value.setText(value)

    def mouseReleaseEvent(self, event) -> None:
        if (event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.position().toPoint())):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


def _ago(when: datetime | None) -> str:
    """"Done today", "Done 3 days ago"; "Done" with no date."""
    if when is None:
        return "Done"
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - when).days
    if days < 1:
        return "Done today"
    return "Done 1 day ago" if days == 1 else f"Done {days} days ago"


def _slots(slots: list[int]) -> str:
    """"9", "5 and 8", "1, 2 and 3"."""
    names = [str(s) for s in sorted(slots)]
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def position_text(xyz) -> str:
    """`≈ slot 8 · X 245.1 Y 132.4 Z 80.0`."""
    x, y, z = xyz
    return (f"≈ slot {nearest_slot(x, y)} · "
            f"X {x:.1f} Y {y:.1f} Z {z:.1f}")


class RobotSetupPage(QWidget):
    """`item_chosen(item)` on a row's click; `said(text)` after Reset."""

    item_chosen = Signal(str)
    said = Signal(str)

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session

        column = QVBoxLayout()
        column.setSpacing(10)
        top = QHBoxLayout()
        top.addWidget(heading(TITLE, 1, self))
        top.addStretch(1)
        self.reset_button = secondary_button("Reset setup", self)
        self.reset_button.clicked.connect(self._reset)
        top.addWidget(self.reset_button)
        column.addLayout(top)
        column.addSpacing(12)

        self.rows: dict[str, SetupRow] = {}
        for name, items in BLOCKS:
            column.addWidget(caption(name, self))
            box = card(self)
            box.layout().setContentsMargins(8, 6, 8, 6)
            box.layout().setSpacing(0)
            for item in items:
                if item is None:
                    line = QFrame(box)
                    line.setObjectName("divider")
                    line.setFixedHeight(1)
                    box.layout().addWidget(line)
                    continue
                row = SetupRow(SETUP_TITLES[item], box)
                row.clicked.connect(
                    lambda i=item: self.item_chosen.emit(i))
                box.layout().addWidget(row)
                self.rows[item] = row
            column.addWidget(box)
            column.addSpacing(14)
        column.addStretch(1)

        holder = QWidget(self)
        holder.setLayout(column)
        holder.setMaximumWidth(COLUMN_WIDTH)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(24, 40, 24, 24)
        outer.addWidget(holder)

        session.profile_changed.connect(lambda _p: self.refresh())
        session.robot_busy_changed.connect(lambda _h: self.refresh())
        self.refresh()

    # -- display -------------------------------------------------------

    def value(self, item: str) -> str:
        """What a row says is set; NOT_SET when nothing is."""
        session, profile = self.session, self.session.profile
        if profile is None or not setup_done(session, item):
            return NOT_SET
        if item in ("upper_camera", "lower_camera"):
            label = (session.upper_camera_label if item == "upper_camera"
                     else session.lower_camera_label)
            return profile.cameras[label].device_name
        if item == "camera_calibration":
            return _ago(profile.pixel_map.fitted_at)
        if item == "platforms":
            # One entry per kind, in the order the deck has them.
            kinds: dict[str, list[int]] = {}
            for module in profile.deck.modules:
                kinds.setdefault(module.name or "Platform",
                                 []).extend(module.slots)
            return " · ".join(f"{name} in {_slots(slots)}"
                              for name, slots in kinds.items())
        return position_text(profile.positions[_POSITIONS[item]])

    def refresh(self) -> None:
        for item, row in self.rows.items():
            row.show_state(setup_done(self.session, item), self.value(item))
        lease = self.session.robot_lease
        self.reset_button.setEnabled(not lease.busy)
        self.reset_button.setToolTip(
            f"Robot busy: {lease.holder}." if lease.busy else "")

    def showEvent(self, event) -> None:
        # A position or a calibration saved on another page has no signal
        # of its own.
        super().showEvent(event)
        self.refresh()

    # -- actions -------------------------------------------------------

    def _reset(self) -> None:
        """Ask, then archive the profile and clear the setup. Keep setup,
        the primary choice, changes nothing."""
        if not ConfirmDialog.confirm(
                self, "Reset the setup?",
                text="Clears the cameras, camera calibration, platforms "
                     "and positions. The deck and picking settings stay.",
                safe="Keep setup", destructive="Reset"):
            return
        try:
            archive = self.session.reset_setup()
        except Exception as exc:                     # noqa: BLE001
            log.error("reset setup: %s", exc)
            self.said.emit(describe_error(exc))
            return
        log.info("setup reset; the profile before it is in %s", archive)
        self.refresh()
        self.said.emit("Setup reset.")
