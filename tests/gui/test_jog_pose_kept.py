"""A click on the picture moves from the pose the panel knows: the robot's
state passing by never takes it away, and without one a click reads it and
says so (a click did nothing until a step with the arrows)."""

import os
import threading
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.session import Session                        # noqa: E402
from micropick.gui.widgets.jog_panel import (JogPanel,           # noqa: E402
                                             POSE_UNKNOWN)
from micropick.gui.widgets.toast import toast_of                 # noqa: E402


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


def _settled(app, panel):
    end = time.monotonic() + 10
    while panel.busy and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert not panel.busy


@pytest.fixture
def panel(app, session):
    made = JogPanel(session)
    _settled(app, made)
    yield made
    made.deleteLater()


def test_the_robots_state_passing_by_keeps_the_job_and_the_pose(
        app, session, panel):
    assert panel.pose is not None
    controller = panel.controller
    go = threading.Event()

    def job(_log):
        go.wait(5)
        return "moved"

    assert panel.run_job(job, what="moving the robot")
    # The monitor: the robot did not answer for a moment, then did.
    session.robot_state_changed.emit(session.robot_state)
    session.robot_state_changed.emit(session.robot_state)
    assert panel.controller is controller
    go.set()
    _settled(app, panel)
    assert toast_of(panel).text() == "moved"
    assert panel.pose is not None


def test_a_click_with_no_pose_reads_it_and_says_so(app, session, panel):
    panel._set_pose(None)
    assert panel.known_pose() is None
    assert toast_of(panel).text() == POSE_UNKNOWN
    _settled(app, panel)
    assert panel.known_pose() is not None


def test_a_job_ended_but_not_delivered_keeps_the_panel_busy(
        app, session, panel, robot_free):
    """Between a job's thread ending and its end reaching the page, a key
    or a click is refused: else it took the job's place and the first
    job's `then` was called with the second's result (GUI-06)."""
    seen = []
    assert panel.run_job(lambda log: "answer of job A", moves=False,
                         then=lambda result: seen.append(result) or result)
    panel._worker.wait()                # the thread ended, nothing delivered
    assert panel.busy
    assert not panel.run_job(lambda log: "job B", moves=False)
    moves = session.robot.moves
    panel.step("x", +1)                 # what an arrow key does
    robot_free(session, panel)
    assert session.robot.moves == moves
    assert seen == ["answer of job A"]


def test_a_callback_that_raises_leaves_the_pad_usable(
        app, session, panel, robot_free, monkeypatch):
    """A page's `then` that raises is the excepthook's to say; the pad and
    its keys come back all the same (GUI-09)."""
    import sys
    raised = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: raised.append(a[0]))

    def then(_result):
        raise ZeroDivisionError

    assert panel.run_job(lambda log: "done", moves=False, then=then)
    robot_free(session, panel)
    assert raised == [ZeroDivisionError]
    assert all(b.isEnabled() for b in panel.pad.buttons.values())
    assert panel.run_job(lambda log: "again", moves=False)
    robot_free(session, panel)
