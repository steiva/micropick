"""A disabled button is the normal button at 0.4 over the card (TIP.md §2.4).

The expected colors are worked by hand, 0.4 * color + 0.6 * card (#171A1F).
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint                                # noqa: E402
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QPushButton

from micropick.gui.theme import COLORS, stylesheet
from micropick.gui.theme.factory import primary_button
from micropick.gui.theme.tokens import faded


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("color, expected", [
    ("#6CA6FF", "#395279"),       # accent
    ("#0B1220", "#12171F"),       # on-accent
    ("#2E333B", "#20242A"),       # control-border
    ("#E8EAED", "#6B6D71"),       # text
])
def test_a_faded_color_is_the_color_at_0_4_over_the_card(color, expected):
    assert faded(color) == expected


def _painted(themed, make, *, enabled: bool):
    button: QPushButton = make("", None)
    button.resize(120, 34)
    button.setEnabled(enabled)
    image = button.grab().toImage()
    button.deleteLater()
    return image


def test_a_disabled_primary_is_the_primary_faded(themed):
    image = _painted(themed, primary_button, enabled=False)
    assert image.pixelColor(QPoint(60, 17)) == QColor("#395279")


def test_an_enabled_primary_does_not_change(themed):
    image = _painted(themed, primary_button, enabled=True)
    assert image.pixelColor(QPoint(60, 17)) == QColor(COLORS["accent"])


def test_a_disabled_secondary_keeps_its_border_faded_and_no_fill(themed):
    sheet = stylesheet()
    rule = sheet[sheet.index("QPushButton#secondary:disabled"):]
    rule = rule[:rule.index("}")]
    assert "#20242A" in rule and "#6B6D71" in rule
    assert "background: transparent" in rule


@pytest.mark.parametrize("name", ["primary", "secondary", "dangerText", "quiet",
                                  "danger", "tile", "pathLink"])
def test_no_disabled_button_is_drawn_in_the_old_grays(name):
    sheet = stylesheet()
    rule = sheet[sheet.index(f"QPushButton#{name}:disabled"):]
    rule = rule[:rule.index("}")]
    assert COLORS["faint"] not in rule and COLORS["selected-bg"] not in rule
