"""Liquid's Run step (REDESIGN §6.9): the plate with progress on the left,
the footer as on Picking.

The main window under --mock, on Liquid's Run step, running a plan on the
mock robot: Start, Pause, Resume, a pause the plan asks for, Stop run, and
a run to its end.
"""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.config.labware import resolve_definition          # noqa: E402
from micropick.core.liquid import (Aspirate, Dispense, Group,    # noqa: E402
                                   Location, Pause, Program, Wait)
from micropick.gui import tasks                                  # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.shell import MainWindow                       # noqa: E402

PLATE = "corning_96_wellplate_360ul_flat"
TRASH = Location(kind="well", slot="5", load_name=PLATE, well="H12")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    w = MainWindow(Options(mock=True))
    session = w.session
    session.probe_robot()
    session.new_run()
    session.load_labware(resolve_definition(PLATE), 5)
    _until(app, lambda: not session.robot_lease.busy)
    session.confirm("deck")
    session.tip = session.tip.__class__(attached=True)
    page = w.pages["liquid"]
    monkeypatch.setattr(page, "_confirm_start", lambda: True)
    w.open_task("liquid")
    w.show_step("run")
    yield w
    session.request_stop()
    _until(app, lambda: not page._running and not page.jog.busy)
    session.shutdown()
    w.deleteLater()


def _until(app, condition, timeout_s=10.0):
    end = time.monotonic() + timeout_s
    while not condition() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    return condition()


def _plan(window, steps, wells=("A1", "A2", "A3")):
    window.session.set_liquid_plan(Program(trash=TRASH, groups=[
        Group(name="Wash", slot="5", load_name=PLATE, wells=list(wells),
              steps=list(steps))]))
    window._refresh()


def _footer(window):
    return window.footer.tag.text(), window.footer.sentence.text()


def _buttons(window):
    layout = window.footer._buttons
    return [layout.itemAt(i).widget().text() for i in range(layout.count())]


def _pill(window):
    pill = window.top.status
    return None if pill.isHidden() else pill.text().split("&nbsp;")[-1]


def _stop_shown(window):
    return not window.top.stop.isHidden()


def test_not_ready_without_a_plan(app, window):
    tag, sentence = _footer(window)
    assert tag == "NOT READY"
    assert sentence == window._need.text             # the banner's need
    assert _buttons(window) == ["Back", "Start"]
    assert not window.pages["liquid"].start_button.isEnabled()


def test_the_plate_is_on_the_left(app, window):
    page = window.pages["liquid"]
    _plan(window, [Aspirate(volume_ul=10), Dispense(location=TRASH)])
    assert page.body.itemAt(0).widget() is page.plate_box  # no camera
    assert page.plate.isVisibleTo(page)
    assert page.wells_heading.text().startswith("Slot 5 · ")


def test_run_pause_resume_stop(app, window, monkeypatch):
    alerts = []
    monkeypatch.setattr(QApplication, "alert",
                        staticmethod(lambda w, ms=0: alerts.append(w)))
    page = window.pages["liquid"]
    _plan(window, [Aspirate(volume_ul=10), Wait(seconds=0.2),
                   Dispense(location=TRASH), Pause(message="look at it")],
          wells=["A1", "A2", "A3", "A4"])
    assert _footer(window) == ("READY", "4 wells to do.")
    assert _buttons(window) == ["Back", "Start"]

    # A read of the pose may hold the robot for a moment.
    assert _until(app, page._startable)
    page.start_button.click()
    assert _until(app, lambda: page._running)
    assert _footer(window)[0] == "RUNNING"
    assert _buttons(window) == ["Pause", "Stop run"]
    # The run holds the robot from Start to its end: STOP and the pill.
    assert window.session.robot_lease.holding_run
    assert _stop_shown(window)
    assert _pill(window) == "Handling liquid"
    assert _until(app, lambda: "Wash · A1" in _footer(window)[1]), \
        _footer(window)

    page.pause_button.click()
    app.processEvents()
    assert _footer(window)[0] == "PAUSED"
    assert _buttons(window) == ["Resume", "Stop run"]
    assert page.pause_button.property("tone") == "warn"
    assert _pill(window) == "Paused"
    page.pause_button.click()
    app.processEvents()
    assert _footer(window)[0] == "RUNNING"
    assert _buttons(window) == ["Pause", "Stop run"]
    assert not alerts

    # The plan's own pause: the run waits for the operator.
    assert _until(app, lambda: _footer(window)[0] == "WAITING FOR YOU")
    assert _footer(window)[1] == "Look at it"
    assert _buttons(window) == ["Resume", "Stop run"]
    assert _pill(window) == "Waiting for you"
    assert alerts == [window]                  # the taskbar flashes
    page.pause_button.click()                  # Resume
    assert _until(app, lambda: _footer(window)[0] == "RUNNING")
    assert _until(app, lambda: (0, "A1") in page.state.done)

    page.stop_button.click()
    assert _until(app, lambda: not page._running and not page.jog.busy)
    assert _footer(window)[0] == "READY"
    assert _footer(window)[1].startswith("Stopped.")
    assert _buttons(window) == ["Back", "Continue", "Start"]
    assert not window.session.robot_lease.busy
    assert not _stop_shown(window)
    assert _pill(window) is None
    assert not tasks.step_state(window.session, "liquid", "run").done


def test_a_run_to_its_end_is_done(app, window):
    page = window.pages["liquid"]
    _plan(window, [Aspirate(volume_ul=10), Dispense(location=TRASH)],
          wells=["A1", "A2"])
    # A read of the pose may hold the robot for a moment.
    assert _until(app, page._startable)
    page.start_button.click()
    # Two short wells: the run may be over before the first look.
    assert _until(app, lambda: page._ended == "done" and not page.jog.busy)
    assert _footer(window) == ("DONE", "All 2 wells done.")
    assert _buttons(window) == ["Back", "Start"]
    assert page.plate._done == {"A1", "A2"}           # marked on the plate
    assert page.progress_line.text() == "2 of 2 wells done"
    assert tasks.step_state(window.session, "liquid", "run").done
    assert not _stop_shown(window)
