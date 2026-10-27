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
- **Shake position** (`shake`, #34) carries its prerequisites: a tip box
  on the deck (a type from the drop-down, the Settings' tip calibration
  type first; a click on the mini deck loads it into that slot as the
  Deck step would) -> put a tip on -> calibrate the tip (the Tip step's
  own blocks, `TipOnBlock` and `TipCalibrationBlock`, and its run, under
  `SHAKE_HOLDER`) -> the source dish in its holder (It is there) -> which
  slot the holder is in -> a click on the hole sends the tip over it at
  the travel height (`camera_moves.move_tip_to_pixel`) -> lower the tip
  with the pad (It is in the hole) -> Save position. The tip stays on.
- **Position over the dish** (`observe`): place the source dish on its
  platform (It is there) -> which slot -> center the dish rim around the
  crosshair (It is centered) -> Save position.

Each position's steps are data (`STEPS`: a list of `Step`s), and a step
is built by its kind (`PositionSheet.builders`: "confirm", "slot",
"save"): a step list with kinds of its own adds a builder for each. A
built step is a `Built`: its card, and how it shows itself for where the
operator is (`refresh(reached, free)`). A step's button is gray until the
steps before it are done; it opens the next (`PositionSheet.next_step`).
A step whose done is a fact of the robot - a tip box on the deck, a tip
on, the tip calibrated - says so (`Built.done`): it is done, and the next
opens, whenever that holds, and the sheet goes back to it when it stops
holding (a tip dropped). So with a calibrated tip on, the shake
position opens on its fourth step.

The two tip blocks are made once and kept (their run outlives a close,
as the camera calibration's sweep does); a build moves their boxes into
its cards. While the sheet shows the shake position the picture is
theirs: the lower camera while the tip is measured.

**Save position** is the only thing that stores: where the robot is,
read from it through the jog panel's `run_job`, under the position's
stored name (`Session.remember`), then "Saved: slot N · X Y Z" - the
slot clicked on the mini deck, else "≈ slot N", the nearest, as Robot
setup's rows say it. Go to saved position marks that nearest slot on the
mini deck. **Done** and × only
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

import functools
import logging
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QLabel, QPushButton, QVBoxLayout,
                               QWidget)

from ...config.labware import (LabwareError, local_definitions,
                               resolve_definition, shared_definitions)
from ...core.deck import nearest_slot
from ...hardware.protocols import xyz
from ..auto_camera import CameraOpener
from ..camera_moves import move_over_slot, move_tip_to_pixel, move_to_pixel
from ..session import Session
from ..tasks import SETUP_POSITIONS, SETUP_TITLES
from ..theme import SPACING
from ..theme.factory import (muted_label, primary_button, quiet_button,
                             scroll_column, select)
from ..widgets.banner import Banner
from ..widgets.camera_view import ZOOM_AND_PAN, CameraView
from ..widgets.checklist import BLOCKED, OK, TODO
from ..widgets.feed_row import FeedRow
from ..widgets.jog_panel import JogPanel
from ..widgets.overlays import SheetWindow
from ..widgets.slot_chooser import SlotChooser
from ..widgets.step_card import StepCard, wrapped
from ..workers import describe_error
from .calibration_pipette import TipCalibrationBlock, TipOnBlock

__all__ = ["Built", "PositionSheet", "SHAKE_HOLDER", "STEPS", "Step",
           "saved_text", "tip_box_types"]

log = logging.getLogger(__name__)

PANEL_WIDTH = 420

CANT_SEE = "The camera can't see this. Watch the robot itself."
USE_THE_ARROWS = "Use the arrows to move the camera."
SLOT_HINT = "Click the slot. The camera moves there."
BY_CLICK = "Click the picture or use the arrows."
BY_ARROWS = "Use the arrows."
TIPBOX_HINT = "Choose its type, then click its slot."
CLICK_CAMERA = "Click   move the camera there"
CLICK_TIP = "Click   move the tip there"

# What the shake position's tip calibration holds the robot for: its pill
# and its way back are `shell.RUNS[SHAKE_HOLDER]`.
SHAKE_HOLDER = "shake tip calibration"


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
    moved from here. `done`, for a step that is done by a fact rather
    than by its button: whether it holds now."""

    card: StepCard
    refresh: Callable[[bool, bool], None]
    done: Callable[[], bool] | None = None


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
        Step("tipbox", "tipbox", "Put a tip box on the deck", TIPBOX_HINT),
        Step("tip", "tip_on", "Put a tip on"),
        Step("calibrate", "tip_calibration", "Calibrate the tip"),
        Step("holder", "confirm", "Put the source dish in its holder",
             "Put the source dish in the holder on the deck.",
             "It is there"),
        Step("slot", "slot", "Which slot is the holder in?", SLOT_HINT),
        Step("hole", "hole", "Click the hole on the picture",
             "The tip goes over it."),
        Step("lower", "confirm", "Lower the tip into the hole",
             "Use the arrows until the tip is in the hole.",
             "It is in the hole"),
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
    """`Saved: slot 8 · X 245.1 Y 132.4 Z 80.0` for the slot clicked; with
    none, `Saved: ≈ slot 8 · …`, the slot nearest the pose."""
    x, y, z = pose
    where = (f"≈ slot {nearest_slot(x, y)}" if slot is None
             else f"slot {slot}")
    return f"Saved: {where} · X {x:.1f} Y {y:.1f} Z {z:.1f}"


@functools.lru_cache(maxsize=1)
def tip_box_types() -> tuple:
    """Every tip box definition the Deck step knows, custom first then
    stock, by display name. Read once: neither list changes while the
    program runs."""
    try:
        custom = local_definitions()
    except LabwareError as exc:
        log.error("custom labware: %s", exc)
        custom = {}
    try:
        stock = shared_definitions()
    except LabwareError as exc:
        log.error("stock labware: %s", exc)
        stock = {}
    known = {**custom, **{n: d for n, d in stock.items() if n not in custom}}
    return tuple(sorted((d for d in known.values() if d.is_tiprack),
                        key=lambda d: d.display_name))


def tip_box_slots(session) -> list[int]:
    """The slots the robot session holds a tip box in, in order."""
    state = session.run_state
    if session.robot is None or state is None:
        return []
    out = []
    for slot, entry in state.labware.items():
        try:
            if resolve_definition(entry.load_name).is_tiprack:
                out.append(int(slot))
        except (LabwareError, ValueError):
            continue
    return sorted(out)


def _slots_text(slots: list[int]) -> str:
    if len(slots) == 1:
        return f"Tip box in slot {slots[0]}."
    return ("Tip boxes in slots " + ", ".join(map(str, slots[:-1]))
            + f" and {slots[-1]}.")


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
        self.buttons: dict[str, QPushButton] = {}
        self._built: list[Built] = []
        self._refreshing = False
        self.slots: SlotChooser | None = None
        self.save_button = None
        self.saved_note: QLabel | None = None
        # The slot marked is the nearest to a pose gone to, not one clicked.
        self._slot_guessed = False
        # Kind -> fn(step, number) -> Built. A step list with kinds of its
        # own (the shake position's tip box, tip, ...) adds builders here.
        self.builders: dict[str, Callable[[Step, int], Built]] = {
            "confirm": self._confirm_step, "slot": self._slot_step,
            "save": self._save_step, "tipbox": self._tipbox_step,
            "tip_on": self._tip_on_step,
            "tip_calibration": self._tip_calibration_step,
            "hole": self._hole_step}
        # The shake position's tip box step, while built.
        self.tipbox_type = self.tipbox_slots = self.tipbox_line = None
        # The Tip step's blocks, made the first time the shake position
        # is built and kept (`_tip_blocks`).
        self.tip_on: TipOnBlock | None = None
        self.tip_calibration: TipCalibrationBlock | None = None
        self.opener = CameraOpener(session, self)

        self.view = CameraView(self.body_widget)
        self.jog = JogPanel(session, shortcut_host=self,
                            machine_controls=False,
                            parent=self.body_widget)
        self.jog.show_position_on(self.view)
        self.view.set_mouse_help(ZOOM_AND_PAN)
        self.view.set_click_to_move(CLICK_CAMERA,
                                    self.session.has_pixel_map)
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
        session.labware_changed.connect(lambda _s: self._refresh())
        session.tip_changed.connect(lambda _t: self._refresh())

    # -- opening and closing -------------------------------------------------

    def open_for(self, item: str) -> None:
        """The sheet for the position `item`, from its first step not
        done."""
        self.item = item
        self.title_label.setText(SETUP_TITLES[item])
        self._build(STEPS[item])
        self.open()
        self.opener.ensure(self.session.upper_camera_label)
        if self._shake_shown():
            self.tip_calibration.open_cameras()
        self._refresh_camera()
        self._refresh()

    def run_activity(self) -> str | None:
        """For the top bar's pill while the shake position's tip
        calibration runs: "waiting" at the disc and the fine-tune."""
        calibration = self.tip_calibration
        return None if calibration is None else calibration.run_activity()

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
        # The tip blocks are kept: their boxes out of the cards first.
        for block in (self.tip_on, self.tip_calibration):
            if block is not None:
                block.box.hide()
                block.box.setParent(self.body_widget)
        for built in self._built:
            built.card.box.setParent(None)
            built.card.box.deleteLater()
        self.steps, self.step = steps, 0
        self.cards, self.buttons, self._built = {}, {}, []
        self.slots = self.save_button = self.saved_note = None
        self._slot_guessed = False
        self.tipbox_type = self.tipbox_slots = self.tipbox_line = None
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
                how.setText(BY_CLICK if self.session.has_pixel_map()
                            else BY_ARROWS)

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

    # -- the shake position's own steps ---------------------------------------

    def _tipbox_step(self, step: Step, number: int) -> Built:
        """Done while a tip box is on the deck; else a type and a slot
        click that loads it there."""
        card = StepCard(self.column, number, step.title)
        line = self.tipbox_line = wrapped()
        card.add(line)
        hint = wrapped(step.text)
        card.add(hint)
        kinds = self.tipbox_type = select(self.column)
        # The keys over this sheet are the gantry's.
        kinds.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for definition in tip_box_types():
            kinds.addItem(definition.display_name, definition.load_name)
        wanted = kinds.findData(self.session.settings.tip_cal_tip_type.strip())
        kinds.setCurrentIndex(max(wanted, 0) if kinds.count() else -1)
        card.add(kinds)
        slots = self.tipbox_slots = SlotChooser(self.column)
        slots.slot_clicked.connect(self._load_tipbox)
        card.add(slots)

        def refresh(reached: bool, free: bool) -> None:
            on = tip_box_slots(self.session)
            line.setText(_slots_text(on) if on else "")
            line.setVisible(bool(on))
            for widget in (hint, kinds, slots):
                widget.setVisible(not on)
            kinds.setEnabled(reached and free)
            slots.setEnabled(reached and free and kinds.currentData()
                             is not None)

        return Built(card, refresh,
                     done=lambda: bool(tip_box_slots(self.session)))

    def _load_tipbox(self, slot: int) -> None:
        """The tip box type chosen into `slot`, as the Deck step loads it:
        the deck changed, the type remembered as used."""
        name = self.tipbox_type.currentData() if self.tipbox_type else None
        definition = next((d for d in tip_box_types()
                           if d.load_name == name), None)
        if definition is None or self.session.robot is None:
            return
        session = self.session

        def job(_log):
            session.load_labware(definition, slot)
            return f"{definition.display_name} is in slot {slot}."

        def loaded(said):
            if self.tipbox_slots is not None:
                self.tipbox_slots.set_marked(slot)
            session.remember_labware(definition.load_name)
            self._refresh()
            return said

        if not self.jog.run_job(job, moves=False, then=loaded,
                                what=f"loading {name} into slot {slot}"):
            self.jog.tell(session.robot_lease.refusal
                          if session.robot_lease.busy else "Robot busy.")

    def _tip_blocks(self) -> tuple[TipOnBlock, TipCalibrationBlock]:
        """The Tip step's two blocks, made once, on this sheet's picture
        and jog panel; their own headings are the step cards'."""
        if self.tip_calibration is None:
            calibration = TipCalibrationBlock(
                self.session, self.jog, self.view, holder=SHAKE_HOLDER,
                parent=self)
            tip = TipOnBlock(self.session, self.jog,
                             calibration=calibration, parent=self)
            for block in (tip, calibration):
                block.heading.hide()
                if isinstance(block.box, QFrame):
                    # Inside a step card: no card of its own.
                    block.box.setObjectName("")
                    block.box.setFrameShape(QFrame.Shape.NoFrame)
                    block.box.layout().setContentsMargins(0, 0, 0, 0)
                block.changed.connect(self._block_changed)
            self.tip_on, self.tip_calibration = tip, calibration
        return self.tip_on, self.tip_calibration

    def _block_step(self, block, step: Step, number: int) -> Built:
        card = StepCard(self.column, number, step.title)
        card.add(block.box)
        block.box.show()

        def refresh(reached: bool, _free: bool) -> None:
            # A run under way is never dimmed.
            running = getattr(block, "running", False)
            card.box.setEnabled(reached or running)

        return Built(card, refresh, done=lambda: block.done)

    def _tip_on_step(self, step: Step, number: int) -> Built:
        return self._block_step(self._tip_blocks()[0], step, number)

    def _tip_calibration_step(self, step: Step, number: int) -> Built:
        return self._block_step(self._tip_blocks()[1], step, number)

    def _hole_step(self, step: Step, number: int) -> Built:
        """Done by a click on the picture that sent the tip over it."""
        card = StepCard(self.column, number, step.title)
        card.add(wrapped(step.text))
        return Built(card, lambda _reached, _free: None)

    def _block_changed(self) -> None:
        self._camera_guard()
        if not self._refreshing:
            self._refresh()

    def _at(self, key: str) -> bool:
        """Step `key` is the one open now."""
        return (self.step < len(self.steps)
                and self.steps[self.step].key == key)

    def _move(self, key: str, slot: int) -> None:
        """The camera over the center of the slot clicked; the slot is
        marked, and the step done, once it went."""

        def moved(said):
            self.slots.set_marked(slot)
            self._slot_guessed = False
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

    def _clicked(self, u: float, v: float) -> None:
        """The camera onto the point clicked; on the shake position's hole
        step, the tip over it."""
        if self._at("hole"):
            move_tip_to_pixel(self.session, self.jog, u, v,
                              no_map=USE_THE_ARROWS,
                              then=self._over_the_hole)
            return
        move_to_pixel(self.session, self.jog, u, v, no_map=USE_THE_ARROWS)

    def _over_the_hole(self, said):
        self.next_step("hole")
        return said

    def _goto(self) -> None:
        """Back to the stored pose, no higher than the travel height; the
        steps before Save are then done, and the slot nearest the pose is
        marked on the mini deck - a guess, said as "≈ slot N"."""
        where = self._stored()
        if where is None:
            return
        motion = self.session.motion

        def job(log_):
            motion.tip_to(where[:2], where[2], log=log_)
            return "At the saved position."

        def there(said):
            if self.slots is not None:
                self.slots.set_marked(nearest_slot(*where[:2]))
                self._slot_guessed = True
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
        marked = (self.slots.marked
                  if self.slots is not None and not self._slot_guessed
                  else None)
        said = saved_text(pose, marked)
        log.info("%s saved: %s", self.name, said)
        if self.saved_note is not None:
            self.saved_note.setText(said)
        self.step = len(self.steps)
        self._refresh()
        self.said.emit(said)
        return said

    # -- display -------------------------------------------------------------

    def _shake_shown(self) -> bool:
        """The sheet shows the shake position, whose picture is the tip
        calibration's."""
        return (self.item == "shake_position" and not self.isHidden()
                and self.tip_calibration is not None)

    def _refresh_camera(self, _label: str = "") -> None:
        """The upper camera on the picture while the sheet is shown; on
        the shake position, the camera the tip calibration looks through."""
        if self._shake_shown():
            self.tip_calibration.refresh()      # it sets the picture
            return
        self._camera_guard()

    def _camera_guard(self) -> None:
        """The picture as this sheet has it, unless it is the tip
        calibration's: nothing while hidden, else the upper camera."""
        if self._shake_shown():
            return
        label = self.session.upper_camera_label
        camera = (None if self.isHidden() or label is None
                  else self.session.camera(label))
        if camera is not self.view.camera:
            self.view.set_camera(camera)

    def _follow_facts(self) -> None:
        """A step done by a fact opens the next while it holds; the sheet
        goes back to it once it does not."""
        for i, built in enumerate(self._built):
            if built.done is None:
                continue
            if not built.done():
                self.step = min(self.step, i)
                return
            if self.step == i:
                self.step = i + 1

    def _refresh(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        try:
            self._show()
        finally:
            self._refreshing = False

    def _show(self) -> None:
        self._follow_facts()
        self.note.setVisible(self._at("lower"))
        self.view.set_click_to_move(
            CLICK_TIP if self._at("hole") else CLICK_CAMERA,
            self.session.has_pixel_map)
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
