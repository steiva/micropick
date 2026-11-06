"""Measuring a plate's well center on the Well center step, one page in
Liquid and Manual pick: saved for the plate, and read by Liquid handling."""

import os
import time
from datetime import datetime, timedelta

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint                                # noqa: E402
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication,   # noqa: E402
                               QMessageBox, QPushButton, QScrollArea)

from micropick.config.labware import resolve_definition          # noqa: E402
from micropick.config.schema import DeckModule, WellCenter       # noqa: E402
from micropick.core.routine import Destination, Routine          # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages import liquid as liquid_page            # noqa: E402
from micropick.gui.pages import well_center as step_page         # noqa: E402
from micropick.gui.session import Session                        # noqa: E402
from micropick.gui.widgets.mini_deck import Tile                 # noqa: E402
from micropick.gui.widgets.step_card import StepCard             # noqa: E402
from micropick.gui.widgets.toast import toast_of                 # noqa: E402
from micropick.workflows import well_center                      # noqa: E402

PLATE = "corning_96_wellplate_360ul_flat"
TIPS = "opentrons_96_tiprack_300ul"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def session(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    s = Session(Options(mock=True))
    s.load_profile("mock")
    s.probe_robot()
    s.new_run()
    s.load_labware(resolve_definition(PLATE), 5)
    yield s
    s.shutdown()


def _settle(app, panel, timeout_s=10.0):
    end = time.monotonic() + timeout_s
    while panel.busy and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def _jog(app, session, panel, dx, dy, dz):
    """The operator's jog onto the real center, as the robot sees it."""
    robot = session.robot
    for axis, d in zip("xyz", (dx, dy, dz)):
        robot.move_relative(axis, d, verbose=False)
    panel.refresh_position()
    _settle(app, panel)


def _liquid(app, session):
    page = liquid_page.LiquidHandlingPage(session)
    _settle(app, page.jog)
    return page


def _step(app, session):
    page = step_page.WellCenterPage(session)
    _settle(app, page.jog)
    page.select_plate(5, PLATE)
    page.wells.map.well_clicked.emit("B2")
    return page


def _go_to_top(app, page):
    page.move_button.click()
    _settle(app, page.jog)
    # The tip at the robot's well top: centering is next.
    assert page.centered_button.isEnabled()
    assert not page.save_button.isEnabled()


def _save(page):
    page.centered_button.click()
    page.level_button.click()
    page.save_button.click()


def _measure(app, session, page, jog):
    _go_to_top(app, page)
    at = tuple(page.jog.pose)
    _jog(app, session, page.jog, *jog)
    _save(page)
    return at


def test_the_step_saves_the_jog_from_the_robots_well_top(app, session):
    page = _step(app, session)
    assert "from the plate's drawing" in page.depth_source.text()
    _measure(app, session, page, (0.4, -0.3, -1.5))
    center = session.profile.deck.well_center(5, PLATE)
    assert center is not None and center.well == "B2"
    assert center.offset == pytest.approx([0.4, -0.3, -1.5], abs=0.05)
    assert center.depth_mm == pytest.approx(10.67, abs=0.01)  # the drawing's
    assert session.well_center_status(5, PLATE) == "measured"
    assert page.plate_status.text() == "Measured today"
    page.deleteLater()


def test_the_step_saves_the_depth_typed(app, session):
    page = _step(app, session)
    _go_to_top(app, page)
    page.centered_button.click()
    page.level_button.click()
    page.well_depth.setValue(9.25)
    page.save_button.click()
    assert session.profile.deck.well_center(5, PLATE).depth_mm == \
        pytest.approx(9.25)
    page.deleteLater()


def test_the_steps_go_in_order(app, session):
    page = _step(app, session)
    assert page.move_button.isEnabled()
    assert not page.centered_button.isEnabled()
    assert not page.level_button.isEnabled()
    assert not page.save_button.isEnabled()
    _go_to_top(app, page)
    page.centered_button.click()
    assert page.level_button.isEnabled()
    assert not page.save_button.isEnabled()
    page.level_button.click()
    assert page.save_button.isEnabled()
    # Another well: back through Redo, the tip not over it.
    page.path.link("well").click()
    assert not page.save_button.isEnabled()
    assert not page.centered_button.isEnabled()
    page.deleteLater()


def test_one_card_holds_the_path(app, session):
    """#127: no numbered card, no chip; the map only on the first item."""
    page = _step(app, session)
    assert not page.findChildren(StepCard)
    assert not page.path_card.isHidden()
    assert page.path.current == "well"
    assert page.wells.isVisibleTo(page.path)
    page.deleteLater()


def test_with_no_plate_on_the_deck_there_is_no_path_card(app, session):
    page = _step(app, session)
    session.unload_labware(5)
    app.processEvents()
    assert page.path_card.isHidden()
    assert page.plate_status.text() == "No plate on the deck."
    page.deleteLater()


def _lit(page):
    """The tiles that can be clicked, and what each says under its name."""
    page.plate_choice.open_popover()
    popover = page.plate_choice._popover
    lit = {t.tile.slot: t.note.text() for t in popover.findChildren(Tile)
           if t.isEnabled()}
    popover.close()
    return lit


def _click_plate(page, slot):
    """The operator's click on a tile of the selector."""
    page.plate_choice.open_popover()
    tile = next(t for t in page.plate_choice._popover.findChildren(Tile)
                if t.tile.slot == slot)
    tile.click()


def test_the_selector_stands_over_the_status_and_the_path(app, session):
    page = _step(app, session)
    page.resize(1200, 900)
    page.show()
    app.processEvents()
    tops = [w.mapTo(page, QPoint(0, 0)).y()
            for w in (page.plate_choice, page.plate_status, page.path_card)]
    assert tops == sorted(tops)
    page.hide()
    page.deleteLater()


@pytest.mark.parametrize("task", ["manual", "auto", "liquid"])
def test_every_plate_is_lit_and_other_labware_is_not(app, session, task):
    session.load_labware(resolve_definition(PLATE), 2)
    session.load_labware(resolve_definition(TIPS), 10)
    page = _step(app, session)
    page.set_task(task)
    assert set(_lit(page)) == {2, 5}
    page.deleteLater()


def test_tiles_say_measured_or_not_and_the_line_follows_the_plate(
        app, session):
    session.load_labware(resolve_definition(PLATE), 2)
    session.set_well_center(5, PLATE, (0.1, 0.2, -1.0), "C3", depth_mm=10.0)
    page = _step(app, session)
    assert _lit(page) == {2: "Not measured", 5: "Measured"}
    _click_plate(page, 5)
    assert page.plate_status.text() == "Measured today"
    _click_plate(page, 2)
    assert page.plate_status.text() == "Not measured: using the drawing"
    assert page.plate_choice.slot == 2
    page.deleteLater()


def test_a_click_on_the_selector_chooses_the_plate_for_the_session(
        app, session):
    session.load_labware(resolve_definition(PLATE), 2)
    page = _step(app, session)
    _click_plate(page, 5)
    assert page._selected == ("5", PLATE)
    assert session.chosen_plate.slot == "5"
    page.deleteLater()


def test_the_page_opens_on_the_first_plate_with_nothing_chosen(app, session):
    session.load_labware(resolve_definition(PLATE), 2)
    page = step_page.WellCenterPage(session)
    _settle(app, page.jog)
    page.set_task("manual")
    assert page._selected == ("2", PLATE)
    page.deleteLater()


def test_auto_opens_on_the_plans_plate_and_the_chosen_one_comes_first(
        app, session):
    session.load_labware(resolve_definition(PLATE), 2)
    session.load_labware(resolve_definition(PLATE), 3)
    session.set_routine(Routine(Destination.from_definition(
        resolve_definition(PLATE), slot=5), {"A1": 1}, name="plan"))
    page = step_page.WellCenterPage(session)
    _settle(app, page.jog)
    page.set_task("auto")
    assert page._selected == ("5", PLATE)
    # Only Auto pick goes by the plan.
    page.set_task("liquid")
    assert page._selected == ("2", PLATE)
    session.choose_plate(3)
    page.set_task("auto")
    assert page._selected == ("3", PLATE)
    page.deleteLater()


def test_auto_shows_the_plates_with_no_plan(app, session):
    page = step_page.WellCenterPage(session)
    _settle(app, page.jog)
    page.set_task("auto")
    assert page._selected == ("5", PLATE)
    assert not page.path_card.isHidden()
    page.deleteLater()


def test_a_chosen_plate_is_kept_coming_back_to_the_step(app, session):
    session.load_labware(resolve_definition(PLATE), 2)
    page = _step(app, session)
    _click_plate(page, 5)
    page.select_plate(2, PLATE)
    page.set_task("liquid")
    assert page._selected == ("5", PLATE)
    page.deleteLater()


def test_a_click_with_no_robot_session_is_refused_and_chooses_nothing(
        app, session):
    page = _step(app, session)
    session.run_state.run_id = None
    page.plate_choice.slot_chosen.emit(5)
    assert session.chosen_plate is None
    assert toast_of(page).text() == "No robot session."
    page.deleteLater()


def test_a_plate_moved_is_forgotten(app, session):
    session.load_labware(resolve_definition(PLATE), 2)
    page = _step(app, session)
    _click_plate(page, 5)
    session.move_labware(5, 6)
    app.processEvents()
    assert session.chosen_plate is None
    assert page._selected == ("2", PLATE)
    page.deleteLater()


def test_a_plate_taken_off_and_put_back_is_another_plate(app, session):
    session.load_labware(resolve_definition(PLATE), 2)
    page = _step(app, session)
    _click_plate(page, 5)
    session.unload_labware(5)
    session.load_labware(resolve_definition(PLATE), 5)
    app.processEvents()
    assert session.chosen_plate is None
    assert page._selected == ("2", PLATE)
    page.deleteLater()


def test_a_new_robot_session_forgets_the_chosen_plate(app, session):
    page = _step(app, session)
    _click_plate(page, 5)
    session.new_run()
    assert session.chosen_plate is None
    page.deleteLater()


def test_a_plate_measured_before_is_measured_again_and_save_replaces_it(
        app, session):
    session.set_well_center(5, PLATE, (0.9, 0.9, -9.0), "C3", depth_mm=10.0)
    page = _step(app, session)
    _measure(app, session, page, (0.4, -0.3, -1.5))
    center = session.profile.deck.well_center(5, PLATE)
    assert center.offset == pytest.approx((0.4, -0.3, -1.5))
    assert center.well == "B2"
    assert page.plate_status.text() == "Measured today"
    page.deleteLater()


def _bodies(page):
    return {"well": page.move_button, "center": page.centered_button,
            "lower": page.level_button, "depth": page.save_button}


def test_only_the_current_item_has_a_body(app, session):
    page = _step(app, session)

    def shows(key):
        assert page.path.current == key
        assert {k for k, b in _bodies(page).items()
                if b.isVisibleTo(page.path)} == {key}

    shows("well")
    page.move_button.click()
    _settle(app, page.jog)
    shows("center")
    page.centered_button.click()
    shows("lower")
    page.level_button.click()
    shows("depth")
    assert page.well_depth.isVisibleTo(page.path)
    assert page.depth_source.isVisibleTo(page.path)
    assert not page.wells.isVisibleTo(page.path)
    page.deleteLater()


def test_done_items_read_as_results_the_well_named(app, session):
    page = _step(app, session)
    _go_to_top(app, page)
    assert page.path.title("well") == "Tip is over B2"
    assert page.path.title("center") == "Center the tip"
    page.centered_button.click()
    assert page.path.title("center") == "Tip is centered"
    page.level_button.click()
    assert page.path.title("lower") == "Tip is level with the top"
    assert page.path.title("depth") == "Well depth"
    page.deleteLater()


def test_a_well_clicked_during_the_move_is_not_the_well_named(app, session):
    """The tip goes where Move the tip said, so the map follows it."""
    page = _step(app, session)
    page.show()
    _settle(app, page.jog)
    claimed = []
    real = page.jog.run_job

    def run_job(job, **kw):
        claimed.append(job)
        page.wells.map.well_clicked.emit("C3")     # while it moves
        return real(job, **kw)

    page.jog.run_job = run_job
    page.move_button.click()
    _settle(app, page.jog)
    assert claimed
    assert page.path.title("well") == "Tip is over B2"
    assert page._well() == "B2"
    page.hide()
    page.deleteLater()


def test_redo_goes_back_to_the_map_and_moves_nothing(app, session):
    page = _step(app, session)
    _go_to_top(app, page)
    page.centered_button.click()
    pose = tuple(page.jog.pose)
    jobs = []
    page.jog.run_job = lambda *a, **k: jobs.append(a)
    page.path.link("well").click()
    assert page.path.current == "well"
    assert page.wells.isVisibleTo(page.path)
    assert page._well() == "B2"                       # the well stays chosen
    assert page._measurement.for_plate(5, PLATE) is None
    assert jobs == [] and not session.robot_lease.busy
    assert tuple(page.jog.pose) == pose
    page.deleteLater()


def test_redo_lets_the_depth_follow_the_drawing_again(app, session):
    page = _step(app, session)
    _go_to_top(app, page)
    page.centered_button.click()
    page.level_button.click()
    page.well_depth.setValue(3.0)
    page.path.link("well").click()
    assert page.well_depth.value() == pytest.approx(10.67, abs=0.01)
    page.deleteLater()


def test_redo_is_the_only_link_on_the_path(app, session):
    page = _step(app, session)
    assert not page.path.link("well").isVisibleTo(page.path)    # not done yet
    _go_to_top(app, page)
    assert page.path.link("well").isVisibleTo(page.path)
    assert all(page.path.link(k) is None
               for k in ("center", "lower", "depth"))
    page.deleteLater()


@pytest.mark.parametrize("presses", [0, 1, 2, 3])
def test_the_pad_works_on_every_item(app, session, presses):
    page = _step(app, session)
    page.show()
    _settle(app, page.jog)
    if presses:
        _go_to_top(app, page)
    if presses > 1:
        page.centered_button.click()
    if presses > 2:
        page.level_button.click()
    before = page.jog.pose[0]
    page.pad.buttons["x+"].click()
    _settle(app, page.jog)
    assert page.jog.pose[0] > before
    page.hide()
    page.deleteLater()


def test_a_plate_measured_or_not_opens_on_the_first_item(app, session):
    session.load_labware(resolve_definition(PLATE), 2)
    session.set_well_center(5, PLATE, (0.1, 0.2, -1.0), "C3", depth_mm=10.0)
    page = step_page.WellCenterPage(session)
    _settle(app, page.jog)
    for slot in (5, 2):
        page.select_plate(slot, PLATE)
        assert page.path.current == "well"
    page.deleteLater()


def test_a_plate_begun_reopens_on_center_the_tip_over_its_well(app, session):
    session.load_labware(resolve_definition(PLATE), 2)
    page = _step(app, session)
    _go_to_top(app, page)
    page.centered_button.click()
    page.select_plate(2, PLATE)
    assert page.path.current == "well"
    page.select_plate(5, PLATE)
    assert page.path.current == "center"
    assert page.path.title("well") == "Tip is over B2"
    assert page._well() == "B2"
    page.deleteLater()


def test_a_move_ending_on_another_plate_leaves_no_path_without_a_measurement(
        app, session):
    """The one measurement in hand passes to the plate moved to."""
    session.load_labware(resolve_definition(PLATE), 2)
    page = _step(app, session)
    _go_to_top(app, page)                             # slot 5, on Center
    page.select_plate(2, PLATE)
    real = page.jog.run_job

    def run_job(job, **kw):
        started = real(job, **kw)
        page.select_plate(5, PLATE)                   # back, during the move
        return started

    page.jog.run_job = run_job
    page.move_button.click()
    _settle(app, page.jog)
    assert page._measurement.for_plate(2, PLATE) is not None
    assert page.path.current == "well"
    page.deleteLater()


def test_a_new_robot_session_puts_the_path_back_on_its_first_item(
        app, session):
    page = _step(app, session)
    _go_to_top(app, page)
    session.new_run()
    app.processEvents()
    _settle(app, page.jog)
    assert page.path.current == "well"
    assert page._measurement.for_plate(5, PLATE) is None
    page.deleteLater()


def test_a_folding_body_leaves_the_keyboard_to_the_robot(app, session):
    """Typing in the depth field, then Redo: no other field gets the keys."""
    page = _step(app, session)
    page.show()
    page.activateWindow()
    _settle(app, page.jog)
    _go_to_top(app, page)
    page.centered_button.click()
    page.level_button.click()
    page.well_depth.setFocus()
    app.processEvents()
    assert QApplication.focusWidget() is not None
    page.path.link("well").click()
    app.processEvents()
    assert not isinstance(QApplication.focusWidget(), QAbstractSpinBox)
    assert QApplication.focusWidget() is None
    page.hide()
    page.deleteLater()


@pytest.mark.parametrize("task", ["liquid", "manual"])
@pytest.mark.parametrize("height", [620, 1400])
def test_the_end_of_move_the_tip_moves_nothing_above_the_path(
        app, session, stands_still, task, height):
    """Scrolled down to reach the button too: a column that shrank would
    bring everything down with it."""
    session.load_labware(resolve_definition(PLATE), 2)
    session.load_labware(resolve_definition(PLATE), 3)
    session.set_well_center(5, PLATE, (0.1, 0.2, -1.0), "B2", depth_mm=10.0)
    page = step_page.WellCenterPage(session)
    page.set_task(task)
    page.select_plate(5, PLATE)
    page.resize(1100, height)
    page.show()
    _settle(app, page.jog)
    column = page.findChild(QScrollArea, "column")
    bar = column.verticalScrollBar()
    app.processEvents()
    bar.setValue(bar.maximum())
    tall = bar.maximum() > 0

    def move():
        page.move_button.click()
        _settle(app, page.jog)

    stands_still(column, move, but=page.path)
    assert page.path.current == "center"
    # No bar for empty room in a window it all fits in.
    assert (bar.maximum() > 0) == tall
    page.hide()
    page.deleteLater()


def test_the_well_is_chosen_on_the_plates_map(app, session):
    page = _step(app, session)
    definition = resolve_definition(PLATE)
    assert page.wells.map._columns == definition.ordering
    assert page._well() == "B2"
    assert page.wells.well_label.text() == "B2"
    assert not page.wells.plate.isVisibleTo(page.wells)
    page.deleteLater()


def test_the_map_opens_on_the_well_last_measured(app, session):
    session.set_well_center(5, PLATE, (0.1, 0.2, -1.0), "C3", depth_mm=10.0)
    page = step_page.WellCenterPage(session)
    _settle(app, page.jog)
    page.select_plate(5, PLATE)
    assert page._well() == "C3"
    page.deleteLater()


def test_a_robot_held_is_a_refusal_and_nothing_is_said_under_the_tip(
        app, session):
    page = _step(app, session)
    lease = session.robot_lease.claim("picking run", run=True)
    try:
        page._go_to_well_top()
    finally:
        lease.release()
    said = toast_of(page)
    assert said.text() == session.robot_lease.refusal
    assert said.property("tone") == "refusal"
    assert not hasattr(page, "said")


def test_the_step_shows_the_tip_and_the_pad_and_no_camera(app, session):
    from micropick.gui.widgets.camera_view import CameraView
    page = _step(app, session)
    assert not page.findChildren(CameraView)
    assert page.pad.parent() is not page.jog       # out of the hidden panel
    page._show_pose((101.5, 52.25, 66.4))
    assert page.xy_readout.text() == "X 101.50   Y 52.25"
    assert page.z_readout.text() == "Z 66.40"
    page.deleteLater()


def test_keys_puts_the_keys_bound_on_the_pads_buttons(app, session):
    """#58: the pad's Keys, as on every jog pad."""
    from micropick.gui import keys
    session.set_settings(session.settings.model_copy(
        update={"keys": {"z+": "J"}}))
    page = _step(app, session)
    page.show()
    pad = page.pad
    assert not pad.keys.isHidden()
    assert all(b.keycap == "" for b in pad.buttons.values())
    pad.keys.click()
    assert pad.buttons["x+"].keycap == "→"
    assert pad.buttons["z+"].keycap == "J"            # as bound
    assert pad.buttons["z-"].keycap == "PgDn"
    assert pad.carousel.bigger.keycap == "+"
    assert pad.carousel.smaller.keycap == "-"
    assert not pad.key_list.isHidden()
    text = pad.key_list.text()
    for action_id in ("undo", "help"):
        assert keys.key_text(session.settings.keys, action_id) in text
    pad.keys.click()
    assert pad.buttons["x+"].keycap == "" and pad.key_list.isHidden()
    page.hide()
    page.deleteLater()


@pytest.mark.parametrize("task", ["auto", "manual", "liquid"])
def test_every_task_says_the_same_of_a_plate_not_measured(app, session, task):
    page = _step(app, session)
    page.set_task(task)
    assert page.intro.text() == "A plate not measured uses its drawing."
    assert page.plate_status.text() == "Not measured: using the drawing"
    assert "Skip this plate, use the drawing" not in [
        button.text() for button in page.findChildren(QPushButton)]
    page.deleteLater()


def test_measuring_again_keeps_the_depth_set_for_the_plate(app, session):
    page = _step(app, session)
    _measure(app, session, page, (0.4, -0.3, -1.5))
    session.set_well_depth(5, PLATE, 9.5)
    page.select_plate(5, PLATE)
    page.wells.map.well_clicked.emit("B2")
    _measure(app, session, page, (0.1, 0.2, -1.0))
    center = session.profile.deck.well_center(5, PLATE)
    assert center.offset == pytest.approx([0.1, 0.2, -1.0], abs=0.05)
    assert center.depth_mm == pytest.approx(9.5)
    page.deleteLater()


def test_a_center_measured_on_the_step_is_liquid_handlings(app, session):
    page = _step(app, session)
    _measure(app, session, page, (0.4, -0.3, -1.5))
    plate = session.liquid_plates()["5"]
    assert plate.center == pytest.approx((0.4, -0.3, -1.5), abs=0.05)
    page.deleteLater()


def test_a_liquid_run_says_which_plates_it_goes_into(app, session):
    from micropick.core.liquid import Location
    session.tip = session.tip.__class__(attached=True)
    from micropick.core.liquid_plan import LiquidPlan, WaitForMe
    page = _liquid(app, session)
    session.set_liquid_plan(LiquidPlan(
        trash=Location(kind="well", slot="5", load_name=PLATE, well="H12"),
        steps=[WaitForMe()]))
    page._confirm_start = lambda: True
    page._start()
    end = time.monotonic() + 10
    while not page._pause.is_set() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    assert session.robot_lease.uses_plate(5, PLATE)
    page._stop_run()
    page._pause.clear()
    _settle(app, page.jog)
    page.deleteLater()


def test_a_picking_run_says_it_goes_into_the_plans_plate(app, session,
                                                         monkeypatch):
    from types import SimpleNamespace
    from micropick.gui.pages.picking import PickingPage
    page = PickingPage(session)
    monkeypatch.setattr(page, "_run_problems", lambda: [])
    monkeypatch.setattr(page, "_confirm_start", lambda: True)
    session.routine = SimpleNamespace(destination=SimpleNamespace(slot=5))
    claims = []
    real = session.robot_lease.claim

    def claim(what, **kw):
        claims.append((what, kw))
        return None                              # stop there: refused

    monkeypatch.setattr(session.robot_lease, "claim", claim)
    page._start_run()
    assert claims == [("picking run",
                       {"run": True, "plates": {("5", PLATE)}})]
    monkeypatch.setattr(session.robot_lease, "claim", real)
    page.deleteLater()


def test_the_measurement_in_hand_outlives_a_change_elsewhere_on_the_deck(
        app, session):
    page = _step(app, session)
    _go_to_top(app, page)
    session.load_labware(resolve_definition(PLATE), 2)
    app.processEvents()
    assert page._selected == ("5", PLATE)
    assert page.centered_button.isEnabled()
    page.deleteLater()


def test_save_says_which_run_uses_the_plate(app, session):
    page = _step(app, session)
    _go_to_top(app, page)
    page.centered_button.click()
    page.level_button.click()
    run = session.robot_lease.claim("liquid run", run=True,
                                    plates={("5", PLATE)})
    try:
        app.processEvents()
        assert "liquid run" in page.save_button.toolTip()
    finally:
        run.release()
    app.processEvents()
    assert "liquid run" not in page.save_button.toolTip()
    page.deleteLater()


def test_a_center_a_run_uses_is_not_replaced_under_it(app, session):
    page = _step(app, session)
    _go_to_top(app, page)
    _jog(app, session, page.jog, 0.4, -0.3, -1.5)
    page.centered_button.click()
    page.level_button.click()
    run = session.robot_lease.claim("picking run", run=True,
                                    plates={("5", PLATE)})
    try:
        app.processEvents()
        assert not page.save_button.isEnabled()
        page._save()
        assert session.profile.deck.well_center(5, PLATE) is None
    finally:
        run.release()
    page.deleteLater()


def test_liquids_well_center_step_is_this_page_and_next_passes_it(
        app, tmp_path, monkeypatch):
    from micropick.gui import tasks
    from micropick.gui.shell import MainWindow
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    w = MainWindow(Options(mock=True))
    session = w.session
    try:
        session.probe_robot()
        session.new_run()
        session.load_labware(resolve_definition(PLATE), 5)
        w.open_task("liquid")
        w.show_step("well_center")
        page = w.stack.currentWidget()
        assert isinstance(page, step_page.WellCenterPage)
        assert page._selected == ("5", PLATE)
        session.tip = session.tip.__class__(attached=True)
        session.tip_changed.emit(session.tip)
        app.processEvents()
        assert page.isEnabled()
        assert not tasks.step_state(session, "liquid", "well_center").done
        assert w.footer.next.isEnabled()
    finally:
        session.shutdown()
        w.deleteLater()


def test_manuals_well_center_step_is_this_page(
        app, tmp_path, monkeypatch):
    """#63: optional, so Next is enabled with nothing measured."""
    from micropick.gui.shell import MainWindow
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    w = MainWindow(Options(mock=True))
    session = w.session
    try:
        session.probe_robot()
        session.new_run()
        session.load_labware(resolve_definition(PLATE), 5)
        w.open_task_step("manual", "well_center")
        page = w.stack.currentWidget()
        assert isinstance(page, step_page.WellCenterPage)
        assert w.footer.next.isEnabled()
    finally:
        session.shutdown()
        w.deleteLater()


def _platform(height):
    return DeckModule(slots=[5], offset=[0, 0, height], name="Platform")


def _plan_on_5(session):
    session.set_routine(Routine(Destination.from_definition(
        resolve_definition(PLATE), slot=5), {"A1": 1}, name="plan"))


def _saved(session, days_ago=3, depth=10.67):
    """A plate measured on B2 some days before, 1.5 mm under the robot's
    top."""
    session.profile.deck.well_centers.append(WellCenter(
        slot=5, load_name=PLATE, offset=[0.4, -0.3, -1.5], well="B2",
        depth_mm=depth, module_height_mm=0.0,
        measured_at=datetime.now() - timedelta(days=days_ago)))


@pytest.mark.parametrize("task", ["manual", "auto", "liquid"])
def test_a_plate_is_not_measured_once_the_slots_module_changes(
        app, session, task):
    _plan_on_5(session)
    page = _step(app, session)
    page.set_task(task)
    _measure(app, session, page, (0.4, -0.3, -1.5))
    assert page.plate_status.text() == "Measured today"
    session.set_deck_modules([_platform(64.2)])
    assert page.plate_status.text() == "Not measured: using the drawing"
    assert _lit(page) == {5: "Not measured"}
    assert "from the plate's drawing" in page.depth_source.text()
    page.deleteLater()


def test_a_deposit_does_not_read_a_measurement_of_another_height(session):
    _plan_on_5(session)
    session.set_well_center(5, PLATE, (0.4, -0.3, -1.5), "B2", depth_mm=10.0)
    read = (session.profile, session.routine, session.run_state)
    assert well_center.for_plan(*read) is not None
    session.set_deck_modules([_platform(64.2)])
    assert well_center.for_plan(*read) is None


def test_manual_shows_the_last_measured_bottom_with_use_it(app, session):
    _saved(session)
    page = _step(app, session)
    page.set_task("manual")
    assert not page.last_card.isHidden()
    # Corning 96: its well top 3.55 + 10.67 on the deck, the rim 1.5 under
    # it, the bottom 10.67 under that.
    assert page.last_value.text() == "Last time: Z 2.05 mm"
    assert page.last_when.text() == "3 days ago"
    assert page.use_button.isEnabled()
    page.deleteLater()


def test_the_last_bottom_counts_the_height_of_the_slots_module(app, session):
    session.set_deck_modules([_platform(64.2)])
    session.set_well_center(5, PLATE, (0.4, -0.3, -1.5), "B2", depth_mm=10.67)
    page = _step(app, session)
    page.set_task("manual")
    assert page.last_value.text() == "Last time: Z 66.25 mm"
    assert page.last_when.text() == "today"
    page.deleteLater()


def test_use_it_confirms_the_selected_plate_and_a_new_session_forgets(
        app, session):
    _saved(session)
    page = _step(app, session)
    page.set_task("manual")
    page.use_button.click()
    assert session.plate_bottom_confirmed(5, PLATE)
    assert not page.use_button.isEnabled()
    session.new_run()
    session.load_labware(resolve_definition(PLATE), 5)
    assert not session.plate_bottom_confirmed(5, PLATE)
    assert page.use_button.isEnabled()
    page.deleteLater()


def test_save_confirms_the_plate_on_the_page(app, session):
    page = _step(app, session)
    page.set_task("manual")
    assert page.last_card.isHidden()
    _measure(app, session, page, (0.4, -0.3, -1.5))
    assert session.plate_bottom_confirmed(5, PLATE)
    assert not page.last_card.isHidden()
    assert not page.use_button.isEnabled()
    page.deleteLater()


def test_a_plate_with_no_stored_measurement_has_no_use_it(app, session):
    page = _step(app, session)
    page.set_task("manual")
    assert page.last_card.isHidden()
    page.deleteLater()


@pytest.mark.parametrize("task", ["liquid", "auto"])
def test_no_use_it_in_liquid_or_auto(app, session, task):
    _plan_on_5(session)
    _saved(session)
    page = _step(app, session)
    page.set_task(task)
    assert page.last_card.isHidden()
    page.deleteLater()


def test_each_plate_shows_its_own_last_bottom(app, session):
    session.load_labware(resolve_definition(PLATE), 2)
    _saved(session)
    page = _step(app, session)
    page.set_task("manual")
    page.select_plate(5, PLATE)
    assert not page.last_card.isHidden()
    page.select_plate(2, PLATE)
    assert page.last_card.isHidden()
    page.deleteLater()
