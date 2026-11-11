"""Category D: what grows as the program is used (GUI audit).

    conda run -n lab python scripts/audit/gui/growth.py [--rounds 40]

The mock window. Per round: every step of every task shown, each Robot
setup sheet opened and closed, Settings opened and closed, a memo point
added and deleted, and 5 jog steps. After each quarter of the rounds: the
process's resident memory, the number of widgets and QObjects under the
window, top-level widgets, live threads, and the receivers of a few session
signals (a connection made again at each show would grow them).
"""
import argparse
import gc
import resource
import sys
import threading

import _mock

parser = argparse.ArgumentParser()
parser.add_argument("--rounds", type=int, default=40)
args = parser.parse_args()

app = _mock.app()
window = _mock.window(speed_mm_s=0.0)
session = window.session

from PySide6.QtCore import QObject, SIGNAL                         # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget                # noqa: E402

from micropick.gui import tasks                                    # noqa: E402


def rss_mb():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1e6
    except ImportError:
        return peak / (1e6 if sys.platform == "darwin" else 1e3)


def census():
    # Outside `app.exec()` a deleteLater waits: send them, as the running
    # program's event loop does.
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    gc.collect()
    QApplication.processEvents()
    return (rss_mb(), len(window.findChildren(QWidget)),
            len(window.findChildren(QObject)),
            len(QApplication.topLevelWidgets()), threading.active_count(),
            session.receivers(SIGNAL("robot_busy_changed(PyObject)")),
            session.receivers(SIGNAL("profile_changed(PyObject)")),
            session.receivers(SIGNAL("tip_changed(PyObject)")))


def one_round():
    for task in tasks.TASKS:
        for step in tasks.steps(task):
            window.open_task_step(task, step)
            _mock.spin(0.03)
    panel = window.manual_page.jog
    window.open_task_step("manual", "work")
    for _ in range(5):
        _mock.until(lambda: not panel.job_pending)
        panel.step("x", +1)
    _mock.until(lambda: _mock.robot_free(window))
    point = session.add_memo_point(100.0, 100.0, 50.0)
    _mock.spin(0.02)
    session.delete_memo_point(session.memo_points[-1].name)
    window.open_robot_setup()
    for item in ("upper_camera", "camera_calibration", "platforms",
                 "calibration_position", "shake_position", "dish_position"):
        window.open_setup_item(item, from_setup=True)
        _mock.spin(0.05)
        window._close_sheets()
    window.open_settings("log")
    _mock.spin(0.03)
    window._close_sheets()
    window.open_home()


print("| rounds | RSS MB | widgets | QObjects | top-level | threads | "
      "robot_busy receivers | profile_changed | tip_changed |")
print("|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
one_round()                     # everything built and shown once
_mock.spin(1.0)
print("| 1 | " + " | ".join(f"{v:.0f}" for v in census()) + " |")
quarter = max(1, args.rounds // 4)
for done in range(1, args.rounds + 1):
    one_round()
    if done % quarter == 0:
        _mock.spin(0.5)
        print(f"| {done + 1} | " + " | ".join(f"{v:.0f}" for v in census()) + " |",
              flush=True)
window.close()
