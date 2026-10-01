"""The Plate plan page: undo and redo, keys, presets, editing a plan that
already has progress, and the slot checked by itself."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.config.labware import resolve_definition          # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages.routine import RoutinePage              # noqa: E402
from micropick.gui.session import Session                        # noqa: E402

PLATE = "corning_96_wellplate_360ul_flat"


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


@pytest.fixture
def page(app, session):
    p = RoutinePage(session)
    p._slot_clicked("5")
    p._use_plate()
    yield p
    p.deleteLater()


def _set(page, wells, count):
    page.plate.set_selection(set(wells))
    page.per_well.setValue(count)
    page._apply_count()


def test_undo_and_redo_walk_the_plan_back_and_forth(page):
    _set(page, {"A1"}, 2)
    _set(page, {"B1", "B2"}, 3)
    assert page._plan == {"A1": 2, "B1": 3, "B2": 3}
    page._undo_plan()
    assert page._plan == {"A1": 2}
    page._undo_plan()
    assert page._plan == {}
    assert not page.undo_button.isEnabled()
    page._redo_plan()
    page._redo_plan()
    assert page._plan == {"A1": 2, "B1": 3, "B2": 3}
    page._clear_plan()
    page._undo_plan()
    assert page._plan == {"A1": 2, "B1": 3, "B2": 3}


def test_typing_a_number_into_one_selection_is_one_step(page):
    page.plate.set_selection({"C3"})
    for value in (1, 12):                      # "1", then "12"
        page.per_well.setValue(value)
    assert page._plan == {"C3": 12}
    page._undo_plan()
    assert page._plan == {}


def test_a_new_change_forgets_what_was_undone(page):
    _set(page, {"A1"}, 1)
    page._undo_plan()
    _set(page, {"A2"}, 1)
    assert not page.redo_button.isEnabled()


def test_choosing_the_plate_again_starts_a_new_history(page):
    _set(page, {"A1"}, 1)
    page._use_plate()
    assert page._plan == {} and not page.undo_button.isEnabled()


def test_ctrl_a_selects_all_and_delete_takes_the_selection_out(app, page):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    page.show()
    app.processEvents()
    _set(page, {"A1", "A2", "B1"}, 2)
    page.plate.set_selection({"A1", "B1"})
    QTest.keyClick(page.plate.viewport(), Qt.Key.Key_Delete)
    assert page._plan == {"A2": 2}
    QTest.keyClick(page.plate.viewport(), Qt.Key.Key_A,
                   Qt.KeyboardModifier.ControlModifier)
    assert len(page.plate.selection) == 96
    QTest.keyClick(page.plate.viewport(), Qt.Key.Key_Z,
                   Qt.KeyboardModifier.ControlModifier)
    assert page._plan == {"A1": 2, "A2": 2, "B1": 2}
    page.hide()


def test_a_preset_saved_from_one_plan_starts_the_next(app, page, tmp_path):
    from micropick.core.routine import PlanPreset
    _set(page, {"A1", "A2"}, 3)
    preset = PlanPreset.from_plan(page.destination, page._plan,
                                  strategy="by_row", name="row A")
    path = tmp_path / "row_a.json"
    preset.save(path)
    page._clear_plan()
    page._apply_preset(PlanPreset.load(path))
    assert page._plan == {"A1": 3, "A2": 3}
    assert page.routine is None                       # a plan, not under way
    assert page.strategy.currentText() == "by_row"
    assert page.create_button.isEnabled()


def test_a_preset_for_a_plate_not_on_the_deck_says_so(page):
    from micropick.core.routine import PlanPreset
    page._apply_preset(PlanPreset("nest_96_wellplate_100ul_pcr_full_skirt",
                                  {"A1": 1}))
    assert "No slot holds" in page.summary.toPlainText()
