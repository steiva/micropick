"""The connection monitor (#4): the robot is looked at every few seconds,
a session it can carry on with is taken without a click, and nothing
moves until the operator says Home and connect.

Driven through `Session.look_at_robot` (the read, on a worker) and
`Session.robot_seen` (what it changes, on the GUI thread), with the mock
switched off and on."""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (QApplication, QInputDialog,        # noqa: E402
                               QPushButton)

from micropick.gui.app import Options                          # noqa: E402
from micropick.gui import tasks                                 # noqa: E402
from micropick.gui.session import Session                      # noqa: E402
from micropick.config.labware import resolve_definition        # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def session(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    s.load_profile("mock")
    yield s
    s.shutdown()


def _look(session):
    return session.robot_seen(session.look_at_robot())


def test_a_robot_switched_off_is_not_connected(session):
    session.look_at_robot()                         # makes the mock
    session._api.power_off()
    assert session.look_at_robot() is None
    _look(session)
    assert session.robot_answers is False
    assert session.robot is None


def _moves(session):
    return [c for c in session._api.calls
            if c[0] in ("home_robot", "retract_axis", "create_run")]


def test_a_session_on_the_robot_is_carried_on_with_and_nothing_moves(session):
    assert _look(session) is True
    session.adopt_run()
    assert session.robot is not None
    assert session.run_origin == "reused"
    assert _moves(session) == []
    assert _look(session) is False                 # nothing more to do


def test_a_restart_forgets_labware_tip_and_deck_and_asks_to_home(session):
    _look(session)
    session.adopt_run()
    session.load_labware(resolve_definition("opentrons_96_tiprack_300ul"), 3)
    session.pick_up_tip(3, "A1")
    assert tasks.step_state(session, "auto", "deck").done

    session._api.power_off()
    _look(session)
    assert session.robot_answers is False
    session._api.power_on()
    assert _look(session) is False                 # nothing to carry on with

    assert session.restarted
    assert session.robot is None
    assert session.needs_home
    assert session.tip.attached is None
    assert session.run_state.labware == {}
    assert not tasks.step_state(session, "auto", "deck").done
    assert _moves(session) == []                   # waits for the operator


def test_home_the_robot_makes_a_new_session_and_clears_the_restart(session):
    session.look_at_robot()
    session._api.power_off()
    session._api.power_on()
    _look(session)
    assert session.needs_home and not session.restarted   # first sight
    session.new_run()
    assert session.robot is not None
    assert not session.needs_home
    assert [c[0] for c in _moves(session)] == ["create_run", "home_robot"]
    assert _look(session) is False


def test_a_robot_off_and_on_with_the_same_session_is_carried_on_with(session):
    _look(session)
    session.adopt_run()
    session._api.power_off()
    _look(session)
    assert session.robot is not None               # a blip loses nothing
    session._api.powered = True
    _look(session)
    assert session.robot_answers is True
    assert session.robot is not None and not session.restarted



def test_a_session_that_could_not_be_carried_on_with_asks_to_home(session):
    assert _look(session) is True
    session.adopt_failed(session.run_state.run_id)
    assert session.needs_home
    assert _look(session) is False                 # not tried again


def test_a_status_passing_by_under_a_run_loses_nothing(session, monkeypatch):
    _look(session)
    session.adopt_run()
    session.confirm("dish_bottom")
    finishing = session.look_at_robot()
    finishing.status = "finishing"
    lease = session.robot_lease.claim("picking run", run=True)
    try:
        session.robot_seen(finishing)
        assert session.robot is not None
        assert session.is_confirmed("dish_bottom")
    finally:
        lease.release()
    session.robot_seen(finishing)                  # nothing uses it now
    assert session.robot is None and session.needs_home

# -- Home, with the monitor running ---------------------------------------------

@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    from micropick.gui.shell import MainWindow
    w = MainWindow(Options(mock=True))
    w.show()
    w.monitor.start()
    yield w
    w.monitor.stop()
    w.hide()
    w.session.shutdown()
    w.deleteLater()


def _until(app, condition, timeout_s=5.0):
    end = time.monotonic() + timeout_s
    while not condition() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert condition()


def _look_now(app, window):
    _until(app, lambda: not window.monitor.busy)
    window.monitor.look()
    _until(app, lambda: not window.monitor.busy)


def _buttons(window):
    return {b.text() for b in window.home.findChildren(QPushButton)
            if b.isVisibleTo(window.home)}


def test_home_follows_the_robot_switched_off_and_on(app, window):
    home, session = window.home, window.session
    _until(app, lambda: session.robot is not None)     # carried on with
    assert home.robot_title.text() == "Robot connected"
    assert not home.waiting.isVisibleTo(home)
    assert not {"Connect", "Disconnect robot session",
                "Start new robot session"} & _buttons(window)

    session._api.power_off()
    _look_now(app, window)
    assert home.robot_title.text() == "Connecting to the robot…"
    assert home.robot_text.text() == (
        "This can take a few minutes after it is switched on.")
    assert home.waiting.isVisibleTo(home)
    assert not home.below.isEnabled()

    session._api.power_on()
    _look_now(app, window)
    assert home.robot_title.text() == "Robot needs to home"
    assert "restarted" in home.robot_text.text()
    assert home.home_button.isVisibleTo(home)
    assert home.home_button.text() == "Home and connect"
    assert not home.below.isEnabled()


def _restarted(app, window):
    session = window.session
    _until(app, lambda: session.robot is not None)
    session._api.power_off()
    session._api.power_on()
    _look_now(app, window)
    assert session.needs_home


def test_cancel_on_home_the_robot_moves_nothing(app, window, monkeypatch):
    _restarted(app, window)
    dialog = window.home.home_dialog()
    assert "Keep hands out of the robot" in dialog.labelText()
    monkeypatch.setattr(QInputDialog, "exec",
                        lambda self: QInputDialog.DialogCode.Rejected)
    window.home.home_button.click()
    app.processEvents()
    assert _moves(window.session) == []
    assert window.session.robot is None


def test_home_the_robot_homes_once_confirmed(app, window, monkeypatch):
    _restarted(app, window)
    monkeypatch.setattr(QInputDialog, "exec",
                        lambda self: QInputDialog.DialogCode.Accepted)
    window.home.home_button.click()
    _until(app, lambda: window.session.robot is not None)
    _until(app, lambda: not window.session.robot_lease.busy)
    assert [c[0] for c in _moves(window.session)] == ["create_run",
                                                      "home_robot"]
    assert window.home.robot_title.text() == "Robot connected"
    assert window.home.below.isEnabled()


def test_home_says_homing_while_it_homes(app, window, monkeypatch):
    import threading
    _restarted(app, window)
    gate = threading.Event()
    new_run = window.session.new_run

    def held(*args, **kwargs):
        gate.wait(5)
        return new_run(*args, **kwargs)

    monkeypatch.setattr(window.session, "new_run", held)
    monkeypatch.setattr(QInputDialog, "exec",
                        lambda self: QInputDialog.DialogCode.Accepted)
    window.home.home_button.click()
    app.processEvents()
    assert window.home.robot_title.text() == "Homing…"
    gate.set()
    _until(app, lambda: window.session.robot is not None)
    _until(app, lambda: not window.session.robot_lease.busy)


def test_home_never_says_not_connected(app, window, monkeypatch):
    home, session = window.home, window.session
    _until(app, lambda: session.robot is not None)
    window.monitor.stop()
    # Answering, its session being carried on with.
    monkeypatch.setattr(session, "robot", None)
    monkeypatch.setattr(session, "robot_answers", True)
    home.refresh()
    assert home.robot_title.text() == "Connecting to the robot…"
    assert home.robot_text.text() == "Continuing the robot's session."
    # Its session let go.
    monkeypatch.setattr(session, "released", "run-1")
    home.refresh()
    assert home.robot_title.text() == "Robot session disconnected"
    assert "not connected" not in home.robot_text.text().lower()


def test_a_look_is_not_work_that_stop_waits_for(app, window, monkeypatch):
    from micropick.gui.workers import any_running
    session = window.session
    _until(app, lambda: session.robot is not None)
    gate = __import__("threading").Event()
    looked = session.look_at_robot
    monkeypatch.setattr(session, "look_at_robot",
                        lambda api=None: (gate.wait(5), looked(api))[1])
    window.monitor.look()
    assert window.monitor.busy
    assert not any_running()
    gate.set()
    _until(app, lambda: not window.monitor.busy)


def test_a_session_let_go_is_not_carried_on_with_until_continued(session):
    _look(session)
    session.adopt_run()
    run_id = session.run_state.run_id
    session.release_run()
    assert session.robot is None and session.released == run_id
    assert _look(session) is False                 # let go: not taken back
    assert not session.needs_home
    session._api.create_run()                      # the Opentrons app's
    assert _look(session) is False                 # not taken either
    run_id = session.run_state.run_id
    session.continue_run()
    assert session.released is None
    assert _look(session) is True
    session.adopt_run()
    assert session.run_state.run_id == run_id
    assert "home_robot" not in [c[0] for c in _moves(session)]


def test_a_new_session_after_letting_go_clears_it(session):
    _look(session)
    session.adopt_run()
    session.release_run()
    _look(session)
    session.new_run()
    assert session.robot is not None and session.released is None
