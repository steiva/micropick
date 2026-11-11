"""Category F: what an exception in a slot does.

    conda run -n lab python scripts/audit/gui/slot_exception.py

1. A button's slot raises: is `sys.excepthook` called (so the log file gets
   "uncaught"), and is the operator told (a toast)?
2. A jog panel's job whose `then` raises (any page's callback after a move):
   is the pad usable afterwards?
"""
import logging

import _mock

app = _mock.app()
window = _mock.window(speed_mm_s=0.0)
session = window.session

from PySide6.QtWidgets import QPushButton                          # noqa: E402

from micropick.gui.widgets.toast import toast_of                   # noqa: E402

records = []


class Catch(logging.Handler):
    def emit(self, record):
        if record.name == "micropick.uncaught":
            records.append(record.getMessage())


logging.getLogger().addHandler(Catch())

print("log file:", window.log_file)
button = QPushButton("raise", window)
button.clicked.connect(lambda: 1 / 0)
before = toast_of(window).text()
button.click()
_mock.spin(0.2)
print("1. excepthook logged:", records)
print("   toast changed:", toast_of(window).text() != before,
      repr(toast_of(window).text()))

window.open_task_step("manual", "work")
_mock.until(lambda: _mock.robot_free(window))
panel = window.manual_page.jog
print("2. pad usable before:", panel._enabled)


def then(_result):
    raise ValueError("a page's callback failed")


panel.run_job(lambda log: "done", moves=False, then=then)
_mock.until(lambda: not panel.job_pending)
_mock.spin(0.3)
print("   pad usable after a `then` that raised:", panel._enabled,
      "| lease free:", not session.robot_lease.busy,
      "| pad buttons enabled:", panel.pad.isEnabled())
print("   excepthook logged:", records[1:])
print("   toast:", repr(toast_of(window).text()))
window.close()
