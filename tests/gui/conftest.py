"""What every GUI test leaves behind is deleted after it.

The tests end with `deleteLater()`, which runs only from an event loop:
`processEvents()` does not deliver a deferred delete, and no test runs
`exec()`. So every window lived on - fifty main windows by the end of the
suite, each with its timers and its robot monitor - and every later
`processEvents()` served all of them, the suite growing slower test by
test. Delivered here, after each test.

A camera still grabbing after a test is closed too: each grabs at 30 fps
for the rest of the run, and later files grew slow by them. Most are a
page's own open still on its way as the test ended, so no warning. That
open is a worker's: before the deferred deletes, every running worker but a
background one is waited for (up to 5 s each), so a camera is not met half
made. A grab thread listed but not yet started (`threading.enumerate` lists
it from `start()`, before it runs) is skipped, its close would raise
"cannot join thread before it is started"; a close that raises it all the
same is tried once more, after a short join once the thread is alive.

And the root logger's handlers and `sys.excepthook` are put back: every
MainWindow adds a bridge to its log page and a file handler (one window in
the program), so by the end of the suite every line logged went through
each window's.
"""

import logging
import sys
import threading
import time

import pytest


@pytest.fixture(autouse=True)
def _delete_what_the_test_left():
    root = logging.getLogger()
    handlers, hook = list(root.handlers), sys.excepthook
    yield
    for handler in root.handlers[:]:
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    sys.excepthook = hook
    try:
        from PySide6.QtCore import QCoreApplication, QEvent
    except ImportError:                      # the gui extra is not installed
        return
    from micropick.gui.workers import base as workers
    for worker in list(workers._RUNNING):
        if worker.running and not worker.background:
            worker.wait(5_000)
    if QCoreApplication.instance() is not None:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    for thread in threading.enumerate():
        if not thread.name.startswith("grab-") or not thread.is_alive():
            continue
        camera = getattr(getattr(thread, "_target", None), "__self__", None)
        if camera is None:
            continue
        try:
            camera.close()
        except RuntimeError:
            _until_alive(thread)
            thread.join(0.1)
            camera.close()


def _until_alive(thread, timeout_s=1.0):
    end = time.monotonic() + timeout_s
    while not thread.is_alive() and time.monotonic() < end:
        time.sleep(0.005)


@pytest.fixture
def until():
    """`until(condition, timeout_s=10.0, what=None)`: events run until
    `condition()` holds, and it returns at once - no events after, which
    could start a job chained on a zero timer and make it False again. A
    wait that runs out fails the test there, naming `what`."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    def wait(condition, timeout_s=10.0, what=None):
        end = time.monotonic() + timeout_s
        while True:
            if condition():
                return
            if time.monotonic() >= end:
                pytest.fail(f"Waited {timeout_s:g} s for "
                            f"{what or 'a condition'}: it never held.")
            app.processEvents()
            time.sleep(0.005)

    return wait


@pytest.fixture
def robot_free(until):
    """`robot_free(session_or_window, *panels)`: until no one holds the
    robot's lease and no panel given - with a window, every `JogPanel` in
    it - has `job_pending`. Never a panel's `busy`: that goes False as the
    worker's thread ends, before the job's end reaches the page."""
    from micropick.gui.widgets.jog_panel import JogPanel

    def wait(holder, *panels, timeout_s=10.0):
        session = getattr(holder, "session", holder)
        if not panels and holder is not session:
            panels = tuple(holder.findChildren(JogPanel))
        until(lambda: not session.robot_lease.busy
              and not any(p.job_pending for p in panels),
              timeout_s, "the robot free and no job pending")

    return wait


@pytest.fixture
def read_pose(robot_free):
    """`read_pose(panel)`: the pose read for sure. A refresh asked while
    the panel is busy is dropped, so it waits for the panel first."""

    def read(panel):
        robot_free(panel.session, panel)
        panel.refresh_position()
        robot_free(panel.session, panel)

    return read


@pytest.fixture(scope="module")
def themed(app):
    """The app in the program's theme for a module, put back after it.

    The theme is the whole application's style sheet, and with it every
    widget added anywhere is styled: a MainWindow took 1.1 s to build
    instead of 0.25 s for every test after the first module to set it.
    """
    from micropick.gui import theme
    sheet, palette, font = app.styleSheet(), app.palette(), app.font()
    style = app.style().name()
    theme.apply_theme(app)
    yield app
    app.setStyleSheet(sheet)
    app.setPalette(palette)
    app.setFont(font)
    app.setStyle(style)


def _places(root) -> dict:
    """Where every widget shown under `root` stands, in `root`'s pixels."""
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QWidget
    return {widget: widget.mapTo(root, QPoint(0, 0))
            for widget in root.findChildren(QWidget)
            if widget.isVisible() and not widget.isWindow()
            and not widget.objectName().startswith("qt_")}


@pytest.fixture
def stands_still(app):
    """`stands_still(root, act)`: fails naming what `act` moved under `root`
    (#106). What appears or goes is not asked about, only what stayed. What
    the operator chooses may move what is under it: see test_nothing_shifts.
    `but` is a widget whose inside may move: a path's items, as one folds;
    it must stay itself."""

    def settle():
        # A scroll column measures itself on a timer of its own.
        end = time.monotonic() + 0.15
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.005)

    def check(root, act, but=None):
        settle()
        before = _places(root)
        act()
        settle()
        after = _places(root)
        if but is not None:
            before = {w: at for w, at in before.items()
                      if not but.isAncestorOf(w)}
        moved = [f"{type(w).__name__} {w.objectName() or getattr(w, 'text', str)()!r}"
                 f" {before[w].toTuple()} -> {after[w].toTuple()}"
                 for w in before if w in after and before[w] != after[w]]
        assert not moved, "\n".join(moved[:12])

    return check
