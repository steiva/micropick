"""The step on the picture (#99): a chip by X Y Z, the pad's step and its
color, on every page with a jog pad, folded or not."""

import os

import numpy as np
import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt                                    # noqa: E402
from PySide6.QtGui import QColor, QImage                         # noqa: E402
from PySide6.QtTest import QTest                                 # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget              # noqa: E402

from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages.calibration_camera import (             # noqa: E402
    CameraCalibrationSheet)
from micropick.gui.pages.calibration_pipette import (            # noqa: E402
    PipetteCalibration)
from micropick.gui.pages.manual import ManualPage                # noqa: E402
from micropick.gui.pages.picking import PickingPage              # noqa: E402
from micropick.gui.pages.position_sheet import PositionSheet     # noqa: E402
from micropick.gui.pages.well_center import WellCenterPage       # noqa: E402
from micropick.gui.session import Session                        # noqa: E402
from micropick.gui.theme import COLORS                           # noqa: E402
from micropick.gui.widgets.camera_view import CameraView         # noqa: E402
from micropick.gui.widgets.jog_panel import JogPanel             # noqa: E402

GREEN = "#12ab34"


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


@pytest.fixture
def shown(app, session):
    """A page with a picture and a panel, on screen."""
    host = QWidget()
    view = CameraView(host)
    panel = JogPanel(session, shortcut_host=host, parent=host)
    panel.show_position_on(view)
    host.show()
    host.activateWindow()
    app.processEvents()
    yield host, view, panel
    host.hide()
    host.deleteLater()


def _pixels(view) -> np.ndarray:
    image = view.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
    return np.frombuffer(image.constBits(), np.uint8).reshape(
        image.height(), image.bytesPerLine() // 4, 4).copy()


def _where(pixels, color: str) -> tuple:
    """Rows and columns of `color`; RGB32 is B, G, R in memory."""
    c = QColor(color)
    return np.nonzero((pixels[:, :, :3] == (c.blue(), c.green(), c.red()))
                      .all(axis=2))


def test_the_chip_is_painted_beside_x_y_z_in_its_color(app):
    view = CameraView()
    view.resize(800, 600)
    view.set_position(["X 1.00  Y 2.00  Z 3.00"])
    view.show()
    app.processEvents()
    without = _pixels(view)
    assert _where(without, GREEN)[0].size == 0
    owner = object()
    view.set_step_chip(owner, "Step 1 mm", GREEN)
    app.processEvents()
    pixels = _pixels(view)
    rows, columns = _where(pixels, GREEN)
    assert rows.size
    # The position's box stands as it did; the chip is on its row, right of it.
    _changed_rows, changed_columns = np.nonzero((pixels != without).any(axis=2))
    assert changed_columns.min() == columns.min() > 100
    assert rows.min() < 40
    view.set_step_chip(owner)
    app.processEvents()
    assert _where(_pixels(view), GREEN)[0].size == 0
    view.hide()
    view.deleteLater()


def test_a_pad_that_hides_leaves_the_other_pads_chip(app):
    """Two pads on one view take turns, and either may hide first."""
    view = CameraView()
    first, second = object(), object()
    view.set_step_chip(first, "Step 1 mm", GREEN)
    view.set_step_chip(second, "Step 0.05 mm", GREEN)
    view.set_step_chip(first)
    assert view.step_chip == ("Step 0.05 mm", GREEN)
    view.set_step_chip(second)
    assert view.step_chip is None
    view.deleteLater()


def test_the_chip_says_the_pads_step_and_its_color(app, shown):
    _host, view, panel = shown
    assert view.step_chip == ("Step 1 mm", COLORS["accent"])
    panel.pad.set_step(5, emit=True)
    assert view.step_chip == ("Step 5 mm", COLORS["warn"])
    panel.pad.set_step(10, emit=True)
    assert view.step_chip == ("Step 10 mm", COLORS["danger-text"])


def test_the_chip_follows_the_steps_chosen_on_the_pad(app, shown):
    _host, view, panel = shown
    panel.pad.bigger.click()
    assert panel.pad.step > 1
    assert view.step_chip[0] == f"Step {panel.pad.step:g} mm"


def test_the_chip_follows_the_step_keys(app, shown):
    _host, view, panel = shown
    panel._command("step_up")
    assert panel.pad.step > 1
    assert view.step_chip[0] == f"Step {panel.pad.step:g} mm"


def test_the_chip_stays_while_the_pad_is_folded(app, shown):
    _host, view, panel = shown
    panel.pad.set_folded(True)
    app.processEvents()
    assert view.step_chip == ("Step 1 mm", COLORS["accent"])
    panel.set_step(5)                                  # a key, while folded
    assert view.step_chip == ("Step 5 mm", COLORS["warn"])


def test_the_chip_goes_with_the_pad(app, shown):
    _host, view, panel = shown
    panel.hide()
    app.processEvents()
    assert view.step_chip is None
    panel.show()
    app.processEvents()
    assert view.step_chip == ("Step 1 mm", COLORS["accent"])


def test_two_pads_on_one_view_take_turns(app, session):
    """The Tip step's jog and touch-up panels, in the order it swaps them:
    the page itself sets who is shown on every refresh."""
    host = QWidget()
    view = CameraView(host)
    jog = JogPanel(session, shortcut_host=host, parent=host)
    touch = JogPanel(session, shortcut_host=host, parent=host)
    jog.show_position_on(view)
    touch.show_position_on(view)
    touch.hide()
    host.show()
    app.processEvents()
    assert view.step_chip == ("Step 1 mm", COLORS["accent"])
    touch.setVisible(True)
    jog.setVisible(False)
    touch.set_step(0.05)
    app.processEvents()
    assert view.step_chip == ("Step 0.05 mm", COLORS["accent"])
    touch.setVisible(False)
    jog.setVisible(True)
    app.processEvents()
    assert view.step_chip == ("Step 1 mm", COLORS["accent"])
    host.hide()
    host.deleteLater()


def _make(page_type, session):
    """The widget to show and the page in it: a sheet opens over a holder."""
    if page_type in (CameraCalibrationSheet, PositionSheet):
        host = QWidget()
        host.resize(1200, 800)
        sheet = page_type(session, host)
        return host, sheet
    page = page_type(session)
    return page, page


@pytest.mark.parametrize("page_type", [ManualPage, PickingPage,
                                       PipetteCalibration, PositionSheet,
                                       CameraCalibrationSheet])
def test_every_page_with_a_picture_has_the_chip(app, session, page_type):
    host, page = _make(page_type, session)
    host.show()
    page.show()
    app.processEvents()
    assert page.view.step_chip == ("Step 1 mm", COLORS["accent"])
    page.jog.pad.set_folded(True)
    page.jog.set_step(5)
    app.processEvents()
    assert page.view.step_chip == ("Step 5 mm", COLORS["warn"])
    host.hide()
    host.deleteLater()


def test_well_center_has_no_picture_and_shows_the_chip_by_the_tip(
        app, session):
    page = WellCenterPage(session)
    page.show()
    app.processEvents()
    assert page.step_chip.text() == "Step 1 mm"
    assert COLORS["accent"] in page.step_chip.styleSheet()
    page.pad.set_folded(True)
    page.jog.set_step(10)
    app.processEvents()
    assert page.step_chip.text() == "Step 10 mm"
    assert COLORS["danger-text"] in page.step_chip.styleSheet()
    page.hide()
    page.deleteLater()
