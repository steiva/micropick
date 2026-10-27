"""The Picking step (REDESIGN §6.5): the footer is the run's status.

The main window under --mock, on Auto pick's Picking step. The run itself
is scripted - a stand-in for `PickingSession` that hands back the events
the test gives it - so the footer can be followed through every state a
run goes through without a dish, a model and a calibrated tip.
"""

import os
import queue
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.config.labware import resolve_definition          # noqa: E402
from micropick.core.routine import Destination, Routine          # noqa: E402
from micropick.gui import tasks                                  # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages import picking as picking_page          # noqa: E402
from micropick.gui.shell import MainWindow                       # noqa: E402
from micropick.workflows.picking import (PickEvent, PickView,    # noqa: E402
                                         RobotState)

PLATE = "corning_96_wellplate_360ul_flat"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class ScriptedRun:
    """What the page asks of a `PickingSession`, its steps given by the
    test: each `step()` waits for the next (state, status) put in
    `ScriptedRun.script`."""

    script: "queue.Queue" = None
    made: list = []

    def __init__(self, *_args, routine=None, **_kw):
        self.state = RobotState.IDLE
        self.shake_pending = False
        self.can_shake_now = True
        self.routine = _args[4] if len(_args) > 4 else routine
        ScriptedRun.made.append(self)

    def start(self):
        self.state = RobotState.DETECT_FLOATERS

    status = "Watching the dish settle."

    def resume(self):
        self.resumed = True

    def request_shake(self):
        self.shake_pending = True

    def close(self):
        pass

    @property
    def done(self):
        return self.state in (RobotState.COMPLETED, RobotState.CANCELED)

    @property
    def view(self):
        return PickView(live=True)

    def step(self, pause=None, stop=None):
        while True:
            if stop is not None and stop.is_set():
                self.state = RobotState.CANCELED
                return PickEvent(self.state, "canceled", "stopped", {},
                                 self.view, "Stopped.")
            try:
                state, status = self.script.get(timeout=0.02)
                break
            except queue.Empty:
                continue
        self.state = state
        return PickEvent(state, state.value, status, {}, self.view, status)


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    ScriptedRun.script = queue.Queue()
    ScriptedRun.made = []
    monkeypatch.setattr(picking_page, "PickingSession", ScriptedRun)
    # The mock profile has no pixel map, and the scripted run needs none.
    monkeypatch.setattr(picking_page.PixelMap, "from_config",
                        staticmethod(lambda _config: None))
    w = MainWindow(Options(mock=True))
    session = w.session
    session.probe_robot()
    session.new_run()
    session.load_labware(resolve_definition(PLATE), 5)
    _settle(app, w)
    definition = resolve_definition(PLATE)
    session.set_routine(Routine(Destination.from_definition(definition, 5),
                                {"A1": 1, "A2": 1}, name="test plate"))
    w.open_task("auto")
    w.show_step("picking")
    yield w
    session.request_stop()
    _until(app, lambda: not w.picking_page._running())
    session.shutdown()
    w.deleteLater()


def _settle(app, window, timeout_s=5.0):
    _until(app, lambda: not window.session.robot_lease.busy, timeout_s)


def _until(app, condition, timeout_s=5.0):
    end = time.monotonic() + timeout_s
    while not condition() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    return condition()


def _footer(window):
    footer = window.footer
    return footer.tag.text(), footer.sentence.text()


def _buttons(window):
    layout = window.footer._buttons
    return [layout.itemAt(i).widget().text() for i in range(layout.count())]


def _ready(window, monkeypatch):
    """The steps before Picking done and the page's own set-up in place:
    what a tip calibration, a dish and a model would give."""
    real = tasks.step_state

    def step_state(session, task, step):
        state = real(session, task, step)
        if step == "picking":
            return tasks.StepState(state.done, None, state.optional)
        return state

    monkeypatch.setattr(tasks, "step_state", step_state)
    page = window.picking_page
    monkeypatch.setattr(page, "_run_problems", lambda: [])
    monkeypatch.setattr(page, "_confirm_start", lambda: True)
    window._refresh()


def _start(app, window):
    window.picking_page.start_button.click()
    assert _until(app, lambda: window.picking_page._running())


def _event(app, window, state, status):
    ScriptedRun.script.put((state, status))
    assert _until(app, lambda: _footer(window)[1] == status), _footer(window)


def test_not_ready_names_the_first_thing_missing(app, window):
    tag, sentence = _footer(window)
    assert tag == "NOT READY"
    assert sentence == window._need.text          # the banner's need
    assert _buttons(window) == ["Back", "Start picking"]
    assert not window.picking_page.start_button.isEnabled()


def test_ready_offers_start(app, window, monkeypatch):
    _ready(window, monkeypatch)
    assert _footer(window)[0] == "READY"
    assert _buttons(window) == ["Back", "Start picking"]
    assert window.picking_page.start_button.isEnabled()


def test_the_footer_follows_the_run(app, window, monkeypatch):
    alerts = []
    monkeypatch.setattr(QApplication, "alert",
                        staticmethod(lambda w, ms=0: alerts.append(w)))
    _ready(window, monkeypatch)
    _start(app, window)
    page = window.picking_page
    assert _footer(window)[0] == "PICKING"
    assert _buttons(window) == ["Pause", "Stop run"]

    _event(app, window, RobotState.PICKUP_SAMPLE,
           "Well A1 of 2 · taking a cuboid")
    assert _footer(window)[0] == "PICKING"

    page.pause_button.click()
    app.processEvents()
    assert _footer(window)[0] == "PAUSED"
    assert _buttons(window) == ["Resume", "Stop run"]
    assert page.pause_button.property("tone") == "warn"
    page.pause_button.click()
    app.processEvents()
    assert _footer(window)[0] == "PICKING"
    assert _buttons(window) == ["Pause", "Stop run"]
    assert not alerts

    _event(app, window, RobotState.NEEDS_OPERATOR,
           "No cuboids found after 3 shakes. Add cuboids, or finish by hand.")
    assert _footer(window)[0] == "WAITING FOR YOU"
    assert _buttons(window) == ["Resume", "Stop run"]
    assert alerts == [window]                  # the taskbar flashes, once
    page.pause_button.click()                  # Resume: out of the wait
    assert ScriptedRun.made[-1].resumed

    _event(app, window, RobotState.TRANSFER_TO_WELL,
           "Well A2 of 2 · putting the cuboid in")
    ScriptedRun.script.put((RobotState.COMPLETED,
                            "The plan is filled."))
    assert _until(app, lambda: not page._running())
    assert _footer(window) == ("DONE", "The plan is filled.")
    assert _buttons(window)[0] == "Back"


def test_stop_run_ends_it(app, window, monkeypatch):
    _ready(window, monkeypatch)
    _start(app, window)
    page = window.picking_page
    assert page.stop_button.text() == "Stop run"
    page.stop_button.click()
    assert _until(app, lambda: not page._running())
    tag, sentence = _footer(window)
    assert tag == "READY"
    assert sentence.startswith("Stopped.")


def test_camera_to_dish_center_waits_for_a_pause(app, window, monkeypatch):
    page = window.picking_page
    _ready(window, monkeypatch)
    _start(app, window)
    assert not page.center_button.isEnabled()
    assert page.center_button.text() == "Pause to move the camera"
    page.pause_button.click()
    app.processEvents()
    assert page.center_button.text() == "Camera to dish center"


def test_and_list_names_the_wells():
    assert picking_page.and_list(["A7"]) == "A7"
    assert picking_page.and_list(["A7", "A18"]) == "A7 and A18"
    assert picking_page.and_list(["A1", "A2", "A3"]) == "A1, A2 and A3"
    assert picking_page.and_list([f"A{i}" for i in range(1, 11)], most=3) \
        == "A1, A2, A3 and 7 more"


def test_wells_left_empty_are_finished_by_hand_on_manual_work(
        app, window, monkeypatch):
    _ready(window, monkeypatch)
    page = window.picking_page
    assert page.unfilled.isHidden()                # no run yet
    _start(app, window)
    page.stop_button.click()
    assert _until(app, lambda: not page._running())
    assert not page.unfilled.isHidden()
    assert page.unfilled_title.text() == "2 wells were not filled"
    assert page.unfilled_wells.text() == "A1 and A2."

    routine = window.session.routine
    page.finish_button.click()
    app.processEvents()
    assert (window.task, window.step) == ("manual", "work")
    assert window.current_page() is window.manual_page
    manual = window.manual_page
    assert window.session.routine is routine       # the plan carried over
    assert manual.dispense_card.is_open()
    assert manual.target.current() == 0            # In a well
    assert not manual.plan_box.isHidden()
    assert manual.target_wells.chosen()[-1] == "A1"
