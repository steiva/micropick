"""GUI-08: a click on the picture moves the robot after the page was left.

    conda run -n lab python scripts/audit/gui/click_after_leaving.py

`CameraView` sends `clicked` a double-click interval after the release, to
tell a click from a double click. Nothing takes the pending click back when
the view is hidden. Manual pick's Run: a left click on the picture, then
Home within the interval. Counted: the mock robot's moves afterwards.
"""
import _mock

app = _mock.app()
window = _mock.window(speed_mm_s=0.0)
session, robot = window.session, window.session.robot

from PySide6.QtCore import QPoint, Qt                              # noqa: E402
from PySide6.QtTest import QTest                                   # noqa: E402
from PySide6.QtWidgets import QApplication                         # noqa: E402

session.motion.camera_to((150.0, 150.0))        # the middle of the deck
window.open_task_step("manual", "work")
page = window.manual_page
session.open_camera(session.upper_camera_label)
_mock.until(lambda: page.view.held_frame is not None and _mock.robot_free(window))
_mock.spin(0.3)
view = page.view
spot = view._shown.center() + QPoint(-120, 40)
before = robot.moves
sent = []
view.clicked.connect(lambda u, v: sent.append((round(u), round(v))))
QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=spot)
print("click pending:", view._pending_click is not None,
      "| double-click interval ms:", QApplication.doubleClickInterval())
window.open_home()
print("page on screen:", page.isVisible(), "| shown:", window._page)
_mock.spin(QApplication.doubleClickInterval() / 1000 + 0.5)
_mock.until(lambda: _mock.robot_free(window))
from micropick.gui.widgets.toast import toast_of                   # noqa: E402
print("clicked sent after the page was hidden:", sent,
      "| toast:", repr(toast_of(window).text()))
print("moves made after Home was shown:", robot.moves - before)
window.close()
