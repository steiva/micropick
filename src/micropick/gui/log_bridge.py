"""Pointing `logging` at the log page.

There are no `print` calls under `gui`. A notebook can print because a notebook
has somewhere to print to; this application's output has to reach a widget, and
a widget may only be touched from the GUI thread. A signal is what makes that
true without every caller knowing it: `logging` is called from wherever the
work happens — the GUI thread, a worker, a camera's grab thread — and Qt queues
the emission onto the thread that owns the receiver.

The handler holds a QObject rather than being one. `logging.Handler` and
`QObject` both want to be the base class and their metaclasses do not agree, so
composition here costs one attribute and avoids the argument.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal

__all__ = ["LogBridge", "install", "FORMAT", "DATE_FORMAT"]

# Level and logger name are worth the width: "which part of the system said
# this" is the first question asked of a line that reports a refused move.
FORMAT = "%(asctime)s  %(levelname)-7s  %(name)-34s  %(message)s"
DATE_FORMAT = "%H:%M:%S"


class LogBridge(QObject):
    """Carries formatted records to whatever is connected to `record`, and
    the name of an exception nothing caught to `uncaught`."""

    record = Signal(str)
    uncaught = Signal(str)


class _Handler(logging.Handler):
    def __init__(self, bridge: LogBridge):
        super().__init__()
        self._bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._bridge.record.emit(self.format(record))
        except Exception:
            # handleError respects logging.raiseExceptions and writes to
            # stderr. A logging handler that raises would turn every logged
            # line into a second failure, which is how a small problem becomes
            # an unusable application.
            self.handleError(record)


def install(level: int = logging.INFO) -> LogBridge:
    """Attach to the root logger and return the bridge to connect to.

    The root logger, not `micropick`'s: a refused HTTP request is logged by
    `urllib3` and is exactly the kind of line that explains a failed connect.
    """
    bridge = LogBridge()
    handler = _Handler(bridge)
    handler.setFormatter(logging.Formatter(FORMAT, DATE_FORMAT))

    root = logging.getLogger()
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    _hook_uncaught(bridge)
    return bridge


def _hook_uncaught(bridge: LogBridge) -> None:
    """Log what escapes everything else and tell the bridge.

    PySide hands an exception raised in a slot to `sys.excepthook`: on the
    GUI thread it would otherwise reach only a console nobody is looking
    at, and the button the operator pressed seemed to do nothing (GUI-09).
    Installed here, not with the file: a window whose log file could not
    be opened still says it.
    """
    import sys

    previous = sys.excepthook

    def hook(kind, value, trace):
        logging.getLogger("micropick.uncaught").critical(
            "uncaught %s", kind.__name__, exc_info=(kind, value, trace))
        try:
            bridge.uncaught.emit(kind.__name__)
        except RuntimeError:
            pass                        # the window and its bridge are gone
        previous(kind, value, trace)

    sys.excepthook = hook


# On disk the day is part of the time: a file is read days later.
FILE_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
FILE_PREFIX = "micropick_"


def day_file(directory, when: float):
    """The log file of the day `when` (a time.time()) is in."""
    import time
    from pathlib import Path
    return Path(directory) / f"{FILE_PREFIX}{time.strftime('%Y%m%d', time.localtime(when))}.log"


class _DayFileHandler(logging.FileHandler):
    """A file per day, by each record's own day: a program left running
    overnight writes the next day into that day's file, not the file of the
    day it started (GUI-15)."""

    def __init__(self, directory):
        import time
        self._directory = directory
        super().__init__(day_file(directory, time.time()), encoding="utf-8")

    def emit(self, record: logging.LogRecord) -> None:
        import os
        wanted = os.path.abspath(day_file(self._directory, record.created))
        if wanted != self.baseFilename:
            self.acquire()
            try:
                if self.stream is not None:
                    self.stream.close()
                    self.stream = None
                self.baseFilename = wanted      # opened by the next write
            finally:
                self.release()
        super().emit(record)


def install_file(directory, level: int = logging.INFO):
    """Also write the log to a file per day in `directory`. Returns the
    file, or None if it cannot be opened - the window must start either way.

    The log page forgets on close, and "it stopped and said something" is
    then all there is to go on. The file is what a report sends
    (`gui.report`). Uncaught exceptions reach it through the root logger
    (`install`).
    """
    import time
    from pathlib import Path

    path = day_file(Path(directory), time.time())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = _DayFileHandler(Path(directory))
    except OSError as exc:
        logging.getLogger(__name__).warning("no log file at %s: %s", path, exc)
        return None
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(FORMAT, FILE_DATE_FORMAT))
    logging.getLogger().addHandler(handler)
    from .._version import describe
    logging.getLogger(__name__).info("micropick %s; log file %s", describe(),
                                     path)
    return path
