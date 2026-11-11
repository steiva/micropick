"""Test suite audit: the GUI tests as a comma-decimal computer runs them.

    PYTHONPATH=scripts/audit AUDIT_LOCALE=nl_BE python -m pytest -p locale_plugin tests/gui

Sets Qt's default locale before any widget is made, as the system's would
be on a Belgian or German Windows. A test that fails here and passes
without it either types a number with a dot or reads one back as text.
"""

import os


def pytest_configure(config):
    from PySide6.QtCore import QLocale
    QLocale.setDefault(QLocale(os.environ.get("AUDIT_LOCALE", "nl_BE")))
