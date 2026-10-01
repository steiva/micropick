"""Running a liquid handling program on the mock robot."""
from __future__ import annotations

import threading

import pytest

from micropick.core.liquid import (Aspirate, BlowOut, Dispense, Group,
                                   Location, Mix, MoveTo, Pause, Program)
from micropick.hardware.mock import MockRobot
from micropick.workflows import liquid
from micropick.workflows.liquid import LiquidState, Plate, Stopped

PLATE = Plate("5", "plate-id", "plate", [["A1", "B1"], ["A2", "B2"]])
RESERVOIR = Plate("2", "res-id", "res", [["A1"]])
PLATES = {"5": PLATE, "2": RESERVOIR}
POSITIONS = {"waste": (200.0, 100.0, 50.0)}
WASTE = Location(kind="well", slot="2", load_name="res", well="A1")


def _program(steps, wells=("A1", "A2")) -> Program:
    return Program(groups=[Group(name="g", slot="5", load_name="plate",
                                 wells=list(wells), steps=steps)])


def _liquid_calls(robot):
    return [c for c in robot.calls if c[0] != "retract_axis"]


def test_every_well_gets_the_whole_chain_before_the_next():
    robot, state = MockRobot(), LiquidState()
    program = _program([Aspirate(volume_ul=20),
                        Dispense(location=WASTE)])
    liquid.run(robot, program, PLATES, POSITIONS, state)
    assert _liquid_calls(robot) == [
        ("move_to_well", "plate-id", "A1", "top"),
        ("aspirate_in_place", 20.0, 50.0),
        ("move_to_well", "res-id", "A1", "top"),
        ("dispense_in_place", 20.0, 50.0),
        ("move_to_well", "plate-id", "A2", "top"),
        ("aspirate_in_place", 20.0, 50.0),
        ("move_to_well", "res-id", "A1", "top"),
        ("dispense_in_place", 20.0, 50.0)]
    assert state.done == [(0, "A1"), (0, "A2")]
    assert state.in_tip == 0.0
    assert robot.calls[-1] == ("retract_axis", "leftZ")


def test_the_same_place_and_here_do_not_move():
    robot = MockRobot()
    program = _program([Aspirate(volume_ul=10), Mix(cycles=2, volume_ul=5),
                        BlowOut(), Dispense(location=Location(kind="here"))],
                       wells=["A1"])
    liquid.run(robot, program, PLATES, POSITIONS, LiquidState())
    moves = [c for c in robot.calls if c[0] == "move_to_well"]
    assert moves == [("move_to_well", "plate-id", "A1", "top")]
    # The blow out is in the well, so it is the well-based one and the
    # shake that prepares the plunger.
    assert [c[0] for c in _liquid_calls(robot)][1:] == [
        "aspirate_in_place", "aspirate_in_place", "dispense_in_place",
        "aspirate_in_place", "dispense_in_place", "blow_out", "aspirate",
        "dispense"]


def test_a_point_is_reached_with_its_offset():
    robot = MockRobot()
    program = _program([MoveTo(location=Location(kind="point", point="waste",
                                                 offset=[1, 2, 3]))],
                       wells=["A1"])
    state = LiquidState()
    liquid.run(robot, program, PLATES, POSITIONS, state)
    assert (201.0, 102.0, 53.0) in [tuple(round(v) for v in p)
                                    for p in robot.log]


def test_a_stop_keeps_the_place_and_continue_does_not_repeat_a_step():
    robot, state = MockRobot(), LiquidState()
    stop = threading.Event()
    calls = []

    def log(text):
        calls.append(text)
        if text.startswith("aspirate") and len(
                [c for c in calls if c.startswith("aspirate")]) == 1:
            stop.set()

    program = _program([Aspirate(volume_ul=20), Dispense(location=WASTE)])
    with pytest.raises(Stopped):
        liquid.run(robot, program, PLATES, POSITIONS, state, stop=stop, log=log)
    assert state.in_tip == 20.0
    assert state.done == []
    assert state.resume == {(0, "A1"): 1}

    robot.calls.clear()
    stop.clear()
    liquid.run(robot, program, PLATES, POSITIONS, state, stop=stop)
    aspirates = [c for c in robot.calls if c[0] == "aspirate_in_place"]
    # A1's aspirate is not done again; only A2's.
    assert len(aspirates) == 1
    assert state.done == [(0, "A1"), (0, "A2")]


def test_here_after_a_stop_goes_back_first():
    robot, state = MockRobot(), LiquidState()
    stop = threading.Event()
    program = _program([Aspirate(volume_ul=20),
                        Dispense(location=Location(kind="here"))],
                       wells=["A1"])

    def log(text):
        if text.startswith("aspirate"):
            stop.set()

    with pytest.raises(Stopped):
        liquid.run(robot, program, PLATES, POSITIONS, state, stop=stop, log=log)
    robot.calls.clear()
    stop.clear()
    liquid.run(robot, program, PLATES, POSITIONS, state)
    assert _liquid_calls(robot) == [("move_to_well", "plate-id", "A1", "top"),
                                    ("dispense_in_place", 20.0, 50.0)]


def test_a_pause_step_holds_the_run_until_continue():
    robot, state = MockRobot(), LiquidState()
    pause, said = threading.Event(), []

    def paused(message):
        said.append(message)
        pause.clear()                   # the operator presses Continue

    program = _program([Pause(message="look"), Aspirate(volume_ul=5)],
                       wells=["A1"])
    liquid.run(robot, program, PLATES, POSITIONS, state, pause=pause,
               on_paused=paused)
    assert said == ["look"]
    assert state.done == [(0, "A1")]


def test_problems_name_what_is_missing():
    program = Program(groups=[
        Group(name="g", slot="7", load_name="plate", wells=["A1"],
              steps=[Aspirate()]),
        Group(name="h", slot="5", load_name="plate", wells=["Z9"],
              steps=[MoveTo(location=Location(kind="point", point="nowhere")),
                     MoveTo(location=Location(kind="well", slot="2",
                                              load_name="other", well="A1"))]),
    ])
    found = "\n".join(liquid.problems(program, PLATES, POSITIONS))
    assert "slot 7 holds nothing" in found
    assert "no well Z9" in found
    assert "no saved point 'nowhere'" in found
    assert "holds res, not other" in found
    assert liquid.problems(Program(), PLATES, POSITIONS)


def test_problems_catch_a_dispense_of_more_than_the_tip_holds():
    program = _program([Aspirate(volume_ul=10, refill=False),
                        Dispense(volume_ul=15)])
    found = liquid.problems(program, PLATES, POSITIONS)
    assert any("dispenses 15 µl" in p for p in found)
    ok = _program([Aspirate(volume_ul=10), Dispense()])
    assert liquid.problems(ok, PLATES, POSITIONS) == []


def test_slots_and_points_used():
    program = _program([Dispense(location=WASTE),
                        MoveTo(location=Location(kind="point", point="waste"))])
    assert liquid.slots_used(program) == {"5", "2"}
    assert liquid.points_used(program) == {"waste"}


def test_a_blow_out_away_from_a_well_prepares_the_plunger():
    robot = MockRobot()
    program = _program([MoveTo(location=Location(kind="point", point="waste")),
                        BlowOut(location=Location(kind="here")),
                        Aspirate(volume_ul=5)], wells=["A1"])
    liquid.run(robot, program, PLATES, POSITIONS, LiquidState())
    names = [c[0] for c in robot.calls]
    at = names.index("blow_out_in_place")
    assert names[at + 1] == "prepare_to_aspirate"
