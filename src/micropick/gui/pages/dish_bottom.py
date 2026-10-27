"""The Dish bottom step (REDESIGN §6.4): Auto pick's fourth, Manual
pick's third, the same screen and state in both.

`dish_bottom` is the Z every pickup height is measured up from
(`PickingConfig.pickup_height`), and this platform has no sensor to find
it: the tip is brought down onto the dish by hand. **No camera** here -
the operator watches the tip itself - only what matters while lowering
it: the robot's Z in big numbers, Up and Down with the step carousel
between them (`jog_pad.StepCarousel`), active only during step 2.

On the right, "Last time: Z 66.40 mm · 3 days ago" with **Use it**, while
there is a last time (`Calibration.dish_bottom_set_at`): the default was
never measured, so it is never taken. Then the three steps:

1. **Tip over the dish center** (Move the tip): the middle of the working
   circle - `circle_center`, a pixel of the picture taken at the position
   over the dish, through the pixel map and the pipette offset - at
   `ABOVE_BOTTOM_MM` over the dish bottom stored now: room for one stored
   a little high, and few presses down.
2. **Lower until it touches** (Done).
3. **Save** (Save dish bottom): Z is read where the tip is, saved into the
   picking settings, and the tip raised. A value more than `BIG_CHANGE_MM`
   from the last is asked about first: it is more often the tip resting
   on something else.

The step is done once the dish bottom was measured, or Use it pressed, in
this robot session (`Session.set_dish_bottom`,
`Session.use_last_dish_bottom`): green "Dish bottom: Z 66.42 mm".

Every robot command goes through the page's jog panel (`JogPanel.run_job`
and `JogPanel.step`), which is never shown: its pad would be a second face
for the same Up and Down, and hidden it binds no keys.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ...core.calibration.pixel_map import PixelMap
from ...hardware.protocols import xyz
from ...workflows import manual as moves
from ..readiness import DISH_POSITION
from ..session import Session, SessionError
from ..theme import COLORS, SIZES, SPACING, mono_font
from ..theme.factory import (caption, card, heading, muted_label,
                             primary_button, scroll_column, secondary_button)
from ..theme.icons import icon
from ..widgets.banner import Banner
from ..widgets.checklist import BLOCKED, OK, TODO
from ..widgets.jog_pad import StepCarousel
from ..widgets.jog_panel import JogPanel
from ..widgets.overlays import ConfirmDialog
from ..widgets.step_card import StepCard, wrapped
from ..widgets.toast import show_toast
from ..workers import describe_error

__all__ = ["DishBottomPage", "ABOVE_BOTTOM_MM", "BIG_CHANGE_MM", "ago"]

TITLE = "Dish bottom"

log = logging.getLogger(__name__)

PANEL_WIDTH = 440

# How high over the stored dish bottom Tip over the dish center stops.
ABOVE_BOTTOM_MM = 10.0
# A new dish bottom this far from the old one is asked about first.
BIG_CHANGE_MM = 3.0
# The step lowering starts with: 10 mm in ten presses would be too few to
# notice the dish coming.
FIRST_STEP_MM = 1.0

# Where the operator is: moving the tip over the center, lowering it,
# saving.
OVER_CENTER, LOWER, SAVE = 0, 1, 2


def ago(when: datetime) -> str:
    """"today", "yesterday", "3 days ago"."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - when).days
    if days <= 0:
        return "today"
    return "yesterday" if days == 1 else f"{days} days ago"


class DishBottomPage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._at = OVER_CENTER

        self.jog = JogPanel(session, shortcut_host=self,
                            machine_controls=False, parent=self)
        self.jog.hide()
        self.jog.pose_changed.connect(self._show_pose)
        self.jog.position_changed.connect(self._show_said)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2,
                                  SPACING * 2)
        layout.setSpacing(SPACING * 2)
        layout.addWidget(self._z_column(), 1)
        layout.addWidget(scroll_column(self._panel(), PANEL_WIDTH))

        session.robot_state_changed.connect(self._on_robot)
        session.robot_busy_changed.connect(lambda _h: self.refresh())
        session.profile_changed.connect(lambda _p: self.refresh())
        session.confirmations_changed.connect(self.refresh)
        self.refresh()

    # -- construction --------------------------------------------------------

    def _z_column(self) -> QWidget:
        column = QWidget(self)
        lines = QVBoxLayout(column)
        lines.setSpacing(SPACING)
        lines.addStretch(1)
        for widget in self._readout(column):
            lines.addWidget(widget, 0, Qt.AlignmentFlag.AlignHCenter)
        lines.addSpacing(SPACING * 4)

        self.up_button = self._z_button("Up", "chevron-up", column)
        self.up_button.clicked.connect(lambda: self.jog.step("z", +1))
        self.carousel = StepCarousel(step=FIRST_STEP_MM, parent=column)
        self.carousel.step_changed.connect(self.jog.set_step)
        self.jog.set_step(self.carousel.step)
        self.down_button = self._z_button("Down", "chevron-down", column)
        self.down_button.clicked.connect(lambda: self.jog.step("z", -1))
        for widget in (self.up_button, self.carousel, self.down_button):
            lines.addWidget(widget, 0, Qt.AlignmentFlag.AlignHCenter)

        # What the robot answered: a step refused, a move not possible.
        self.said = muted_label("", column)
        self.said.setWordWrap(True)
        self.said.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lines.addSpacing(SPACING)
        lines.addWidget(self.said)
        lines.addStretch(1)
        return column

    def _readout(self, parent: QWidget) -> list[QWidget]:
        self.z_readout = QLabel("Z —", parent)
        self.z_readout.setFont(mono_font(SIZES["readout"], 500))
        unit = muted_label("mm", parent)
        return [caption("Tip height", parent), self.z_readout, unit]

    def _z_button(self, text: str, name: str, parent: QWidget):
        button = secondary_button(text, parent)
        button.setIcon(icon(name, COLORS["text"], 22, stroke=2.2))
        button.setFixedSize(180, 72)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return button

    def _panel(self) -> QWidget:
        panel = QWidget(self)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        column.addWidget(heading("Find the dish bottom", 1, panel))

        self.last_card = card(panel)
        row = QHBoxLayout()
        words = QVBoxLayout()
        words.setSpacing(2)
        self.last_value = heading("", 3, panel)
        self.last_when = muted_label("", panel)
        words.addWidget(self.last_value)
        words.addWidget(self.last_when)
        row.addLayout(words, 1)
        self.use_button = secondary_button("Use it", panel)
        self.use_button.clicked.connect(self._use_last)
        row.addWidget(self.use_button)
        self.last_card.layout().addLayout(row)
        column.addWidget(self.last_card)

        self.over_step = StepCard(panel, 1, "Tip over the dish center")
        self.over_step.add(wrapped(
            "The tip goes to the middle of the dish and stops a little "
            "above the bottom."))
        self.move_button = primary_button("Move the tip", panel)
        self.move_button.clicked.connect(self._tip_over_center)
        self.over_step.add(self.move_button)
        column.addWidget(self.over_step.box)

        self.lower_step = StepCard(panel, 2, "Lower until it touches")
        self.lower_step.add(wrapped(
            "Go down until the dish bottom bends a little. Then go up one "
            "step, make the step smaller and lower again."))
        self.lowered_button = primary_button("Done", panel)
        self.lowered_button.clicked.connect(self._lowered)
        self.lower_step.add(self.lowered_button)
        column.addWidget(self.lower_step.box)

        self.save_step = StepCard(panel, 3, "Save")
        self.save_button = primary_button("Save dish bottom", panel)
        self.save_button.clicked.connect(self._save)
        self.save_step.add(self.save_button)
        column.addWidget(self.save_step.box)

        self.result = Banner("", tone="ok", parent=panel)
        column.addWidget(self.result)
        column.addStretch(1)
        return panel

    # -- what the page shows -------------------------------------------------

    def set_step(self, mm: float) -> None:
        """The step Up and Down take."""
        self.carousel.set_step(mm, emit=True)

    def _show_pose(self, pose) -> None:
        if pose is not None:
            self.z_readout.setText(f"Z {pose[2]:.2f}")
        elif self.session.robot is None:
            self.z_readout.setText("Z —")

    def _show_said(self, lines) -> None:
        """Every answer of the panel's, a job's end included: the buttons
        follow it (the robot's lease is given back before)."""
        self.said.setText("\n".join(lines[1:]))
        self.refresh()

    def _on_robot(self, _state) -> None:
        # A new connection: the tip may be anywhere.
        if self.session.robot is None:
            self._at = OVER_CENTER
            self._show_pose(None)
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # The robot may have moved while another page was shown.
        self.jog.refresh_position()
        self.refresh()

    def refresh(self) -> None:
        session, profile = self.session, self.session.profile
        movable = (session.robot is not None and not session.robot_lease.busy
                   and not self.jog.busy)

        measured = (profile.calibration.dish_bottom_set_at
                    if profile is not None else None)
        self.last_card.setVisible(measured is not None)
        if measured is not None:
            self.last_value.setText(
                f"Last time: Z {profile.picking.dish_bottom:.2f} mm")
            self.last_when.setText(ago(measured))
        confirmed = session.is_confirmed("dish_bottom")
        self.use_button.setEnabled(not confirmed)

        center, why = self._center_target()
        self.move_button.setEnabled(movable and center is not None)
        self.move_button.setToolTip(why)
        lowering = self._at == LOWER
        self.up_button.setEnabled(movable and lowering)
        self.down_button.setEnabled(movable and lowering)
        self.carousel.setEnabled(lowering)
        self.lowered_button.setEnabled(lowering)
        self.save_button.setEnabled(movable and self._at == SAVE)
        for number, step in enumerate((self.over_step, self.lower_step,
                                       self.save_step)):
            step.set_state(OK if number < self._at else
                           TODO if number == self._at else BLOCKED)

        self.result.setVisible(confirmed and profile is not None)
        if confirmed and profile is not None:
            self.result.set_text(
                f"Dish bottom: Z {profile.picking.dish_bottom:.2f} mm")

    # -- the steps -----------------------------------------------------------

    def _center_target(self):
        """(x, y) for the tip over the dish center, or None and why."""
        profile = self.session.profile
        if profile is None:
            return None, "No profile loaded."
        observe = profile.positions.get(DISH_POSITION)
        if observe is None:
            return None, "The position over the dish is not set."
        if profile.pixel_map is None:
            return None, "The camera is not calibrated."
        offset = profile.calibration.pipette_offset
        if offset is None:
            return None, "The tip is not calibrated."
        pmap = PixelMap.from_config(profile.pixel_map)
        cx, cy = profile.picking.circle_center
        xy = moves.tip_target(pmap, float(cx), float(cy), observe, offset)
        return (float(xy[0]), float(xy[1])), ""

    def _tip_over_center(self) -> None:
        xy, why = self._center_target()
        if xy is None or self.session.robot is None:
            return
        z = self.session.profile.picking.dish_bottom + ABOVE_BOTTOM_MM
        motion = self.session.motion
        refused = motion.refused((*xy, z))
        if refused:
            self.jog.tell(f"Not possible: {refused}.")
            return

        def job(log):
            motion.tip_to(xy, z, log=log)

        def then(_result):
            self._at = LOWER
            self.refresh()
            return ""

        if not self.jog.run_job(job, what="tip over the dish center",
                                then=then):
            self.jog.tell(self.session.robot_lease.refusal)
        self.refresh()

    def _lowered(self) -> None:
        self._at = SAVE
        self.refresh()

    def _save(self) -> None:
        """Read Z where the tip is, then save it."""
        robot = self.session.robot
        if robot is None or self.session.profile is None:
            return
        if not self.jog.run_job(lambda _log: float(xyz(robot)[2]),
                                moves=False, what="reading Z",
                                then=self._saved):
            self.jog.tell(self.session.robot_lease.refusal)
        self.refresh()

    def _saved(self, z: float) -> str:
        old = self.session.profile.picking.dish_bottom
        if (abs(z - old) > BIG_CHANGE_MM
                and not ConfirmDialog.confirm(
                    self, f"Save Z {z:.2f} mm?",
                    text=f"It is {abs(z - old):.2f} mm from last time. Check "
                         f"that the tip rests on the dish bottom.",
                    safe="Cancel", destructive="Save")):
            return ""
        try:
            self.session.set_dish_bottom(z)
        except SessionError as exc:
            return describe_error(exc)
        self._at = OVER_CENTER
        show_toast(self, "Dish bottom saved.")
        # After this job's own completion, which is still being delivered.
        QTimer.singleShot(0, self._lift)
        self.refresh()
        return ""

    def _lift(self) -> None:
        if self.session.robot is None:
            return
        motion = self.session.motion

        def job(log):
            motion.raise_tip(log=log)

        self.jog.run_job(job, what="lifting the tip")

    def _use_last(self) -> None:
        try:
            self.session.use_last_dish_bottom()
        except SessionError as exc:
            self.jog.tell(describe_error(exc))
            return
        show_toast(self, "Using the last dish bottom.")
        self.refresh()
