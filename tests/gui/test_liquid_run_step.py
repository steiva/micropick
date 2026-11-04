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

from PySide6.QtWidgets import (QApplication, QLabel,             # noqa: E402
                               QPushButton)

from micropick.config.labware import resolve_definition          # noqa: E402
from micropick.core.liquid import Location                       # noqa: E402
from micropick.core.liquid_plan import (BlowOutStep, LiquidPlan,  # noqa: E402
                                        MixStep, MoveLiquid, MoveTip, Place,
                                        Spot, WaitForMe, WaitStep)
from micropick.gui import shell, tasks                           # noqa: E402
from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages import liquid as page_module            # noqa: E402
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
    # All in a row; the page is disabled until it has a plan.
    page.how.set_current(1)
    page._how_chosen(1)
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
    assert _buttons(window) == ["Back to plan"]
    assert tasks.step_state(window.session, "liquid", "run").done
    assert not _stop_shown(window)
    assert page.tip_line.text() == "The tip is empty."


def test_what_the_tip_holds_is_counted_during_a_step(app, window):
    page = window.pages["liquid"]
    _plan(window, MoveLiquid(source=_wells("H1"), dest=_wells("A1", "A2", "A3"),
                             volume_ul=10, wait_after_s=0.3))
    _started(app, page)
    assert _until(app, lambda: page._step_done(page._step()) >= 1)
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
    assert _until(app, lambda: page._step_done(page._step()) >= 1)
    page.stop_button.click()
    assert _until(app, lambda: not page._running and not page.jog.busy)
    tag, sentence = _footer(window)
    assert tag == "READY" and sentence.startswith("Stopped. ")
    well = page._step_done(step) + 1
    assert _buttons(window) == ["Back to plan", "Start the step over",
                                "Start from step 1",
                                f"Continue step 1 from well {well}"]
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


# -- one step at a time --------------------------------------------------------

def _one_at_a_time(page):
    page.how.buttons[0].click()
    assert page.start_button.text() == "Start step 1"


def _three(window):
    _plan(window,
          MoveLiquid(source=_wells("H1"), dest=_wells("A1"), volume_ul=10),
          MoveLiquid(source=_wells("H2"), dest=_wells("B1"), volume_ul=10),
          MoveLiquid(source=_wells("H3"), dest=_wells("C1"), volume_ul=10))


def _puts(window):
    return [c[2] for c in window.session.robot.calls if c[0] == "dispense"
            and c[2] != "H12"]


def _asked(app, page, step):
    assert _until(app, lambda: page._asking == step - 1), page.run_status()


def test_nothing_moves_between_steps_and_the_robot_stays_busy(app, window):
    page = window.pages["liquid"]
    _three(window)
    _one_at_a_time(page)
    _started(app, page)
    _asked(app, page, 1)
    robot, session = window.session.robot, window.session
    assert page.after_banner.label.text().startswith(
        "<b>Step 1 is done. 1 of 1 well.</b>")
    assert page.repeat_button.text() == "Repeat step 1"
    assert page.go_button.text() == "Go to step 2"
    assert _footer(window)[0] == "WAITING FOR YOU"
    assert page.head_caption.text() == "STEP 1 OF 3 · WAITING FOR YOU"
    assert session.robot_lease.busy and _stop_shown(window)
    assert _pill(window) == "Waiting for you"
    assert not page.pause_button.isEnabled()
    calls = len(robot.calls)
    _until(app, lambda: False, timeout_s=0.2)
    assert len(robot.calls) == calls
    assert session.robot_lease.busy


def test_go_to_runs_the_next_step_and_the_last_ends_with_its_banner(
        app, window):
    page = window.pages["liquid"]
    _three(window)
    _one_at_a_time(page)
    _started(app, page)
    _asked(app, page, 1)
    page.go_button.click()
    _asked(app, page, 2)
    assert _puts(window) == ["A1", "B1"]
    page.go_button.click()
    _ended(app, page)
    assert _puts(window) == ["A1", "B1", "C1"]
    assert not page.after_banner.isVisibleTo(page)
    assert page.end_banner.isVisibleTo(page)
    assert page.end_banner.label.text() == "<b>All 3 steps done.</b>"
    assert _buttons(window) == ["Back to plan"]
    assert window.session.is_confirmed("liquid_run")


def test_repeat_redoes_the_step(app, window):
    page = window.pages["liquid"]
    _three(window)
    _one_at_a_time(page)
    _started(app, page)
    _asked(app, page, 1)
    page.repeat_button.click()
    assert _until(app, lambda: _puts(window) == ["A1", "A1"]
                  and page._asking == 0)
    assert page.go_button.text() == "Go to step 2"


def test_finish_here_lets_the_robot_go_and_continue_goes_on(app, window):
    page = window.pages["liquid"]
    _three(window)
    _one_at_a_time(page)
    _started(app, page)
    _asked(app, page, 1)
    page.finish_button.click()
    assert _until(app, lambda: page._ended == "finished" and not page.jog.busy
                  and not window.session.robot_lease.busy)
    assert _puts(window) == ["A1"]
    assert not _stop_shown(window)
    assert _footer(window) == ("READY", "Finished after step 1.")
    assert _buttons(window) == ["Back to plan", "Start from step 1",
                                "Continue step 2"]
    assert not window.session.is_confirmed("liquid_run")
    assert _until(app, page._startable)
    page.continue_button.click()
    _asked(app, page, 2)
    assert _puts(window) == ["A1", "B1"]


def test_a_stop_at_the_banner_keeps_the_step_done(app, window):
    page = window.pages["liquid"]
    _three(window)
    _one_at_a_time(page)
    _started(app, page)
    _asked(app, page, 1)
    window.session.request_stop()
    assert _until(app, lambda: page._ended == "stopped" and not page.jog.busy)
    assert not page.after_banner.isVisibleTo(page)
    assert _buttons(window)[-1] == "Continue step 2"


def test_after_the_last_step_run_again_home_and_back_to_plan(app, window):
    page = window.pages["liquid"]
    _plan(window, MoveLiquid(source=_wells("H1"), dest=_wells("A1"),
                             volume_ul=10))
    _started(app, page)
    _ended(app, page)
    assert _until(app, page.again_button.isEnabled)
    page.again_button.click()
    assert _until(app, lambda: _puts(window) == ["A1", "A1"]
                  and page._ended == "done" and not page.jog.busy)
    assert _until(app, lambda: not window.session.robot_lease.busy)
    buttons = page.end_banner.findChildren(QPushButton)
    by_text = {button.text(): button for button in buttons}
    by_text["Back to plan"].click()
    assert window.current_page() is window.pages["liquid_plan"]
    window.show_step("run")
    by_text["Home"].click()
    assert window.current_page() is window.home


# -- what holds the robot ------------------------------------------------------

@pytest.fixture
def alerts(monkeypatch):
    said = []
    monkeypatch.setattr(shell.QApplication, "alert",
                        lambda *a: said.append(a))
    return said


def test_pause_after_the_first_well_shows_its_strip_and_flashes(app, window,
                                                                alerts):
    page = window.pages["liquid"]
    _plan(window, MoveLiquid(source=_wells("H1", "H2"),
                             dest=_wells("A1", "A2"), volume_ul=10,
                             pause_after_first=True))
    _started(app, page)
    assert _until(app, lambda: page._checking is not None)
    assert page.strip.isVisibleTo(page)
    assert page.strip.label.text() == (
        "<b>First well done. Check it, then continue.</b><br>"
        "You asked for this in the plan.")
    assert len(alerts) == 1
    page.pause_button.click()
    _ended(app, page)
    assert not page.strip.isVisibleTo(page)


def test_wait_for_me_shows_its_message_and_flashes(app, window, alerts):
    page = window.pages["liquid"]
    _plan(window, WaitForMe(message="Add <the> cells."))
    _started(app, page)
    assert _until(app, lambda: page._checking is not None)
    assert "Add &lt;the&gt; cells." in page.strip.label.text()
    assert _pill(window) == "Waiting for you"
    assert len(alerts) == 1
    page.pause_button.click()
    _ended(app, page)


def test_the_banner_after_a_step_flashes_too(app, window, alerts):
    page = window.pages["liquid"]
    _three(window)
    _one_at_a_time(page)
    _started(app, page)
    _asked(app, page, 1)
    assert len(alerts) == 1


def test_paused_says_where_it_goes_on_from(app, window):
    page = window.pages["liquid"]
    _plan(window, MoveLiquid(source=_wells("H1", "H2", "H3"),
                             dest=_wells("A1", "A2", "A3"), volume_ul=10,
                             wait_after_s=0.5))
    _started(app, page)
    assert _until(app, lambda: page._step_done(page._step()) >= 1)
    page.pause_button.click()
    assert _until(app, lambda: page.strip.isVisibleTo(page))
    assert page.strip.label.text().startswith(
        "<b>Paused. The robot holds the tip where it is.</b><br>"
        "Press Continue to go on from well ")
    page.pause_button.click()
    _ended(app, page)


def test_a_wait_shows_the_time_left_and_obeys_pause_and_stop(app, window):
    page = window.pages["liquid"]
    step = WaitStep(seconds=252)
    _plan(window, step)
    _started(app, page)
    assert _until(app, lambda: step.id in page.state.wait_left)
    assert _until(app, lambda: page.counter.text().endswith(" left"))
    assert page.counter.text() in ("4:12 left", "4:11 left")
    assert page.counter.isVisibleTo(page)
    assert not page.counter_note.isVisibleTo(page)
    page.pause_button.click()
    assert _until(app, lambda: _footer(window)[0] == "PAUSED")
    _until(app, lambda: False, timeout_s=0.15)
    held = page.state.wait_left[step.id]
    _until(app, lambda: False, timeout_s=0.2)
    assert page.state.wait_left[step.id] == held
    window.session.request_stop()
    assert _until(app, lambda: page._ended == "stopped" and not page.jog.busy)
    assert page.counter.text().endswith(" left")   # what it carries on with
    assert _buttons(window)[-1] == "Continue step 1"
    assert "Start the step over" in _buttons(window)


def test_time_left_rounds_up():
    assert page_module.time_left(252) == "4:12 left"
    assert page_module.time_left(251.2) == "4:12 left"
    assert page_module.time_left(0.1) == "0:01 left"


# -- after a stop --------------------------------------------------------------

def _stopped_in_step_two(app, window):
    page = window.pages["liquid"]
    _plan(window,
          MoveLiquid(source=_wells("H1"), dest=_wells("G1"), volume_ul=10),
          MoveLiquid(source=_wells("A1", "A2", "A3"),
                     dest=_wells("B1", "B2", "B3"), volume_ul=10,
                     wait_after_s=0.5))
    _started(app, page)
    assert _until(app, lambda: page._index == 1
                  and page._step_done(page._step()) >= 1)
    window.session.request_stop()
    assert _until(app, lambda: page._ended == "stopped" and not page.jog.busy)
    assert _until(app, page._startable)
    return page


def test_stop_keeps_the_steps_and_continue_resumes_at_the_well_it_says(
        app, window):
    page = _stopped_in_step_two(app, window)
    done = page._step_done(page._step())
    assert done >= 1
    assert page.continue_button.text() == f"Continue step 2 from well {done + 1}"
    assert _buttons(window) == ["Back to plan", "Start the step over",
                                "Start from step 1", page.continue_button.text()]
    assert page.cards[page._steps()[0].id].status.text() == "✓ Done"
    page.continue_button.click()
    _ended(app, page)
    # A well put into but still waiting is not put into again.
    assert _puts(window) == ["G1", "B1", "B2", "B3"]


def test_start_the_step_over_redoes_it_from_its_first_well(app, window):
    page = _stopped_in_step_two(app, window)
    before = _puts(window)
    page.over_button.click()
    _ended(app, page)
    assert _puts(window) == before + ["B1", "B2", "B3"]
    assert _puts(window).count("G1") == 1          # step 1 stays done
