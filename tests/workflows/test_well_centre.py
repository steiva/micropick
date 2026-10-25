"""Measuring a plate's well center, and what a deposit adds from it."""
from __future__ import annotations

import pytest

from micropick.hardware.mock import MockRobot
from micropick.workflows import well_centre
from micropick.workflows.motion import Motion


def _motion(position=(120.0, 80.0, 40.0)):
    robot = MockRobot(position=position, noise_mm=0.0, speed_mm_s=0)
    return Motion(robot), robot


def test_the_well_top_is_where_the_robot_says_it_is():
    motion, robot = _motion()
    pose = well_centre.go_to_top(motion, "plate-id", "B2")
    assert robot.calls[-1] == ("move_to_well", "plate-id", "B2", "top")
    # Up first (the mock retracts to 100), then the robot's own well move,
    # which the mock answers without moving.
    assert pose == pytest.approx((120.0, 80.0, 100.0))


def test_the_offset_is_the_jog_from_the_robots_well_top():
    measurement = well_centre.Measurement()
    measurement.started("5", "corning_96", "B2", (100.0, 50.0, 20.0))
    assert measurement.offset((100.4, 49.7, 18.5)) == pytest.approx(
        [0.4, -0.3, -1.5])


def test_a_measurement_belongs_to_its_plate_only():
    measurement = well_centre.Measurement()
    assert measurement.for_plate("5", "corning_96") is None
    measurement.started("5", "corning_96", "B2", (100.0, 50.0, 20.0))
    assert measurement.for_plate("5", "corning_96").well == "B2"
    assert measurement.for_plate("2", "corning_96") is None
    assert measurement.for_plate("5", "nest_96") is None


def test_a_measurement_is_dropped_when_forgotten():
    measurement = well_centre.Measurement()
    measurement.started("5", "corning_96", "B2", (100.0, 50.0, 20.0))
    measurement.forget()
    assert measurement.for_plate("5", "corning_96") is None
    with pytest.raises(ValueError):
        measurement.offset((100.0, 50.0, 20.0))


def _centre(offset=(0.4, -0.3, -1.5), slot=5, load_name="corning_96"):
    from micropick.config.schema import WellCentre
    return WellCentre(slot=slot, load_name=load_name, offset=list(offset),
                      well="B2", depth_mm=10.9)


def test_a_deposit_adds_the_measured_centers_x_and_y_but_not_its_z():
    from types import SimpleNamespace
    cfg = SimpleNamespace(well_offset_x=1.0, well_offset_y=-2.0)
    assert well_centre.deposit_offset(cfg, _centre()) == pytest.approx(
        (1.4, -2.3))


def test_with_no_measured_center_a_deposit_is_the_well_offset_alone():
    from types import SimpleNamespace
    cfg = SimpleNamespace(well_offset_x=1.0, well_offset_y=-2.0)
    assert well_centre.deposit_offset(cfg, None) == pytest.approx((1.0, -2.0))


def _plan_parts(slot=5, held_name="corning_96"):
    from types import SimpleNamespace
    from micropick.config.schema import DeckConfig
    deck = DeckConfig(well_centres=[_centre(slot=5)])
    profile = SimpleNamespace(deck=deck)
    routine = SimpleNamespace(destination=SimpleNamespace(slot=slot))
    state = SimpleNamespace(labware={str(slot): SimpleNamespace(
        load_name=held_name)})
    return profile, routine, state


def test_the_plan_uses_the_center_of_the_plate_it_delivers_to():
    profile, routine, state = _plan_parts()
    assert well_centre.for_plan(profile, routine, state).well == "B2"


def test_another_plate_type_in_that_slot_has_no_center_of_its_own():
    profile, routine, state = _plan_parts(held_name="nest_96")
    assert well_centre.for_plan(profile, routine, state) is None


def test_no_plan_or_nothing_on_the_deck_has_no_center():
    profile, routine, state = _plan_parts()
    assert well_centre.for_plan(profile, None, state) is None
    assert well_centre.for_plan(profile, routine, None) is None
    profile, routine, state = _plan_parts(slot=2)
    assert well_centre.for_plan(profile, routine, state) is None


def test_the_depth_a_first_measurement_starts_from_is_the_definitions():
    from types import SimpleNamespace
    definition = SimpleNamespace(data={"wells": {"B2": {"depth": 10.67}}})
    assert well_centre.definition_depth(definition, "B2") == 10.67
    assert well_centre.definition_depth(definition, "Z9") is None
    assert well_centre.definition_depth(SimpleNamespace(data={}), "B2") is None
