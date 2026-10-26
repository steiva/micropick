"""A run seen from every screen (REDESIGN §3, §4): the top bar's pill says
what the run is doing and goes back to it, STOP shows only while a run has
the robot and does what Esc does, Park is refused during one, and Home's
tiles are dimmed under a line naming where it is."""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox         # noqa: E402

from micropick.gui import shell                                 # noqa: E402
from micropick.gui.app import Options                           # noqa: E402
from micropick.gui.shell import MainWindow                      # noqa: E402

BUSY_NOTE = "The robot is busy. Go back to {} to stop or finish it."


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
    while window.session.robot_lease.busy and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def _connect(app, window):
    window.session.probe_robot()
    window.session.new_run()
    _settle(app, window)


def _pill(window):
    pill = window.top.status
    return None if pill.isHidden() else (pill.text().split("&nbsp;")[-1],
                                         pill.tone)


def _stop_shown(window):
    return not window.top.stop.isHidden()


@pytest.fixture
def picking_run(app, window):
    """A picking run started on Auto pick's Picking step."""
    _connect(app, window)
    window.open_task("auto")
    window.show_step("picking")
    lease = window.session.robot_lease.claim("picking run", run=True)
    yield lease
    lease.release()


# -- the pill and STOP -------------------------------------------------------


def test_idle_there_is_neither_pill_nor_stop(app, window):
    _connect(app, window)
    window._refresh()
    assert _pill(window) is None
    assert not _stop_shown(window)


@pytest.mark.parametrize("what", ["moving the robot", "Park", "Go to"])
def test_a_short_job_shows_neither(app, window, what):
    _connect(app, window)
    lease = window.session.robot_lease.claim(what)
    try:
        window._refresh()
        assert _pill(window) is None
        assert not _stop_shown(window)
        assert window.home.run_note.isHidden()
    finally:
        lease.release()


@pytest.mark.parametrize("holder, text", [
    ("picking run", "Picking"),
    ("liquid run", "Handling liquid"),
    ("camera calibration sweep", "Calibrating the camera"),
    ("pipette calibration", "Calibrating the tip"),
])
def test_a_run_shows_the_pill_and_stop(app, window, holder, text):
    _connect(app, window)
    lease = window.session.robot_lease.claim(holder, run=True)
    try:
        app.processEvents()
        assert _pill(window) == (text, "busy")
        assert _stop_shown(window)
    finally:
        lease.release()
    app.processEvents()
    assert _pill(window) is None
    assert not _stop_shown(window)


@pytest.mark.parametrize("activity, pill", [
    ("paused", ("Paused", "paused")),
    ("waiting", ("Waiting for you", "waiting")),
    (None, ("Picking", "busy")),
])
def test_the_pill_follows_the_run_page(app, window, picking_run,
                                       monkeypatch, activity, pill):
    monkeypatch.setattr(window.picking_page, "run_activity",
                        lambda: activity)
    window.session.run_activity_changed.emit()
    assert _pill(window) == pill
    assert _stop_shown(window)          # paused and waiting included


def test_the_pill_is_seen_on_home(app, window, picking_run):
    window.open_home()
    app.processEvents()
    assert _pill(window) == ("Picking", "busy")


def test_a_click_on_the_pill_goes_back_to_the_run(app, window, picking_run):
    window.open_home()
    window.top.status_clicked.emit()
    assert window.task == "auto" and window.step == "picking"
    assert window.current_page() is window.picking_page
    assert window.top.path == ["Auto pick"]


def test_a_run_started_out_of_a_task_is_gone_back_to_on_its_page(app,
                                                                  window):
    _connect(app, window)
    window.open_home()
    window.open_setup_item("camera_calibration")
    lease = window.session.robot_lease.claim("camera calibration sweep",
                                             run=True)
    try:
        app.processEvents()
        window.open_home()
        assert window.home.run_note.text() == BUSY_NOTE.format(
            "Camera calibration")
        window.top.status_clicked.emit()
        assert window.current_page() is window.camera_calibration_page
        assert window.top.path == ["Camera calibration"]
    finally:
        lease.release()


def test_stop_does_what_esc_does(app, window, picking_run, monkeypatch):
    stops, holds = [], []
    window.session.stop_requested.connect(lambda: stops.append(1))
    monkeypatch.setattr(shell, "any_running", lambda: True)
    monkeypatch.setattr(window, "_hold", lambda: holds.append(1))
    window.top.stop.click()
    assert stops == [1] and holds == []
    window.top.stop.click()                 # again within 10 s: the hold
    assert holds == [1]


# -- Park and Home during a run ---------------------------------------------


def test_park_is_refused_during_a_run(app, window, picking_run, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(1))
    app.processEvents()
    assert not window.top.park.isEnabled()
    assert "picking run" in window.top.park.toolTip()
    window._park()
    assert asked == []
    assert "busy" in window.toast.text()


def test_parking_shows_until_the_move_ends(app, window, monkeypatch):
    _connect(app, window)
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.open_home()                       # no jog panel: its own worker
    window._park()
    assert window.top.park.text() == "Parking…"
    assert not _stop_shown(window)
    _settle(app, window)
    assert window.top.park.text() == "Park"


def test_home_during_a_run_dims_the_tiles(app, window, picking_run):
    window.open_home()
    app.processEvents()
    home = window.home
    assert not home.tile_row.isEnabled()
    assert not home.tiles_dim.isHidden()
    assert home.run_note.text() == BUSY_NOTE.format("Auto pick")
    assert not home.run_note.isHidden()
    picking_run.release()
    app.processEvents()
    assert home.tile_row.isEnabled()
    assert home.tiles_dim.isHidden()
    assert home.run_note.isHidden()


def test_the_liquid_page_says_paused_or_waiting(window):
    page = window.liquid_page
    assert page.run_activity() is None
    page._running = True
    try:
        assert page.run_activity() is None
        page._pause.set()
        assert page.run_activity() == "paused"
        page._checking = "Check the first well."
        assert page.run_activity() == "waiting"
    finally:
        page._running, page._checking = False, None
        page._pause.clear()
