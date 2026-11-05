"""Manual pick's Run in its frame (#113): the picture, a work column and a
Points column of 400 px, one column of 420 under about 1600 px of window.
Nothing done on the page moves a column, and no card leaves one."""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (QApplication, QPushButton,        # noqa: E402
                               QScrollArea, QTableWidget)

from micropick.config.labware import (LabwareDefinition,         # noqa: E402
                                      resolve_definition)
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages.manual import FROM, ManualPage          # noqa: E402
from micropick.gui.session import Session                        # noqa: E402
from micropick.gui.widgets.toast import show_toast               # noqa: E402
from micropick.gui.widgets.work_columns import WorkColumns       # noqa: E402

# 1920 x 860 is a full HD window; 1600 is where the two columns begin.
SIZES = [(1920, 860), (1600, 680)]
NARROW = (1400, 860)


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
def page(themed, session):
    p = ManualPage(session)
    yield p
    p.close()
    p.deleteLater()


def _settle(app, page):
    for _ in range(4):
        app.processEvents()
    end = time.monotonic() + 5
    while page.jog.busy and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def _shown(app, page, size):
    page.resize(*size)
    page.show()
    _settle(app, page)
    return page.findChild(WorkColumns)


def _cards(page):
    return [page.in_place_card, page.aspirate_card, page.dispense_card,
            page.points_card]


def _where(page):
    """The picture, and each card's column and width."""
    return (page.view.geometry(),
            [(card.mapTo(page, card.rect().topLeft()).x(), card.width())
             for card in _cards(page)])


def _actions(page, session):
    """Everything on the page that changes what a card shows."""
    plate = resolve_definition("corning_96_wellplate_360ul_flat")
    rack = LabwareDefinition(load_name="opentrons_96_tiprack_300ul",
                             namespace="opentrons", version=1,
                             display_name="tips", ordering=[["A1"]], data={},
                             source="shared")

    def well_a7():
        page.target.buttons[0].click()                   # In a well
        page.target_wells.well.setCurrentText("A7")
        assert page.dispense_button.text() == "Dispense into A7"

    def long_named_point():
        session.add_memo_point(120.0, 90.0, 70.0)
        session.save_memo_point("M1", "the point over the far corner")
        page.points_list.choose("the point over the far corner")

    yield "the plate", lambda: session.load_labware(plate, 5)
    yield "the tip", lambda: (session.load_labware(rack, 10),
                              session.pick_up_tip(10, "A1"))
    yield "liquid in the tip", lambda: session.aspirate(20.0, 50.0)
    yield "Dispense into A7", well_a7
    yield "Part", lambda: page.amount.buttons[0].click()
    yield "All", lambda: page.amount.buttons[1].click()
    yield "At a point", lambda: page.target.buttons[1].click()
    yield "Dispense's Change opened", lambda: page.dispense_change.setChecked(
        True)
    yield "a long speed", lambda: page.dispense_rate.setValue(999.9)
    yield "Dispense's Change closed", lambda: page.dispense_change.setChecked(
        False)
    yield "Change opened", lambda: page.change_button.setChecked(True)
    for name in ("In a well", "At a point", "By hand"):
        yield name, lambda i=FROM.index(name): page.source.buttons[i].click()
    yield "Above the bottom", lambda: page.height_mode.buttons[1].click()
    yield "Change closed", lambda: page.change_button.setChecked(False)
    yield "a saved point", long_named_point
    yield "a long status", lambda: page.view.set_status(["word " * 30])
    yield "the pad's keys", lambda: page.jog.pad.keys.click()
    yield "the pad folded", lambda: page.jog.pad.set_folded(True)


@pytest.mark.parametrize("size", SIZES)
def test_no_action_changes_the_columns(app, page, session, size):
    panel = _shown(app, page, size)
    assert panel.two_columns
    first = _where(page)
    assert {width for _x, width in first[1]} == {400}
    assert len({x for x, _width in first[1]}) == 2       # both columns used
    for name, action in _actions(page, session):
        action()
        _settle(app, page)
        assert _where(page) == first, name


def test_the_frame_at_full_hd(app, page):
    _shown(app, page, (1920, 860))
    points = page.points_card
    assert points.isVisible() and points.is_open()
    x = [card.mapTo(page, card.rect().topLeft()).x() for card in _cards(page)]
    assert x[0] == x[1] == x[2] < x[3]
    picture = page.view.mapTo(page, page.view.rect().topLeft())
    assert (picture.x(), picture.y()) == (20, 16)
    # A scroll column's viewport stands a pixel inside it.
    assert x[0] - picture.x() - page.view.width() - 1 == 16
    assert page.view.height() == 860 - 32
    # Points stands the column's height, the page's bottom margin under it.
    bottom = points.mapTo(page, points.rect().bottomLeft()).y()
    assert bottom == 860 - 16 - 1 - 1


def test_the_list_fills_the_points_column(app, page):
    _shown(app, page, (1920, 860))
    assert page.points_list.height() > 280
    page.resize(1400, 860)
    _settle(app, page)
    page.points_card.set_open(True)
    _settle(app, page)
    assert page.points_list.height() == 280


def test_the_scroll_bar_does_not_narrow_the_columns(app, page):
    _shown(app, page, (2560, 640))
    area = next(a for a in page.findChildren(QScrollArea)
                if a.widget() is page.aspirate_card.parentWidget())
    first = _where(page)
    assert not area.verticalScrollBar().isVisible()
    page.change_button.setChecked(True)
    page.dispense_change.setChecked(True)
    page.source.buttons[FROM.index("At a point")].click()
    _settle(app, page)
    assert area.verticalScrollBar().isVisible()
    assert _where(page) == first


def test_under_1600_px_one_column_of_420_points_folded_after_dispense(
        app, page):
    panel = _shown(app, page, NARROW)
    assert not panel.two_columns
    assert {width for _x, width in _where(page)[1]} == {420}
    assert len({x for x, _width in _where(page)[1]}) == 1
    assert page.points_card.y() > page.dispense_card.y()
    assert not page.points_card.is_open()
    assert page.undo_button.isVisible()                  # its header stays


def test_one_column_fills_the_panel_through_the_same_actions(app, page,
                                                             session):
    _shown(app, page, NARROW)
    first = _where(page)
    for name, action in _actions(page, session):
        action()
        _settle(app, page)
        assert _where(page) == first, name


def test_no_button_is_cut_on_a_full_hd_window(app, page, session):
    panel = _shown(app, page, (1920, 860))
    for _name, action in _actions(page, session):
        action()
        _settle(app, page)
        cut = [button.text() for button in panel.findChildren(QPushButton)
               if button.isVisible() and button.text()
               and button.width() < button.sizeHint().width()]
        assert cut == []


def test_look_at_detect_and_past_picks_are_on_the_picture(app, page):
    _shown(app, page, (1920, 860))
    for control in (page.look_at, page.cuboid_button,
                    page.past_picks_button):
        assert control.parentWidget() is page.view
    assert page.past_picks_button.text() == "Past picks"
    assert page.look_at.isVisible() and page.cuboid_button.isVisible()
    assert page.look_at.geometry().right() < page.cuboid_button.x()
    assert page.look_at.prefix.text() == "Look at"


def test_the_cards_hold_no_tools_and_no_sizes(app, page):
    _shown(app, page, (1920, 860))
    assert not hasattr(page, "sizes") and not hasattr(page, "state")
    assert page.findChildren(QTableWidget) == []


@pytest.mark.parametrize("size", [(1920, 860), NARROW])
def test_a_status_and_a_toast_move_nothing(app, page, stands_still, size):
    _shown(app, page, size)
    stands_still(page, lambda: page.view.set_status(["23 cuboids found"]))
    stands_still(page, lambda: page.view.set_status(["a line", "and another"]))
    stands_still(page, lambda: page.view.set_status([]))
    stands_still(page, lambda: show_toast(page, "Saved: M1"))
    stands_still(page, lambda: show_toast(page, "Not reachable.", refusal=True))


def test_the_empty_tip_warning_is_read_whole_with_a_well_chosen(
        app, page, session):
    """#114: the one summary that must not be cut, beside the gray button."""
    _shown(app, page, (1920, 860))
    session.load_labware(
        resolve_definition("corning_96_wellplate_360ul_flat"), 5)
    _settle(app, page)
    summary = page.dispense_card.summary
    assert summary.full == "The tip is empty. Aspirate first."
    assert summary.text() == summary.full
