"""The connection monitor (REDESIGN §4, #4): the robot is looked at every
few seconds, from the start, with no Connect.

A look is a read on a worker (`Session.look_at_robot`); what it changes is
taken on the GUI thread (`Session.robot_seen`). A session the robot holds
and can carry on with is carried on with at once (`Session.adopt_run`,
under the robot's lease): it moves nothing. A robot with no such session
waits for the operator's Home the robot on Home (`Session.needs_home`),
because a new session homes, and a hand may be in the robot.

Started by `app.run` once the window is up, not by the window itself: a
test that builds a window decides when there is a robot.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer, Signal

from .session import LOOK_TIMEOUT_S, Session
from .workers import Worker

__all__ = ["LOOK_EVERY_MS", "RobotMonitor"]

log = logging.getLogger(__name__)

LOOK_EVERY_MS = 3000


class RobotMonitor(QObject):
    """`failed(reason)`: the robot's session could not be carried on with.
    It is not tried again (`Session.adopt_failed`); Home asks to home."""

    failed = Signal(str)

    def __init__(self, session: Session, parent: QObject | None = None):
        super().__init__(parent)
        self.session = session
        self._look: Worker | None = None
        self._adopt: Worker | None = None
        self._adopting: str | None = None        # the run id being taken
        self._generation = 0                     # the session's, at a look
        self._stopped = True
        self._timer = QTimer(self)
        self._timer.setInterval(LOOK_EVERY_MS)
        self._timer.timeout.connect(self.look)

    def start(self) -> None:
        self._stopped = False
        self._timer.start()
        self.look()

    def stop(self) -> None:
        """Before the session shuts down: a look still running is waited
        for, so no thread outlives the window and no client is made again
        after the session let go of its own."""
        self._stopped = True
        self._timer.stop()
        for worker in (self._look, self._adopt):
            if worker is not None:
                worker.wait(int((LOOK_TIMEOUT_S + 2) * 1000))

    @property
    def busy(self) -> bool:
        return any(w is not None and w.running
                   for w in (self._look, self._adopt))

    def look(self) -> None:
        if self._stopped or self.busy:
            return
        worker = Worker(self.session.look_at_robot, self.session._client(),
                        what="looking for the robot", background=True)
        worker.finished.connect(self._seen)
        self._look = worker
        self._generation = self.session.run_generation
        worker.start()

    def _seen(self, state) -> None:
        self._look = None
        if self._stopped:
            return
        if self.session.run_generation != self._generation:
            # Home the robot ended while it looked: what it saw is gone.
            return
        if not self.session.robot_seen(state):
            return
        what = f"continuing robot session {state.label}"
        lease = self.session.robot_lease.claim(what)
        if lease is None:                        # the next look tries again
            return
        log.info("%s", what)
        worker = Worker(self.session.adopt_run, what=what, lease=lease)
        worker.finished.connect(self._adopted)
        worker.failed.connect(self._adopt_failed)
        self._adopt, self._adopting = worker, state.run_id
        worker.start()

    def _adopted(self, _robot) -> None:
        self._adopt = self._adopting = None

    def _adopt_failed(self, reason: str) -> None:
        run_id, self._adopt, self._adopting = self._adopting, None, None
        log.error("could not carry on with the robot's session: %s", reason)
        self.session.adopt_failed(run_id)
        self.failed.emit(reason)
