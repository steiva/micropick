"""Robot setup's camera calibration sheet (#10): place the marker, say its
slot and move the camera there, center it, calibrate - the sweep a run
with STOP and the pill - and the verdict."""

import os
import threading
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.config.schema import DeckModule                   # noqa: E402
from micropick.core.deck import slot_center                      # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages import calibration_camera               # noqa: E402
from micropick.gui.shell import MainWindow                       # noqa: E402
from micropick.hardware.protocols import xyz                     # noqa: E402
from micropick.workflows.calibrate_camera import Cancelled      # noqa: E402

# Where the mock's camera sees its marker (`hardware.mock.MarkerScene`).
MARKER_XY = (181.5, 162.0)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    w = MainWindow(Options(mock=True))
    w.show()
    w.session.probe_robot()
    w.session.new_run()
    _wait(app, lambda: not w.session.robot_lease.busy)
    yield w
    w.hide()
    w.session.shutdown()
    w.deleteLater()


def _wait(app, condition, timeout_s=10.0):
    end = time.monotonic() + timeout_s
    while not condition() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert condition()


def _open(app, window):
    window.open_robot_setup()
    window.robot_setup_page.rows["camera_calibration"].clicked.emit()
    return window.calibration_sheet


def _at_the_marker(app, window, sheet):
    """Steps 1 and 2 done, and the camera over the marker."""
    sheet.placed_button.click()
    sheet.slot.setCurrentIndex(sheet.slot.findText("Slot 5"))
    sheet.move_button.click()
    _wait(app, lambda: sheet.step == "center")
    window.session.motion.camera_to(MARKER_XY)
    sheet.jog.refresh_position()
    _wait(app, lambda: sheet.centered_button.isEnabled())


def test_the_row_opens_the_sheet_on_the_upper_camera(app, window):
    sheet = _open(app, window)
    assert sheet.isVisible()
    assert sheet.title_label.text() == "Camera calibration"
    assert window.top.path == ["Robot setup", "Camera calibration"]
    _wait(app, lambda: sheet.view.camera is not None)
    assert sheet.view.camera is window.session.camera(
        window.session.upper_camera_label)
    sheet.close_button.click()
    assert not sheet.isVisible()
    assert window.top.path == ["Robot setup"]


def test_the_steps_open_one_after_another(app, window):
    sheet = _open(app, window)
    assert sheet.step == "place"
    assert not sheet.move_button.isEnabled()
    sheet.placed_button.click()
    assert sheet.step == "slot"
    assert sheet.move_button.isEnabled()
    assert not sheet.start_button.isEnabled()


def test_the_slots_are_the_platforms(app, window):
    sheet = _open(app, window)
    slots = [sheet.slot.itemText(i) for i in range(sheet.slot.count())]
    assert slots == [f"Slot {n}" for n in range(1, 12)]
    sheet.close_button.click()
    window.session.profile.deck.modules.append(
        DeckModule(slots=[5, 8], offset=[0, 0, 64]))
    sheet = _open(app, window)
    assert [sheet.slot.itemText(i) for i in range(sheet.slot.count())] \
        == ["Slot 5", "Slot 8"]


def test_move_the_camera_there_puts_the_camera_over_the_slot(app, window):
    sheet = _open(app, window)
    sheet.placed_button.click()
    sheet.slot.setCurrentIndex(sheet.slot.findText("Slot 5"))
    sheet.move_button.click()
    _wait(app, lambda: sheet.step == "center")
    # No tip calibration yet: the camera is that far from the tip by the
    # start offset in Settings.
    dx, dy = window.session.settings.tip_cal_start_offset
    cx, cy = slot_center(5)
    x, y, _z = xyz(window.session.robot)
    assert (x, y) == pytest.approx((cx - dx, cy - dy), abs=0.01)


def test_a_slot_out_of_reach_is_not_moved_to(app, window):
    sheet = _open(app, window)
    sheet.placed_button.click()
    _wait(app, lambda: sheet.jog.pose is not None)
    before = xyz(window.session.robot)
    # Slot 1's center less the start offset is behind the deck.
    sheet.slot.setCurrentIndex(sheet.slot.findText("Slot 1"))
    sheet.move_button.click()
    app.processEvents()
    assert "out of reach" in sheet.jog.position_lines[-1]
    assert xyz(window.session.robot) == before
    assert sheet.step == "slot"


def test_the_picture_says_whether_the_marker_is_in_view(app, window):
    sheet = _open(app, window)
    sheet.placed_button.click()
    sheet.slot.setCurrentIndex(sheet.slot.findText("Slot 4"))
    sheet.move_button.click()
    _wait(app, lambda: sheet.step == "center")
    _wait(app, lambda: sheet.marker_state == "NO MARKER IN VIEW")
    assert not sheet.centered_button.isEnabled()
    window.session.motion.camera_to(MARKER_XY)
    _wait(app, lambda: sheet.marker_state == "MARKER FOUND")
    _wait(app, lambda: sheet.centered_button.isEnabled())


def test_without_a_pixel_map_a_click_moves_nothing(app, window):
    sheet = _open(app, window)
    _at_the_marker(app, window, sheet)
    before = xyz(window.session.robot)
    sheet.view.clicked.emit(100.0, 100.0)
    app.processEvents()
    assert xyz(window.session.robot) == before
    assert "arrows" in sheet.jog.position_lines[-1]


@pytest.fixture
def sweep(monkeypatch):
    """The sweep, held until `go` is set or it is canceled; what it was
    given is in `calls`."""
    go = threading.Event()
    calls = []

    def fake(robot, camera, detector, *, cancel, on_progress, log, **kw):
        calls.append(kw)
        on_progress(1, 49)
        while not go.wait(0.01):
            if cancel.is_set():
                raise Cancelled("canceled")
        raise RuntimeError("the test lets it go only by Stop")

    monkeypatch.setattr(calibration_camera, "calibrate_camera", fake)
    return calls


def test_the_sweep_is_a_run_with_stop_and_the_pill(app, window, sweep):
    sheet = _open(app, window)
    _at_the_marker(app, window, sheet)
    sheet.centered_button.click()
    assert sheet.step == "calibrate"
    window.session.set_settings(window.session.settings.model_copy(
        update={"sweep_grid_n": 9}))
    sheet.start_button.click()
    _wait(app, lambda: sweep)
    lease = window.session.robot_lease
    assert lease.holder == "camera calibration sweep" and lease.holding_run
    assert not window.top.stop.isHidden()
    assert window.top.status.text().endswith("Calibrating the camera")
    assert sheet.notice.text() == "Don't touch the robot."
    assert not sheet.jog.isVisible()
    assert sweep[0]["grid_n"] == 9
    assert sweep[0]["marker_side_mm"] == 6.8
    window.top.stop.click()
    _wait(app, lambda: not lease.busy)
    assert window.top.stop.isHidden()
    # Stopped: the gantry is not where the sweep started.
    assert sheet.step == "center"
    assert window.session.profile.pixel_map is None


# -- moved from the page -----------------------------------------------------

def test_without_the_upper_camera_nothing_starts(app, window):
    window.session.reset_setup()
    sheet = _open(app, window)
    assert sheet.view.camera is None
    assert sheet.camera_note.text() == "Choose the upper camera first."
    assert not sheet.start_button.isEnabled()


def test_the_sweep_parameters_are_the_ones_in_settings(app, window):
    from micropick.gui.pages.calibration_camera import (SWEEP_DEFAULTS,
                                                         sweep_parameters)
    session = window.session
    assert sweep_parameters(session.settings) == SWEEP_DEFAULTS
    sheet = _open(app, window)
    assert "6.8 mm" in sheet.parameters.text()
    session.set_settings(session.settings.model_copy(
        update={"sweep_grid_n": 9}))
    assert "grid 9" in sheet.parameters.text()


def _report(holdout=24.0, resid_max=60.0, side=6.772, frac=(0.9, 0.85)):
    from micropick.core.calibration.pixel_map import FitReport
    return FitReport(degree=3, n_poses=49, n_points=196, resid_mean_um=10.0,
                     resid_max_um=resid_max, holdout_mean_um=holdout,
                     holdout_max_um=80.0, track_side_mm=side,
                     coverage=(0, 0, 100, 100), coverage_frac=frac,
                     scale_center_um=26.0, scale_edge_um=28.8)


def test_the_first_real_sweep_is_good():
    from micropick.gui.pages.calibration_camera import GOOD, judge
    verdict = judge(_report(), 6.8)
    assert verdict.level == GOOD
    assert verdict.line.startswith("Good:")
    assert "marker side 0.4 % off" in verdict.line


def test_the_worst_check_decides_and_is_named():
    from micropick.gui.pages.calibration_camera import (ACCEPTABLE, REDO,
                                                        judge)
    acceptable = judge(_report(side=6.7127), 6.8)          # degree 2's 1.3 %
    assert acceptable.level == ACCEPTABLE
    assert "marker side 1.3 % off (above 1 % off)" in acceptable.line
    redo = judge(_report(holdout=213.0, frac=(0.5, 0.9)), 6.8)   # affine
    assert redo.level == REDO
    assert "held-out error 213.0 µm (over 80 µm)" in redo.line
    assert "50 % of the frame covered (under 60 %)" in redo.line
    assert redo.line.endswith("sweep again.")


def test_without_a_recovered_side_the_side_is_not_judged():
    from micropick.gui.pages.calibration_camera import judge
    verdict = judge(_report(side=None), 6.8)
    assert all(c[0] != "marker side" for c in verdict.checks)
