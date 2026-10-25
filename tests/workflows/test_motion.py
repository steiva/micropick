"""Moves across the deck: the travel height, the top of Z, the envelope."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from micropick.core.deck import slot_center
from micropick.hardware.mock import MockRobot
from micropick.hardware.protocols import xyz
from micropick.workflows.motion import (JOG_BELOW_TOP_MM, UNKNOWN_TIP_LENGTH_MM,
                                        Z_NOZZLE_TOP_MM, Motion, NoRobot)

TOP = 159.9                                  # the wide bore tip, retracted


def _tip(attached=True, length=40.7, pickup="p1"):
    return SimpleNamespace(attached=attached, length=length, pickup=pickup)


def _robot(z=50.0):
    return MockRobot(position=(100.0, 100.0, z), noise_mm=0.0, speed_mm_s=0,
                     top_mm=TOP)


def _retracts(robot):
    return [c for c in robot.calls if c == ("retract_axis", "leftZ")]


def _refused(robot):
    return [c for c in robot.calls if c[0] == "refused"]


def test_first_move_retracts_once_then_one_straight_move_under_the_top():
    robot = _robot()
    motion = Motion(robot)
    motion.camera_to((200.0, 150.0))
    assert len(_retracts(robot)) == 1
    assert motion.top == pytest.approx(TOP)
    assert xyz(robot) == pytest.approx((200.0, 150.0, TOP - 1.0))
    assert robot.log == [pytest.approx((200.0, 150.0, TOP - 1.0))]
    assert not _refused(robot)


def test_at_the_travel_height_the_next_move_does_not_retract():
    robot = _robot()
    motion = Motion(robot)
    motion.camera_to((200.0, 150.0))
    motion.camera_to((120.0, 80.0))
    assert len(_retracts(robot)) == 1


def test_below_the_travel_height_the_next_move_retracts_first():
    robot = _robot()
    motion = Motion(robot)
    motion.tip_to((200.0, 150.0), 30.0)
    assert xyz(robot)[2] == pytest.approx(30.0)
    motion.tip_to((120.0, 80.0), 40.0)
    assert len(_retracts(robot)) == 2
    assert xyz(robot) == pytest.approx((120.0, 80.0, 40.0))


def test_a_pose_taught_at_the_top_is_reached_under_it():
    robot = _robot()
    motion = Motion(robot)
    motion.tip_to((200.0, 150.0), TOP)
    assert xyz(robot) == pytest.approx((200.0, 150.0, TOP - 1.0))
    assert not _refused(robot)
    assert motion.reachable((1.0, 2.0, TOP))[2] == pytest.approx(TOP - 1.0)
    assert motion.reachable((1.0, 2.0, 20.0))[2] == pytest.approx(20.0)


def test_reachable_leaves_a_pose_alone_while_the_top_is_unknown():
    motion = Motion(_robot())
    assert motion.reachable((1.0, 2.0, TOP))[2] == pytest.approx(TOP)
    assert motion.travel_z is None


def test_another_tip_makes_the_next_move_measure_again():
    robot = _robot()
    motion = Motion(robot, tip=_tip())
    motion.camera_to((200.0, 150.0))
    motion.tip_changed(_tip())                    # the same tip, read again
    assert motion.top == pytest.approx(TOP)
    motion.tip_changed(_tip(pickup="p2"))
    assert motion.top is None
    motion.camera_to((120.0, 80.0))
    assert len(_retracts(robot)) == 2


def test_another_robot_has_no_top_yet():
    motion = Motion(_robot())
    motion.camera_to((200.0, 150.0))
    motion.set_robot(_robot())
    assert motion.top is None
    motion.set_robot(None)
    with pytest.raises(NoRobot):
        motion.camera_to((200.0, 150.0))


def test_a_well_move_raises_the_tip_first_and_leaves_the_path_to_the_robot():
    robot = _robot()
    motion = Motion(robot)
    motion.to_well("plate-id", "A1", "top", (0.0, 0.0, 2.0))
    assert robot.calls[-2:] == [("retract_axis", "leftZ"),
                                ("move_to_well", "plate-id", "A1", "top")]
    with pytest.raises(ValueError):
        motion.to_well("plate-id", "A1", "middle", (0.0, 0.0, 0.0))


@pytest.mark.parametrize("tip, length", [
    (_tip(attached=False, length=None), 0.0),
    (_tip(length=40.7), 40.7),
    (_tip(length=None), UNKNOWN_TIP_LENGTH_MM),
    (None, UNKNOWN_TIP_LENGTH_MM),
    (_tip(attached=None, length=None), UNKNOWN_TIP_LENGTH_MM),
])
def test_the_envelope_top_follows_the_tip(tip, length):
    motion = Motion(tip=tip)
    assert motion.envelope.z[1] == pytest.approx(
        Z_NOZZLE_TOP_MM - length - JOG_BELOW_TOP_MM)


def test_a_deck_module_raises_the_floor_over_its_slot_only():
    module = SimpleNamespace(slots=["3"], name="heater", height_mm=64.0)
    motion = Motion(modules=lambda: [module])
    x, y = slot_center("3")
    assert "heater in slot 3" in motion.refused((x, y, 50.0))
    assert motion.refused((x, y, 70.0)) == ""
    x, y = slot_center("1")
    assert motion.refused((x, y, 50.0)) == ""


def test_the_envelope_reads_the_modules_when_asked():
    modules = []
    motion = Motion(modules=lambda: modules)
    x, y = slot_center("3")
    assert motion.refused((x, y, 50.0)) == ""
    modules.append(SimpleNamespace(slots=["3"], name=None, height_mm=64.0))
    assert "module in slot 3" in motion.refused((x, y, 50.0))


def test_a_z_above_the_top_is_not_a_reason_but_x_y_and_the_floor_are():
    motion = Motion(tip=_tip())
    assert motion.refused((100.0, 100.0, 199.0)) == ""
    assert "x 400.0 is past" in motion.refused((400.0, 100.0, 50.0))
    assert "y -1.0 is below" in motion.refused((100.0, -1.0, 50.0))
    assert "z 0.0 is below" in motion.refused((100.0, 100.0, 0.0))
