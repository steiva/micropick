"""The liquid handling program: its file, its steps, its order of wells."""
from __future__ import annotations

import pytest

from micropick.core.liquid import (Aspirate, Dispense, Group, Location, Mix,
                                   Program, ProgramError, describe, new_step,
                                   ordered_wells)

# Two columns of three: A1 B1 C1 | A2 B2 C2.
ORDERING = [["A1", "B1", "C1"], ["A2", "B2", "C2"]]


def _group(**kw) -> Group:
    return Group(name="g", slot="5", load_name="plate",
                 wells=["C1", "A2", "A1"], **kw)


def test_program_round_trips_through_its_file(tmp_path):
    program = Program(name="p", groups=[_group(steps=[
        Aspirate(volume_ul=20),
        Dispense(location=Location(kind="well", slot="2", load_name="res",
                                   well="A1", level="bottom",
                                   offset=[0, 0, 1])),
        new_step("mix"), new_step("blow_out"), new_step("move_to"),
        new_step("wait"), new_step("pause")])])
    path = tmp_path / "p.json"
    program.save(path)
    again = Program.load(path)
    assert again == program
    assert [s.action for s in again.groups[0].steps] == [
        "aspirate", "dispense", "mix", "blow_out", "move_to", "wait", "pause"]


def test_a_file_that_is_not_a_program_is_refused(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"groups": [{"name": 1}]}')
    with pytest.raises(ProgramError):
        Program.load(path)
    path.write_text("{")
    with pytest.raises(ProgramError):
        Program.load(path)


def test_wells_by_row_and_by_column_come_from_the_ordering():
    assert ordered_wells(_group(order="by_row"), ORDERING) == ["A1", "A2", "C1"]
    assert ordered_wells(_group(order="by_column"), ORDERING) == ["A1", "C1", "A2"]


def test_a_step_describes_itself_with_its_place():
    assert describe(Aspirate(volume_ul=50, flow_rate=10,
                             location=Location(level="bottom",
                                               offset=[0, 0, 1]))) == \
        "Aspirate 50 µl at 10 µl/s @ this well, bottom +1 mm"
    assert describe(Dispense(location=Location(kind="here"))) == \
        "Dispense all at 50 µl/s @ where the tip is"
    assert describe(Mix(cycles=2, location=Location(kind="point",
                                                    point="waste"))) == \
        "Mix 2× 50 µl at 50 µl/s @ point waste"


def test_new_groups_get_colours_not_yet_used():
    program = Program(groups=[_group()])
    assert program.next_colour() != program.groups[0].color
