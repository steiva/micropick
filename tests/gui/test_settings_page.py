"""Settings: the robot's address and the folders, saved for this computer;
the gear in the top bar opens it as a sheet with Advanced (the robot
session by hand) and the Log."""

import json
import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick import paths                                      # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages.settings import (SettingsPage,          # noqa: E402
                                          parse_address)
from micropick.gui.session import Session                        # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def session(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    yield s
    s.shutdown()
    paths.set_overrides()


def test_addresses_are_read_as_typed():
    assert parse_address("10.0.0.5") == ("10.0.0.5", None)
    assert parse_address("ot2.local:31951") == ("ot2.local", 31951)
    assert parse_address("http://10.0.0.5:31950/") == ("10.0.0.5", 31950)
    with pytest.raises(ValueError):
        parse_address("not an address")


def test_save_writes_the_file_and_moves_the_folders(app, session, tmp_path):
    page = SettingsPage(session)
    page.host.setText("10.0.0.5:31951")
    page.folders["outputs_dir"].setText(str(tmp_path / "out"))
    page._save()
    data = json.loads((tmp_path / "settings.json").read_text())
    assert (data["robot_host"], data["robot_port"]) == ("10.0.0.5:31951",
                                                        31951)
    assert session.settings.outputs_dir == str(tmp_path / "out")
    assert paths.outputs_dir() == tmp_path / "out"
    assert session.robot_address == "10.0.0.5:31951"
    page.deleteLater()


def test_a_bad_address_is_not_saved(app, session, tmp_path):
    page = SettingsPage(session)
    page.host.setText("not an address")
    page._save()
    assert "Not saved" in page.save_state.text()
    assert not (tmp_path / "settings.json").exists()
    page.deleteLater()


def test_the_address_is_fixed_while_connected(app, session):
    page = SettingsPage(session)
    assert page.host.isEnabled()
    session.probe_robot()
    assert page.host.isEnabled()                # looked for, not connected
    session.new_run()
    assert not page.host.isEnabled()
    assert "Connected to" in page.robot_state.text()
    page.deleteLater()


def test_a_new_address_is_looked_at_next_while_not_connected(app, session):
    from micropick.config.app_settings import AppSettings
    session.look_at_robot()
    assert session.has_connection
    session.set_settings(AppSettings(robot_host="10.0.0.7"))
    assert not session.has_connection           # the next look makes it anew
    assert session.robot_address == "10.0.0.7:31950"


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    from micropick.gui.shell import MainWindow
    w = MainWindow(Options(mock=True))
    yield w
    w.session.shutdown()
    w.deleteLater()
    paths.set_overrides()


def test_the_gear_opens_settings_as_a_sheet_over_the_page(app, window):
    window.home.tiles["auto"].clicked.emit()
    page = window.current_page()
    window.top.settings.click()
    sheet = window.settings_sheet
    assert sheet.isVisibleTo(window) and window.current_page() is page
    assert window.top.path == ["Auto pick", "Settings"]
    assert sheet.section() == "general"
    sheet.sections.buttons[2].click()
    assert sheet.section() == "log"
    window.top.crumb_clicked.emit(0)               # Auto pick: closes it
    assert not sheet.isVisibleTo(window)
    assert window.top.path == ["Auto pick"] and window.current_page() is page


def test_the_log_is_read_in_the_sheet(app, window):
    import logging
    window.session.page_requested.emit("log")
    sheet = window.settings_sheet
    assert sheet.isVisibleTo(window) and sheet.section() == "log"
    assert window.top.path == ["Settings"]
    logging.getLogger("micropick.test").warning("a line for the log")
    app.processEvents()
    assert "a line for the log" in window.log_page.view.toPlainText()
    window.top.home_clicked.emit()
    assert not sheet.isVisibleTo(window) and window.top.path == []


def test_close_discards_unsaved_changes(app, window):
    window.top.settings.click()
    form = window.settings_page
    form.host.setText("10.0.0.9")
    window.settings_sheet.close_button.click()
    assert not window.settings_sheet.isVisibleTo(window)
    assert form.host.text() == ""
    assert window.top.path == []
    window.top.settings.click()
    form.host.setText("10.0.0.9")
    window.home.tiles["auto"].clicked.emit()       # going elsewhere: the same
    assert not window.settings_sheet.isVisibleTo(window)
    assert form.host.text() == ""


def _advanced(window):
    window.top.settings.click()
    window.settings_sheet.sections.buttons[1].click()
    return window.settings_sheet.advanced


def test_advanced_is_refused_while_the_robot_is_held(app, window):
    session = window.session
    session.look_at_robot()
    session.probe_robot()
    session.new_run()
    advanced = _advanced(window)
    assert advanced.new_button.isEnabled()
    assert advanced.disconnect_button.isEnabled()
    lease = session.robot_lease.claim("picking run", run=True)
    try:
        advanced.refresh()
        for button in (advanced.new_button, advanced.disconnect_button):
            assert not button.isEnabled()
            assert "picking run" in button.toolTip()
        advanced._disconnect()                     # as if clicked anyway
        assert session.robot is not None
        assert advanced.state.text() == "Robot busy."
    finally:
        lease.release()


def _until(app, condition, timeout_s=5.0):
    import time
    end = time.monotonic() + timeout_s
    while not condition() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    assert condition()


def test_disconnect_and_continue_from_advanced(app, window):
    session = window.session
    session.look_at_robot()
    session.probe_robot()
    session.new_run()
    run_id = session.run_state.run_id
    advanced = _advanced(window)
    advanced.disconnect_button.click()
    _until(app, lambda: session.robot is None
           and not session.robot_lease.busy)
    assert session.released == run_id
    app.processEvents()
    assert advanced.continue_button.isVisibleTo(advanced)
    assert not advanced.disconnect_button.isVisibleTo(advanced)
    assert window.home.continue_button.isVisibleTo(window.home)
    advanced.continue_button.click()
    assert session.released is None


def test_start_new_robot_session_from_advanced(app, window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    session = window.session
    session.look_at_robot()
    session.probe_robot()
    session.new_run()
    session.confirm("deck")
    advanced = _advanced(window)
    session._api.calls.clear()
    monkeypatch.setattr(QInputDialog, "exec",
                        lambda self: QInputDialog.DialogCode.Rejected)
    advanced.new_button.click()
    assert session._api.calls == []                # Cancel: nothing
    monkeypatch.setattr(QInputDialog, "exec",
                        lambda self: QInputDialog.DialogCode.Accepted)
    advanced.new_button.click()
    _until(app, lambda: not session.robot_lease.busy)
    # The mock keeps its one run; the robot makes a new one.
    assert "home_robot" in [c[0] for c in session._api.calls]
    assert not session.is_confirmed("deck")        # a new robot session
    assert session.robot is not None


def test_connect_goes_to_the_saved_address_or_the_command_lines(
        app, tmp_path, monkeypatch):
    from opentrons_api import ot2_api
    from micropick.config.app_settings import AppSettings, save_settings
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    save_settings(AppSettings(robot_host="10.0.0.5", robot_port=31951))
    made = []

    class Refusing(ot2_api.OpentronsAPI):
        def get_all_runs(self):
            made.append(self.BASE_URL)
            raise ConnectionError("no robot in a test")

    monkeypatch.setattr(ot2_api, "OpentronsAPI", Refusing)
    for options, url in ((Options(), "http://10.0.0.5:31951"),
                         (Options(robot_host="192.168.1.9:31950"),
                          "http://192.168.1.9:31950")):
        s = Session(options)
        with pytest.raises(ConnectionError):
            s.probe_robot()
        assert made[-1] == url
        s.shutdown()


def test_the_inference_device_is_saved_and_the_detector_reloads(app, session,
                                                                 tmp_path):
    from micropick.gui.detector import STANDIN
    session.detector.load(STANDIN)
    page = SettingsPage(session)
    page.inference_device.setCurrentIndex(
        page.inference_device.findData("gpu"))
    page._save()
    data = json.loads((tmp_path / "settings.json").read_text())
    assert data["inference_device"] == "gpu"
    assert session.detector.name is None          # loaded again when needed
    assert "GPU" in page.inference_state.text()   # found, or none found
    page.deleteLater()
