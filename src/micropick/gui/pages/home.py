"""Home: where the window opens (REDESIGN §4).

Three cards, top to bottom, in one centered column:

1. **Robot**: whether it is connected, as a headline. Until the app
   connects by itself (the connection monitor), Connect is here, with
   Start new robot session for a robot that has no session to carry on.
2. **Robot setup**: "Setup is done", or "Complete the setup · N steps
   left: …" with Complete setup, which opens the first item missing.
3. **What are we doing today?**: the three task tiles, words only.

No profile name and no chooser: the last profile is loaded by itself
(`Session.load_last_profile`).

Bringing the robot up is one button, as it was on the Profile page. A
session the robot holds and can carry on with is carried on with - its
pipette, its labware, its offsets; any other state has only one way on, a
new robot session, which homes and so asks first.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout,
                               QInputDialog, QLabel, QVBoxLayout, QWidget)

from ...config import robot_sessions
from ..session import Session
from ..tasks import SETUP, SETUP_TITLES, TASKS
from ..theme import COLORS
from ..theme.factory import (caption, card, muted_label, primary_button,
                             quiet_button, secondary_button)
from ..theme.icons import pixmap
from ..workers import Worker

__all__ = ["HomePage", "TaskTile", "TILE_TEXT"]

log = logging.getLogger(__name__)

# The tiles' one line each: the prototype's draft copy (REDESIGN §12.11).
TILE_TEXT = {
    "auto": "Cuboids from a dish into a plate, by the robot, following "
            "your plan.",
    "manual": "Move cuboids one at a time: you choose which and where.",
    "liquid": "Add or remove liquid in many wells at once.",
}

COLUMN_WIDTH = 1040
MARK_PX = 44


class TaskTile(QFrame):
    """A task's name and its one line; the whole tile is clicked."""

    clicked = Signal()

    def __init__(self, title: str, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("taskTile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(170)
        self.title, self.text = title, text
        column = QVBoxLayout(self)
        column.setContentsMargins(24, 26, 24, 26)
        column.setSpacing(12)
        name = QLabel(title, self)
        name.setObjectName("tileTitle")
        column.addWidget(name)
        column.addWidget(muted_label(text, self))
        column.addStretch(1)

    def mouseReleaseEvent(self, event) -> None:
        if (event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.position().toPoint())):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


def _mark(parent: QWidget) -> QLabel:
    mark = QLabel(parent)
    mark.setFixedSize(MARK_PX, MARK_PX)
    mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return mark


def _set_mark(mark: QLabel, ok: bool) -> None:
    """A green ✓ on `ok-bg`, or a yellow ! on `warn-bg`: never red, a robot
    switched off is not an emergency."""
    name, ink, bg = (("check", "ok", "ok-bg") if ok
                     else ("alert", "warn", "warn-bg"))
    mark.setPixmap(pixmap(name, COLORS[ink], 22))
    mark.setStyleSheet(f"background: {COLORS[bg]}; "
                       f"border-radius: {MARK_PX // 2}px;")


def _title(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("homeTitle")
    return label


class HomePage(QWidget):
    """`task_chosen(task)` on a tile; `setup_requested(item)` for a Robot
    setup item (`tasks.SETUP`)."""

    task_chosen = Signal(str)
    setup_requested = Signal(str)

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._worker: Worker | None = None
        self._then = None                        # see _run

        column = QVBoxLayout()
        column.setSpacing(16)
        column.addWidget(self._robot_card())
        column.addWidget(self._setup_card())
        column.addWidget(self._tasks_card())
        column.addStretch(1)
        holder = QWidget(self)
        holder.setLayout(column)
        holder.setMaximumWidth(COLUMN_WIDTH)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(24, 48, 24, 24)
        outer.addWidget(holder)

        session.robot_state_changed.connect(lambda _s: self.refresh())
        session.robot_busy_changed.connect(lambda _h: self.refresh())
        session.profile_changed.connect(lambda _p: self.refresh())
        self.refresh()

    # -- construction --------------------------------------------------

    def _robot_card(self) -> QWidget:
        box = card(self)
        row = QHBoxLayout()
        row.setSpacing(20)
        self.robot_mark = _mark(box)
        row.addWidget(self.robot_mark, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(6)
        self.robot_title = _title("", box)
        self.robot_text = muted_label("", box)
        text.addWidget(self.robot_title)
        text.addWidget(self.robot_text)
        # Selectable: a path or an address in it can be copied out.
        self.error = QLabel(box)
        self.error.setObjectName("warnText")
        self.error.setWordWrap(True)
        self.error.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.error.hide()
        text.addWidget(self.error)
        row.addLayout(text, 1)
        self.connect_button = primary_button("Connect", box)
        self.connect_button.clicked.connect(self._connect_or_disconnect)
        self.new_run_button = secondary_button("Start new robot session", box)
        self.new_run_button.setToolTip(
            "Start a new robot session with an empty deck. The robot homes "
            "first.")
        self.new_run_button.clicked.connect(self._new_run)
        buttons = QHBoxLayout()
        buttons.addWidget(self.new_run_button)
        buttons.addWidget(self.connect_button)
        row.addLayout(buttons)
        box.layout().addLayout(row)
        return box

    def _setup_card(self) -> QWidget:
        box = card(self)
        row = QHBoxLayout()
        row.setSpacing(20)
        self.setup_mark = _mark(box)
        row.addWidget(self.setup_mark, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(6)
        self.setup_title = _title("", box)
        self.setup_text = muted_label("", box)
        text.addWidget(self.setup_title)
        text.addWidget(self.setup_text)
        row.addLayout(text, 1)
        self.complete_button = primary_button("Complete setup", box)
        self.complete_button.clicked.connect(self._complete_setup)
        self.setup_link = quiet_button("Robot setup ›", box)
        # Until Robot setup is a page of its own, the item that has no step
        # to be reached from.
        self.setup_link.clicked.connect(
            lambda: self.setup_requested.emit("camera_calibration"))
        row.addWidget(self.complete_button)
        row.addWidget(self.setup_link)
        box.layout().addLayout(row)
        return box

    def _tasks_card(self) -> QWidget:
        box = card(self)
        box.layout().setSpacing(16)
        box.layout().addWidget(caption("What are we doing today?", box))
        grid = QGridLayout()
        grid.setSpacing(16)
        self.tiles: dict[str, TaskTile] = {}
        for i, (task, (title, _steps)) in enumerate(TASKS.items()):
            tile = TaskTile(title, TILE_TEXT[task], box)
            tile.clicked.connect(lambda t=task: self.task_chosen.emit(t))
            grid.addWidget(tile, 0, i)
            grid.setColumnStretch(i, 1)
            self.tiles[task] = tile
        box.layout().addLayout(grid)
        return box

    # -- display -------------------------------------------------------

    def missing_setup(self) -> list[str]:
        """The Robot setup items not set yet, in order."""
        profile = self.session.profile
        return [key for key, (_text, is_set) in SETUP.items()
                if profile is None or not is_set(profile)]

    def refresh(self) -> None:
        session = self.session
        busy = self._worker is not None and self._worker.running
        connected = session.has_connection
        _set_mark(self.robot_mark, session.robot is not None)
        if session.robot is not None:
            self.robot_title.setText("Robot connected")
            self.robot_text.setText("Ready to work.")
        elif connected and not busy:
            # The home was declined, or a session could not be carried on.
            self.robot_title.setText("Robot not connected")
            self.robot_text.setText("Start a new robot session.")
        else:
            self.robot_title.setText("Robot not connected")
            self.robot_text.setText(
                "Please turn the robot on. When its button light stops "
                "blinking, Connect.")
        self.connect_button.setText("Disconnect robot session" if connected
                                    else "Connect")
        robot_free = not session.robot_lease.busy
        self.connect_button.setEnabled(not busy
                                       and (robot_free or not connected))
        self.new_run_button.setVisible(connected)
        self.new_run_button.setEnabled(connected and not busy and robot_free)

        missing = self.missing_setup()
        _set_mark(self.setup_mark, not missing)
        self.complete_button.setVisible(bool(missing))
        self.setup_link.setVisible(not missing)
        if missing:
            self.setup_title.setText("Complete the setup")
            names = ", ".join(SETUP_TITLES[k].lower() for k in missing)
            steps = "1 step" if len(missing) == 1 else f"{len(missing)} steps"
            self.setup_text.setText(f"{steps} left: {names}.")
        else:
            self.setup_title.setText("Setup is done")
            self.setup_text.setText("Cameras, platforms and positions.")

    def showEvent(self, event) -> None:
        # A position or a calibration saved on another page has no signal
        # of its own.
        super().showEvent(event)
        self.refresh()

    def show_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.setVisible(bool(text))

    # -- actions -------------------------------------------------------

    def _complete_setup(self) -> None:
        missing = self.missing_setup()
        if missing:
            self.setup_requested.emit(missing[0])

    def _connect_or_disconnect(self) -> None:
        if self.session.has_connection:
            # Its retract would go into whatever has the robot now.
            if self.session.robot_lease.busy:
                self.show_error("Robot busy.")
                return
            self.show_error("")
            self.session.disconnect_robot()
            return
        self.show_error("")
        self._run(Worker(self.session.probe_robot), "probing the robot",
                  then=self._probed)

    def _probed(self) -> None:
        state = self.session.run_state
        if state is None:
            return
        if state.reusable:
            self._run(Worker(self.session.adopt_run),
                      f"continuing robot session {state.label}",
                      robot_work=True)
        else:
            self._new_run()

    def _new_run(self) -> None:
        """Ends in a home, so it asks: the gantry travels to its limits on
        every axis, and a hand in the deck is the failure this dialog is
        for."""
        state = self.session.run_state
        detail = ("The robot will home its axes. Stand clear and keep your "
                  "hands out of the robot.")
        if state is not None and state.reusable:
            detail += (f"\n\nThe current robot session {state.label} will "
                       f"be left behind, with its labware and offsets.")
        profile = self.session.profile
        dialog = QInputDialog(self)
        dialog.setWindowTitle("New robot session")
        dialog.setLabelText(detail + "\n\nName of the new session:")
        dialog.setTextValue(robot_sessions.default_name(
            profile.name if profile is not None else None))
        dialog.setOkButtonText("Start")
        if dialog.exec() != QInputDialog.DialogCode.Accepted:
            return
        name = dialog.textValue().strip() or None
        self.show_error("")
        self._run(Worker(self.session.new_run, name),
                  "new robot session, then home", robot_work=True)

    def _run(self, worker: Worker, what: str, then=None,
             robot_work: bool = False) -> None:
        """`then` is called once it finished well. `robot_work`: it sends
        the robot session commands, so it claims the robot's lease first;
        a probe only asks."""
        if self._worker is not None and self._worker.running:
            return
        if robot_work:
            lease = self.session.robot_lease.claim(what)
            if lease is None:
                self.show_error("Robot busy.")
                return
            worker.lease = lease
        self._worker = worker
        self._then = then
        worker.what = worker.what or what
        # Bound methods, not lambdas: Qt queues them onto this thread.
        worker.finished.connect(self._worker_done)
        worker.failed.connect(self._worker_failed)
        log.info("%s", what)
        worker.start()
        self.refresh()

    def _worker_done(self, _result=None) -> None:
        self._worker = None
        then, self._then = self._then, None
        self.refresh()
        if then is not None:
            then()

    def _worker_failed(self, reason: str) -> None:
        self._worker = None
        self._then = None
        self.show_error(reason)
        self.refresh()
