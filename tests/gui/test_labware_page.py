"""The Deck step's page (REDESIGN §6.1, #40, #41): the deck's tiles, what
goes to an empty slot, what is in a filled one, × and Clear all, a move by
drag, and the banner about the robot's memory."""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.config.labware import resolve_definition          # noqa: E402
from micropick.config.schema import DeckModule                   # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages.labware import LabwarePage              # noqa: E402
from micropick.gui.session import Session                        # noqa: E402

PLATE = "corning_96_wellplate_360ul_flat"
PLATE_384 = "corning_384_wellplate_112ul_flat"
RACK = "opentrons_96_tiprack_300ul"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def session(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    s.load_profile("mock")
    s.probe_robot()
    s.new_run()
    yield s
    s.shutdown()


@pytest.fixture
def page(app, session):
    p = LabwarePage(session)
    yield p
    p.deleteLater()


def _wait(app, page):
    end = time.monotonic() + 10
    while page._busy() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def _listed(page):
    return [d.load_name for d in page.listed()]


def test_an_empty_slot_asks_what_goes_there_and_a_choice_loads_it(
        app, session, page):
    page.choose(7)
    assert page.title.text() == "What goes to slot 7?"
    assert not page.catalogue.isHidden() and page.held.isHidden()
    page.put(resolve_definition(PLATE))
    _wait(app, page)
    assert session.run_state.labware["7"].load_name == PLATE
    assert page.title.text() == "In slot 7"
    tile = page.deck.tile(7)
    assert (tile.name, tile.kind, tile.facts) == (
        "Corning 96 Well Plate 360 µL Flat", "plate",
        "96 wells · 360 µl · flat")
    # Frequent has it first now.
    page.choose(4)
    assert _listed(page)[0] == PLATE


def test_tabs_and_a_search_inside_the_tab_shown(page):
    page.choose(7)
    assert list(page.chip_buttons) == ["Frequent", "All", "Plates", "Tips"]
    assert page.chip == "Frequent"
    assert page.chip_buttons["Frequent"].isChecked()
    page.choose_chip("Tips")
    assert RACK in _listed(page) and PLATE not in _listed(page)
    page.choose_chip("Plates")
    assert PLATE in _listed(page) and RACK not in _listed(page)
    page.choose_chip("All")
    assert PLATE in _listed(page) and RACK in _listed(page)
    # Typed: only inside the tab shown.
    page.search.setText("384")
    assert PLATE_384 in _listed(page)
    page.choose_chip("Tips")
    assert PLATE_384 not in _listed(page)
    page.choose_chip("Plates")
    assert PLATE_384 in _listed(page)
    page.choose_chip("Frequent")
    assert PLATE_384 not in _listed(page)          # never loaded here


def test_remove_empties_and_nothing_puts_something_else(app, session, page):
    from PySide6.QtWidgets import QPushButton
    session.load_labware(resolve_definition(PLATE), 5)
    page.choose(5)
    assert page.title.text() == "In slot 5" and not page.held.isHidden()
    assert "Put something else" not in [
        b.text() for b in page.findChildren(QPushButton)]
    page.remove()
    _wait(app, page)
    assert "5" not in session.run_state.labware
    assert page.title.text() == "What goes to slot 5?"


def test_a_tiles_x_removes_its_labware(app, session, page):
    session.load_labware(resolve_definition(PLATE), 5)
    close = page.deck.tiles[5].close_button
    assert not close.isHidden()
    assert page.deck.tiles[4].close_button.isHidden()      # empty
    close.click()
    _wait(app, page)
    assert "5" not in session.run_state.labware


def test_clear_all_removes_everything(app, session, page):
    session.load_labware(resolve_definition(PLATE), 5)
    session.load_labware(resolve_definition(RACK), 10)
    assert page.clear_button.isEnabled()
    page.clear_button.click()
    _wait(app, page)
    assert session.run_state.labware == {}
    assert not page.clear_button.isEnabled()               # nothing left


def test_the_map_keeps_the_decks_proportions(app, page):
    deck = page.deck
    page.show()
    for size in ((1800, 700), (900, 1400)):
        page.resize(*size)
        app.processEvents()
        shown = deck.grid_widget.geometry()
        assert abs(shown.width() / shown.height() - 40 / 36.5) < 0.02
        assert deck.rect().contains(shown)
    page.hide()


def test_a_long_name_wraps_inside_its_card(app, page):
    from micropick.gui.widgets.mini_deck import DeckTile
    deck = page.deck
    deck.set_tiles([DeckTile(5, name="corning_384_wellplate_112ul_flat_"
                                      "with_a_lid_and_a_long_name",
                             kind="plate", facts="384 wells")])
    page.resize(1000, 700)
    page.show()
    app.processEvents()
    tile = deck.tiles[5]
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFontMetrics
    name = tile.name
    drawn = QFontMetrics(name.font()).boundingRect(
        0, 0, name.width(), 10_000, Qt.TextFlag.TextWordWrap, name.shown())
    assert name.width() <= tile.item.width()
    assert drawn.height() <= name.height() and drawn.width() <= name.width()
    assert tile.rect().contains(tile.item.geometry())
    assert name.shown().startswith("corning_")
    page.hide()


def test_the_drag_carries_the_name_card_only(session, page):
    session.load_labware(resolve_definition(PLATE), 5)
    tile = page.deck.tiles[5]
    assert tile.drag_pixmap().size() == tile.item.size()


def test_the_calibration_modules_slot_is_not_a_slot_to_choose(session, page):
    session.set_deck_modules([DeckModule(slots=[9], offset=[0, 0, 64.2],
                                         name="Calibration module")])
    page.choose(9)
    assert page.slot is None
    assert not page.deck.tile(9).valid
    assert page.deck.tile(9).platform == "Calibration module"


def test_a_tile_dragged_to_an_empty_slot_moves_it(app, session, page):
    session.load_labware(resolve_definition(PLATE), 5)
    page.deck.moved.emit(5, 6)
    _wait(app, page)
    held = session.run_state.labware
    assert "5" not in held and held["6"].load_name == PLATE
    assert page.slot == 6


def test_the_trash_is_not_a_slot_to_choose(page):
    page.choose(12)
    assert page.slot is None
    assert page.deck.tile(12).kind == "trash"


def test_a_platform_shows_on_its_tile(session, page):
    session.set_deck_modules([DeckModule(slots=[8], offset=[0, 0, 64.2],
                                         name="Picking platform")])
    assert page.deck.tile(8).platform == "Picking platform"
    assert page.deck.tile(5).platform == ""


def test_the_memory_banner_shows_until_got_it_in_this_robot_session(
        app, session, page):
    assert page.memory.isHidden()                  # nothing remembered
    session.load_labware(resolve_definition(PLATE), 5)
    assert not page.memory.isHidden()
    page._memory_seen_now()
    assert page.memory.isHidden()
    session.new_run()
    session.load_labware(resolve_definition(PLATE), 5)
    assert not page.memory.isHidden()


def test_a_calibration_module_placed_under_the_chosen_slot_unchooses_it(
        session, page):
    session.load_labware(resolve_definition(PLATE), 9)
    page.choose(9)
    assert not page.held.isHidden()
    session.set_deck_modules([DeckModule(slots=[9], offset=[0, 0, 64.2],
                                         name="Calibration module")])
    assert page.slot is None and page.deck.selected is None
    assert page.held.isHidden() and page.catalogue.isHidden()
    assert not page.deck.tiles[9].close_button.isHidden()  # still removable

