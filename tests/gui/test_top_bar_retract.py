"""Retract in the top bar (#108): the tip straight up to the travel height,
no confirmation, through the shown page's jog panel or on Park's path;
refused as Park is; and its key, R, rebound or cleared in Settings."""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QKeySequence, QShortcut                # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox          # noqa: E402

from micropick.gui import keys                                   # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.shell import MainWindow                       # noqa: E402
from micropick.gui.widgets.jog_panel import JogPanel             # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    w = MainWindow(Options(mock=True))
    yield w
    w.session.shutdown()
    w.deleteLater()


def _settle(app, window, timeout_s=5.0):
    end = time.monotonic() + timeout_s
    while time.monotonic() < end and (
            window._robot_busy()
            or any(p.job_pending for p in window.findChildren(JogPanel))):
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def _connect(app, window):
    window.session.probe_robot()
    window.session.new_run()
    _settle(app, window)
    window.session.motion.retract()
    _settle(app, window)


def _rises(window, monkeypatch):
    calls = []
    motion = window.session.motion
    rise = motion.rise

    def counted(**kwargs):
        calls.append(kwargs)
        return rise(**kwargs)

    monkeypatch.setattr(motion, "rise", counted)
    return calls


def test_it_stands_after_park_and_before_the_divider(app, window):
    row = window.top.layout()
    places = {row.itemAt(i).widget(): i for i in range(row.count())}
    assert window.top.retract.text() == "Retract"
    assert places[window.top.retract] > places[window.top.park]
    assert places[window.top.retract] < places[window.top.settings]
    between = [row.itemAt(i).widget() for i in range(
        places[window.top.retract] + 1, places[window.top.settings])]
    assert any(w is not None and w.objectName() == "barDivider"
               for w in between)


def test_one_click_rises_with_no_question(app, window, monkeypatch):
    _connect(app, window)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: pytest.fail(
        "Retract asks nothing"))
    calls = _rises(window, monkeypatch)
    assert window.top.retract.isEnabled()
    window.top.retract.click()
    _settle(app, window)
    assert len(calls) == 1
    assert window.top.retract.text() == "Retract"


def test_it_shows_retracting_on_its_own_worker(app, window, monkeypatch):
    _connect(app, window)
    window.top.retract.click()
    assert window.top.retract.text() == "Retracting…"
    _settle(app, window)
    assert window.top.retract.text() == "Retract"
    assert window.top.retract.isEnabled()


def test_it_goes_through_the_shown_pages_panel(app, window, monkeypatch):
    _connect(app, window)
    window.resize(1400, 900)
    window.show()
    window.open_task_step("manual", "work")
    _settle(app, window)
    panels = [p for p in window.current_page().findChildren(JogPanel)
              if p.isVisible()]
    assert panels
    jobs = []
    run_job = panels[0].run_job
    monkeypatch.setattr(panels[0], "run_job", lambda fn, **kw: (
        jobs.append(fn), run_job(fn, **kw))[1])
    window.top.retract.click()
    assert window.top.retract.text() == "Retracting…"
    _settle(app, window)
    assert len(jobs) == 1
    assert window.top.retract.text() == "Retract"


def test_a_failed_retract_lets_the_button_go(app, window, monkeypatch):
    _connect(app, window)

    def fails(**_kwargs):
        raise RuntimeError("no answer")

    monkeypatch.setattr(window.session.motion, "rise", fails)
    window.top.retract.click()
    _settle(app, window)
    assert window.top.retract.text() == "Retract"
    assert window.top.retract.isEnabled()


def test_refused_while_busy_with_no_robot_call(app, window, monkeypatch):
    _connect(app, window)
    calls = _rises(window, monkeypatch)
    lease = window.session.robot_lease.claim("a picking run", run=True)
    app.processEvents()
    window._refresh()
    assert not window.top.retract.isEnabled()
    assert "picking run" in window.top.retract.toolTip()
    window._retract()
    _settle_events(app)
    assert calls == []
    assert window.toast.text() == "Robot busy."
    lease.release()


def _settle_events(app):
    for _ in range(5):
        app.processEvents()


def test_disabled_and_refused_while_locked(app, window, monkeypatch):
    _connect(app, window)
    calls = _rises(window, monkeypatch)
    window._set_locked(True)
    assert not window.top.retract.isEnabled()
    assert window.top.retract.toolTip() == "Robot locked."
    window._retract()
    _settle(app, window)
    assert calls == []
    window._set_locked(False)
    assert window.top.retract.isEnabled()


def test_the_action_has_r_by_default():
    assert keys.key_text({}, "retract") == "R"
    assert keys.problems({}) == []


def test_a_key_bound_in_settings_retracts(app, window, monkeypatch):
    _connect(app, window)
    calls = _rises(window, monkeypatch)
    settings = window.session.settings.model_copy(
        update={"keys": {"retract": "F6"}})
    window.session.set_settings(settings)
    bound = [s for s in window.findChildren(QShortcut)
             if s.parent() is window and s.key() == QKeySequence("F6")]
    assert len(bound) == 1
    bound[0].activated.emit()
    _settle(app, window)
    assert len(calls) == 1


def test_a_bound_key_is_in_the_pads_list(app, window):
    panel = window.findChildren(JogPanel)[0]
    assert "R   retract the tip" in panel._pad_help()
    window.session.set_settings(window.session.settings.model_copy(
        update={"keys": {"retract": ""}}))
    assert not any("retract" in line for line in panel._pad_help())


def test_the_settings_card_binds_and_clears_it(app, window):
    sheet = window.settings_sheet
    page = next(w for w in sheet.findChildren(object)
                if hasattr(w, "key_edits"))
    assert page.key_edits["retract"].keySequence() == QKeySequence("R")
    assert page._typed_keys() == {}
    page.key_edits["retract"].setKeySequence(QKeySequence("F6"))
    assert page._typed_keys() == {"retract": "F6"}
    assert page.key_edits["retract"].isClearButtonEnabled()
    assert not page.key_edits["x-"].isClearButtonEnabled()
    page.key_edits["retract"].clear()
    assert page._typed_keys() == {"retract": ""}


def test_the_machine_card_is_gone(app, window):
    panel = window.findChildren(JogPanel)[0]
    assert not hasattr(panel, "home_button")
    assert not hasattr(panel, "retract_button")
