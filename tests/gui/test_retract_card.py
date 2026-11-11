"""Manual pick Run's Retract card (#136): at the foot of the work column,
outside its scroll, in both layouts; the top bar's Retract by the same path;
and R, its default key, shown on the button as bound."""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QKeySequence, QShortcut                # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea          # noqa: E402

from micropick.config.labware import LabwareDefinition           # noqa: E402
from micropick.gui import keys                                   # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages.manual import ManualPage                # noqa: E402
from micropick.gui.session import Session                        # noqa: E402
from micropick.gui.shell import MainWindow                       # noqa: E402
from micropick.gui.widgets.jog_panel import JogPanel             # noqa: E402
from micropick.gui.widgets.work_columns import (COLUMN,          # noqa: E402
                                                ONE_COLUMN)

WIDE, NARROW = (1920, 860), (1400, 860)


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


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    w = MainWindow(Options(mock=True))
    yield w
    w.session.shutdown()
    w.deleteLater()


def _settle(app, window, timeout_s=5.0):
    end = time.monotonic() + timeout_s
    while time.monotonic() < end and (
            window._robot_busy()
            or any(p.job_pending for p in window.findChildren(JogPanel))):
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def _on_manual(app, window):
    """Manual pick's Run on screen."""
    window.session.probe_robot()
    window.session.new_run()
    _settle(app, window)
    rack = LabwareDefinition(load_name="opentrons_96_tiprack_300ul",
                             namespace="opentrons", version=1,
                             display_name="tips", ordering=[["A1"]], data={},
                             source="shared")
    window.session.load_labware(rack, 10)
    window.session.pick_up_tip(10, "A1")
    window.session.motion.retract()
    window.resize(*WIDE)
    window.show()
    window.open_task_step("manual", "work")
    _settle(app, window)
    return window.current_page()


def _shown(app, page, size):
    page.resize(*size)
    page.show()
    for _ in range(6):
        app.processEvents()


def _rises(window, monkeypatch):
    calls = []
    motion = window.session.motion
    rise = motion.rise

    def counted(**kwargs):
        calls.append(kwargs)
        return rise(**kwargs)

    monkeypatch.setattr(motion, "rise", counted)
    return calls


def _place(page):
    return page.retract_card.mapTo(page, page.retract_card.rect().topLeft())


@pytest.mark.parametrize("size, width", [(WIDE, COLUMN), (NARROW, ONE_COLUMN)])
def test_the_card_is_at_the_foot_of_the_work_column_outside_its_scroll(
        app, page, size, width):
    _shown(app, page, size)
    card = page.retract_card
    assert card.isVisible()
    assert card.width() == width
    assert not any(a.isAncestorOf(card)
                   for a in page.findChildren(QScrollArea))
    # The page's bottom margin under it, as Points stands in its column.
    assert card.mapTo(page, card.rect().bottomLeft()).y() == size[1] - 16 - 1 - 1
    assert _place(page).x() == page.in_place_card.mapTo(
        page, page.in_place_card.rect().topLeft()).x()


@pytest.mark.parametrize("size", [WIDE, NARROW])
def test_the_card_stands_still_while_the_cards_fold(
        app, page, stands_still, size):
    _shown(app, page, size)
    place = _place(page)
    stands_still(page.retract_card,
                 lambda: page.work_cards.open(page.dispense_card))
    assert _place(page) == place
    stands_still(page.retract_card,
                 lambda: page.work_cards.open(page.aspirate_card))
    assert _place(page) == place


def test_the_card_stands_still_while_the_column_scrolls(app, page):
    _shown(app, page, (1920, 500))
    area = next(a for a in page.findChildren(QScrollArea)
                if a.isAncestorOf(page.in_place_card))
    place = _place(page)
    bar = area.verticalScrollBar()
    bar.setValue(bar.maximum())
    app.processEvents()
    assert bar.value() > 0
    assert _place(page) == place


def test_its_button_rises_by_the_top_bars_path(app, window, monkeypatch,
                                               until, robot_free):
    page = _on_manual(app, window)
    calls = _rises(window, monkeypatch)
    assert page.retract_button.isEnabledTo(page)
    page.retract_button.clicked.emit()     # the step is dimmed: no calibration
    assert window.top.retract.text() == "Retracting…"
    until(lambda: window.top.retract.text() == "Retract", what="Retract again")
    robot_free(window)
    assert len(calls) == 1


def test_gray_with_the_top_bars_reason_while_busy(app, window):
    page = _on_manual(app, window)
    lease = window.session.robot_lease.claim("a picking run", run=True)
    app.processEvents()
    window._refresh()
    assert not page.retract_button.isEnabledTo(page)
    assert page.retract_button.toolTip() == window.top.retract.toolTip()
    lease.release()
    app.processEvents()
    window._refresh()
    assert page.retract_button.isEnabledTo(page)


def test_gray_with_the_top_bars_reason_while_locked(app, window):
    page = _on_manual(app, window)
    window._set_locked(True)
    assert not page.retract_button.isEnabledTo(page)
    assert page.retract_button.toolTip() == "Robot locked."
    assert window.top.retract.toolTip() == "Robot locked."
    window._set_locked(False)
    assert page.retract_button.isEnabledTo(page)


def test_gray_with_no_robot(themed, app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    s.load_profile("mock")
    p = ManualPage(s)
    assert not p.retract_button.isEnabled()
    assert p.retract_button.toolTip() == "Robot not connected."
    p.deleteLater()
    s.shutdown()


def test_r_is_the_default_and_on_the_button(page):
    assert keys.key_text({}, "retract") == "R"
    assert page.retract_button.keycap == "R"
    assert keys.problems({}) == []


def test_rebound_the_button_shows_the_new_key(page, session):
    session.set_settings(session.settings.model_copy(
        update={"keys": {"retract": "F6"}}))
    assert page.retract_button.keycap == "F6"


def test_cleared_it_has_no_key_and_the_button_shows_none(page, session):
    session.set_settings(session.settings.model_copy(
        update={"keys": {"retract": ""}}))
    assert keys.sequences(session.settings.keys, "retract") == []
    assert page.retract_button.keycap == ""


def _key(window):
    bound = [s for s in window.findChildren(QShortcut)
             if s.parent() is window and s.key() == QKeySequence("R")]
    assert len(bound) == 1
    return bound[0]


def test_r_retracts_on_any_screen(app, window, monkeypatch):
    window.session.probe_robot()
    window.session.new_run()
    _settle(app, window)
    calls = _rises(window, monkeypatch)
    _key(window).activated.emit()
    _settle(app, window)
    assert len(calls) == 1


def test_r_does_nothing_while_a_field_has_the_keyboard(
        app, window, monkeypatch):
    page = _on_manual(app, window)
    calls = _rises(window, monkeypatch)
    field = page.in_place_amount
    monkeypatch.setattr(QApplication, "focusWidget", lambda: field)
    _key(window).activated.emit()
    _settle(app, window)
    assert calls == []
