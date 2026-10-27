"""The Deck step's page (REDESIGN §6.1): the deck's tiles, what goes to an
empty slot, what is in a filled one, a move by drag, and the banner about
the robot's memory."""

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


def test_chips_and_search(page):
    page.choose(7)
    page.choose_chip("Tips")
    assert RACK in _listed(page) and PLATE not in _listed(page)
    page.choose_chip("Plates")
    assert PLATE in _listed(page) and RACK not in _listed(page)
    # Typed: the whole catalogue, whatever the chip.
    page.choose_chip("Tips")
    page.search.setText("384")
    assert PLATE_384 in _listed(page) and RACK not in _listed(page)


def test_put_something_else_replaces_and_remove_empties(app, session, page):
    session.load_labware(resolve_definition(PLATE), 5)
    page.choose(5)
    assert page.title.text() == "In slot 5" and not page.held.isHidden()
    page.put_something_else()
    assert page.title.text() == "What goes to slot 5?"
    page.put(resolve_definition(PLATE_384))
    _wait(app, page)
    assert session.run_state.labware["5"].load_name == PLATE_384
    assert page.title.text() == "In slot 5"
    page.remove()
    _wait(app, page)
    assert "5" not in session.run_state.labware
    assert page.title.text() == "What goes to slot 5?"


def test_a_tile_dragged_to_an_empty_slot_moves_it(app, session, page):
    session.load_labware(resolve_definition(PLATE), 5)
    session.confirm("deck")
    page.deck.moved.emit(5, 6)
    _wait(app, page)
    held = session.run_state.labware
    assert "5" not in held and held["6"].load_name == PLATE
    assert page.slot == 6
    assert not session.is_confirmed("deck")


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
