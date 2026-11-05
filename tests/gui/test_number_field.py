"""The number field of Manual pick's Run (MANUAL.md §2.7): no arrows, the unit
in the label above it, ↑/↓ and the wheel while it has the keyboard."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt                   # noqa: E402
from PySide6.QtGui import QWheelEvent                            # noqa: E402
from PySide6.QtTest import QTest                                 # noqa: E402
from PySide6.QtWidgets import (QApplication, QStyle,             # noqa: E402
                               QStyleOptionSpinBox)

from micropick.gui.theme.factory import labeled, number_field    # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _wheel(field, up=True):
    event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0),
                        QPoint(0, 120 if up else -120),
                        Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(field, event)


def test_it_is_38_px_high_and_right_aligned(app):
    field = number_field(None, 10.0, (0.0, 100.0))
    assert field.height() == 38 and field.minimumHeight() == 38
    assert field.alignment() & Qt.AlignmentFlag.AlignRight
    assert field.font().pixelSize() == 14
    assert field.suffix() == ""


def test_the_keys_step_it(app):
    field = number_field(None, 10.0, (0.0, 100.0), step=1.0)
    field.show()
    field.setFocus()
    QTest.keyClick(field, Qt.Key.Key_Up)
    assert field.value() == 11.0
    QTest.keyClick(field, Qt.Key.Key_Down)
    assert field.value() == 10.0


def test_the_wheel_turns_it_only_with_the_keyboard(app):
    # Scrolled past, a Z must not change under the hand.
    field = number_field(None, 10.0, (0.0, 100.0), step=1.0)
    _wheel(field)
    assert field.value() == 10.0
    # Offscreen no window has the keyboard: said here.
    field.hasFocus = lambda: True
    _wheel(field)
    assert field.value() == 11.0


def test_the_number_has_the_room_and_the_arrows_none(themed):
    # NoButtons under the theme left the line edit 1 px wide, and no value.
    field = number_field(None, 1000.0, (0.0, 1000.0), decimals=1, width=72)
    field.show()
    QApplication.processEvents()
    assert field.height() == 38
    line = field.lineEdit()
    assert line.width() >= line.fontMetrics().horizontalAdvance("1000.0") + 12
    option = QStyleOptionSpinBox()
    field.initStyleOption(option)
    for arrow in (QStyle.SubControl.SC_SpinBoxUp,
                  QStyle.SubControl.SC_SpinBoxDown):
        assert field.style().subControlRect(
            QStyle.ComplexControl.CC_SpinBox, option, arrow, field).isEmpty()


def test_a_label_stands_over_its_field(app):
    field = number_field(None, 10.0, (0.0, 100.0))
    box = labeled("Amount, µl", field)
    box.show()
    assert box.label.text() == "Amount, µl"
    assert box.label.geometry().bottom() < field.geometry().top()
