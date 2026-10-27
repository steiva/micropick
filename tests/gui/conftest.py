"""What every GUI test leaves behind is deleted after it.

The tests end with `deleteLater()`, which runs only from an event loop:
`processEvents()` does not deliver a deferred delete, and no test runs
`exec()`. So every window lived on - fifty main windows by the end of the
suite, each with its timers and its robot monitor - and every later
`processEvents()` served all of them, the suite growing slower test by
test. Delivered here, after each test.
"""

import pytest


@pytest.fixture(autouse=True)
def _delete_what_the_test_left():
    yield
    try:
        from PySide6.QtCore import QCoreApplication, QEvent
    except ImportError:                      # the gui extra is not installed
        return
    if QCoreApplication.instance() is not None:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
