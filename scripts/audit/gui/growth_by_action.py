"""Category D: which action leaves widgets behind (GUI audit, after growth.py).

    conda run -n lab python scripts/audit/gui/growth_by_action.py

Each action 10 times; then the widgets under the window and the top-level
widgets that were added per repetition, by class and parent.
"""
import collections
import gc

import _mock

app = _mock.app()
window = _mock.window(speed_mm_s=0.0)
session = window.session

from PySide6.QtWidgets import QApplication, QWidget                # noqa: E402

from micropick.gui import tasks                                    # noqa: E402


def kinds():
    # Outside `app.exec()` a deleteLater waits: send them, as the running
    # program's event loop does.
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    gc.collect()
    QApplication.processEvents()
    inside = collections.Counter(
        f"{type(w).__name__} in {type(w.parentWidget()).__name__}"
        for w in window.findChildren(QWidget))
    top = collections.Counter(f"top-level {type(w).__name__}"
                              for w in QApplication.topLevelWidgets())
    return inside + top


def sheet(item):
    def action():
        window.open_robot_setup()
        window.open_setup_item(item, from_setup=True)
        _mock.spin(0.05)
        window._close_sheets()
    return action


def step(task, name):
    def action():
        window.open_task_step(task, name)
        _mock.spin(0.03)
        window.open_home()
    return action


def point():
    session.add_memo_point(100.0, 100.0, 50.0)
    _mock.spin(0.02)
    session.delete_memo_point(session.memo_points[-1].name)


def settings():
    window.open_settings("log")
    _mock.spin(0.03)
    window._close_sheets()


actions = {f"step {task}:{name}": step(task, name)
           for task in tasks.TASKS for name in tasks.steps(task)}
actions.update({f"sheet {item}": sheet(item) for item in
                ("upper_camera", "camera_calibration", "platforms",
                 "calibration_position", "shake_position", "dish_position")})
actions["memo point added and deleted"] = point
actions["settings opened and closed"] = settings
actions["home"] = window.open_home

for action in actions.values():      # everything built and shown once
    action()
_mock.spin(0.5)
for name, action in actions.items():
    before = kinds()
    for _ in range(10):
        action()
    _mock.spin(0.3)
    grown = kinds() - before
    total = sum(grown.values())
    if total:
        print(f"\n{name}: +{total / 10:.1f} widgets per time")
        for kind, n in grown.most_common(6):
            print(f"    +{n / 10:.1f}  {kind}")
window.close()
