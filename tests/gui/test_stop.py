"""The first Stop ends a short job after the command in progress, the tip
left where it is; a run is not cut short by it (GUI-04)."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.session import Session                        # noqa: E402
from micropick.gui.widgets.jog_panel import JogPanel             # noqa: E402
from micropick.gui.widgets.toast import toast_of                 # noqa: E402
from micropick.gui.workers.base import STOPPED                   # noqa: E402

SPOTS = [(150.0, 150.0), (250.0, 150.0), (250.0, 250.0)]


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
    yield s
    s.shutdown()


@pytest.fixture
def panel(app, session, robot_free):
    made = JogPanel(session)
    robot_free(session, made)
    yield made
    made.deleteLater()


def _three_moves(session):
    motion = session.motion
    travel = []

    def job(log):
        for xy in SPOTS:
            motion.tip_to(xy, 100.0, log=log)
            travel.append(xy)
        return "All three."

    return job, travel


def test_stop_ends_a_short_job_after_the_move_in_progress(
        session, panel, until, robot_free):
    session.robot.speed = 100.0
    job, travel = _three_moves(session)
    before = session.robot.moves
    assert panel.run_job(job, what="moving the robot")
    until(lambda: session.robot.moves > before or travel,
          what="the first move under way")
    session.request_stop()
    at_stop = session.robot.moves
    robot_free(session, panel)
    assert session.robot.moves - at_stop <= 1
    assert len(travel) < len(SPOTS)
    assert toast_of(panel).text() == STOPPED
    assert not session.robot_lease.busy


def test_a_stop_before_a_job_is_not_that_jobs(session, panel, robot_free):
    session.request_stop()
    job, travel = _three_moves(session)
    assert panel.run_job(job, what="moving the robot")
    robot_free(session, panel)
    assert travel == SPOTS
    assert toast_of(panel).text() == "All three."


def test_a_run_is_not_cut_short_by_it(session, panel, until, robot_free):
    """A run stops at its own safe points (`stop_requested`)."""
    session.robot.speed = 100.0
    job, travel = _three_moves(session)
    assert panel.run_job(job, what="a run", run=True)
    until(lambda: session.robot.moves > 0, what="the run moving")
    session.request_stop()
    robot_free(session, panel)
    assert travel == SPOTS
