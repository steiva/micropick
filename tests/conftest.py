"""The mock robot moves at once in the tests.

`MockRobot` sleeps a move's length at 200 mm/s, so the GUI's mock looks
busy as the robot does; in the tests that was most of their time, every
travel across the deck a second or more of waiting. A test that needs a
move to take time (a stop that lands mid-batch) passes `speed_mm_s`.
"""

import pytest

from micropick.hardware.mock import MockRobot


@pytest.fixture(autouse=True)
def _instant_mock_robot(monkeypatch):
    monkeypatch.setattr(MockRobot, "SPEED_MM_S", 0.0)
