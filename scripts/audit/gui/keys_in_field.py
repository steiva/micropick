"""Category C: which keys move the robot while a field has the keyboard.

    conda run -n lab python scripts/audit/gui/keys_in_field.py

Manual pick's Run under --mock, a tip on. The keyboard is given to a number
field (`in_place_amount`), then to a point's name being edited (a QLineEdit),
and each robot key is pressed once (QTest, which goes through Qt's shortcut
map as a real key does). Reported per key: whether the mock robot moved,
whether the pad's step changed, whether the field's value changed. The pad
says "Keys now control the input field, not the robot." all the while.
"""
import _mock

app = _mock.app()
window = _mock.window(speed_mm_s=0.0)
session, robot = window.session, window.session.robot

from PySide6.QtCore import Qt                                      # noqa: E402
from PySide6.QtTest import QTest                                   # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit              # noqa: E402

window.activateWindow()
window.open_task_step("manual", "work")
page = window.manual_page
_mock.until(lambda: _mock.robot_free(window))
print("page enabled:", page.isEnabled(), " need:", getattr(window._need, "text", None))

KEYS = [("Left", Qt.Key.Key_Left), ("Right", Qt.Key.Key_Right),
        ("Up", Qt.Key.Key_Up), ("Down", Qt.Key.Key_Down),
        ("PgUp", Qt.Key.Key_PageUp), ("PgDown", Qt.Key.Key_PageDown),
        ("+", Qt.Key.Key_Plus), ("-", Qt.Key.Key_Minus),
        ("U", Qt.Key.Key_U), ("A", Qt.Key.Key_A), ("D", Qt.Key.Key_D),
        ("R", Qt.Key.Key_R)]


def press_all(field, read):
    for name, key in KEYS:
        field.setFocus()
        _mock.spin(0.05)
        assert QApplication.focusWidget() is field or field.isAncestorOf(
            QApplication.focusWidget()), "the field lost the keyboard"
        moves, step, value, in_tip = (robot.moves, page.jog.pad.step,
                                      read(), session.in_tip)
        QTest.keyClick(QApplication.focusWidget(), key)
        _mock.until(lambda: _mock.robot_free(window))
        did = []
        if robot.moves != moves:
            did.append("ROBOT MOVED")
        if page.jog.pad.step != step:
            did.append("pad step changed")
        if session.in_tip != in_tip:
            did.append("liquid moved")
        if read() != value:
            did.append("field changed")
        print(f"  {name:7s} {', '.join(did) or 'nothing'}")


print("\nA number field (In place's amount) has the keyboard; pad note:",
      repr(page.jog.pad.note.text()[:48]))
field = page.in_place_amount
field.setFocus()
_mock.spin(0.1)
print("typing:", page.jog.typing)
press_all(field, field.value)

print("\nA point's name is being edited (QLineEdit)")
session.add_memo_point(100.0, 100.0, None)
_mock.spin(0.2)
from micropick.gui.widgets.point_list import PointRow              # noqa: E402
row = page.points_list.findChildren(PointRow)[0]
row.edit("name")
_mock.spin(0.1)
edit = row.findChildren(QLineEdit)[0]
print("typing:", page.jog.typing)
press_all(edit, edit.text)
window.close()
