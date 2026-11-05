"""Skipped plates (REDESIGN §6.7, §12.10, #21): "Skip this plate, use the
drawing" holds for the robot session it was said in, per slot and plate
type, in memory only; a measured plate is measured whatever was said."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                  # noqa: E402

from micropick.config.labware import resolve_definition     # noqa: E402
from micropick.config.schema import DeckModule              # noqa: E402
from micropick.gui.app import Options                       # noqa: E402
from micropick.gui.session import (MEASURED, NOT_MEASURED,  # noqa: E402
                                   SKIPPED, Session, SessionError)

PLATE = "corning_96_wellplate_360ul_flat"
OTHER = "nest_96_wellplate_200ul_flat"


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
    s.load_labware(resolve_definition(PLATE), 5)
    yield s
    s.shutdown()


def test_a_plate_is_not_measured_until_measured_or_skipped(session):
    assert session.well_center_status(5, PLATE) == NOT_MEASURED
    assert not session.plate_skipped(5, PLATE)


def test_skipping_a_plate_marks_it_skipped(session):
    seen = []
    session.confirmations_changed.connect(lambda: seen.append(True))
    session.skip_plate(5, PLATE)
    assert session.plate_skipped("5", PLATE)
    assert session.well_center_status(5, PLATE) == SKIPPED
    assert seen


def test_skipped_is_per_slot_and_plate_type(session):
    session.skip_plate(5, PLATE)
    assert not session.plate_skipped(6, PLATE)
    assert not session.plate_skipped(5, OTHER)


def test_skipped_is_remembered_for_the_robot_session_only(session):
    session.skip_plate(5, PLATE)
    session.new_run()
    assert not session.plate_skipped(5, PLATE)


def test_a_measured_plate_is_measured_even_if_skipped(session):
    session.skip_plate(5, PLATE)
    session.set_well_center(5, PLATE, (0.1, 0.2, -1.0), "B2", depth_mm=10.0)
    assert session.well_center_status(5, PLATE) == MEASURED
    session.forget_well_center(5, PLATE)
    assert session.well_center_status(5, PLATE) == SKIPPED


def test_a_run_reads_a_skipped_plate_as_the_status_says(session):
    session.skip_plate(5, PLATE)
    assert session.liquid_plates()["5"].center is None
    session.set_well_center(5, PLATE, (0.1, 0.2, -1.0), "B2", depth_mm=10.0)
    assert session.well_center_status(5, PLATE) == MEASURED
    assert session.liquid_plates()["5"].center == (0.1, 0.2, -1.0)


def test_a_plate_cannot_be_skipped_without_a_robot_session(app, tmp_path,
                                                           monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    s.load_profile("mock")
    try:
        with pytest.raises(SessionError):
            s.skip_plate(5, PLATE)
    finally:
        s.shutdown()


def _platform(height):
    return DeckModule(slots=[5], offset=[0, 0, height], name="Platform")


def test_a_saved_measurement_keeps_the_height_of_the_slots_module(session):
    session.set_deck_modules([_platform(64.2)])
    session.set_well_center(5, PLATE, (0.1, 0.2, -1.0), "B2", depth_mm=10.0)
    assert session.profile.deck.well_center(5, PLATE).module_height_mm == 64.2
    assert session.well_center_status(5, PLATE) == MEASURED


def test_a_plate_is_not_measured_once_the_slots_module_changes(session):
    session.set_well_center(5, PLATE, (0.1, 0.2, -1.0), "B2", depth_mm=10.0)
    session.set_deck_modules([_platform(64.2)])
    assert session.well_center_status(5, PLATE) == NOT_MEASURED


def test_liquids_plates_do_not_use_a_measurement_of_another_height(session):
    session.set_well_center(5, PLATE, (0.1, 0.2, -1.0), "B2", depth_mm=10.0)
    assert session.liquid_plates()["5"].center == (0.1, 0.2, -1.0)
    session.set_deck_modules([_platform(64.2)])
    plate = session.liquid_plates()["5"]
    assert plate.center is None and plate.depth is None


def test_use_it_confirms_a_plates_bottom_per_slot_and_plate_type(session):
    session.set_well_center(5, PLATE, (0.1, 0.2, -1.0), "B2", depth_mm=10.0)
    session.new_run()                       # a measurement of an earlier day
    assert not session.plate_bottom_confirmed(5, PLATE)
    session.use_plate_bottom(5, PLATE)
    assert session.plate_bottom_confirmed("5", PLATE)
    assert not session.plate_bottom_confirmed(5, OTHER)


def test_saving_a_measurement_confirms_the_plates_bottom(session):
    session.set_well_center(5, PLATE, (0.1, 0.2, -1.0), "B2", depth_mm=10.0)
    assert session.plate_bottom_confirmed(5, PLATE)


def test_a_new_robot_session_forgets_a_confirmed_bottom(session):
    session.set_well_center(5, PLATE, (0.1, 0.2, -1.0), "B2", depth_mm=10.0)
    session.new_run()
    assert not session.plate_bottom_confirmed(5, PLATE)
    assert session.well_center_status(5, PLATE) == MEASURED


def test_use_it_is_refused_with_no_measurement(session):
    with pytest.raises(SessionError):
        session.use_plate_bottom(5, PLATE)
    assert not session.plate_bottom_confirmed(5, PLATE)


def test_a_confirmed_bottom_does_not_outlive_the_slots_module(session):
    session.set_well_center(5, PLATE, (0.1, 0.2, -1.0), "B2", depth_mm=10.0)
    session.set_deck_modules([_platform(64.2)])
    assert not session.plate_bottom_confirmed(5, PLATE)
    with pytest.raises(SessionError):
        session.use_plate_bottom(5, PLATE)
