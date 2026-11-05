"""Cards in one column or two, and a wide card across both."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

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


def _columns(app, width, left_text="left", right_text="right"):
    left, right = QPushButton(left_text), QPushButton(right_text)
    panel = CardColumns([left, right])
    panel.resize(width, 600)
    panel.show()
    app.processEvents()
    return panel, left, right


def test_a_long_text_does_not_widen_its_column(app):
    panel, left, right = _columns(app, 700, right_text="word " * 40)
    assert panel.two_columns
    assert left.width() == right.width() == 346       # (700 - SPACING) / 2
    assert right.x() == 354
    panel.close()


def test_a_text_changing_moves_no_column(app):
    panel, left, right = _columns(app, 700)
    right.setText("word " * 40)
    app.processEvents()
    assert left.width() == right.width() == 346
    left.setText("word " * 80)
    right.setText("a")
    app.processEvents()
    assert left.width() == right.width() == 346
    panel.close()


def test_the_columns_are_equal_on_an_odd_width(app):
    panel, left, right = _columns(app, 701)
    assert left.width() == right.width() == 346
    panel.close()


def test_one_column_keeps_a_long_text_inside_the_panel(app):
    panel, left, right = _columns(app, 460, left_text="word " * 40)
    assert not panel.two_columns
    assert left.width() == right.width() == 460
    panel.close()
