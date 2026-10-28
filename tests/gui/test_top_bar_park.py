"""Park from the top bar: refuses while busy, and moves the head to the
back right at the travel height - no home - through the shown page's jog
panel or on its own. Stop, its hold, and closing the window."""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox          # noqa: E402

from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.shell import MainWindow                       # noqa: E402

YES, NO = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    w = MainWindow(Options(mock=True))
    yield w
    w.session.shutdown()
    w.deleteLater()


def _settle(app, window, timeout_s=5.0):
    end = time.monotonic() + timeout_s
    while window._robot_busy() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def _homes(window):
    return [c for c in window.session.robot.calls if c[0] == "home_robot"]


def _connect(app, window):
    window.session.probe_robot()
    window.session.new_run()
    _settle(app, window)
    assert window.top.park.isEnabled()


def test_off_until_connected(app, window):
    assert not window.top.park.isEnabled()
    _connect(app, window)


@pytest.mark.parametrize("page", ["home", "manual"])
def test_parks_without_homing_from_any_page(app, window, page):
    from micropick.hardware.protocols import xyz
    from micropick.workflows.motion import PARK_XY
    _connect(app, window)
    window.show()
    window.show_page(page)
    robot = window.session.robot
    x, y, _ = xyz(robot)
    robot.move_to_coordinates((x, y, 20.0))       # the tip down low
    homes = len(_homes(window))
    retracts = robot.calls.count(("retract_axis", "leftZ"))
    window.top.park.click()
    _settle(app, window)
    assert len(_homes(window)) == homes
    assert robot.calls.count(("retract_axis", "leftZ")) == retracts + 1
    px, py, pz = xyz(robot)
    assert (px, py) == pytest.approx(PARK_XY, abs=0.05)
    assert pz == pytest.approx(window.session.motion.travel_z, abs=0.05)
    # At the travel height already: no retract, one move.
    window.top.park.click()
    _settle(app, window)
    assert robot.calls.count(("retract_axis", "leftZ")) == retracts + 1
    window.hide()


def test_refused_while_a_jog_panel_is_busy(app, window, monkeypatch):
    _connect(app, window)
    robot = window.session.robot
    before = list(robot.calls)
    # What a jog step holds while it moves, on whichever page.
    lease = window.session.robot_lease.claim("moving the robot")
    try:
        window._park()
        assert robot.calls == before
        assert "busy" in window.toast.text()
        assert not window.top.park.isEnabled()
    finally:
        lease.release()


def test_a_second_stop_holds_the_robot_and_keeps_its_session(app, window,
                                                             monkeypatch):
    from micropick.gui.workers import Worker
    from opentrons_api.ot2_api import RobotHeld

    _connect(app, window)
    session = window.session
    robot, run = session.robot, session.run_state
    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda box: shown.append(
        box.text()) or 0)

    def job():
        # A job that does not listen to Stop: only the hold ends it.
        while True:
            robot.move_relative("x", 0.1)
            time.sleep(0.01)

    worker = Worker(job, what="moving")
    failures = []
    worker.failed.connect(failures.append)
    worker.start()
    window._stop()
    window._stop()
    assert shown == ["Robot held."]
    assert session.robot_held
    end = time.monotonic() + 5
    while (worker.running or session.robot_held) and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    assert failures and "Stop was pressed twice" in failures[0]
    assert not session.robot_held                  # let go once it stopped
    assert session.robot is robot and session.run_state is run
    robot.move_relative("x", 0.1)                  # moves again
    robot.hold.set()
    with pytest.raises(RobotHeld):
        robot.move_relative("x", 0.1)
    robot.hold.clear()


def test_closing_during_a_run_asks_and_stays_open_until_it_has_stopped(
        app, window, monkeypatch):
    from PySide6.QtGui import QCloseEvent
    from micropick.gui import shell
    _connect(app, window)
    monkeypatch.setattr(shell, "CLOSE_WAIT_S", 0.2)
    stops = []
    window.session.stop_requested.connect(lambda: stops.append(1))
    lease = window.session.robot_lease.claim("pipette calibration", run=True)
    robot = window.session.robot
    try:
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: NO)
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted()
        assert window.session.robot is not None and stops == []
        # Yes: the run is told to stop, but does not in time - the pipette
        # calibration hears Stop only in its touch-up. The window stays,
        # the robot session with it, and nothing is sent into the run.
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: YES)
        before = robot.calls.count(("retract_axis", "leftZ"))
        event = QCloseEvent()
        window.closeEvent(event)
        assert stops == [1]
        assert not event.isAccepted()
        assert window.session.robot is robot
        assert robot.calls.count(("retract_axis", "leftZ")) == before
        assert "still" in window.toast.text()
    finally:
        lease.release()
    # Stopped: now it closes, and parks the axis.
    window.closeEvent(QCloseEvent())
    assert window.session.robot is None
    assert robot.calls.count(("retract_axis", "leftZ")) == before + 1


def test_closing_after_a_short_job_parks_the_axis(app, window):
    from PySide6.QtGui import QCloseEvent
    _connect(app, window)
    robot = window.session.robot
    before = robot.calls.count(("retract_axis", "leftZ"))
    window.closeEvent(QCloseEvent())
    assert robot.calls.count(("retract_axis", "leftZ")) == before + 1


def test_a_second_close_while_the_first_waits_does_not_shut_down_twice(
        app, window):
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QCloseEvent
    _connect(app, window)
    robot = window.session.robot
    before = robot.calls.count(("retract_axis", "leftZ"))
    shutdowns = []
    real = window.session.shutdown
    window.session.shutdown = lambda: (shutdowns.append(1), real())
    lease = window.session.robot_lease.claim("Home")   # a short job
    second = []
    QTimer.singleShot(50, lambda: second.append(
        window.closeEvent(QCloseEvent())))
    QTimer.singleShot(150, lease.release)
    window.closeEvent(QCloseEvent())
    assert second                                      # it did come in
    assert shutdowns == [1]
    assert window.session.robot is None
    assert robot.calls.count(("retract_axis", "leftZ")) == before + 1
