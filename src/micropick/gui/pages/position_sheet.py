"""Robot setup's position sheets (REDESIGN §5, #12, #33): the calibration
position, the shake position and the position over the dish.

One sheet for the three, opened from a Positions row (or a step that
needs one): the upper camera's live picture on the left, the jog pad
floating over it, and on the right the position's **steps**, each a
numbered card with a mark (`widgets.step_card`, as Camera calibration's),
opened one after another by the operator's answer:

- **Calibration position** (`tip_calib`, where the tip calibration
  starts - no new pose): place the crosshair disc on the calibration
  module (It is there) -> which slot (the mini deck: a click moves the
  camera over it) -> center the crosshair (It is centered) -> Save
  position.
- **Shake position** (`shake`): lower the tip into the hole of the
  holder (It is in the hole; the camera can't see it) -> Save position.
- **Position over the dish** (`observe`): place the source dish on its
  platform (It is there) -> which slot -> center the dish rim around the
  crosshair (It is centered) -> Save position.

Each position's steps are data (`STEPS`: a list of `Step`s), and a step
is built by its kind (`PositionSheet.builders`: "confirm", "slot",
"save"): a step list with kinds of its own adds a builder for each. A
built step is a `Built`: its card, and how it shows itself for where the
operator is (`refresh(reached, free)`). A step's button is gray until the
steps before it are done; it opens the next (`PositionSheet.next_step`).

**Save position** is the only thing that stores: where the robot is,
read from it through the jog panel's `run_job`, under the position's
stored name (`Session.remember`), then "Saved: slot N · X Y Z" - the
slot chosen on the mini deck, else the nearest. **Done** and × only
close: closing without Save stores nothing and asks nothing.

A position already saved shows its line on top and **Go to saved
position**, which goes back there no higher than the travel height
(`Motion.tip_to`: a pose taught after a retract is out of bounds for the
robot) and marks every step before Save done - saving again is one click.

A click on the picture moves the camera there through the pixel map
(`camera_moves.move_to_pixel`); before the first camera calibration it
moves nothing, and the arrows do it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ...core.deck import nearest_slot
from ...hardware.protocols import xyz
from ..auto_camera import CameraOpener
from ..camera_moves import move_over_slot, move_to_pixel
from ..session import Session
from ..tasks import SETUP_POSITIONS, SETUP_TITLES
from ..theme import SPACING
from ..theme.factory import muted_label, primary_button, quiet_button, \
    scroll_column
from ..widgets.banner import Banner
from ..widgets.camera_view import ZOOM_AND_PAN, CameraView
from ..widgets.checklist import BLOCKED, OK, TODO
from ..widgets.feed_row import FeedRow
from ..widgets.jog_panel import JogPanel
from ..widgets.overlays import SheetWindow
from ..widgets.slot_chooser import SlotChooser
from ..widgets.step_card import StepCard, wrapped
from ..workers import describe_error

__all__ = ["Built", "PositionSheet", "STEPS", "Step", "saved_text"]

log = logging.getLogger(__name__)

PANEL_WIDTH = 420

CANT_SEE = "The camera can't see this. Watch the robot itself."
USE_THE_ARROWS = "Use the arrows to move the camera."
SLOT_HINT = "Click the slot. The camera moves there."
BY_CLICK = "Click the picture or use the arrows."
BY_ARROWS = "Use the arrows."


@dataclass(frozen=True)
class Step:
    """One numbered step: `key` names it on the sheet (`buttons[key]`,
    `cards[key]`), `kind` says how it is built (`PositionSheet.builders`).
    A "confirm" step is a sentence and a button that opens the next; a
    `mapped` sentence says that a click on the picture moves the camera,
    which is only so once there is a pixel map."""

    key: str
    kind: str
    title: str
    text: str = ""
    button: str = ""
    mapped: bool = False


@dataclass
class Built:
    """A step as built: its card, and `refresh(reached, free)` - `reached`
    while the steps before it are done, `free` while the robot can be
    moved from here."""

    card: StepCard
    refresh: Callable[[bool, bool], None]


_SLOT = Step("slot", "slot", "Which slot is it in?", SLOT_HINT)
_SAVE = Step("save", "save", "Save position")

# Each position's steps, in order. The last is Save position.
STEPS: dict[str, tuple[Step, ...]] = {
    "calibration_position": (
        Step("place", "confirm", "Place the crosshair disc",
             "Put the crosshair disc on the calibration module.",
             "It is there"),
        _SLOT,
        Step("center", "confirm", "Center the crosshair",
             "Bring the crosshair to the middle of the picture.",
             "It is centered", mapped=True),
        _SAVE),
    "shake_position": (
        Step("lower", "confirm", "Lower the tip into the hole",
             "Put the dish in its holder. Lower the tip into the hole of "
             "the holder.", "It is in the hole"),
        _SAVE),
    "dish_position": (
        Step("place", "confirm", "Place the source dish",
             "Put the source dish on its platform.", "It is there"),
        _SLOT,
        Step("center", "confirm", "Center the dish rim around the crosshair",
             "The whole dish should be in the picture.", "It is centered",
             mapped=True),
        _SAVE),
}


def saved_text(pose, slot: int | None = None) -> str:
    """`Saved: slot 8 · X 245.1 Y 132.4 Z 80.0`; the slot nearest the pose
    unless `slot` is given."""
    x, y, z = pose
    slot = nearest_slot(x, y) if slot is None else slot
    return f"Saved: slot {slot} · X {x:.1f} Y {y:.1f} Z {z:.1f}"


class PositionSheet(SheetWindow):
    """`open_for(item)`, one of `tasks.SETUP_POSITIONS`; `said(text)` after
    Save position. `step` is the index of the step open now in `steps`."""

    said = Signal(str)

    def __init__(self, session: Session, host: QWidget):
        super().__init__(host, "")
        self.session = session
        self.item = "calibration_position"
        self.steps: tuple[Step, ...] = ()
        self.step = 0
        self.cards: dict[str, StepCard] = {}
        self.buttons: dict = {}
        self._built: list[Built] = []
        self.slots: SlotChooser | None = None
        self.save_button = None
        self.saved_note: QLabel | None = None
        # Kind -> fn(step, number) -> Built. A step list with kinds of its
        # own (the shake position's tip box, tip, ...) adds builders here.
        self.builders: dict[str, Callable[[Step, int], Built]] = {
            "confirm": self._confirm_step, "slot": self._slot_step,
            "save": self._save_step}
        self.opener = CameraOpener(session, self)

        self.view = CameraView(self.body_widget)
        self.jog = JogPanel(session, shortcut_host=self,
                            machine_controls=False, collapsed=("positions",),
                            parent=self.body_widget)
        self.jog.show_position_on(self.view)
        self.view.set_mouse_help(ZOOM_AND_PAN)
        self.view.set_click_to_move("Click   move the camera there",
                                    self._mapped)
        self.view.clicked.connect(self._clicked)

        self.column = column = QWidget(self.body_widget)
        self.panel = panel = QVBoxLayout(column)
        panel.setContentsMargins(0, 0, 0, 0)
        panel.setSpacing(SPACING)

        # What is saved now, and the way back to it.
        self.saved_line = muted_label("", column)
        panel.addWidget(self.saved_line)
        self.goto_button = quiet_button("Go to saved position", column)
        self.goto_button.clicked.connect(self._goto)
        panel.addWidget(self.goto_button)
        self.note = Banner(CANT_SEE, tone="warn", parent=column)
        panel.addWidget(self.note)
        # The step cards go between here and the jog panel (`_build`).
        self._cards_at = panel.count()
        panel.addWidget(self.jog)
        panel.addStretch(1)

        self.body.addWidget(
            FeedRow(self.view, scroll_column(column, PANEL_WIDTH)), 1)

        # Done only closes, as × does; Save position is the one action.
        self.cancel_button.hide()
        self.done_button.setObjectName("secondary")
        self.done_button.setDefault(False)
        self.close_button.setToolTip("Close")
        self.done.connect(self._let_go)
        self.canceled.connect(self._let_go)
        self.jog.pose_changed.connect(lambda _p: self._refresh())
        session.camera_opened.connect(self._refresh_camera)
        session.camera_closed.connect(self._refresh_camera)
        session.profile_changed.connect(lambda _p: self._refresh())
        session.robot_busy_changed.connect(lambda _h: self._refresh())

    # -- opening and closing -------------------------------------------------

    def open_for(self, item: str) -> None:
        """The sheet for the position `item`, from its first step."""
        self.item = item
        self.title_label.setText(SETUP_TITLES[item])
        self._build(STEPS[item])
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

    # -- the steps -----------------------------------------------------------

    def _build(self, steps: tuple[Step, ...]) -> None:
        """The cards of `steps`, in place of the last position's."""
        for built in self._built:
            built.card.box.setParent(None)
            built.card.box.deleteLater()
        self.steps, self.step = steps, 0
        self.cards, self.buttons, self._built = {}, {}, []
        self.slots = self.save_button = self.saved_note = None
        for number, step in enumerate(steps, start=1):
            built = self.builders[step.kind](step, number)
            self.panel.insertWidget(self._cards_at + number - 1,
                                    built.card.box)
            self.cards[step.key] = built.card
            self._built.append(built)

    def index(self, key: str) -> int:
        return next(i for i, s in enumerate(self.steps) if s.key == key)

    def next_step(self, key: str) -> None:
        """Step `key` is done: the one after it opens."""
        self.step = max(self.step, self.index(key) + 1)
        self._refresh()

    def _confirm_step(self, step: Step, number: int) -> Built:
        card = StepCard(self.column, number, step.title)
        text = wrapped(step.text)
        card.add(text)
        how = wrapped() if step.mapped else None
        if how is not None:
            card.add(how)
        button = primary_button(step.button, self.column)
        button.clicked.connect(lambda: self.next_step(step.key))
        card.add(button)
        self.buttons[step.key] = button

        def refresh(reached: bool, _free: bool) -> None:
            button.setEnabled(reached)
            if how is not None:
                how.setText(BY_CLICK if self._mapped() else BY_ARROWS)

        return Built(card, refresh)

    def _slot_step(self, step: Step, number: int) -> Built:
        card = StepCard(self.column, number, step.title)
        card.add(wrapped(step.text))
        slots = self.slots = SlotChooser(self.column)
        slots.slot_clicked.connect(lambda slot: self._move(step.key, slot))
        card.add(slots)

        def refresh(reached: bool, free: bool) -> None:
            slots.setEnabled(reached and free)
            lease = self.session.robot_lease
            slots.setToolTip(f"Robot busy: {lease.holder}." if lease.busy
                             else "")

        return Built(card, refresh)

    def _save_step(self, step: Step, number: int) -> Built:
        card = StepCard(self.column, number, step.title)
        button = self.save_button = primary_button("Save position",
                                                   self.column)
        button.clicked.connect(self._save)
        card.add(button)
        note = self.saved_note = muted_label("", self.column)
        note.setWordWrap(True)
        card.add(note)

        def refresh(reached: bool, free: bool) -> None:
            button.setEnabled(reached and free and self.jog.pose is not None)

        return Built(card, refresh)

    def _move(self, key: str, slot: int) -> None:
        """The camera over the center of the slot clicked; the slot is
        marked, and the step done, once it went."""

        def moved(said):
            self.slots.set_marked(slot)
            self.next_step(key)
            return said

        move_over_slot(self.session, self.jog, slot, then=moved)

    # -- the position --------------------------------------------------------

    @property
    def name(self) -> str:
        """The position's stored name in the profile."""
        return SETUP_POSITIONS[self.item]

    def _stored(self):
        profile = self.session.profile
        return None if profile is None else profile.positions.get(self.name)

    def _mapped(self) -> bool:
        """Whether a click on the picture can move the robot: only through
        the profile's pixel map, so not before the first camera
        calibration."""
        profile = self.session.profile
        return profile is not None and profile.pixel_map is not None

    def _clicked(self, u: float, v: float) -> None:
        move_to_pixel(self.session, self.jog, u, v, no_map=USE_THE_ARROWS)

    def _goto(self) -> None:
        """Back to the stored pose, no higher than the travel height; the
        steps before Save are then done."""
        where = self._stored()
        if where is None:
            return
        motion = self.session.motion

        def job(log_):
            motion.tip_to(where[:2], where[2], log=log_)
            return "At the saved position."

        def there(said):
            self.step = max(self.step, len(self.steps) - 1)
            self._refresh()
            return said

        self.jog.run_job(job, what=f"going to {self.name}", then=there)

    def _save(self) -> None:
        """Where the robot is, read from it and remembered."""
        if self.session.robot is None:
            return
        robot = self.session.robot
        if not self.jog.run_job(lambda _log: xyz(robot), moves=False,
                                what=f"reading the {self.name} pose",
                                then=self._remember):
            self.said.emit(self.session.robot_lease.refusal)

    def _remember(self, pose) -> str:
        try:
            self.session.remember(self.name, pose)
        except Exception as exc:                     # noqa: BLE001
            log.error("%s not saved: %s", self.name, exc)
            said = describe_error(exc)
            if self.saved_note is not None:
                self.saved_note.setText(said)
            self.said.emit(said)
            return said
        marked = self.slots.marked if self.slots is not None else None
        said = saved_text(pose, marked)
        log.info("%s saved: %s", self.name, said)
        if self.saved_note is not None:
            self.saved_note.setText(said)
        self.step = len(self.steps)
        self._refresh()
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
        self.note.setVisible(self.item == "shake_position")
        stored = self._stored()
        self.saved_line.setVisible(stored is not None)
        if stored is not None:
            self.saved_line.setText(saved_text(stored))
        lease = self.session.robot_lease
        free = not lease.busy and self.session.robot is not None
        self.goto_button.setVisible(stored is not None)
        self.goto_button.setEnabled(free)
        for i, built in enumerate(self._built):
            built.refresh(i <= self.step, free)
            if i < self.step:
                built.card.set_state(OK)
            elif i == self.step:
                built.card.set_state(TODO)
            else:
                built.card.set_state(BLOCKED, f"First: step {self.step + 1}.")
