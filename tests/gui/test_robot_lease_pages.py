"""One job at a time on the robot, across pages (`Session.robot_lease`).

Offscreen, with a mock session. Each test is an overlap that used to be
possible: a page kept its own idea of busy and could not see another's.
"""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.config.labware import resolve_definition          # noqa: E402
from micropick.core.liquid import Location                       # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages import liquid as liquid_page            # noqa: E402
from micropick.gui.pages.labware import LabwarePage              # noqa: E402
from micropick.gui.pages.picking import PickingPage              # noqa: E402
from micropick.gui.pages.home import HomePage                    # noqa: E402
from micropick.gui.session import Session                        # noqa: E402
from micropick.gui.widgets.jog_panel import JogPanel             # noqa: E402

PLATE = "corning_96_wellplate_360ul_flat"
RACK = "opentrons_96_tiprack_300ul"


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


def _until(app, condition, timeout_s=10.0):
    end = time.monotonic() + timeout_s
    while not condition() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    return condition()


def _settled(app, panel):
    assert _until(app, lambda: not panel.busy)


def test_a_liquid_run_keeps_another_pages_panel_off_the_robot(app, session):
    session.tip = session.tip.__class__(attached=True)
    page = liquid_page.LiquidHandlingPage(session)
    other = JogPanel(session)                   # Manual control's, say
    _settled(app, page.jog)
    _settled(app, other)
    page.plate.set_selection({"A1", "A2"})
    page._new_group()
    page._add_step("pause")
    page._add_step("aspirate")
    page.trash.set_location(Location(kind="well", slot="5", load_name=PLATE,
                                     well="H12"))
    page._trash_edited()
    page._confirm_start = lambda: True
    page._start()
    # Held at its pause: still the run's, as a pause is.
    assert _until(app, lambda: page._pause.is_set())
    assert session.robot_lease.holder == "liquid run"
    assert session.robot_lease.holding_run
    assert not other.goto_button.isEnabled()
    assert not any(s.isEnabled() for s in other._shortcuts)
    assert "liquid run" in other.toolTip()
    before = list(session.robot.calls)
    assert other.run_job(lambda _log: session.robot.home_robot()) is False
    assert session.robot.calls == before
    page._stop_run()
    page._pause.clear()
    assert _until(app, lambda: not session.robot_lease.busy)
    assert _until(app, lambda: not page._running)
    other.show()
    assert _until(app, lambda: other._enabled)
    page.deleteLater()
    other.deleteLater()


def test_start_picking_is_refused_while_the_robot_is_held(app, session,
                                                           monkeypatch):
    page = PickingPage(session)
    monkeypatch.setattr(page, "_run_problems", lambda: [])
    monkeypatch.setattr(page, "_confirm_start", lambda: True)
    # What the Picking page's own panel holds while it moves to the dish.
    lease = session.robot_lease.claim("moving the robot")
    try:
        page._refresh()
        assert not page.start_button.isEnabled()
        page._start_run()
        assert not page._running()
        assert page.run_state.text() == "Robot busy."
    finally:
        lease.release()
    page.deleteLater()


def test_the_deck_waits_for_a_run(app, session):
    session.load_labware(resolve_definition(RACK), 1)
    page = LabwarePage(session)
    page.choose(1)
    assert page.tip_well.count() > 0
    lease = session.robot_lease.claim("picking run", run=True)
    try:
        app.processEvents()
        assert not page.pick_button.isEnabled()
        before = list(session.robot.calls)
        page._pick_up()
        assert session.robot.calls == before
        assert session.tip.attached is False
    finally:
        lease.release()
    app.processEvents()
    assert page.pick_button.isEnabled()
    page.deleteLater()


def test_home_the_robot_is_refused_while_the_robot_is_busy(app, session):
    session._api.power_off()
    session._api.power_on()
    session.robot_seen(session.look_at_robot())
    assert session.needs_home
    page = HomePage(session)
    lease = session.robot_lease.claim("picking run", run=True)
    try:
        assert page.home_button.isVisibleTo(page)
        assert not page.home_button.isEnabled()
    finally:
        lease.release()
    app.processEvents()
    assert page.home_button.isEnabled()
    page.deleteLater()


def test_a_lent_lease_lets_the_touch_up_panel_jog_and_stays_held(app,
                                                                 session):
    panel = JogPanel(session)
    _settled(app, panel)
    calibration = session.robot_lease.claim("pipette calibration", run=True)
    try:
        assert panel.run_job(lambda _log: None) is False
        panel.lend(calibration)
        assert panel.run_job(lambda _log: None) is True
        _settled(app, panel)
        assert session.robot_lease.holder == "pipette calibration"
        panel.end_loan()
        assert panel.run_job(lambda _log: None) is False
    finally:
        calibration.release()
    panel.deleteLater()


def test_after_a_failed_move_the_pose_is_read_without_taking_the_robot(
        app, session):
    panel = JogPanel(session)
    _settled(app, panel)
    holders = []
    session.robot_busy_changed.connect(holders.append)

    def refused(_log):
        raise RuntimeError("Destination out of bounds")

    assert panel.run_job(refused, what="moving the robot")
    # Not `_settled`: the failed move's thread ends before its failure is
    # handled, and handling it starts the read. The move cleared the pose,
    # so a pose again, with nothing in flight, is the read done.
    assert _until(app, lambda: panel.pose is not None and not panel.busy)
    # The move's claim and its release, and nothing for the read after it.
    assert holders == ["moving the robot", None]
    assert any("out of bounds" in line for line in panel.position_lines)
    panel.deleteLater()


def test_a_load_refused_for_a_busy_robot_is_not_taken_for_the_next_job(
        app, session):
    session.load_labware(resolve_definition(RACK), 1)
    page = LabwarePage(session)
    refused = "corning_384_wellplate_112ul_flat"
    page.choose(3)
    lease = session.robot_lease.claim("picking run", run=True)
    try:
        page.put(resolve_definition(refused))
    finally:
        lease.release()
    # The next job here, a tip, is not the load that never started.
    page.choose(1)
    page._pick_up()
    assert _until(app, lambda: not page._busy())
    assert session.tip.attached is True
    assert refused not in session.profile.deck.recent_labware
    page.deleteLater()
