"""A static GUI (#106): only what the operator folds or unfolds with a click
moves others. A choice or a message moves nothing else on the page."""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.config.labware import resolve_definition          # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages.calibration_pipette import (           # noqa: E402
    PipetteCalibration)
from micropick.gui.pages.dish_bottom import DishBottomPage       # noqa: E402
from micropick.gui.pages.home import HomePage                    # noqa: E402
from micropick.gui.pages.labware import LabwarePage              # noqa: E402
from micropick.gui.pages.liquid import LiquidHandlingPage        # noqa: E402
from micropick.gui.pages.liquid_plan import LiquidPlanPage       # noqa: E402
from micropick.gui.pages.manual import (AMOUNT, FROM, HEIGHT,    # noqa: E402
                                        WHERE, ManualPage)
from micropick.gui.pages.picking import PickingPage              # noqa: E402
from micropick.gui.pages.routine import RoutinePage              # noqa: E402
from micropick.gui.pages.well_center import WellCenterPage       # noqa: E402
from micropick.gui.session import Session                        # noqa: E402
from micropick.gui.widgets.toast import toast_of                 # noqa: E402

PLATE = "corning_96_wellplate_360ul_flat"
FULL_HD = (1920, 860)


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
    s.load_labware(resolve_definition(PLATE), 5)
    yield s
    s.shutdown()


def _choose(control, index):
    """As a click, gray or not: Dispense is gray until the tip holds
    something, and what it shows has to stand still all the same."""
    control.buttons[index].setChecked(True)
    control.changed.emit(index)


def _opened(app, page):
    """The pose read as it is shown, and what that lays out again."""
    jog = getattr(page, "jog", None)
    end = time.monotonic() + 5
    while jog is not None and jog.busy and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)
    end = time.monotonic() + 0.2
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


@pytest.fixture
def manual(app, themed, session):
    page = ManualPage(session)
    page.resize(*FULL_HD)
    page.show()
    _opened(app, page)
    yield page
    page.close()
    page.deleteLater()


@pytest.mark.parametrize("chosen", FROM)
def test_manual_from_moves_nothing(manual, stands_still, chosen):
    stands_still(manual, lambda: _choose(manual.source, FROM.index(chosen)))


@pytest.mark.parametrize("chosen", WHERE)
def test_manual_where_moves_nothing(manual, stands_still, chosen):
    stands_still(manual, lambda: _choose(manual.target, WHERE.index(chosen)))


@pytest.mark.parametrize("chosen", AMOUNT)
def test_manual_part_or_all_moves_nothing(manual, stands_still, chosen):
    stands_still(manual, lambda: _choose(manual.amount, AMOUNT.index(chosen)))


@pytest.mark.parametrize("chosen", HEIGHT)
def test_manual_height_moves_nothing_with_the_settings_open(
        manual, stands_still, chosen):
    manual.change_button.click()
    stands_still(manual,
                 lambda: _choose(manual.height_mode, HEIGHT.index(chosen)))


@pytest.mark.parametrize("chosen", FROM)
def test_manual_from_moves_nothing_with_the_settings_open(
        manual, stands_still, chosen):
    manual.change_button.click()
    stands_still(manual, lambda: _choose(manual.source, FROM.index(chosen)))


@pytest.mark.parametrize("make, name", [
    (LabwarePage, "problems"),
    (RoutinePage, "no_plate"),
    (RoutinePage, "choose_wells"),
    (RoutinePage, "save_changes_button"),
    (RoutinePage, "template_box"),
    (LiquidPlanPage, "locked_note"),
    (DishBottomPage, "last_card"),
    (WellCenterPage, "last_card"),
    (PickingPage, "again_line"),
    (HomePage, "error"),
    (HomePage, "run_note"),
    (HomePage, "waiting"),
    (LiquidHandlingPage, "counter"),
    (LiquidHandlingPage, "counter_note"),
])
def test_what_appears_on_a_page_has_its_room(app, themed, session, stands_still,
                                             make, name):
    page = make(session)
    page.resize(*FULL_HD)
    page.show()
    _opened(app, page)
    widget = getattr(page, name)
    stands_still(page, lambda: widget.setVisible(not widget.isVisibleTo(page)))
    page.close()
    page.deleteLater()


def test_a_banner_of_a_liquid_run_keeps_the_plates_where_they_are(
        app, themed, session, stands_still):
    page = LiquidHandlingPage(session)
    page.resize(*FULL_HD)
    page.show()
    _opened(app, page)
    for banner in (page.strip, page.after_banner, page.end_banner):
        stands_still(page, lambda: page.banners.setCurrentWidget(banner))
    page.close()
    page.deleteLater()


def test_a_tip_taken_leaves_the_calibrate_card_where_it_is(
        app, themed, session, stands_still):
    page = PipetteCalibration(session)
    page.resize(*FULL_HD)
    page.show()
    _opened(app, page)
    block = page.tip
    stands_still(page, lambda: block.tip_stack.setCurrentWidget(block.dropping))
    stands_still(page, lambda: block.tip_stack.setCurrentWidget(block.no_rack))
    page.close()
    page.deleteLater()


def test_a_refusal_on_the_deck_step_is_a_toast(app, themed, session):
    page = LabwarePage(session)
    page.show()
    lease = session.robot_lease.claim("a test")
    assert page._run(None, "loading") is False
    assert toast_of(page).text() == session.robot_lease.refusal
    lease.release()
    page.close()
    page.deleteLater()
