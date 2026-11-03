"""A liquid plan on the mock robot, and what `plan_problems` says about one."""
from __future__ import annotations

import threading

import pytest

from micropick.core.liquid import Location
from micropick.core.liquid_plan import (Height, LiquidPlan, MoveLiquid, Place,
                                        Spot, WaitStep)
from micropick.hardware.mock import MockRobot
from micropick.workflows.liquid import Plate, Stopped
from micropick.workflows.liquid_plan import (PlanState, plan_problems,
                                             run_plan)
from micropick.workflows.motion import Motion

PLATE = Plate("5", "plate-id", "plate", [["A1", "B1"], ["A2", "B2"]],
              center=(0.0, 0.0, 0.0), depth=10.0)
TRASH_PLATE = Plate("2", "trash-id", "res", [["A1"]])
PLATES = {"5": PLATE, "2": TRASH_PLATE}
POSITIONS = {"Reservoir": (200.0, 100.0, 50.0)}
TRASH = Location(kind="well", slot="2", load_name="res", well="A1")
RESERVOIR = Place(kind="point", point="Reservoir")
TO_TRASH = Place(kind="trash")


def _wells(*wells) -> Place:
    return Place(slot="5", load_name="plate", wells=list(wells))


def _plan(*steps, trash=TRASH, **kw) -> LiquidPlan:
    return LiquidPlan(trash=trash, steps=list(steps), **kw)


def _in_plate(robot) -> list:
    return [c for c in robot.calls if c[0] in ("aspirate", "dispense")
            and c[1] == "plate-id"]


def _run(plan, state=None, **kw):
    robot = MockRobot()
    state = state if state is not None else PlanState()
    run_plan(Motion(robot), plan, PLATES, POSITIONS, state, **kw)
    return robot, state


def test_all_steps_run_in_order_after_the_trash_blow_out():
    plan = _plan(
        MoveLiquid(source=_wells("A1"), dest=_wells("A2"), volume_ul=20),
        MoveLiquid(source=_wells("B1"), dest=_wells("B2"), volume_ul=30))
    robot, state = _run(plan)
    assert _in_plate(robot) == [
        ("aspirate", "plate-id", "A1", 20.0, 10.0),
        ("dispense", "plate-id", "A2", 20.0, 10.0),
        ("aspirate", "plate-id", "B1", 30.0, 10.0),
        ("dispense", "plate-id", "B2", 30.0, 10.0)]
    first_blow = next(i for i, c in enumerate(robot.calls)
                      if c[0] == "blow_out")
    assert robot.calls[first_blow][1] == "trash-id"
    assert first_blow < robot.calls.index(_in_plate(robot)[0])
    assert state.done == [s.id for s in plan.steps]


def test_the_blow_out_empties_what_the_tip_held_and_comes_once():
    plan = _plan(MoveLiquid(source=_wells("A1"), dest=_wells("A2")),
                 MoveLiquid(source=_wells("B1"), dest=_wells("B2")))
    robot, state = _run(plan, PlanState(in_tip=30.0))
    assert state.in_tip == 0.0
    assert [c[0] for c in robot.calls].count("blow_out") == 1


def test_in_tip_is_carried_from_step_to_step():
    # 3 × 60 drawn, 2 × 60 used: 60 stay in the tip into the next step.
    plan = _plan(
        MoveLiquid(source=RESERVOIR, dest=_wells("A1", "A2"), volume_ul=60,
                   empty_at_end=False),
        MoveLiquid(source=RESERVOIR, dest=_wells("B1"), volume_ul=60))
    robot, state = _run(plan)
    assert ("aspirate_in_place", 180.0, 10.0) in robot.calls
    assert ("aspirate_in_place", 60.0, 10.0) in robot.calls
    # What the two steps left: 60 µl, emptied at the end of the last.
    assert ("dispense", "trash-id", "A1", 60.0, 200.0) in robot.calls
    assert state.in_tip == 0.0


def test_a_point_to_a_spot_runs_on_the_carrier():
    spot = Place(kind="spot", spot=Spot(name="By hand", x=10, y=20, z=30))
    plan = _plan(MoveLiquid(source=RESERVOIR, dest=spot, volume_ul=40))
    robot, state = _run(plan)
    assert ("aspirate_in_place", 40.0, 10.0) in robot.calls
    assert ("dispense_in_place", 40.0, 10.0) in robot.calls
    assert (10.0, 20.0, 30.0) in [tuple(round(v) for v in p)
                                  for p in robot.log]
    assert state.in_tip == 0.0


def test_the_carrier_does_not_reach_the_callbacks():
    paused, progress = [], []
    pause = threading.Event()

    def on_paused(message):
        paused.append(message)
        pause.clear()

    plan = _plan(MoveLiquid(source=RESERVOIR, dest=TO_TRASH,
                            pause_after_first=True))
    _run(plan, pause=pause, on_paused=on_paused,
         on_progress=lambda *args: progress.append(args))
    assert progress == [(1, 1, 0, None)]
    assert len(paused) == 1 and "A1" not in paused[0]


def test_a_well_is_reported_to_on_progress_by_the_steps_own_count():
    progress = []
    plan = _plan(MoveLiquid(source=_wells("A1", "A2"),
                            dest=_wells("B1", "B2")),
                 MoveLiquid(source=_wells("A1"), dest=_wells("B1")))
    _run(plan, on_progress=lambda *args: progress.append(args))
    assert progress == [(1, 2, 0, "B1"), (2, 2, 1, "B2"), (1, 1, 0, "B1")]


def test_on_step_says_when_a_step_starts_and_ends():
    said = []
    plan = _plan(MoveLiquid(source=_wells("A1"), dest=_wells("A2")),
                 MoveLiquid(source=_wells("B1"), dest=_wells("B2")))
    _run(plan, on_step=lambda index, what: said.append((index, what)))
    assert said == [(0, "started"), (0, "done"), (1, "started"), (1, "done")]


def test_a_stop_keeps_what_the_tip_holds_in_the_plan_state():
    stop = threading.Event()

    def log(text):
        if text.startswith("aspirate 60"):
            stop.set()

    plan = _plan(MoveLiquid(source=_wells("A1"), dest=_wells("A2"),
                            volume_ul=60))
    state = PlanState()
    with pytest.raises(Stopped):
        _run(plan, state, stop=stop, log=log)
    assert state.in_tip == 60.0 and state.done == []


def test_the_bench_wash_on_the_robot():
    wells = _wells("A1", "A2", "B1")
    plan = _plan(
        MoveLiquid(source=wells, dest=TO_TRASH, volume_ul=100,
                   put_flow_ul_s=200, slow_lift=True,
                   take_at=Height(ref="bottom", offset_mm=2.5, aside_x_mm=-2)))
    robot, state = _run(plan)
    # A measured bottom is reached by coordinates, so the draws are in place.
    assert [c for c in robot.calls if c[0] == "aspirate_in_place"] == [
        ("aspirate_in_place", 100.0, 10.0)] * 3
    # 200 µl after the second well, 100 µl after the last.
    assert [c[3] for c in robot.calls if c[:2] == ("dispense", "trash-id")
            and c[3] > 10] == [200.0, 100.0]
    assert state.in_tip == 0.0


def test_a_run_needs_the_trash():
    robot = MockRobot()
    plan = _plan(MoveLiquid(source=_wells("A1"), dest=_wells("A2")), trash=None)
    with pytest.raises(ValueError, match="trash"):
        run_plan(Motion(robot), plan, PLATES, POSITIONS, PlanState())
    assert robot.calls == []


# -- plan_problems -------------------------------------------------------------


def test_a_good_plan_has_no_problems():
    plan = _plan(MoveLiquid(source=RESERVOIR, dest=_wells("A1", "A2")),
                 WaitStep(),
                 MoveLiquid(source=_wells("A1", "A2"), dest=TO_TRASH))
    assert plan_problems(plan, PLATES, POSITIONS) == {}


def test_a_missing_trash_is_the_plans_problem_not_a_steps():
    step = MoveLiquid(source=_wells("A1"), dest=TO_TRASH)
    assert plan_problems(_plan(step, trash=None), PLATES, POSITIONS) == {
        "plan": ["Set the trash to run this plan."]}


def test_a_plan_with_no_steps_says_so():
    assert plan_problems(_plan(), PLATES, POSITIONS) == {
        "plan": ["Add a step to the plan."]}


def test_a_trash_that_is_gone_is_the_plans_problem_too():
    trash = Location(kind="point", point="Gone")
    step = MoveLiquid(source=_wells("A1"), dest=TO_TRASH)
    assert plan_problems(_plan(step, trash=trash), PLATES, POSITIONS) == {
        "plan": ["Trash: there is no saved point 'Gone' in the profile."]}


def test_an_empty_place_is_the_steps_problem():
    step = MoveLiquid(dest=_wells("A1"))
    other = MoveLiquid(source=_wells("A1"))
    both = MoveLiquid()
    assert plan_problems(_plan(step, other, both), PLATES, POSITIONS) == {
        step.id: ["Choose where it comes from."],
        other.id: ["Choose where it goes."],
        both.id: ["Choose where it comes from.", "Choose where it goes."]}


def test_n_to_m_is_a_problem_in_the_rules_words():
    step = MoveLiquid(source=_wells("A1", "A2"),
                      dest=_wells("A1", "A2", "B1"))
    assert plan_problems(_plan(step), PLATES, POSITIONS) == {
        step.id: ["Take from 1 well, or from as many wells as it goes into "
                  "(3)."]}


def test_the_trash_cannot_be_a_source():
    step = MoveLiquid(source=TO_TRASH, dest=_wells("A1"))
    assert plan_problems(_plan(step), PLATES, POSITIONS) == {
        step.id: ["Take from a well or a point."]}


def test_a_volume_over_the_tip_is_said_plainly():
    step = MoveLiquid(source=RESERVOIR, dest=_wells("A1", "A2"), volume_ul=300)
    assert plan_problems(_plan(step), PLATES, POSITIONS) == {
        step.id: ["300 µl is more than a tip holds (200 µl)."]}


def test_a_step_in_a_plate_that_is_gone_says_it_once_as_a_sentence():
    step = MoveLiquid(source=_wells("A1", "A2"), dest=_wells("B1", "B2"))
    assert plan_problems(_plan(step), {"2": TRASH_PLATE}, POSITIONS) == {
        step.id: ["Slot 5 holds nothing now; load plate there on the Deck "
                  "step."]}


def test_a_point_that_is_gone_is_said_without_the_programs_words():
    step = MoveLiquid(source=Place(kind="point", point="Gone"),
                      dest=_wells("A1"))
    (found,) = plan_problems(_plan(step), PLATES, POSITIONS)[step.id]
    assert found == "There is no saved point 'Gone' in the profile."


def test_volumes_are_played_through_all_steps():
    # The first step leaves 60 µl; the second draws 150 more on top.
    first = MoveLiquid(source=RESERVOIR, dest=_wells("A1", "A2"), volume_ul=60,
                       empty_at_end=False)
    second = MoveLiquid(source=RESERVOIR, dest=_wells("B1"), volume_ul=150)
    assert plan_problems(_plan(first, second), PLATES, POSITIONS) == {
        second.id: ["The tip would hold 210 µl, more than the 200 µl it "
                    "takes."]}
    first = first.model_copy(update={"empty_at_end": True})
    assert plan_problems(_plan(first, second), PLATES, POSITIONS) == {}


def test_a_step_too_full_for_its_well_is_said_with_the_capacity_given():
    step = MoveLiquid(source=RESERVOIR, dest=_wells("A1", "A2"), volume_ul=50)
    assert plan_problems(_plan(step), PLATES, POSITIONS,
                         capacities={"5": 36}) == {
        step.id: ["50 µl is more than this well holds (36 µl)."]}


def test_n_into_one_well_adds_up_against_its_capacity():
    step = MoveLiquid(source=_wells("A1", "A2"),
                      dest=_wells("B1"), volume_ul=20)
    assert plan_problems(_plan(step), PLATES, POSITIONS,
                         capacities={"5": 36}) == {
        step.id: ["40 µl is more than this well holds (36 µl)."]}
