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
    monkeypatch.setattr(type(window.manual_page.jog), "busy",
                        property(lambda self: True))
    window.status.home.click()
    assert asked == []
    assert "busy" in window.status.currentMessage()
