"""What a jog panel says goes to the toast; on the picture only X Y Z."""

import os
import re
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.session import Session                        # noqa: E402
from micropick.gui.widgets.jog_panel import JogPanel             # noqa: E402
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
    made.refresh_position()
    _settled(app, made)
    yield made
    made.deleteLater()


def _at(app, session, panel, x, y, z):
    session.robot.move_to_coordinates((x, y, z), verbose=False)
    panel.refresh_position()
    _settled(app, panel)


def _only_the_coordinates(panel):
    """The mock's pose has a little noise in it: the shape, not the value."""
    number = r"-?\d+\.\d\d"
    assert len(panel.position_lines) == 1
    assert re.fullmatch(f"X {number}  Y {number}  Z {number}",
                        panel.position_lines[0])


def test_a_step_refused_is_red_and_the_picture_keeps_its_coordinates(
        app, session, panel):
    _at(app, session, panel, -5.0, 100.0, 50.0)
    panel.step("x", -1)
    _settled(app, panel)
    toast = toast_of(panel)
    assert toast.text().startswith("X is already")
    assert toast.text().endswith("only moves back are allowed.")
    assert toast.property("tone") == "refusal"
    _only_the_coordinates(panel)


def test_a_step_cut_short_is_an_answer_in_a_sentence(app, session, panel):
    panel.set_step(10)
    _at(app, session, panel, 3.0, 100.0, 50.0)
    panel.step("x", -1)
    _settled(app, panel)
    toast = toast_of(panel)
    assert toast.text() == "Stopped at the X limit 0."
    assert toast.property("tone") == "answer"
    _only_the_coordinates(panel)


def test_nothing_is_said_of_a_pose_out_of_the_envelope(app, session, panel):
    top = session.motion.envelope.z[1]
    _at(app, session, panel, 100.0, 100.0, top + 0.5)
    _only_the_coordinates(panel)


def test_a_failed_job_is_red(app, session, panel):
    def job(_log):
        raise RuntimeError("Destination out of bounds")

    assert panel.run_job(job, what="moving the robot")
    end = time.monotonic() + 10
    while (panel.pose is None or panel.busy) and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    assert "out of bounds" in toast_of(panel).text()
    assert toast_of(panel).property("tone") == "refusal"
    assert len(panel.position_lines) == 1


def test_what_a_job_says_is_an_answer(app, panel):
    assert panel.run_job(lambda _log: "Camera over slot 3.", moves=False)
    _settled(app, panel)
    assert toast_of(panel).text() == "Camera over slot 3."
    assert toast_of(panel).property("tone") == "answer"
    assert len(panel.position_lines) == 1


def test_a_robot_held_refuses_in_red(app, session, panel):
    lease = session.robot_lease.claim("picking run", run=True)
    try:
        assert not panel.run_job(lambda _log: "moved")
    finally:
        lease.release()
    assert toast_of(panel).text() == session.robot_lease.refusal
    assert toast_of(panel).property("tone") == "refusal"


def test_nothing_to_say_shows_no_toast(app, panel):
    panel.tell("")
    panel.refuse("")
    assert panel.run_job(lambda _log: None, moves=False)
    _settled(app, panel)
    assert toast_of(panel).isHidden()


def test_a_read_after_a_refusal_leaves_the_toast(app, panel):
    panel.refuse("Robot busy.")
    panel.refresh_position()
    _settled(app, panel)
    assert toast_of(panel).text() == "Robot busy."
    assert not toast_of(panel).isHidden()


def test_a_panels_listeners_hear_of_a_jobs_end_after_its_then(app, panel):
    """Pages refresh on `position_changed`: what `then` changes is seen."""
    state, seen = {"done": False}, []
    panel.position_changed.connect(lambda _lines: seen.append(state["done"]))

    def then(_result):
        state["done"] = True

    assert panel.run_job(lambda _log: None, moves=False, then=then)
    _settled(app, panel)
    assert seen[-1] is True
