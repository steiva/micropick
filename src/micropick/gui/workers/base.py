"""One worker for every blocking call.

Not a subclass per operation. A calibration sweep, an HTTP connect and a jog
step differ in what they call, not in how they are run: something blocks, the
window must stay alive, progress has to arrive, and a failure must become a
message rather than a stack trace on a dead application.

Fitting the callables, not adapting them
----------------------------------------
The workflows already take `on_progress(i, total)`, a `cancel` that is a
`threading.Event`, and `log=`. Those are their interface and the reason they can
be driven from a notebook as easily as from here, so the worker fits itself to
them: it inspects the callable and passes only what that callable declares.
`calibrate_camera(..., on_progress=, cancel=, log=)` therefore needs no wrapper,
and neither does `Session.connect_robot()`, which declares none of them.

Cancellation stays a `threading.Event` and never becomes a Qt primitive. The
event is what `workflows` understands, and teaching them about Qt to run them
from a window would be the wrong direction entirely.

Signals cross the thread boundary; nothing else does. `progress` and `message`
are emitted from the worker thread and Qt queues them onto the GUI thread, so a
workflow's `on_progress` callback stays a plain function call and the widget it
ends up changing is still only touched from the thread that owns it.
"""

from __future__ import annotations

import inspect
import threading
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal

__all__ = ["Worker"]

# The names the workflows in this repository use. They are a convention rather
# than a guess: `on_progress`, `cancel` and `log` are what `calibrate_camera`
# and `run_sweep` declare, and a callable that declares none of them simply
# receives none of them.
PROGRESS_ARG = "on_progress"
CANCEL_ARG = "cancel"
MESSAGE_ARG = "log"


# Workers that are still running. A worker must outlive its thread, and the
# caller is the wrong place to enforce that: a page naturally clears its
# reference in the `finished` slot, which Qt delivers *before* the thread it is
# connected to has quit. Dropping the last reference there collects the QThread
# wrapper mid-run and aborts the process with "QThread: Destroyed while thread
# is still running". The worker therefore holds itself until its thread object
# is destroyed, and the caller's reference is a convenience.
_RUNNING: set["Worker"] = set()


class Worker(QObject):
    """Runs one blocking callable on a QThread of its own."""

    started = Signal()
    progress = Signal(int, int)
    message = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[..., Any], *args,
                 progress_arg: str | None = PROGRESS_ARG,
                 cancel_arg: str | None = CANCEL_ARG,
                 message_arg: str | None = MESSAGE_ARG,
                 parent: QObject | None = None, **kwargs):
        """`*_arg` name the parameters to inject, for a callable that spells
        them differently — `PickingSession.step` calls its cancellation `stop`.
        Passing None suppresses one. A name the callable does not declare is
        skipped rather than forced in, so the default names cost nothing at a
        call site that has neither.
        """
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._kwargs = dict(kwargs)
        self._names = {"progress": progress_arg, "cancel": cancel_arg,
                       "message": message_arg}
        self.cancel = threading.Event()
        self._thread: QThread | None = None

    # -- what the callable is actually given --------------------------------

    def _accepts(self, name: str | None) -> bool:
        if name is None or name in self._kwargs:
            return False
        try:
            parameters = inspect.signature(self._fn).parameters
        except (TypeError, ValueError):      # builtins, C callables
            return False
        if name in parameters:
            return True
        return any(p.kind is inspect.Parameter.VAR_KEYWORD
                   for p in parameters.values())

    def _call_kwargs(self) -> dict:
        kwargs = dict(self._kwargs)
        if self._accepts(self._names["progress"]):
            kwargs[self._names["progress"]] = self._on_progress
        if self._accepts(self._names["cancel"]):
            kwargs[self._names["cancel"]] = self.cancel
        if self._accepts(self._names["message"]):
            kwargs[self._names["message"]] = self._on_message
        return kwargs

    # These are plain functions as far as the workflow is concerned; the signal
    # emission behind them is what crosses to the GUI thread.
    def _on_progress(self, done: int, total: int) -> None:
        self.progress.emit(int(done), int(total))

    def _on_message(self, *parts) -> None:
        self.message.emit(" ".join(str(p) for p in parts))

    # -- running -------------------------------------------------------------

    def run(self) -> None:
        """The thread's entry point. Never raises."""
        self.started.emit()
        try:
            result = self._fn(*self._args, **self._call_kwargs())
        except BaseException as exc:
            # Broad on purpose: an exception reaching a QThread's run() is not
            # caught by anything above it, and on some platforms it takes the
            # process down. It becomes a message here instead, and the full
            # traceback goes to the log through `message`.
            self.message.emit(traceback.format_exc().rstrip())
            # The class's short name and its own text. The last line of the
            # traceback carries the full dotted path, which in a dialog is a
            # module name in front of the sentence that matters.
            self.failed.emit(f"{type(exc).__name__}: {exc}" if str(exc)
                             else type(exc).__name__)
        else:
            self.finished.emit(result)

    def start(self) -> QThread:
        if self._thread is not None:
            raise RuntimeError("this worker has already been started")
        thread = QThread()
        self._thread = thread
        self.moveToThread(thread)
        thread.started.connect(self.run)
        self.finished.connect(thread.quit)
        self.failed.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        # destroyed, not finished: deleteLater is processed on the thread that
        # owns the QThread object, which is the one that called start(), so by
        # the time this runs the thread has really ended.
        thread.destroyed.connect(lambda *_: _RUNNING.discard(self))
        _RUNNING.add(self)
        thread.start()
        return thread

    def request_cancel(self) -> None:
        """Ask the callable to stop. It stops where it chooses to look."""
        self.cancel.set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def wait(self, timeout_ms: int = 30_000) -> bool:
        """Block until the thread ends. For shutdown and for tests."""
        if self._thread is None:
            return True
        return self._thread.wait(timeout_ms)
