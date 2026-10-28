"""The Dish bottom step (REDESIGN §6.4, #16): measured with the tip, or
the last value taken with Use it - either holds for the robot session it
was done in. The page: no camera, a big Z readout with Up and Down (and
their keys, #49) that work only while lowering, Last time with Use it,
two steps - the second ending in Save dish bottom - and Redo."""

import os
import time
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt                                    # noqa: E402
from PySide6.QtTest import QTest                                 # noqa: E402
from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.config.schema import PipetteOffset                # noqa: E402
from micropick.config.schema import PixelMap as PixelMapConfig   # noqa: E402
from micropick.gui import keys                                   # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages import dish_bottom as page_module       # noqa: E402
from micropick.gui.pages.dish_bottom import DishBottomPage, ago  # noqa: E402
from micropick.gui.readiness import DISH_POSITION                # noqa: E402
from micropick.gui.session import Session, SessionError          # noqa: E402
from micropick.hardware.protocols import xyz                     # noqa: E402

W, H, K = 2592, 1944, 0.026


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


@pytest.fixture
def page(app, session):
    p = DishBottomPage(session)
    p.show()
    # Shown, it reads the pose; Move the tip stays gray until that is done.
    assert _until(app, lambda: not p.jog.busy)
    yield p
    p.hide()
    p.deleteLater()


def _until(app, condition, timeout_s=10.0):
    end = time.monotonic() + timeout_s
    while not condition() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    return condition()


def _measured(session, z=66.4, days=3):
    """A dish bottom measured `days` ago, in an earlier robot session."""
    session.set_dish_bottom(z)
    session.profile.calibration.dish_bottom_set_at = (
        datetime.now(timezone.utc) - timedelta(days=days))
    session.new_run()


def _calibrated(session):
    """A linear toy map, a pipette offset and the position over the dish:
    what Tip over the dish center needs."""
    s = W / 2
    profile = session.profile
    profile.calibration.pixel_map = PixelMapConfig(
        degree=1, cu=W / 2, cv=H / 2, s=s,
        coef=[[0.0, K * s], [K * s, 0.0]], zero=[0.0, 0.0],
        ref=[W / 2, H / 2], bounds=[100.0, 100.0, W - 100.0, H - 100.0],
        image_size=[W, H], sweep_z=0.0)
    profile.calibration.pipette_offset = PipetteOffset(dx=10.0, dy=-5.0)
    profile.positions[DISH_POSITION] = (200.0, 150.0, 120.0)


# -- the session ---------------------------------------------------------------

def test_a_measured_dish_bottom_is_saved_and_holds_for_the_robot_session(
        session):
    assert not session.is_confirmed("dish_bottom")
    session.set_dish_bottom(66.423)
    assert session.profile.picking.dish_bottom == 66.42
    assert session.profile.calibration.dish_bottom_set_at is not None
    assert session.is_confirmed("dish_bottom")
    session.new_run()
    assert not session.is_confirmed("dish_bottom")
    assert session.profile.picking.dish_bottom == 66.42      # the value stays


def test_the_checklist_marks_the_dish_bottom_as_the_step_does(session):
    from micropick.gui import readiness
    session.set_dish_bottom(66.4)
    assert readiness._status(session)["dish_bottom"][0]
    session.new_run()                                  # an earlier date
    done, tip, _note = readiness._status(session)["dish_bottom"]
    assert not done
    assert tip.startswith("Not set in this robot session: 66.40 mm, set on")


def test_use_it_takes_the_last_value_for_the_robot_session_only(session):
    _measured(session, 66.4)
    assert not session.is_confirmed("dish_bottom")
    session.use_last_dish_bottom()
    assert session.is_confirmed("dish_bottom")
    assert session.profile.picking.dish_bottom == 66.4       # not changed
    session.new_run()
    assert not session.is_confirmed("dish_bottom")


def test_use_it_is_refused_while_the_dish_bottom_was_never_measured(session):
    assert session.profile.calibration.dish_bottom_set_at is None
    with pytest.raises(SessionError):
        session.use_last_dish_bottom()
    assert not session.is_confirmed("dish_bottom")


# -- the page ------------------------------------------------------------------

def test_ago_says_when_in_days():
    now = datetime.now(timezone.utc)
    assert ago(now) == "today"
    assert ago(now - timedelta(days=1, hours=1)) == "yesterday"
    assert ago(now - timedelta(days=3, hours=1)) == "3 days ago"


def test_last_time_with_use_it(app, session, page):
    assert page.last_card.isHidden()                  # never measured
    _measured(session, 66.4, days=3)
    page.refresh()
    assert not page.last_card.isHidden()
    assert page.last_value.text() == "Last time: Z 66.40 mm"
    assert page.last_when.text() == "3 days ago"
    assert page.result.isHidden()
    page.use_button.click()
    assert session.is_confirmed("dish_bottom")
    assert not page.result.isHidden()
    assert page.result.label.text() == "Dish bottom: Z 66.40 mm"


def test_up_and_down_only_while_lowering(app, session, page):
    _calibrated(session)
    page.refresh()
    assert not page.up_button.isEnabled() and not page.down_button.isEnabled()
    assert page.move_button.isEnabled()
    assert not page.save_button.isEnabled()


def test_measured_with_the_tip(app, session, page):
    _calibrated(session)
    page.refresh()
    robot = session.robot
    page.move_button.click()
    assert _until(app, lambda: page.up_button.isEnabled())
    # Over the dish center, a little above the dish bottom stored now.
    z = float(xyz(robot)[2])
    assert z == pytest.approx(session.profile.picking.dish_bottom
                              + page_module.ABOVE_BOTTOM_MM, abs=0.01)
    assert page.z_readout.text() == f"Z {z:.2f}"

    # Down 10 mm, then up half a millimeter: 0.5 mm over the old value.
    page.set_step(10.0)
    page.down_button.click()
    assert _until(app, lambda: page.z_readout.text() == f"Z {z - 10.0:.2f}"
                  and page.up_button.isEnabled())
    page.set_step(0.5)
    page.up_button.click()
    low = z - 9.5
    assert _until(app, lambda: page.z_readout.text() == f"Z {low:.2f}"
                  and page.up_button.isEnabled())
    assert page.save_button.text() == "Save dish bottom"
    assert page.save_button.isEnabled()

    page.save_button.click()
    assert _until(app, lambda: session.is_confirmed("dish_bottom")
                  and not page.jog.busy)
    assert session.profile.picking.dish_bottom == pytest.approx(low, abs=0.01)
    assert page.result.label.text() == f"Dish bottom: Z {low:.2f} mm"
    assert not page.up_button.isEnabled()
    assert not page.redo_button.isHidden()
    # The tip is raised afterwards.
    assert _until(app, lambda: float(xyz(robot)[2]) > low + 1
                  and not page.jog.busy)


def test_a_big_change_is_asked_about_first(app, session, page, monkeypatch):
    _calibrated(session)
    page.refresh()
    asked = []
    monkeypatch.setattr(page_module.ConfirmDialog, "confirm",
                        classmethod(lambda cls, host, q, **kw:
                                    asked.append(q) or False))
    page.move_button.click()
    assert _until(app, lambda: page.save_button.isEnabled()
                  and not page.jog.busy)
    page.save_button.click()                       # 10 mm over the old value
    assert _until(app, lambda: asked and not page.jog.busy)
    assert not session.is_confirmed("dish_bottom")
    assert session.profile.picking.dish_bottom == 66.1


def test_nothing_moves_while_the_robot_is_busy_elsewhere(app, session, page):
    _calibrated(session)
    page.refresh()
    page.move_button.click()
    assert _until(app, lambda: page.up_button.isEnabled())
    lease = session.robot_lease.claim("picking run", run=True)
    try:
        app.processEvents()
        assert not page.move_button.isEnabled()
        assert not page.up_button.isEnabled()
        assert not page.down_button.isEnabled()
    finally:
        lease.release()
    app.processEvents()
    assert page.up_button.isEnabled()


def _press(app, page, key):
    page.activateWindow()
    assert QTest.qWaitForWindowActive(page)
    QTest.keyClick(page, key)
    app.processEvents()


def test_the_z_and_step_keys_work_while_lowering(app, session, page):
    _calibrated(session)
    page.refresh()
    robot = session.robot
    calls = len(robot.calls)
    _press(app, page, Qt.Key.Key_PageDown)           # not lowering yet
    assert len(robot.calls) == calls
    page.move_button.click()
    assert _until(app, lambda: page.up_button.isEnabled())
    z = float(xyz(robot)[2])
    assert page.carousel.step == page_module.FIRST_STEP_MM
    _press(app, page, Qt.Key.Key_Plus)               # a larger step
    step = page.carousel.step
    assert step > page_module.FIRST_STEP_MM
    _press(app, page, Qt.Key.Key_PageDown)
    assert _until(app, lambda: page.z_readout.text() == f"Z {z - step:.2f}"
                  and page.up_button.isEnabled())
    _press(app, page, Qt.Key.Key_Minus)
    assert page.carousel.step == page_module.FIRST_STEP_MM
    _press(app, page, Qt.Key.Key_PageUp)
    assert _until(app, lambda: page.z_readout.text()
                  == f"Z {z - step + page_module.FIRST_STEP_MM:.2f}"
                  and page.up_button.isEnabled())
    # No XY keys here.
    before = xyz(robot)
    for key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up,
                Qt.Key.Key_Down):
        _press(app, page, key)
    assert _until(app, lambda: not page.jog.busy)
    assert xyz(robot)[:2] == pytest.approx(before[:2], abs=0.05)


def test_keys_lists_the_keys_bound(app, session, page):
    assert page.keys_list.isHidden()
    page.keys_button.click()
    text = page.keys_list.text()
    assert not page.keys_list.isHidden()
    bindings = session.settings.keys
    for action_id in ("z+", "z-", "step_up", "step_down", "help"):
        assert keys.key_text(bindings, action_id) in text
    for action_id in ("x-", "x+", "y+", "y-"):
        assert keys.key_text(bindings, action_id) not in text
    _press(app, page, Qt.Key.Key_H)
    assert page.keys_list.isHidden()


def test_redo_goes_back_to_the_first_step_and_keeps_the_value(
        app, session, page):
    _calibrated(session)
    page.refresh()
    page.move_button.click()
    assert _until(app, lambda: page.save_button.isEnabled()
                  and not page.jog.busy)
    session.profile.picking.dish_bottom = 76.0        # no question asked
    page.save_button.click()
    assert _until(app, lambda: session.is_confirmed("dish_bottom")
                  and not page.jog.busy)
    saved = session.profile.picking.dish_bottom
    assert page.over_step.state == page_module.OK
    page.redo_button.click()
    assert page.over_step.state == page_module.TODO
    assert page.redo_button.isHidden()
    assert session.profile.picking.dish_bottom == saved
    assert session.is_confirmed("dish_bottom")
