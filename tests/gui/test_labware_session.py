"""Loading and unloading labware through the session, on the mock robot.

What the run reports afterwards is the only thing checked, because it is the
only thing the robot will act on. A QApplication is needed for the session's
signals; offscreen, so this runs with no display.
"""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                  # noqa: E402

from micropick.config.labware import LabwareDefinition      # noqa: E402
from micropick.gui.app import Options                       # noqa: E402
from micropick.gui.session import Session, SessionError     # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def definition(load_name, namespace="opentrons", source="shared"):
    return LabwareDefinition(load_name=load_name, namespace=namespace,
                             version=1, display_name=load_name,
                             ordering=[["A1"]], data={}, source=source)


@pytest.fixture
def session(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    s.probe_robot()
    s.new_run()
    yield s
    s.shutdown()


def test_load_reports_back_from_the_run(session):
    seen = []
    session.labware_changed.connect(lambda state: seen.append(state))
    entry = session.load_labware(definition("opentrons_96_tiprack_300ul"), 10)
    assert entry.slot == "10" and entry.load_name == "opentrons_96_tiprack_300ul"
    assert session.run_state.labware["10"].labware_id == entry.labware_id
    assert seen and "10" in seen[-1].labware


def test_replace_empties_the_slot_first(session):
    first = session.load_labware(definition("corning_96_wellplate_360ul_flat"), 5)
    second = session.load_labware(definition("plate_b", "custom_beta", "local"), 5)
    assert second.labware_id != first.labware_id
    assert session.run_state.labware["5"].load_name == "plate_b"
    calls = [c for c in session.robot.calls if c[0] == "move_labware"]
    assert calls == [("move_labware", first.labware_id, "offDeck")]


def test_unload_moves_off_deck_and_tolerates_an_empty_slot(session):
    entry = session.load_labware(definition("nest_12_reservoir_15ml"), 1)
    session.unload_labware(1)
    assert "1" not in session.run_state.labware
    assert ("move_labware", entry.labware_id, "offDeck") in session.robot.calls
    before = len(session.robot.calls)
    session.unload_labware(1)                     # nothing there: no command
    assert len(session.robot.calls) == before


def test_refuses_without_a_run_and_outside_the_deck(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    with pytest.raises(SessionError):
        s.load_labware(definition("x"), 1)
    s.probe_robot()
    s.new_run()
    with pytest.raises(SessionError):
        s.load_labware(definition("x"), 12)           # the fixed trash
    s.shutdown()




# -- tips --------------------------------------------------------------------
#
# The tip is what the robot reports. On the mock that is its `tip` attribute,
# which is also what a "notebook" pick-up sets, so the session's answer can be
# checked against a pick-up it never saw.

def rack_in(session, slot=10):
    return session.load_labware(definition("opentrons_96_tiprack_300ul"), slot)


def test_connect_asks_the_robot_for_the_tip(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    assert s.tip.attached is None                   # no run: not known
    s.probe_robot()
    s._api.load_labware("opentrons_96_tiprack_300ul", 3)
    s._api.pick_up_tip(s._api.labware_dct["3"], "B2")   # before this session
    s.new_run()
    assert s.tip.attached is True and (s.tip.slot, s.tip.well) == ("3", "B2")
    s.shutdown()
    assert s.tip.attached is None


def test_pick_up_then_return(session):
    rack = rack_in(session)
    notes = []
    session.tip_changed.connect(lambda tip: notes.append(tip.attached))
    session.pick_up_tip(10, "A1")
    assert session.tip.attached and session.tip.returnable
    assert (session.tip.slot, session.tip.well) == ("10", "A1")
    session.return_tip()
    assert session.tip.attached is False
    assert notes == [True, False]
    assert session.robot.calls[-2:] == [("pick_up_tip", rack.labware_id, "A1"),
                                        ("drop_tip", rack.labware_id, "A1")]


def test_no_pick_up_over_a_tip(session):
    rack = rack_in(session)
    session.pick_up_tip(10, "A1")
    with pytest.raises(SessionError, match="reports a tip"):
        session.pick_up_tip(10, "B1")
    picks = [c for c in session.robot.calls if c[0] == "pick_up_tip"]
    assert picks == [("pick_up_tip", rack.labware_id, "A1")]


def test_no_pick_up_over_an_unknown_tip(session, monkeypatch):
    rack_in(session)
    from micropick.gui.session import Tip
    monkeypatch.setattr(session, "read_tip", lambda: Tip.unknown())
    session.tip = Tip.unknown()
    with pytest.raises(SessionError, match="could not be read"):
        session.pick_up_tip(10, "A1")
    assert not [c for c in session.robot.calls if c[0] == "pick_up_tip"]


def test_trash_is_an_area_not_a_slot(session):
    rack_in(session)
    session.pick_up_tip(10, "A1")
    session.drop_tip_in_trash()
    assert session.tip.attached is False
    assert session.robot.calls[-2:] == [("move_to_trash",), ("drop_tip_in_place",)]
    assert "12" not in session.run_state.labware


def test_drop_in_place_rereads(session):
    rack_in(session)
    session.pick_up_tip(10, "H12")
    session.drop_tip_in_place()
    assert session.tip.attached is False
    assert session.robot.calls[-1] == ("drop_tip_in_place",)


def test_a_tip_from_elsewhere_is_seen_on_reread(session):
    rack = rack_in(session)
    session.robot.pick_up_tip(rack.labware_id, "D3")      # a notebook did this
    assert session.tip.attached is False                  # not yet asked
    session.read_tip()
    assert (session.tip.attached, session.tip.slot, session.tip.well) == (True, "10", "D3")
    session.unload_labware(10)                            # rack gone off deck
    session.read_tip()
    assert session.tip.attached is True and not session.tip.returnable


def test_refusals_come_as_sentences(session):
    session.load_labware(definition("corning_96_wellplate_360ul_flat"), 5)
    with pytest.raises(SessionError, match="not a tip rack"):
        session.pick_up_tip(5, "A1")
    with pytest.raises(SessionError, match="nothing in slot 7"):
        session.pick_up_tip(7, "A1")
    rack_in(session)
    with pytest.raises(SessionError, match="no well"):
        session.pick_up_tip(10, "Z9")
    with pytest.raises(SessionError, match="no rack to return"):
        session.return_tip()
    assert session.tip.attached is False


# -- lights ------------------------------------------------------------------

def test_lights_are_read_on_connect_and_set_explicitly(session):
    assert session.lights is True
    seen = []
    session.lights_changed.connect(lambda on: seen.append(on))
    session.set_lights(False)
    assert session.lights is False and session.robot.lights is False
    session.set_lights(False)                       # already off: no toggle
    session.toggle_lights()
    assert session.lights is True
    assert session.robot.calls.count(("toggle_lights",)) == 2
    assert seen == [False, False, False, True]
