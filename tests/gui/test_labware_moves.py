"""Labware loaded, moved and refused on the deck (#13, #40): a move into
an empty slot loads it again there, and the calibration module's slot,
like the trash, takes nothing."""

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


def test_moving_labware_to_an_empty_slot_loads_it_there(session):
    session.load_labware(definition(PLATE), 5)
    session.move_labware(5, 6)
    held = session.run_state.labware
    assert "5" not in held and held["6"].load_name == PLATE


def test_moving_onto_labware_is_refused_and_changes_nothing(session):
    session.load_labware(definition(PLATE), 5)
    session.load_labware(definition("nest_12_reservoir_15ml"), 6)
    with pytest.raises(SessionError, match="Slot 6 is not empty"):
        session.move_labware(5, 6)
    held = session.run_state.labware
    assert held["5"].load_name == PLATE
    assert held["6"].load_name == "nest_12_reservoir_15ml"


def test_the_calibration_modules_slot_takes_no_labware(session):
    from micropick.config.schema import DeckModule
    session.set_deck_modules([
        DeckModule(slots=[9], offset=[0, 0, 64.2], name="Calibration module"),
        DeckModule(slots=[5], offset=[0, 0, 64.2], name="Picking platform")])
    with pytest.raises(SessionError, match="calibration module"):
        session.load_labware(definition(PLATE), 9)
    session.load_labware(definition(PLATE), 5)        # a platform does
    with pytest.raises(SessionError, match="calibration module"):
        session.move_labware(5, 9)
    held = session.run_state.labware
    assert "9" not in held and held["5"].load_name == PLATE
    assert session.refuses_labware(9) and not session.refuses_labware(5)


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
