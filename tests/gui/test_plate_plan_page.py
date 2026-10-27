"""The Plan step (REDESIGN §6.3): the plate from the dropdown, the wells
chosen on the grid, cuboids per well and fill order, Use this plan, and
templates - Save as for a new plan, Save changes for an edited one."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt                                    # noqa: E402
from PySide6.QtTest import QTest                                 # noqa: E402
from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick import paths                                      # noqa: E402
from micropick.config.labware import resolve_definition          # noqa: E402
from micropick.core.routine import (Destination, PlanPreset,     # noqa: E402
                                    Routine)
from micropick.gui import tasks                                  # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages.routine import RoutinePage              # noqa: E402
from micropick.gui.session import Session                        # noqa: E402

PLATE = "corning_96_wellplate_360ul_flat"
OTHER = "nest_96_wellplate_100ul_pcr_full_skirt"


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
    yield p
    p.deleteLater()


def _plan_done(session):
    return tasks.step_state(session, "auto", "plan").done


def test_the_plate_on_the_deck_is_chosen_and_nothing_is_planned(page):
    assert page.dropdown.slot == 5
    assert page.plan() == {}
    assert page.summary.text() == "No wells chosen yet."
    assert not page.use_button.isEnabled()
    assert not page.save_as_button.isEnabled()


def test_use_this_plan_makes_the_plan_ready(page, session):
    page.plate.set_selection({"A1", "A2", "B1"})
    page.per_well.stepUp()
    page.order_tiles["by_row"].click()
    assert page.summary.text() == "3 wells × 2 cuboids"
    assert not _plan_done(session)

    page.use_button.click()
    routine = session.routine
    assert routine.plan == {"A1": 2, "A2": 2, "B1": 2}
    assert routine.strategy == "by_row"
    assert routine.destination.slot == 5
    assert _plan_done(session)
    assert not page.ready.isHidden()
    assert not page.use_button.isEnabled()

    page.plate.set_selection({"A1"})                  # changed after use
    assert page.use_button.isEnabled()
    assert page.ready.isHidden()
    assert session.routine is None                     # Plan not done
    assert not _plan_done(session)


def test_a_change_during_a_picking_run_keeps_the_plan_in_use(page, session):
    page.plate.set_selection({"A1", "A2"})
    page.use_button.click()
    routine = session.routine
    lease = session.robot_lease.claim("picking run", run=True)
    try:
        page.plate.set_selection({"A1"})
        assert session.routine is routine
    finally:
        lease.release()


def test_another_plate_reloaded_keeps_the_plan_in_use(page, session):
    page.plate.set_selection({"A1", "A2"})
    page.use_button.click()
    routine = session.routine
    session.labware_changed.emit(session)            # the page reloads plates
    assert session.routine is routine


def test_the_grid_undoes_and_redoes_the_wells_chosen(page):
    page.plate.set_selection({"A1"})
    page.plate.set_selection({"A1", "B1"})
    page.tools.undo.click()
    assert page.plan() == {"A1": 1}
    page.tools.redo.click()
    assert page.plan() == {"A1": 1, "B1": 1}
    page.tools.select_all.click()
    assert len(page.plan()) == 96


def test_ctrl_z_undoes_while_the_page_shows(app, page):
    page.show()
    app.processEvents()
    page.plate.set_selection({"A1"})
    QTest.keyClick(page.plate, Qt.Key.Key_Z,
                   Qt.KeyboardModifier.ControlModifier)
    assert page.plan() == {}
    page.hide()


def test_save_as_template_names_it_inline_then_save_changes(page, session):
    page.plate.set_selection({"A1", "A2"})
    page.save_as_button.click()
    assert not page.name_row.isHidden()
    page.name_field.setText("Row A")
    page.save_name_button.click()
    assert [t.name for t in session.plate_templates()] == ["Row A"]
    assert page.save_as_button.isHidden()             # the template is open
    assert page.save_changes_button.isHidden()        # nothing changed yet

    page.plate.set_selection({"A1", "A2", "A3"})
    assert not page.save_changes_button.isHidden()
    assert page.save_changes_button.text() == 'Save changes to "Row A"'
    page.save_changes_button.click()
    listed = session.plate_templates()
    assert len(listed) == 1
    assert PlanPreset.load(listed[0].path).plan == {"A1": 1, "A2": 1,
                                                    "A3": 1}
    assert page.save_changes_button.isHidden()


def test_a_name_in_use_is_refused_inline(page, session):
    page.plate.set_selection({"A1"})
    session.save_plate_template(
        PlanPreset.from_plan(page.destination, {"B1": 1}), "Row A")
    page.save_as_button.click()
    page.name_field.setText("Row A")
    page.save_name_button.click()
    assert "already" in page.template_note.text()
    assert not page.name_row.isHidden()
    assert len(session.plate_templates()) == 1


def test_a_template_from_the_list_is_put_on_the_plate(page, session):
    session.save_plate_template(
        PlanPreset.from_plan(Destination.from_labware(PLATE, 5),
                             {"A1": 2, "B1": 2, "C1": 3}, strategy="by_row"),
        "Mixed")
    page.template_tile.click()
    assert page.template_list.count() == 1
    assert "3 wells · saved" in page.template_list.item(0).text()
    page._template_clicked(page.template_list.item(0))
    assert page.plan() == {"A1": 2, "B1": 2, "C1": 3}
    assert page.per_well.value() == 2
    assert page.order_tiles["by_row"].isChecked()
    assert page.summary.text() == "3 wells, 7 cuboids"
    page.per_well.stepUp()                          # one number for all
    assert page.plan() == {"A1": 3, "B1": 3, "C1": 3}
    assert page.save_changes_button.text() == 'Save changes to "Mixed"'


def test_a_template_for_a_plate_not_on_the_deck_says_so(page, tmp_path):
    path = tmp_path / "other.json"
    PlanPreset(OTHER, {"A1": 1}, name="Other").save(path)
    page.open_template(path)
    assert "No plate on the deck" in page.message.text()
    assert page.template is None


def test_the_plan_in_use_is_shown_again_on_a_new_page(session):
    routine = Routine(Destination.from_labware(PLATE, 5), {"D4": 1},
                      name="plate", strategy="by_column",
                      path=paths.outputs_dir() / "plate.json")
    session.set_routine(routine)
    page = RoutinePage(session)
    assert page.plan() == {"D4": 1}
    assert not page.ready.isHidden()
    page.deleteLater()


def test_with_no_plate_on_the_deck_it_says_back_to_deck(app, tmp_path,
                                                        monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    s.load_profile("mock")
    s.probe_robot()
    s.new_run()
    page = RoutinePage(s)
    assert not page.no_plate.isHidden()
    assert page.destination is None
    assert not page.use_button.isEnabled()
    page.deleteLater()
    s.shutdown()


def test_new_plan_closes_the_template_and_clears_its_wells(page, session):
    session.save_plate_template(
        PlanPreset.from_plan(Destination.from_labware(PLATE, 5), {"A1": 1}),
        "One")
    page.template_tile.click()
    page._template_clicked(page.template_list.item(0))
    page.new_tile.click()
    assert page.template is None
    assert page.plan() == {}
    assert not page.save_as_button.isHidden()
    page.tools.undo.click()                           # the wells come back
    assert page.plan() == {"A1": 1}
