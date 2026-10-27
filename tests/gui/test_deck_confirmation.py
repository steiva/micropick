"""Deck confirmation (REDESIGN §8.3, #13): Next on Deck confirms it for the
robot session, and any change to the deck - labware loaded, removed or
moved, platforms changed - or a new robot session clears it until the
next Next."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                  # noqa: E402

from micropick.config.labware import LabwareDefinition      # noqa: E402
from micropick.gui.app import Options                       # noqa: E402
from micropick.gui.session import Session, SessionError     # noqa: E402

PLATE = "corning_96_wellplate_360ul_flat"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def definition(load_name):
    return LabwareDefinition(load_name=load_name, namespace="opentrons",
                             version=1, display_name=load_name,
                             ordering=[["A1"]], data={}, source="shared")


@pytest.fixture
def session(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    s.load_profile("mock")
    s.probe_robot()
    s.new_run()
    yield s
    s.shutdown()


def test_loading_labware_clears_the_deck_confirmation(session):
    session.confirm("deck")
    session.load_labware(definition(PLATE), 5)
    assert not session.is_confirmed("deck")


def test_removing_labware_clears_it_and_an_empty_slot_does_not(session):
    session.load_labware(definition(PLATE), 5)
    session.confirm("deck")
    session.unload_labware(4)                     # nothing there
    assert session.is_confirmed("deck")
    session.unload_labware(5)
    assert not session.is_confirmed("deck")


def test_moving_labware_to_an_empty_slot_clears_it(session):
    session.load_labware(definition(PLATE), 5)
    session.confirm("deck")
    session.move_labware(5, 6)
    held = session.run_state.labware
    assert "5" not in held and held["6"].load_name == PLATE
    assert not session.is_confirmed("deck")


def test_moving_onto_labware_is_refused_and_changes_nothing(session):
    session.load_labware(definition(PLATE), 5)
    session.load_labware(definition("nest_12_reservoir_15ml"), 6)
    session.confirm("deck")
    with pytest.raises(SessionError, match="Slot 6 is not empty"):
        session.move_labware(5, 6)
    held = session.run_state.labware
    assert held["5"].load_name == PLATE
    assert held["6"].load_name == "nest_12_reservoir_15ml"
    assert session.is_confirmed("deck")


def test_changing_the_platforms_clears_it(session):
    from micropick.config.schema import DeckModule
    session.confirm("deck")
    session.set_deck_modules([DeckModule(slots=[5], offset=[0, 0, 64.2],
                                         name="Picking platform")])
    assert not session.is_confirmed("deck")


def test_a_new_robot_session_clears_it(session):
    session.confirm("deck")
    session.new_run()
    assert not session.is_confirmed("deck")


def test_a_tip_picked_up_is_not_a_change_to_the_deck(session):
    rack = LabwareDefinition(load_name="opentrons_96_tiprack_300ul",
                             namespace="opentrons", version=1,
                             display_name="tips", ordering=[["A1"]], data={},
                             source="shared")
    session.load_labware(rack, 10)
    session.confirm("deck")
    session.pick_up_tip(10, "A1")
    assert session.is_confirmed("deck")


def test_a_rack_whose_tip_is_on_is_not_moved(session):
    rack = LabwareDefinition(load_name="opentrons_96_tiprack_300ul",
                             namespace="opentrons", version=1,
                             display_name="tips", ordering=[["A1"]], data={},
                             source="shared")
    session.load_labware(rack, 10)
    session.pick_up_tip(10, "A1")
    with pytest.raises(SessionError, match="tip"):
        session.move_labware(10, 11)
    assert "10" in session.run_state.labware
