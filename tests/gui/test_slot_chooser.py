"""The mini deck slot chooser (#28): all 12 slots, nothing chosen, a click
says which slot."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt                             # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget              # noqa: E402

from micropick.gui.widgets.mini_deck import (DeckTile,           # noqa: E402
                                             TILE_GAP, MiniDeckDropdown,
                                             Tile)
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
    assert chooser.button(9).name.text() == "Calibration module"
    assert chooser.button(9).slot_label.text() == "SLOT 9"
    assert chooser.button(2).name.text() == "Empty"


def test_its_tiles_are_the_mini_decks(app):
    chooser, deck = SlotChooser(), MiniDeckDropdown()
    deck.open_popover()
    tile = deck._popover.findChildren(Tile)[0]
    assert isinstance(chooser.button(5), Tile)
    assert chooser.button(5).size() == tile.size()
    deck._popover.close()


def test_a_wider_panel_keeps_its_gaps(app):
    chooser = SlotChooser()
    chooser.resize(700, 400)
    chooser.show()
    app.processEvents()
    gap = chooser.button(5).x() - chooser.button(4).geometry().right() - 1
    assert gap == TILE_GAP
    chooser.close()


def test_it_fits_a_step_card_in_a_panel_of_420(app):
    assert SlotChooser().sizeHint().width() <= 420 - 2 * 16


def test_a_note_leaves_a_two_line_name_whole(app, themed):
    host = QWidget()
    tile = Tile(DeckTile(5, name="Corning 96 Well Plate 360 µL Flat",
                         kind="plate", note="Not measured"), False, host)
    host.show()
    app.processEvents()
    metrics = tile.name.fontMetrics()
    wanted = metrics.boundingRect(QRect(0, 0, tile.name.width(), 500),
                                  Qt.TextFlag.TextWordWrap,
                                  tile.name.text()).height()
    assert wanted > metrics.height()                       # it does wrap
    assert tile.name.height() >= wanted
    assert tile.note.geometry().bottom() < tile.height()
    host.hide()
    host.deleteLater()
