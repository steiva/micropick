"""Liquid's Plan step on a mock session: the plate and its well center,
groups from the selection, steps added, edited and tried, the run order,
what to run, templates, and Use this plan."""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.config.labware import resolve_definition          # noqa: E402
from micropick.core.liquid import Dispense, Location, Program    # noqa: E402
from micropick.gui import tasks                                  # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages import liquid_plan as page_module       # noqa: E402
from micropick.gui.session import Session                        # noqa: E402

PLATE = "corning_96_wellplate_360ul_flat"
RESERVOIR = "nest_1_reservoir_195ml"
TRASH = Location(kind="well", slot="5", load_name=PLATE, well="H12")


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
    p = page_module.LiquidPlanPage(session)
    _settle(app, p)
    yield p
    p.deleteLater()


def _settle(app, page, timeout_s=10.0):
    end = time.monotonic() + timeout_s
    while page.jog.busy and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def _group(page, wells):
    page.plate.set_selection(set(wells))
    page.make_group()
    return page.program.groups[-1]


def test_a_group_is_made_from_the_selection(page):
    assert page.slot == "5"
    page.plate.set_selection({"A1", "A2", "A3"})
    assert page.selected.text() == "3 wells selected"
    assert page.make_group_button.isEnabled()
    group = _group(page, {"A1", "A2", "A3"})
    assert (group.slot, group.load_name) == ("5", PLATE)
    assert group.wells == ["A1", "A2", "A3"]
    # A well is in one group of its plate at most.
    _group(page, {"A3", "B1"})
    assert page.program.groups[0].wells == ["A1", "A2"]
    assert sorted(page.program.groups[1].wells) == ["A3", "B1"]
    # The grid is colored by group.
    assert page.plate._fills["A1"].name() == group.color
    assert len(page.group_cards) == 2


def test_an_unmeasured_plate_warns_with_measure_now_and_use_anyway(page,
                                                                  session):
    asked = []
    session.page_requested.connect(asked.append)
    assert page.not_measured.isVisibleTo(page)
    assert page.dropdown.tiles[5].note == "Not measured"
    page.measure_button.click()
    assert asked == ["well_center"]
    page.use_anyway_button.click()
    assert not page.not_measured.isVisibleTo(page)
    # Skipped on the Well center step: no warning either.
    other = page_module.LiquidPlanPage(session)
    session.skip_plate(5, PLATE)
    assert not other.not_measured.isVisibleTo(other)
    assert other.dropdown.tiles[5].note == "Not measured"   # the drawing
    other.deleteLater()


def test_steps_are_added_edited_deleted_and_saved(page):
    _group(page, {"A1"})
    page.add_step(0, "aspirate")
    page.add_step(0, "dispense")
    page.add_step(0, "wait")                              # under More…
    assert [s.action for s in page.program.groups[0].steps] == [
        "aspirate", "dispense", "wait"]
    page.edit_step(0, 0)
    assert page.editor.isVisibleTo(page)
    page.editor.volume.setValue(25.0)
    assert page.program.groups[0].steps[0].volume_ul == 25.0
    page.edit_step(0, 2)
    page.delete_step_button.click()
    assert [s.action for s in page.program.groups[0].steps] == [
        "aspirate", "dispense"]
    page._save_current()
    saved = Program.load(page_module.programs_dir() / page_module.CURRENT_FILE)
    assert saved == page.program


def test_take_liquid_from_is_where_a_new_aspirate_takes_from(page, session):
    session.load_labware(resolve_definition(RESERVOIR), 2)
    page._deck_changed()
    _group(page, {"A1"})
    page.source_where.set_location(Location(kind="well", slot="2",
                                            load_name=RESERVOIR, well="A1"))
    page.add_step(0, "aspirate")
    where = page.program.groups[0].steps[-1].location
    assert (where.kind, where.slot, where.well) == ("well", "2", "A1")
    page.source.buttons[2].click()                        # By hand
    assert page.by_hand_note.isVisibleTo(page)
    page.add_step(0, "aspirate")
    steps = page.program.groups[0].steps
    assert [s.action for s in steps[-2:]] == ["pause", "aspirate"]
    assert steps[-1].location.kind == "here"


def test_a_group_dragged_runs_at_its_new_place(page):
    first = _group(page, {"A1"}).name
    second = _group(page, {"B1"}).name
    page.move_group(1, 0)
    assert [g.name for g in page.program.groups] == [second, first]
    assert [c.index for c in page.group_cards] == [0, 1]
    page.delete_group(0)
    assert [g.name for g in page.program.groups] == [first]


def test_use_this_plan_hands_the_plan_and_which_groups_run(page, session):
    _group(page, {"A1", "A2"})
    page.add_step(0, "aspirate")
    page.add_step(0, "dispense")
    _group(page, {"B1"})
    page.add_step(1, "aspirate")
    page.add_step(1, "dispense")
    assert not page.use_button.isEnabled()                # no trash yet
    assert "trash" in page.problem.text()
    page.trash.set_location(TRASH)
    page._trash_edited()
    assert page.use_button.isEnabled()
    assert page.summary.text() == "2 groups · 3 wells"
    page.scope.buttons[1].click()
    page.only_choice.setCurrentIndex(1)
    assert page.groups_to_run() == [1]
    assert page.summary.text() == "1 group · 1 well"
    assert not tasks.step_state(session, "liquid", "liquid_plan").done
    page.use()
    assert tasks.step_state(session, "liquid", "liquid_plan").done
    assert session.liquid_plan == page.program
    assert session.liquid_plan is not page.program        # a copy
    assert session.liquid_groups == [1]
    assert page.ready.isVisibleTo(page)
    assert not page.use_button.isEnabled()
    # Changed after: Use this plan again.
    page.add_step(1, "wait")
    assert page.use_button.isEnabled()
    assert not page.ready.isVisibleTo(page)


def test_templates_start_save_as_and_save_changes(page, session):
    _group(page, {"A1"})
    page.add_step(0, "aspirate")
    page._ask_name()
    page.name_field.setText("Wash")
    page.save_as()
    assert page.template.name == "Wash"
    assert not page.save_changes_button.isVisibleTo(page)
    page.add_step(0, "dispense")
    assert page.save_changes_button.isVisibleTo(page)
    assert page.save_changes_button.text() == 'Save changes to "Wash"'
    page.save_changes()
    assert [s.action for s in Program.load(page.template.path)
            .groups[0].steps] == ["aspirate", "dispense"]

    page.new_tile.click()                                 # New plan
    assert page.program.groups == []
    assert page.template is None
    page.template_tile.click()
    assert page.template_list.count() == 1
    page.open_template(page.template_list.item(0).data(
        page_module.Qt.ItemDataRole.UserRole))
    assert page.template.name == "Wash"
    assert [s.action for s in page.program.groups[0].steps] == [
        "aspirate", "dispense"]


def test_try_once_does_the_step_for_the_selected_well(app, page, session):
    session.tip = session.tip.__class__(attached=True)
    _group(page, {"A1", "A2"})
    page.add_step(0, "aspirate")
    page.plate.set_selection({"A2"})
    page._refresh()
    assert page.try_button.isEnabled()
    page.try_button.click()
    _settle(app, page)
    aspirates = [c for c in session.robot.calls if c[0] == "aspirate"]
    assert aspirates and aspirates[-1][2] == "A2"
    page.delete_step_button.click()
    assert page.program.groups[0].steps == []


def test_try_once_says_what_is_missing(page, session):
    session.tip = session.tip.__class__(attached=False)
    _group(page, {"A1"})
    page.add_step(0, "dispense")
    page.program.groups[0].steps[0] = Dispense(
        location=Location(kind="point", point="nowhere"))
    problems = page.try_problems()
    assert any("no tip" in p for p in problems)
    assert any("nowhere" in p for p in problems)
    assert not page.try_button.isEnabled()


def test_the_next_start_keeps_the_trash_and_not_the_groups(page, session):
    _group(page, {"A1"})
    page.trash.set_location(TRASH)
    page._trash_edited()
    page._save_current()
    again = page_module.LiquidPlanPage(session)
    assert again.program.groups == []
    assert again.program.trash == page.program.trash
    again.deleteLater()


def test_a_well_location_on_the_deck_is_not_marked_missing(page, session):
    session.load_labware(resolve_definition(RESERVOIR), 2)
    page._deck_changed()
    _group(page, {"A1"})
    page.add_step(0, "dispense")
    step = Dispense(location=Location(kind="well", slot="2",
                                      load_name=RESERVOIR, well="A1"))
    page.program.groups[0].steps[0] = step
    page.edit_step(0, 0)                    # added open: Edit folds it
    assert not page.editor.isVisibleTo(page)
    page.edit_step(0, 0)
    where = page.editor.where
    assert "not on the deck" not in where.labware.currentText()
    assert where.location() == step.location
