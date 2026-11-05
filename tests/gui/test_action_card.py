"""Manual pick's Aspirate and Dispense card (#114): a FoldCard with its action
in the header, so that the button works folded and a summary says the rest."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt                                    # noqa: E402
from PySide6.QtTest import QTest                                 # noqa: E402
from PySide6.QtWidgets import (QApplication, QDoubleSpinBox,     # noqa: E402
                               QVBoxLayout, QWidget)

from micropick.gui.widgets.fold_card import ActionCard, FoldGroup  # noqa: E402

LONG = "Slot 2 · A3 · 10 µl · 50 µl/s · retract slowly after 5 s, " * 3


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def card(app):
    box = ActionCard("Aspirate", 1, "tips")
    box.resize(400, 200)
    box.show()
    app.processEvents()
    yield box
    box.close()
    box.deleteLater()


def test_the_button_works_with_the_card_folded(card):
    pressed = []
    card.button.clicked.connect(lambda: pressed.append(True))
    assert not card.is_open()
    assert card.button.isVisible()
    QTest.mouseClick(card.button, Qt.MouseButton.LeftButton)
    assert pressed == [True]
    assert not card.is_open()                  # a press of it folds nothing


def test_a_press_on_the_header_folds_and_on_a_gray_button_does_not(card):
    QTest.mouseClick(card, Qt.MouseButton.LeftButton,
                     pos=card.summary.mapTo(card,
                                            card.summary.rect().center()))
    assert card.is_open()
    card.button.setEnabled(False)
    # Qt hands a press on a disabled widget to its parent, the card.
    QTest.mouseClick(card, Qt.MouseButton.LeftButton,
                     pos=card.button.geometry().center())
    assert card.is_open()


def test_the_summary_is_cut_to_its_room_and_whole_in_the_tooltip(app, card):
    card.set_summary(LONG)
    app.processEvents()
    assert card.summary.text().endswith("…")
    assert card.summary.toolTip() == LONG
    assert card.summary.fontMetrics().horizontalAdvance(
        card.summary.text()) <= card.summary.width()
    assert card.width() == 400                 # a long one widens nothing


def test_the_summary_warns_until_it_does_not(card):
    card.set_summary("The tip is empty. Aspirate first.", warn=True)
    assert card.summary.property("warn") is True
    card.set_summary("By hand")
    assert card.summary.property("warn") is False


def test_an_open_card_says_so_for_its_border(card):
    assert card.property("tint") == "tips"
    assert card.property("open") is False
    card.set_open(True)
    assert card.property("open") is True


def test_a_field_folded_away_gives_the_keyboard_back(app):
    """Not to the next field the window finds, and the robot's keys with it:
    the operator never chose that one."""
    window = QWidget()
    layout = QVBoxLayout(window)
    first, second = ActionCard("Aspirate", 1, "tips", open=True), \
        ActionCard("Dispense", 2, "plate")
    typed, other = QDoubleSpinBox(), QDoubleSpinBox()
    first.body.layout().addWidget(typed)
    second.body.layout().addWidget(other)
    layout.addWidget(first)
    layout.addWidget(second)
    group = FoldGroup([first, second])
    window.show()
    window.activateWindow()
    typed.setFocus()
    app.processEvents()
    assert window.focusWidget() is typed
    group.open(second)
    app.processEvents()
    assert not first.is_open() and second.is_open()
    assert window.focusWidget() is None
    window.close()
    window.deleteLater()


def test_the_header_is_as_tall_as_the_design(themed, app):
    box = ActionCard("Aspirate", 1, "tips")
    box.resize(400, box.sizeHint().height())
    box.show()
    app.processEvents()
    assert box.height() == 64
    assert box.button.height() == 40
    box.close()
    box.deleteLater()
