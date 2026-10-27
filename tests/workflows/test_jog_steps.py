"""The jog step sizes: one list, walked one size at a time."""

from micropick.hardware.mock import MockRobot
from micropick.workflows.jog import DEFAULT_STEPS, JogController

TEN = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10]


def test_the_steps_are_the_ten_sizes_of_the_pad():
    assert list(DEFAULT_STEPS) == TEN


def test_step_up_and_down_walk_the_sizes_and_stop_at_the_ends():
    jog = JogController(MockRobot(), step=1.0)
    assert [jog.step_up() for _ in range(4)] == [2, 5, 10, 10]
    jog.step = 0.02
    assert [jog.step_down() for _ in range(2)] == [0.01, 0.01]
