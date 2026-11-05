"""How the tip leaves after Aspirate or Dispense (#102), on its own."""

import threading

import pytest

from micropick.hardware.mock import MockRobot
from micropick.hardware.protocols import xyz
from micropick.workflows.manual import Retract, retract
from micropick.workflows.motion import Motion

TOP = 159.9


@pytest.fixture
def motion():
    m = Motion(MockRobot(position=(100.0, 100.0, 50.0), noise_mm=0.0,
                         speed_mm_s=0, top_mm=TOP))
    m.tip_to((100.0, 100.0), 30.0)
    return m


def _z(motion):
    return xyz(motion.robot)[2]


def test_it_rises_at_once_by_default(motion):
    assert retract(motion, Retract(), threading.Event()) is True
    assert _z(motion) == pytest.approx(TOP - 1.0)


def test_it_stays_down_when_it_does_not_retract(motion):
    assert retract(motion, Retract(rises=False), threading.Event()) is False
    assert _z(motion) == pytest.approx(30.0)


def test_it_rises_after_the_delay(motion):
    stopped = threading.Event()
    waited = []
    stopped.wait = lambda seconds: waited.append(seconds) or False
    assert retract(motion, Retract(delay_s=5.0), stopped) is True
    assert waited == [5.0]
    assert _z(motion) == pytest.approx(TOP - 1.0)


def test_a_stop_during_the_delay_leaves_the_tip_down(motion):
    stopped = threading.Event()
    stopped.set()
    assert retract(motion, Retract(delay_s=60.0), stopped) is False
    assert _z(motion) == pytest.approx(30.0)


def test_slowly_is_passed_on(motion):
    seen = []
    motion.rise = lambda **kwargs: seen.append(kwargs["slowly"])
    retract(motion, Retract(slowly=True), threading.Event())
    assert seen == [True]
