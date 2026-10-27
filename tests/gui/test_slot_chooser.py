"""The mini deck slot chooser (#28): all 12 slots, nothing chosen, a click
says which slot."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.gui.widgets.mini_deck import DeckTile             # noqa: E402
from micropick.gui.widgets.slot_chooser import SlotChooser       # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_every_slot_is_offered_and_none_is_marked(app):
    chooser = SlotChooser()
    assert chooser.slots == list(range(1, 13))
    assert all(chooser.button(s).isEnabled() for s in chooser.slots)
    assert chooser.marked is None


def test_a_click_on_a_slot_says_which(app):
    chooser = SlotChooser()
    clicked = []
    chooser.slot_clicked.connect(clicked.append)
    chooser.button(8).click()
    chooser.button(3).click()
    assert clicked == [8, 3]


def test_a_disabled_chooser_says_nothing(app):
    chooser = SlotChooser()
    clicked = []
    chooser.slot_clicked.connect(clicked.append)
    chooser.setEnabled(False)
    chooser.button(5).click()
    assert clicked == []


def test_the_marked_slot_is_shown_and_can_be_cleared(app):
    chooser = SlotChooser()
    chooser.set_marked(9)
    assert chooser.marked == 9
    assert chooser.button(9).isChecked()
    assert not chooser.button(5).isChecked()
    chooser.set_marked(None)
    assert chooser.marked is None
    assert not chooser.button(9).isChecked()


def test_a_click_does_not_mark_by_itself(app):
    """The caller marks a slot once its move went; a refused one is not."""
    chooser = SlotChooser()
    chooser.button(4).click()
    assert chooser.marked is None and not chooser.button(4).isChecked()


def test_tiles_name_what_stands_in_a_slot(app):
    chooser = SlotChooser()
    chooser.set_tiles([DeckTile(9, platform="Calibration module")])
    assert "Calibration module" in chooser.button(9).text()
    assert chooser.button(9).text().startswith("9")
    assert chooser.button(2).text() == "2"
