"""Test suite audit: what a number field does on a comma-decimal computer.

    QT_QPA_PLATFORM=offscreen conda run -n lab python scripts/audit/comma_locale.py

The program sets no QLocale, so a QDoubleSpinBox (every `number_field`)
follows the system's. This types the same keys into a number field under
the C locale and under Dutch (Belgium), where the decimal sign is a comma,
and prints what the field shows and the value the program would read.
"""

from PySide6.QtCore import QLocale
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from micropick.gui.theme.factory import number_field  # noqa: E402


def typed(locale: QLocale, keys: str):
    QLocale.setDefault(locale)
    field = number_field(None, 10.0, (0.0, 300.0))
    field.show()
    field.setFocus()
    field.selectAll()
    QTest.keyClicks(field, keys)
    field.interpretText()
    shown, value = field.text(), field.value()
    field.deleteLater()
    return shown, value


for name, locale in (("C", QLocale.c()),
                     ("nl_BE", QLocale(QLocale.Language.Dutch,
                                       QLocale.Country.Belgium)),
                     ("de_DE", QLocale(QLocale.Language.German,
                                       QLocale.Country.Germany))):
    for keys in ("1.5", "1,5", "12.5"):
        shown, value = typed(locale, keys)
        print(f"{name:6} typed {keys!r:7} shows {shown!r:9} value {value}")
