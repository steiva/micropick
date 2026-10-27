"""Home: where the window opens (REDESIGN §4).

Three cards, top to bottom, in one centered column:

1. **Robot**: whether it is connected, as a headline. The app connects
   by itself (`gui.robot_monitor`); there is no Connect. A robot with no
   session to carry on with says "Robot needs to home", with Home the
   robot. Until the robot is connected, the cards below are dimmed under
   one line.
2. **Robot setup**: "Setup is done", the whole card opening Robot setup,
   or "Complete the setup · N steps left: …" with Complete setup, which
   opens it too: its rows show what is missing.
3. **What are we doing today?**: the three task tiles, words only;
   dimmed while a run has the robot, under a line naming where the run
   is (`show_run`, told by the shell).

No profile name and no chooser: the last profile is loaded by itself
(`Session.load_last_profile`).

A session the robot holds and can carry on with is carried on with by the
monitor - its pipette, its labware, its offsets - and that moves nothing.
Any other state has only one way on, a new robot session, which homes: so
it is a button, and the button asks first. A hand may be in the robot, and
nothing moves until the operator says so.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout,
                               QInputDialog, QLabel, QVBoxLayout, QWidget)

from ...config import robot_sessions
from ..session import Session
from ..tasks import SETUP_TITLES, TASKS, missing_setup
from ..theme import COLORS, SPACING
from ..theme.factory import (caption, card, muted_label, primary_button,
                             quiet_button)
from ..theme.icons import pixmap
from ..widgets.overlays import DimCover
from ..workers import Worker

__all__ = ["HomePage", "TaskTile", "TILE_TEXT", "new_session_dialog"]

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


def new_session_dialog(session: Session, parent: QWidget, *,
                       title: str = "Home and connect") -> QInputDialog:
    """The question a new robot session asks - Home and connect here, Start
    new robot session in Settings (`title`) - with the new session's
    name. It homes either way, so the button says Home."""
    profile = session.profile
    dialog = QInputDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setLabelText("Keep hands out of the robot. The gantry will "
                        "move.\n\nName of the robot session:")
    dialog.setTextValue(robot_sessions.default_name(
        profile.name if profile is not None else None))
    dialog.setOkButtonText("Home")
    return dialog


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


class _ClickableCard(QFrame):
    """A card (`theme.factory.card`) that is clicked as a whole."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setFrameShape(QFrame.Shape.Panel)
        # As `card` has it: its wrapped text asks for height by width.
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2,
                                  SPACING * 2)
        layout.setSpacing(SPACING)

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
    """`task_chosen(task)` on a tile; `robot_setup_requested` for the Robot
    setup page."""

    task_chosen = Signal(str)
    robot_setup_requested = Signal()

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._worker: Worker | None = None

        column = QVBoxLayout()
        column.setSpacing(16)
        column.addWidget(self._robot_card())
        # Until the robot is connected: one line, and the rest dimmed.
        self.waiting = muted_label("Waiting for the robot.", self)
        column.addWidget(self.waiting)
        self.below = QWidget(self)
        below = QVBoxLayout(self.below)
        below.setContentsMargins(0, 0, 0, 0)
        below.setSpacing(16)
        below.addWidget(self._setup_card())
        below.addWidget(self._tasks_card())
        self.below_dim = DimCover(self.below)
        column.addWidget(self.below)
        column.addStretch(1)
        holder = QWidget(self)
        holder.setLayout(column)
        holder.setMaximumWidth(COLUMN_WIDTH)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(24, 48, 24, 24)
        outer.addWidget(holder)

        session.robot_state_changed.connect(self.refresh)
        session.robot_busy_changed.connect(self.refresh)
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
        self.home_button = primary_button("Home and connect", box)
        self.home_button.setToolTip(
            "Start a new robot session with an empty deck. The robot homes "
            "first.")
        self.home_button.clicked.connect(self._new_run)
        row.addWidget(self.home_button, 0, Qt.AlignmentFlag.AlignTop)
        # A session let go in Settings (`Session.release_run`) is taken
        # back on the operator's word only.
        self.continue_button = primary_button("Continue robot session", box)
        self.continue_button.setToolTip("Carry on with the robot's session.")
        self.continue_button.clicked.connect(self.session.continue_run)
        row.addWidget(self.continue_button, 0, Qt.AlignmentFlag.AlignTop)
        box.layout().addLayout(row)
        return box

    def _setup_card(self) -> QWidget:
        box = self.setup_card = _ClickableCard(self)
        box.clicked.connect(self._setup_card_clicked)
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
        self.complete_button.clicked.connect(self.robot_setup_requested)
        self.setup_link = quiet_button("Robot setup ›", box)
        self.setup_link.clicked.connect(self.robot_setup_requested)
        row.addWidget(self.complete_button)
        row.addWidget(self.setup_link)
        box.layout().addLayout(row)
        return box

    def _tasks_card(self) -> QWidget:
        box = card(self)
        box.layout().setSpacing(16)
        box.layout().addWidget(caption("What are we doing today?", box))
        # During a run: one line, and the tiles dimmed under it.
        self.run_note = muted_label("", box)
        self.run_note.hide()
        box.layout().addWidget(self.run_note)
        self.tile_row = QWidget(box)
        grid = QGridLayout(self.tile_row)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(16)
        self.tiles: dict[str, TaskTile] = {}
        for i, (task, (title, _steps)) in enumerate(TASKS.items()):
            tile = TaskTile(title, TILE_TEXT[task], self.tile_row)
            tile.clicked.connect(lambda t=task: self.task_chosen.emit(t))
            grid.addWidget(tile, 0, i)
            grid.setColumnStretch(i, 1)
            self.tiles[task] = tile
        self.tiles_dim = DimCover(self.tile_row)
        box.layout().addWidget(self.tile_row)
        return box

    # -- display -------------------------------------------------------

    def refresh(self, _arg=None) -> None:
        session = self.session
        busy = self._worker is not None and self._worker.running
        ready = (session.robot is not None
                 and session.robot_answers is not False)
        _set_mark(self.robot_mark, ready)
        if ready:
            self.robot_title.setText("Robot connected")
            self.robot_text.setText("Ready to work.")
        elif busy:
            self.robot_title.setText("Homing…")
            self.robot_text.setText("Keep hands out of the robot.")
        elif session.needs_home:
            self.robot_title.setText("Robot needs to home")
            self.robot_text.setText(
                "The robot was restarted. Labware and the tip are unknown."
                if session.restarted else
                "The robot's session could not be continued."
                if session.adopt_refused is not None else
                "The robot has no robot session to carry on with.")
        elif session.released is not None:
            self.robot_title.setText("Robot not connected")
            self.robot_text.setText("Robot session disconnected.")
        elif session.robot_answers and session.robot is None:
            self.robot_title.setText("Robot not connected")
            self.robot_text.setText("Continuing the robot's session.")
        else:
            self.robot_title.setText("Connecting to the robot…")
            self.robot_text.setText(
                "This can take a few minutes after it is switched on.")
        self.home_button.setVisible(session.needs_home)
        self.continue_button.setVisible(session.released is not None
                                        and not ready)
        self.home_button.setEnabled(not busy
                                    and not session.robot_lease.busy)
        self.waiting.setVisible(not ready)
        self.below.setEnabled(ready)
        if ready:
            self.below_dim.hide()
        elif not self.below_dim.isVisible():
            self.below_dim.open()

        missing = missing_setup(session)
        _set_mark(self.setup_mark, not missing)
        self.complete_button.setVisible(bool(missing))
        self.setup_link.setVisible(not missing)
        self.setup_card.setCursor(Qt.CursorShape.ArrowCursor if missing
                                  else Qt.CursorShape.PointingHandCursor)
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

    def show_run(self, place: str | None) -> None:
        """While a run has the robot, the tiles are dimmed under a line
        naming where it is (`place`, "Auto pick"); None: no run."""
        running = place is not None
        if running:
            self.run_note.setText(f"The robot is busy. Go back to {place} "
                                  f"to stop or finish it.")
        self.run_note.setVisible(running)
        self.tile_row.setEnabled(not running)
        if not running:
            self.tiles_dim.hide()
        elif not self.tiles_dim.isVisible():
            self.tiles_dim.open()

    def show_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.setVisible(bool(text))

    # -- actions -------------------------------------------------------

    def _setup_card_clicked(self) -> None:
        """The whole card opens Robot setup once it is done; before, its
        Complete setup does."""
        if not missing_setup(self.session):
            self.robot_setup_requested.emit()

    def _new_run(self) -> None:
        """Home and connect: a new robot session, which ends in a home, so it
        asks. The gantry travels to its limits on every axis, and a hand in
        the deck is the failure this dialog is for; Cancel moves nothing."""
        dialog = self.home_dialog()
        if dialog.exec() != QInputDialog.DialogCode.Accepted:
            return
        name = dialog.textValue().strip() or None
        self.show_error("")
        what = "new robot session, then home"
        lease = self.session.robot_lease.claim(what)
        if lease is None:
            self.show_error(self.session.robot_lease.refusal)
            return
        worker = Worker(self.session.new_run, name, what=what, lease=lease)
        # Bound methods, not lambdas: Qt queues them onto this thread.
        worker.finished.connect(self._worker_done)
        worker.failed.connect(self._worker_failed)
        self._worker = worker
        log.info("%s", what)
        worker.start()
        self.refresh()

    def home_dialog(self) -> QInputDialog:
        """The question Home and connect asks, with the new session's name."""
        return new_session_dialog(self.session, self)

    def _worker_done(self, _result=None) -> None:
        self._worker = None
        self.refresh()

    def _worker_failed(self, reason: str) -> None:
        self._worker = None
        self.show_error(reason)
        self.refresh()
