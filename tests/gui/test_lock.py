"""Lock: nothing moves the robot until it is turned off (REDESIGN §3).

The session refuses: the robot wrapper takes no command that moves, and a
claim of the robot's lease is refused with "Robot locked.". The lights still
switch. The window covers the page with the lock overlay; Unlock lets go.
"""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel              # noqa: E402

from micropick.gui.app import Options                           # noqa: E402
from micropick.gui.session import Session                       # noqa: E402
from micropick.gui.shell import MainWindow                      # noqa: E402
from opentrons_api.ot2_api import RobotHeld                     # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def session(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    s.probe_robot()
    s.new_run()
    yield s
    s.shutdown()


# -- the session's refusals ------------------------------------------------


def test_locked_the_robot_takes_no_move(session):
    assert session.set_locked(True)
    assert session.locked
    with pytest.raises(RobotHeld):
        session.robot.move_relative("x", 1.0)
    with pytest.raises(RobotHeld):
        session.robot.home_robot()


def test_locked_a_claim_is_refused_as_locked(session):
    session.set_locked(True)
    assert session.robot_lease.claim("jog") is None
    assert session.robot_lease.refusal == "Robot locked."


def test_unlocked_the_robot_moves_and_is_claimed_again(session):
    session.set_locked(True)
    session.set_locked(False)
    assert not session.locked
    session.robot.move_relative("x", 1.0)
    lease = session.robot_lease.claim("jog")
    assert lease is not None
    assert session.robot_lease.refusal == "Robot busy."
    lease.release()


def test_the_lights_switch_while_locked(session):
    session.set_locked(True)
    session.set_lights(True)
    assert session.lights is True
    session.set_lights(False)
    assert session.lights is False


def test_lock_is_refused_while_a_run_has_the_robot(session):
    run = session.robot_lease.claim("picking run", run=True)
    assert not session.set_locked(True)
    assert not session.locked
    session.robot.move_relative("x", 1.0)            # the run goes on
    run.release()
    assert session.set_locked(True)


def test_the_stop_hold_let_go_keeps_the_lock(session):
    session.set_locked(True)
    session.hold_robot()
    session.release_robot()
    with pytest.raises(RobotHeld):
        session.robot.move_relative("x", 1.0)


def test_a_lock_holds_a_robot_connected_after_it(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    try:
        s.set_locked(True)
        s.probe_robot()
        with pytest.raises(RobotHeld):
            s.new_run()                               # homes: a move
    finally:
        s.shutdown()


# -- the window ------------------------------------------------------------


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    w = MainWindow(Options(mock=True))
    w.session.probe_robot()
    w.session.new_run()
    app.processEvents()
    yield w
    w.session.shutdown()
    w.deleteLater()


def _texts(widget):
    return [label.text() for label in widget.findChildren(QLabel)]


def test_lock_covers_the_page_and_unlock_lets_go(app, window):
    assert not window.top.lock.isHidden() and window.top.lock.isEnabled()
    window.top.lock.click()
    app.processEvents()
    assert window.session.locked
    assert not window.lock_overlay.isHidden()
    assert "Robot locked" in _texts(window.lock_overlay)
    assert ("Nothing will move. Work with your hands safely."
            in _texts(window.lock_overlay))
    assert not window.stack.isEnabled()
    assert window.top.lock.isChecked()

    window.lock_overlay.unlock.click()
    app.processEvents()
    assert not window.session.locked
    assert window.lock_overlay.isHidden()
    assert window.stack.isEnabled()
    assert not window.top.lock.isChecked()


def test_lock_is_grayed_while_a_run_has_the_robot(app, window):
    run = window.session.robot_lease.claim("picking run", run=True)
    app.processEvents()
    assert not window.top.lock.isEnabled()
    assert "picking run" in window.top.lock.toolTip()
    run.release()
    app.processEvents()
    assert window.top.lock.isEnabled()


def test_the_light_button_works_while_locked(app, window):
    window.top.lock.click()
    app.processEvents()
    before = window.session.read_lights()
    window.top.light.click()
    end = time.monotonic() + 5
    while window.session.lights == before and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    assert window.session.lights is (not before)
    assert window.top.light.isEnabled()


def test_park_is_grayed_while_locked(app, window):
    window.top.lock.click()
    app.processEvents()
    assert not window.top.park.isEnabled()
    assert window.top.park.toolTip() == "Robot locked."
    window.lock_overlay.unlock.click()
    app.processEvents()
    assert window.top.park.isEnabled()
