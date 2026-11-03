"""Liquid's Plan step (LIQUID.md §3) in the main window under --mock: a plan
built with the mouse, read as steps, and run through Next."""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt         # noqa: E402
from PySide6.QtGui import QDragMoveEvent, QDropEvent              # noqa: E402
from PySide6.QtTest import QTest                                 # noqa: E402
from PySide6.QtWidgets import (QAbstractButton, QApplication,    # noqa: E402
                               QLabel)

from micropick.config.labware import resolve_definition          # noqa: E402
from micropick.core.liquid import Location, Program              # noqa: E402
from micropick.core.liquid_plan import LiquidPlan                # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages import liquid_plan as page_module       # noqa: E402
from micropick.gui.shell import MainWindow                       # noqa: E402
from micropick.gui.widgets.step_list import MIME                 # noqa: E402

PLATE = "corning_96_wellplate_360ul_flat"
SIX = "corning_6_wellplate_16.8ml_flat"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    w = MainWindow(Options(mock=True))
    session = w.session
    session.probe_robot()
    session.new_run()
    session.load_labware(resolve_definition(PLATE), 5)
    session.load_labware(resolve_definition(SIX), 2)
    _until(app, lambda: not session.robot_lease.busy)
    session.tip = session.tip.__class__(attached=True)
    monkeypatch.setattr(w.pages["liquid"], "_confirm_start", lambda: True)
    w.resize(1400, 900)
    w.show()
    w.open_task("liquid")
    w.show_step("liquid_plan")
    app.processEvents()
    yield w
    run = w.pages["liquid"]
    session.request_stop()
    _until(app, lambda: not run._running and not run.jog.busy)
    session.shutdown()
    w.deleteLater()


@pytest.fixture
def page(window):
    return window.pages["liquid_plan"]


def _until(app, condition, timeout_s=10.0):
    end = time.monotonic() + timeout_s
    while not condition() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    return condition()


def _click(app, page, *wells):
    """Each well clicked on the plate shown, with the mouse."""
    for well in wells:
        app.processEvents()
        QTest.mouseClick(page.plate, Qt.MouseButton.LeftButton,
                         pos=page.plate.well_rect(well).center().toPoint())


def _choose_plate(page, slot):
    page.dropdown.slot_chosen.emit(slot)


def _set_trash(app, page, slot=5, well="H12"):
    page.trash_row.click()
    _choose_plate(page, slot)
    _click(app, page, well)


def _run_to_the_end(app, window):
    assert window.footer.next.isEnabled(), window.footer.sentence.text()
    window.footer.next.click()
    run = window.pages["liquid"]
    assert window.current_page() is run
    assert _until(app, run._startable), run.run_status()
    run.start_button.click()
    assert _until(app, lambda: run._ended is not None and not run.jog.busy)
    assert run._ended == "done", run._outcome


def _taken_and_put(window):
    calls = window.session.robot.calls
    return ([c[2] for c in calls if c[0] == "aspirate"],
            [c[2] for c in calls if c[0] == "dispense"])


def test_a_wells_to_wells_step_is_built_by_mouse_and_runs(app, window, page):
    _set_trash(app, page)
    page.first_step_button.click()
    assert page.section == "source"
    _click(app, page, "A1", "A2")
    page.next_button.click()
    assert page.section == "dest"
    _click(app, page, "B1", "B2")
    page.next_button.click()                       # Done
    _run_to_the_end(app, window)
    taken, put = _taken_and_put(window)
    assert taken[-2:] == ["A1", "A2"]
    assert put[-2:] == ["B1", "B2"]


def test_a_wells_to_trash_step_is_built_by_mouse_and_runs(app, window, page):
    _set_trash(app, page)
    page.first_step_button.click()
    _click(app, page, "C1", "C2", "C3")
    page.next_button.click()
    page.dest_kind.buttons[1].click()              # Trash
    assert page.into_trash.isVisibleTo(page)
    page.next_button.click()
    assert page.step_list.cards()[0].title.text() == "Empty 3 wells"
    _run_to_the_end(app, window)
    taken, put = _taken_and_put(window)
    assert [w for w in taken if w != "H12"] == ["C1", "C2", "C3"]
    assert put[-1] == "H12"                        # emptied there, not over C
    assert not {"C1", "C2", "C3"} & set(put)


def _warned(page):
    return page.rule.property("warn") is True


def test_from_is_asked_before_to_and_the_rule_says_what_would_work(app, page):
    page.first_step_button.click()
    assert page.question_caption.text() == "STEP 1 · TAKE FROM"
    assert page.question_title.text() == "Where is the liquid taken from?"
    assert page.next_button.text() == "Next: Put into"
    assert page.rule.text() == ("Click a well. Click several wells to take "
                                "from each of them.")
    _click(app, page, "A1", "A2", "A3")
    page.next_button.click()
    assert page.question_caption.text() == "STEP 1 · PUT INTO"
    assert page.next_button.text() == "Done"
    assert page.rule.text() == "Click the wells it goes into."
    _click(app, page, "B1", "B2")
    assert page.rule.text() == "ⓘ Take from 1 well, or from as many wells as it goes into (2)."
    assert _warned(page)
    _click(app, page, "B3")
    assert page.rule.text() == ("Well by well: first to first, second to "
                                "second, 50 µl each.")
    assert not _warned(page)


def test_a_card_with_a_problem_says_not_finished_and_choose(app, page):
    page.first_step_button.click()
    card = page.step_list.cards()[0]
    rows = page.card.rows
    assert [(r.label.text(), r.value.text()) for r in rows] == [
        ("Take from", "choose"), ("Put into", "choose")]
    assert rows[0].value.property("missing") is True
    assert page.card.amount.text() == "50 µl per well · 10 µl/s"
    assert [c.text() for c in card.chips] == ["choose", "choose"]
    assert not card.warning.isVisibleTo(page)      # open: the rows say it
    _click(app, page, "A1")
    assert rows[0].value.text() == "Slot 5 · A1"
    assert rows[0].value.property("missing") is False
    page.next_button.click()
    page.next_button.click()                       # Done, with nothing chosen
    assert card.warning.isVisibleTo(page)
    assert card.warning.text() == "Not finished"
    assert card.chips[1].text() == "choose"


def test_next_is_gray_with_one_sentence_until_nothing_is_wrong(app, window,
                                                               page):
    def said():
        return window.footer.sentence.text()

    next_ = window.footer.next
    assert not next_.isEnabled()
    assert said() == "Add a step to the plan."
    page.first_step_button.click()
    assert said() == "Set the trash to run this plan."
    assert page.trash_action.text() == "Set"
    assert page.trash_row.property("missing") is True
    _set_trash(app, page)
    assert page.trash_action.text() == "Change"
    assert page.trash_text.text() == "Trash · Slot 5 · H12 · used by every step"
    assert page.section == "source"                # back where it was
    assert said() == "Finish step 1 to run this plan."
    assert not next_.isEnabled()
    _click(app, page, "A1")
    page.next_button.click()
    _click(app, page, "B1")
    assert next_.isEnabled()
    assert said() == ""
    assert window.footer.tag.isHidden()
    # A second step, not finished: the sentence names it.
    page.add_step("move_liquid")
    assert said() == "Finish step 2 to run this plan."
    assert not next_.isEnabled()
    # The trash taken away: said first, at once.
    page.trash_row.click()
    page.clear_button.click()
    assert page.plan.trash is None
    assert said() == "Set the trash to run this plan."


def _three_steps(app, page):
    """Steps 1, 2 and 3, on rows A, B and C of the plate into row H."""
    for row in "ABC":
        page.add_step("move_liquid")
        _click(app, page, f"{row}1")
        page.next_button.click()
        _click(app, page, f"{row}2")
        page.next_button.click()
    app.processEvents()
    return [s.id for s in page.plan.steps]


def _drop(app, page, step_id, card):
    """`step_id` dragged onto the top of `card`, through the list's own
    drag events with real mime data."""
    steps = page.step_list
    data = QMimeData()
    data.setData(MIME, step_id.encode())
    at = QPointF(steps.mapFrom(card, QPoint(card.width() // 2, 2)))
    options = Qt.DropAction.MoveAction
    args = (options, data, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier)
    steps.dragMoveEvent(QDragMoveEvent(at.toPoint(), *args))
    steps.dropEvent(QDropEvent(at, *args))
    app.processEvents()


def _legend(page):
    return [page.legend.itemAt(i).widget().text().split("</span> ")[1]
            for i in range(page.legend.count())]


def test_a_card_dragged_reorders_the_plan_and_ctrl_z_undoes_it(app, window,
                                                               page):
    ids = _three_steps(app, page)
    colors = [s.color for s in page.plan.steps]
    assert len(set(colors)) == 3
    assert page.left.currentWidget() is page.overview
    assert page.overview_title.text() == "3 steps, in this order"
    first = _legend(page)
    assert first[0] == "Step 1: Slot 5 · A1 → Slot 5 · A2"

    _drop(app, page, ids[2], page.step_list.cards()[0])
    assert [s.id for s in page.plan.steps] == [ids[2], ids[0], ids[1]]
    cards = page.step_list.cards()
    assert [c.step_id for c in cards] == [ids[2], ids[0], ids[1]]
    assert [c.number.text() for c in cards] == ["1", "2", "3"]
    assert [s.color for s in page.plan.steps] == [colors[2], colors[0],
                                                  colors[1]]
    assert _legend(page)[0] == "Step 1: Slot 5 · C1 → Slot 5 · C2"
    # What a run does follows at once.
    assert window.session.liquid_plan.steps[0].id == ids[2]

    window.activateWindow()
    QTest.qWaitForWindowActive(window)
    QTest.keyClick(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    app.processEvents()
    assert [s.id for s in page.plan.steps] == ids
    assert _legend(page) == first
    page.redo()
    assert [s.id for s in page.plan.steps] == [ids[2], ids[0], ids[1]]


def test_alt_down_moves_a_card_and_ctrl_z_restores_it(app, window, page):
    ids = _three_steps(app, page)
    QTest.keyClick(page.step_list.cards()[0], Qt.Key.Key_Down,
                   Qt.KeyboardModifier.AltModifier)
    assert [s.id for s in page.plan.steps] == [ids[1], ids[0], ids[2]]
    page.undo()
    assert [s.id for s in page.plan.steps] == ids


def test_a_look_at_the_robot_leaves_the_cards_alone(app, window, page):
    """The monitor's tick, in the middle of a drag perhaps."""
    _three_steps(app, page)
    chips = list(page.step_list.cards()[0].chips)
    window.session.robot_state_changed.emit(window.session.run_state)
    assert page.step_list.cards()[0].chips == chips


def test_the_kind_already_chosen_keeps_the_wells(app, page):
    page.first_step_button.click()
    _click(app, page, "A1")
    page.next_button.click()
    _click(app, page, "B1")
    page.dest_kind.buttons[0].click()              # Wells, as it was
    assert page.plan.steps[0].dest.wells == ["B1"]
    page.dest_kind.buttons[1].click()
    page.trash_kind.buttons[1].click()             # Trash, as it was
    assert page.plan.steps[0].dest.kind == "trash"
    page.trash_kind.buttons[0].click()
    assert page.left.currentWidget() is page.question
    page.undo()
    page.undo()
    assert page.plan.steps[0].dest.wells == ["B1"]


def test_an_open_card_stays_open_when_it_moves(app, page):
    ids = _three_steps(app, page)
    page.step_list.set_open(ids[0])
    assert page.section == "source"
    _drop(app, page, ids[2], page.step_list.cards()[0])
    assert page.open_id == ids[0]
    assert page.step_list.card(ids[0]).is_open
    assert page.question_caption.text() == "STEP 2 · TAKE FROM"


def test_wells_chosen_are_one_undo_each(app, page):
    page.first_step_button.click()
    _click(app, page, "A1")
    _click(app, page, "A2")
    assert page.plan.steps[0].source.wells == ["A1", "A2"]
    page.undo_button.click()
    assert page.plan.steps[0].source.wells == ["A1"]
    assert page.plate.selection == {"A1"}
    page.redo_button.click()
    assert page.plate.selection == {"A1", "A2"}
    page.clear_button.click()
    assert page.plan.steps[0].source.wells == []
    page.select_all_button.click()
    assert len(page.plan.steps[0].source.wells) == 96
    assert page.plan.steps[0].source.wells[:2] == ["A1", "A2"]    # by row


def test_undoing_a_step_added_closes_its_card(app, page):
    page.first_step_button.click()
    page.undo()
    assert page.plan.steps == []
    assert page.open_id == ""
    assert page.section is None
    assert page.left.currentWidget() is page.empty_box
    assert not page.card.isVisibleTo(page)
    page.add_step("wait")                          # the one card, not deleted
    assert page.card.isVisibleTo(page)


def test_other_steps_wells_are_dim_and_a_step_sees_its_own_source(app, page):
    ids = _three_steps(app, page)
    page.step_list.set_open(ids[1])
    assert set(page.plate._dim_fills) == {"A1", "A2", "B2", "C1", "C2"}
    assert page.plate.selection == {"B1"}


def test_a_plate_chosen_on_the_mini_deck_is_the_steps(app, page):
    page.first_step_button.click()
    _click(app, page, "A1")
    _choose_plate(page, 2)
    assert page.plan.steps[0].source.wells == []
    assert len(page.plate.wells) == 6
    assert page.facts.text() == "from drawing"
    _click(app, page, "B2")
    assert page.plan.steps[0].source.slot == "2"
    assert page.plan.steps[0].source.load_name == SIX
    assert page.card.rows[0].value.text() == "Slot 2 · B2"


def test_an_unmeasured_plate_warns_with_measure_now_and_use_anyway(app, window,
                                                                   page):
    page.first_step_button.click()
    assert page.not_measured.isVisibleTo(page)
    assert page.dropdown.tiles[5].note == "Not measured"
    page.use_anyway_button.click()
    assert not page.not_measured.isVisibleTo(page)
    _choose_plate(page, 2)                         # another plate warns
    assert page.not_measured.isVisibleTo(page)
    window.session.skip_plate(2, SIX)              # skipped: no warning
    assert not page.not_measured.isVisibleTo(page)
    page.measure_button.click()
    assert window.step == "well_center"


def test_add_a_step_lists_every_kind_with_its_help_line(app, page):
    items = [a.defaultWidget() for a in page.add_menu.actions()]
    assert [i.findChildren(QLabel)[0].text() for i in items] == [
        "Move liquid", "Mix", "Blow out", "Move the tip", "Wait",
        "Wait for me"]
    assert all(i.findChildren(QLabel)[1].text() for i in items)
    page.add_menu.actions()[4].trigger()           # Wait
    assert page.plan.steps[0].kind == "wait"
    assert page.open_id == page.plan.steps[0].id
    assert page.left.currentWidget() is page.overview
    assert page.next_button.text() == "Done"


def test_a_mix_and_a_move_of_the_tip_choose_their_wells(app, page):
    page.add_step("mix")
    assert page.card.rows[0].label.text() == "Wells"
    _click(app, page, "A1", "A2")
    assert page.plan.steps[0].wells.wells == ["A1", "A2"]
    page.add_step("move_tip")
    _click(app, page, "B1", "B2")                  # one place: replaced
    assert page.plan.steps[1].place.wells == ["B2"]
    assert not page.select_all_button.isEnabled()


def test_the_empty_plan_shows_only_what_starts_one(app, page):
    assert page.left.currentWidget() is page.empty_box
    assert not page.step_list.isVisibleTo(page)
    assert page.add_button.isVisibleTo(page)
    page.first_step_button.click()
    assert page.step_list.isVisibleTo(page)
    page.new_plan_button.click()
    assert page.plan.steps == []
    assert page.left.currentWidget() is page.empty_box
    page.undo()                                    # a new plan costs nothing
    assert len(page.plan.steps) == 1


def test_a_card_and_the_legend_leave_no_old_text_on_screen(app, page):
    """Taken out and only deleted later, a label stays drawn until then."""
    page.first_step_button.click()
    _click(app, page, "A1")
    page.step_list.set_open("")
    app.processEvents()
    card = page.step_list.cards()[0]
    shown = [w.text() for w in card.header.findChildren(QLabel)
             if w.isVisibleTo(page) and w.objectName().startswith("chip")]
    assert shown == ["Slot 5 · A1", "choose"]
    lines = [w.text() for w in page.overview.findChildren(QLabel)
             if w.isVisibleTo(page) and "Step 1" in w.text()]
    assert len(lines) == 1


def test_duplicate_and_delete_from_the_menu(app, page):
    page.first_step_button.click()
    _click(app, page, "A1")
    page.next_button.click()
    page.next_button.click()
    first = page.plan.steps[0]
    page.step_list.cards()[0].action("Duplicate").trigger()
    copy = page.plan.steps[1]
    assert copy.source == first.source
    assert (copy.id != first.id) and (copy.color != first.color)
    page.step_list.cards()[1].action("Delete").trigger()
    assert [s.id for s in page.plan.steps] == [first.id]


def test_the_plan_cannot_change_during_a_run(app, window, page):
    _set_trash(app, page)
    ids = _three_steps(app, page)
    held = window.session.robot_lease.claim("liquid run", run=True)
    app.processEvents()
    assert page.locked_note.isVisibleTo(page)
    assert not page.add_button.isEnabled()
    assert not page.step_list.cards()[0].handle.isVisibleTo(page)
    assert not page.step_list.cards()[0].action("Delete").isEnabled()
    page.undo()
    assert [s.id for s in page.plan.steps] == ids
    held.release()
    app.processEvents()
    assert page.add_button.isEnabled()
    assert not page.locked_note.isVisibleTo(page)


def test_the_steps_are_for_the_plates_on_the_deck_that_day(app, window, page):
    _set_trash(app, page)
    page.first_step_button.click()
    page.plan.tip_ul = 200.0
    page._save_current()
    again = page_module.LiquidPlanPage(window.session)
    assert again.plan.steps == []
    assert again.plan.trash == page.plan.trash
    assert again.plan.tip_ul == 200.0
    again.deleteLater()


def test_the_trash_of_a_file_from_before_plans_is_kept(app, window, page):
    trash = Location(kind="well", slot="5", load_name=PLATE, well="H1")
    Program(trash=trash).save(page_module.plan_file())
    again = page_module.LiquidPlanPage(window.session)
    assert again.plan == LiquidPlan(trash=trash)
    again.deleteLater()


def test_the_trashs_plate_is_not_where_the_next_step_opens(app, page):
    _set_trash(app, page, slot=2, well="A1")
    assert page.plan.trash.slot == "2"
    page.first_step_button.click()
    assert len(page.plate.wells) == 96             # slot 5


def test_a_plate_taken_off_the_deck_makes_the_step_not_finished(app, window,
                                                                page):
    _set_trash(app, page, slot=2, well="A1")
    page.first_step_button.click()
    _click(app, page, "A1")
    page.next_button.click()
    _click(app, page, "B1")
    page.next_button.click()
    assert window.footer.next.isEnabled()
    window.session.run_state.labware.pop("5")
    window.session.labware_changed.emit(window.session.run_state)
    app.processEvents()
    assert not window.footer.next.isEnabled()
    assert page.step_list.cards()[0].warning.isVisibleTo(page)
    assert window.footer.sentence.text() == "Finish step 1 to run this plan."


def test_no_internal_word_is_on_the_screen(app, page):
    """LIQUID.md §4, on every text of the page, in every state of a step."""
    import re
    banned = re.compile(r"this_well|\bhere\b|measured_bottom|location|group"
                        r"|aspirat|dispens", re.IGNORECASE)
    _set_trash(app, page)
    page.first_step_button.click()
    _click(app, page, "A1")
    page.next_button.click()
    page.dest_kind.buttons[1].click()
    page.next_button.click()
    widgets = (page.findChildren(QLabel) + page.findChildren(QAbstractButton)
               + page.add_menu.findChildren(QLabel))
    said = [w.text() for w in widgets] + [w.toolTip() for w in widgets]
    assert not [text for text in said if banned.search(text)]
