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
import time
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QThread, Signal

__all__ = ["Worker", "any_running", "activity", "describe_error"]

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


def any_running(*, robot_only: bool = False) -> bool:
    """Whether any worker's thread is still running, on any page. For an act
    that must not overlap whatever else is talking to the robot - the status
    bar's Home - and cannot know which page started it. `robot_only` leaves
    out the workers marked `robot = False`: a camera opening, a look for the
    marker, a picture being saved."""
    return any(worker.running and (worker.robot or not robot_only)
               for worker in list(_RUNNING))


NO_ROBOT_ANSWER = ("The robot did not answer. Check that it is switched on "
                   "and has finished starting up (the light on its front "
                   "stops blinking), and that the cable is plugged in.")


def describe_error(exc: BaseException) -> str:
    """What a failure says on screen; the traceback goes to the log.

    This application's own errors (a class from `micropick`) are written as
    sentences for the operator, so the sentence alone is shown: "SessionError:"
    in front of it is a word for whoever wrote it. A robot that does not
    answer is said in words, with what to check. Anything else is unexpected
    and keeps its class name, which is what someone reading the log looks
    for.
    """
    try:
        import requests
        if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
            return NO_ROBOT_ANSWER
    except ImportError:                      # the gui extra without requests
        pass
    text = str(exc)
    if type(exc).__module__.startswith("micropick") and text:
        return text
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def activity() -> list["Worker"]:
    """The workers running now, the longest-running first: what the status
    bar's activity indicator shows. Each says `what` it is doing, since
    when (`started_at`, monotonic) and the last line it logged."""
    running = [worker for worker in list(_RUNNING) if worker.running]
    return sorted(running, key=lambda worker: worker.started_at or 0.0)


class Worker(QObject):
    """Runs one blocking callable on a QThread of its own."""

    started = Signal()
    progress = Signal(int, int)
    message = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[..., Any], *args,
                 progress_arg=AUTO, cancel_arg=AUTO, message_arg=AUTO,
                 what: str | None = None,
                 parent: QObject | None = None, **kwargs):
        """`*_arg` name the parameters to inject.

        Left alone they follow the convention above and are passed only to a
        callable that declares them, so the defaults cost nothing at a call
        site with neither — `Session.connect_robot()` takes no arguments at
        all. Named outright they are passed regardless, which is how a callable
        that spells one differently is driven: `PickingSession.step` calls its
        cancellation `stop`. None suppresses one.

        `what` is the job in a few words, for the activity indicator
        ("calibrating the pipette"); it is not passed to the callable.

        A name is never guessed onto a `**kwargs` signature. Forwarding `log`
        into a callable that does not understand it produces a failure a long
        way from here, and inspection cannot tell the two cases apart.
        """
        super().__init__(parent)
        # False for a job that never talks to the robot; see any_running.
        self.robot = True
        self._fn = fn
        self._args = args
        self._kwargs = dict(kwargs)
        self._names = {"progress": (progress_arg, PROGRESS_ARG),
                       "cancel": (cancel_arg, CANCEL_ARG),
                       "message": (message_arg, MESSAGE_ARG)}
        self.cancel = threading.Event()
        self._thread: QThread | None = None
        # Read by the activity indicator. Plain attributes, written from the
        # worker's thread: one reference assignment each, and a reader that
        # sees the previous value only shows it for one more tick.
        self.what = what
        self.started_at: float | None = None
        self.last_message = ""
        self.last_progress: tuple[int, int] | None = None
        self.error: BaseException | None = None

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
        self.last_progress = (int(done), int(total))
        self.progress.emit(int(done), int(total))

    def _on_message(self, *parts) -> None:
        text = " ".join(str(p) for p in parts)
        line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        if line:
            self.last_message = line
        self.message.emit(text)

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
            # The exception itself, for a slot that has to tell one kind from
            # another (a cancel from a failure) without reading the words.
            self.error = exc
            self.failed.emit(describe_error(exc))
        else:
            self.finished.emit(result)

    def start(self) -> QThread:
        if self._thread is not None:
            raise RuntimeError("this worker has already been started")
        thread = QThread()
        self._thread = thread
        self.started_at = time.monotonic()
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
