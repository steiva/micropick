"""Robot setup's camera sheets (#9): the live picture of the camera chosen
in the Select, which follows the choice; Done saves it (the other role's
device swapped in), Cancel and × leave it as it was."""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton        # noqa: E402

from micropick.gui.app import Options                          # noqa: E402
from micropick.gui.shell import MainWindow                     # noqa: E402


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


def _wait(app, condition, timeout_s=5.0):
    end = time.monotonic() + timeout_s
    while not condition() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert condition()


def _open(app, window, item):
    window.open_robot_setup()
    window.robot_setup_page.rows[item].clicked.emit()
    sheet = window.camera_sheet
    _wait(app, lambda: sheet.view.camera is not None)
    return sheet


def _devices(window):
    return {role: window.session.camera_device(role)
            for role in ("upper", "lower")}


def test_a_camera_row_opens_its_sheet_on_the_live_picture(app, window):
    sheet = _open(app, window, "upper_camera")
    assert sheet.isVisible()
    assert sheet.title_label.text() == "Upper camera"
    assert sheet.question.text() == "Which camera is the upper camera?"
    assert [sheet.select.itemText(i) for i in range(sheet.select.count())] \
        == ["mock-over", "mock-under"]
    assert sheet.select.currentText() == "mock-over"
    assert sheet.view.camera is window.session.camera("over")
    assert window.top.path == ["Robot setup", "Upper camera"]


def test_the_picture_follows_the_choice_and_done_swaps(app, window):
    sheet = _open(app, window, "upper_camera")
    sheet.select.setCurrentIndex(sheet.select.findText("mock-under"))
    _wait(app, lambda: sheet.view.camera is window.session.camera("under")
          and sheet.view.camera is not None)
    assert _devices(window) == {"upper": "mock-over", "lower": "mock-under"}
    sheet.done_button.click()
    assert not sheet.isVisible()
    assert _devices(window) == {"upper": "mock-under", "lower": "mock-over"}
    assert window.robot_setup_page.rows["upper_camera"].value.text() \
        == "mock-under"
    assert window.top.path == ["Robot setup"]


@pytest.mark.parametrize("close", ["cancel_button", "close_button"])
def test_cancel_and_close_leave_the_choice(app, window, close):
    sheet = _open(app, window, "lower_camera")
    sheet.select.setCurrentIndex(sheet.select.findText("mock-over"))
    _wait(app, lambda: sheet.view.camera is window.session.camera("over")
          and sheet.view.camera is not None)
    getattr(sheet, close).click()
    assert not sheet.isVisible()
    assert _devices(window) == {"upper": "mock-over", "lower": "mock-under"}
    assert window.top.path == ["Robot setup"]


def test_a_profile_without_the_camera_gets_it_on_done(app, window):
    window.session.reset_setup()
    sheet = _open(app, window, "lower_camera")
    assert sheet.select.currentText() == "mock-over"
    assert window.session.open_cameras == []
    sheet.select.setCurrentIndex(sheet.select.findText("mock-under"))
    _wait(app, lambda: sheet.view.camera is not None
          and sheet.view.camera.crop == 0.5)
    sheet.done_button.click()
    assert _devices(window) == {"upper": None, "lower": "mock-under"}
    assert window.robot_setup_page.rows["lower_camera"].done


def test_done_is_gray_while_the_robot_is_busy(app, window):
    sheet = _open(app, window, "upper_camera")
    assert sheet.done_button.isEnabled()
    lease = window.session.robot_lease.claim("picking run", run=True)
    app.processEvents()
    assert not sheet.done_button.isEnabled()
    assert sheet.done_button.toolTip() == "Robot busy: picking run."
    lease.release()
    app.processEvents()
    assert sheet.done_button.isEnabled()


def test_opening_camera_is_shown_until_the_first_frame(app, window):
    window.open_robot_setup()
    window.robot_setup_page.rows["upper_camera"].clicked.emit()
    sheet = window.camera_sheet
    assert sheet.opening.isVisible()
    assert sheet.opening_label.text() == "Opening camera…"
    _wait(app, lambda: not sheet.opening.isVisible())
    assert sheet.view.camera is window.session.camera("over")


def test_no_camera_attached_shows_no_opening(app, window, monkeypatch):
    monkeypatch.setattr(window.session, "attached_cameras", lambda: [])
    window.session.reset_setup()
    window.open_robot_setup()
    window.robot_setup_page.rows["upper_camera"].clicked.emit()
    sheet = window.camera_sheet
    app.processEvents()
    assert not sheet.opening.isVisible()
    assert sheet.state.text() == "No camera is attached."


def _focus_shown(app, sheet):
    _wait(app, lambda: sheet.focus_row.isVisible())


def test_focus_is_inline_for_a_camera_with_focus(app, window):
    sheet = _open(app, window, "lower_camera")
    _focus_shown(app, sheet)
    assert sheet.focus_slider.value() == 500
    assert sheet.focus_default.text() == "Default (960)"
    sheet.select.setCurrentIndex(sheet.select.findText("mock-over"))
    _wait(app, lambda: sheet.view.camera is window.session.camera("over")
          and sheet.view.camera is not None)
    _wait(app, lambda: not sheet.opening.isVisible())
    assert not sheet.focus_row.isVisible()         # this one has no focus


def test_focus_is_inline_for_a_device_only_previewed(app, window):
    window.session.reset_setup()
    sheet = _open(app, window, "lower_camera")
    sheet.select.setCurrentIndex(sheet.select.findText("mock-under"))
    _wait(app, lambda: sheet.view.camera is not None
          and sheet.view.camera.crop == 0.5)
    _focus_shown(app, sheet)
    sheet.focus_number.setValue(700)
    _wait(app, lambda: sheet.view.camera.get_control("focus") == 700)
    sheet.done_button.click()
    label = window.session.lower_camera_label
    assert window.session.profile.cameras[label].controls["focus"] == 700


def test_focus_is_tried_live_and_saved_with_done(app, window):
    sheet = _open(app, window, "lower_camera")
    camera = window.session.camera("under")
    _focus_shown(app, sheet)
    sheet.focus_slider.setValue(640)
    _wait(app, lambda: camera.get_control("focus") == 640)
    assert window.session.profile.cameras["under"].controls["focus"] == 500
    sheet.done_button.click()
    assert window.session.profile.cameras["under"].controls["focus"] == 640


def test_default_puts_back_960(app, window):
    sheet = _open(app, window, "lower_camera")
    camera = window.session.camera("under")
    _focus_shown(app, sheet)
    sheet.focus_default.click()
    assert sheet.focus_number.value() == 960
    _wait(app, lambda: camera.get_control("focus") == 960)


def test_close_does_not_save_the_focus_and_puts_it_back(app, window):
    sheet = _open(app, window, "lower_camera")
    camera = window.session.camera("under")
    _focus_shown(app, sheet)
    sheet.focus_slider.setValue(640)
    _wait(app, lambda: camera.get_control("focus") == 640)
    sheet.close_button.click()
    assert window.session.profile.cameras["under"].controls["focus"] == 500
    assert camera.get_control("focus") == 500


def test_there_is_no_focus_button_or_window(app, window):
    sheet = _open(app, window, "lower_camera")
    assert not hasattr(sheet, "focus_button")
    assert not hasattr(sheet, "feed_window")
    labels = {b.text() for b in sheet.findChildren(QPushButton)}
    assert "Focus" not in labels
