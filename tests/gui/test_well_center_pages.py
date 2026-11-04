"""Measuring a plate's well center on the Well center step, one page in
Liquid and Manual pick: saved for the plate, and read by Liquid handling."""

import os
import time
from datetime import datetime, timedelta

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox          # noqa: E402

from micropick.config.labware import resolve_definition          # noqa: E402
from micropick.config.schema import DeckModule, WellCenter       # noqa: E402
from micropick.core.routine import Destination, Routine          # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages import liquid as liquid_page            # noqa: E402
from micropick.gui.pages import well_center as step_page         # noqa: E402
from micropick.gui.session import Session                        # noqa: E402
from micropick.workflows import well_center                      # noqa: E402

PLATE = "corning_96_wellplate_360ul_flat"


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
    page.well_choice.setCurrentText("B2")
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
    assert page._rows[("5", PLATE)].value.text() == "Measured today"
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
    # Another well: the tip is not over it.
    page.well_choice.setCurrentText("C3")
    assert not page.save_button.isEnabled()
    assert not page.centered_button.isEnabled()
    page.deleteLater()


def test_the_step_lists_the_plates_with_their_state(app, session):
    session.load_labware(resolve_definition(PLATE), 2)
    page = _step(app, session)
    rows = page._rows
    assert set(rows) == {("2", PLATE), ("5", PLATE)}
    assert all(r.value.text() == "Not measured yet" for r in rows.values())
    page.select_plate(2, PLATE)
    page.skip_button.click()
    assert session.plate_skipped(2, PLATE)
    assert rows[("2", PLATE)].value.text() == \
        "Not measured: using the drawing"
    # On to the plate still to do.
    assert page._selected == ("5", PLATE)
    page.deleteLater()


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


def test_skip_is_done_for_the_well_center_step(app, session):
    from micropick.gui import tasks
    page = _step(app, session)
    assert not tasks.step_state(session, "liquid", "well_center").done
    page.skip_button.click()
    assert tasks.step_state(session, "liquid", "well_center").done
    assert not page.skip_button.isEnabled()
    page.deleteLater()


@pytest.mark.parametrize("task, skip", [("liquid", True),
                                        ("manual", False)])
def test_skip_is_only_in_liquid(app, session, task, skip):
    """#63: optional in Manual pick, where a plate not measured uses its
    drawing; Liquid asks about every plate."""
    page = _step(app, session)
    page.set_task(task)
    assert page.skip_button.isHidden() is not skip
    assert set(page._rows) == {("5", PLATE)}
    page.deleteLater()


def test_auto_shows_only_the_plans_plate(app, session):
    """#64: no Skip, and the plate the plan delivers to, as it changes."""
    from micropick.core.routine import Destination, Routine
    session.load_labware(resolve_definition(PLATE), 2)
    page = _step(app, session)
    page.set_task("auto")
    assert page.skip_button.isHidden()
    assert page._rows == {} and not page.no_plates.isHidden()
    session.set_routine(Routine(Destination.from_definition(
        resolve_definition(PLATE), slot=2), {"A1": 1}, name="plan"))
    assert set(page._rows) == {("2", PLATE)}
    assert page._selected == ("2", PLATE)
    page.deleteLater()


def test_measuring_again_keeps_the_depth_set_for_the_plate(app, session):
    page = _step(app, session)
    _measure(app, session, page, (0.4, -0.3, -1.5))
    session.set_well_depth(5, PLATE, 9.5)
    page.select_plate(5, PLATE)
    page.well_choice.setCurrentText("B2")
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


def test_liquids_well_center_step_is_this_page_and_skip_marks_it_done(
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
        page.skip_button.click()
        app.processEvents()
        assert tasks.step_state(session, "liquid", "well_center").done
        assert w.footer.next.isEnabled()
    finally:
        session.shutdown()
        w.deleteLater()


def test_manuals_well_center_step_is_this_page_without_skip(
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
        assert page.skip_button.isHidden()
        assert w.footer.next.isEnabled()
        w.open_task_step("liquid", "well_center")
        assert not page.skip_button.isHidden()
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
def test_a_plate_is_not_measured_yet_once_the_slots_module_changes(
        app, session, task):
    _plan_on_5(session)
    page = _step(app, session)
    page.set_task(task)
    _measure(app, session, page, (0.4, -0.3, -1.5))
    row = page._rows[("5", PLATE)]
    assert row.value.text() == "Measured today"
    session.set_deck_modules([_platform(64.2)])
    assert page._rows[("5", PLATE)].value.text() == "Not measured yet"
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
    assert not page.last_card.isHidden()
    page.select_plate(2, PLATE)
    assert page.last_card.isHidden()
    page.deleteLater()
