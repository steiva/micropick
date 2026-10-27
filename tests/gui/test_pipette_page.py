"""The Tip step (`pages.calibration_pipette`, REDESIGN §6.2): a tip put on
from a rack on the deck, then the tip calibration as a run - Start, the
disc, Calibrate, the fine-tune - whose touch-up arrows follow the lower
camera."""

import os
import threading
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.config.labware import resolve_definition          # noqa: E402
from micropick.config.schema import PipetteOffset                # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages import calibration_pipette as tip_page  # noqa: E402
from micropick.gui.pages.calibration_pipette import (            # noqa: E402
    PipetteCalibration)
from micropick.gui.readiness import (CALIBRATION_POSITION,       # noqa: E402
                                     tip_calibration_status)
from micropick.gui.session import Session                        # noqa: E402
from micropick.gui.widgets.checklist import BLOCKED, OK          # noqa: E402
from micropick.gui.widgets.jog_panel import view_move            # noqa: E402
from micropick.gui.workers import Worker                         # noqa: E402

RACK = "opentrons_96_tiprack_300ul"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def page(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    s.load_profile("mock")
    s.probe_robot()
    s.new_run()
    p = PipetteCalibration(s)
    yield p
    p.deleteLater()
    s.shutdown()


def _until(app, condition, timeout_s=10.0):
    end = time.monotonic() + timeout_s
    while not condition() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    return condition()


def _rack(page, slot=10):
    page.session.load_labware(resolve_definition(RACK), slot)
    page._refresh()


def _tip_on(app, page, well="A1"):
    _rack(page)
    page.rack_grid.well_clicked.emit(well)
    page.take_button.click()
    assert _until(app, lambda: page.session.tip.attached is True
                  and not page.jog.busy)


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


def test_a_click_on_the_rack_chooses_the_tip_to_take(app, page):
    _rack(page)
    assert page.take_button.text() == "Take tip A1"     # the first not used
    width = page.take_button.width()
    page.rack_grid.well_clicked.emit("F12")
    assert page.take_button.text() == "Take tip F12"
    assert page.take_button.width() == width             # fixed
    page.rack_grid.well_clicked.emit("A3")
    page.take_button.click()
    assert _until(app, lambda: page.session.tip.attached is True)
    assert (page.session.tip.slot, page.session.tip.well) == ("10", "A3")
    assert _until(app, lambda: page.tip_state.text() == "Tip A3 is on.")
    assert not page.drop_button.isHidden() and not page.put_back_button.isHidden()
    assert page.take_button.isHidden()


def test_put_back_returns_the_tip_and_drop_in_trash_uses_it(app, page):
    _tip_on(app, page, "A3")
    page.put_back_button.click()
    assert _until(app, lambda: page.session.tip.attached is False
                  and not page.jog.busy)
    assert page.session.used_tips(10) == set()
    assert page.take_button.text() == "Take tip A3"      # it is there again
    page.take_button.click()
    assert _until(app, lambda: page.session.tip.attached is True
                  and not page.jog.busy)
    page.drop_button.click()
    assert _until(app, lambda: page.session.tip.attached is False
                  and not page.jog.busy)
    assert page.session.used_tips(10) == {"A3"}
    assert page.take_button.text() == "Take tip A1"


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

def test_the_calibration_steps_are_shown_and_dimmed_until_a_tip_is_on(app, page):
    assert not page.calibration_box.isHidden()
    assert not page.calibration_box.isEnabled()
    assert page.start_step.state == BLOCKED
    assert page.start_button.text() == "Start tip calibration"
    _tip_on(app, page)
    assert page.calibration_box.isEnabled()


def test_liquid_needs_no_calibration(page):
    page.set_task("liquid")
    assert page.calibration_box.isHidden()
    page.set_task("auto")
    assert not page.calibration_box.isHidden()


@pytest.fixture
def ready(app, page, monkeypatch):
    """A tip on, a calibration position, and the routine replaced by one
    that asks for the touch-up and saves an offset for this tip."""
    session = page.session
    _tip_on(app, page)
    session.remember(CALIBRATION_POSITION, (150.0, 150.0, 80.0))
    seen = {}

    def calibrate(robot, over, under, detector, pmap, *, profile,
                  current_offset, manual_touch_up, robot_session,
                  tip_pickup, **_kw):
        seen["start"] = current_offset
        if manual_touch_up is not None:
            manual_touch_up(robot, under, None)
        offset = PipetteOffset(dx=1.5, dy=-2.5, robot_session=robot_session,
                               tip_pickup=tip_pickup)
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


def test_the_calibration_is_a_run_through_the_disc_and_the_fine_tune(
        app, page, ready):
    session = page.session
    assert page.start_step.state != BLOCKED
    page.start_button.click()
    # A run: the robot's lease from Start to the end, the pill's name.
    assert session.robot_lease.holding_run
    assert session.robot_lease.holder == "pipette calibration"
    # The camera went to the calibration position; the disc is checked.
    assert _until(app, lambda: page.disc_button.isEnabled())
    assert page.start_step.state == OK
    assert not page.calibrate_button.isEnabled()
    assert page.run_activity() == "waiting"
    page.disc_button.click()
    assert page.calibrate_button.isEnabled()
    page.calibrate_button.click()
    # The fine-tune, by hand: Save or Skip.
    assert _until(app, lambda: page.save_button.isEnabled())
    assert page.skip_button.isEnabled()
    assert page.run_activity() == "waiting"
    page.save_button.click()
    assert _until(app, lambda: not session.robot_lease.busy)
    assert ready["start"] == session.settings.tip_cal_start_offset
    assert tip_calibration_status(session)[0]
    assert not page.calibrated.isHidden()
    assert page.calibrated.label.text() == "Tip calibrated."
    assert page.check_button.isEnabled()
    # Done follows the tip that is on: another tip is to calibrate again.
    page.drop_button.click()
    assert _until(app, lambda: session.tip.attached is False
                  and not page.jog.busy)
    assert page.calibrated.isHidden()


def test_check_sends_the_tip_to_the_crosshair_it_found(app, page, ready):
    page.start_button.click()
    assert _until(app, lambda: page.disc_button.isEnabled())
    page.disc_button.click()
    page.calibrate_button.click()
    assert _until(app, lambda: page.skip_button.isEnabled())
    page.skip_button.click()
    assert _until(app, lambda: not page.session.robot_lease.busy)
    crosshair = page._crosshair
    assert crosshair is not None
    page.check_button.click()
    assert _until(app, lambda: not page.jog.busy)
    x, y, _z = tip_page.xyz(page.session.robot)
    # The mock's pose wanders by a few hundredths.
    assert (x, y) == pytest.approx((crosshair[0] + 1.5, crosshair[1] - 2.5),
                                   abs=0.05)


def test_stop_at_the_disc_ends_the_run_with_nothing_saved(app, page, ready):
    session = page.session
    page.start_button.click()
    assert _until(app, lambda: page.disc_button.isEnabled())
    session.stop_requested.emit()
    assert _until(app, lambda: not session.robot_lease.busy)
    assert session.profile.calibration.pipette_offset is None
    assert "Nothing was saved." in page.checks.text()
    assert page.start_button.isEnabled()


# -- the touch-up --------------------------------------------------------------

def test_the_touch_up_follows_the_lower_camera_in_xy_only(app, page):
    page._begin_touch_up()
    jog = page.touch_jog
    axes = page.session.profile.calibration.tip_target.axes
    assert jog._view_axes == tuple(tuple(r) for r in axes)
    assert view_move(jog._view_axes, "x", +1) == ("y", +1)
    assert jog._xy_only
    assert not any("PgUp" in line for line in jog.help_lines)
    assert not page.jog.isVisible()                    # the page's panel steps aside
    page._abort()


def test_abort_waits_for_a_touch_up_step_still_moving(app, page):
    session = page.session
    # The calibration as it runs: it holds the robot and waits in the
    # touch-up for the operator.
    lease = session.robot_lease.claim("pipette calibration", run=True)
    worker = Worker(lambda: page._touch_up(session.robot, None, None),
                    lease=lease)
    page._worker = worker
    worker.start()
    assert _until(app, lambda: page.save_button.isEnabled())
    # A touch-up step, still moving when Abort is pressed.
    moving, done = threading.Event(), threading.Event()

    def step(_log):
        moving.set()
        done.wait(10)

    assert page.touch_jog.run_job(step)
    assert moving.wait(5)
    page._abort()
    assert session.robot_lease.holder == "pipette calibration"
    assert worker.running and page.save_button.isEnabled()
    done.set()
    assert _until(app, lambda: not page.touch_jog.busy)
    page._abort()
    assert _until(app, lambda: not session.robot_lease.busy)
