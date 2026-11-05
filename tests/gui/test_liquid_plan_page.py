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
from PySide6.QtWidgets import (QAbstractButton,                  # noqa: E402
                               QAbstractSpinBox, QApplication,
                               QComboBox, QLabel, QLineEdit)

from micropick.config.labware import resolve_definition          # noqa: E402
from micropick.core.liquid import Location, Program              # noqa: E402
from micropick.core.liquid_plan import (BlowOutStep,  # noqa: E402
                                        LiquidPlan, Place)
from micropick.gui import session as session_module              # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages import liquid_plan as page_module       # noqa: E402
from micropick.gui.shell import MainWindow                       # noqa: E402
from micropick.gui.theme import COLORS                           # noqa: E402
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
    page.trash_done_button.click()


def _run_to_the_end(app, window):
    assert window.footer.next.isEnabled(), window.footer.sentence.text()
    window.footer.next.click()
    run = window.pages["liquid"]
    assert window.current_page() is run
    run.how.buttons[1].click()                     # all in a row
    assert _until(app, run._startable), run.run_status()
    run.start_button.click()
    assert _until(app, lambda: run._ended is not None and not run.jog.busy)
    assert run._ended == "done", run._outcome


def _done(page):
    """Next through what is left of the open card, then Done."""
    while page.next_button.text() != "Done":
        page.next_button.click()
    page.next_button.click()


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
    _done(page)
    _run_to_the_end(app, window)
    taken, put = _taken_and_put(window)
    assert taken[-2:] == ["A1", "A2"]
    assert put[-2:] == ["B1", "B2"]


def test_a_wells_to_trash_step_is_built_by_mouse_and_runs(app, window, page):
    _set_trash(app, page)
    page.first_step_button.click()
    _click(app, page, "C1", "C2", "C3")
    page.next_button.click()
    page.dest_kind.buttons[2].click()              # Trash
    assert page.into_trash.isVisibleTo(page)
    _done(page)
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
    assert page.next_button.text() == "Next: Amount"
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
        ("Take from", "choose"), ("Put into", "choose"),
        ("Amount", "50 µl per well · 10 µl/s"), ("Extras", "none")]
    assert rows[0].value.property("missing") is True
    assert [c.text() for c in card.chips] == ["choose", "choose"]
    assert not card.warning.isVisibleTo(page)      # open: the rows say it
    _click(app, page, "A1")
    assert rows[0].value.text() == "Slot 5 · A1"
    assert rows[0].value.property("missing") is False
    _done(page)                                    # with nothing chosen
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
        _done(page)
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
    page.dest_kind.buttons[2].click()
    page.dest_kind.buttons[2].click()              # Trash, as it was
    assert page.plan.steps[0].dest.kind == "trash"
    page.dest_kind.buttons[0].click()
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
    assert page.left.currentWidget() is page.answer
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
    _done(page)
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
    page.dest_kind.buttons[2].click()
    _done(page)
    widgets = (page.findChildren(QLabel) + page.findChildren(QAbstractButton)
               + page.add_menu.findChildren(QLabel))
    said = [w.text() for w in widgets] + [w.toolTip() for w in widgets]
    assert not [text for text in said if banned.search(text)]


# -- Amount, Extras and the other kinds (#73) ---------------------------------


def _to_section(page, name):
    while page.section != name:
        page.next_button.click()


def _type(box, value):
    box.selectAll()
    QTest.keyClicks(box, str(value))


def _step(page, index=0):
    return page.plan.steps[index]


def _fields_in_the_card(page):
    return (page.card.findChildren(QAbstractSpinBox)
            + page.card.findChildren(QLineEdit))


def test_the_drawing_and_its_sentence_follow_the_fields_live(app, page):
    page.first_step_button.click()
    _click(app, page, "A1")
    page.next_button.click()
    _click(app, page, "B1")
    page.next_button.click()
    assert page.question_caption.isHidden() or page.section == "amount"
    assert page.answer_caption.text() == "STEP 1 · AMOUNT AND HEIGHT"
    form = page.amount_form
    take, put = form.take_at, form.put_at
    assert take.sentence.text() == "2 mm above the bottom"
    assert put.sentence.text() == "1 mm below the top"
    before = take.drawing.tip_y()
    _type(form.take_at.offset, "4")
    assert take.sentence.text() == "4 mm above the bottom"
    assert take.drawing.tip_y() < before           # higher up the picture
    _type(form.take_at.offset, "-1")
    assert take.sentence.text() == "1 mm below the bottom"
    form.put_at.ref.buttons[1].click()            # Middle
    _type(form.put_at.offset, "0")
    assert put.sentence.text() == "in the middle"
    assert "+" not in take.sentence.text() + put.sentence.text()
    assert "−" not in take.sentence.text() + put.sentence.text()


def test_the_note_says_where_the_bottom_comes_from(app, window, page):
    page.first_step_button.click()
    _click(app, page, "A1")
    page.next_button.click()
    _click(app, page, "B1")
    page.next_button.click()
    assert page.amount_form.take_at.note.text() == "Bottom from the plate drawing"
    assert page.amount_form.put_at.note.text() == "Top of the well"
    session = window.session
    session.set_well_center("5", PLATE, (0.1, 0.2, 40.0), "A1", 10.0)
    page._show()
    assert page.amount_form.take_at.note.text() == "Bottom measured in Well center"


def test_take_fields_only_for_wells_and_put_fields_only_for_wells(app, page):
    page.first_step_button.click()
    _click(app, page, "A1")
    page.next_button.click()
    page.dest_kind.buttons[2].click()              # Trash
    page.next_button.click()
    form = page.amount_form
    assert form.take_at.isVisibleTo(page)
    assert not form.put_at.isVisibleTo(page)
    assert form.no_height.isVisibleTo(page)


def test_a_wash_is_built_on_the_page_and_compiles_to_the_bench_wash(app,
                                                                    window,
                                                                    page):
    """Notebook 03's wash: take beside the cuboid, slowly, empty fast; refill
    from the reservoir, checking the first well."""
    from micropick.core.liquid_plan import compile_step
    _set_trash(app, page)
    page.first_step_button.click()
    _click(app, page, "A1", "A2", "A3")
    page.next_button.click()
    page.dest_kind.buttons[2].click()              # Trash
    page.next_button.click()
    form = page.amount_form
    _type(form.volume, "100")
    _type(form.put_flow, "200")
    _type(form.take_at.offset, "2.5")
    _type(form.take_at.aside_x, "-2")
    page.next_button.click()
    assert page.section == "extras"
    extras = page.extras_form
    extras.slow_lift.click()
    assert page.card.rows[3].value.text() == "lift slowly"
    page.next_button.click()                       # Done

    page.add_step("move_liquid")
    _click(app, page, "H1")
    page.next_button.click()
    _click(app, page, "A1", "A2", "A3")
    _to_section(page, "extras")
    extras.pause_after_first.click()
    assert page.card.rows[3].value.text() == "pause after the first well"
    page.next_button.click()

    plates = window.session.liquid_plates()
    first = compile_step(page.plan, 0, plates).groups[0]
    take, put = first.steps
    assert first.wells == ["A1", "A2", "A3"]
    assert take.volume_ul == 100 and take.flow_rate == 10
    assert take.location.offset == [-2, 0, 2.5]
    assert take.slow_rise
    assert put.auto_empty and put.flow_rate == 200
    second = compile_step(page.plan, 1, plates).groups[0]
    assert second.pause_after_first


def test_amount_and_extras_are_asked_on_the_left_not_in_the_card(app, page):
    page.first_step_button.click()
    _click(app, page, "A1")
    page.next_button.click()
    _click(app, page, "B1", "B2")
    page.next_button.click()
    assert page.amount_form.volume.isVisibleTo(page)
    assert not _fields_in_the_card(page)
    _to_section(page, "extras")
    assert page.answer_title.text() == "Anything else in this step?"
    assert page.extras_form.slow_lift.isVisibleTo(page)
    assert not _fields_in_the_card(page)


def test_amount_and_extras_sit_in_the_middle_of_the_room(app, page):
    page.first_step_button.click()
    _click(app, page, "A1")
    page.next_button.click()
    _click(app, page, "B1")
    page.next_button.click()
    for form in (page.amount_form, page.extras_form):
        app.processEvents()
        room = form.parentWidget()
        assert abs(form.geometry().center().y() - room.rect().center().y()) <= 2
        assert abs(form.geometry().center().x() - room.rect().center().x()) <= 2
        page.next_button.click()
    # Extras is about half as wide as the room.
    assert page.extras_form.width() == page_module.EXTRAS_WIDTH


def test_extras_follow_the_ends_of_the_step(app, page):
    page.first_step_button.click()
    _click(app, page, "A1")
    page.next_button.click()
    page.dest_kind.buttons[2].click()              # Trash
    _to_section(page, "extras")
    form = page.extras_form
    assert not form.mix_box.isVisibleTo(page)      # the destination is no well
    assert form.lift_box.isVisibleTo(page)
    assert form.empty_at_end.isChecked()


def test_mixing_after_opens_its_times_and_volume(app, page):
    page.first_step_button.click()
    _click(app, page, "A1")
    page.next_button.click()
    _click(app, page, "B1")
    _to_section(page, "extras")
    form = page.extras_form
    assert not form.mix_fields.isVisibleTo(page)
    form.mix_after.click()
    assert form.mix_fields.isVisibleTo(page)
    _type(form.mix_cycles, "5")
    _type(form.mix_ul, "30")
    assert form.mix_help.text() == "Pipettes up and down 5 times in each well."
    assert (_step(page).mix_cycles, _step(page).mix_ul) == (5, 30)


def test_digits_typed_into_a_field_are_one_undo(app, page):
    page.first_step_button.click()
    _click(app, page, "A1")
    page.next_button.click()
    _click(app, page, "B1")
    page.next_button.click()
    box = page.amount_form.volume
    _type(box, "120")
    assert _step(page).volume_ul == 120
    assert box.value() == 120
    page.undo()
    assert _step(page).volume_ul == 50
    assert box.value() == 50
    page.redo()
    assert _step(page).volume_ul == 120


def test_what_a_tip_holds_is_beside_the_trash_row(app, page):
    assert page.tip_ul.value() == page.plan.tip_ul
    _type(page.tip_ul, "300")
    assert page.plan.tip_ul == 300
    page.undo()
    assert page.tip_ul.value() == page.plan.tip_ul != 300


def _top(widget, page):
    return widget.mapTo(page, widget.rect().topLeft()).y()


def test_a_wait_asks_its_time_on_the_left_under_the_large_time(app, page):
    page.add_step("wait")
    form = page.wait_form
    assert page.card.rows[0].label.text() == "How long"
    assert page.answer_body.currentWidget() is page.wait_box
    assert form.minutes.isVisibleTo(page) and form.seconds.isVisibleTo(page)
    app.processEvents()
    assert _top(form.minutes, page) > _top(form.time, page)
    assert _top(form.seconds, page) > _top(form.time, page)
    _type(form.minutes, "5")
    assert form.time.text() == "5 min"
    _type(form.seconds, "30")
    assert _step(page).seconds == 330
    assert form.time.text() == "5 min 30 s"
    assert page.step_list.cards()[0].title.text() == "Wait 5 min 30 s"


def test_a_wait_still_says_stop_works(app, page):
    page.add_step("wait")
    said = [label.text() for label in page.wait_form.findChildren(QLabel)
            if label.isVisibleTo(page)]
    assert ("The robot does nothing for this long. Pause and Stop still work."
            in said)


def test_the_wait_help_line_is_not_cut_off_on_a_small_window(app, themed,
                                                             window, page):
    window.resize(1280, 800)
    page.add_step("wait")
    app.processEvents()
    (line,) = [label for label in page.wait_form.findChildren(QLabel)
               if "Pause and Stop" in label.text()]
    assert line.width() == page.wait_form.width()
    assert line.height() >= line.heightForWidth(line.width())


@pytest.mark.parametrize("step_kind, form", [
    ("wait", lambda page: page.wait_form),
    ("wait_for_me", lambda page: page.message_form)])
def test_the_waits_are_a_column_in_the_middle_under_the_title(app, window,
                                                              page, step_kind,
                                                              form):
    page.add_step(step_kind)
    app.processEvents()
    shown = form(page)
    room = shown.parentWidget()
    assert shown.width() == 480
    assert abs(shown.geometry().center().x() - room.rect().center().x()) <= 2
    assert abs(shown.geometry().center().y() - room.rect().center().y()) <= 2


def test_a_wait_for_me_asks_its_message_over_the_preview(app, page):
    page.add_step("wait_for_me")
    form = page.message_form
    assert form.message.isVisibleTo(page)
    app.processEvents()
    assert _top(form.message, page) < _top(form.strip, page)
    form.message.clear()
    QTest.keyClicks(form.message, "Add the drug")
    assert _step(page).message == "Add the drug"
    assert form.strip.label.text() == "Add the drug"


@pytest.mark.parametrize("step_kind", [kind for kind, _name, _help
                                       in page_module.KINDS])
def test_no_kind_of_step_has_a_field_in_the_open_card(app, page, step_kind):
    page.add_step(step_kind)
    assert page.card.isVisibleTo(page)
    assert not _fields_in_the_card(page)


def test_digits_typed_into_a_wait_are_one_undo(app, page):
    page.add_step("wait")
    before = _step(page).seconds
    _type(page.wait_form.minutes, "120")
    assert _step(page).seconds == 7200
    page.undo()
    assert _step(page).seconds == before
    assert page.wait_form.time.text() == "1 min"


def test_a_message_typed_is_one_undo(app, page):
    page.add_step("wait_for_me")
    before = _step(page).message
    field = page.message_form.message
    field.selectAll()
    QTest.keyClicks(field, "Add the drug")
    page.undo()
    assert _step(page).message == before
    assert field.text() == before


@pytest.mark.parametrize("step_kind, form", [
    ("wait", lambda page: page.wait_form),
    ("wait_for_me", lambda page: page.message_form)])
def test_the_waits_are_locked_during_a_run(app, window, page, step_kind, form):
    page.add_step(step_kind)
    held = window.session.robot_lease.claim("liquid run", run=True)
    app.processEvents()
    assert not form(page).isEnabled()
    held.release()
    app.processEvents()
    assert form(page).isEnabled()


def _how_shown(page):
    """What How shows, of what it can show."""
    how = page.how_form
    return {name for name, widget in (
        ("times", how.cycles), ("volume", how.volume), ("speed", how.flow),
        ("height", how.at), ("own height", how.no_height))
        if widget.isVisibleTo(page)}


def test_a_blow_out_in_a_well_asks_its_speed_and_height(app, page):
    page.add_step("blow_out")
    assert page.question_title.text() == "Where is the tip blown out?"
    _click(app, page, "C3")
    assert _step(page).place.wells == ["C3"]
    page.next_button.click()
    assert _how_shown(page) == {"speed", "height"}
    _type(page.how_form.flow, "75")
    assert _step(page).flow_ul_s == 75


def test_a_blow_out_offers_a_well_a_point_by_hand_and_the_trash(app, page):
    page.add_step("blow_out")
    assert [b.text() for b in page.kinds["blow_out"].buttons] == [
        "A well", "A point", "By hand", "Trash"]


def test_a_new_blow_out_opens_on_a_well_with_nothing_chosen(app, page):
    page.add_step("blow_out")
    assert page.kinds["blow_out"].current() == 0
    assert page.on_plate.isVisibleTo(page)
    assert page.plate.isVisibleTo(page)
    assert not page.plate.selection
    assert _step(page).place.kind == "wells" and _step(page).place.empty
    assert page.card.rows[0].value.text() == "choose"


@pytest.mark.parametrize("choice", [0, 1, 2],
                         ids=["a well", "a point", "by hand"])
def test_a_blow_out_with_no_place_says_so_and_grays_try_and_next(
        app, window, page, choice):
    page.add_step("blow_out")
    page.kinds["blow_out"].buttons[choice].click()
    app.processEvents()
    rule = page.rule if choice == 0 else page.camera_rule
    assert rule.isVisibleTo(page)
    assert rule.text() == "Choose the place."
    assert not page.try_button.isEnabled()
    assert page.try_button.toolTip() == "Choose the place."
    assert not window.footer.next.isEnabled()
    assert (window.session.liquid_plan_problems()[_step(page).id]
            == ["Choose the place."])


def test_a_blow_out_given_a_place_can_be_tried_and_stops_saying_so(app, window,
                                                                   page):
    page.add_step("blow_out")
    _click(app, page, "C3")
    app.processEvents()
    assert page.rule.text() == "Click a well."
    assert page.try_button.isEnabled()
    assert page.try_button.toolTip() == page_module.TRY_HELP
    assert not window.session.liquid_plan_problems().get(_step(page).id)


def test_a_mix_with_no_wells_grays_try_once_too(app, page):
    page.add_step("mix")
    assert not page.try_button.isEnabled()
    assert page.try_button.toolTip() == "Choose the wells."
    assert page.rule.text() == "Click the wells to mix."


def test_a_blow_out_by_hand_keeps_the_spot_in_the_step(app, window, page):
    page.add_step("blow_out")
    page.kinds["blow_out"].buttons[2].click()      # By hand
    assert page.use_spot_button.isVisibleTo(page)
    assert _until(app, lambda: page.jog.pose is not None
                  and page.use_spot_button.isEnabled())
    pose = page.jog.pose
    page.use_spot_button.click()
    place = _step(page).place
    assert place.kind == "spot"
    assert (place.spot.x, place.spot.y, place.spot.z) == pytest.approx(pose[:3])
    assert page.card.rows[0].value.text() == "By hand"
    assert page.kinds["blow_out"].current() == 2
    assert page.try_button.isEnabled()
    assert not window.session.liquid_plan_problems().get(_step(page).id)
    page.next_button.click()
    assert _how_shown(page) == {"speed", "own height"}


@pytest.mark.parametrize("choice", [1, 3], ids=["a point", "the trash"])
def test_a_blow_out_elsewhere_says_it_uses_its_own_height(app, page, choice):
    page.add_step("blow_out")
    page.kinds["blow_out"].buttons[choice].click()
    page.next_button.click()
    assert _how_shown(page) == {"speed", "own height"}
    assert page.how_form.no_height.text() == (
        "A point and the trash are used at their own height.")


def test_a_saved_blow_out_into_the_trash_opens_as_it_was(app, tmp_path, page):
    path = tmp_path / "old.json"
    LiquidPlan(steps=[BlowOutStep()]).save(path)
    page.open_template(path)
    assert _step(page).place == Place(kind="trash")
    page.step_list.set_open(_step(page).id)
    assert page.kinds["blow_out"].current() == 3
    assert page.into_trash.isVisibleTo(page)


def test_a_move_of_the_tip_in_a_well_asks_only_its_height(app, page):
    page.add_step("move_tip")
    _click(app, page, "C3")
    page.next_button.click()
    assert _how_shown(page) == {"height"}
    _type(page.how_form.at.offset, "3")
    assert _step(page).at.offset_mm == 3


def test_a_move_of_the_tip_elsewhere_has_no_how(app, page):
    page.add_step("move_tip")
    page.kinds["move_tip"].buttons[1].click()      # A point
    assert [row.label.text() for row in page.card.rows
            if row.isVisibleTo(page)] == ["Where"]


def test_a_mix_asks_its_times_volume_speed_and_height(app, page):
    page.add_step("mix")
    _click(app, page, "A1")
    page.next_button.click()
    assert _how_shown(page) == {"times", "volume", "speed", "height"}
    how = page.how_form
    _type(how.cycles, "4")
    _type(how.volume, "25")
    _type(how.flow, "30")
    _type(how.at.offset, "1")
    step = _step(page)
    assert (step.cycles, step.volume_ul, step.flow_ul_s) == (4, 25, 30)
    assert how.at.sentence.text() == "1 mm above the bottom"
    assert how.at.note.text() == "Bottom from the plate drawing"
    assert how.at.drawing.height_.offset_mm == 1
    assert page.step_list.cards()[0].title.text() == "Mix 1 well · 4×"


def test_how_is_asked_on_the_left_not_in_the_card(app, page):
    page.add_step("mix")
    _click(app, page, "A1")
    page.next_button.click()
    assert page.how_form.isVisibleTo(page)
    assert not _fields_in_the_card(page)


@pytest.mark.parametrize("size", [(1400, 900), (1280, 800)],
                         ids=["1400x900", "1280x800"])
def test_how_is_a_column_in_the_middle_under_the_title(app, window, page,
                                                       size):
    window.resize(*size)
    page.add_step("mix")
    _click(app, page, "A1")
    page.next_button.click()
    app.processEvents()
    form = page.how_form
    room = form.parentWidget()
    assert form.width() == 480
    assert abs(form.geometry().center().x() - room.rect().center().x()) <= 2
    assert abs(form.geometry().center().y() - room.rect().center().y()) <= 2


def test_digits_typed_into_how_are_one_undo(app, page):
    page.add_step("mix")
    _click(app, page, "A1")
    page.next_button.click()
    before = _step(page).volume_ul
    box = page.how_form.volume
    _type(box, "120")
    assert _step(page).volume_ul == 120
    page.undo()
    assert _step(page).volume_ul == before
    assert box.value() == before


def test_how_is_locked_during_a_run(app, window, page):
    page.add_step("mix")
    _click(app, page, "A1")
    page.next_button.click()
    held = window.session.robot_lease.claim("liquid run", run=True)
    app.processEvents()
    assert not page.how_form.isEnabled()
    held.release()
    app.processEvents()
    assert page.how_form.isEnabled()


def test_every_kind_of_step_is_added_filled_in_and_run(app, window, page):
    _set_trash(app, page)
    page.first_step_button.click()
    _click(app, page, "A1")
    page.next_button.click()
    _click(app, page, "B1")
    _done(page)
    page.add_step("mix")
    _click(app, page, "B1")
    _done(page)
    page.add_step("blow_out")
    page.kinds["blow_out"].buttons[3].click()      # Trash
    _done(page)
    page.add_step("move_tip")
    _click(app, page, "C1")
    _done(page)
    page.add_step("wait")
    _type(page.wait_form.minutes, "0")
    _type(page.wait_form.seconds, "0.1")
    _done(page)
    page.add_step("wait_for_me")
    _done(page)
    assert not page.session.liquid_plan_problems()
    run = window.pages["liquid"]
    window.footer.next.click()
    run.how.buttons[1].click()                     # all in a row
    assert _until(app, run._startable), run.run_status()
    run.start_button.click()
    assert _until(app, lambda: run.run_activity() == "waiting")
    run.pause_button.click()
    assert _until(app, lambda: run._ended is not None and not run.jog.busy)
    assert run._ended == "done", run._outcome


# -- Points and By hand on the camera (#74) -----------------------------------


POINT = (120.0, 90.0, 60.0)


def _take_from(page, index):
    """A new step, on Take from's A well, A point or By hand."""
    page.first_step_button.click()
    page.kinds["source"].buttons[index].click()


def _point_button(page, name):
    return page.point_buttons[name]


def _went_to(window, point):
    """Whether the tip was moved to `point`'s X and Y."""
    return any(pose[:2] == pytest.approx(point[:2], abs=0.5)
               for pose in window.session.robot.log)


def _keys_on(page):
    return any(s.isEnabled() for s in page.jog._shortcuts)


def _free(app, window, page):
    assert _until(app, lambda: not window.session.robot_lease.busy
                  and not page.jog.busy)


def test_a_step_takes_from_a_saved_point_and_runs(app, window, page):
    window.session.remember("Reservoir", POINT)
    _set_trash(app, page)
    _take_from(page, 1)
    assert page.on_camera.isVisibleTo(page)
    assert page.card.rows[0].value.text() == "choose"
    _point_button(page, "Reservoir").use.click()
    assert page.card.rows[0].value.text() == "Point · Reservoir"
    assert page.plan.steps[0].source.point == "Reservoir"
    page.next_button.click()
    _click(app, page, "B1")
    _done(page)
    _free(app, window, page)
    _run_to_the_end(app, window)
    assert _taken_and_put(window)[1][-1] == "B1"
    assert _went_to(window, POINT)


def test_setup_positions_are_never_points(app, window, page):
    window.session.remember("tip_calib", POINT)
    window.session.remember("Reservoir", POINT)
    _take_from(page, 1)
    assert set(page.point_buttons) == {"Reservoir"}


def test_a_right_click_adds_a_memo_point_a_step_can_use(app, window, page,
                                                        monkeypatch):
    monkeypatch.setattr(page_module.moves, "tip_at_pixel",
                        lambda *a, **k: (50.0, 60.0))
    _take_from(page, 1)
    page.view.right_clicked.emit(10.0, 20.0)
    assert [p.name for p in window.session.memo_points] == ["M1"]
    assert window.session.memo_points[0].z is None
    assert set(page.point_buttons) == {"M1"}
    assert page.point_buttons["M1"].star.text() == "☆"


def test_a_memo_point_is_stored_as_a_spot_and_survives_a_restart(
        app, window, page, tmp_path):
    session = window.session
    session.add_memo_point(120.0, 90.0, 60.0)
    _set_trash(app, page)
    _take_from(page, 1)
    _point_button(page, "M1").use.click()
    source = page.plan.steps[0].source
    assert (source.kind, source.spot.name) == ("spot", "M1")
    assert (source.spot.x, source.spot.y, source.spot.z) == POINT
    assert page.card.rows[0].value.text() == "Point · M1"
    assert page.kinds["source"].current() == 1     # still A point
    page.next_button.click()
    _click(app, page, "B1")
    _done(page)

    saved = tmp_path / "kept.json"
    page.plan.save(saved)
    session.delete_memo_point("M1")                # the program closed
    session.set_liquid_plan(LiquidPlan.load(saved))
    assert session.liquid_plan_problems() == {}
    _free(app, window, page)
    _run_to_the_end(app, window)
    assert _went_to(window, POINT)


def test_a_memo_point_with_no_z_takes_the_travel_height(app, window, page):
    session = window.session
    session.add_memo_point(120.0, 90.0, None)
    _take_from(page, 1)
    _point_button(page, "M1").use.click()
    assert page.plan.steps[0].source.spot.z == session.point_z(
        session.memo_point("M1"))
    assert page.plan.steps[0].source.spot.z == session.motion.envelope.z[1]


def test_the_star_saves_a_point_with_its_name_as_the_default(app, window, page,
                                                             monkeypatch):
    session = window.session
    session.add_memo_point(120.0, 90.0, 60.0)
    asked = []

    def get_text(_parent, _title, _label, text=""):
        asked.append(text)
        return "Reservoir", True

    monkeypatch.setattr(page_module.QInputDialog, "getText", get_text)
    _take_from(page, 1)
    _point_button(page, "M1").use.click()
    _point_button(page, "M1").star.click()
    assert asked == ["M1"]
    assert session.saved_points()["Reservoir"] == POINT
    assert session.memo_points == []
    assert page.point_buttons["Reservoir"].star.text() == "★"
    # The step it was in uses the kept point from now on.
    assert page.plan.steps[0].source.kind == "point"
    assert page.plan.steps[0].source.point == "Reservoir"


def test_the_star_is_refused_for_a_name_in_use(app, window, page, monkeypatch):
    session = window.session
    session.remember("Reservoir", POINT)
    session.add_memo_point(10.0, 20.0, 30.0)
    said = []
    monkeypatch.setattr(page_module.QInputDialog, "getText",
                        lambda *a, **k: ("Reservoir", True))
    monkeypatch.setattr(page_module, "show_toast",
                        lambda _w, text, **k: said.append(text))
    _take_from(page, 1)
    _point_button(page, "M1").star.click()
    assert said == ['A point is already called "Reservoir".']
    assert session.memo_point("M1") is not None
    assert session.saved_points()["Reservoir"] == POINT


def test_use_this_spot_stores_the_tips_pose_and_the_run_comes_back(
        app, window, page):
    _set_trash(app, page)
    _take_from(page, 2)
    assert page.use_spot_button.isVisibleTo(page)
    assert page.card.rows[0].value.text() == "choose"
    assert _until(app, lambda: page.jog.pose is not None and
                  page.use_spot_button.isEnabled())
    pose = page.jog.pose
    page.use_spot_button.click()
    source = page.plan.steps[0].source
    assert (source.spot.x, source.spot.y, source.spot.z) == pytest.approx(pose)
    assert page.card.rows[0].value.text() == "By hand"
    assert page.kinds["source"].current() == 2
    page.next_button.click()
    _click(app, page, "B1")
    _done(page)
    _free(app, window, page)
    _run_to_the_end(app, window)


def test_no_camera_is_said_and_is_not_a_dead_end(app, window, page,
                                                 monkeypatch):
    session = window.session
    session.remember("Reservoir", POINT)
    monkeypatch.setattr(type(session), "upper_camera_label",
                        property(lambda _s: None))
    asked = []
    session.page_requested.connect(asked.append)
    _take_from(page, 1)
    assert page.no_camera.isVisibleTo(page)
    _point_button(page, "Reservoir").use.click()   # a point needs no picture
    assert page.plan.steps[0].source.point == "Reservoir"
    page.no_camera_button.click()
    assert asked == ["upper_camera"]


def test_the_keys_are_on_only_while_the_pad_is_on_screen(app, window, page):
    _take_from(page, 2)
    assert _until(app, lambda: page.jog.pose is not None
                  and not page.jog.busy)
    assert not page.jog.pad.isVisibleTo(page.view)
    assert not _keys_on(page)
    page.controls_button.click()
    assert page.jog.pad.isVisible()
    assert _until(app, lambda: _keys_on(page))
    page.kinds["source"].buttons[0].click()        # A well: the camera goes
    app.processEvents()
    assert not _keys_on(page)
    page.kinds["source"].buttons[2].click()
    assert _until(app, lambda: _keys_on(page))
    window.show_step("deck")
    app.processEvents()
    assert not _keys_on(page)


def test_the_step_is_in_the_plate_with_the_pad_hidden(app, page):
    """#109: the pad is behind Controls here, the step never."""
    _take_from(page, 2)
    assert _until(app, lambda: page.jog.pose is not None
                  and not page.jog.busy)
    assert not page.jog.pad.isVisibleTo(page.view)
    assert page.view.step_line == ("STEP · 1 mm", COLORS["accent-hover"])
    page.controls_button.click()
    app.processEvents()
    page.jog.set_step(5)
    assert page.view.step_line == ("STEP · 5 mm", COLORS["warn"])
    page.controls_button.click()
    app.processEvents()
    assert page.view.step_line == ("STEP · 5 mm", COLORS["warn"])


def test_a_point_takes_no_height_of_the_wells(app, page):
    """The offset a new step has for a well is not put on a point."""
    page.first_step_button.click()
    page.kinds["source"].buttons[1].click()
    step = page.plan.steps[0]
    assert (step.take_at.offset_mm, step.take_at.aside_x_mm) == (0, 0)
    page.kinds["source"].buttons[0].click()
    assert page.plan.steps[0].take_at.ref == "bottom"
    assert page.plan.steps[0].take_at.offset_mm == 2


# -- the trash chooser ---------------------------------------------------------

def test_a_new_plan_starts_with_the_default_trash(app, window, page):
    page.new_plan_button.click()
    assert page.plan.trash is None                 # none was set
    _set_trash(app, page)
    assert window.session.default_trash == page.plan.trash
    page.plan = LiquidPlan()
    page.new_plan_button.click()
    assert page.plan.trash == window.session.default_trash
    assert page.trash_text.text() == "Trash · Slot 5 · H12 · used by every step"


def test_a_trash_not_for_future_plans_is_the_plans_only(app, window, page):
    page.trash_row.click()
    assert page.future_trash.isChecked()           # ticked by default
    page.future_trash.click()
    _choose_plate(page, 5)
    _click(app, page, "H12")
    assert page.plan.trash.well == "H12"
    assert window.session.default_trash is None
    page.new_plan_button.click()
    assert page.plan.trash is None


def test_unticking_takes_the_trash_back_out_of_future_plans(app, window, page):
    page.trash_row.click()
    _choose_plate(page, 5)
    _click(app, page, "H12")
    page.future_trash.click()
    assert window.session.default_trash is None
    page.future_trash.click()
    assert window.session.default_trash == page.plan.trash


def test_removing_the_trash_grays_next_and_the_row_sets_it_again(app, window,
                                                                 page):
    _set_trash(app, page)
    page.first_step_button.click()
    _click(app, page, "A1")
    page.next_button.click()
    _click(app, page, "B1")
    next_ = window.footer.next
    assert next_.isEnabled()
    page.trash_row.click()
    page.remove_trash_button.click()
    assert page.plan.trash is None
    assert not next_.isEnabled()
    assert window.footer.sentence.text() == "Set the trash to run this plan."
    _click(app, page, "H12")                       # the chooser is still open
    assert next_.isEnabled()


def test_the_trash_keeps_its_height_and_offset(app, page):
    _set_trash(app, page)
    page.trash_row.click()
    page.trash_level.buttons[1].click()            # Middle
    _type(page.trash_offsets[2], -3)
    assert page.plan.trash.level == "center"
    assert page.plan.trash.offset == [0, 0, -3]
    _click(app, page, "H11")                       # another well, same height
    assert page.plan.trash.well == "H11"
    assert page.plan.trash.level == "center"
    assert page.plan.trash.offset == [0, 0, -3]
    page.undo()
    page.undo()
    assert page.plan.trash.offset == [0, 0, 0]


def test_a_saved_point_is_a_trash_and_a_memo_point_is_named_first(
        app, window, page, monkeypatch):
    session = window.session
    session.remember("Waste", (100.0, 100.0, 50.0))
    session.add_memo_point(10.0, 20.0, None)
    page.trash_row.click()
    page.kinds["trash"].buttons[1].click()         # A point
    assert page.question_body.currentWidget() is page.on_camera
    page.point_buttons["Waste"].use.click()
    assert page.plan.trash == Location(kind="point", point="Waste")
    assert not page.trash_level.isVisibleTo(page)

    monkeypatch.setattr(page_module.QInputDialog, "getText",
                        lambda *a, **k: ("Bucket", True))
    page.point_buttons["M1"].use.click()
    assert page.plan.trash == Location(kind="point", point="Bucket")
    assert "Bucket" in session.saved_points()
    assert session.memo_point("M1") is None
    assert session.default_trash == page.plan.trash


def test_a_memo_point_not_named_is_not_the_trash(app, window, page,
                                                 monkeypatch):
    window.session.add_memo_point(10.0, 20.0, None)
    page.trash_row.click()
    page.kinds["trash"].buttons[1].click()
    monkeypatch.setattr(page_module.QInputDialog, "getText",
                        lambda *a, **k: ("", False))
    page.point_buttons["M1"].use.click()
    assert page.plan.trash is None
    assert window.session.memo_point("M1") is not None


# -- Try once ------------------------------------------------------------------

@pytest.fixture
def toasts(page, monkeypatch):
    said = []
    monkeypatch.setattr(page_module, "show_toast",
                        lambda _host, text, **_kind: said.append(text))
    return said


def _free(app, window, page):
    assert _until(app, lambda: not window.session.robot_lease.busy
                  and not page.jog.busy)


def _one_to_four(app, page):
    page.first_step_button.click()
    _click(app, page, "A1")
    page.next_button.click()
    _click(app, page, "B1", "B2", "B3", "B4")


def test_try_once_does_the_first_well_and_leaves_the_tip_there(app, window,
                                                               page, toasts):
    _one_to_four(app, page)
    assert page.plan.trash is None                 # not needed to try
    _free(app, window, page)
    page.try_button.click()
    assert toasts == ["Step 1: trying on the first well."]
    assert _until(app, lambda: len(toasts) == 2)
    assert toasts[1] == "Done. The tip holds 150 µl."
    calls = window.session.robot.calls
    assert [c[2:4] for c in calls if c[0] == "aspirate"] == [("A1", 200)]
    assert [c[2:4] for c in calls if c[0] == "dispense"] == [("B1", 50)]
    assert calls[-1][0] == "dispense"              # nothing after: no raise
    assert window.session.in_tip == 150
    assert "150 µl" in window.pages["liquid"].tip_line.text()
    # Tried again, the tip's count goes on from there.
    _free(app, window, page)
    page.try_button.click()
    assert _until(app, lambda: len(toasts) == 4)
    assert toasts[3] == "Done. The tip holds 100 µl."


def test_try_once_is_refused_with_a_sentence_when_the_step_has_a_problem(
        app, window, page, toasts):
    page.first_step_button.click()
    _click(app, page, "A1")
    page.try_button.click()
    assert toasts == ["Choose where it goes."]
    assert not window.session.robot_lease.busy
    assert not [c for c in window.session.robot.calls if c[0] == "aspirate"]


def test_try_once_into_the_trash_needs_the_trash(app, page, toasts):
    page.first_step_button.click()
    _click(app, page, "A1")
    page.next_button.click()
    page.dest_kind.buttons[2].click()              # Trash
    page.try_button.click()
    assert toasts == ["Set the trash to try this step."]


def test_try_once_needs_a_tip(app, window, page, toasts):
    _one_to_four(app, page)
    window.session.tip = window.session.tip.__class__(attached=False)
    page.try_button.click()
    assert toasts == ["No tip on: put one on on the Tip step."]


def test_a_wait_has_nothing_to_try_and_a_busy_robot_grays_it(app, window, page):
    page.add_step("wait")
    assert page.try_button.isHidden()
    page.add_step("move_liquid")
    assert not page.try_button.isHidden()
    assert page.try_button.isEnabled()
    _free(app, window, page)
    held = window.session.robot_lease.claim("a test")
    assert held is not None
    window.session.robot_busy_changed.emit("a test")
    assert not page.try_button.isEnabled()
    assert page.try_button.toolTip() == "Robot busy."
    held.release()


# -- templates -----------------------------------------------------------------

def _save_as(page, name):
    page.save_as_button.click()
    page.name_field.setText(name)
    page.save_name_button.click()


def _listed(page):
    page.template_tile.click()
    assert page.template_box.isVisibleTo(page)
    return [page.template_list.item(n)
            for n in range(page.template_list.count())]


def test_a_template_opens_again_as_it_was_saved(app, window, page):
    _set_trash(app, page)
    _one_to_four(app, page)
    page.add_step("wait")
    page.step_list.set_open("")
    _save_as(page, "Wash")
    saved = page.plan
    assert saved.name == "Wash"
    assert page.save_changes_button.text() == 'Save changes to "Wash"'
    assert not page.save_changes_button.isEnabled()

    page.new_plan_button.click()
    assert page.plan.steps == []
    assert page.save_changes_button.isHidden()

    (item,) = _listed(page)
    assert item.text().startswith("Wash\n2 steps · 5 wells · saved ")
    page.template_list.itemClicked.emit(item)
    assert page.plan == saved
    assert [s.id for s in page.plan.steps] == [s.id for s in saved.steps]
    assert [s.color for s in page.plan.steps] == [s.color for s in saved.steps]
    assert not page.template_box.isVisibleTo(page)
    assert window.session.liquid_plan == saved


def test_save_changes_writes_over_the_open_template(app, window, page):
    _one_to_four(app, page)
    page.step_list.set_open("")
    _save_as(page, "Wash")
    page.add_step("wait")
    assert page.save_changes_button.isEnabled()
    page.save_changes_button.click()
    assert not page.save_changes_button.isEnabled()
    (template,) = window.session.liquid_templates()
    assert LiquidPlan.load(template.path) == page.plan


def test_undo_past_an_opening_closes_the_template(app, window, page):
    _one_to_four(app, page)
    page.step_list.set_open("")
    _save_as(page, "Wash")
    page.new_plan_button.click()
    page.add_step("wait")
    page.step_list.set_open("")
    other = page.plan
    page.open_template(window.session.liquid_templates()[0].path)
    assert not page.save_changes_button.isHidden()
    page.undo()
    assert page.plan == other
    assert page.save_changes_button.isHidden()     # never written over Wash


def test_a_name_in_use_is_refused_and_the_row_stays(app, page, toasts):
    _one_to_four(app, page)
    page.step_list.set_open("")
    _save_as(page, "Wash")
    _save_as(page, "wash")
    assert toasts[-1] == 'A template is already called "wash".'
    assert not page.name_row.isHidden()
    assert page.plan.name == "Wash"


def test_a_plate_not_on_the_deck_today_is_said_on_its_step(app, window, page):
    _one_to_four(app, page)
    page.step_list.set_open("")
    plan = page.plan.model_copy(deep=True, update={"name": "Elsewhere"})
    plan.steps[0].source.slot = "9"
    plan.steps[0].dest.slot = "9"
    path = page_module.paths.outputs_dir() / "elsewhere.json"
    plan.save(path)
    page.open_template(path)
    assert page.plan == plan
    assert window.session.liquid_plan_problems()[plan.steps[0].id]
    assert window.footer.sentence.text() == "Set the trash to run this plan."


def test_an_old_program_is_gray_and_does_not_open(app, window, page, toasts):
    folder = page_module.templates.templates_dir(
        page_module.templates.LIQUID_PLANS)
    path = folder / "daily.json"
    Program(name="Daily").save(path)
    before = path.read_bytes()
    (item,) = _listed(page)
    assert item.text() == "Daily\nMade with an older version"
    assert not item.flags() & Qt.ItemFlag.ItemIsEnabled
    plan = page.plan
    page.open_template(path)                       # as Open from file… would
    assert toasts == [page_module.OLD_FILE]
    assert page.plan == plan
    assert path.read_bytes() == before
    assert Program.load(path).name == "Daily"


def test_a_file_that_is_no_plan_is_said(app, page, tmp_path, toasts):
    path = tmp_path / "broken.json"
    path.write_text("{", encoding="utf-8")
    page.open_template(path)
    assert toasts == [page_module.NOT_A_PLAN]


def _a_step_on_slot_5(page):
    page.add_step("move_liquid")
    plan = page._edit()
    plan.steps[0].source = Place(kind="wells", slot="5", wells=["A1"])
    plan.steps[0].dest = Place(kind="wells", slot="5", wells=["A2"])
    page._commit(plan)
    return plan.steps[0].id


def test_a_digit_typed_checks_the_plan_once(app, page, monkeypatch):
    _a_step_on_slot_5(page)
    checked = []
    real = session_module.plan_problems
    monkeypatch.setattr(session_module, "plan_problems",
                        lambda *a: checked.append(a) or real(*a))
    page.amount_form.volume.setValue(33)
    app.processEvents()
    assert page.plan.steps[0].volume_ul == 33
    assert len(checked) == 1


def test_the_check_sees_the_deck_as_it_is_now(app, window, page):
    step_id = _a_step_on_slot_5(page)
    session = window.session
    assert step_id not in session.liquid_plan_problems()
    session.unload_labware(5)
    _until(app, lambda: not session.robot_lease.busy)
    assert step_id in session.liquid_plan_problems()


# -- the focus -----------------------------------------------------------------


def _field_focused(window):
    """The field with the keyboard, if any."""
    focused = window.focusWidget()
    return focused if isinstance(
        focused, (QAbstractSpinBox, QComboBox, QLineEdit)) else None


def _a_move(app, page):
    """A Move liquid with its wells, on Amount."""
    page.add_step("move_liquid")
    _click(app, page, "A1")
    page.next_button.click()
    _click(app, page, "B1")
    page.next_button.click()


def _go(page, by, row):
    if by == "next":
        page.next_button.click()
    else:
        page.card.rows[row].click()


@pytest.mark.parametrize("by", ["next", "row"])
def test_a_change_of_section_leaves_no_field_with_the_focus(app, window, page,
                                                            by):
    _a_move(app, page)
    page.card.rows[0].click()                      # back on Take from
    for row, field in ((1, page.tip_ul), (2, page.tip_ul),
                       (3, page.amount_form.volume)):
        field.setFocus()
        assert _field_focused(window) is field
        _go(page, by, row)
        app.processEvents()
        assert _field_focused(window) is None, page.section


def test_going_back_a_section_leaves_no_field_with_the_focus(app, window,
                                                             page):
    page.add_step("mix")
    _click(app, page, "A1")
    page.next_button.click()
    page.how_form.volume.setFocus()
    page.card.rows[0].click()                      # back on Wells
    app.processEvents()
    assert _field_focused(window) is None
    _a_move(app, page)
    page.next_button.click()                       # Extras
    page.extras_form.wait_after.setFocus()
    page.card.rows[2].click()                      # back on Amount
    app.processEvents()
    assert _field_focused(window) is None


@pytest.mark.parametrize("kind, field", [
    ("mix", lambda page: page.how_form.volume),
    ("wait", lambda page: page.wait_form.minutes),
    ("wait_for_me", lambda page: page.message_form.message)])
def test_opening_a_step_leaves_no_field_with_the_focus(app, window, page, kind,
                                                       field):
    page.add_step(kind)
    if kind == "mix":
        _click(app, page, "A1")
        page.next_button.click()
    field(page).setFocus()
    page.add_step("wait")
    app.processEvents()
    assert _field_focused(window) is None
    page.step_list.set_open(page.plan.steps[0].id)
    app.processEvents()
    assert _field_focused(window) is None


def test_done_leaves_no_field_with_the_focus(app, window, page):
    page.add_step("wait")
    page.wait_form.minutes.setFocus()
    page.next_button.click()                       # Done
    app.processEvents()
    assert _field_focused(window) is None


def test_a_key_after_a_change_of_section_changes_nothing(app, window, page):
    _a_move(app, page)
    page.amount_form.volume.setFocus()
    page.next_button.click()
    app.processEvents()
    plan = page.plan
    QTest.keyClick(window.focusWidget() or window, Qt.Key.Key_5)
    QTest.keyClick(window.focusWidget() or window, Qt.Key.Key_Up)
    app.processEvents()
    assert page.plan == plan


@pytest.mark.parametrize("kind, field", [
    ("mix", lambda page: page.how_form.volume),
    ("wait", lambda page: page.wait_form.minutes),
    ("wait_for_me", lambda page: page.message_form.message)])
def test_a_clicked_field_keeps_the_focus_through_its_digits(app, window, page,
                                                            kind, field):
    page.add_step(kind)
    if kind == "mix":
        _click(app, page, "A1")
        page.next_button.click()
    box = field(page)
    inner = box if isinstance(box, QLineEdit) else box.lineEdit()
    QTest.mouseClick(inner, Qt.MouseButton.LeftButton)
    assert _field_focused(window) is box
    box.selectAll()
    for digit in "123":
        QTest.keyClick(window.focusWidget(), digit)
        app.processEvents()
        assert _field_focused(window) is box


# -- the place choice (last: `themed` stays for the module) ---------------------


def _open_take_from(page):
    page.add_step("move_liquid")


def _open_put_into(page):
    page.add_step("move_liquid")
    page.next_button.click()


def _open_where_blown_out(page):
    page.add_step("blow_out")


def _open_where_the_tip_goes(page):
    page.add_step("move_tip")


def _open_the_trash(page):
    page.trash_row.click()


CHOICES = [_open_take_from, _open_put_into, _open_where_blown_out,
           _open_where_the_tip_goes, _open_the_trash]


def _choice(page):
    shown = [c for c in page.kinds.values() if c.isVisibleTo(page)]
    assert len(shown) == 1
    return shown[0]


def _left_right(widget, page):
    """Where the widget's left and right edges are in the left area."""
    left = widget.mapTo(page.left, widget.rect().topLeft()).x()
    return left, left + widget.width()


@pytest.mark.parametrize("opens", CHOICES)
def test_the_place_choice_is_centered_in_the_left_area(app, window, page,
                                                       opens):
    window.resize(1800, 900)
    opens(page)
    app.processEvents()
    left, right = _left_right(_choice(page), page)
    assert abs((left + right) / 2 - page.left.width() / 2) <= 1


@pytest.mark.parametrize("opens", CHOICES)
def test_the_place_choice_clears_the_title_on_a_small_window(app, themed,
                                                             window, page,
                                                             opens):
    window.resize(1280, 800)
    opens(page)
    app.processEvents()
    choice = _choice(page)
    left, right = _left_right(choice, page)
    title = page.question_title
    assert left >= _left_right(title, page)[0] + title.sizeHint().width()
    assert right <= page.left.width()
    bottom = choice.mapTo(page.question, choice.rect().bottomLeft()).y()
    assert 0 < bottom < page.question_body.y()     # on the title's row


def test_what_is_under_the_title_starts_at_the_same_height(app, themed,
                                                           window, page):
    page.add_step("mix")                           # asks wells: no choice
    app.processEvents()
    assert not any(c.isVisibleTo(page) for c in page.kinds.values())
    height = page.question_body.y()
    for opens in CHOICES:
        opens(page)
        app.processEvents()
        assert page.question_body.y() == height
