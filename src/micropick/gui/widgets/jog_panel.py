"""Driving the robot by hand.

A third input backend for `JogController`, after the cv2 window and the global
hotkeys, and it shares the controller rather than the loop: the step sizes, the
soft limits, the saved positions and the undo history all live there, and this
widget only decides what a click and a key press mean.

A widget rather than a page, because four pages need it. Manual control is
one; the first step of either calibration is another, where a target has to
be centred before anything can be measured; and the check page needs it to
come back to the pose the camera was looking from. Those are the same act,
and a second D-pad would be a second place for the step list and the soft
limits to drift.

Two sections of it fold
-----------------------
Not every page that needs the panel needs all of it. Centring a marker is
the D-pad and nothing else; the tip calibration usually drives to a stored
position and never jogs at all; the check page wants the stored positions
above everything. So Move and Positions are `theme.factory.Section`s and
each page says which of them start folded, rather than four panels or one
panel that guesses. A folded section is hidden, not merely empty, so it
costs no height on a short screen.

Positions are the profile's
---------------------------
There was a second, parallel store: `JogController.saved`, in memory, with
generated names, emptied on every disconnect - and beside it a "Remember in
profile" button writing to `profile.positions`, which is what everything
else reads. Two lists, one visible and one useful. The visible one is gone:
this list is `profile.positions`, with its coordinates beside each name,
and saving, renaming and deleting all write to the profile. `tip_calib`
taught by the pipette calibration, or by the notebook, appears here and can
be driven to, which is the point of having named a pose at all.

`shortcut_host` is the widget the key bindings are registered on, normally the
page. They are `WindowShortcut`: while the panel is on screen the keys reach
the robot from anywhere in the window, whatever has focus — the camera view,
a page tab, a combo box that was just clicked. The cv2 window
behaves the same way, and it is what an operator with one hand on the keyboard
and eyes on the picture expects; a shortcut that works only while the right
widget has focus is one that silently stops working after a click somewhere
else. They are disabled while the panel is hidden, because two pages each
holding one would otherwise both claim the arrow keys and leave Qt to guess
between them.

The layout is jog's, not a new one
----------------------------------
Keys come from `jog.LAYOUT` and the help from `jog.help_lines`, both of them
generated rather than restated. `LAYOUT` exists because the help and the
dispatch had drifted once and the on-screen text described WASD for axes the
operator drove with the arrows; inventing a third arrangement here would be
that same drift with an extra window in it. So Z is on PgUp and PgDn as it is
everywhere else, and there is no WASD.

`KEY_TO_QT` is checked against `LAYOUT` at import. A key in the layout with no
Qt equivalent would otherwise produce a line of help, and a labelled button,
for a key that does nothing — the same fault seen from the other side.

One key of the layout has no counterpart here, and it is listed rather than
dropped: `enter` finishes the cv2 window's loop, and this page has no loop to
finish. It is named in `UNBOUND`, with its reason, and filtered out of the
generated help so the help does not promise it.

Every move goes to a worker
---------------------------
`move_relative` blocks on HTTP. In the GUI thread that is a freeze on every
step, so each one runs in a `Worker` and the controls are disabled until it
returns. `JogController` already refuses a second move while one is in flight —
that is what stops a held-down arrow queueing moves that keep running after the
key is released — and this neither repeats that guard nor works around it.

Where the gantry is goes on the picture
---------------------------------------
The panel has no readout of its own. It says where the gantry is, and what a
refused or clamped step was, through `position_changed`, and the page hands
that to its `CameraView`, which draws it in a box under the resolution. The
operator jogging is watching the picture; a readout at the other side of the
window was one more place to look.

The keys are listed there too, in a box in the picture's bottom-right corner
(`help_changed`, `CameraView.set_help`), shown until H hides it. A page adds
its own keys and mouse actions to the same list with `add_help`.
"""

from __future__ import annotations

import logging
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase, QKeySequence, QShortcut
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QInputDialog,
                               QLabel, QListWidget, QListWidgetItem,
                               QMessageBox, QVBoxLayout, QWidget)

from ...workflows.jog import (DEFAULT_STEPS, LAYOUT, JogController,
                              help_lines)
from ..session import Session
from ..theme import SPACING
from ..theme.factory import (Section, card, combo_box, heading,
                             primary_button, secondary_button)

# What the picture says while there is no robot to ask.
NO_ROBOT = "no robot — connect it on the Profile page"
from ..workers import Worker

__all__ = ["JogPanel", "KEY_TO_QT", "UNBOUND", "SECTIONS"]

log = logging.getLogger(__name__)

# The position's name rides on its row, so the text can carry coordinates
# without the name having to be parsed back out of it.
POSITION_ROLE = Qt.ItemDataRole.UserRole


def _position_row(name: str, where) -> str:
    return f"{name:<14} {where[0]:8.2f} {where[1]:8.2f} {where[2]:7.2f}"


PANEL_WIDTH = 400

# The sections a page may ask to start folded, by name.
SECTIONS = ("move", "positions")

# Every name in jog.LAYOUT, spelled in Qt. The aliases match jog._CODES: "+"
# and "=" are one key on most keyboards, and Enter arrives as either.
KEY_TO_QT: dict[str, tuple] = {
    "left": (Qt.Key.Key_Left,),
    "right": (Qt.Key.Key_Right,),
    "up": (Qt.Key.Key_Up,),
    "down": (Qt.Key.Key_Down,),
    "PgUp": (Qt.Key.Key_PageUp,),
    "PgDn": (Qt.Key.Key_PageDown,),
    "+": (Qt.Key.Key_Plus, Qt.Key.Key_Equal),
    "-": (Qt.Key.Key_Minus, Qt.Key.Key_Underscore),
    "space": (Qt.Key.Key_Space,),
    "u": (Qt.Key.Key_U,),
    "h": (Qt.Key.Key_H,),
    "enter": (Qt.Key.Key_Return, Qt.Key.Key_Enter),
}

# Layout entries this backend deliberately does not bind, and why. Listed
# rather than omitted: an entry that is simply absent is indistinguishable from
# one that was forgotten.
UNBOUND: dict[str, str] = {
    "finish": "the cv2 window's loop ends on Enter; this page has no loop",
}

# The commands this backend implements. Checked against the layout below.
HANDLED = {"step_up", "step_down", "save", "undo", "help"}


def _check_layout() -> None:
    """Fail at import rather than at a key press that does nothing."""
    missing = [key.name for key in LAYOUT if key.name not in KEY_TO_QT]
    if missing:
        raise RuntimeError(
            f"jog.LAYOUT has keys with no Qt equivalent in KEY_TO_QT: "
            f"{', '.join(missing)}. Add them there, or the help will list a "
            f"key that does nothing.")
    unknown = [key.command for key in LAYOUT
               if key.command is not None
               and key.command not in HANDLED and key.command not in UNBOUND]
    if unknown:
        raise RuntimeError(
            f"jog.LAYOUT has commands this page neither handles nor declares "
            f"unbound: {', '.join(unknown)}. Implement them in _command, or "
            f"name them in UNBOUND with a reason.")


_check_layout()


def _reflow(line: str) -> str:
    """One line of jog's help for a proportional font: the padding it is laid
    out with only lines the columns up in a fixed one."""
    keys, _, text = line.strip().partition(" ")
    return f"{keys}   {text.strip()}"


def _bound_layout() -> tuple:
    """The layout minus what this backend does not bind, for the help."""
    return tuple(key for key in LAYOUT if key.command not in UNBOUND)


class JogPanel(QWidget):
    # Where the gantry is, and what the last step ran into, as lines for
    # `CameraView.set_position`. Emitted on every change; `position_lines`
    # holds the latest for a view connected later.
    position_changed = Signal(list)
    # A job that may have moved the gantry has finished. A page with
    # detections drawn on the picture drops them: they were measured from
    # where the camera was.
    moved = Signal()
    # The key list for the picture, or [] while H has it hidden.
    help_changed = Signal(list)

    def __init__(self, session: Session, *,
                 shortcut_host: QWidget | None = None,
                 machine_controls: bool = True,
                 collapsed: tuple[str, ...] = (),
                 parent: QWidget | None = None):
        """`collapsed` names the sections that start folded; see SECTIONS.

        A name that is not a section is a caller's typo rather than a new
        section, so it is refused here instead of quietly doing nothing.
        """
        super().__init__(parent)
        unknown = [name for name in collapsed if name not in SECTIONS]
        if unknown:
            raise ValueError(f"no such jog panel section(s): "
                             f"{', '.join(unknown)}; have {', '.join(SECTIONS)}")
        self.session = session
        self.controller: JogController | None = None
        self._worker: Worker | None = None
        self._shortcuts: list[QShortcut] = []
        self._movers: list[QWidget] = []
        self._collapsed = tuple(collapsed)
        self._status = NO_ROBOT
        self._message = ""
        self._job_moves = True
        self._help = [_reflow(line) for line in help_lines(_bound_layout())]
        self._help_shown = True

        # Not a scroll area itself. The page that hosts it may put it in one
        # (theme.factory.scroll_column), which takes no focus, so PageUp and
        # PageDown - the Z axis - still reach the window-level shortcuts.
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        column.addWidget(self._move_card())
        column.addWidget(self._positions_card())
        if machine_controls:
            # Off during a calibration: homing halfway through centring the
            # marker throws away the pose the whole sweep is planned around.
            column.addWidget(self._machine_card())
        else:
            self.home_button = self.retract_button = None
        column.addStretch(1)

        self._install_shortcuts(shortcut_host or self)

        session.robot_state_changed.connect(self._on_robot_changed)
        session.profile_changed.connect(self._on_profile_changed)

        self._on_robot_changed(session.robot_state)

    # -- construction --------------------------------------------------------

    def _move_card(self) -> QWidget:
        box = Section("Move", collapsed="move" in self._collapsed, parent=self)
        self.move_section = box

        grid = QGridLayout()
        grid.setSpacing(SPACING // 2)
        pad = {("y", +1): (0, 1, "↑ Y"), ("y", -1): (2, 1, "↓ Y"),
               ("x", -1): (1, 0, "← X"), ("x", +1): (1, 2, "X →")}
        for (axis, direction), (row, col, text) in pad.items():
            grid.addWidget(self._move_button(text, axis, direction), row, col)
        grid.addWidget(self._move_button("↑ Z", "z", +1), 0, 3)
        grid.addWidget(self._move_button("↓ Z", "z", -1), 2, 3)
        grid.setColumnMinimumWidth(3, 90)
        box.body.layout().addLayout(grid)

        step = QHBoxLayout()
        step.addWidget(QLabel("Step"))
        self.step_choice = combo_box(self)
        for value in DEFAULT_STEPS:
            self.step_choice.addItem(f"{value:g} mm", value)
        self.step_choice.currentIndexChanged.connect(self._step_chosen)
        step.addWidget(self.step_choice, 1)
        smaller = secondary_button("−", self)
        smaller.clicked.connect(lambda: self._command("step_down"))
        bigger = secondary_button("+", self)
        bigger.clicked.connect(lambda: self._command("step_up"))
        step.addWidget(smaller)
        step.addWidget(bigger)
        self._movers += [smaller, bigger, self.step_choice]
        box.body.layout().addLayout(step)

        # Undo belongs with the steps it undoes. It sat under Positions,
        # where "Undo" beside a list of saved poses read as if it would undo
        # the saving; here, next to the D-pad, and named for what it does.
        self.undo_button = secondary_button("Undo step", self)
        self.undo_button.setToolTip(
            "Reverse the last jog step. Going to a position is not a step "
            "and is not undone.")
        self.undo_button.clicked.connect(lambda: self._command("undo"))
        self._movers.append(self.undo_button)
        box.body.layout().addWidget(self.undo_button)
        return box

    def _move_button(self, text: str, axis: str, direction: int):
        button = secondary_button(text, self)
        button.clicked.connect(lambda: self._move(axis, direction))
        button.setAutoRepeat(False)      # the controller refuses a queue anyway
        self._movers.append(button)
        return button

    def _positions_card(self) -> QWidget:
        """The profile's named poses: what is stored, where, and how to go.

        Double click drives to one, because that is what a list of places
        invites; the button is there for anyone who does not try it.
        """
        box = Section("Positions", collapsed="positions" in self._collapsed,
                      parent=self)
        self.positions_section = box

        self.saved = QListWidget(self)
        # PageUp and PageDown belong to the Z axis on this page, and a list
        # with focus would scroll on them instead.
        self.saved.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.saved.setMinimumHeight(110)
        self.saved.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.saved.itemDoubleClicked.connect(lambda _item: self._goto())
        self.saved.currentItemChanged.connect(lambda *_: self._refresh_buttons())
        box.body.layout().addWidget(self.saved)

        row = QHBoxLayout()
        self.goto_button = secondary_button("Go to", self)
        self.goto_button.clicked.connect(self._goto)
        self.rename_button = secondary_button("Rename…", self)
        self.rename_button.clicked.connect(self._rename)
        self.delete_button = secondary_button("Delete", self)
        self.delete_button.clicked.connect(self._delete)
        for button in (self.goto_button, self.rename_button,
                       self.delete_button):
            row.addWidget(button)
        self._movers.append(self.goto_button)
        box.body.layout().addLayout(row)

        self.remember_button = primary_button("Save this position…", self)
        self.remember_button.setToolTip(
            "Store where the gantry is now, under a name, in the profile.")
        self.remember_button.clicked.connect(self._remember)
        self._movers.append(self.remember_button)
        box.body.layout().addWidget(self.remember_button)

        self.positions_note = QLabel()
        self.positions_note.setWordWrap(True)
        box.body.layout().addWidget(self.positions_note)
        return box

    def _machine_card(self) -> QWidget:
        """Home and retract, kept apart from the jogging controls.

        Both move the gantry a long way from wherever it is, and neither is
        undoable, so they do not sit beside a button that moves 0.05 mm."""
        box = card(self)
        box.layout().addWidget(heading("Machine", 3))
        row = QHBoxLayout()
        self.home_button = secondary_button("Home", self)
        self.home_button.clicked.connect(self._home)
        self.retract_button = secondary_button("Retract Z", self)
        self.retract_button.clicked.connect(self._retract)
        row.addWidget(self.home_button)
        row.addWidget(self.retract_button)
        row.addStretch(1)
        self._movers += [self.home_button, self.retract_button]
        box.layout().addLayout(row)
        return box

    # -- keys ----------------------------------------------------------------

    def _install_shortcuts(self, host: QWidget) -> None:
        """One shortcut per Qt spelling of every bound key in the layout.

        WindowShortcut: these reach the robot from anywhere in this window
        while the host is visible, and from no other window. A global hotkey
        would drive it from whatever application the operator happened to be
        typing in, which is the reason `jog_in_window` exists in preference
        to `jog_with_hotkeys`. Widgets that need a key for themselves still
        get it: Qt asks the focus widget first, and a line edit or a spin box
        keeps the characters it edits with. A plain button or a list does not
        ask, so the arrow keys belong to the gantry even with focus there.
        """
        for key in LAYOUT:
            if key.command in UNBOUND:
                continue
            for code in KEY_TO_QT[key.name]:
                shortcut = QShortcut(QKeySequence(code), host)
                shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
                if key.move is not None:
                    axis, direction = key.move
                    shortcut.activated.connect(
                        lambda a=axis, d=direction: self._move(a, d))
                else:
                    shortcut.activated.connect(
                        lambda c=key.command: self._command(c))
                if key.command == "help":
                    # Left out of the disable set: reading the key list is
                    # exactly what is wanted when nothing else works yet.
                    continue
                self._shortcuts.append(shortcut)

    # -- acting --------------------------------------------------------------

    def _move(self, axis: str, direction: int) -> None:
        if self.controller is None or self._busy():
            return

        controller = self.controller

        def job():
            # status() re-reads the pose, so a step costs one extra GET. At a
            # key press per step that is nothing, and it keeps the line on
            # screen formatted by jog itself rather than reassembled here.
            return controller.move(axis, direction), controller.status()

        self._run(Worker(job))

    def _command(self, command: str) -> None:
        if command == "help":
            # Before the robot check: reading the keys is exactly what is
            # wanted when nothing else works yet.
            self._help_shown = not self._help_shown
            self._emit_help()
            return
        if self.controller is None:
            return
        if self._busy():
            return
        controller = self.controller

        if command == "step_up":
            self._set_step(controller.step_up())
        elif command == "step_down":
            self._set_step(controller.step_down())
        elif command == "save":
            # The same act as the button: one store, and it asks for a name.
            # It used to put the pose in the controller's own dict under a
            # generated name, which is the list nothing else could see.
            self._remember()
        elif command == "undo":
            self._run(Worker(lambda: (controller.undo(), controller.status())))
        else:                                  # _check_layout rules this out
            raise RuntimeError(f"unhandled jog command {command!r}")

    def _chosen_position(self) -> str | None:
        item = self.saved.currentItem()
        return None if item is None else item.data(POSITION_ROLE)

    def _select_position(self, name: str) -> None:
        """Leave the row for `name` chosen. After a rename or a save the
        position the operator was working with is still the one in hand;
        losing the selection there means the next click is on whatever the
        sorting put in its place."""
        for index in range(self.saved.count()):
            if self.saved.item(index).data(POSITION_ROLE) == name:
                self.saved.setCurrentRow(index)
                return

    def _goto(self) -> None:
        name = self._chosen_position()
        if name is None or self.controller is None or self._busy():
            return
        where = (self.session.profile.positions or {}).get(name)
        if where is None:
            return
        controller = self.controller
        # move_to, not the controller's `goto`: the controller's own saved
        # dict is not what this list shows, and the soft limits apply either
        # way because move_to is where they are checked.
        self._run(Worker(lambda: (controller.move_to(where),
                                  controller.status())))

    def _rename(self) -> None:
        name = self._chosen_position()
        if name is None or self.session.profile is None or self._busy():
            return
        new, ok = QInputDialog.getText(self, "Rename position",
                                       f"New name for {name!r}:", text=name)
        new = new.strip()
        if not ok or not new or new == name:
            return
        try:
            self.session.rename_position(name, new)
        except Exception as exc:                 # noqa: BLE001
            self._say(str(exc), firm=True)
            return
        self._select_position(new)

    def _delete(self) -> None:
        name = self._chosen_position()
        if name is None or self.session.profile is None or self._busy():
            return
        where = self.session.profile.positions.get(name, ())
        answer = QMessageBox.question(
            self, "Delete position",
            f"Delete {name!r} "
            f"({', '.join(f'{v:.2f}' for v in where)}) from profile "
            f"{self.session.profile.name!r}? Anything that drives to it by "
            f"name will stop working until it is taught again.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.session.forget(name)
        except Exception as exc:                 # noqa: BLE001
            self._say(str(exc), firm=True)

    def _remember(self) -> None:
        if self.controller is None or self._busy():
            return
        if self.session.profile is None:
            self._say("no profile is loaded, so there is nowhere to remember a "
                      "position", firm=True)
            return
        name, ok = QInputDialog.getText(
            self, "Save this position",
            f"Name this deck position in profile {self.session.profile.name!r}:")
        name = name.strip()
        if not ok or not name:
            return
        controller, session = self.controller, self.session

        def job():
            session.remember(name, controller.position)
            return None, controller.status()

        self._run(Worker(job), moves=False)
        # The pose just saved is the one in hand; Go to should need no
        # further aiming.
        self._select_position(name)

    def _home(self) -> None:
        if not self._confirm(
                "Home the robot?",
                "The gantry travels to the home position on all axes. Saved "
                "positions are kept, but the undo history no longer describes "
                "where the robot is."):
            return
        robot, controller = self.session.robot, self.controller

        def job():
            # None, not the HTTP response: _job_done reads the first item as
            # a MoveResult, and a home has no clamp or refusal to report.
            robot.home_robot(verbose=False)
            log.info("homed")
            return None, controller.status()

        self._run(Worker(job))

    def _retract(self) -> None:
        if not self._confirm(
                "Retract the Z axis?",
                "The left Z axis travels to the top of its range."):
            return
        robot, controller = self.session.robot, self.controller

        def job():
            robot.retract_axis("leftZ", verbose=False)
            return None, controller.status()

        self._run(Worker(job))

    def _confirm(self, title: str, detail: str) -> bool:
        if self.controller is None or self._busy():
            return False
        answer = QMessageBox.question(
            self, title, detail,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        # `==`, not `is`: the answer can come back as a plain int (the profile
        # page has the same note), and `is` never matches it.
        return answer == QMessageBox.StandardButton.Yes

    # -- for the pages that host one -----------------------------------------

    @property
    def busy(self) -> bool:
        """A step is in flight. A page that reads the pose after the operator
        is done should wait for this to clear first."""
        return self._busy()

    def run_job(self, fn, *, moves: bool = True) -> bool:
        """Run `fn(log)` on this panel's worker, after any step in flight.

        The one queue for the robot on a page with a panel: a click-move or an
        aspirate from the page and a jog key can then never overlap, and the
        controls grey out for all of them. `fn` may return a MoveResult, None,
        or a sentence to show under the position. Returns False, and runs
        nothing, while the panel is busy or there is no robot.
        """
        if self.controller is None or self._busy():
            return False
        controller = self.controller

        def job(log):
            return fn(log), controller.status()

        # `moves=False` for a job that leaves the gantry where it is, such
        # as an aspirate: what was detected on the picture still stands.
        self._run(Worker(job), moves=moves)
        return True

    def tell(self, text: str) -> None:
        """A line under the position on the picture, as it is given."""
        self._message = text
        self._emit_position()

    def set_step(self, value: float) -> None:
        """Choose the step from outside, e.g. 0.05 mm for a touch-up. A value
        not in the list is still set on the controller; the chooser then
        shows nothing selected rather than the wrong number."""
        if self.controller is None:
            return
        self.controller.step = float(value)
        self._set_step(float(value))

    # -- the worker ----------------------------------------------------------

    def _busy(self) -> bool:
        return self._worker is not None and self._worker.running

    def _run(self, worker: Worker, *, moves: bool = True) -> None:
        self._worker = worker
        self._job_moves = moves
        # Bound methods, not lambdas: Qt takes a connection's thread from the
        # receiver, and a lambda has none, so it would touch these widgets from
        # the worker's thread.
        worker.finished.connect(self._job_done)
        worker.failed.connect(self._job_failed)
        worker.message.connect(self._job_said)
        worker.start()
        self._set_enabled(False)

    def _job_said(self, text: str) -> None:
        log.info("%s", text)

    def _claim(self) -> bool:
        """True if the signal came from the worker still in flight.

        These slots are queued, so a worker can finish, be replaced, and only
        then have its completion delivered. Clearing `_worker` unconditionally
        would mark the newer one idle and let a second move be requested while
        the first is still running.
        """
        if self.sender() is not self._worker:
            return False
        self._worker = None
        return True

    def _job_done(self, payload) -> None:
        if not self._claim():
            return
        result, status = payload
        self._set_status(status)
        self._refresh_saved()
        if isinstance(result, str):
            self.tell(result)
        elif result is None:
            self._say("")
        elif not result.ok:
            self._say(result.reason or "refused", firm=True)
        elif result.clamped:
            self._say(result.reason, firm=False)
        else:
            self._say("")
        self._set_enabled(True)
        if self._job_moves:
            self.moved.emit()

    def _job_failed(self, reason: str) -> None:
        if not self._claim():
            return
        self._say(reason, firm=True)
        self._set_enabled(True)
        # A failed move may still have gone part of the way.
        if self._job_moves:
            self.moved.emit()

    # -- display -------------------------------------------------------------

    @property
    def position_lines(self) -> list[str]:
        return [self._status] + ([self._message] if self._message else [])

    def show_position_on(self, view) -> None:
        """Keep `view` (a CameraView) showing where the gantry is, and the
        keys, from now."""
        self.position_changed.connect(view.set_position)
        view.set_position(self.position_lines)
        self.help_changed.connect(view.set_help)
        view.set_help(self.help_lines)

    @property
    def help_lines(self) -> list[str]:
        return list(self._help) if self._help_shown else []

    def add_help(self, lines) -> None:
        """A page's own keys and mouse actions, after the jog keys."""
        self._help += [str(line) for line in lines]
        self._emit_help()

    def _emit_help(self) -> None:
        self.help_changed.emit(self.help_lines)

    def _emit_position(self) -> None:
        self.position_changed.emit(self.position_lines)

    def _set_status(self, status: str) -> None:
        # `controller.status()` pads its numbers into columns for a fixed
        # font; the picture's box is set in the UI font, where the padding is
        # only gaps.
        self._status = re.sub(r"\(\s+", "(", re.sub(r"\s+", " ", status)).strip()
        self._emit_position()

    def _say(self, text: str, firm: bool = False) -> None:
        """Words rather than colour. Running into a soft limit is ordinary, and
        the difference between refused and clamped is what the sentence says.
        Shown on the picture under the position rather than in a dialog: a
        modal box for it would be in the way several times a minute."""
        self._message = (("REFUSED: " if firm else "Clamped: ") + text
                         if text else "")
        self._emit_position()

    def _set_step(self, value: float) -> None:
        index = self.step_choice.findData(value)
        if index >= 0:
            self.step_choice.blockSignals(True)
            self.step_choice.setCurrentIndex(index)
            self.step_choice.blockSignals(False)
        if self.controller is not None:
            self._set_status(self._offline_status())

    def _step_chosen(self, index: int) -> None:
        if self.controller is None or index < 0:
            return
        self.controller.step = self.step_choice.itemData(index)
        self._set_status(self._offline_status())

    def _offline_status(self) -> str:
        """The line already on screen with the step brought up to date.

        `controller.status()` reads the pose over HTTP, and changing the step
        size is not a reason to talk to the robot.
        """
        current = self._status
        step = f"step {self.controller.step:g} mm"
        if "step " not in current:
            return step
        head, _, tail = current.partition("step ")
        _, _, rest = tail.partition("mm")
        return head + step + rest

    def _refresh_saved(self) -> None:
        """The profile's positions, name and coordinates, selection kept."""
        profile = self.session.profile
        positions = dict(profile.positions) if profile is not None else {}
        rows = [(name, positions[name]) for name in sorted(positions)]
        wanted = [_position_row(name, where) for name, where in rows]
        if [self.saved.item(i).text() for i in range(self.saved.count())] == wanted:
            return
        chosen = self._chosen_position()
        self.saved.clear()
        for (name, where), text in zip(rows, wanted):
            item = QListWidgetItem(text)
            item.setData(POSITION_ROLE, name)
            self.saved.addItem(item)
            if name == chosen:
                self.saved.setCurrentItem(item)
        self.positions_note.setText(
            "" if rows else
            ("No positions in this profile. Jog somewhere worth returning to "
             "and save it."))

    def _on_profile_changed(self, _profile) -> None:
        if self.controller is not None:
            self.controller.limits = self.session.jog_limits
        self._refresh_saved()
        self._refresh_buttons()

    def _on_robot_changed(self, _state: str) -> None:
        robot = self.session.robot
        if robot is None:
            self.controller = None
            self._status = NO_ROBOT
            self._say("")
            self._set_enabled(False)
            # The list outlives the connection: it is the profile's, and
            # tidying it up is exactly what happens with the robot off.
            self._refresh_saved()
            return

        self.controller = JogController(robot, limits=self.session.jog_limits,
                                        steps=list(DEFAULT_STEPS))
        self._set_step(self.controller.step)
        self._set_enabled(True)
        # Now, not when the first pose read comes back: the list is the
        # profile's and needs no robot, and an empty one for the second it
        # takes to answer is a list that looks lost.
        self._refresh_saved()
        # The first pose read is HTTP like any other.
        controller = self.controller
        self._run(Worker(lambda: (None, controller.status())))

    def _set_enabled(self, enabled: bool) -> None:
        usable = enabled and self.controller is not None
        for widget in self._movers:
            widget.setEnabled(usable)
        # Only while on screen: a robot connect enables every panel, and a
        # window-wide shortcut on a hidden page would compete with the one on
        # the page that is showing. hideEvent keeps this true afterwards.
        for shortcut in self._shortcuts:
            shortcut.setEnabled(usable and self.isVisible())
        self._enabled = usable
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        """The three that need a position chosen, and not only a robot."""
        chosen = self._chosen_position() is not None
        usable = getattr(self, "_enabled", False)
        self.goto_button.setEnabled(usable and chosen)
        # Renaming and deleting touch the profile, not the robot: they work
        # with nothing connected, which is when tidying up happens.
        editable = self.session.profile is not None and not self._busy()
        self.rename_button.setEnabled(editable and chosen)
        self.delete_button.setEnabled(editable and chosen)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._set_enabled(self._worker is None)

    def hideEvent(self, event) -> None:
        """Give up the arrow keys.

        Two pages hold a panel each, and both sets of shortcuts exist for the
        life of the window. Left enabled they would both claim the same keys
        and Qt would pick one of them without saying which.
        """
        for shortcut in self._shortcuts:
            shortcut.setEnabled(False)
        super().hideEvent(event)
