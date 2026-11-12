"""Number fields read a point as the decimal point whatever the computer's
regional format, and a comma too (`theme.factory.number_locale`).

On a Belgian format Qt took "0.5" for 5: the point read as a thousands
separator and dropped.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QLocale, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from micropick.gui.theme import factory  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def comma_locale(app):
    """The computer set to a format with a decimal comma, as Belgium's."""
    before = QLocale()
    QLocale.setDefault(QLocale("fr_BE"))
    assert QLocale().decimalPoint() == ","
    yield
    QLocale.setDefault(before)


def _fields():
    return [factory.number_field(None, 1.0, (0.0, 200.0), decimals=2),
            factory.double_spin_box()]


def _typed(box, text: str) -> float:
    box.setRange(0.0, 200.0)
    box.setDecimals(2)
    box.lineEdit().selectAll()
    QTest.keyClicks(box.lineEdit(), text)
    QTest.keyClick(box.lineEdit(), Qt.Key.Key_Return)
    return box.value()


@pytest.mark.parametrize("text, value", [
    ("0.5", 0.5), ("5.5", 5.5), ("66.4", 66.4), ("12.25", 12.25),
    ("0,5", 0.5), ("66,4", 66.4)])
def test_a_typed_number_is_read_as_meant(comma_locale, text, value):
    for box in _fields():
        assert _typed(box, text) == pytest.approx(value)


def test_a_number_is_shown_with_a_point(comma_locale):
    for box in _fields():
        box.setDecimals(2)
        box.setValue(1500.5 if box.maximum() > 1500 else 1.5)
        assert "," not in box.text()
        assert "." in box.text()


def test_an_integer_field_has_no_thousands_separator(comma_locale):
    box = factory.spin_box()
    box.setRange(0, 100000)
    box.setValue(12000)
    assert box.text() == "12000"
