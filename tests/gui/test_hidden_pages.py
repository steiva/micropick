"""A hidden page does not refresh on a change of the robot's lease; it
reads the state when it is shown again (GUI-10)."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.gui import tasks                                  # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages import calibration_pipette, well_center  # noqa: E402
from micropick.gui.shell import MainWindow                       # noqa: E402

COUNTED = {"tip": (calibration_pipette.TipCalibrationBlock, "refresh"),
           "tip card": (calibration_pipette.TipOnBlock, "refresh"),
           "well center": (well_center.WellCenterPage, "refresh")}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def counts(monkeypatch):
    """Calls of each page's refresh, counted from before the window is
    made: a page connects its bound method as it is built."""
    seen = {name: 0 for name in COUNTED}
    for name, (cls, method) in COUNTED.items():
        real = getattr(cls, method)

        def counted(self, *args, _real=real, _name=name):
            seen[_name] += 1
            return _real(self, *args)

        monkeypatch.setattr(cls, method, counted)
    return seen


@pytest.fixture
def window(app, tmp_path, monkeypatch, counts, robot_free):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    real = tasks.step_state
    monkeypatch.setattr(tasks, "step_state", lambda s, t, step: tasks.StepState(
        real(s, t, step).done, None, tasks.optional(t, step)))
    w = MainWindow(Options(mock=True))
    w.show()
    # The monitor's look tells the robot's state every few seconds; what is
    # counted here is the lease's change.
    w.monitor.stop()
    w.session.probe_robot()
    w.session.new_run()
    robot_free(w)
    yield w
    w.hide()
    w.session.shutdown()
    w.deleteLater()


def _jog(window, robot_free, steps):
    panel = window.manual_page.jog
    for _ in range(steps):
        robot_free(window)
        panel.step("x", +1)
    robot_free(window)


def test_a_hidden_page_does_not_refresh_on_a_lease_change(
        window, counts, robot_free, until):
    window.open_task_step("manual", "work")
    # The camera's opening is told to every page, shown or not.
    until(lambda: window.manual_page.view.camera is not None,
          what="Manual pick's camera open")
    _jog(window, robot_free, 1)
    before = dict(counts)
    moves = window.session.robot.moves
    _jog(window, robot_free, 10)
    assert window.session.robot.moves > moves
    assert counts == before


def test_a_page_shown_again_reads_the_state_then(
        window, counts, robot_free, until):
    window.open_task_step("manual", "work")
    _jog(window, robot_free, 2)
    before = counts["well center"]
    window.open_task_step("manual", "well_center")
    until(lambda: counts["well center"] > before,
          what="Well center refreshed as it is shown")
