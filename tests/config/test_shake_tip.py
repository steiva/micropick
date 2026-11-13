"""The shake position follows the tip on now: it was saved with one tip's
end in the holder's hole, and another tip sits on the nozzle differently
(`Profile.shake_pose`, `Profile.set_pipette_offset`)."""

import pytest

from micropick.config import store
from micropick.config.schema import PipetteOffset

HOLE = (300.0, 200.0, 70.0)


def tip(dx, dy, pickup="p1", session="s1"):
    return PipetteOffset(dx=dx, dy=dy, tip_pickup=pickup,
                         robot_session=session)


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    return store.create_profile("bench")


def test_a_new_tip_moves_the_shake_position_by_its_difference(profile):
    profile.set_pipette_offset(tip(10.0, -5.0))
    profile.remember("shake", HOLE)
    assert profile.shake_pose() == HOLE

    profile.set_pipette_offset(tip(10.4, -5.3, pickup="p2"))
    x, y, z = profile.shake_pose()
    assert (x, y, z) == pytest.approx((300.4, 199.7, 70.0))
    assert profile.positions["shake"] == HOLE         # the saved pose stays


def test_it_is_kept_in_the_profile(profile):
    profile.set_pipette_offset(tip(10.0, -5.0))
    profile.remember("shake", HOLE)
    profile.set_pipette_offset(tip(11.0, -5.0, pickup="p2"))
    profile.save_calibration(backup=False)
    again = store.load_profile("bench")
    assert again.shake_pose() == pytest.approx((301.0, 200.0, 70.0))


def test_the_same_tip_measured_again_moves_nothing(profile):
    """A Redo or an adjustment by hand corrects the measure of the tip the
    hole was found with; that tip is still where it was."""
    profile.set_pipette_offset(tip(10.0, -5.0))
    profile.remember("shake", HOLE)
    profile.set_pipette_offset(tip(10.2, -5.1))
    assert profile.shake_pose() == HOLE
    profile.set_pipette_offset(tip(11.2, -5.1, pickup="p2"))
    assert profile.shake_pose() == pytest.approx((301.0, 200.0, 70.0))


def test_one_saved_before_takes_the_offset_it_replaces(profile):
    """A shake position from before the tip was recorded with it: the tip
    measured before the next calibration is the best guess."""
    profile.set_pipette_offset(tip(10.0, -5.0))
    profile.positions["shake"] = HOLE
    assert profile.shake_pose() == HOLE
    profile.set_pipette_offset(tip(9.5, -5.0, pickup="p2"))
    assert profile.shake_pose() == pytest.approx((299.5, 200.0, 70.0))


def test_forgotten_or_reset_it_keeps_no_tip(profile):
    profile.set_pipette_offset(tip(10.0, -5.0))
    profile.remember("shake", HOLE)
    profile.forget("shake")
    assert profile.calibration.shake_tip is None
    assert profile.shake_pose() is None

    profile.remember("shake", HOLE)
    profile.reset_setup(["shake"])
    assert profile.calibration.shake_tip is None
