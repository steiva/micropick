"""Who has the robot: one job at a time that moves it or changes its session.

Every page used to keep its own idea of busy - its jog panel's worker, its
own worker - and a page could not see another's. A liquid run on its page
left Manual control's panel free to jog under it; a tip picked up on Robot &
Deck went straight into a picking run's moves. So the robot has one lease,
held by `Session` (`Session.robot_lease`), and whatever is about to send a
command that moves the robot or changes the robot session claims it first:
a jog step, a Go to, Home, a tip pick-up, a labware load, a new robot
session, and a picking, liquid or calibration run for its whole length,
pauses included.

A claim that finds the robot taken is refused, never queued: a move that
waited would run later from a pose nobody is looking at. Reads - the pose,
the tip record, the lights - and the lights switch take no lease.

The lease is released by whoever holds it, once: `gui.workers.Worker` does
it as its job ends, however it ends, so a page cannot leak one on an error
path. A holder may lend its lease to work it waits on - the pipette
calibration's touch-up, jogged on the disc panel while the calibration
waits - which then runs under it and does not release it.

No Qt: `on_change(holder)` is called, from whichever thread claimed or
released, with what now holds the robot or None.
"""

from __future__ import annotations

import logging
import threading

__all__ = ["RobotLease", "Lease"]

log = logging.getLogger(__name__)


class Lease:
    """The robot, held for `what` until `release`."""

    def __init__(self, owner: "RobotLease", what: str, run: bool = False,
                 plates=()):
        self._owner = owner
        self.what = what
        # A run - picking, liquid, a calibration - rather than a short job:
        # closing the window asks before stopping one.
        self.run = run
        # (slot, load name) of the plates the run goes into: their measured
        # well centers are not forgotten or changed under it.
        self.plates = frozenset((str(slot), name) for slot, name in plates)
        self._released = False

    @property
    def released(self) -> bool:
        return self._released

    def release(self) -> None:
        """Give the robot back. A second release does nothing."""
        self._owner._release(self)


class RobotLease:
    """The robot's one lease. See the module docstring."""

    def __init__(self, on_change=None):
        self._lock = threading.Lock()
        self._held: Lease | None = None
        self._on_change = on_change

    def claim(self, what: str, *, run: bool = False,
              plates=()) -> Lease | None:
        """The robot for `what`, or None while something else has it.
        `run`: a run that holds it for minutes, not a short job; `plates`,
        the (slot, load name) of the plates it goes into."""
        with self._lock:
            if self._held is not None:
                log.info("robot busy with %s: %s refused", self._held.what,
                         what)
                return None
            lease = self._held = Lease(self, what, run, plates)
        self._changed(what)
        return lease

    @property
    def busy(self) -> bool:
        return self._held is not None

    @property
    def holding_run(self) -> bool:
        held = self._held
        return held is not None and held.run

    def uses_plate(self, slot, load_name: str) -> bool:
        """Whether what holds the robot goes into this plate's wells."""
        held = self._held
        return held is not None and (str(slot), load_name) in held.plates

    @property
    def holder(self) -> str | None:
        """What has the robot, in a few words, or None."""
        held = self._held
        return None if held is None else held.what

    def _release(self, lease: Lease) -> None:
        with self._lock:
            if lease._released:
                return
            lease._released = True
            if self._held is not lease:
                return
            self._held = None
        self._changed(None)

    def _changed(self, holder: str | None) -> None:
        if self._on_change is not None:
            self._on_change(holder)
