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
    """Carries formatted records to whatever is connected to `record`."""

    record = Signal(str)


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
    return bridge


# On disk the day is part of the time: a file is read days later.
FILE_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
FILE_PREFIX = "micropick_"


def install_file(directory, level: int = logging.INFO):
    """Also write the log to a file per day in `directory`, and log what
    escapes everything else. Returns the file, or None if it cannot be
    opened - the window must start either way.

    The log page forgets on close, and "it stopped and said something" is
    then all there is to go on. The file is what a report sends
    (`gui.report`). Uncaught exceptions go to it too: on the GUI thread one
    would otherwise reach only a console nobody is looking at.
    """
    import sys
    import time
    from pathlib import Path

    path = Path(directory) / f"{FILE_PREFIX}{time.strftime('%Y%m%d')}.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
    except OSError as exc:
        logging.getLogger(__name__).warning("no log file at %s: %s", path, exc)
        return None
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(FORMAT, FILE_DATE_FORMAT))
    logging.getLogger().addHandler(handler)

    previous = sys.excepthook

    def hook(kind, value, trace):
        logging.getLogger("micropick.uncaught").critical(
            "uncaught %s", kind.__name__, exc_info=(kind, value, trace))
        previous(kind, value, trace)

    sys.excepthook = hook
    from .._version import describe
    logging.getLogger(__name__).info("micropick %s; log file %s", describe(),
                                     path)
    return path
