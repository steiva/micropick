"""Robot setup (REDESIGN §5): one page of rows in three blocks, each with a
mark, a label and a value, reached from Home's Setup card; Reset setup
asks first, with Keep setup the safe choice."""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer                               # noqa: E402
from PySide6.QtWidgets import QApplication                     # noqa: E402

from micropick.config.schema import DeckModule                 # noqa: E402
from micropick.gui.app import Options                          # noqa: E402
from micropick.gui.shell import MainWindow                     # noqa: E402
from micropick.gui.widgets.overlays import ConfirmDialog       # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    w = MainWindow(Options(mock=True))
    w.show()
    yield w
    w.hide()
    w.session.shutdown()
    w.deleteLater()


def _connect(app, window):
    window.session.probe_robot()
    window.session.new_run()
    end = time.monotonic() + 5
    while window.session.robot_lease.busy and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def _rows(window):
    page = window.robot_setup_page
    return [(row.label.text(), row.done, row.value.text())
            for row in page.rows.values()]


def _positions(session):
    session.remember("observe", (245.1, 132.4, 80.0))
    session.remember("shake", (251.0, 140.2, 62.5))
    session.remember("tip_calib", (380.2, 220.6, 95.0))


def test_home_opens_robot_setup_with_its_rows(app, window):
    _connect(app, window)
    home = window.home
    assert home.setup_title.text() == "Complete the setup"
    # The mock profile has its two cameras and nothing else.
    assert home.setup_text.text().startswith("5 steps left")
    home.complete_button.click()
    assert window.current_page() is window.robot_setup_page
    assert window.top.path == ["Robot setup"]
    assert _rows(window) == [
        ("Upper camera", True, "mock-over"),
        ("Camera calibration", False, "Not set."),
        ("Lower camera", True, "mock-under"),
        ("Platforms", False, "Not set."),
        ("Calibration position", False, "Not set."),
        ("Shake position", False, "Not set."),
        ("Position over the dish", False, "Not set."),
    ]
    window.open_home()
    assert home.setup_text.text() == (
        "5 steps left: camera calibration, platforms, calibration position, "
        "shake position, position over the dish.")


def test_done_goes_back_home(app, window):
    window.open_robot_setup()
    page = window.robot_setup_page
    assert page.done_button.text() == "Done"
    page.done_button.click()
    assert window.current_page() is window.home
    assert window.top.path == []


def test_positions_read_as_a_slot_and_coordinates(app, window):
    _positions(window.session)
    window.session.set_deck_modules([
        DeckModule(slots=[5, 8], offset=[0, 0, 64.2], name="Picking platform"),
        DeckModule(slots=[9], offset=[0, 0, 64.2], name="Calibration module")])
    window.open_robot_setup()
    rows = {label: (done, value) for label, done, value in _rows(window)}
    assert rows["Platforms"] == (
        True, "Picking platform in 5 and 8 · Calibration module in 9")
    assert rows["Position over the dish"] == (
        True, "≈ slot 5 · X 245.1 Y 132.4 Z 80.0")
    assert rows["Shake position"] == (True, "≈ slot 5 · X 251.0 Y 140.2 Z 62.5")
    assert rows["Calibration position"] == (
        True, "≈ slot 9 · X 380.2 Y 220.6 Z 95.0")
    assert not any("observe" in v or "tip_calib" in v
                   for _l, _d, v in _rows(window))


def test_a_row_opens_its_item_and_the_crumb_comes_back(app, window):
    window.open_robot_setup()
    window.robot_setup_page.rows["shake_position"].clicked.emit()
    assert window.position_sheet.isVisible()
    assert window.current_page() is window.robot_setup_page
    assert window.top.path == ["Robot setup", "Shake position"]
    window.top.crumb_clicked.emit(0)
    assert not window.position_sheet.isVisible()
    assert window.current_page() is window.robot_setup_page
    assert window.top.path == ["Robot setup"]


def _answer(window, destructive: bool) -> None:
    """Click Reset setup and answer its question."""
    def click():
        (dialog,) = [d for d in window.findChildren(ConfirmDialog)
                     if d.isVisible()]
        assert dialog.safe.text() == "Keep setup"
        (dialog.destructive if destructive else dialog.safe).click()
    QTimer.singleShot(0, click)
    window.robot_setup_page.reset_button.click()


def test_keep_setup_leaves_everything(app, window):
    _positions(window.session)
    window.open_robot_setup()
    before = _rows(window)
    _answer(window, destructive=False)
    assert _rows(window) == before
    assert set(window.session.profile.positions) == {
        "observe", "shake", "tip_calib"}
    folder = window.session.profile.path
    assert not list(folder.parent.glob(f"{folder.name}-*.zip"))


def test_reset_clears_the_setup_after_the_question(app, window):
    _positions(window.session)
    window.open_robot_setup()
    _answer(window, destructive=True)
    assert [done for _l, done, _v in _rows(window)] == [False] * 7
    assert window.session.profile.positions == {}
    folder = window.session.profile.path
    assert len(list(folder.parent.glob(f"{folder.name}-*.zip"))) == 1
    window.open_home()
    assert window.home.setup_text.text().startswith("7 steps left")
