"""The pipette calibration page: one page, the starting offset's defaults,
and a touch-up whose arrows follow the lower camera."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages.calibration_pipette import (            # noqa: E402
    DEFAULT_OFFSET_MM, PipetteCalibration)
from micropick.gui.session import Session                        # noqa: E402
from micropick.gui.widgets.jog_panel import view_move            # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def page(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    s.load_profile("mock")
    s.probe_robot()
    s.new_run()
    p = PipetteCalibration(s)
    yield p
    p.deleteLater()
    s.shutdown()


def test_no_offset_in_the_profile_starts_from_the_defaults(page):
    assert page.session.profile.calibration.pipette_offset is None
    assert (page.dx.value(), page.dy.value()) == DEFAULT_OFFSET_MM
    assert "Start values" in page.offset_note.text()
    page.dx.setValue(3.0)
    page._refresh()
    assert page.dx.value() == 3.0                  # typed, so kept
    page.reset_offset_button.click()
    assert (page.dx.value(), page.dy.value()) == DEFAULT_OFFSET_MM


def test_one_page_with_the_gate_in_words(page):
    assert not hasattr(page, "stack")
    assert not page.start_button.isEnabled()
    assert page.checks.text().startswith("Before Start:")
    assert page.details.toggle.isChecked() is False     # Details folded
    assert page.accept_button.text() == "Done"


def test_the_touch_up_follows_the_lower_camera_in_xy_only(app, page):
    page._begin_touch_up()
    jog = page.touch_jog
    axes = page.session.profile.calibration.tip_target.axes
    assert jog._view_axes == tuple(tuple(r) for r in axes)
    assert view_move(jog._view_axes, "x", +1) == ("y", +1)
    assert jog._xy_only
    assert not any("PgUp" in line for line in jog.help_lines)
    assert not page.jog.isVisible()                    # the disc panel steps aside
    page._abort()


def _until(app, condition, timeout_s=10.0):
    import time
    end = time.monotonic() + timeout_s
    while not condition() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    return condition()


def test_abort_waits_for_a_touch_up_step_still_moving(app, page):
    import threading
    from micropick.gui.workers import Worker
    session = page.session
    # The calibration as it runs: it holds the robot and waits in the
    # touch-up for the operator.
    lease = session.robot_lease.claim("pipette calibration", run=True)
    worker = Worker(lambda: page._touch_up(None, None, None), lease=lease)
    page._worker = worker
    worker.start()
    assert _until(app, lambda: not page.touch_box.isHidden())
    # A touch-up step, still moving when Abort is pressed.
    moving, done = threading.Event(), threading.Event()

    def step(_log):
        moving.set()
        done.wait(10)

    assert page.touch_jog.run_job(step)
    assert moving.wait(5)
    page._abort()
    assert session.robot_lease.holder == "pipette calibration"
    assert worker.running and not page.touch_box.isHidden()
    done.set()
    assert _until(app, lambda: not page.touch_jog.busy)
    page._abort()
    assert _until(app, lambda: not session.robot_lease.busy)
