"""A steady GUI (#106): the picture and the cards' widths never move. Only
what the operator clicks moves others, and only down the column it is in: a
section, or a choice showing other controls. A message moves nothing."""

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
from micropick.gui.widgets.fold_card import FoldCard             # noqa: E402
from micropick.gui.widgets.toast import toast_of                 # noqa: E402

PLATE = "corning_96_wellplate_360ul_flat"
RACK = "opentrons_96_tiprack_300ul"
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


def _frame(page):
    """What a choice never moves: the picture, and every card's left edge and
    width."""
    return (page.view.geometry(),
            {card: (card.x(), card.width())
             for card in page.findChildren(FoldCard) if card.isVisible()})


def _column_height(page):
    return page.aspirate_card.parentWidget().height()


def _stands_with_the_frame(manual, stands_still, control, index, card):
    """The frame stays, and the card's own top: only what is under the choice
    moves down its column."""
    _opened(QApplication.instance(), manual)
    before = _frame(manual)
    top = card.y()
    stands_still(manual.view, lambda: _choose(control, index))
    assert _frame(manual) == before
    assert card.y() == top


@pytest.mark.parametrize("chosen", FROM)
def test_manual_from_keeps_the_frame(manual, stands_still, chosen):
    _stands_with_the_frame(manual, stands_still, manual.source,
                           FROM.index(chosen), manual.aspirate_card)


@pytest.mark.parametrize("chosen", WHERE)
def test_manual_where_keeps_the_frame(manual, stands_still, chosen):
    manual.dispense_card.set_open(True)
    _stands_with_the_frame(manual, stands_still, manual.target,
                           WHERE.index(chosen), manual.dispense_card)


@pytest.mark.parametrize("chosen", FROM)
def test_manual_from_keeps_the_frame_with_the_lifting_open(
        manual, stands_still, chosen):
    manual.aspirate_retract.header.click()
    _stands_with_the_frame(manual, stands_still, manual.source,
                           FROM.index(chosen), manual.aspirate_card)


@pytest.mark.parametrize("chosen", HEIGHT)
def test_manual_height_keeps_the_frame(
        manual, stands_still, chosen):
    _stands_with_the_frame(manual, stands_still, manual.height_mode,
                           HEIGHT.index(chosen), manual.aspirate_card)


@pytest.mark.parametrize("chosen", AMOUNT)
def test_manual_part_or_all_moves_nothing(manual, stands_still, chosen):
    manual.dispense_card.set_open(True)
    stands_still(manual, lambda: _choose(manual.amount, AMOUNT.index(chosen)))


def test_manual_status_gaining_a_line_moves_nothing(manual, stands_still):
    stands_still(manual, lambda: manual.view.set_status(["One.", "Two."]))
    stands_still(manual, lambda: manual.view.set_status(["One."]))
    stands_still(manual, lambda: manual.view.set_status([]))


def test_a_choice_is_laid_out_before_anything_is_painted(app, manual):
    """Measured at once, or the column is painted once at the old height."""
    manual.dispense_card.set_open(True)
    manual.resize(FULL_HD[0], 400)
    _opened(app, manual)
    column = manual.dispense_card.parentWidget()
    _choose(manual.target, WHERE.index("In a well"))
    tall = column.minimumHeight()
    _choose(manual.target, WHERE.index("At a point"))
    short = column.minimumHeight()
    assert short < tall
    _opened(app, manual)
    assert column.minimumHeight() == short


def test_a_card_is_as_tall_as_what_its_choice_shows(app, manual):
    """No room kept for a choice not made."""
    def height_with(card, control, chosen):
        card.set_open(True)
        _choose(control, chosen)
        _opened(app, manual)
        return card.height()

    tall = (height_with(manual.aspirate_card, manual.source,
                        FROM.index("In a well")),
            height_with(manual.dispense_card, manual.target,
                        WHERE.index("In a well")))
    assert height_with(manual.aspirate_card, manual.source,
                       FROM.index("At a point")) < tall[0] - 100
    assert height_with(manual.dispense_card, manual.target,
                       WHERE.index("At a point")) < tall[1] - 100


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


def _tip_page(app, session):
    page = PipetteCalibration(session)
    page.resize(*FULL_HD)
    page.show()
    _opened(app, page)
    return page


def test_the_select_shows_which_slot_a_tip_box_is_in(app, themed, session):
    session.load_labware(resolve_definition(RACK), 10)
    session.load_labware(resolve_definition(RACK), 7)
    page = _tip_page(app, session)
    racks = page.racks
    # What a Select keeps for its text: the chevron and the padding out.
    room = racks.width() - 45
    assert racks.fontMetrics().horizontalAdvance(racks.itemText(1)) < room
    page.close()
    page.deleteLater()


def test_the_warning_with_no_tip_box_is_as_tall_as_its_words(
        app, themed, session):
    page = _tip_page(app, session)
    banner = page.no_rack
    assert banner.isVisibleTo(page)
    assert banner.height() < page.tip.rack_stack.height() / 2
    page.close()
    page.deleteLater()


def test_what_the_robot_says_leaves_the_calibrate_card_where_it_is(
        app, themed, session, stands_still):
    page = _tip_page(app, session)
    block = page.tip
    stands_still(page, lambda: block.rack_stack.setCurrentWidget(block.rack_view))
    stands_still(page, lambda: block.actions.setCurrentWidget(block.dropping))
    page.close()
    page.deleteLater()


def test_the_rack_folding_moves_only_what_is_under_it(
        app, themed, session):
    """A tip going on is the second thing a job's end moves (#121)."""
    session.load_labware(resolve_definition(RACK), 10)
    page = _tip_page(app, session)
    block = page.tip
    frame, top = page.view.geometry(), block.box.geometry().top()
    under = page.calibration_box.geometry().top()
    block.take_button.click()
    end = time.monotonic() + 5
    while not block.done and time.monotonic() < end:
        app.processEvents()
    _opened(app, page)
    assert block.done
    assert page.calibration_box.geometry().top() < under
    assert page.view.geometry() == frame
    assert block.box.geometry().top() == top
    assert block.box.width() == page.calibration_box.width() == 440
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
