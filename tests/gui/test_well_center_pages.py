"""Measuring a plate's well center on Manual control and on Liquid's Well
center step: the same measurement, saved for the plate whichever page took
it, and read by Liquid handling."""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox          # noqa: E402

from micropick.config.labware import resolve_definition          # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages import liquid as liquid_page            # noqa: E402
from micropick.gui.pages import liquid_plan                      # noqa: E402
from micropick.gui.pages import well_center as step_page         # noqa: E402
from micropick.gui.pages.manual import ManualPage                # noqa: E402
from micropick.gui.session import Session                        # noqa: E402

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


def _manual(app, session):
    page = ManualPage(session)
    _settle(app, page.jog)
    page.plate_choice.setCurrentIndex(page.plate_choice.findText(
        next(page.plate_choice.itemText(i)
             for i in range(page.plate_choice.count())
             if "5" in page.plate_choice.itemText(i))))
    page.well_choice.setCurrentText("B2")
    return page


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
    if isinstance(page, step_page.WellCenterPage):
        page.move_button.click()
        _settle(app, page.jog)
        # The tip at the robot's well top: centering is next.
        assert page.centered_button.isEnabled()
        assert not page.save_button.isEnabled()
    else:
        page._go_to_well_top()
        _settle(app, page.jog)
        # The measurement in hand, said under the well center line.
        assert "At the top of B2" in page.center_state.text()
        assert page.set_center_button.isEnabled()


def _save(page):
    if isinstance(page, step_page.WellCenterPage):
        page.centered_button.click()
        page.level_button.click()
        page.save_button.click()
    else:
        page._set_well_center()


def _measure(app, session, page, jog):
    _go_to_top(app, page)
    at = tuple(page.jog.pose)
    _jog(app, session, page.jog, *jog)
    _save(page)
    return at


@pytest.mark.parametrize("make", [_manual],
                         ids=["manual control"])
def test_set_well_center_saves_the_jog_from_the_robots_well_top(
        app, session, make):
    page = make(app, session)
    _measure(app, session, page, (0.4, -0.3, -1.5))
    center = session.profile.deck.well_center(5, PLATE)
    assert center is not None and center.well == "B2"
    assert center.offset == pytest.approx([0.4, -0.3, -1.5], abs=0.05)
    assert center.depth_mm == pytest.approx(10.67)    # the definition's
    page.deleteLater()


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
    assert page.pad.keys.isHidden()                # no keys bound here
    page._show_pose((101.5, 52.25, 66.4))
    assert page.xy_readout.text() == "X 101.50   Y 52.25"
    assert page.z_readout.text() == "Z 66.40"
    page.deleteLater()


def test_skip_is_done_for_the_well_center_step(app, session):
    from micropick.gui import tasks
    page = _step(app, session)
    assert not tasks.step_state(session, "liquid", "well_center").done
    page.skip_button.click()
    assert tasks.step_state(session, "liquid", "well_center").done
    assert not page.skip_button.isEnabled()
    page.deleteLater()


@pytest.mark.parametrize("make", [_manual, _step],
                         ids=["manual control", "well center step"])
def test_measuring_again_keeps_the_depth_set_for_the_plate(app, session,
                                                           make):
    page = make(app, session)
    _measure(app, session, page, (0.4, -0.3, -1.5))
    session.set_well_depth(5, PLATE, 9.5)
    if make is _step:
        page.select_plate(5, PLATE)
        page.well_choice.setCurrentText("B2")
    _measure(app, session, page, (0.1, 0.2, -1.0))
    center = session.profile.deck.well_center(5, PLATE)
    assert center.offset == pytest.approx([0.1, 0.2, -1.0], abs=0.05)
    assert center.depth_mm == pytest.approx(9.5)
    page.deleteLater()


def test_a_center_measured_on_one_page_is_the_other_pages(app, session):
    manual = _manual(app, session)
    _measure(app, session, manual, (0.4, -0.3, -1.5))
    plate = liquid_plan.plates_for(session)["5"]
    assert plate.center == pytest.approx((0.4, -0.3, -1.5), abs=0.05)
    manual.deleteLater()


@pytest.mark.parametrize("make", [_manual], ids=["manual control"])
def test_a_center_a_run_uses_is_not_forgotten_under_it(app, session, make):
    page = make(app, session)
    _measure(app, session, page, (0.4, -0.3, -1.5))
    page._refresh()
    assert page.forget_center_button.isEnabled()
    run = session.robot_lease.claim("liquid run", run=True,
                                    plates={("5", PLATE)})
    try:
        app.processEvents()
        assert not page.forget_center_button.isEnabled()
        assert "liquid run" in page.forget_center_button.toolTip()
        if hasattr(page, "well_depth"):
            assert not page.well_depth.isEnabled()
        page._forget_well_center()
        assert session.profile.deck.well_center(5, PLATE) is not None
    finally:
        run.release()
    app.processEvents()
    assert page.forget_center_button.isEnabled()
    page.deleteLater()


def test_a_run_on_another_plate_leaves_this_ones_center_alone(app, session):
    page = _manual(app, session)
    _measure(app, session, page, (0.4, -0.3, -1.5))
    run = session.robot_lease.claim("liquid run", run=True,
                                    plates={("2", PLATE)})
    try:
        app.processEvents()
        page._refresh()
        # The robot is busy, so no Go to; but this plate is not the run's.
        assert page.forget_center_button.isEnabled()
    finally:
        run.release()
    page.deleteLater()


def test_a_liquid_run_says_which_plates_it_goes_into(app, session):
    from micropick.core.liquid import Location
    session.tip = session.tip.__class__(attached=True)
    from micropick.core.liquid import Group, Pause, Program
    page = _liquid(app, session)
    session.set_liquid_plan(Program(
        trash=Location(kind="well", slot="5", load_name=PLATE, well="H12"),
        groups=[Group(name="g", slot="5", load_name=PLATE, wells=["A1"],
                      steps=[Pause()])]))
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


@pytest.mark.parametrize("make", [_manual, _step],
                         ids=["manual control", "well center step"])
def test_the_measurement_in_hand_outlives_a_change_elsewhere_on_the_deck(
        app, session, make):
    page = make(app, session)
    _go_to_top(app, page)
    session.load_labware(resolve_definition(PLATE), 2)
    app.processEvents()
    if make is _step:
        assert page._selected == ("5", PLATE)
        assert page.centered_button.isEnabled()
    else:
        page._refresh()
        assert "At the top of B2" in page.center_state.text()
        assert page.set_center_button.isEnabled()
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


@pytest.mark.parametrize("make", [_manual, _step],
                         ids=["manual control", "well center step"])
def test_a_center_a_run_uses_is_not_replaced_under_it(app, session, make):
    page = make(app, session)
    _go_to_top(app, page)
    _jog(app, session, page.jog, 0.4, -0.3, -1.5)
    if make is _step:
        page.centered_button.click()
        page.level_button.click()
    run = session.robot_lease.claim("picking run", run=True,
                                    plates={("5", PLATE)})
    try:
        app.processEvents()
        if make is _step:
            assert not page.save_button.isEnabled()
            page._save()
        else:
            page._refresh()
            assert not page.set_center_button.isEnabled()
            page._set_well_center()
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
        session.confirm("deck")
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
