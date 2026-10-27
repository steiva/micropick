"""Robot setup's position sheets (REDESIGN §5, #12): the position over the
dish, the shake position and the calibration position.

One sheet for the three, opened from a Positions row (or a step that
needs one): the upper camera's live picture on the left, the jog pad
floating over it, and on the right what to do for the position opened:

- **Position over the dish** (`observe`): "Where will the dish with
  cuboids stand?" - Slot 8, on the picking platform (the default), or
  somewhere else - and for slot 8 "Move camera to slot 8". Then the
  picture is clicked, or the arrows used, until the dish rim surrounds
  the crosshair.
- **Shake position** (`shake`): the dish in its holder, the tip into the
  holder's hole, by the pad; the camera cannot see it, the note says so.
- **Calibration position** (`tip_calib`, the disc position the tip
  calibration starts at - no new pose): the crosshair disc on the
  calibration module, and the crosshair in the middle of the picture.
  "Move camera to the calibration module" while a slot has one.

Done saves where the robot is - read from it, through the jog panel's
`run_job` - under the position's stored name (`Session.remember`); Cancel and × save nothing. Done is gray until the
pose is read, while a move runs and while anything else has the robot.
**Go to saved position** goes back to what is stored, no higher than
the travel height (`Motion.tip_to`): a pose taught after a retract is out
of bounds for the robot.

A click on the picture moves the camera there through the pixel map
(`camera_moves.move_to_pixel`); before the first camera calibration it
moves nothing, and the arrows do it.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QRadioButton, QVBoxLayout, QWidget

from ...hardware.protocols import xyz
from ..auto_camera import CameraOpener
from ..camera_moves import move_over_slot, move_to_pixel
from ..session import Session
from ..tasks import SETUP_POSITIONS, SETUP_TITLES
from ..theme import SPACING
from ..theme.factory import heading, quiet_button, scroll_column, \
    secondary_button
from ..widgets.banner import Banner
from ..widgets.camera_view import ZOOM_AND_PAN, CameraView
from ..widgets.feed_row import FeedRow
from ..widgets.jog_panel import JogPanel
from ..widgets.overlays import SheetWindow
from ..widgets.step_card import wrapped
from ..workers import describe_error

__all__ = ["CALIBRATION_MODULE", "DISH_SLOT", "PositionSheet"]

log = logging.getLogger(__name__)

PANEL_WIDTH = 400

# Where the dish stands unless the operator says otherwise.
DISH_SLOT = 8
# The module type the crosshair disc stands on.
CALIBRATION_MODULE = "Calibration module"

DISH_QUESTION = "Where will the dish with cuboids stand?"
DISH_HINT = ("Put the dish there. The whole dish should be in the "
             "picture, its rim around the crosshair.")
CANT_SEE = "The camera can't see this. Watch the robot itself."
USE_THE_ARROWS = "Use the arrows to move the camera."

# What each position's sheet says to do.
INSTRUCTIONS = {
    "dish_position": "Click the picture or use the arrows until the dish "
                     "rim surrounds the crosshair.",
    "shake_position": "1. Put the dish in its holder.\n"
                      "2. Lower the tip into the hole of the holder.",
    "calibration_position": "1. Put the crosshair disc on the calibration "
                            "module.\n"
                            "2. Click the picture or use the arrows until "
                            "the crosshair is in the middle.\n"
                            "3. Press Done.",
}


class PositionSheet(SheetWindow):
    """`open_for(item)`, one of `tasks.SETUP_POSITIONS`; `said(text)`
    after Done."""

    said = Signal(str)

    def __init__(self, session: Session, host: QWidget):
        super().__init__(host, "")
        self.session = session
        self.item = "dish_position"
        self.opener = CameraOpener(session, self)

        self.view = CameraView(self.body_widget)
        self.jog = JogPanel(session, shortcut_host=self,
                            machine_controls=False,
                            parent=self.body_widget)
        self.jog.show_position_on(self.view)
        self.view.set_mouse_help(["Click   move the camera there",
                                  *ZOOM_AND_PAN])
        self.view.clicked.connect(self._clicked)

        column = QWidget(self.body_widget)
        panel = QVBoxLayout(column)
        panel.setContentsMargins(0, 0, 0, 0)
        panel.setSpacing(SPACING)

        self.question = heading(DISH_QUESTION, 3, column)
        self.question.setWordWrap(True)
        panel.addWidget(self.question)
        self.slot_8 = QRadioButton(f"Slot {DISH_SLOT}, on the picking "
                                   f"platform", column)
        self.elsewhere = QRadioButton("Somewhere else", column)
        self._where = QButtonGroup(self)
        for choice in (self.slot_8, self.elsewhere):
            self._where.addButton(choice)
            choice.toggled.connect(lambda _on: self._refresh())
            panel.addWidget(choice)
        self.dish_hint = wrapped(DISH_HINT)
        panel.addWidget(self.dish_hint)

        self.instructions = wrapped()
        panel.addWidget(self.instructions)
        self.note = Banner(CANT_SEE, tone="warn", parent=column)
        panel.addWidget(self.note)
        self.move_button = secondary_button("", column)
        self.move_button.clicked.connect(self._move)
        panel.addWidget(self.move_button)
        self.goto_button = quiet_button("Go to saved position", column)
        self.goto_button.clicked.connect(self._goto)
        panel.addWidget(self.goto_button)
        panel.addWidget(self.jog)
        panel.addStretch(1)

        self.body.addWidget(
            FeedRow(self.view, scroll_column(column, PANEL_WIDTH)), 1)

        self.done.connect(self._save)
        self.canceled.connect(self._let_go)
        self.jog.pose_changed.connect(lambda _p: self._refresh())
        session.camera_opened.connect(self._refresh_camera)
        session.camera_closed.connect(self._refresh_camera)
        session.profile_changed.connect(lambda _p: self._refresh())
        session.robot_busy_changed.connect(lambda _h: self._refresh())

    # -- opening and closing -------------------------------------------------

    def open_for(self, item: str) -> None:
        """The sheet for the position `item`; the dish on slot 8 first."""
        self.item = item
        self.title_label.setText(SETUP_TITLES[item])
        self.instructions.setText(INSTRUCTIONS[item])
        self.slot_8.setChecked(True)
        self.open()
        self.opener.ensure(self.session.upper_camera_label)
        self._refresh_camera()
        self._refresh()

    def discard(self) -> None:
        """Closed by going elsewhere: as ×, nothing is saved."""
        if not self.isHidden():
            self.hide()
            self._let_go()

    def _let_go(self) -> None:
        self.view.set_camera(None)

    # -- the position --------------------------------------------------------

    @property
    def name(self) -> str:
        """The position's stored name in the profile."""
        return SETUP_POSITIONS[self.item]

    def _stored(self):
        profile = self.session.profile
        return None if profile is None else profile.positions.get(self.name)

    def _target_slot(self) -> int | None:
        """The slot "Move camera to" goes over, or None for none."""
        if self.item == "dish_position":
            return DISH_SLOT if self.slot_8.isChecked() else None
        if self.item == "calibration_position":
            profile = self.session.profile
            modules = profile.deck.modules if profile is not None else []
            return next((min(m.slots) for m in modules
                         if m.name == CALIBRATION_MODULE), None)
        return None

    def _move(self) -> None:
        slot = self._target_slot()
        if slot is not None:
            move_over_slot(self.session, self.jog, slot)

    def _clicked(self, u: float, v: float) -> None:
        move_to_pixel(self.session, self.jog, u, v, no_map=USE_THE_ARROWS)

    def _goto(self) -> None:
        """Back to the stored pose, no higher than the travel height."""
        where = self._stored()
        if where is None:
            return
        motion = self.session.motion

        def job(log_):
            motion.tip_to(where[:2], where[2], log=log_)
            return "At the saved position."

        self.jog.run_job(job, what=f"going to {self.name}")

    def _save(self) -> None:
        """Where the robot is, read from it and remembered."""
        self._let_go()
        robot = self.session.robot
        if robot is None:
            return
        if not self.jog.run_job(lambda _log: xyz(robot), moves=False,
                                what=f"reading the {self.name} pose",
                                then=self._remember):
            self.said.emit(self.session.robot_lease.refusal)

    def _remember(self, pose) -> str:
        try:
            self.session.remember(self.name, pose)
        except Exception as exc:                     # noqa: BLE001
            log.error("%s not saved: %s", self.name, exc)
            self.said.emit(describe_error(exc))
            return describe_error(exc)
        said = f"{SETUP_TITLES[self.item]} saved."
        self.said.emit(said)
        return said

    # -- display -------------------------------------------------------------

    def _refresh_camera(self, _label: str = "") -> None:
        """The upper camera on the picture while the sheet is shown."""
        label = self.session.upper_camera_label
        camera = (None if self.isHidden() or label is None
                  else self.session.camera(label))
        if camera is not self.view.camera:
            self.view.set_camera(camera)

    def _refresh(self) -> None:
        dish = self.item == "dish_position"
        for widget in (self.question, self.slot_8, self.elsewhere,
                       self.dish_hint):
            widget.setVisible(dish)
        self.note.setVisible(self.item == "shake_position")
        slot = self._target_slot()
        self.move_button.setVisible(slot is not None)
        self.move_button.setText(
            "Move camera to the calibration module"
            if self.item == "calibration_position"
            else f"Move camera to slot {slot}")
        lease = self.session.robot_lease
        free = not lease.busy and self.session.robot is not None
        self.move_button.setEnabled(free)
        self.goto_button.setVisible(self._stored() is not None)
        self.goto_button.setEnabled(free)
        self.done_button.setEnabled(free and self.jog.pose is not None)
        self.done_button.setToolTip(
            f"Robot busy: {lease.holder}." if lease.busy else "")
