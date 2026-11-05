"""Manual pick's Run in two columns (#100): nothing done on the page moves
the line between them, and no card leaves its column."""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (QApplication, QPushButton,        # noqa: E402
                               QScrollArea)

from micropick.config.labware import (LabwareDefinition,         # noqa: E402
                                      resolve_definition)
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages.manual import FROM, ManualPage          # noqa: E402
from micropick.gui.session import Session                        # noqa: E402
from micropick.gui.theme import SPACING                          # noqa: E402
from micropick.gui.widgets.card_columns import CardColumns       # noqa: E402

# The picture takes four thirds of the height, the panel what is left, less
# a scroll bar as wide as the platform draws it: 1920 x 860 is a full HD
# window. It is the size that moved: there the two columns' minimums fit the
# panel, so the box layout gave each what it asked for. On 1560 x 680 they
# do not.
SIZES = [(1920, 860), (1560, 680)]


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
    return page.findChild(CardColumns)


def _where(panel):
    """Each card's left edge and width."""
    return [(card.x(), card.width()) for card in panel._cards]


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
        assert page.dispense_button.text() == "Go to A7 and dispense"

    def long_named_point():
        session.add_memo_point(120.0, 90.0, 70.0)
        session.save_memo_point("M1", "the point over the far corner")
        page.saved_table.selectRow(0)

    yield "the plate", lambda: session.load_labware(plate, 5)
    yield "the tip", lambda: (session.load_labware(rack, 10),
                              session.pick_up_tip(10, "A1"))
    yield "liquid in the tip", lambda: session.aspirate(20.0, 50.0)
    yield "Go to A7 and dispense", well_a7
    yield "Part", lambda: page.amount.buttons[0].click()
    yield "All", lambda: page.amount.buttons[1].click()
    yield "At a point", lambda: page.target.buttons[1].click()
    yield "Here", lambda: page.target.buttons[2].click()
    yield "Dispense's Change opened", lambda: page.dispense_change.setChecked(
        True)
    yield "a long speed", lambda: page.dispense_rate.setValue(999.9)
    yield "Dispense's Change closed", lambda: page.dispense_change.setChecked(
        False)
    yield "Change opened", lambda: page.change_button.setChecked(True)
    for name in ("In a well", "At a point", "Here", "By hand"):
        yield name, lambda i=FROM.index(name): page.source.buttons[i].click()
    yield "Above the bottom", lambda: page.height_mode.buttons[1].click()
    yield "Change closed", lambda: page.change_button.setChecked(False)
    yield "Sizes opened", lambda: page.sizes.toggle.setChecked(True)
    yield "a saved point", long_named_point
    yield "Memo folded", lambda: page.memo_section.set_collapsed(True)
    yield "Saved folded", lambda: page.saved_section.set_collapsed(True)
    yield "Memo opened", lambda: page.memo_section.set_collapsed(False)
    yield "Saved opened", lambda: page.saved_section.set_collapsed(False)
    yield "a long state", lambda: page.state.setText("word " * 60)
    yield "the pad's keys", lambda: page.jog.pad.keys.click()
    yield "the pad folded", lambda: page.jog.pad.set_folded(True)


@pytest.mark.parametrize("size", SIZES)
def test_no_action_changes_the_columns(app, page, session, size):
    panel = _shown(app, page, size)
    assert panel.two_columns
    first = _where(panel)
    assert {width for _x, width in first} == {
        (panel.width() - SPACING) // 2}
    assert len({x for x, _width in first}) == 2          # both columns used
    for name, action in _actions(page, session):
        action()
        _settle(app, page)
        assert _where(panel) == first, name


def test_the_scroll_bar_does_not_narrow_the_columns(app, page):
    panel = _shown(app, page, (1920, 860))
    area = next(a for a in page.findChildren(QScrollArea)
                if a.widget() is panel)
    first = _where(panel)
    assert not area.verticalScrollBar().isVisible()
    page.change_button.setChecked(True)
    page.dispense_change.setChecked(True)
    _settle(app, page)
    assert area.verticalScrollBar().isVisible()
    assert _where(panel) == first


def test_one_column_fills_the_panel_through_the_same_actions(app, page,
                                                             session):
    panel = _shown(app, page, (1600, 900))
    assert not panel.two_columns
    width = panel.width()
    for name, action in _actions(page, session):
        action()
        _settle(app, page)
        assert _where(panel) == [(0, width)] * len(panel._cards), name


def test_no_button_is_cut_on_a_full_hd_window(app, page, session):
    panel = _shown(app, page, (1920, 860))
    for _name, action in _actions(page, session):
        action()
        _settle(app, page)
        cut = [button.text() for button in panel.findChildren(QPushButton)
               if button.isVisible() and button.text()
               and button.width() < button.sizeHint().width()]
        assert cut == []
