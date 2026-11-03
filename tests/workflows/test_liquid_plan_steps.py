"""What a plan can do beyond moving liquid, on the mock robot: every kind of
step, one step alone, stopping and carrying on, Repeat, and Try once."""
from __future__ import annotations

import threading
import time

import pytest

from micropick.core.liquid import Location
from micropick.core.liquid_plan import (BlowOutStep, Height, LiquidPlan,
                                        MixStep, MoveLiquid, MoveTip, Place,
                                        Spot, WaitForMe, WaitStep)
from micropick.hardware.mock import MockRobot
from micropick.workflows.liquid import Plate, Stopped
from micropick.workflows.liquid_plan import PlanState, run_plan
from micropick.workflows.motion import Motion

PLATE = Plate("5", "plate-id", "plate", [["A1", "B1"], ["A2", "B2"]],
              center=(0.0, 0.0, 0.0), depth=10.0)
TRASH_PLATE = Plate("2", "trash-id", "res", [["A1"]])
PLATES = {"5": PLATE, "2": TRASH_PLATE}
POSITIONS = {"Reservoir": (200.0, 100.0, 50.0)}
TRASH = Location(kind="well", slot="2", load_name="res", well="A1")
RESERVOIR = Place(kind="point", point="Reservoir")
SPOT = Place(kind="spot", spot=Spot(name="By hand", x=10, y=20, z=30))


def _wells(*wells) -> Place:
    return Place(slot="5", load_name="plate", wells=list(wells))


def _plan(*steps, trash=TRASH) -> LiquidPlan:
    return LiquidPlan(trash=trash, steps=list(steps))


def _move(source, dest, volume=20, **kw) -> MoveLiquid:
    return MoveLiquid(source=_wells(source), dest=_wells(dest),
                      volume_ul=volume, **kw)


def _in_plate(robot) -> list:
    return [c[:3] for c in robot.calls if c[0] in ("aspirate", "dispense")
            and c[1] == "plate-id"]


def _blow_outs(robot) -> list:
    return [c for c in robot.calls if c[0].startswith("blow_out")]


def _run(plan, state=None, robot=None, **kw):
    robot = robot or MockRobot()
    state = state if state is not None else PlanState()
    run_plan(Motion(robot), plan, PLATES, POSITIONS, state, **kw)
    return robot, state


def _stop_after_well(stop, count=1):
    def progress(done, _total, _gi, _well):
        if done >= count:
            stop.set()
    return progress


def test_only_runs_that_step_alone():
    plan = _plan(_move("A1", "A2"), _move("B1", "B2"), _move("A1", "B2"))
    robot, state = _run(plan, only=1)
    assert _in_plate(robot) == [("aspirate", "plate-id", "B1"),
                                ("dispense", "plate-id", "B2")]
    assert state.done == [plan.steps[1].id]


def test_only_a_later_step_does_not_blow_out_first():
    robot, _ = _run(_plan(_move("A1", "A2"), _move("B1", "B2",
                                                   empty_at_end=False)),
                    only=1)
    assert not _blow_outs(robot)


def test_only_the_first_step_of_a_new_plan_blows_out_first():
    robot, _ = _run(_plan(_move("A1", "A2", empty_at_end=False),
                          _move("B1", "B2")), only=0)
    assert [c[1] for c in _blow_outs(robot)] == ["trash-id"]


def test_step_after_step_with_only_is_the_plan_in_order():
    plan = _plan(_move("A1", "A2"), _move("B1", "B2"))
    robot, state = _run(plan, only=0)
    _run(plan, state, robot, only=1)
    assert [c[2] for c in _in_plate(robot)] == ["A1", "A2", "B1", "B2"]


def test_continue_after_a_stop_resumes_at_the_next_well_with_no_blow_out():
    plan = _plan(_move("A1", "A2"),
                 MoveLiquid(source=_wells("A1", "B1"),
                            dest=_wells("A2", "B2"), volume_ul=20,
                            empty_at_end=False))
    stop, state, robot = threading.Event(), PlanState(), MockRobot()

    def progress(_done, _total, _gi, well):
        if well == "A2" and len(state.done) == 1:
            stop.set()

    with pytest.raises(Stopped):
        _run(plan, state, robot, stop=stop, on_progress=progress)
    robot.calls.clear()
    stop.clear()
    _run(plan, state, robot, stop=stop)
    assert _in_plate(robot) == [("aspirate", "plate-id", "B1"),
                                ("dispense", "plate-id", "B2")]
    assert not _blow_outs(robot)


def test_a_stop_before_the_first_well_does_not_blow_out_again():
    plan = _plan(_move("A1", "A2", empty_at_end=False))
    stop, state, robot = threading.Event(), PlanState(), MockRobot()

    def blown(_text):
        if _blow_outs(robot):
            stop.set()

    with pytest.raises(Stopped):
        _run(plan, state, robot, stop=stop, log=blown)
    robot.calls.clear()
    stop.clear()
    _run(plan, state, robot, stop=stop)
    assert not _blow_outs(robot)
    assert len(_in_plate(robot)) == 2


def test_repeat_redoes_the_step_from_its_first_well_with_in_tip_carried():
    # 180 drawn, 120 used: the 60 left go to A1 the second time, then 180
    # more for A2.
    step = MoveLiquid(source=RESERVOIR, dest=_wells("A1", "A2"), volume_ul=60,
                      empty_at_end=False)
    plan = _plan(step)
    robot, state = _run(plan, only=0)
    assert state.in_tip == 60.0
    robot.calls.clear()
    state.redo(step.id)
    _run(plan, state, robot, only=0)
    assert [c[2] for c in _in_plate(robot)] == ["A1", "A2"]
    assert [c for c in robot.calls if c[0] == "aspirate_in_place"] == [
        ("aspirate_in_place", 180.0, 10.0)]
    assert state.in_tip == 120.0
    assert state.done == [step.id]
    assert not _blow_outs(robot)


def test_a_done_step_is_not_run_again_without_repeat():
    plan = _plan(_move("A1", "A2"))
    robot, state = _run(plan, only=0)
    robot.calls.clear()
    _run(plan, state, robot, only=0)
    assert not _in_plate(robot)


def test_a_wait_takes_its_time():
    begun = time.monotonic()
    _run(_plan(WaitStep(seconds=0.12)))
    assert time.monotonic() - begun >= 0.12


def test_a_wait_stops_at_once_and_continues_with_the_time_it_had_left():
    step = WaitStep(seconds=30)
    plan = _plan(step)
    stop, state = threading.Event(), PlanState()
    timer = threading.Timer(0.1, stop.set)
    timer.start()
    begun = time.monotonic()
    with pytest.raises(Stopped):
        _run(plan, state, stop=stop)
    assert time.monotonic() - begun < 5
    assert 29 < state.wait_left[step.id] < 30
    assert step.id not in state.done
    # Almost done when stopped: a Continue is short, not the 30 s again.
    state.wait_left[step.id] = 0.05
    stop.clear()
    begun = time.monotonic()
    _run(plan, state, stop=stop)
    assert time.monotonic() - begun < 5
    assert state.done == [step.id] and not state.wait_left


def test_a_stopped_wait_as_the_first_step_does_not_blow_out_again():
    plan = _plan(WaitStep(seconds=30), _move("A1", "A2", empty_at_end=False))
    stop, state, robot = threading.Event(), PlanState(), MockRobot()
    threading.Timer(0.1, stop.set).start()
    with pytest.raises(Stopped):
        _run(plan, state, robot, stop=stop)
    robot.calls.clear()
    stop.clear()
    state.wait_left[plan.steps[0].id] = 0.01
    _run(plan, state, robot, stop=stop)
    assert not _blow_outs(robot)


def test_a_paused_wait_does_not_count_down():
    step = WaitStep(seconds=0.2)
    pause, state, said = threading.Event(), PlanState(), []
    pause.set()
    threading.Timer(0.5, pause.clear).start()
    # The trash blow-out would hold at the pause too: it is past already.
    state.cleared = True
    _run(_plan(step), state, pause=pause,
         on_step=lambda index, what: said.append(what))
    assert said == ["started", "paused", "done"]
    assert state.done == [step.id]


def test_a_pause_during_a_wait_holds_it_with_the_time_left_kept():
    step = WaitStep(seconds=30)
    pause, stop, state = threading.Event(), threading.Event(), PlanState()
    threading.Timer(0.1, pause.set).start()
    threading.Timer(0.3, stop.set).start()
    with pytest.raises(Stopped):
        _run(_plan(step), state, pause=pause, stop=stop)
    # 0.1 s went by before the pause; the 0.2 s of the pause are not counted.
    assert state.wait_left[step.id] > 29.8


def test_wait_for_me_reports_its_message_and_holds_the_run():
    messages, said = [], []
    pause = threading.Event()

    def on_paused(message):
        messages.append(message)
        assert pause.is_set()
        pause.clear()

    plan = _plan(WaitForMe(message="Add the medium."), _move("A1", "A2"))
    _, state = _run(plan, pause=pause, on_paused=on_paused,
                    on_step=lambda i, what: said.append((i, what)))
    assert messages == ["Add the medium."]
    assert said == [(0, "started"), (0, "waiting"), (0, "done"),
                    (1, "started"), (1, "done")]
    assert len(state.done) == 2


def test_a_continue_after_a_stop_does_not_wait_for_me_again():
    pause, stop, state = threading.Event(), threading.Event(), PlanState()
    messages = []

    def on_paused(message):
        messages.append(message)
        stop.set()

    plan = _plan(WaitForMe(), _move("A1", "A2"))
    robot = MockRobot()
    with pytest.raises(Stopped):
        _run(plan, state, robot, pause=pause, stop=stop, on_paused=on_paused)
    pause.clear()
    stop.clear()
    _run(plan, state, robot, pause=pause, stop=stop, on_paused=on_paused)
    assert len(messages) == 1
    assert len(_in_plate(robot)) == 2


def test_on_step_says_paused_for_the_operators_pause_in_a_well():
    pause, said = threading.Event(), []

    def progress(_done, _total, _gi, well):
        if well == "A2":
            pause.set()
            threading.Timer(0.1, pause.clear).start()

    plan = _plan(MoveLiquid(source=_wells("A1", "B1"),
                            dest=_wells("A2", "B2"), volume_ul=20))
    _run(plan, pause=pause, on_progress=progress,
         on_step=lambda i, what: said.append(what))
    assert said == ["started", "paused", "done"]


def test_on_step_says_waiting_after_the_first_well_when_the_step_asks():
    pause, said = threading.Event(), []

    def on_paused(_message):
        pause.clear()

    plan = _plan(_move("A1", "A2", pause_after_first=True))
    _run(plan, pause=pause, on_paused=on_paused,
         on_step=lambda i, what: said.append(what))
    assert said == ["started", "waiting", "done"]


def test_a_mix_step_mixes_each_of_its_wells():
    robot, _ = _run(_plan(MixStep(wells=_wells("A1", "A2"), cycles=2,
                                  volume_ul=30)))
    assert [c[2] for c in _in_plate(robot)] == ["A1", "A1", "A1", "A1",
                                                "A2", "A2", "A2", "A2"]


def test_move_the_tip_goes_to_a_well_and_to_a_point():
    robot, _ = _run(_plan(MoveTip(place=_wells("B2"),
                                  at=Height(ref="top", offset_mm=5)),
                          MoveTip(place=RESERVOIR)))
    assert any(c[0] == "move_to_well" and c[1:3] == ("plate-id", "B2")
               for c in robot.calls)
    assert not _in_plate(robot)


@pytest.mark.parametrize("place, where", [
    (_wells("B2"), "plate-id"), (Place(kind="trash"), "trash-id")])
def test_blow_out_in_a_well_and_over_the_trash(place, where):
    robot, _ = _run(_plan(_move("A1", "A2", empty_at_end=False),
                          BlowOutStep(place=place)))
    assert _blow_outs(robot)[-1][:2] == ("blow_out", where)


@pytest.mark.parametrize("place", [RESERVOIR, SPOT])
def test_blow_out_at_a_point_and_at_a_spot_is_in_place(place):
    robot, _ = _run(_plan(_move("A1", "A2", empty_at_end=False),
                          BlowOutStep(place=place)))
    assert _blow_outs(robot)[-1][0] == "blow_out_in_place"


def test_a_blow_out_step_leaves_the_tip_empty():
    _, state = _run(_plan(
        MoveLiquid(source=RESERVOIR, dest=_wells("A1", "A2"), volume_ul=60,
                   empty_at_end=False),
        BlowOutStep(place=SPOT)))
    assert state.in_tip == 0.0


def test_a_wait_after_each_well_holds_each_well():
    begun = time.monotonic()
    robot, _ = _run(_plan(MoveLiquid(source=_wells("A1", "B1"),
                                     dest=_wells("A2", "B2"), volume_ul=20,
                                     wait_after_s=0.1)))
    assert time.monotonic() - begun >= 0.2
    assert len(_in_plate(robot)) == 4


def test_try_once_does_the_first_pair_and_leaves_the_tip_there():
    plan = _plan(MoveLiquid(source=_wells("A1", "B1"),
                            dest=_wells("A2", "B2"), volume_ul=20))
    robot, _ = _run(plan, only=0, first_pair=True)
    assert _in_plate(robot) == [("aspirate", "plate-id", "A1"),
                                ("dispense", "plate-id", "A2")]
    assert not _blow_outs(robot)
    assert robot.calls[-1][0] == "dispense"


def test_try_once_skips_the_emptying_so_the_tip_says_what_it_holds():
    plan = _plan(MoveLiquid(source=RESERVOIR, dest=_wells("A1", "A2"),
                            volume_ul=50))
    robot, state = _run(plan, only=0, first_pair=True)
    assert ("aspirate_in_place", 200.0, 10.0) in robot.calls
    assert state.in_tip == 150.0
    assert not _blow_outs(robot)


def test_try_once_is_not_the_plans_progress():
    plan = _plan(_move("A1", "A2"))
    _, state = _run(plan, only=0, first_pair=True)
    assert state.fresh and state.done == []


def test_try_once_needs_no_trash():
    plan = _plan(_move("A1", "A2"), trash=None)
    robot, _ = _run(plan, only=0, first_pair=True)
    assert len(_in_plate(robot)) == 2


@pytest.mark.parametrize("step", [
    MoveLiquid(source=_wells("A1"), dest=Place(kind="trash")),
    BlowOutStep()])
def test_try_once_into_the_trash_with_none_set_is_refused(step):
    robot = MockRobot()
    with pytest.raises(ValueError, match="^Set the trash to try this step.$"):
        _run(_plan(step, trash=None), robot=robot, only=0, first_pair=True)
    assert not robot.calls


def test_try_once_into_the_trash_empties_there():
    plan = _plan(MoveLiquid(source=_wells("A1", "B1"),
                            dest=Place(kind="trash"), volume_ul=20))
    robot, state = _run(plan, only=0, first_pair=True)
    assert ("aspirate", "plate-id", "A1") in _in_plate(robot)
    assert any(c[0] == "dispense" and c[1] == "trash-id" for c in robot.calls)
    assert state.in_tip == 0.0


def test_try_once_on_a_step_with_a_problem_says_it():
    robot = MockRobot()
    plan = _plan(MoveLiquid(source=_wells("A1")))
    with pytest.raises(ValueError, match="^Choose where it goes.$"):
        _run(plan, robot=robot, only=0, first_pair=True)
    assert not robot.calls


def test_try_once_has_nothing_to_try_in_a_wait():
    with pytest.raises(ValueError, match="nothing to try"):
        _run(_plan(WaitStep()), only=0, first_pair=True)


def test_try_once_on_step_reports_started_and_done():
    said = []
    _run(_plan(_move("A1", "A2")), only=0, first_pair=True,
         on_step=lambda i, what: said.append((i, what)))
    assert said == [(0, "started"), (0, "done")]
