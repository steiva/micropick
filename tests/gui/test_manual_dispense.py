"""Manual dispense (REDESIGN §8.10, #19): the session counts what is in the
tip, and a dispense gives back all of it or a part, where the tip is."""

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


@pytest.fixture
def session(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    s.load_profile("mock")
    s.probe_robot()
    s.new_run()
    yield s
    s.shutdown()


def _tip_on(session):
    rack = LabwareDefinition(load_name="opentrons_96_tiprack_300ul",
                             namespace="opentrons", version=1,
                             display_name="tips", ordering=[["A1", "B1"]],
                             data={}, source="shared")
    session.load_labware(rack, 10)
    session.pick_up_tip(10, "A1")


def _liquid_calls(session):
    return [c for c in session.robot.calls
            if c[0] in ("aspirate_in_place", "dispense_in_place")]


def test_aspirate_adds_to_what_is_in_the_tip(session):
    _tip_on(session)
    seen = []
    session.in_tip_changed.connect(seen.append)
    assert session.in_tip == 0.0
    session.aspirate(15.0, 50.0)
    session.aspirate(5.0, 50.0)
    assert session.in_tip == pytest.approx(20.0)
    assert seen == [15.0, 20.0]
    assert _liquid_calls(session) == [("aspirate_in_place", 15.0, 50.0),
                                      ("aspirate_in_place", 5.0, 50.0)]


def test_dispense_all_empties_the_tip(session):
    _tip_on(session)
    session.aspirate(15.0, 50.0)
    assert session.dispense(40.0) == pytest.approx(15.0)
    assert session.in_tip == 0.0
    assert _liquid_calls(session)[-1] == ("dispense_in_place", 15.0, 40.0)


def test_dispense_part_leaves_the_rest(session):
    _tip_on(session)
    session.aspirate(15.0, 50.0)
    assert session.dispense(50.0, 6.0) == pytest.approx(6.0)
    assert session.in_tip == pytest.approx(9.0)
    assert _liquid_calls(session)[-1] == ("dispense_in_place", 6.0, 50.0)


def test_a_part_larger_than_what_is_in_the_tip_is_refused(session):
    _tip_on(session)
    session.aspirate(5.0, 50.0)
    with pytest.raises(SessionError):
        session.dispense(50.0, 6.0)
    assert session.in_tip == pytest.approx(5.0)
    assert _liquid_calls(session)[-1][0] == "aspirate_in_place"


def test_nothing_in_the_tip_is_not_dispensed(session):
    _tip_on(session)
    with pytest.raises(SessionError):
        session.dispense(50.0)
    assert _liquid_calls(session) == []


def test_no_tip_on_is_refused(session):
    with pytest.raises(SessionError):
        session.aspirate(15.0, 50.0)
    assert _liquid_calls(session) == []


def test_another_tip_holds_nothing_and_the_same_tip_read_again_keeps_it(
        session):
    _tip_on(session)
    session.aspirate(15.0, 50.0)
    session.read_tip()                       # the same tip, read again
    assert session.in_tip == pytest.approx(15.0)
    session.drop_tip_in_trash()
    assert session.in_tip == 0.0
    session.pick_up_tip(10, "B1")
    assert session.in_tip == 0.0
