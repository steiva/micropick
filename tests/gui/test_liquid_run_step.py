"""Liquid's Run step (LIQUID.md §6): what the robot is doing now, the footer
as on Picking.

The main window under --mock, on Liquid's Run step, running a plan on the
mock robot: Start, Pause, Continue, a pause the plan asks for, Stop run, and
a run to its end.
"""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel               # noqa: E402

from micropick.config.labware import resolve_definition          # noqa: E402
from micropick.core.liquid import Location                       # noqa: E402
from micropick.core.liquid_plan import (BlowOutStep, LiquidPlan,  # noqa: E402
                                        MixStep, MoveLiquid, MoveTip, Place,
                                        Spot, WaitForMe, WaitStep)
from micropick.gui import tasks                                  # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.shell import MainWindow                       # noqa: E402

PLATE = "corning_96_wellplate_360ul_flat"
SIX = "corning_6_wellplate_16.8ml_flat"
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
    session.load_labware(resolve_definition(SIX), 2)
    _until(app, lambda: not session.robot_lease.busy)
    session.tip = session.tip.__class__(attached=True)
    page = w.pages["liquid"]
    monkeypatch.setattr(page, "_confirm_start", lambda: True)
    w.resize(1440, 900)
    w.show()
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


def _wells(*names):
    return Place(slot="5", load_name=PLATE, wells=list(names))


def _plan(window, *steps):
    window.session.set_liquid_plan(LiquidPlan(trash=TRASH, steps=list(steps)))
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


def _started(app, page):
    # A read of the pose may hold the robot for a moment.
    assert _until(app, page._startable), page.run_status()
    page.start_button.click()


def _ended(app, page):
    assert _until(app, lambda: page._ended is not None and not page.jog.busy)
    assert page._ended == "done", page._outcome


def _said(window, page):
    """Every word of the page and the footer."""
    labels = page.findChildren(QLabel) + window.footer.findChildren(QLabel)
    return " ".join(label.text() for label in labels)


def test_not_ready_without_a_plan(app, window):
    tag, sentence = _footer(window)
    assert tag == "NOT READY"
    assert sentence == window._need.text             # the banner's need
    assert _buttons(window) == ["Back to plan", "Start"]
    assert not window.pages["liquid"].start_button.isEnabled()


def test_before_the_start_it_says_what_will_run(app, window):
    page = window.pages["liquid"]
    _plan(window,
          MoveLiquid(source=_wells("H1"), dest=_wells("A1", "A2", "A3"),
                     volume_ul=10),
          WaitStep(seconds=1))
    assert page.head_caption.text() == "READY"
    assert page.head_title.text() == "Slot 5 · H1 → Slot 5 · A1–A3 (3)"
    assert page.counter.text() == "0 / 3"
    assert page.counter_note.text() == "wells done in this step"
    cards = list(page.cards.values())
    assert [c.status.text() for c in cards] == ["First", "Next"]
    assert not cards[0].more.isVisibleTo(page)       # to read, nothing else
    assert not cards[0].handle.isVisibleTo(page)
    assert page.trash_line.text() == "Trash: Slot 5 · H12"
    assert page.taking.caption.text() == "Taking from"
    assert page.putting.caption.text() == "Putting into"
    assert page.taking.note.text() == ("10 µl per well · refills when the tip "
                                       "runs low")
    assert "still to do" in page.putting.note.text()


def test_both_plates_are_drawn_to_the_same_size(app, window):
    page = window.pages["liquid"]
    six = Place(slot="2", load_name=SIX, wells=["A1"])
    _plan(window, MoveLiquid(source=six, dest=_wells("A1", "A2"), volume_ul=10))
    app.processEvents()
    assert len(page.taking.plate.wells) == 6
    assert len(page.putting.plate.wells) == 96
    assert page.taking.plate.size() == page.putting.plate.size()
    assert page.taking.plate.plate_rect() == page.putting.plate.plate_rect()


def test_a_plan_runs_to_its_end_with_the_counter_and_the_plates_following(
        app, window):
    page = window.pages["liquid"]
    step = MoveLiquid(source=_wells("H1", "H2", "H3"),
                      dest=_wells("A1", "A2", "A3"), volume_ul=10,
                      wait_after_s=0.3)
    other = MoveLiquid(source=_wells("G1"), dest=_wells("A1"), volume_ul=10)
    _plan(window, step, other)
    _started(app, page)
    assert _until(app, lambda: page.counter.text() == "1 / 3"), \
        page.counter.text()
    assert page.head_caption.text() == "STEP 1 OF 2 · RUNNING"
    plate = page.putting.plate
    assert set(plate._fills) == {"A1"}               # done
    assert set(plate._dim_fills) == {"A2", "A3"}     # still to do
    assert plate._current == "A2"                    # now
    assert set(page.taking.plate._fills) == {"H1"}
    assert page.taking.plate._current == "H2"
    assert page.bar.fraction == pytest.approx(1 / 3)
    assert _footer(window) == ("RUNNING", "Now at well A2.")
    card = page.cards[step.id]
    assert card.status.text() == "Running · 1/3"
    assert card.property("open") is True
    assert page.cards[other.id].status.text() == "Next"

    _ended(app, page)
    assert page.head_caption.text() == "STEP 2 OF 2 · DONE"
    assert page.cards[step.id].status.text() == "✓ Done"
    assert page.cards[step.id].graphicsEffect() is not None
    assert page.counter.text() == "1 / 1"
    # What the step before did is on its plate in its color.
    assert {"A1", "A2", "A3"} <= set(page.putting.plate._fills)
    assert page.putting.plate._fills["A2"].name() == step.color
    assert _footer(window) == ("DONE", "All 2 steps done.")
    assert _buttons(window) == ["Back to plan", "Start"]
    assert tasks.step_state(window.session, "liquid", "run").done
    assert not _stop_shown(window)
    assert page.tip_line.text() == "The tip is empty."


def test_what_the_tip_holds_is_counted_during_a_step(app, window):
    page = window.pages["liquid"]
    _plan(window, MoveLiquid(source=_wells("H1"), dest=_wells("A1", "A2", "A3"),
                             volume_ul=10, wait_after_s=0.3))
    _started(app, page)
    assert _until(app, lambda: page.counter.text() == "1 / 3")
    assert page.tip_line.text().startswith("The tip holds ")
    _ended(app, page)


def test_back_to_plan_is_gray_with_its_sentence_during_a_run(app, window):
    page = window.pages["liquid"]
    plan_page = window.pages["liquid_plan"]
    _plan(window, MoveLiquid(source=_wells("H1"), dest=_wells("A1", "A2"),
                             volume_ul=10, wait_after_s=0.5))
    assert page.back_button.isEnabled()
    _started(app, page)
    assert _until(app, lambda: page._running)
    assert not page.back_button.isEnabled()
    assert _buttons(window) == [
        "The plan can be changed when the robot is not moving.",
        "Back to plan", "Pause", "Stop run"]
    assert page.back_button.toolTip() == page.back_note.text()
    assert not plan_page.add_button.isEnabled()
    window.session.request_stop()
    assert _until(app, lambda: not page._running and not page.jog.busy)
    assert page.back_button.isEnabled()
    page.back_button.click()
    assert window.current_page() is plan_page


def test_the_pill_says_the_step_and_a_click_comes_back(app, window):
    alerts = []
    page = window.pages["liquid"]
    _plan(window,
          MoveLiquid(source=_wells("H1"), dest=_wells("A1"), volume_ul=10),
          WaitForMe(message="look at it"),
          MoveLiquid(source=_wells("H2"), dest=_wells("B1", "B2"),
                     volume_ul=10, wait_after_s=0.3))
    QApplication.alert, keep = (lambda w, ms=0: alerts.append(w),
                                QApplication.alert)
    try:
        _started(app, page)
        assert _until(app, lambda: _pill(window) == "Waiting for you"), \
            _pill(window)
        assert _footer(window) == ("WAITING FOR YOU", "look at it")
        assert page.head_caption.text() == "STEP 2 OF 3 · WAITING FOR YOU"
        assert page.pause_button.text() == "Continue"
        assert page.pause_button.property("tone") == "warn"
        assert alerts == [window]                  # the taskbar flashes
    finally:
        QApplication.alert = keep

    window.open_home()
    app.processEvents()
    assert window.current_page() is not page
    window.go_to_run()
    assert window.current_page() is page

    page.pause_button.click()                      # Continue
    assert _until(app, lambda: _pill(window) == "Liquid · step 3 of 3"), \
        _pill(window)
    assert _footer(window)[0] == "RUNNING"
    page.pause_button.click()                      # Pause
    assert _until(app, lambda: _pill(window) == "Paused")
    assert _footer(window)[0] == "PAUSED"
    assert page.head_caption.text() == "STEP 3 OF 3 · PAUSED"
    assert _buttons(window)[-2:] == ["Continue", "Stop run"]
    assert page.pause_button.property("tone") == "warn"
    assert list(page.cards.values())[2].status.text() == "Paused"
    page.pause_button.click()
    _ended(app, page)
    assert _pill(window) is None


def test_stop_keeps_the_progress_and_continue_carries_on(app, window):
    page = window.pages["liquid"]
    step = MoveLiquid(source=_wells("H1"), dest=_wells("A1", "A2", "A3"),
                      volume_ul=10, wait_after_s=0.3)
    _plan(window, step)
    _started(app, page)
    assert _until(app, lambda: page.counter.text() == "1 / 3")
    page.stop_button.click()
    assert _until(app, lambda: not page._running and not page.jog.busy)
    tag, sentence = _footer(window)
    assert tag == "READY" and sentence.startswith("Stopped. ")
    assert _buttons(window) == ["Back to plan", "Continue", "Start"]
    assert not window.session.robot_lease.busy
    assert not _stop_shown(window)
    assert not tasks.step_state(window.session, "liquid", "run").done

    assert _until(app, page._startable)
    calls = window.session.robot.calls
    before = len(calls)
    page.continue_button.click()
    _ended(app, page)
    assert page.counter.text() == "3 / 3"
    # On from the next well, with no blow-out before it.
    went_on = [c for c in calls[before:]
               if c[0] in ("aspirate", "dispense", "blow_out")]
    assert went_on[0][0] != "blow_out"
    assert [c[2] for c in went_on if c[0] == "dispense"][0] != "A1"
    assert tasks.step_state(window.session, "liquid", "run").done


def test_the_run_never_says_aspirating_or_dispensing(app, window):
    """On every look at the page and the footer while a plan of every kind of
    step runs."""
    import re
    banned = re.compile(r"aspirat|dispens", re.IGNORECASE)
    page = window.pages["liquid"]
    _plan(window,
          MoveLiquid(source=_wells("H1"), dest=_wells("A1", "A2"),
                     volume_ul=10, wait_after_s=0.05),
          MixStep(wells=_wells("A1"), volume_ul=5),
          BlowOutStep(),
          MoveTip(place=_wells("C1")),
          WaitStep(seconds=0.05))
    said = []
    _started(app, page)
    while _until(app, lambda: True) and (page._running or page.jog.busy):
        said.append(_said(window, page))
        time.sleep(0.01)
    assert page._ended == "done", page._outcome
    assert said
    assert not [text for text in said if banned.search(text)]


def test_a_step_with_one_place_shows_one_card_and_a_wait_none(app, window):
    page = window.pages["liquid"]
    _plan(window, MixStep(wells=_wells("A1", "A2"), volume_ul=5),
          WaitStep(seconds=60))
    assert not page.taking.isVisibleTo(page)
    assert page.putting.isVisibleTo(page)
    assert page.putting.caption.text() == "Mixing in"
    assert not page.arrow.isVisibleTo(page)
    assert page.head_title.text() == "Slot 5 · A1–A2 (2)"
    page._index = 1
    page._refresh()
    assert not page.putting.isVisibleTo(page)
    assert page.head_title.text() == "Wait 1 min"
    assert not page.counter.isVisibleTo(page)


def test_a_point_and_the_trash_are_shown_by_name(app, window):
    page = window.pages["liquid"]
    window.session.remember("Reservoir", (120.0, 90.0, 60.0))
    _plan(window,
          MoveLiquid(source=Place(kind="point", point="Reservoir"),
                     dest=Place(kind="trash")),
          MoveLiquid(source=Place(kind="spot", spot=Spot(
              name="By hand", x=1, y=2, z=3)), dest=_wells("A1")))
    taking, putting = page.taking, page.putting
    assert taking.place_name.text() == "Point · Reservoir"
    assert taking.place_facts.text() == "X 120.0  Y 90.0  Z 60.0"
    assert putting.place_name.text() == "Into the trash"
    assert putting.place_facts.text() == "Slot 5 · H12"
    assert not putting.icon.pixmap().isNull()
    page._index = 1
    page._refresh()
    assert taking.place_name.text() == "By hand"
    assert taking.place_facts.text() == "X 1.0  Y 2.0  Z 3.0"
    assert putting.stack.currentWidget() is putting.plate
