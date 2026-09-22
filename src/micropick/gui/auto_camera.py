"""Opening the camera a page needs, once, without being asked.

A page that shows a camera used to show "no camera" until the operator went
back to the Profile page and opened it. That is a step with no decision in
it: arriving at the page *is* the request to see what it shows.

The rule, and why it is not "open everything at startup"
-------------------------------------------------------
`MainWindow._start` deliberately reaches for no hardware, and that stays
true: an application that opens devices because it was launched does it at
the wrong moment eventually. This is the other case — the operator
navigated to a page whose whole content is one camera's picture — and there
the open is what was asked for.

Three things keep it from becoming a nuisance:

- **once per label.** A camera that failed to open is not tried again by
  this; the Profile page's button is how a fixed cable is retried, and it
  is one click. Without that, a page with an unplugged camera would try to
  open it every time it is looked at, each attempt several seconds of
  blocking device open.
- **never twice at a time.** `Session.open_camera` hands back an already
  open camera, so a second request while one is in flight is dropped
  rather than queued.
- **in a worker.** A device open plus a warm-up is seconds, and in the GUI
  thread that is the page arriving frozen.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal

from .workers import Worker

__all__ = ["CameraOpener"]

log = logging.getLogger(__name__)


class CameraOpener(QObject):
    """Owns the "open it if it is not open" policy for one page."""

    opened = Signal(str)
    failed = Signal(str, str)          # label, reason

    def __init__(self, session, parent: QObject | None = None):
        super().__init__(parent)
        self.session = session
        self._in_flight: dict[str, Worker] = {}
        self._refused: dict[str, str] = {}

    def ensure(self, label: str | None) -> bool:
        """Open `label` unless it is open, in flight, or has failed here.

        Returns True when the camera is already open and there is nothing
        to do, so a caller can tell "showing it now" from "asked for it".
        """
        if not label or self.session.profile is None:
            return False
        if self.session.camera(label) is not None:
            return True
        if label in self._in_flight and self._in_flight[label].running:
            return False
        if label in self._refused:
            return False

        worker = Worker(self.session.open_camera, label)
        worker.label = label
        self._in_flight[label] = worker
        # Bound methods, not lambdas: Qt takes a connection's thread from the
        # receiver, and what these touch belongs to this one.
        worker.finished.connect(self._done)
        worker.failed.connect(self._failed)
        log.info("opening camera %r for the page that needs it", label)
        worker.start()
        return False

    def forget(self, label: str | None = None) -> None:
        """Allow a failed camera to be tried again — after a profile change,
        or when a page offers the operator a retry."""
        if label is None:
            self._refused.clear()
        else:
            self._refused.pop(label, None)

    def refusal(self, label: str | None) -> str | None:
        """Why this opener will not try `label` again, or None."""
        return self._refused.get(label) if label else None

    def _done(self, _camera) -> None:
        label = self.sender().label
        self._in_flight.pop(label, None)
        self.opened.emit(label)

    def _failed(self, reason: str) -> None:
        label = self.sender().label
        self._in_flight.pop(label, None)
        self._refused[label] = reason
        log.error("could not open camera %r: %s", label, reason)
        self.failed.emit(label, reason)
