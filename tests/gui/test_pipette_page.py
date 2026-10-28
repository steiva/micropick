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
    assert page.drop_button.text() == "Drop in trash"
    assert page.drop_here_button.text() == "Drop in place"
    assert not page.drop_button.isHidden()
    assert not page.drop_here_button.isHidden()
    assert not hasattr(page, "put_back_button")
    assert page.take_button.isHidden()


def test_drop_in_trash_uses_the_tip(app, page):
    _tip_on(app, page, "A3")
    page.drop_button.click()
    assert _until(app, lambda: page.session.tip.attached is False
                  and not page.jog.busy)
    assert page.session.used_tips(10) == {"A3"}
    assert page.take_button.text() == "Take tip A1"


def test_drop_in_place_lets_go_of_the_tip_without_moving(app, page):
    _tip_on(app, page, "A3")
    robot = page.session.robot
    before = len(robot.calls)
    page.drop_here_button.click()
    assert _until(app, lambda: page.session.tip.attached is False
                  and not page.jog.busy)
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


def test_check_after_a_restart_takes_the_crosshair_from_the_pixel_map(
        app, page, ready):
    from micropick.config.schema import PixelMap as PixelMapConfig
    from micropick.core.calibration.pixel_map import PixelMap
    w, h, k = 1600, 1200, 0.01
    page.session.profile.calibration.pixel_map = PixelMapConfig(   # a toy
        degree=1, cu=w / 2, cv=h / 2, s=w / 2,
        coef=[[0.0, k * w / 2], [k * w / 2, 0.0]], zero=[0.0, 0.0],
        ref=[w / 2, h / 2], bounds=[100.0, 100.0, w - 100.0, h - 100.0],
        image_size=[w, h], sweep_z=0.0)
    page.start_button.click()
    assert _until(app, lambda: page.disc_button.isEnabled())
    page.disc_button.click()
    page.calibrate_button.click()
    assert _until(app, lambda: page.skip_button.isEnabled())
    page.skip_button.click()
    assert _until(app, lambda: not page.session.robot_lease.busy)
    page._crosshair = None                   # as a page of a new start
    page._refresh()
    assert page.check_button.isEnabled()
    pmap = PixelMap(page.session.profile.pixel_map)
    gx, gy = pmap.to_robot(pmap.config.cu, pmap.config.cv, (150.0, 150.0))
    page.check_button.click()
    assert _until(app, lambda: not page.jog.busy)
    x, y, _z = tip_page.xyz(page.session.robot)
    assert (x, y) == pytest.approx((gx + 1.5, gy - 2.5), abs=0.05)


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


# -- the cards as blocks in another widget ------------------------------------

def test_the_two_cards_work_embedded_in_another_widget(app, tmp_path,
                                                        monkeypatch):
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
    jog = JogPanel(session, shortcut_host=host, machine_controls=False,
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
        assert tip.take_button.text() == "Take tip A1"
        tip.take_button.click()
        assert _until(app, lambda: tip.done and not jog.busy)
        assert "tip" in changes
        assert calibration.box.isEnabled()

        session.remember(CALIBRATION_POSITION, (150.0, 150.0, 80.0))
        _fake_routine(monkeypatch)
        monkeypatch.setattr(calibration, "_readiness", lambda: [])
        calibration.refresh()
        calibration.start_button.click()
        # A run under its own holder's name, from Start to the end.
        assert session.robot_lease.holding_run
        assert session.robot_lease.holder == "shake tip calibration"
        assert not tip.take_button.isEnabled()
        assert _until(app, lambda: calibration.disc_button.isEnabled())
        assert calibration.run_activity() == "waiting"
        calibration.disc_button.click()
        calibration.calibrate_button.click()
        assert _until(app, lambda: not session.robot_lease.busy)
        assert calibration.done
        assert "calibrated" in changes
        assert not calibration.calibrated_banner.isHidden()
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
