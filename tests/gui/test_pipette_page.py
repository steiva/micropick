"""The Tip step (`pages.calibration_pipette`, REDESIGN §6.2): a tip put on
from a rack on the deck, then the tip calibration as a run on a path - Go to
the disc, Yes, calibrate, Adjust by hand - whose touch-up arrows follow the
lower camera."""

import os
import sys
import threading
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt                                    # noqa: E402
from PySide6.QtTest import QTest                                 # noqa: E402
from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.config.labware import resolve_definition          # noqa: E402
from micropick.config.schema import PipetteOffset                # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages import calibration_pipette as tip_page  # noqa: E402
from micropick.gui.pages.calibration_pipette import (            # noqa: E402
    PipetteCalibration)
from micropick.gui.readiness import (CALIBRATION_POSITION,       # noqa: E402
                                     tip_calibration_status)
from micropick.gui.session import Session, Tip                   # noqa: E402
from micropick.gui.widgets.jog_panel import view_move            # noqa: E402
from micropick.gui.widgets.path_steps import (CURRENT, DONE,     # noqa: E402
                                              LATER)
from micropick.gui.widgets.toast import toast_of                 # noqa: E402
from micropick.hardware.protocols import move_to                 # noqa: E402

RACK = "opentrons_96_tiprack_300ul"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def page(app, tmp_path, monkeypatch, robot_free):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    s.load_profile("mock")
    s.probe_robot()
    s.new_run()
    p = PipetteCalibration(s)
    # made, its panels read the pose; a job meanwhile is refused
    robot_free(s, p.jog, p.touch_jog)
    yield p
    p.deleteLater()
    s.shutdown()


def _rack(page, slot=10):
    page.session.load_labware(resolve_definition(RACK), slot)
    page._refresh()


def _tip_on(until, page, well="A1"):
    _rack(page)
    page.rack_grid.well_clicked.emit(well)
    # a take while the panel reads the pose is refused, never queued; the
    # panel's job is over once its end is delivered here, after the lease
    until(lambda: not page.session.robot_lease.busy
          and not page.jog.job_pending, what="the panel free")
    page.take_button.click()
    until(lambda: page.session.tip.attached is True
          and not page.jog.job_pending, what="the tip on")


# -- put a tip on -------------------------------------------------------------

def test_no_tip_box_on_the_deck_says_so_and_goes_back_to_the_deck(page):
    assert not page.no_rack.isHidden()
    assert "no tip box on the deck" in page.no_rack.label.text()
    assert page.take_button.isHidden()
    asked = []
    page.session.page_requested.connect(asked.append)
    page.back_to_deck.click()
    assert asked == ["labware"]


def test_the_tip_boxes_on_the_deck_by_name_and_slot(page):
    _rack(page, 10)
    _rack(page, 7)
    assert page.no_rack.isHidden()
    labels = [page.racks.itemText(i) for i in range(page.racks.count())]
    name = resolve_definition(RACK).display_name
    assert labels == [f"{name} · slot 7", f"{name} · slot 10"]
    assert page.rack_grid.wells and len(page.rack_grid.wells) == 96


def test_the_title_and_icon_follow_the_robots_answer(until, app, page):
    _rack(page)
    assert page.tip_state.text() == "No tip on"
    assert not page.tip.icon.on
    page.take_button.click()
    until(lambda: page.tip_state.text() == "Tip A1 is on"
          and not page.jog.job_pending, what="the tip on and shown")
    assert page.tip.icon.on
    page.session.tip = Tip.unknown()
    page.tip.refresh()
    assert page.tip_state.text() == "Tip state unknown."
    assert not page.tip.icon.on


def test_a_tip_from_nowhere_known_is_still_a_tip_on(page):
    page.session.tip = Tip(attached=True)
    page.tip.refresh()
    assert page.tip_state.text() == "A tip is on"
    assert page.tip.icon.on


def test_one_tip_box_is_plain_text_several_a_select(page):
    name = resolve_definition(RACK).display_name
    _rack(page, 10)
    assert page.tip.rack_line.currentWidget() is page.tip.rack_name
    assert page.tip.rack_name.text() == f"{name} · slot 10"
    _rack(page, 7)
    assert page.tip.rack_line.currentWidget() is page.racks


def test_a_click_on_the_rack_chooses_the_tip_to_take(until, app, page):
    _rack(page)
    assert page.take_button.text() == "Pick up A1"     # the first not used
    width = page.take_button.width()
    page.rack_grid.well_clicked.emit("F12")
    assert page.take_button.text() == "Pick up F12"
    assert page.rack_grid._current == "F12"              # drawn as next
    assert page.take_button.width() == width             # fixed
    page.rack_grid.well_clicked.emit("A3")
    page.take_button.click()
    until(lambda: page.tip_state.text() == "Tip A3 is on"
          and not page.jog.job_pending, what="the tip on and shown")
    assert (page.session.tip.slot, page.session.tip.well) == ("10", "A3")
    assert page.drop_button.text() == "Drop in trash"
    assert page.drop_here_button.text() == "Drop in place"
    assert not page.drop_button.isHidden()
    assert not page.drop_here_button.isHidden()
    assert not hasattr(page, "put_back_button")
    assert page.take_button.isHidden()


def test_pick_up_is_as_wide_for_any_tip(page):
    _rack(page)
    width = page.take_button.width()
    for well in ("A1", "H12"):
        page.rack_grid.well_clicked.emit(well)
        assert page.take_button.width() == width
    assert page.take_button.fontMetrics().horizontalAdvance(
        "Pick up H12") < width


def test_the_rack_folds_away_with_a_tip_on(until, page):
    fold = page.tip.rack_fold
    _rack(page)
    assert fold.isHidden()                           # no tip on: always shown
    assert not page.rack_grid.isHidden()
    _tip_on(until, page)
    assert not fold.isHidden() and fold.text() == "Show rack"
    assert page.tip.rack_stack.isHidden()
    fold.click()
    assert fold.text() == "Hide rack"
    assert not page.tip.rack_stack.isHidden()
    assert not page.rack_grid.isHidden()
    fold.click()
    assert fold.text() == "Show rack"
    assert page.tip.rack_stack.isHidden()


def test_the_rack_is_folded_again_at_the_next_tip(until, app, page):
    _tip_on(until, page, "A1")
    page.tip.rack_fold.click()
    page.drop_here_button.click()
    until(lambda: page.session.tip.attached is False
          and not page.jog.job_pending)
    assert page.tip.rack_fold.isHidden()
    assert not page.tip.rack_stack.isHidden()
    page.take_button.click()
    until(lambda: page.session.tip.attached is True
          and not page.jog.job_pending)
    assert page.tip.rack_stack.isHidden()
    assert page.tip.rack_fold.text() == "Show rack"


def test_the_rack_shown_with_a_tip_on_takes_no_choice(until, app, page):
    _tip_on(until, page, "A1")
    page.resize(1920, 860)
    page.show()
    page.tip.rack_fold.click()
    app.processEvents()
    grid = page.rack_grid
    assert grid._current == "B1"
    point = grid.well_rect("F12").center().toPoint()
    QTest.mouseClick(grid, Qt.MouseButton.LeftButton, pos=point)
    app.processEvents()
    assert grid._current == "B1"
    page.hide()


def test_a_tip_on_with_no_tip_box_has_no_rack_to_show(app, page):
    page.session.tip = Tip(attached=True)
    page.tip.refresh()
    assert page.tip.rack_fold.isHidden()
    assert page.tip.rack_stack.isHidden()
    assert not page.drop_button.isHidden()


def test_a_tip_taken_says_so_in_a_toast(until, app, page):
    page.show()
    _tip_on(until, page, "A3")
    until(lambda: toast_of(page) is not None
          and toast_of(page).text() == "Tip A3 is on.")
    page.hide()


@pytest.mark.parametrize("button", ["drop_button", "drop_here_button"])
def test_a_tip_dropped_says_calibration_needs_a_new_one(until, app, page, button):
    page.show()
    _tip_on(until, page, "A3")
    getattr(page, button).click()
    until(lambda: page.session.tip.attached is False
          and not page.jog.job_pending)
    until(lambda: toast_of(page).text()
          == "Tip dropped. Calibration needs a new one.")
    page.hide()


def test_the_drops_are_gray_while_the_robot_is_busy(until, app, page):
    _tip_on(until, page)
    assert page.drop_button.isEnabled() and page.drop_here_button.isEnabled()
    lease = page.session.robot_lease.claim("a test")
    until(lambda: not page.drop_button.isEnabled())
    assert not page.drop_here_button.isEnabled()
    lease.release()
    until(lambda: page.drop_button.isEnabled())


def test_a_refused_drop_is_a_toast(until, app, page):
    page.show()
    _tip_on(until, page)
    lease = page.session.robot_lease.claim("a test")
    page.tip._drop_in_trash()
    assert toast_of(page).text() == page.session.robot_lease.refusal
    assert page.session.tip.attached is True
    lease.release()
    page.hide()


def test_a_drop_while_the_panel_works_is_refused_in_a_toast(until, app, page):
    page.show()
    _tip_on(until, page)
    gate = threading.Event()
    assert page.jog.run_job(lambda _log: gate.wait(5), what="a test")
    page.tip._drop_in_trash()
    assert toast_of(page).text() == "Robot busy."
    gate.set()
    until(lambda: not page.jog.job_pending)
    assert page.session.tip.attached is True
    page.hide()


def test_no_robot_is_no_filled_tip(page):
    page.session.tip = Tip(attached=True)
    page.session.robot = None
    page.tip.refresh()
    assert page.tip_state.text() == "Tip state unknown."
    assert not page.tip.icon.on


def test_drop_in_trash_uses_the_tip(until, app, page):
    _tip_on(until, page, "A3")
    page.drop_button.click()
    until(lambda: page.session.tip.attached is False
          and not page.jog.job_pending)
    assert page.session.used_tips(10) == {"A3"}
    assert page.take_button.text() == "Pick up A1"


def test_drop_in_place_lets_go_of_the_tip_without_moving(until, app, page):
    _tip_on(until, page, "A3")
    robot = page.session.robot
    before = len(robot.calls)
    page.drop_here_button.click()
    until(lambda: page.session.tip.attached is False
          and not page.jog.job_pending)
    names = [call[0] for call in robot.calls[before:]]
    assert "drop_tip_in_place" in names
    assert not any(n.startswith("move") for n in names)
    assert page.session.used_tips(10) == {"A3"}


def test_the_tip_step_is_one_column(app, page):
    page.resize(3000, 900)              # room for two columns
    page.show()
    app.processEvents()
    left = [w.mapTo(page, w.rect().topLeft())
            for w in (page.tip.box, page.calibration_box)]
    assert left[0].x() == left[1].x()
    assert left[0].y() < left[1].y()
    page.hide()


def test_the_rack_waits_for_a_run(app, page):
    _rack(page)
    lease = page.session.robot_lease.claim("picking run", run=True)
    try:
        page._refresh()
        assert not page.take_button.isEnabled()
        before = list(page.session.robot.calls)
        page.take_button.click()
        app.processEvents()
        assert page.session.robot.calls == before
    finally:
        lease.release()
    page._refresh()
    assert page.take_button.isEnabled()


# -- calibrate the tip -------------------------------------------------------

def _states(page):
    return [page.path.state(key) for key in ("go", "check", "find")]


def test_with_no_tip_the_card_is_dimmed_and_takes_no_click(until, app, page):
    box = page.calibration_box
    assert not box.isHidden() and not box.isEnabled()
    assert box.graphicsEffect().isEnabled()
    assert box.graphicsEffect().opacity() == pytest.approx(0.55)
    assert page.calibration.first_tip.text() == "Put a tip on first."
    assert not page.calibration.first_tip.isHidden()
    assert _states(page) == [LATER, LATER, LATER]
    assert page.chip.text() == "Not done"
    _tip_on(until, page)
    assert box.isEnabled() and not box.graphicsEffect().isEnabled()
    assert page.calibration.first_tip.isHidden()
    assert _states(page) == [CURRENT, LATER, LATER]


def test_the_path_has_no_numbers(page):
    titles = [page.path.title(key) for key in ("go", "check", "find")]
    assert titles == ["Camera over the disc", "Disc in the picture",
                      "Find the tip"]
    assert not any(c.isdigit() for title in titles for c in title)


def test_what_stops_a_start_is_said_in_the_current_item(until, app, page):
    _tip_on(until, page)
    assert page.path.current == "go"
    assert not page.go_button.isEnabled()
    assert page.checks.text() == page._readiness()[0]


def test_the_cards_stand_in_one_column_of_440(app, page):
    page.resize(1920, 860)
    page.show()
    app.processEvents()
    assert page.calibration_box.width() == 440
    assert page.tip.box.width() == 440
    page.hide()


def test_liquid_needs_no_calibration(page):
    page.set_task("liquid")
    assert page.calibration_box.isHidden()
    page.set_task("auto")
    assert not page.calibration_box.isHidden()


@pytest.fixture
def ready(until, app, page, monkeypatch):
    """A tip on, a calibration position, and the routine replaced by one
    that puts the tip on the crosshair at Settings' height and saves an
    offset for this tip. `ready["gate"]` cleared holds it measuring."""
    session = page.session
    _tip_on(until, page)
    session.remember(CALIBRATION_POSITION, (150.0, 150.0, 80.0))
    seen = {"gate": threading.Event()}
    seen["gate"].set()

    def calibrate(robot, over, under, detector, pmap, *, profile, height,
                  current_offset, manual_touch_up, robot_session,
                  tip_pickup, **_kw):
        seen["start"] = current_offset
        seen["touch_up"] = manual_touch_up
        seen["gate"].wait(10)
        x, y, _z = tip_page.xyz(robot)
        move_to(robot, (x, y, height), force_direct=True)
        offset = PipetteOffset(dx=1.5, dy=-2.5, robot_session=robot_session,
                               tip_pickup=tip_pickup, method="auto")
        profile.calibration.pipette_offset = offset
        return SimpleNamespace(offset=offset)

    monkeypatch.setattr(tip_page, "calibrate_pipette_offset", calibrate)
    monkeypatch.setattr(tip_page, "load_tip_detector",
                        lambda *a, **k: object())
    monkeypatch.setattr(tip_page.PixelMap, "from_config",
                        staticmethod(lambda _config: None))
    monkeypatch.setattr(page, "_readiness", lambda: [])
    session.settings = session.settings.model_copy(
        update={"tip_cal_touch_up": True})
    page._refresh()
    return seen


def _calibrate(until, page):
    page.go_button.click()
    until(lambda: page.yes_button.isEnabled())
    page.yes_button.click()
    until(lambda: not page.session.robot_lease.busy
          and not page.calibration.running and not page.jog.job_pending)


def _adjust(until, page):
    until(lambda: page.adjust_button.isEnabled())
    page.adjust_button.click()
    assert page.touch_jog.isVisibleTo(page)
    # shown, the panel reads the pose; a step then would be refused
    until(lambda: not page.touch_jog.job_pending
          and not page.session.robot_lease.busy)


def test_the_drops_are_gray_while_a_run_waits_at_the_disc(until, app, page, ready):
    page.go_button.click()
    until(lambda: page.yes_button.isEnabled())
    assert page.run_activity() == "waiting"
    assert not page.drop_button.isEnabled()
    assert not page.drop_here_button.isEnabled()
    page.session.stop_requested.emit()
    until(lambda: not page.session.robot_lease.busy
          and not page.calibration.running and not page.jog.job_pending)
    assert page.drop_button.isEnabled()


def test_two_clicks_calibrate_save_at_once_and_end_the_run(until, app, page, ready):
    session = page.session
    assert page.chip.text() == "Not done"
    page.go_button.click()
    # A run: the robot's lease from Go to the end, the pill's name.
    assert session.robot_lease.holding_run
    assert session.robot_lease.holder == "pipette calibration"
    assert page.path.current == "go"                 # until the move ends
    assert page.chip.text() == "In progress"
    # The camera went to the calibration position; the disc is checked.
    until(lambda: page.yes_button.isEnabled())
    assert _states(page) == [DONE, CURRENT, LATER]
    assert page.run_activity() == "waiting"
    ready["gate"].clear()
    page.yes_button.click()
    # Measuring: a bar that only turns, nothing to wait for the operator;
    # what the routine does goes to the log only.
    until(lambda: page.spinner.isVisibleTo(page))
    assert _states(page) == [DONE, DONE, CURRENT]
    assert page.spinner.maximum() == 0
    assert page.run_activity() is None
    ready["gate"].set()
    # The lease is let go on the worker's thread, before its end arrives.
    until(lambda: not session.robot_lease.busy
          and not page.calibration.running)
    assert ready["touch_up"] is None                 # no fine-tune in the run
    assert ready["start"] == session.settings.tip_cal_start_offset
    assert not page.spinner.isVisibleTo(page)
    assert tip_calibration_status(session)[0]
    assert _states(page) == [DONE, DONE, DONE]
    assert page.path.title("find") == "Tip found"
    assert page.chip.text() == "Done"
    assert page.path.link("find").text() == "Redo"
    assert page.path.link("find").isEnabled()
    # Next on the path, offered and nothing more.
    assert page.path.current == "fine"


def test_yes_calibrate_confirms_then_calibrates_once_each(until, app, page, ready):
    block, calls = page.calibration, []
    there, calibrate = block._disc_there, block._calibrate
    block._disc_there = lambda: (calls.append("disc"), there())
    block._calibrate = lambda: (calls.append("calibrate"), calibrate())
    _calibrate(until, page)
    assert calls == ["disc", "calibrate"]


def test_dropping_the_tip_resets_the_path(until, app, page, ready):
    session = page.session
    _calibrate(until, page)
    page.drop_button.click()
    until(lambda: session.tip.attached is False
          and not page.jog.job_pending)
    assert _states(page) == [LATER, LATER, LATER]
    assert page.chip.text() == "Not done"
    assert page.footer_line() == ("Put a tip on, then calibrate it.", False)


def test_coming_back_with_a_valid_calibration_shows_the_path_at_its_end(until, 
        app, page, ready):
    _calibrate(until, page)
    again = PipetteCalibration(page.session)
    assert [again.path.state(k) for k in ("go", "check", "find")] == [DONE] * 3
    assert again.chip.text() == "Done"
    assert again.footer_line() == ("Tip calibrated.", True)
    again.deleteLater()


def test_the_footer_says_where_the_tip_is_in_each_task(until, app, page):
    for task in ("auto", "manual"):
        page.set_task(task)
        assert page.footer_line() == ("Put a tip on, then calibrate it.",
                                      False)
    page.set_task("liquid")
    assert page.footer_line() == ("Put a tip on.", False)
    _tip_on(until, page)
    assert page.footer_line() == ("", False)
    page.set_task("auto")
    assert page.footer_line() == ("Calibrate the tip to go on.", False)


def test_the_fine_tune_saves_the_offset_once_the_tip_was_moved(until, 
        app, page, ready):
    session = page.session
    _calibrate(until, page)
    crosshair = page._crosshair
    until(lambda: not page.touch_jog.job_pending)
    _adjust(until, page)
    assert not page.tune_save_button.isEnabled()      # nothing moved yet
    page.touch_jog.step("x", +1)
    until(lambda: not page.touch_jog.job_pending
          and page.tune_save_button.isEnabled())
    page.tune_save_button.click()
    # A short job, not a run.
    assert not session.robot_lease.holding_run
    until(lambda: not page.touch_jog.job_pending
          and not session.robot_lease.busy)
    x, y, _z = tip_page.xyz(session.robot)
    offset = session.profile.calibration.pipette_offset
    assert (offset.dx, offset.dy) == pytest.approx(
        (x - crosshair[0], y - crosshair[1]), abs=1e-6)
    assert (offset.dx, offset.dy) != (1.5, -2.5)
    assert offset.method == "auto+manual"
    assert tip_calibration_status(session)[0]         # same tip, same session
    assert not page.tune_save_button.isEnabled()      # saved: nothing new
    assert not page.touch_jog.isVisibleTo(page)       # offered again
    assert page.adjust_button.isVisibleTo(page)


def test_the_fine_tune_says_when_the_tip_is_away_from_the_crosshair(until, 
        app, page, ready):
    _calibrate(until, page)
    motion = page.session.motion
    assert page.jog.run_job(lambda log: motion.tip_to((100.0, 100.0), 90.0,
                                                      log=log))
    until(lambda: not page.jog.job_pending)
    app.processEvents()
    assert page.path.current == "fine"
    assert not page.adjust_button.isEnabled()
    assert page.tune_away.isVisibleTo(page)
    assert page.tune_away.text() == "The tip is not over the crosshair."
    assert not page.touch_jog.isVisibleTo(page)


def test_the_fine_tune_follows_the_lower_camera_in_xy_only(until, app, page, ready):
    _calibrate(until, page)
    _adjust(until, page)
    jog = page.touch_jog
    axes = page.session.profile.calibration.tip_target.axes
    assert jog._view_axes == tuple(tuple(r) for r in axes)
    assert view_move(jog._view_axes, "x", +1) == ("y", +1)
    assert jog._xy_only
    assert not any("PgUp" in line for line in jog.help_lines)


def test_the_movement_keys_move_the_tip_in_the_fine_tune(until, app, page, ready):
    """The fine-tune's pad has the keys (#57), the host's none."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    session = page.session
    _calibrate(until, page)
    page.show()
    page.activateWindow()
    assert QTest.qWaitForWindowActive(page)
    until(lambda: not page.touch_jog.job_pending and not page.jog.job_pending)
    _adjust(until, page)
    x0, y0, _z = tip_page.xyz(session.robot)
    QTest.keyClick(page, Qt.Key.Key_Right)       # right on the picture
    until(lambda: tip_page.xyz(session.robot)[1] != y0)
    until(lambda: not page.touch_jog.job_pending)
    x1, y1, _z = tip_page.xyz(session.robot)
    # Through TipTarget.axes: right on the picture is robot +Y, one step.
    assert y1 - y0 == pytest.approx(page.touch_jog.pad.step, abs=0.02)
    assert abs(x1 - x0) < 0.1
    page.hide()


def test_the_pages_arrows_follow_the_lower_camera_when_it_is_shown(until, 
        app, page, ready):
    """The calibration leaves the tip over the crosshair, seen from below,
    with the fine-tune folded: the page's own pad moves the tip the way
    its arrows point on that turned and mirrored picture."""
    session = page.session
    _calibrate(until, page)
    session.settings.tip_cal_touch_up = False         # no fine-tune pad
    page.calibration.refresh()
    assert page.view._camera is page.calibration._under_camera()
    assert page.jog.isVisibleTo(page)
    axes = session.profile.calibration.tip_target.axes
    assert page.jog._view_axes == tuple(tuple(r) for r in axes)
    x0, y0, _z = tip_page.xyz(session.robot)
    page.jog.step("x", -1)                       # left on the picture
    until(lambda: not page.jog.job_pending)
    x1, y1, _z = tip_page.xyz(session.robot)
    # Mock axes: left on the picture is robot -Y; X stays (the mock's pose
    # wanders by a few hundredths).
    assert y1 < y0 - 0.5 and abs(x1 - x0) < 0.1
    # Away from the crosshair the picture is the upper camera's, whose
    # arrows are the robot's own.
    motion = session.motion
    assert page.jog.run_job(lambda log: motion.tip_to((100.0, 100.0), 90.0,
                                                      log=log))
    until(lambda: not page.jog.job_pending)
    assert page.view._camera is page.calibration._over_camera()
    assert page.jog._view_axes is None


def test_leaving_the_step_raises_the_tip_to_the_travel_height(until, 
        app, page, ready):
    """The calibration leaves the tip low over the crosshair; going to
    another step takes it up, under the robot's lease."""
    session = page.session
    page.show()
    _calibrate(until, page)
    assert tip_page.xyz(session.robot)[2] < session.motion.travel_z
    before = len(session.robot.calls)
    page.hide()
    # the page's job: waited for by what it does, since an instant mock
    # move can end it before a look at the lease
    until(lambda: tip_page.xyz(session.robot)[2]
          >= session.motion.travel_z - 0.05)
    until(lambda: not page.jog.job_pending
          and not session.robot_lease.busy)
    assert any(c[0].startswith(("move", "retract"))
               for c in session.robot.calls[before:])


def test_the_panel_is_free_again_after_the_tip_was_raised(until, app, page, ready):
    """The raise ends the panel's job like any other: the number `raise_tip`
    returns is not what a job may return, and it broke the job's end before
    `moved`."""
    _tip_on(until, page)
    errors = []
    sys.excepthook, hook = lambda *e: errors.append(e), sys.excepthook
    moved = []
    page.jog.moved.connect(lambda: moved.append(True))
    try:
        page.show()
        # shown, the panel reads the pose; a raise then would be refused
        until(lambda: not page.jog.job_pending
              and not page.session.robot_lease.busy)
        page.hide()
        until(lambda: moved or errors)
    finally:
        sys.excepthook = hook
    assert not errors


def test_leaving_the_step_moves_nothing_while_the_robot_is_held(until, 
        app, page, ready):
    session = page.session
    page.show()
    _calibrate(until, page)
    z = tip_page.xyz(session.robot)[2]
    lease = session.robot_lease.claim("picking run", run=True)
    try:
        before = len(session.robot.calls)
        page.hide()
        app.processEvents()
        assert session.robot.calls[before:] == []
    finally:
        lease.release()
    assert tip_page.xyz(session.robot)[2] == pytest.approx(z, abs=0.05)


def test_leaving_the_step_high_moves_nothing(until, app, page, ready):
    session = page.session
    page.show()
    # shown, the panel reads the pose under the lease; a job before is refused
    until(lambda: not session.robot_lease.busy
          and not page.jog.job_pending)
    motion = session.motion

    def job(log):
        motion.raise_tip(log=log)

    assert page.jog.run_job(job)
    until(lambda: not page.jog.job_pending)
    page.jog.refresh_position()
    # the pose reaches the page by a signal after the job lets go
    until(lambda: page.calibration._pose is not None
          and page.calibration._pose[2] >= motion.travel_z - 0.05)
    until(lambda: not page.jog.job_pending)
    before = len(session.robot.calls)
    page.hide()
    app.processEvents()
    assert not session.robot_lease.busy
    assert not any(c[0].startswith(("move", "retract"))
                   for c in session.robot.calls[before:])


def test_redo_goes_back_through_the_disc(until, app, page, ready):
    session = page.session
    _calibrate(until, page)
    page.path.link("find").click()
    assert session.robot_lease.holding_run
    until(lambda: page.yes_button.isEnabled())
    assert _states(page) == [DONE, CURRENT, LATER]
    assert page.path.link("find").isHidden()         # no link while it waits
    page.yes_button.click()
    until(lambda: not session.robot_lease.busy
          and not page.calibration.running)
    assert tip_calibration_status(session)[0]


def test_after_a_restart_the_crosshair_is_the_pixel_maps(until, 
        app, page, ready):
    from micropick.config.schema import PixelMap as PixelMapConfig
    from micropick.core.calibration.pixel_map import PixelMap
    w, h, k = 1600, 1200, 0.01
    page.session.profile.calibration.pixel_map = PixelMapConfig(   # a toy
        degree=1, cu=w / 2, cv=h / 2, s=w / 2,
        coef=[[0.0, k * w / 2], [k * w / 2, 0.0]], zero=[0.0, 0.0],
        ref=[w / 2, h / 2], bounds=[100.0, 100.0, w - 100.0, h - 100.0],
        image_size=[w, h], sweep_z=0.0)
    _calibrate(until, page)
    page._crosshair = None                   # as a page of a new start
    pmap = PixelMap(page.session.profile.pixel_map)
    gx, gy = pmap.to_robot(pmap.config.cu, pmap.config.cv, (150.0, 150.0))
    assert page.calibration._crosshair_xy() == pytest.approx((gx, gy))
    # The tip over it plus the offset: Adjust by hand can be used.
    module = page.session.settings.tip_cal_height_mm
    motion = page.session.motion
    assert page.jog.run_job(lambda log: motion.tip_to(
        (gx + 1.5, gy - 2.5), module, log=log))
    until(lambda: not page.jog.job_pending)
    page.calibration.refresh()
    _adjust(until, page)


def _toasts(monkeypatch):
    said = []
    monkeypatch.setattr(tip_page, "show_toast",
                        lambda _where, text, **_kw: said.append(text))
    return said


def test_stop_at_the_disc_ends_the_run_with_nothing_saved(until, 
        app, page, ready, monkeypatch):
    session = page.session
    said = _toasts(monkeypatch)
    page.go_button.click()
    until(lambda: page.yes_button.isEnabled())
    session.stop_requested.emit()
    until(lambda: not session.robot_lease.busy
          and not page.calibration.running)
    assert said == ["Stopped. Nothing was saved."]
    assert session.profile.calibration.pipette_offset is None
    assert _states(page) == [CURRENT, LATER, LATER]
    assert page.go_button.isEnabled()
    assert page.calibration.failure.isHidden()


def test_stop_at_the_disc_goes_to_the_end_while_a_calibration_holds(until, 
        app, page, ready):
    session = page.session
    _calibrate(until, page)
    offset = session.profile.calibration.pipette_offset
    page.path.link("find").click()
    until(lambda: page.yes_button.isEnabled())
    session.stop_requested.emit()
    until(lambda: not session.robot_lease.busy
          and not page.calibration.running)
    assert session.profile.calibration.pipette_offset is offset
    assert _states(page) == [DONE, DONE, DONE]
    assert page.chip.text() == "Done"


def test_a_first_stop_while_finding_the_tip_changes_nothing(until, app, page, ready):
    session = page.session
    page.go_button.click()
    until(lambda: page.yes_button.isEnabled())
    ready["gate"].clear()
    page.yes_button.click()
    until(lambda: page.path.current == "find")
    session.stop_requested.emit()
    app.processEvents()
    assert page.path.current == "find" and session.robot_lease.busy
    ready["gate"].set()
    until(lambda: not session.robot_lease.busy
          and not page.calibration.running)
    assert tip_calibration_status(session)[0]        # it ended and saved


def test_a_failure_is_a_red_banner_with_a_sentence(until, 
        app, page, ready, monkeypatch):
    def fails(*_a, **_k):
        raise RuntimeError("the tip was not seen")

    monkeypatch.setattr(tip_page, "calibrate_pipette_offset", fails)
    page.go_button.click()
    until(lambda: page.yes_button.isEnabled())
    page.yes_button.click()
    until(lambda: not page.calibration.running)
    failure = page.calibration.failure
    assert not failure.isHidden() and failure.tone == "danger"
    assert "Nothing was saved." in failure.label.text()
    assert _states(page) == [CURRENT, LATER, LATER]
    page.go_button.click()                           # gone with a new start
    assert failure.isHidden()
    until(lambda: page.yes_button.isEnabled())
    page.session.stop_requested.emit()
    until(lambda: not page.calibration.running)


# -- the cards as blocks in another widget ------------------------------------

def test_the_two_cards_work_embedded_in_another_widget(
        until, robot_free, app, tmp_path, monkeypatch):
    """What a Robot setup sheet does with them (#34): a plain widget, its
    own picture and jog panel, the blocks' boxes in its layout."""
    from PySide6.QtWidgets import QVBoxLayout, QWidget
    from micropick.gui.widgets.camera_view import CameraView
    from micropick.gui.widgets.jog_panel import JogPanel
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    session = Session(Options(mock=True))
    session.load_profile("mock")
    session.probe_robot()
    session.new_run()
    host = QWidget()
    view = CameraView(host)
    jog = JogPanel(session, shortcut_host=host,
                   parent=host)
    calibration = tip_page.TipCalibrationBlock(
        session, jog, view, holder="shake tip calibration", parent=host)
    tip = tip_page.TipOnBlock(session, jog, calibration=calibration,
                              parent=host)
    layout = QVBoxLayout(host)
    for widget in (view, tip.box, calibration.box, jog):
        layout.addWidget(widget)
    changes = []
    tip.changed.connect(lambda: changes.append("tip"))
    calibration.calibrated.connect(lambda: changes.append("calibrated"))
    try:
        assert not tip.done and not calibration.done
        assert not calibration.box.isEnabled()
        session.load_labware(resolve_definition(RACK), 10)
        assert tip.take_button.text() == "Pick up A1"
        robot_free(session, jog)          # made, the panel reads the pose
        tip.take_button.click()
        until(lambda: tip.done and not jog.job_pending)
        assert "tip" in changes
        assert calibration.box.isEnabled()

        session.remember(CALIBRATION_POSITION, (150.0, 150.0, 80.0))
        _fake_routine(monkeypatch)
        monkeypatch.setattr(calibration, "_readiness", lambda: [])
        calibration.refresh()
        calibration.go_button.click()
        # A run under its own holder's name, from Start to the end.
        assert session.robot_lease.holding_run
        assert session.robot_lease.holder == "shake tip calibration"
        assert not tip.take_button.isEnabled()
        until(lambda: calibration.yes_button.isEnabled())
        assert calibration.run_activity() == "waiting"
        calibration.yes_button.click()
        # let go on the worker's thread, before the block knows
        until(lambda: not session.robot_lease.busy
              and not calibration.running)
        assert calibration.done
        assert "calibrated" in changes
        assert calibration.chip.text() == "Done"
        # Folded is the Tip page's rule: a host keeps its pad.
        assert not jog.pad.folded and jog.isVisibleTo(host)
    finally:
        host.deleteLater()
        session.shutdown()


def _fake_routine(monkeypatch):
    """The routine replaced by one that saves an offset for this tip,
    with no detector and no pixel map."""
    def calibrate(robot, over, under, detector, pmap, *, profile,
                  current_offset, manual_touch_up, robot_session,
                  tip_pickup, **_kw):
        offset = PipetteOffset(dx=1.5, dy=-2.5, robot_session=robot_session,
                               tip_pickup=tip_pickup)
        profile.calibration.pipette_offset = offset
        return SimpleNamespace(offset=offset)

    monkeypatch.setattr(tip_page, "calibrate_pipette_offset", calibrate)
    monkeypatch.setattr(tip_page, "load_tip_detector",
                        lambda *a, **k: object())
    monkeypatch.setattr(tip_page.PixelMap, "from_config",
                        staticmethod(lambda _config: None))


def test_a_fine_tune_not_saved_is_asked_about_when_leaving(until, app, page, ready):
    _calibrate(until, page)
    assert page.unsaved() == ""
    _adjust(until, page)
    page.touch_jog.step("x", +1)
    until(lambda: not page.touch_jog.job_pending
          and page.tune_save_button.isEnabled())
    assert page.unsaved() == "Tip calibration: the fine-tune is not saved."
    page.discard_unsaved()
    assert page.unsaved() == ""
    assert not page.tune_save_button.isEnabled()
    assert not page.touch_jog.isVisibleTo(page)


# -- adjust by hand (#123) ----------------------------------------------------

def test_after_a_calibration_adjust_by_hand_is_offered_and_opens_controls(until, 
        app, page, ready):
    _calibrate(until, page)
    assert page.path.current == "fine"
    assert page.path.title("fine") == "Adjust by hand · optional"
    assert page.adjust_button.isEnabled()
    # Offered, not begun: the page's own pad, no fine-tune's.
    assert not page.touch_jog.isVisibleTo(page)
    assert page.jog.isVisibleTo(page)
    assert not page.tune_save_button.isVisibleTo(page)

    page.adjust_button.click()
    assert page.touch_jog.isVisibleTo(page)
    assert not page.jog.isVisibleTo(page)            # one pad, one set of keys
    assert not page.touch_jog.pad.folded
    assert page.touch_jog.pad.step == pytest.approx(0.05)
    assert page.touch_jog._xy_only
    assert not page.adjust_button.isVisibleTo(page)
    assert page.tune_save_button.isVisibleTo(page)
    assert page.tune_cancel_button.isVisibleTo(page)


def test_cancel_saves_nothing_and_leaves_the_tip_where_it_is(until, app, page, ready):
    session = page.session
    _calibrate(until, page)
    offset = session.profile.calibration.pipette_offset
    _adjust(until, page)
    page.touch_jog.step("x", +1)
    until(lambda: not page.touch_jog.job_pending
          and page.tune_save_button.isEnabled())
    pose = tip_page.xyz(session.robot)
    before = len(session.robot.calls)
    page.tune_cancel_button.click()
    assert session.profile.calibration.pipette_offset is offset
    assert page.unsaved() == ""
    assert not page.touch_jog.isVisibleTo(page)
    assert page.adjust_button.isVisibleTo(page)
    assert not any(c[0].startswith(("move", "retract"))
                   for c in session.robot.calls[before:])
    assert tip_page.xyz(session.robot) == pytest.approx(pose, abs=0.05)


def test_with_the_setting_off_the_path_ends_on_tip_found(until, app, page, ready):
    session = page.session
    session.settings.tip_cal_touch_up = False
    page.calibration.refresh()
    _calibrate(until, page)
    assert page.path._rows["fine"].box.isHidden()
    assert page.path.current is None
    assert _states(page) == [DONE, DONE, DONE]
    assert page.path.link("find").isVisibleTo(page)
    session.settings.tip_cal_touch_up = True
    page.calibration.refresh()
    assert page.path.current == "fine"


def test_controls_start_folded_and_the_keys_still_work(until, app, page, ready):
    session = page.session
    assert page.jog.pad.folded
    page.show()
    page.activateWindow()
    assert QTest.qWaitForWindowActive(page)
    until(lambda: not page.jog.job_pending
          and not session.robot_lease.busy)
    # The step stays on the status plate, folded or not.
    assert page.view._step_line[0] is page.jog.pad
    x0 = tip_page.xyz(session.robot)[0]
    QTest.keyClick(page, Qt.Key.Key_Right)
    until(lambda: abs(tip_page.xyz(session.robot)[0] - x0) > 0.5)
    until(lambda: not page.jog.job_pending)
    page.hide()


def test_controls_stay_hidden_while_a_run_has_the_robot(until, app, page, ready):
    page.go_button.click()
    until(lambda: page.yes_button.isEnabled())
    assert not page.jog.isVisibleTo(page)
    assert not page.touch_jog.isVisibleTo(page)
    page.session.stop_requested.emit()
    until(lambda: not page.calibration.running)


def test_a_step_not_saved_survives_the_raise_on_leaving(until, app, page, ready):
    """Leaving raises the tip before Save / Discard is asked: straight up, so
    the step is still the tip's, and Save stores it."""
    session = page.session
    _calibrate(until, page)
    crosshair = page._crosshair
    _adjust(until, page)
    page.touch_jog.step("x", +1)
    until(lambda: not page.touch_jog.job_pending
          and page.tune_save_button.isEnabled())
    motion = session.motion
    assert page.jog.run_job(lambda log: motion.raise_tip(log=log) and None)
    until(lambda: not page.jog.job_pending
          and not page.tune_save_button.isEnabled())
    assert page.unsaved() != ""
    page.save_unsaved()
    until(lambda: page.unsaved() == ""
          and not page.touch_jog.job_pending)
    x, y, _z = tip_page.xyz(session.robot)
    offset = session.profile.calibration.pipette_offset
    assert offset.method == "auto+manual"
    assert (offset.dx, offset.dy) == pytest.approx(
        (x - crosshair[0], y - crosshair[1]), abs=1e-6)


def test_a_step_is_nothing_to_save_once_the_tip_went_elsewhere(until, 
        app, page, ready):
    """Parked with a step not saved: its pose is no offset."""
    session = page.session
    _calibrate(until, page)
    offset = session.profile.calibration.pipette_offset
    _adjust(until, page)
    page.touch_jog.step("x", +1)
    until(lambda: not page.touch_jog.job_pending
          and page.tune_save_button.isEnabled())
    motion = session.motion
    assert page.jog.run_job(lambda log: motion.tip_to((100.0, 100.0), 90.0,
                                                      log=log))
    until(lambda: not page.jog.job_pending and page.unsaved() == "")
    assert page.adjust_button.isVisibleTo(page)
    assert not page.adjust_button.isEnabled()
    assert session.profile.calibration.pipette_offset is offset


def test_dropping_the_tip_while_adjusting_ends_it(until, app, page, ready):
    session = page.session
    _calibrate(until, page)
    _adjust(until, page)
    page.touch_jog.step("x", +1)
    until(lambda: not page.touch_jog.job_pending
          and page.tune_save_button.isEnabled())
    page.drop_button.click()
    until(lambda: session.tip.attached is False
          and not page.jog.job_pending)
    assert page.unsaved() == ""
    assert not page.calibration._adjusting


# -- by hand (#141) -------------------------------------------------------------

def _frame(tip=False):
    """A frame as the routine hands it on, and the model's boxes on it."""
    import numpy as np

    from micropick.workflows.calibrate_pipette import Detection
    frame = np.zeros((480, 640, 3), np.uint8)
    dets = [Detection("point", np.array([320.0, 240.0]), 0.9,
                      (310, 230, 330, 250))]
    if tip:
        dets.append(Detection("tip", np.array([300.0, 200.0]), 0.6,
                              (290, 190, 310, 210)))
    return frame, dets


def _pattern():
    import numpy as np

    from micropick.workflows.calibrate_pipette import PatternView
    frame, dets = _frame()
    return PatternView(np.array([320.0, 240.0]), np.zeros((4, 2)), None, 0.2,
                       0.5, 0.0, 5, frame, dets)


@pytest.fixture
def by_hand(until, app, page, monkeypatch):
    """A tip on, a calibration position, Settings' mode By hand, and the
    routine replaced by one that shows the lower camera's reading and leaves
    the tip at the crosshair (150, 150) plus the start offset."""
    import numpy as np

    from micropick.config.schema import TipTarget
    from micropick.workflows.calibrate_pipette import HandStart
    session = page.session
    _tip_on(until, page)
    session.remember(CALIBRATION_POSITION, (150.0, 150.0, 80.0))
    seen = {"gate": threading.Event()}
    seen["gate"].set()

    def start(robot, over, under, detector, pmap, *, current_offset, height,
              on_frame, on_move, **_kw):
        seen["start"] = current_offset
        on_frame("1_upper", 1, *_frame())
        seen["gate"].wait(10)
        on_move()
        on_frame("2_lower", 1, *_frame())
        on_move()
        move_to(robot, (150.0 + current_offset[0], 150.0 + current_offset[1],
                        height), force_direct=True)
        return HandStart(np.array([150.0, 150.0]), _pattern(), None,
                         (150.0, 150.0), (640, 480), (640, 480), TipTarget())

    monkeypatch.setattr(tip_page, "start_by_hand", start)
    monkeypatch.setattr(tip_page, "load_tip_detector",
                        lambda *a, **k: object())
    monkeypatch.setattr(tip_page.PixelMap, "from_config",
                        staticmethod(lambda _config: None))
    monkeypatch.setattr(page, "_readiness", lambda: [])
    session.settings = session.settings.model_copy(
        update={"tip_cal_mode": "hand"})
    page.calibration.open_cameras()
    until(lambda: page.calibration._under_camera() is not None
          and page.calibration._over_camera() is not None)
    page._refresh()
    return seen


def test_by_hand_the_run_leaves_the_tip_to_center_and_save_writes_it(until, 
        app, page, by_hand, monkeypatch):
    session = page.session
    said = _toasts(monkeypatch)
    assert page.path.hidden("find") and page.path.hidden("fine")
    _calibrate(until, page)
    assert page.path.current == "hand"
    assert page.path.title("hand") == "Center the tip by hand"
    assert not tip_calibration_status(session)[0]
    # Controls open over the lower camera, the steps from 0.5 mm.
    assert page.touch_jog.isVisibleTo(page)
    assert not page.jog.isVisibleTo(page)
    assert not page.touch_jog.pad.folded
    assert page.touch_jog.pad.step == pytest.approx(0.5)
    assert page.view.camera is page.calibration._under_camera()
    until(lambda: not page.touch_jog.job_pending)
    # The start offset may be right already: Save at once.
    assert page.hand_save_button.isEnabled()
    assert page.unsaved() == "Tip calibration: the tip is not centered yet."
    page.touch_jog.step("x", +1)
    until(lambda: not page.touch_jog.job_pending
          and not session.robot_lease.busy)
    x, y, _z = tip_page.xyz(session.robot)
    page.hand_save_button.click()
    until(lambda: tip_calibration_status(session)[0])
    offset = session.profile.calibration.pipette_offset
    assert offset.method == "manual"
    assert (offset.dx, offset.dy) == pytest.approx((x - 150.0, y - 150.0))
    assert offset.robot_session == session.run_state.run_id
    assert said[-1] == tip_page.CALIBRATED
    assert page.unsaved() == ""
    assert page.path.current is None or page.path.current != "hand"
    assert page.path.title("hand") == "Tip centered"


def test_by_hand_cancel_saves_nothing_and_goes_back_to_the_disc(until, 
        app, page, by_hand, monkeypatch):
    session = page.session
    said = _toasts(monkeypatch)
    _calibrate(until, page)
    until(lambda: not page.touch_jog.job_pending)
    pose = tip_page.xyz(session.robot)
    page.hand_cancel_button.click()
    assert session.profile.calibration.pipette_offset is None
    assert said[-1] == tip_page.STOPPED
    assert page.path.current == "go"
    assert page.unsaved() == ""
    assert not page.touch_jog.isVisibleTo(page)
    assert tip_page.xyz(session.robot) == pytest.approx(pose, abs=0.05)


def test_by_hand_leaving_unsaved_can_be_discarded(until, app, page, by_hand):
    _calibrate(until, page)
    assert page.unsaved()
    page.discard_unsaved()
    assert page.unsaved() == ""
    assert page.session.profile.calibration.pipette_offset is None


# -- the detections on the picture (#141) ---------------------------------------

def test_each_frame_has_its_boxes_over_the_live_camera_that_took_it(until, 
        app, page, by_hand):
    from micropick.viz.overlays import Rect
    upper = page.calibration._over_camera()
    under = page.calibration._under_camera()
    by_hand["gate"].clear()
    page.go_button.click()
    until(lambda: page.yes_button.isEnabled())
    page.yes_button.click()
    # The upper camera's frame: its live picture, the boxes over it.
    until(lambda: any(isinstance(i, Rect)
          for i in page.view._overlay))
    assert page.view.camera is upper
    assert page.view.live
    assert page.detections_chip.isChecked()
    page.detections_chip.click()
    assert page.view._overlay == []
    page.detections_chip.click()
    assert any(isinstance(i, Rect) for i in page.view._overlay)
    by_hand["gate"].set()
    until(lambda: not page.calibration.running)
    # The gantry moved after the last frame: the lower camera live, bare.
    assert page.view.camera is under
    assert page.view.live
    assert page.view._overlay == []


def test_a_failed_reading_keeps_its_boxes_over_its_camera(until, 
        app, page, ready, monkeypatch):
    from micropick.viz.overlays import Rect
    from micropick.workflows.calibrate_pipette import TipCalibrationError

    def failing(robot, over, under, detector, pmap, *, on_frame, on_move,
                **_kw):
        on_frame("1_upper", 1, *_frame())
        on_move()
        on_frame("2_lower", 1, *_frame(tip=True))
        raise TipCalibrationError("the tip was not detected")

    monkeypatch.setattr(tip_page, "calibrate_pipette_offset", failing)
    page.calibration.open_cameras()
    until(lambda: page.calibration._under_camera() is not None)
    _calibrate(until, page)
    assert page.calibration.failure.isVisibleTo(page)
    assert "the tip was not detected" in page.calibration.failure.label.text()
    assert page.view.camera is page.calibration._under_camera()
    assert page.view.live
    assert any(isinstance(i, Rect) for i in page.view._overlay)
