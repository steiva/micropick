"""The jog panel's face is the JogPad: its buttons move the robot through
the panel, its steps are the controller's, its keycaps the keys as bound."""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt                                    # noqa: E402
from PySide6.QtTest import QTest                                 # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget              # noqa: E402

from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.session import Session                        # noqa: E402
from micropick.gui.widgets.camera_view import CameraView         # noqa: E402
from micropick.gui.widgets.jog_panel import JogPanel             # noqa: E402


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


def _settled(app, panel):
    end = time.monotonic() + 5
    while panel.busy and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert not panel.busy


@pytest.fixture
def shown(app, session):
    """A page with a picture and a panel, on screen."""
    host = QWidget()
    view = CameraView(host)
    panel = JogPanel(session, shortcut_host=host, parent=host)
    panel.show_position_on(view)
    host.show()
    host.activateWindow()
    _settled(app, panel)
    yield host, view, panel
    host.deleteLater()


def test_a_pad_button_steps_the_robot_by_the_pads_step(app, session, shown):
    _host, _view, panel = shown
    panel.pad.set_step(2, emit=True)
    before = session.robot.get_position()[0]
    panel.pad.buttons["y+"].click()
    _settled(app, panel)
    after = session.robot.get_position()[0]
    assert after["y"] == pytest.approx(before["y"] + 2, abs=0.05)
    assert after["x"] == pytest.approx(before["x"], abs=0.05)


def test_the_pad_steps_are_the_controllers(app, shown):
    _host, _view, panel = shown
    panel.pad.bigger.click()
    assert panel.controller.step == panel.pad.step
    panel.set_step(0.05)
    assert panel.pad.step == 0.05


def test_the_pad_is_grayed_with_the_holder_while_the_robot_is_held(
        app, session, shown):
    _host, _view, panel = shown
    lease = session.robot_lease.claim("picking run", run=True)
    try:
        app.processEvents()
        button = panel.pad.buttons["x+"]
        assert not button.isEnabled()
        assert "picking run" in button.toolTip()
        before = session.robot.get_position()[0]
        panel.pad.jog.emit("x", +1)
        _settled(app, panel)
        assert session.robot.get_position()[0] == before
    finally:
        lease.release()
    app.processEvents()
    assert panel.pad.buttons["x+"].isEnabled()


def test_a_key_rebound_in_settings_is_on_the_pads_keycap(app, session, shown):
    _host, _view, panel = shown
    panel.pad.keys.setChecked(True)
    assert panel.pad.buttons["z+"].keycap == "PgUp"
    session.set_settings(session.settings.model_copy(
        update={"keys": {"z+": "Home"}}))
    assert panel.pad.buttons["z+"].keycap == "Home"


def test_h_shows_and_hides_the_keys_on_the_pad(app, shown):
    host, _view, panel = shown
    assert not panel.pad.keys.isChecked()
    QTest.keyClick(host, Qt.Key.Key_H)
    assert panel.pad.keys.isChecked()
    QTest.keyClick(host, Qt.Key.Key_H)
    assert not panel.pad.keys.isChecked()


def test_the_pads_key_list_has_the_pages_keys(app, shown):
    _host, _view, panel = shown
    panel.add_help(["A   aspirate"])
    assert any("aspirate" in line for line in panel.help_lines)
    assert any("aspirate" in line for line in panel.pad.key_lines)


def test_undo_step_on_the_pad_undoes_the_last_jog_step(app, session, shown):
    _host, _view, panel = shown
    panel.pad.set_step(2, emit=True)
    before = session.robot.get_position()[0]
    panel.pad.buttons["x+"].click()
    _settled(app, panel)
    assert session.robot.get_position()[0]["x"] == pytest.approx(
        before["x"] + 2, abs=0.05)
    assert panel.pad.undo_button.text() == "Undo step"
    panel.pad.undo_button.click()
    _settled(app, panel)
    after = session.robot.get_position()[0]
    assert after["x"] == pytest.approx(before["x"], abs=0.05)


def test_undo_step_is_grayed_while_the_robot_is_held(app, session, shown):
    _host, _view, panel = shown
    lease = session.robot_lease.claim("picking run", run=True)
    try:
        app.processEvents()
        assert not panel.pad.undo_button.isEnabled()
        assert "picking run" in panel.pad.undo_button.toolTip()
    finally:
        lease.release()
    app.processEvents()
    assert panel.pad.undo_button.isEnabled()


def test_the_pad_folds_into_its_corner(app):
    """Folded, only its title row is left over the picture; its keys still
    move the robot, they are the panel's (#50)."""
    from micropick.gui.widgets.jog_pad import JogPad
    pad = JogPad()
    pad.show()
    tall = pad.sizeHint().height()
    assert not pad.folded
    pad.fold_button.click()
    assert pad.folded
    assert not pad.buttons["x+"].isVisible() and pad.carousel.isHidden()
    assert pad.sizeHint().height() < tall / 2
    pad.fold_button.click()
    assert not pad.folded and pad.buttons["x+"].isVisible()
    pad.deleteLater()
