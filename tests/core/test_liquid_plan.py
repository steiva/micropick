"""A liquid plan: its file, its order, its pairing rule, what it compiles to."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from micropick.core.liquid import (Aspirate, BlowOut, Dispense, Location, Mix,
                                   MoveTo, ProgramError)
from micropick.core.liquid_plan import (CARRIER_SLOT, BlowOutStep, Height,
                                        LiquidPlan, MixStep, MoveLiquid,
                                        MoveTip, Place, Spot, WaitForMe,
                                        WaitStep, compile_step, move_step,
                                        pairing, spot_name)

ORDERING = [["A1", "B1"], ["A2", "B2"], ["A3", "B3"]]


@dataclass
class _Plate:
    ordering: list
    has_bottom: bool = False


PLATES = {"5": _Plate(ORDERING), "2": _Plate([["A1"]]),
          "7": _Plate(ORDERING, has_bottom=True)}
TRASH = Location(kind="well", slot="2", load_name="res", well="A1",
                 level="top", offset=[0, 0, 5])


def _wells(*wells, slot="5") -> Place:
    return Place(slot=slot, load_name="plate", wells=list(wells))


RESERVOIR = Place(kind="point", point="Reservoir")
TO_TRASH = Place(kind="trash")


def _spot(name="By hand") -> Place:
    return Place(kind="spot", spot=Spot(name=name, x=1, y=2, z=3))


def _plan(*steps, **kw) -> LiquidPlan:
    return LiquidPlan(name="p", trash=TRASH, steps=list(steps), **kw)


def _compiled(step, **kw):
    return compile_step(_plan(step, **kw), 0, PLATES)



# -- the file and the order ----------------------------------------------------


def test_a_plan_round_trips_through_its_file_with_every_kind(tmp_path):
    plan = _plan(
        MoveLiquid(source=_wells("A1"), dest=_spot(),
                   take_at=Height(ref="bottom", offset_mm=2, aside_x_mm=-2)),
        MixStep(wells=_wells("A1")), BlowOutStep(),
        MoveTip(place=RESERVOIR), WaitStep(seconds=300), WaitForMe())
    path = tmp_path / "p.json"
    plan.save(path)
    again = LiquidPlan.load(path)
    assert again == plan
    assert [s.kind for s in again.steps] == [
        "move_liquid", "mix", "blow_out", "move_tip", "wait", "wait_for_me"]


def test_a_program_file_is_not_a_plan(tmp_path):
    path = tmp_path / "old.json"
    path.write_text('{"version": 1, "name": "", "tip_ul": 200, "trash": null, '
                    '"groups": []}')
    with pytest.raises(ProgramError):
        LiquidPlan.load(path)


def test_the_speeds_are_ten_and_the_tip_is_emptied_unless_told():
    step = MoveLiquid()
    assert (step.take_flow_ul_s, step.put_flow_ul_s) == (10, 10)
    assert step.empty_at_end is True and step.mix_cycles == 3


@pytest.mark.parametrize("old, new, order", [
    (2, 0, "cab"), (0, 2, "bca"), (0, 1, "bac"), (1, 0, "bac"), (2, 1, "acb")])
def test_move_step_takes_any_index_to_any_index(old, new, order):
    steps = {n: WaitForMe(message=n, color=f"#00000{i}")
             for i, n in enumerate("abc")}
    plan = _plan(*steps.values())
    moved = move_step(plan, old, new)
    assert "".join(s.message for s in moved.steps) == order
    for step in moved.steps:
        assert step.id == steps[step.message].id
        assert step.color == steps[step.message].color
    assert [s.message for s in plan.steps] == list("abc")     # pure


def test_move_step_to_the_same_place_changes_nothing():
    plan = _plan(WaitForMe(), WaitForMe())
    assert move_step(plan, 1, 1) == plan


def test_a_new_step_has_its_own_id_and_the_next_free_color():
    first, second = WaitForMe(), WaitForMe()
    assert first.id != second.id
    plan = _plan(first.model_copy(update={"color": "#5e9eeb"}))
    assert plan.next_color() == "#e8a33d"


# -- the pairing rule ----------------------------------------------------------


@pytest.mark.parametrize("source, dest, sentence", [
    (_wells("A1"), _wells("A1", "A2", "A3"),
     "One source, 50 µl into each of 3 wells. The robot refills when the tip "
     "runs low."),
    (RESERVOIR, _wells("A1", "A2"),
     "One source, 50 µl into each of 2 wells. The robot refills when the tip "
     "runs low."),
    (_spot(), _wells("A1", "A2"),
     "One source, 50 µl into each of 2 wells. The robot refills when the tip "
     "runs low."),
    (_wells("A1", "A2"), _wells("B1", "B2", slot="7"),
     "Well by well: first to first, second to second, 50 µl each."),
    (_wells("A1", "A2", "A3"), _wells("A1", slot="7"),
     "From each of 3 wells into Slot 7 · A1, 50 µl each."),
    (_wells("A1", "A2"), RESERVOIR,
     "From each of 2 wells into Point · Reservoir, 50 µl each."),
    (_wells("A1", "A2", "A3"), TO_TRASH,
     "Empty each of the 3 wells into the trash."),
    (_wells("A1"), _wells("B1", slot="7"),
     "Move 50 µl from Slot 5 · A1 to Slot 7 · B1."),
    (RESERVOIR, _wells("B1"), "Move 50 µl from Point · Reservoir to Slot 5 · B1."),
    (_wells("A1"), RESERVOIR, "Move 50 µl from Slot 5 · A1 to Point · Reservoir."),
    (_wells("A1"), TO_TRASH, "Move 50 µl from Slot 5 · A1 to the trash."),
    (_spot(), TO_TRASH, "Move 50 µl from By hand to the trash."),
])
def test_each_pairing_has_its_sentence(source, dest, sentence):
    rule = pairing(MoveLiquid(source=source, dest=dest, volume_ul=50))
    assert rule.sentence == sentence and rule.allowed


def test_n_to_m_is_not_allowed_and_says_what_would_work():
    rule = pairing(MoveLiquid(source=_wells("A1", "A2"),
                              dest=_wells("B1", "B2", "B3")))
    assert not rule.allowed
    assert rule.sentence == ("Take from 1 well, or from as many wells as it "
                             "goes into (3).")


def test_a_tip_that_mixes_does_not_promise_a_refill():
    rule = pairing(MoveLiquid(source=_wells("A1"), dest=_wells("A2", "A3"),
                              volume_ul=50, mix_after=True))
    assert rule.sentence == "One source, 50 µl into each of 2 wells."


def test_a_place_not_chosen_has_no_rule_yet():
    rule = pairing(MoveLiquid(source=_wells("A1")))
    assert rule.sentence == "" and rule.allowed


# -- what a step compiles to ---------------------------------------------------


def test_one_to_n_is_a_refill_chain_of_whole_wells():
    program = _compiled(MoveLiquid(
        source=_wells("A1", slot="7"), dest=_wells("A1", "A2", "A3"),
        volume_ul=60, take_flow_ul_s=20, put_flow_ul_s=30))
    (group,) = program.groups
    assert (group.slot, group.wells) == ("5", ["A1", "A2", "A3"])
    aspirate, dispense = group.steps
    assert isinstance(aspirate, Aspirate) and aspirate.refill
    assert aspirate.volume_ul == 180        # three of 60 fit in 200
    assert aspirate.flow_rate == 20
    assert aspirate.location.kind == "well"
    assert (aspirate.location.slot, aspirate.location.well) == ("7", "A1")
    assert isinstance(dispense, Dispense)
    assert (dispense.volume_ul, dispense.flow_rate) == (60, 30)
    assert dispense.location.kind == "this_well"


def test_one_to_n_refills_within_the_plans_tip():
    program = _compiled(MoveLiquid(source=RESERVOIR, dest=_wells("A1", "A2"),
                                   volume_ul=30), tip_ul=100)
    assert program.tip_ul == 100
    assert program.groups[0].steps[0].volume_ul == 90


def test_one_to_n_with_mix_goes_back_for_each_well():
    program = _compiled(MoveLiquid(
        source=RESERVOIR, dest=_wells("A1", "A2"), volume_ul=50,
        put_flow_ul_s=30, mix_after=True, mix_cycles=4))
    (group,) = program.groups
    aspirate, dispense, mix = group.steps
    assert not aspirate.refill and aspirate.volume_ul == 50
    assert dispense.volume_ul == 50
    assert isinstance(mix, Mix)
    assert (mix.cycles, mix.volume_ul, mix.flow_rate) == (4, 50, 30)
    assert mix.location == dispense.location


def test_mix_ul_is_its_own_volume_when_given():
    program = _compiled(MoveLiquid(source=RESERVOIR, dest=_wells("A1", "A2"),
                                   volume_ul=50, mix_after=True, mix_ul=20))
    assert program.groups[0].steps[2].volume_ul == 20


def test_n_to_n_pairs_by_row_whatever_order_they_were_chosen():
    program = _compiled(MoveLiquid(
        source=_wells("B1", "A2", "A1"), dest=_wells("A3", "B3", "B2", slot="7"),
        volume_ul=50))
    assert [(g.slot, g.wells) for g in program.groups] == [
        ("7", ["A3"]), ("7", ["B2"]), ("7", ["B3"])]
    assert [g.steps[0].location.well for g in program.groups] == [
        "A1", "A2", "B1"]
    for group in program.groups:
        aspirate, dispense = group.steps
        assert not aspirate.refill and aspirate.volume_ul == 50
        assert dispense.location.kind == "this_well"


def test_n_to_n_mixes_each_destination():
    program = _compiled(MoveLiquid(source=_wells("A1", "A2"),
                                   dest=_wells("B1", "B2"), mix_after=True))
    assert [[s.action for s in g.steps] for g in program.groups] == [
        ["aspirate", "dispense", "mix"]] * 2


def test_one_to_one_takes_exactly_the_volume():
    program = _compiled(MoveLiquid(source=_wells("A1"), dest=_wells("B2"),
                                   volume_ul=70))
    (group,) = program.groups
    assert group.wells == ["B2"]
    aspirate, dispense = group.steps
    assert not aspirate.refill and aspirate.volume_ul == 70
    assert dispense.volume_ul == 70


def test_one_to_one_with_mix_mixes_where_it_put():
    program = _compiled(MoveLiquid(source=RESERVOIR, dest=_wells("B2"),
                                   mix_after=True))
    assert [s.action for s in program.groups[0].steps] == [
        "aspirate", "dispense", "mix"]
    assert not program.groups[0].steps[0].refill


def test_n_to_one_well_goes_through_the_sources():
    program = _compiled(MoveLiquid(source=_wells("A1", "A2", "A3"),
                                   dest=_wells("A1", slot="7"), volume_ul=20))
    (group,) = program.groups
    assert (group.slot, group.wells) == ("5", ["A1", "A2", "A3"])
    aspirate, dispense = group.steps
    assert aspirate.location.kind == "this_well" and aspirate.volume_ul == 20
    assert dispense.volume_ul == 20
    assert dispense.location.kind == "well"
    assert (dispense.location.slot, dispense.location.well) == ("7", "A1")


def test_n_to_one_with_mix_mixes_in_the_fixed_destination():
    program = _compiled(MoveLiquid(source=_wells("A1", "A2"),
                                   dest=_wells("A1", slot="7"), mix_after=True))
    _aspirate, dispense, mix = program.groups[0].steps
    assert isinstance(mix, Mix) and mix.location == dispense.location
    assert mix.location.well == "A1" and mix.location.kind == "well"


def test_n_to_a_point_puts_at_the_point():
    program = _compiled(MoveLiquid(source=_wells("A1", "A2"), dest=RESERVOIR))
    assert program.groups[0].steps[1].location.kind == "point"
    assert program.groups[0].steps[1].location.point == "Reservoir"


def test_n_to_the_trash_is_an_auto_empty_at_the_plans_trash():
    program = _compiled(MoveLiquid(source=_wells("A1", "A2"), dest=TO_TRASH,
                                   put_flow_ul_s=200))
    (group,) = program.groups
    assert group.wells == ["A1", "A2"]
    _aspirate, dispense = group.steps
    assert dispense.auto_empty and dispense.flow_rate == 200
    assert dispense.location == TRASH


def test_mix_is_a_group_of_its_wells():
    program = _compiled(MixStep(wells=_wells("A1", "A2", slot="7"), cycles=5,
                                volume_ul=30, flow_ul_s=15,
                                at=Height(ref="middle", offset_mm=1)))
    (group,) = program.groups
    assert (group.slot, group.wells) == ("7", ["A1", "A2"])
    (mix,) = group.steps
    assert (mix.cycles, mix.volume_ul, mix.flow_rate) == (5, 30, 15)
    assert mix.location.kind == "this_well" and mix.location.level == "center"


def test_blow_out_and_move_the_tip_ride_on_a_carrier():
    blow = _compiled(BlowOutStep(flow_ul_s=40))
    (step,) = blow.groups[0].steps
    assert isinstance(step, BlowOut) and step.flow_rate == 40
    assert step.location == TRASH and blow.groups[0].slot == CARRIER_SLOT

    move = _compiled(MoveTip(place=_wells("B2", slot="7"),
                             at=Height(offset_mm=3)))
    (step,) = move.groups[0].steps
    assert isinstance(step, MoveTo)
    assert (step.location.kind, step.location.well) == ("well", "B2")
    assert step.location.offset == [0, 0, 3]
    assert move.groups[0].slot == CARRIER_SLOT


@pytest.mark.parametrize("step", [WaitStep(), WaitForMe()])
def test_a_wait_does_not_compile(step):
    with pytest.raises(ValueError):
        _compiled(step)


def test_no_wells_on_either_side_is_one_well_of_the_carrier():
    program = _compiled(MoveLiquid(source=RESERVOIR, dest=_spot(), volume_ul=40))
    (group,) = program.groups
    assert group.slot == CARRIER_SLOT and len(group.wells) == 1
    aspirate, dispense = group.steps
    assert not aspirate.refill and aspirate.volume_ul == 40
    assert aspirate.location.point == "Reservoir"
    assert dispense.location.kind == "point"


def test_a_spot_to_the_trash_is_on_the_carrier_too():
    program = _compiled(MoveLiquid(source=_spot(), dest=TO_TRASH))
    assert program.groups[0].slot == CARRIER_SLOT
    assert program.groups[0].steps[1].auto_empty


def test_both_ends_of_a_step_can_be_spots_under_their_own_names():
    step = MoveLiquid(source=_spot("a"), dest=_spot("b"))
    aspirate, dispense = _compiled(step).groups[0].steps
    assert aspirate.location.point == spot_name(step, "source")
    assert dispense.location.point == spot_name(step, "dest")
    assert aspirate.location.point != dispense.location.point


def test_pause_goes_on_the_first_group_and_the_emptying_on_the_last():
    program = _compiled(MoveLiquid(source=_wells("A1", "A2", "A3"),
                                   dest=_wells("B1", "B2", "B3"),
                                   pause_after_first=True))
    assert [g.pause_after_first for g in program.groups] == [True, False, False]
    assert [g.empty_after for g in program.groups] == [False, False, True]


def test_empty_at_end_off_leaves_the_tip():
    program = _compiled(MoveLiquid(source=RESERVOIR, dest=_wells("A1", "A2"),
                                   empty_at_end=False))
    assert not program.groups[-1].empty_after


def test_slow_lift_is_notebook_03s_and_only_out_of_a_well():
    program = _compiled(MoveLiquid(source=_wells("A1"), dest=TO_TRASH,
                                   slow_lift=True))
    aspirate = program.groups[0].steps[0]
    assert (aspirate.lift_steps, aspirate.lift_step_mm,
            aspirate.lift_pause_s) == (4, 0.1, 0.2)
    program = _compiled(MoveLiquid(source=RESERVOIR, dest=TO_TRASH,
                                   slow_lift=True))
    assert program.groups[0].steps[0].lift_steps == 0


@pytest.mark.parametrize("ref, slot, level", [
    ("top", "5", "top"), ("middle", "5", "center"), ("bottom", "5", "bottom"),
    ("bottom", "7", "measured_bottom")])
def test_heights_are_the_engines_levels(ref, slot, level):
    program = _compiled(MoveLiquid(
        source=_wells("A1", slot=slot), dest=TO_TRASH,
        take_at=Height(ref=ref, offset_mm=2.5, aside_x_mm=-2, aside_y_mm=1)))
    location = program.groups[0].steps[0].location
    assert location.level == level
    assert location.offset == [-2, 1, 2.5]


def test_a_point_takes_the_offset_on_its_own_z():
    program = _compiled(MoveLiquid(source=RESERVOIR, dest=TO_TRASH,
                                   take_at=Height(ref="bottom", offset_mm=4)))
    assert program.groups[0].steps[0].location.offset == [0, 0, 4]


def test_the_trash_keeps_its_own_level_and_offset():
    program = _compiled(MoveLiquid(source=_wells("A1"), dest=TO_TRASH,
                                   put_at=Height(ref="bottom", offset_mm=9)))
    assert program.groups[0].steps[1].location == TRASH
    assert program.trash == TRASH


def test_a_step_that_cannot_compile_says_so():
    with pytest.raises(ValueError):
        _compiled(MoveLiquid(source=_wells("A1", "A2"),
                             dest=_wells("B1", "B2", "B3")))
    with pytest.raises(ValueError):
        _compiled(MoveLiquid(dest=TO_TRASH))
    with pytest.raises(ValueError):
        compile_step(LiquidPlan(steps=[MoveLiquid(source=RESERVOIR,
                                                  dest=TO_TRASH)]), 0, PLATES)


def test_the_wash_compiles_to_the_bench_wash():
    wells = ["A1", "A2", "A3"]
    wash = LiquidPlan(name="Wash", trash=TRASH, steps=[
        MoveLiquid(source=Place(slot="7", load_name="plate", wells=wells),
                   dest=TO_TRASH, volume_ul=100, take_flow_ul_s=10,
                   put_flow_ul_s=200, slow_lift=True,
                   take_at=Height(ref="bottom", offset_mm=2.5, aside_x_mm=-2)),
        MoveLiquid(source=RESERVOIR,
                   dest=Place(slot="7", load_name="plate", wells=wells),
                   volume_ul=100, pause_after_first=True)])
    drawn = compile_step(wash, 0, PLATES)
    (group,) = drawn.groups
    assert group.wells == wells
    aspirate, dispense = group.steps
    assert aspirate == Aspirate(
        volume_ul=100, flow_rate=10, refill=False, lift_steps=4,
        lift_step_mm=0.1, lift_pause_s=0.2,
        location=Location(kind="this_well", level="measured_bottom",
                          offset=[-2, 0, 2.5]))
    assert dispense == Dispense(location=TRASH, auto_empty=True, flow_rate=200,
                                volume_ul=None)
    refilled = compile_step(wash, 1, PLATES)
    assert refilled.groups[0].pause_after_first
    assert refilled.groups[0].steps[0].volume_ul == 200
