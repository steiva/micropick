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
are emitted from the worker thread, and a workflow's `on_progress` stays a
plain function call on that thread.

Connect a bound method, never a lambda
--------------------------------------
Qt decides a connection's thread from the **receiver object**. A slot that is a
bound method of a QObject is queued onto that object's thread, which is what
keeps a widget touched only from the thread that owns it. A lambda or a plain
function has no receiver, so Qt connects it **directly** and runs it on the
thread that emitted — here, the worker's. `worker.finished.connect(lambda _:
self.refresh())` therefore repaints from the worker thread while
`worker.finished.connect(self._done)` does not, and the two look identical at
the call site. Connect bound methods.

`request_cancel` is called, not connected
-----------------------------------------
It is an ordinary method, and the `threading.Event` behind it is what is safe
to touch from anywhere. Connecting it as a slot does not work: after `start()`
the worker lives in its own thread, whose event loop is blocked inside `run()`,
so a queued invocation waits for the very call it is meant to interrupt.
"""

from __future__ import annotations

import inspect
import threading
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QThread, Signal

__all__ = ["Worker"]

# The names the workflows in this repository use: `calibrate_camera` and
# `run_sweep` declare exactly these. They are a convention, so they are applied
# only where the callable declares them.
PROGRESS_ARG = "on_progress"
CANCEL_ARG = "cancel"
MESSAGE_ARG = "log"

# Distinguishes "follow the convention" from "the caller named this parameter".
# A name given outright is passed whatever the signature says, because the
# caller knows something inspection does not; a name that came from the
# convention is passed only when the callable declares it.
AUTO = object()


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
                 progress_arg=AUTO, cancel_arg=AUTO, message_arg=AUTO,
                 parent: QObject | None = None, **kwargs):
        """`*_arg` name the parameters to inject.

        Left alone they follow the convention above and are passed only to a
        callable that declares them, so the defaults cost nothing at a call
        site with neither — `Session.connect_robot()` takes no arguments at
        all. Named outright they are passed regardless, which is how a callable
        that spells one differently is driven: `PickingSession.step` calls its
        cancellation `stop`. None suppresses one.

        A name is never guessed onto a `**kwargs` signature. Forwarding `log`
        into a callable that does not understand it produces a failure a long
        way from here, and inspection cannot tell the two cases apart.
        """
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._kwargs = dict(kwargs)
        self._names = {"progress": (progress_arg, PROGRESS_ARG),
                       "cancel": (cancel_arg, CANCEL_ARG),
                       "message": (message_arg, MESSAGE_ARG)}
        self.cancel = threading.Event()
        self._thread: QThread | None = None

    # -- what the callable is actually given --------------------------------

    def _declares(self, name: str) -> bool:
        try:
            parameters = inspect.signature(self._fn).parameters
        except (TypeError, ValueError):      # builtins, C callables
            return False
        return name in parameters

    def _slot(self, which: str) -> str | None:
        """The parameter to inject for one hook, or None."""
        given, conventional = self._names[which]
        if given is None:
            return None
        name = conventional if given is AUTO else given
        if name in self._kwargs:             # the caller supplied their own
            return None
        if given is not AUTO:
            return name
        return name if self._declares(name) else None

    def _call_kwargs(self) -> dict:
        kwargs = dict(self._kwargs)
        values = {"progress": self._on_progress, "cancel": self.cancel,
                  "message": self._on_message}
        for which, value in values.items():
            name = self._slot(which)
            if name is not None:
                kwargs[name] = value
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
        # DirectConnection, deliberately. The worker lives in `thread` and the
        # QThread object lives in the thread that called start(), so an auto
        # connection here is a queued one: quit() would be delivered by the
        # caller's event loop. Anything that ends that loop and then waits -
        # a test, a shutdown - is then waiting on the only thread that could
        # have delivered the quit. QThread::quit is thread-safe, so running it
        # where the signal is emitted is both correct and unblocking.
        self.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        self.failed.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        thread.finished.connect(thread.deleteLater)
        # destroyed, not finished: deleteLater is processed on the thread that
        # owns the QThread object, which is the one that called start(), so by
        # the time this runs the thread has really ended.
        thread.destroyed.connect(lambda *_: _RUNNING.discard(self))
        _RUNNING.add(self)
        thread.start()
        return thread

    def request_cancel(self) -> None:
        """Ask the callable to stop. It stops where it chooses to look.

        Call this; do not connect it to a signal. See the module docstring.
        """
        self.cancel.set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def wait(self, timeout_ms: int = 30_000) -> bool:
        """Block until the thread ends. For shutdown and for tests.

        A thread already deleted is a thread already finished: deleteLater only
        runs once the thread has ended, so the deleted wrapper is an answer
        rather than an error.
        """
        if self._thread is None:
            return True
        try:
            return self._thread.wait(timeout_ms)
        except RuntimeError:
            return True
