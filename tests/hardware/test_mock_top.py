"""The mock robot keeping to the top of Z, as the robot does, when asked to."""
from __future__ import annotations

import pytest

from micropick.hardware.mock import MockRobot
from micropick.hardware.protocols import MoveFailed, move_to, xyz


def test_off_by_default_a_retract_goes_to_100_and_any_z_is_taken():
    robot = MockRobot(noise_mm=0.0, speed_mm_s=0)
    robot.retract_axis("leftZ")
    assert xyz(robot)[2] == 100.0
    move_to(robot, (100.0, 100.0, 180.0))
    assert xyz(robot)[2] == pytest.approx(180.0)


def test_a_retract_goes_to_the_top():
    robot = MockRobot(noise_mm=0.0, speed_mm_s=0, top_mm=159.9)
    robot.retract_axis("leftZ")
    assert xyz(robot)[2] == pytest.approx(159.9)


def test_a_move_to_the_top_is_declined_and_the_pose_kept():
    robot = MockRobot(noise_mm=0.0, speed_mm_s=0, top_mm=159.9)
    robot.retract_axis("leftZ")
    with pytest.raises(MoveFailed, match="out of bounds"):
        move_to(robot, (100.0, 100.0, 159.9), force_direct=True)
    assert xyz(robot) == pytest.approx((150.0, 150.0, 159.9))
    move_to(robot, (100.0, 100.0, 158.9), force_direct=True)
    assert xyz(robot) == pytest.approx((100.0, 100.0, 158.9))


def test_a_step_sideways_at_the_top_does_nothing():
    robot = MockRobot(noise_mm=0.0, speed_mm_s=0, top_mm=159.9)
    robot.retract_axis("leftZ")
    robot.move_relative("x", 5.0, verbose=False)
    assert xyz(robot)[0] == pytest.approx(150.0)
    robot.move_relative("z", -2.0, verbose=False)
    assert xyz(robot)[2] == pytest.approx(157.9)
