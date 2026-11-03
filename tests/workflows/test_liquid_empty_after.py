"""`Group.empty_after`: what is left in the tip goes to the trash at the end."""
from __future__ import annotations

import threading

import pytest

from micropick.core.liquid import Aspirate, Dispense, Group, Location, Program
from micropick.hardware.mock import MockRobot
from micropick.workflows import liquid
from micropick.workflows.liquid import LiquidState, Plate, Stopped
from micropick.workflows.motion import Motion

PLATE = Plate("5", "plate-id", "plate", [["A1", "B1"], ["A2", "B2"]])
TRASH_PLATE = Plate("2", "trash-id", "res", [["A1"]])
SOURCE_PLATE = Plate("3", "source-id", "res", [["A1"]])
PLATES = {"5": PLATE, "2": TRASH_PLATE, "3": SOURCE_PLATE}
TRASH = Location(kind="well", slot="2", load_name="res", well="A1")
SOURCE = Location(kind="well", slot="3", load_name="res", well="A1")


def _refill_chain(*, empty_after=True, trash=TRASH) -> Program:
    """Three wells of 50 µl from a 200 µl refill: 50 µl left at the end."""
    return Program(trash=trash, groups=[Group(
        name="g", slot="5", load_name="plate", wells=["A1", "A2", "B1"],
        empty_after=empty_after,
        steps=[Aspirate(volume_ul=200, location=SOURCE),
               Dispense(volume_ul=50)])])


def _at_trash(robot) -> list:
    return [c for c in robot.calls if c[0] in ("dispense", "blow_out")
            and c[1] == "trash-id"]


def test_what_is_left_goes_to_the_trash_once_after_the_last_well():
    robot, state = MockRobot(), LiquidState()
    liquid.run(Motion(robot), _refill_chain(), PLATES, {}, state)
    assert state.in_tip == 0.0
    # 50 µl, the blow out, and the shake's own 10 µl in and out.
    assert [c[0] for c in _at_trash(robot)] == ["dispense", "blow_out",
                                               "dispense"]
    assert _at_trash(robot)[0][3] == 50.0
    assert robot.calls.index(_at_trash(robot)[0]) > robot.calls.index(
        ("dispense", "plate-id", "B1", 50.0, 50.0))


def test_off_the_tip_keeps_what_is_left():
    robot, state = MockRobot(), LiquidState()
    liquid.run(Motion(robot), _refill_chain(empty_after=False), PLATES, {},
               state)
    assert state.in_tip == 50.0
    assert _at_trash(robot) == []


def test_an_empty_tip_makes_no_trip():
    robot, state = MockRobot(), LiquidState()
    program = Program(trash=TRASH, groups=[Group(
        name="g", slot="5", load_name="plate", wells=["A1"], empty_after=True,
        steps=[Aspirate(volume_ul=50, location=SOURCE, refill=False),
               Dispense(volume_ul=50)])])
    liquid.run(Motion(robot), program, PLATES, {}, state)
    assert not [c for c in robot.calls if "trash-id" in c]


def test_a_stop_during_it_is_resumed_and_repeats_no_well():
    robot, state = MockRobot(), LiquidState()
    motion = Motion(robot)
    stop = threading.Event()

    def log(text):
        # The last well's dispense is under way: the stop lands on the
        # emptying's first checkpoint.
        if text.startswith("dispense") and "B1" in text:
            stop.set()

    with pytest.raises(Stopped):
        liquid.run(motion, _refill_chain(), PLATES, {}, state, stop=stop,
                   log=log)
    assert state.in_tip == 50.0
    assert (0, "B1") not in state.done
    assert _at_trash(robot) == []

    robot.calls.clear()
    stop.clear()
    liquid.run(motion, _refill_chain(), PLATES, {}, state, stop=stop)
    assert state.in_tip == 0.0
    assert (0, "B1") in state.done
    assert [c for c in robot.calls if c[0] == "dispense"
            and c[1] == "plate-id"] == []
    assert _at_trash(robot)[0][3] == 50.0


def test_only_the_groups_last_well_empties():
    robot, state = MockRobot(), LiquidState()
    program = _refill_chain()
    first = program.groups[0].model_copy(update={"empty_after": False,
                                                 "wells": ["A1"]})
    second = program.groups[0].model_copy(update={"wells": ["B2"]})
    program = program.model_copy(update={"groups": [first, second]})
    liquid.run(Motion(robot), program, PLATES, {}, state)
    assert state.in_tip == 0.0
    assert len([c for c in _at_trash(robot) if c[0] == "blow_out"]) == 1


def test_the_paper_play_through_counts_it():
    # Off, the 50 µl left does not leave room for the next group's 200 µl.
    def two_groups(empty_after):
        chain = _refill_chain(empty_after=empty_after).groups[0]
        again = Group(name="h", slot="5", load_name="plate", wells=["B2"],
                      steps=[Aspirate(volume_ul=200, refill=False)])
        return Program(trash=TRASH, groups=[chain, again])

    assert liquid.problems(two_groups(True), PLATES, {}) == []
    assert any("more than" in p
               for p in liquid.problems(two_groups(False), PLATES, {}))


def test_it_needs_a_trash():
    robot = MockRobot()
    with pytest.raises(ValueError, match="trash"):
        liquid.run(Motion(robot), _refill_chain(trash=None), PLATES, {},
                   LiquidState())
    assert robot.calls == []


def test_an_old_program_file_reads_it_as_off(tmp_path):
    path = tmp_path / "old.json"
    path.write_text('{"groups": [{"name": "g", "slot": "5", '
                    '"load_name": "plate"}]}')
    assert Program.load(path).groups[0].empty_after is False
