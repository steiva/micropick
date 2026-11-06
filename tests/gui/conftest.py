"""What every GUI test leaves behind is deleted after it.

The tests end with `deleteLater()`, which runs only from an event loop:
`processEvents()` does not deliver a deferred delete, and no test runs
`exec()`. So every window lived on - fifty main windows by the end of the
suite, each with its timers and its robot monitor - and every later
`processEvents()` served all of them, the suite growing slower test by
test. Delivered here, after each test.

A camera still grabbing after a test is closed too: each grabs at 30 fps
for the rest of the run, and later files grew slow by them. Most are a
page's own open still on its way as the test ended, so no warning.

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
    if QCoreApplication.instance() is not None:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    for thread in threading.enumerate():
        if thread.name.startswith("grab-"):
            camera = getattr(getattr(thread, "_target", None), "__self__",
                             None)
            if camera is not None:
                camera.close()



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
