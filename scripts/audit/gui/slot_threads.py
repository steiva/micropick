"""Which thread runs a slot, by the kind of callable connected (GUI audit, B).

    QT_QPA_PLATFORM=offscreen conda run -n lab python scripts/audit/gui/slot_threads.py

Two senders: a `Worker` (it lives in its own thread once started) and a
QObject that lives in the GUI thread and whose signal is emitted from a
worker's thread (as `Session.camera_opened`, `tip_changed`, `lights_changed`
are). For each: a bound method of a QObject, a lambda, a nested function, a
functools.partial of a bound method. Prints where each one ran.
"""
import functools
import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QApplication

from micropick.gui.workers import Worker

app = QApplication.instance() or QApplication([])
GUI = threading.get_ident()
ran: dict[str, bool] = {}


def note(name):
    ran[name] = threading.get_ident() == GUI


class Receiver(QObject):
    def method(self, *_):
        note(self.objectName() + " bound method")

    def helper(self, tag, *_):
        note(tag)


class Sender(QObject):
    """Lives in the GUI thread, like the Session."""
    changed = Signal(object)


def run(case):
    receiver = Receiver()
    receiver.setObjectName(case)
    sender = Sender()

    def nested(*_):
        note(f"{case} nested function")

    if case == "worker signal":
        worker = Worker(lambda: 1)
        signal = worker.finished
    else:
        worker = Worker(lambda: sender.changed.emit(1))
        signal = sender.changed
    signal.connect(receiver.method)
    signal.connect(lambda *_: note(f"{case} lambda"))
    signal.connect(nested)
    signal.connect(functools.partial(receiver.helper, f"{case} partial of a bound method"))
    worker.start()
    end = time.monotonic() + 3
    while time.monotonic() < end and len([k for k in ran if k.startswith(case)]) < 4:
        app.processEvents()
        time.sleep(0.01)
    worker.wait()
    app.processEvents()


for case in ("worker signal", "gui-thread object's signal emitted on a worker"):
    run(case)
import PySide6
print("PySide6", PySide6.__version__)
for name, on_gui in ran.items():
    print(f"{'GUI thread   ' if on_gui else 'WORKER THREAD'}  {name}")
