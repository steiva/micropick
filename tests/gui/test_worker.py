"""The worker: progress, cancellation, failure, and the lifetime rule.

Needs a QApplication because the signals it is judged on are queued across a
thread boundary, and a queued signal is delivered by an event loop. Offscreen,
so this runs with no display.
"""

import os
import threading
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import (QEventLoop, QObject,      # noqa: E402
                            QTimer)
from PySide6.QtWidgets import QApplication            # noqa: E402

from micropick.gui.workers.base import Worker         # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def run(worker, timeout_ms=5000):
    """Start the worker and return once it has ended, either way.

    The final processEvents is not tidiness. `finished` is connected here to a
    lambda, so it is delivered directly on the worker thread and quits the loop
    from there - possibly ahead of the progress emissions already queued for
    this one. Draining afterwards is what makes a test that counts them
    deterministic.
    """
    loop = QEventLoop()
    worker.finished.connect(lambda *_: loop.quit())
    worker.failed.connect(lambda *_: loop.quit())
    guard = QTimer()
    guard.setSingleShot(True)
    guard.timeout.connect(loop.quit)
    guard.start(timeout_ms)
    worker.start()
    loop.exec()
    assert worker.wait(timeout_ms), "the worker thread did not end"
    QApplication.processEvents()


class Collect:
    """Signal receiver that also proves which thread the slot ran on."""

    def __init__(self):
        self.values = []
        self.threads = set()

    def __call__(self, *args):
        self.values.append(args[0] if len(args) == 1 else args)
        self.threads.add(threading.current_thread().ident)


# -- progress ---------------------------------------------------------------

def counting(steps, on_progress=None):
    for i in range(steps):
        if on_progress is not None:
            on_progress(i, steps)
    return steps


def test_progress_arrives(app):
    seen = Collect()
    worker = Worker(counting, 3)
    worker.progress.connect(seen)
    run(worker)
    assert seen.values == [(0, 3), (1, 3), (2, 3)]


def test_a_plain_callable_slot_runs_on_the_worker_thread(app):
    """The trap, asserted so it stays visible.

    Qt takes a connection's thread from the receiver object. A lambda or a
    plain function has none, so the connection is direct and the slot runs
    where the signal was emitted. A page that connects a lambda touching a
    widget is repainting from the worker thread and looks no different at the
    call site from one that does not.
    """
    seen = Collect()
    worker = Worker(counting, 2)
    worker.progress.connect(seen)              # a plain callable
    run(worker)
    assert seen.threads and threading.current_thread().ident not in seen.threads


def test_a_bound_method_of_a_qobject_runs_on_its_own_thread(app):
    """And this is the remedy the pages use."""
    class Receiver(QObject):
        def __init__(self):
            super().__init__()
            self.threads = set()

        def took(self, done, total):
            self.threads.add(threading.current_thread().ident)

    receiver = Receiver()                      # lives in this thread
    worker = Worker(counting, 2)
    worker.progress.connect(receiver.took)
    run(worker)
    # queued onto the receiver's thread, which is the one running the loop
    assert receiver.threads == {threading.current_thread().ident}


def test_started_and_finished_carry_the_result(app):
    started, done = Collect(), Collect()
    worker = Worker(counting, 4)
    worker.started.connect(started)
    worker.finished.connect(done)
    run(worker)
    assert len(started.values) == 1
    assert done.values == [4]


# -- cancellation ------------------------------------------------------------

def until_cancelled(cancel=None):
    spun = 0
    deadline = time.monotonic() + 3.0
    while not cancel.is_set():
        spun += 1
        if time.monotonic() > deadline:
            raise AssertionError("cancel was never set")
    return spun


def test_cancel_is_a_threading_event_and_stops_it(app):
    worker = Worker(until_cancelled)
    assert isinstance(worker.cancel, threading.Event)
    # A lambda, deliberately: request_cancel is a plain method to be called,
    # not a slot. Connected as one it would be queued onto the worker's own
    # thread, whose event loop is blocked inside the call it is meant to
    # interrupt, and the cancellation would never arrive.
    QTimer.singleShot(50, lambda: worker.request_cancel())
    done = Collect()
    worker.finished.connect(done)
    run(worker)
    assert done.values and done.values[0] > 0


def test_cancel_connected_as_a_slot_would_never_arrive(app):
    """Why request_cancel is documented as called and not connected.

    The worker is moved to its thread by start(), so a queued invocation of one
    of its own methods waits for the event loop that run() is blocking.
    """
    worker = Worker(until_cancelled)
    QTimer.singleShot(50, worker.request_cancel)   # the mistake
    failed = Collect()
    worker.failed.connect(failed)
    run(worker, timeout_ms=8000)
    assert failed.values and "cancel was never set" in failed.values[0]


def step_like(pause=None, stop=None):
    """Shaped like PickingSession.step, which spells cancellation `stop`."""
    return "stopped" if stop is not None and stop.is_set() else "ran"


def test_a_differently_named_cancellation_is_given_outright(app):
    worker = Worker(step_like, cancel_arg="stop")
    worker.request_cancel()
    done = Collect()
    worker.finished.connect(done)
    run(worker)
    assert done.values == ["stopped"]


def test_none_suppresses_a_hook(app):
    worker = Worker(step_like, cancel_arg=None)
    worker.request_cancel()
    done = Collect()
    worker.finished.connect(done)
    run(worker)
    assert done.values == ["ran"], "stop was injected despite cancel_arg=None"


# -- messages ----------------------------------------------------------------

def chatty(log=None):
    log("measuring scale")
    log("collected", 25, "poses")
    return None


def test_log_becomes_message(app):
    seen = Collect()
    worker = Worker(chatty)
    worker.message.connect(seen)
    run(worker)
    assert seen.values == ["measuring scale", "collected 25 poses"]


# -- what a plain callable receives ------------------------------------------

def plain(a, b):
    return a + b


def test_a_callable_without_the_hooks_gets_none_of_them(app):
    """Session.connect_robot() takes no arguments; the defaults must cost it
    nothing."""
    done = Collect()
    worker = Worker(plain, 2, 3)
    worker.finished.connect(done)
    run(worker)
    assert done.values == [5]


def swallows(**kwargs):
    return sorted(kwargs)


def test_hooks_are_never_guessed_onto_kwargs(app):
    """Forwarding `log` into something that does not understand it fails a long
    way from here, and inspection cannot tell that case from a real one."""
    done = Collect()
    worker = Worker(swallows)
    worker.finished.connect(done)
    run(worker)
    assert done.values == [[]]


def test_a_caller_supplied_value_is_not_overwritten(app):
    calls = []
    worker = Worker(counting, 2, on_progress=lambda i, n: calls.append((i, n)))
    seen = Collect()
    worker.progress.connect(seen)
    run(worker)
    assert calls == [(0, 2), (1, 2)]
    assert seen.values == []


# -- failure -----------------------------------------------------------------

def explodes():
    raise ValueError("the robot declined the move")


def test_an_exception_becomes_failed_and_not_a_dead_process(app):
    failed, done, messages = Collect(), Collect(), Collect()
    worker = Worker(explodes)
    worker.failed.connect(failed)
    worker.finished.connect(done)
    worker.message.connect(messages)
    run(worker)
    assert done.values == []
    assert failed.values == ["ValueError: the robot declined the move"]
    # the whole traceback went to the log, not to the dialog
    assert "Traceback" in messages.values[0]
    assert "in explodes" in messages.values[0]


# -- the lifetime rule -------------------------------------------------------

def test_the_caller_may_drop_its_reference_in_the_finished_slot(app):
    """The bug this class was rewritten for.

    A page naturally clears its reference to the worker in the `finished` slot,
    and Qt delivers that before the `thread.quit` connected to the same signal.
    If the worker did not hold itself, the QThread wrapper would be collected
    mid-run and abort the process with "QThread: Destroyed while thread is
    still running".
    """
    holder = {"worker": Worker(counting, 2)}
    worker = holder["worker"]

    loop = QEventLoop()
    worker.finished.connect(lambda *_: holder.pop("worker"))
    worker.finished.connect(lambda *_: loop.quit())
    guard = QTimer()
    guard.setSingleShot(True)
    guard.timeout.connect(loop.quit)
    guard.start(5000)
    worker.start()
    loop.exec()

    assert "worker" not in holder
    assert worker.wait(5000)


def test_a_worker_is_not_restarted(app):
    worker = Worker(plain, 1, 1)
    run(worker)
    with pytest.raises(RuntimeError, match="already been started"):
        worker.start()


# -- the signatures it has to fit -------------------------------------------

def test_it_fits_the_workflows_without_an_adapter():
    """The three conventional names are what the workflows actually declare.

    If one of them is renamed below, this fails here rather than as a silent
    loss of progress reporting in a five-minute sweep.
    """
    import inspect

    from micropick.workflows.calibrate_camera import calibrate_camera, run_sweep
    from micropick.workflows.picking import PickingSession
    from micropick.gui.workers.base import CANCEL_ARG, MESSAGE_ARG, PROGRESS_ARG

    for fn in (calibrate_camera, run_sweep):
        parameters = inspect.signature(fn).parameters
        for name in (PROGRESS_ARG, CANCEL_ARG, MESSAGE_ARG):
            assert name in parameters, f"{fn.__name__} no longer declares {name}"

    # step spells its cancellation differently, which is why cancel_arg exists.
    step = inspect.signature(PickingSession.step).parameters
    assert "stop" in step and CANCEL_ARG not in step
