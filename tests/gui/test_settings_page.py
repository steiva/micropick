"""The Settings page: the robot's address and the folders, saved for this
computer; the gear in the status bar opens it."""

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
    assert not page.host.isEnabled()
    assert "Disconnect" in page.robot_state.text()
    page.deleteLater()


def test_the_gear_opens_the_settings_page(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    from micropick.gui.shell import MainWindow
    window = MainWindow(Options(mock=True))
    window.gear.click()
    assert window.stack.currentWidget() is window.settings_page
    assert not any(tab.isChecked() for tab in window.tabs.values())
    window.session.shutdown()
    window.deleteLater()


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
