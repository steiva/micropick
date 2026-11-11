"""GUI-02: numbers typed on a computer with a decimal comma (GUI audit, E).

    conda run -n lab python -m pytest -q scripts/audit/gui/test_comma_locale.py

Qt's default locale is set to Dutch (Belgium), as a Windows laptop set up in
Belgium has it, and a number is typed with a dot into each kind of number
field the program has. Every test here FAILS today: that is the finding
(the test audit's G-01, here per field and with what the robot would be
given). They are the tests for the fix: with the program's locale fixed
(`QLocale.setDefault(QLocale.c())` before the first widget) they pass, and
`test_a_comma_is_not_read_as_nothing` says what to decide about a comma.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QLocale, Qt                             # noqa: E402
from PySide6.QtTest import QTest                                   # noqa: E402
from PySide6.QtWidgets import QApplication                         # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def belgian(app):
    before = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.Dutch, QLocale.Country.Belgium))
    yield
    QLocale.setDefault(before)


def _type(box, text):
    box.show()
    box.setFocus()
    box.selectAll()
    QTest.keyClicks(box, text)
    QTest.keyClick(box, Qt.Key.Key_Return)
    return box.value()


def test_a_number_field_reads_a_dot_as_the_decimal_sign(belgian):
    """Manual pick's Amount, Speed, Z, Above the bottom (`number_field`)."""
    from micropick.gui.theme.factory import number_field
    field = number_field(None, 1.0, (0.0, 300.0))
    assert _type(field, "1.5") == 1.5


def test_a_signed_offset_reads_a_dot_as_the_decimal_sign(belgian):
    """Dispense's well height Offset: -0.5 mm must not become -5 mm."""
    from micropick.gui.theme.factory import number_field
    field = number_field(None, 0.0, (-20.0, 20.0))
    assert _type(field, "-0.5") == -0.5


def test_a_picking_setting_reads_a_dot_as_the_decimal_sign(belgian):
    """More settings' numbers (`settings_form._number`): pickup offset."""
    from micropick.gui.widgets.settings_form import _number
    box = _number(float, 0.3, (0.0, 5.0))
    assert _type(box, "0.5") == 0.5


def test_a_points_z_reads_a_dot_as_the_decimal_sign(belgian, tmp_path,
                                                    monkeypatch):
    """Points: a double click on Z, a number typed, Enter."""
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    from PySide6.QtWidgets import QDoubleSpinBox

    from micropick.gui.app import Options
    from micropick.gui.session import Session
    from micropick.gui.widgets.point_list import PointList, PointRow
    session = Session(Options(mock=True))
    session.load_profile("mock")
    session.add_memo_point(100.0, 100.0, 50.0)
    points = PointList(session)
    points.show()
    QApplication.processEvents()
    row = points.findChildren(PointRow)[0]
    row.edit("z")
    box = row.findChildren(QDoubleSpinBox)[0]
    box.selectAll()
    QTest.keyClicks(box, "47.5")
    QTest.keyClick(box, Qt.Key.Key_Return)
    QApplication.processEvents()
    try:
        assert session.memo_points[0].z == 47.5
    finally:
        session.shutdown()


def test_a_comma_is_not_read_as_nothing(belgian):
    """What a Belgian keyboard's numeric pad types. To decide with the fix:
    either a comma is the decimal sign too, or it is refused where it is
    typed; it must not be dropped (1,5 read as 15)."""
    from micropick.gui.theme.factory import number_field
    field = number_field(None, 1.0, (0.0, 300.0))
    assert _type(field, "1,5") in (1.5, 1.0)
