"""Measuring a plate's well center on Manual control and on Liquid handling:
the same measurement, saved for the plate whichever page took it."""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox          # noqa: E402

from micropick.config.labware import resolve_definition          # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages import liquid as liquid_page            # noqa: E402
from micropick.gui.pages.manual import ManualPage                # noqa: E402
from micropick.gui.session import Session                        # noqa: E402

PLATE = "corning_96_wellplate_360ul_flat"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def session(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    s = Session(Options(mock=True))
    s.load_profile("mock")
    s.probe_robot()
    s.new_run()
    s.load_labware(resolve_definition(PLATE), 5)
    yield s
    s.shutdown()


def _settle(app, panel, timeout_s=10.0):
    end = time.monotonic() + timeout_s
    while panel.busy and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def _jog(app, session, panel, dx, dy, dz):
    """The operator's jog onto the real center, as the robot sees it."""
    robot = session.robot
    for axis, d in zip("xyz", (dx, dy, dz)):
        robot.move_relative(axis, d, verbose=False)
    panel.refresh_position()
    _settle(app, panel)


def _manual(app, session):
    page = ManualPage(session)
    _settle(app, page.jog)
    page.plate_choice.setCurrentIndex(page.plate_choice.findText(
        next(page.plate_choice.itemText(i)
             for i in range(page.plate_choice.count())
             if "5" in page.plate_choice.itemText(i))))
    page.well_choice.setCurrentText("B2")
    return page


def _liquid(app, session):
    page = liquid_page.LiquidHandlingPage(session)
    _settle(app, page.jog)
    page.plate.set_selection({"B2"})
    page._refresh()
    return page


def _measure(app, session, page, jog):
    page._go_to_well_top()
    _settle(app, page.jog)
    # The measurement in hand, said under the well center line.
    assert "At the top of B2" in page.centre_state.text()
    assert page.set_centre_button.isEnabled()
    at = tuple(page.jog.pose)
    _jog(app, session, page.jog, *jog)
    page._set_well_centre()
    return at


@pytest.mark.parametrize("make", [_manual, _liquid],
                         ids=["manual control", "liquid handling"])
def test_set_well_center_saves_the_jog_from_the_robots_well_top(
        app, session, make):
    page = make(app, session)
    _measure(app, session, page, (0.4, -0.3, -1.5))
    centre = session.profile.deck.well_centre(5, PLATE)
    assert centre is not None and centre.well == "B2"
    assert centre.offset == pytest.approx([0.4, -0.3, -1.5], abs=0.05)
    assert centre.depth_mm == pytest.approx(10.668)   # the definition's
    page.deleteLater()


@pytest.mark.parametrize("make", [_manual, _liquid],
                         ids=["manual control", "liquid handling"])
def test_measuring_again_keeps_the_depth_set_for_the_plate(app, session,
                                                           make):
    page = make(app, session)
    _measure(app, session, page, (0.4, -0.3, -1.5))
    session.set_well_depth(5, PLATE, 9.5)
    _measure(app, session, page, (0.1, 0.2, -1.0))
    centre = session.profile.deck.well_centre(5, PLATE)
    assert centre.offset == pytest.approx([0.1, 0.2, -1.0], abs=0.05)
    assert centre.depth_mm == pytest.approx(9.5)
    page.deleteLater()


def test_a_center_measured_on_one_page_is_the_other_pages(app, session):
    manual = _manual(app, session)
    _measure(app, session, manual, (0.4, -0.3, -1.5))
    liquid = _liquid(app, session)
    plate = liquid._plates()["5"]
    assert plate.centre == pytest.approx((0.4, -0.3, -1.5), abs=0.05)
    manual.deleteLater()
    liquid.deleteLater()
