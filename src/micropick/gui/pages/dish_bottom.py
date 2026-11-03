"""The Dish bottom step (REDESIGN §6.4): Auto pick's fourth, Manual
pick's third, the same screen and state in both.

`dish_bottom` is the Z every pickup height is measured up from
(`PickingConfig.pickup_height`), and this platform has no sensor to find
it: the tip is brought down onto the dish by hand. **No camera** here -
the operator watches the tip itself - only what matters while lowering
it: the robot's Z in big numbers and, under it, three cards side by side,
the tip lowered in finer steps from one to the next (`STAGES`: Coarse,
Finer, Finest at 1, 0.5 and 0.1 mm). Each has Up and Down with its own
step carousel (`jog_pad.StepCarousel`), active only while it is the one
lowering; **Keys** puts each one's key on it as a keycap, as the jog pad
does (`jog_pad.KeycapButton`), and lists the keys under them (#58).

1. **Move the tip**, in the first card: over the middle of the working
   circle - `circle_center`, a pixel of the picture taken at the position
   over the dish, through the pixel map and the pipette offset - at
   `ABOVE_BOTTOM_MM` over the dish bottom stored now: room for one stored
   a little high, and few presses down.
2. **It touches**, in each card but the last: the tip goes up by that
   card's step and the next card, a finer step, lowers it again. The
   operator touches the bottom three times, each closer.
3. **Save dish bottom**, the last card's: Z is read where the tip is,
   saved into the picking settings, and the tip raised. A value more than
   `BIG_CHANGE_MM` from the last is asked about first: it is more often
   the tip resting on something else.

On the right, "Last time: Z 66.40 mm · 3 days ago" with **Use it**, while
there is a last time (`Calibration.dish_bottom_set_at`): the default was
never measured, so it is never taken; and where the dish is, "Over the
dish · slot 9" with **Change**, Robot setup's sheet for that position
over the task (a dish put elsewhere for one experiment).

The step is done once the dish bottom was measured, or Use it pressed, in
this robot session (`Session.set_dish_bottom`,
`Session.use_last_dish_bottom`): green "Dish bottom: Z 66.42 mm" with
**Redo**, back to Move the tip; the value saved holds until a new one is.

Every robot command goes through the page's jog panel (`JogPanel.run_job`
and `JogPanel.step`), which is never shown: its pad would be a second face
for the same Up and Down, and hidden it binds no keys. The page binds its
own, from the key actions (`gui.keys`: Z up and down, the step sizes of
the card lowering, the key list) - no XY keys, the tip stays over the
dish center - enabled while lowering, the page shown and the robot free.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ...core.calibration.pixel_map import PixelMap
from ...hardware.protocols import xyz
from ..camera_moves import pose_slot
from ...workflows import manual as moves
from .. import keys
from ..readiness import DISH_POSITION
from ..session import Session, SessionError
from ..theme import COLORS, SIZES, SPACING, mono_font
from ..theme.factory import (caption, card, heading, muted_label,
                             primary_button, scroll_column, secondary_button,
                             set_primary)
from ..theme.icons import icon
from ..widgets.banner import Banner
from ..widgets.checklist import BLOCKED, OK, TODO
from ..widgets.jog_pad import KeycapButton, StepCarousel, key_row, keycap
from ..widgets.jog_panel import JogPanel
from ..widgets.overlays import ConfirmDialog
from ..widgets.step_card import StepCard, wrapped
from ..widgets.toast import show_toast
from ..workers import describe_error

__all__ = ["DishBottomPage", "ABOVE_BOTTOM_MM", "BIG_CHANGE_MM", "ago",
           "dish_center"]

TITLE = "Dish bottom"

log = logging.getLogger(__name__)

PANEL_WIDTH = 440

# How high over the stored dish bottom Tip over the dish center stops.
ABOVE_BOTTOM_MM = 10.0
# A new dish bottom this far from the old one is asked about first.
BIG_CHANGE_MM = 3.0
# The cards the tip is lowered in, each a finer step: (title, step mm).
# The first starts at 1 mm: 10 mm in ten presses would be too few to
# notice the dish coming.
STAGES = (("Coarse", 1.0), ("Finer", 0.5), ("Finest", 0.1))
FIRST_STEP_MM = STAGES[0][1]
Z_BUTTON = (150, 56)

# Where the operator is: moving the tip over the center, lowering it (in
# `_stage`, a card of STAGES), done (saved, or the last value used).
OVER_CENTER, LOWER, DONE = 0, 1, 2

# The page's keys: action id -> what it does, in the key list.
KEYS = (("z+", "z-", "tip up / down"),
        ("step_up", "step_down", "larger / smaller step"),
        ("help", None, "show or hide this list"))


def ago(when: datetime) -> str:
    """"today", "yesterday", "3 days ago"."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - when).days
    if days <= 0:
        return "today"
    return "yesterday" if days == 1 else f"{days} days ago"


def dish_center(profile):
    """(x, y) for the tip over the dish center, or None and why: the
    middle of the working circle (`circle_center`, a pixel of the picture
    taken at the position over the dish) through the pixel map and the
    pipette offset. Liquid in tip's too (`widgets.tip_liquid`)."""
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


class _Stage:
    """One card of STAGES: its step, Up and Down, and It touches (Save
    dish bottom on the last)."""

    def __init__(self, parent: QWidget, title: str, step: float, text: str,
                 done: str):
        self.card = StepCard(parent, None, title)
        self.card.add(wrapped(text))
        self.carousel = StepCarousel(step=step, parent=parent)
        self.up = _z_button("Up", "chevron-up", parent)
        self.down = _z_button("Down", "chevron-down", parent)
        for widget in (self.up, self.carousel, self.down):
            self.card.box.layout().addWidget(widget, 0,
                                             Qt.AlignmentFlag.AlignHCenter)
        self.touch = primary_button(done, parent)
        self.card.add_button(self.touch)


def _z_button(text: str, name: str, parent: QWidget):
    # A secondary button that can carry its key, as the pad's do (#58).
    button = KeycapButton(text, parent)
    button.setObjectName("secondary")
    button.setIcon(icon(name, COLORS["text"], 22, stroke=2.2))
    button.setFixedSize(*Z_BUTTON)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return button


class DishBottomPage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._at = OVER_CENTER
        self._stage = 0

        # Its keys on itself, hidden: they never fire (the page's own do).
        self.jog = JogPanel(session, machine_controls=False, parent=self)
        self.jog.hide()
        self.jog.pose_changed.connect(self._show_pose)
        self.jog.position_changed.connect(self._show_said)
        self._shortcuts: list[QShortcut] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2,
                                  SPACING * 2)
        layout.setSpacing(SPACING * 2)
        layout.addWidget(self._z_column(), 1)
        layout.addWidget(scroll_column(self._panel(), PANEL_WIDTH))

        session.robot_state_changed.connect(self._on_robot)
        session.robot_busy_changed.connect(self._on_robot_busy)
        session.profile_changed.connect(lambda _p: self.refresh())
        session.confirmations_changed.connect(self._on_confirmations)
        session.settings_changed.connect(lambda _s: self._bind_keys())
        self._bind_keys()
        self.refresh()

    # -- construction --------------------------------------------------------

    def _z_column(self) -> QWidget:
        column = QWidget(self)
        lines = QVBoxLayout(column)
        lines.setSpacing(SPACING)
        lines.addStretch(1)
        for widget in self._readout(column):
            lines.addWidget(widget, 0, Qt.AlignmentFlag.AlignHCenter)
        lines.addSpacing(SPACING * 3)

        row = QHBoxLayout()
        row.setSpacing(SPACING)
        last = len(STAGES) - 1
        self.stages: list[_Stage] = []
        for i, (title, step) in enumerate(STAGES):
            stage = _Stage(column, title, step,
                           "Lower until the tip touches the dish." if i == 0
                           else "Lower again, in smaller steps.",
                           "Save dish bottom" if i == last else "It touches")
            if i == 0:
                self.move_button = primary_button("Move the tip", column)
                self.move_button.clicked.connect(self._tip_over_center)
                stage.card.box.layout().insertWidget(2, self.move_button)
                stage.card.own_button(self.move_button)
            stage.up.clicked.connect(lambda: self.jog.step("z", +1))
            stage.down.clicked.connect(lambda: self.jog.step("z", -1))
            stage.carousel.step_changed.connect(
                lambda mm, i=i: self._step_chosen(i, mm))
            stage.touch.clicked.connect(self._save if i == last
                                        else self._touches)
            row.addWidget(stage.card.box, 1)
            self.stages.append(stage)
        lines.addLayout(row)
        self.save_button = self.stages[-1].touch
        self.jog.set_step(self.carousel.step)

        self.keys_button = secondary_button("Keys", column)
        self.keys_button.setProperty("compact", True)
        self.keys_button.setCheckable(True)
        self.keys_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.keys_button.toggled.connect(lambda _on: self._show_keycaps())
        lines.addSpacing(SPACING)
        lines.addWidget(self.keys_button, 0, Qt.AlignmentFlag.AlignHCenter)
        # The pad's list: its look, the keys named as on the keycaps.
        self.keys_list = QLabel(column)
        self.keys_list.setObjectName("faint")
        self.keys_list.setTextFormat(Qt.TextFormat.RichText)
        self.keys_list.hide()
        lines.addWidget(self.keys_list, 0, Qt.AlignmentFlag.AlignHCenter)

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

        dish_card = card(panel)
        row = QHBoxLayout()
        self.dish_line = QLabel("", panel)
        row.addWidget(self.dish_line, 1)
        self.dish_button = secondary_button("Change", panel)
        self.dish_button.clicked.connect(
            lambda: self.session.page_requested.emit("dish_position"))
        row.addWidget(self.dish_button)
        dish_card.layout().addLayout(row)
        column.addWidget(dish_card)

        self.result = Banner("", tone="ok", parent=panel)
        self.redo_button = self.result.add_action("Redo")
        self.redo_button.clicked.connect(self._redo)
        column.addWidget(self.result)
        column.addStretch(1)
        return panel

    # -- what the page shows -------------------------------------------------

    @property
    def lowering(self) -> _Stage:
        """The card lowering now, or the first before Move the tip."""
        return self.stages[self._stage]

    # The lowering card's, for the keys and a test.
    @property
    def carousel(self) -> StepCarousel:
        return self.lowering.carousel

    @property
    def up_button(self):
        return self.lowering.up

    @property
    def down_button(self):
        return self.lowering.down

    def set_step(self, mm: float) -> None:
        """The step Up and Down take, on the card lowering."""
        self.carousel.set_step(mm, emit=True)

    def _step_chosen(self, stage: int, mm: float) -> None:
        if stage == self._stage:
            self.jog.set_step(mm)

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

    # -- keys ----------------------------------------------------------------

    def _bind_keys(self) -> None:
        """The page's keys as bound in Settings: again after a change."""
        for shortcut in self._shortcuts:
            shortcut.setEnabled(False)
            shortcut.setParent(None)
            shortcut.deleteLater()
        self._shortcuts = []
        bindings = self.session.settings.keys
        doing = {"z+": lambda: self.jog.step("z", +1),
                 "z-": lambda: self.jog.step("z", -1),
                 "step_up": lambda: self.carousel.step_by(+1),
                 "step_down": lambda: self.carousel.step_by(-1),
                 "help": self.keys_button.toggle}
        for action_id, act in doing.items():
            for sequence in keys.sequences(bindings, action_id):
                shortcut = QShortcut(sequence, self)
                shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
                shortcut.activated.connect(act)
                shortcut.setProperty("action", action_id)
                self._shortcuts.append(shortcut)
        lines = []
        for first, second, what in KEYS:
            names = [keycap(bindings, a) for a in (first, second) if a]
            lines.append(f"{'/'.join(names)}   {what}")
        self.keys_list.setText("<br>".join(key_row(line) for line in lines))
        self._show_keycaps()
        self._enable_keys()

    def _show_keycaps(self) -> None:
        """Keys on: each card's Up, Down and step arrows with its key as
        bound, as on the jog pad, and the list under them."""
        on = self.keys_button.isChecked()
        bindings = self.session.settings.keys
        for stage in self.stages:
            stage.up.set_keycap(keycap(bindings, "z+") if on else "")
            stage.down.set_keycap(keycap(bindings, "z-") if on else "")
            stage.carousel.show_keycaps(bindings if on else None)
        self.keys_list.setVisible(on)

    def _enable_keys(self) -> None:
        """Only on screen - a window-wide key of a hidden page would
        compete with the shown one's - and, but for the list, only while
        lowering with the robot free."""
        movable = (self.session.robot is not None
                   and not self.session.robot_lease.busy
                   and not self.jog.busy)
        for shortcut in self._shortcuts:
            listing = shortcut.property("action") == "help"
            shortcut.setEnabled(self.isVisible() and (
                listing or self._at == LOWER and movable))

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._enable_keys()

    def _on_confirmations(self) -> None:
        # A new robot session forgot the dish bottom: measure it again.
        if self._at == DONE and not self.session.is_confirmed("dish_bottom"):
            self._to_start()
        self.refresh()

    def _on_robot(self, _state) -> None:
        # A new connection: the tip may be anywhere.
        if self.session.robot is None:
            self._to_start()
            self._show_pose(None)
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # The robot may have moved while another page was shown.
        self.jog.refresh_position()
        self.refresh()

    def _on_robot_busy(self, holder) -> None:
        """The robot let go: work begun elsewhere may have moved it - the
        Tip step's raise, still going as this page was shown (#56) - so
        the pose is read again, where the tip ended. Not after a job of
        this page's: its lease is let go before its end is delivered, and
        a read then would take its place (`JogPanel._claim`)."""
        if (holder is None and self.isVisible()
                and not self.jog.job_pending):
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

        observe = (profile.positions.get(DISH_POSITION)
                   if profile is not None else None)
        self.dish_line.setText(
            "Over the dish: not set" if observe is None else
            f"Over the dish · slot "
            f"{pose_slot(session, DISH_POSITION, observe[:2])}")
        self.dish_button.setText("Set" if observe is None else "Change")
        self.dish_button.setEnabled(profile is not None)

        center, why = dish_center(profile)
        self.move_button.setEnabled(movable and center is not None)
        self.move_button.setToolTip(why)
        for i, stage in enumerate(self.stages):
            now = self._at == LOWER and i == self._stage
            stage.up.setEnabled(movable and now)
            stage.down.setEnabled(movable and now)
            stage.carousel.setEnabled(now)
            stage.touch.setEnabled(movable and now)
            done = self._at == DONE or (self._at == LOWER
                                        and i < self._stage)
            stage.card.set_state(
                OK if done else
                TODO if now or (i == 0 and self._at == OVER_CENTER)
                else BLOCKED)
        # Move the tip is the first card's button until the tip is there.
        set_primary(self.move_button, self._at == OVER_CENTER)
        set_primary(self.stages[0].touch, self._at == LOWER
                    and self._stage == 0)

        self.result.setVisible(confirmed and profile is not None)
        if confirmed and profile is not None:
            self.result.set_text(
                f"Dish bottom: Z {profile.picking.dish_bottom:.2f} mm")
        self.redo_button.setVisible(self._at == DONE)
        self._enable_keys()

    # -- the steps -----------------------------------------------------------

    def _to_start(self) -> None:
        """Back to Move the tip, every card at its first step."""
        self._at = OVER_CENTER
        self._stage = 0
        for stage, (_title, step) in zip(self.stages, STAGES):
            stage.carousel.set_step(step)
        self.jog.set_step(self.carousel.step)

    def _tip_over_center(self) -> None:
        xy, why = dish_center(self.session.profile)
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
            self._stage = 0
            self.jog.set_step(self.carousel.step)
            self.refresh()
            return ""

        if not self.jog.run_job(job, what="tip over the dish center",
                                then=then):
            self.jog.tell(self.session.robot_lease.refusal)
        self.refresh()

    def _touches(self) -> None:
        """It touches: up by this card's step, and the next card lowers,
        a finer step."""
        controller = self.jog.controller
        if controller is None:
            return
        step = self.carousel.step

        def then(result):
            # Refused (the top of the envelope): the same card again.
            if result.ok:
                self._stage += 1
                self.jog.set_step(self.carousel.step)
            self.refresh()
            return result

        if not self.jog.run_job(
                lambda _log: controller.move("z", +1, distance=step),
                what="raising the tip a step", then=then):
            self.jog.tell(self.session.robot_lease.refusal)
        self.refresh()

    def _redo(self) -> None:
        """Back to Move the tip; the value saved holds until a new one is."""
        self._to_start()
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
        self._at = DONE
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
        self._at = DONE
        show_toast(self, "Using the last dish bottom.")
        self.refresh()
