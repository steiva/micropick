"""Cards in one column or two, and a wide card across both."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel               # noqa: E402

from micropick.gui.widgets.card_columns import CardColumns       # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _panel(app, width):
    cards = [QLabel(f"card {i}") for i in range(4)]
    wide = QLabel("map")
    panel = CardColumns(cards, wide=[wide])
    panel.resize(width, 600)
    panel.show()
    app.processEvents()
    return panel, cards, wide


def test_one_column_is_as_wide_as_the_panel(app):
    panel, cards, wide = _panel(app, 460)
    assert not panel.two_columns
    assert {c.width() for c in cards} == {wide.width()} == {460}
    panel.close()


def test_a_wide_card_spans_both_columns(app):
    panel, cards, wide = _panel(app, 900)
    assert panel.two_columns
    assert wide.width() == 900
    assert all(c.width() < 460 for c in cards)
    assert wide.y() < min(c.y() for c in cards)       # above them
    panel.close()
