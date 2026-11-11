"""Test suite audit: what one MainWindow leaves alive after the tests'
teardown (hide, session.shutdown, deleteLater, deferred deletes delivered).

    QT_QPA_PLATFORM=offscreen MICROPICK_ROOT=<empty folder> \
        conda run -n lab python scripts/audit/left_after_window.py

Prints the widgets the application still knows, by class, top-level ones
apart, then the Python-side referrers of one of them.
"""

import gc
from collections import Counter

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from micropick.gui.app import Options
from micropick.gui.shell import MainWindow
from micropick.hardware.mock import MockRobot

MockRobot.SPEED_MM_S = 0.0
app = QApplication.instance() or QApplication([])


def once():
    w = MainWindow(Options(mock=True))
    w.show()
    w.session.probe_robot()
    w.session.new_run()
    app.processEvents()
    w.hide()
    w.session.shutdown()
    w.deleteLater()
    del w
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


for i in range(3):
    once()
    print(f"after window {i + 1}: {len(app.allWidgets())} widgets, "
          f"{len(app.topLevelWidgets())} top-level")
gc.collect()
QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
print(f"after gc.collect(): {len(app.allWidgets())} widgets, "
      f"{len(app.topLevelWidgets())} top-level")
once()
tops = app.topLevelWidgets()
print(Counter(type(w).__name__ for w in tops).most_common(12))
print(Counter(type(w.parent()).__name__ for w in app.allWidgets()).most_common(6))
if tops:
    one = tops[0]
    print("one of them:", type(one).__name__, repr(one.objectName()))
    for ref in gc.get_referrers(one)[:6]:
        print("  held by", type(ref).__name__, str(ref)[:140])
