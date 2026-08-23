"""Driving the robot by hand.

A third input backend for `JogController`, after the cv2 window and the global
hotkeys, and it shares the controller rather than the loop: the step sizes, the
soft limits, the saved positions and the undo history all live there, and this
widget only decides what a click and a key press mean.

A widget rather than a page, because two pages need it. Manual control is one;
the first step of the camera calibration is the other, where the marker has to
be centred and the working Z set before a sweep can be planned. Those are the
same act, and a second D-pad would be a second place for the step list and the
soft limits to drift.

`shortcut_host` is the widget the key bindings are registered on, normally the
page: `WidgetWithChildrenShortcut` then covers the camera view beside the panel
as well, which is where the operator is looking. They are disabled while the
panel is hidden, because two pages each holding one would otherwise both claim
the arrow keys and leave Qt to guess between them.

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
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase, QKeySequence, QShortcut
from PySide6.QtWidgets import (QComboBox, QGridLayout, QHBoxLayout,
                               QInputDialog, QLabel, QListWidget, QMessageBox,
                               QVBoxLayout, QWidget)

from ...workflows.jog import (DEFAULT_STEPS, LAYOUT, JogController,
                              help_lines)
from ..session import Session
from ..theme import SPACING
from ..theme.factory import card, heading, primary_button, secondary_button
from ..workers import Worker

__all__ = ["JogPanel", "KEY_TO_QT", "UNBOUND"]

log = logging.getLogger(__name__)

PANEL_WIDTH = 400

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


def _bound_layout() -> tuple:
    """The layout minus what this backend does not bind, for the help."""
    return tuple(key for key in LAYOUT if key.command not in UNBOUND)


class JogPanel(QWidget):
    def __init__(self, session: Session, *,
                 shortcut_host: QWidget | None = None,
                 machine_controls: bool = True,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.controller: JogController | None = None
        self._worker: Worker | None = None
        self._shortcuts: list[QShortcut] = []
        self._movers: list[QWidget] = []

        # Deliberately not a QScrollArea. PageUp and PageDown are the Z axis
        # here, and a scroll area would take them for scrolling before the
        # shortcut ever saw them.
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        column.addWidget(self._position_card())
        column.addWidget(self._move_card())
        column.addWidget(self._positions_card())
        if machine_controls:
            # Off during a calibration: homing halfway through centring the
            # marker throws away the pose the whole sweep is planned around.
            column.addWidget(self._machine_card())
        else:
            self.home_button = self.retract_button = None
        column.addWidget(self._help_label())
        column.addStretch(1)

        self._install_shortcuts(shortcut_host or self)

        session.robot_state_changed.connect(self._on_robot_changed)
        session.profile_changed.connect(self._on_profile_changed)

        self._on_robot_changed(session.robot_state)

    # -- construction --------------------------------------------------------

    def _position_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Position", 2))
        self.status_label = QLabel("no robot")
        self.status_label.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addWidget(self.status_label)

        # Refusals and clamps land here rather than in a dialog: running into a
        # soft limit is ordinary, and a modal box for it would be in the way
        # several times a minute.
        self.message = QLabel()
        self.message.setWordWrap(True)
        box.layout().addWidget(self.message)
        return box

    def _move_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Move", 2))

        grid = QGridLayout()
        grid.setSpacing(SPACING // 2)
        pad = {("y", +1): (0, 1, "↑ Y"), ("y", -1): (2, 1, "↓ Y"),
               ("x", -1): (1, 0, "← X"), ("x", +1): (1, 2, "X →")}
        for (axis, direction), (row, col, text) in pad.items():
            grid.addWidget(self._move_button(text, axis, direction), row, col)
        grid.addWidget(self._move_button("↑ Z", "z", +1), 0, 3)
        grid.addWidget(self._move_button("↓ Z", "z", -1), 2, 3)
        grid.setColumnMinimumWidth(3, 90)
        box.layout().addLayout(grid)

        step = QHBoxLayout()
        step.addWidget(QLabel("Step"))
        self.step_choice = QComboBox(self)
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
        box.layout().addLayout(step)
        return box

    def _move_button(self, text: str, axis: str, direction: int):
        button = secondary_button(text, self)
        button.clicked.connect(lambda: self._move(axis, direction))
        button.setAutoRepeat(False)      # the controller refuses a queue anyway
        self._movers.append(button)
        return button

    def _positions_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Positions", 2))

        self.saved = QListWidget(self)
        # PageUp and PageDown belong to the Z axis on this page, and a list
        # with focus would scroll on them instead.
        self.saved.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.saved.setMaximumHeight(110)
        self.saved.itemDoubleClicked.connect(lambda _item: self._goto())
        box.layout().addWidget(self.saved)

        row = QHBoxLayout()
        save = secondary_button("Save", self)
        save.clicked.connect(lambda: self._command("save"))
        self.goto_button = secondary_button("Go to", self)
        self.goto_button.clicked.connect(self._goto)
        undo = secondary_button("Undo", self)
        undo.clicked.connect(lambda: self._command("undo"))
        for button in (save, self.goto_button, undo):
            row.addWidget(button)
            self._movers.append(button)
        box.layout().addLayout(row)

        self.remember_button = primary_button("Remember in profile…", self)
        self.remember_button.clicked.connect(self._remember)
        self._movers.append(self.remember_button)
        box.layout().addWidget(self.remember_button)
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

    def _help_label(self) -> QWidget:
        self.help = QLabel("\n".join(help_lines(_bound_layout())))
        self.help.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.help.hide()
        return self.help

    # -- keys ----------------------------------------------------------------

    def _install_shortcuts(self, host: QWidget) -> None:
        """One shortcut per Qt spelling of every bound key in the layout.

        Registered on `host` rather than on the panel, so they also fire when
        the focus is on the camera view beside it.

        WidgetWithChildrenShortcut: these reach the robot while that host has
        focus and nowhere else. A global hotkey would drive it from whatever
        application the operator happened to be typing in, which is the reason
        `jog_in_window` exists in preference to `jog_with_hotkeys`.
        """
        for key in LAYOUT:
            if key.command in UNBOUND:
                continue
            for code in KEY_TO_QT[key.name]:
                shortcut = QShortcut(QKeySequence(code), host)
                shortcut.setContext(
                    Qt.ShortcutContext.WidgetWithChildrenShortcut)
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
        if self.controller is None:
            return
        if command == "help":
            # isHidden, not isVisible: isVisible() is false whenever any
            # ancestor is hidden, and these pages live in a QStackedWidget
            # where four of the five are. Toggling on it reads "not shown" for
            # a panel that is shown and sticks the toggle on.
            self.help.setVisible(self.help.isHidden())
            return
        if self._busy():
            return
        controller = self.controller

        if command == "step_up":
            self._set_step(controller.step_up())
        elif command == "step_down":
            self._set_step(controller.step_down())
        elif command == "save":
            self._run(Worker(lambda: (None, self._save_and_report())))
        elif command == "undo":
            self._run(Worker(lambda: (controller.undo(), controller.status())))
        else:                                  # _check_layout rules this out
            raise RuntimeError(f"unhandled jog command {command!r}")

    def _save_and_report(self) -> str:
        name = self.controller.save_position()
        log.info("saved position %r at %s", name,
                 tuple(round(v, 2) for v in self.controller.saved[name]))
        return self.controller.status()

    def _goto(self) -> None:
        item = self.saved.currentItem()
        if item is None or self.controller is None or self._busy():
            return
        controller, name = self.controller, item.text()
        self._run(Worker(lambda: (controller.goto(name), controller.status())))

    def _remember(self) -> None:
        if self.controller is None or self._busy():
            return
        if self.session.profile is None:
            self._say("no profile is loaded, so there is nowhere to remember a "
                      "position", firm=True)
            return
        name, ok = QInputDialog.getText(
            self, "Remember in profile",
            f"Name this deck position in profile {self.session.profile.name!r}:")
        name = name.strip()
        if not ok or not name:
            return
        controller, session = self.controller, self.session

        def job():
            session.remember(name, controller.position)
            return None, controller.status()

        self._run(Worker(job))

    def _home(self) -> None:
        if not self._confirm(
                "Home the robot?",
                "The gantry travels to the home position on all axes. Saved "
                "positions are kept, but the undo history no longer describes "
                "where the robot is."):
            return
        robot, controller = self.session.robot, self.controller
        self._run(Worker(lambda: (robot.home_robot(), controller.status())))

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
        return answer is QMessageBox.StandardButton.Yes

    # -- the worker ----------------------------------------------------------

    def _busy(self) -> bool:
        return self._worker is not None and self._worker.running

    def _run(self, worker: Worker) -> None:
        self._worker = worker
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
        self.status_label.setText(status)
        self._refresh_saved()
        if result is None:
            self._say("")
        elif not result.ok:
            self._say(result.reason or "refused", firm=True)
        elif result.clamped:
            self._say(result.reason, firm=False)
        else:
            self._say("")
        self._set_enabled(True)

    def _job_failed(self, reason: str) -> None:
        if not self._claim():
            return
        self._say(reason, firm=True)
        self._set_enabled(True)

    # -- display -------------------------------------------------------------

    def _say(self, text: str, firm: bool = False) -> None:
        """Words rather than colour. Running into a soft limit is ordinary, and
        the difference between refused and clamped is what the sentence says."""
        if not text:
            self.message.clear()
            return
        self.message.setText(("Refused: " if firm else "Clamped: ") + text)
        font = self.message.font()
        font.setBold(firm)
        self.message.setFont(font)

    def _set_step(self, value: float) -> None:
        index = self.step_choice.findData(value)
        if index >= 0:
            self.step_choice.blockSignals(True)
            self.step_choice.setCurrentIndex(index)
            self.step_choice.blockSignals(False)
        if self.controller is not None:
            self.status_label.setText(self._offline_status())

    def _step_chosen(self, index: int) -> None:
        if self.controller is None or index < 0:
            return
        self.controller.step = self.step_choice.itemData(index)
        self.status_label.setText(self._offline_status())

    def _offline_status(self) -> str:
        """The line already on screen with the step brought up to date.

        `controller.status()` reads the pose over HTTP, and changing the step
        size is not a reason to talk to the robot.
        """
        current = self.status_label.text()
        step = f"step {self.controller.step:g} mm"
        if "step " not in current:
            return step
        head, _, tail = current.partition("step ")
        _, _, rest = tail.partition("mm")
        return head + step + rest

    def _refresh_saved(self) -> None:
        if self.controller is None:
            self.saved.clear()
            return
        names = sorted(self.controller.saved)
        if [self.saved.item(i).text() for i in range(self.saved.count())] == names:
            return
        self.saved.clear()
        self.saved.addItems(names)

    def _on_profile_changed(self, _profile) -> None:
        if self.controller is not None:
            self.controller.limits = self.session.jog_limits

    def _on_robot_changed(self, _state: str) -> None:
        robot = self.session.robot
        if robot is None:
            self.controller = None
            self.status_label.setText("no robot — connect it on the Profile page")
            self._say("")
            self.saved.clear()
            self._set_enabled(False)
            return

        self.controller = JogController(robot, limits=self.session.jog_limits,
                                        steps=list(DEFAULT_STEPS))
        self._set_step(self.controller.step)
        self._set_enabled(True)
        # The first pose read is HTTP like any other.
        controller = self.controller
        self._run(Worker(lambda: (None, controller.status())))

    def _set_enabled(self, enabled: bool) -> None:
        usable = enabled and self.controller is not None
        for widget in self._movers:
            widget.setEnabled(usable)
        for shortcut in self._shortcuts:
            shortcut.setEnabled(usable)
        self.goto_button.setEnabled(usable and self.saved.count() > 0)

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
