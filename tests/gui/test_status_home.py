"""Home robot position from the status bar: asks, refuses while busy, and
homes through the shown page's jog panel or on its own."""

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
    assert window.status.home.isEnabled()


def test_off_until_connected(app, window):
    assert not window.status.home.isEnabled()
    _connect(app, window)


@pytest.mark.parametrize("page", ["profile", "manual"])
def test_asks_then_homes_from_any_page(app, window, monkeypatch, page):
    _connect(app, window)
    window.show()
    window.show_page(page)
    before = len(_homes(window))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: NO)
    window.status.home.click()
    _settle(app, window)
    assert len(_homes(window)) == before
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: YES)
    window.status.home.click()
    _settle(app, window)
    assert len(_homes(window)) == before + 1
    window.hide()


def test_refused_while_a_jog_panel_is_busy(app, window, monkeypatch):
    _connect(app, window)
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(1) or YES)
    # What a jog step holds while it moves, on whichever page.
    lease = window.session.robot_lease.claim("moving the robot")
    try:
        window._home_robot()
        assert asked == []
        assert "busy" in window.status.currentMessage()
        assert not window.status.home.isEnabled()
    finally:
        lease.release()


def test_a_camera_is_closed_from_its_window_unless_the_robot_is_busy(window):
    session = window.session
    label = session.lower_camera_label
    session.connect_robot()           # the mock scene needs the gantry
    session.open_camera(label)
    feed = window._feed(label)
    window._robot_busy = lambda: True
    assert window._close_camera(label) == "Robot busy."
    assert session.camera(label) is not None
    window._robot_busy = lambda: False
    feed.close_button.click()
    assert session.camera(label) is None
    assert not feed.isVisible()


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
