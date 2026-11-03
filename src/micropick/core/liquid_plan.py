"""A liquid plan: the steps a biologist writes, over the program the robot runs.

Liquid's Plan step builds one of these and `workflows.liquid_plan` runs it.
No Qt, no robot: a plan is data, saved as JSON as a program is. The engine's
`core.liquid.Program` stays what runs; a plan only says it in the operator's
words, and each of its steps compiles to a program of its own (`compile_step`).

Steps and places
----------------
A plan is a list of **plan steps**, each read as a sentence: Move liquid from
a source to destinations, Mix, Blow out, Move the tip, Wait, Wait for me. A
step keeps its `id` and its `color` wherever it is moved (`move_step`): the
number on screen is its position, the color is its own.

A `Place` is where a step goes: wells of one plate, a saved point by name, a
**spot** kept in the plan by its coordinates (brought to by hand, or a memo
point used there: the plan does not depend on what is in memory), or the
plan's trash. Its wells are kept as the page chose them; `compile_step` puts
them in the plate's order.

A `Height` says where in a well: top, middle or bottom with an offset, and
aside in X and Y from the well's center, as a wash draws beside the cuboid.
At a point or a spot the offsets are on its own Z and X, Y and the reference
means nothing. The trash keeps its own level and offset.

How many on each side
---------------------
`pairing` is the rule, and its sentence the only explanation on screen. One
source fills N wells, refilling. N wells pair with N, first to first, in the
plate's order by row. N wells go into one well, a point or the trash. N into
M, both above one and not equal, has no meaning, and the sentence says what
would.

What a step compiles to
-----------------------
========================  ====================  =============================
Step                      Groups                Steps of each
========================  ====================  =============================
1 → N wells               the N wells           refill, dispense
1 → N wells, mixing       the N wells           aspirate, dispense, mix
N → N, 1 → 1 well         a group per well      aspirate at its source,
                                                dispense, mix if on
N → 1 well, point, spot   the N sources         aspirate, dispense, mix if on
N → trash                 the N sources         aspirate, auto empty
Point or spot → not       the carrier           aspirate, dispense or auto
wells                                           empty, mix if on
Mix                       its wells             mix
Blow out                  the carrier           blow out
Move the tip              the carrier           move to
========================  ====================  =============================

A refill draws the most whole wells that fit in the tip. A tip that mixed
carries the destination's liquid, so with mixing no reserve is kept and each
well has its own trip. Mixing is at the put speed, in the place put into.

`pause_after_first` goes on the first compiled group only and `empty_at_end`
on the last only, so N → N does not visit the trash per well. `wait_after_s`
ends the steps of each compiled well, after any mixing. Wait and Wait for me
do not compile: the runner does them.

The carrier
-----------
The engine runs wells of groups, and a step with no wells on either side has
none: it gets one well of a stand-in plate (`CARRIER_SLOT`), which only the
plan layer adds to the plates and which never reaches the screen.

Spots reach the engine as positions, under a name of their own per step and
role (`spot_name`), since both ends of a step can be spots (`SPOT_PREFIX`
is refused as the start of a point's name).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal, NamedTuple, Union
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .liquid import (GROUP_COLOURS, TIP_UL, VOLUME_TOL, Aspirate, BlowOut,
                     Dispense, Group, Location, Mix, MoveTo, Program,
                     ProgramError, Wait, ordered_wells, write_atomic)

__all__ = ["CARRIER_SLOT", "CARRIER_WELL", "SPOT_PREFIX", "Height", "Spot",
           "Place", "MoveLiquid", "MixStep", "BlowOutStep", "MoveTip",
           "WaitStep", "WaitForMe", "PlanStep", "LiquidPlan", "Pairing",
           "pairing", "chip", "move_step", "compile_step", "compiles",
           "by_row", "goes_to_trash", "places", "spot_name", "spots",
           "step_chips", "step_title"]

PLAN_VERSION = 1

# The stand-in plate of a step with no wells. See "The carrier".
CARRIER_SLOT = "plan-carrier"
CARRIER_WELL = "A1"

SPOT_PREFIX = "plan-spot:"

# Notebook 03's slow lift: 4 rises of 0.1 mm, 0.2 s apart.
SLOW_LIFT = {"lift_steps": 4, "lift_step_mm": 0.1, "lift_pause_s": 0.2}

# What each height is to the engine; a bottom measured is `_level`'s.
LEVELS = {"top": "top", "middle": "center", "bottom": "bottom"}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Height(_Model):
    ref: Literal["top", "middle", "bottom"] = "top"
    offset_mm: float = 0.0
    aside_x_mm: float = 0.0
    aside_y_mm: float = 0.0


class Spot(_Model):
    name: str
    x: float
    y: float
    z: float


class Place(_Model):
    """See "Steps and places". What a kind does not use stays empty."""

    kind: Literal["wells", "point", "spot", "trash"] = "wells"
    slot: str | None = None
    load_name: str | None = None
    wells: list[str] = Field(default_factory=list)
    point: str | None = None
    spot: Spot | None = None

    @property
    def empty(self) -> bool:
        """Whether nothing was chosen yet."""
        if self.kind == "wells":
            return not (self.slot and self.wells)
        if self.kind == "point":
            return not self.point
        return self.kind == "spot" and self.spot is None

    @property
    def count(self) -> int:
        """How many places it is: a point, a spot or the trash is one."""
        return len(self.wells) if self.kind == "wells" else 1


class _Step(_Model):
    id: str = Field(default_factory=lambda: str(uuid4()))
    color: str = GROUP_COLOURS[0]


class MoveLiquid(_Step):
    kind: Literal["move_liquid"] = "move_liquid"
    source: Place = Field(default_factory=Place)
    dest: Place = Field(default_factory=Place)
    volume_ul: float = Field(default=50.0, gt=0)
    take_flow_ul_s: float = Field(default=10.0, gt=0)
    put_flow_ul_s: float = Field(default=10.0, gt=0)
    take_at: Height = Field(default_factory=Height)
    put_at: Height = Field(default_factory=Height)
    mix_after: bool = False
    mix_cycles: int = Field(default=3, ge=1)
    # None: the volume put in.
    mix_ul: float | None = Field(default=None, gt=0)
    pause_after_first: bool = False
    empty_at_end: bool = True
    slow_lift: bool = False
    # Seconds the robot waits after each well; 0: none.
    wait_after_s: float = Field(default=0.0, ge=0)


class MixStep(_Step):
    kind: Literal["mix"] = "mix"
    wells: Place = Field(default_factory=Place)
    cycles: int = Field(default=3, ge=1)
    volume_ul: float = Field(default=50.0, gt=0)
    flow_ul_s: float = Field(default=10.0, gt=0)
    at: Height = Field(default_factory=Height)


class BlowOutStep(_Step):
    kind: Literal["blow_out"] = "blow_out"
    place: Place = Field(default_factory=lambda: Place(kind="trash"))
    flow_ul_s: float = Field(default=50.0, gt=0)
    at: Height = Field(default_factory=Height)


class MoveTip(_Step):
    kind: Literal["move_tip"] = "move_tip"
    place: Place = Field(default_factory=Place)
    at: Height = Field(default_factory=Height)


class WaitStep(_Step):
    kind: Literal["wait"] = "wait"
    seconds: float = Field(default=60.0, ge=0)


class WaitForMe(_Step):
    kind: Literal["wait_for_me"] = "wait_for_me"
    message: str = "Check the plate, then continue."


PlanStep = Annotated[Union[MoveLiquid, MixStep, BlowOutStep, MoveTip, WaitStep,
                           WaitForMe], Field(discriminator="kind")]


class LiquidPlan(_Model):
    version: int = PLAN_VERSION
    name: str = ""
    # The most the tip holds, in µl.
    tip_ul: float = Field(default=TIP_UL, gt=0)
    # The one place leftover liquid goes to, set once for the plan. A run
    # needs it; editing does not.
    trash: Location | None = None
    steps: list[PlanStep] = Field(default_factory=list)

    def next_color(self) -> str:
        used = {s.color for s in self.steps}
        for color in GROUP_COLOURS:
            if color not in used:
                return color
        return GROUP_COLOURS[len(self.steps) % len(GROUP_COLOURS)]

    def save(self, path: str | Path) -> None:
        write_atomic(path, self.model_dump_json(indent=2) + "\n")

    @classmethod
    def load(cls, path: str | Path) -> "LiquidPlan":
        path = Path(path)
        try:
            return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            raise ProgramError(f"{path} is not valid JSON: {exc}") from exc
        except ValidationError as exc:
            raise ProgramError(f"{path} is not a liquid plan:\n{exc}") from exc


def move_step(plan: LiquidPlan, index: int, new_index: int) -> LiquidPlan:
    """The plan with the step at `index` at `new_index`; `plan` as it was."""
    steps = list(plan.steps)
    steps.insert(new_index, steps.pop(index))
    return plan.model_copy(update={"steps": steps})


def chip(place: Place) -> str:
    """A place in a few words: `Slot 2 · B1–B12 (12)`, `Point · Reservoir`."""
    if place.empty:
        return "choose"
    if place.kind == "trash":
        return "Trash"
    if place.kind == "spot":
        return place.spot.name
    if place.kind == "point":
        return f"Point · {place.point}"
    wells = place.wells
    if len(wells) == 1:
        return f"Slot {place.slot} · {wells[0]}"
    return f"Slot {place.slot} · {wells[0]}–{wells[-1]} ({len(wells)})"


def _duration(seconds: float) -> str:
    minutes, rest = divmod(seconds, 60)
    if not minutes:
        return f"{rest:g} s"
    return f"{minutes:g} min {rest:g} s" if rest else f"{minutes:g} min"


def step_title(step) -> str:
    """The verb of the step's sentence: `Move 50 µl`, `Wait 5 min`."""
    if step.kind == "move_liquid":
        if step.dest.kind == "trash":
            count = step.source.count
            return f"Empty {count} well{'' if count == 1 else 's'}"
        return f"Move {step.volume_ul:g} µl"
    if step.kind == "mix":
        count = step.wells.count
        return f"Mix {count} well{'' if count == 1 else 's'} · {step.cycles}×"
    if step.kind == "wait":
        return f"Wait {_duration(step.seconds)}"
    return {"blow_out": "Blow out", "move_tip": "Move the tip",
            "wait_for_me": "Wait for me"}[step.kind]


def step_chips(step) -> list[str]:
    """The step's places as `chip`s, from before to."""
    return [chip(place) for _role, place in places(step)]


class Pairing(NamedTuple):
    sentence: str
    allowed: bool = True


def pairing(step: MoveLiquid) -> Pairing:
    """The rule for how many sources and destinations `step` has. No
    sentence until both are chosen."""
    source, dest = step.source, step.dest
    if source.empty or dest.empty:
        return Pairing("")
    n, m, volume = source.count, dest.count, f"{step.volume_ul:g}"
    if n > 1 and m > 1 and n != m:
        return Pairing("Take from 1 well, or from as many wells as it goes "
                       f"into ({m}).", False)
    if n == 1 and m > 1:
        refills = "" if step.mix_after else (" The robot refills when the tip "
                                             "runs low.")
        return Pairing(f"One source, {volume} µl into each of {m} wells."
                       + refills)
    if n > 1 and m > 1:
        return Pairing("Well by well: first to first, second to second, "
                       f"{volume} µl each.")
    if n > 1 and dest.kind == "trash":
        return Pairing(f"Empty each of the {n} wells into the trash.")
    if n > 1:
        return Pairing(f"From each of {n} wells into {chip(dest)}, {volume} "
                       "µl each.")
    to = "the trash" if dest.kind == "trash" else chip(dest)
    return Pairing(f"Move {volume} µl from {chip(source)} to {to}.")


def spot_name(step, role: str) -> str:
    """The position the spot of `step` in `role` reaches the engine under."""
    return f"{SPOT_PREFIX}{step.id}:{role}"


def places(step) -> list[tuple[str, Place]]:
    """(role, place) of every place of `step`."""
    if step.kind == "move_liquid":
        return [("source", step.source), ("dest", step.dest)]
    if step.kind == "mix":
        return [("wells", step.wells)]
    if step.kind in ("blow_out", "move_tip"):
        return [("place", step.place)]
    return []


def spots(plan: LiquidPlan) -> dict[str, tuple[float, float, float]]:
    """Every spot of the plan as a position, for the engine's `positions`."""
    return {spot_name(step, role): (place.spot.x, place.spot.y, place.spot.z)
            for step in plan.steps for role, place in places(step)
            if place.kind == "spot" and place.spot is not None}


def goes_to_trash(step) -> bool:
    """Whether `step` has the trash for a place, so needs the plan's."""
    return any(place.kind == "trash" for _role, place in places(step))


def compiles(step) -> bool:
    """Whether `step` is a program; the runner does the others itself."""
    return step.kind not in ("wait", "wait_for_me")


def by_row(place: Place, plates: dict) -> list[str]:
    """Its wells in the plate's order by row. Wells the plate does not have
    stay, last, for the engine's `problems` to name."""
    plate = plates.get(place.slot)
    if plate is None:
        return list(place.wells)
    ordered = ordered_wells(Group(name="", slot=place.slot,
                                  load_name=place.load_name or "",
                                  wells=place.wells), plate.ordering)
    return ordered + [w for w in place.wells if w not in ordered]


def _level(height: Height, slot: str, plates: dict) -> str:
    plate = plates.get(slot)
    if height.ref == "bottom" and plate is not None and plate.has_bottom:
        return "measured_bottom"
    return LEVELS[height.ref]


def _offset(height: Height) -> list[float]:
    return [height.aside_x_mm, height.aside_y_mm, height.offset_mm]


def _each(place: Place, at: Height, plates: dict) -> Location:
    """Each well of a group on `place`'s plate."""
    return Location(kind="this_well", level=_level(at, place.slot, plates),
                    offset=_offset(at))


def _fixed(plan: LiquidPlan, step, role: str, place: Place, at: Height,
           plates: dict, well: str | None = None) -> Location:
    """`place` as one location the same for every well of a group; of its
    wells `well`, else the first."""
    if place.kind == "trash":
        if plan.trash is None:
            raise ValueError("the plan has no trash")
        return plan.trash
    if place.kind == "wells":
        return Location(kind="well", slot=place.slot,
                        load_name=place.load_name, well=well or place.wells[0],
                        level=_level(at, place.slot, plates),
                        offset=_offset(at))
    name = place.point if place.kind == "point" else spot_name(step, role)
    return Location(kind="point", point=name, offset=_offset(at))


def _refill_ul(plan: LiquidPlan, volume: float) -> float:
    """The most whole wells of `volume` that fit in the tip."""
    whole = int((plan.tip_ul + VOLUME_TOL) // volume)
    return min(plan.tip_ul, whole * volume) if whole else volume


def _move_groups(plan: LiquidPlan, index: int, step: MoveLiquid,
                 plates: dict) -> list[Group]:
    source, dest = step.source, step.dest
    if source.empty or dest.empty or source.kind == "trash":
        raise ValueError(f"step {index + 1}: no source or no destination")
    if not pairing(step).allowed:
        raise ValueError(f"step {index + 1}: {pairing(step).sentence}")
    lift = SLOW_LIFT if step.slow_lift and source.kind == "wells" else {}

    def take(location: Location, volume: float = step.volume_ul,
             refill: bool = False) -> Aspirate:
        return Aspirate(volume_ul=volume, flow_rate=step.take_flow_ul_s,
                        location=location, refill=refill, **lift)

    def put(location: Location) -> list:
        if dest.kind == "trash":
            out = [Dispense(location=location, auto_empty=True,
                            flow_rate=step.put_flow_ul_s)]
        else:
            out = [Dispense(volume_ul=step.volume_ul, location=location,
                            flow_rate=step.put_flow_ul_s)]
        if step.mix_after and dest.kind != "trash":
            out.append(Mix(cycles=step.mix_cycles,
                           volume_ul=step.mix_ul or step.volume_ul,
                           flow_rate=step.put_flow_ul_s, location=location))
        if step.wait_after_s:
            out.append(Wait(seconds=step.wait_after_s))
        return out

    def group(place: Place, wells: list[str], steps: list) -> Group:
        return Group(name=f"Step {index + 1}", color=step.color,
                     slot=place.slot, load_name=place.load_name or "",
                     wells=wells, steps=steps)

    def fixed(role: str, place: Place, at: Height, well=None) -> Location:
        return _fixed(plan, step, role, place, at, plates, well)

    if dest.kind == "wells" and dest.count > 1 and source.count == 1:
        volume = (step.volume_ul if step.mix_after
                  else _refill_ul(plan, step.volume_ul))
        return [group(dest, by_row(dest, plates), [
            take(fixed("source", source, step.take_at), volume,
                 refill=not step.mix_after),
            *put(_each(dest, step.put_at, plates))])]
    if dest.kind == "wells" and dest.count == source.count:
        froms = (by_row(source, plates) if source.kind == "wells"
                 else [None])
        return [group(dest, [to], [
            take(fixed("source", source, step.take_at, well)),
            *put(_each(dest, step.put_at, plates))])
            for well, to in zip(froms, by_row(dest, plates))]
    target = fixed("dest", dest, step.put_at)
    if source.kind == "wells":
        return [group(source, by_row(source, plates), [
            take(_each(source, step.take_at, plates)), *put(target)])]
    return [_on_carrier(index, step, [
        take(fixed("source", source, step.take_at)), *put(target)])]


def _on_carrier(index: int, step, steps: list) -> Group:
    return Group(name=f"Step {index + 1}", color=step.color, slot=CARRIER_SLOT,
                 load_name=CARRIER_SLOT, wells=[CARRIER_WELL], steps=steps)


def compile_step(plan: LiquidPlan, index: int, plates: dict) -> Program:
    """The step at `index` as a program of its own, for `plates` by slot
    (each with `ordering` and `has_bottom`). See "What a step compiles to".
    Raises ValueError for a step that is not one, or that is not finished:
    `workflows.liquid_plan.plan_problems` says why in words."""
    step = plan.steps[index]
    if step.kind == "move_liquid":
        groups = _move_groups(plan, index, step, plates)
        groups[0].pause_after_first = step.pause_after_first
        groups[-1].empty_after = step.empty_at_end
    elif step.kind == "mix":
        if step.wells.empty:
            raise ValueError(f"step {index + 1}: no wells")
        wells = step.wells
        groups = [Group(
            name=f"Step {index + 1}", color=step.color, slot=wells.slot,
            load_name=wells.load_name or "", wells=by_row(wells, plates),
            steps=[Mix(cycles=step.cycles, volume_ul=step.volume_ul,
                       flow_rate=step.flow_ul_s,
                       location=_each(wells, step.at, plates))])]
    elif step.kind in ("blow_out", "move_tip"):
        if step.place.empty:
            raise ValueError(f"step {index + 1}: no place")
        location = _fixed(plan, step, "place", step.place, step.at, plates)
        groups = [_on_carrier(index, step, [
            BlowOut(flow_rate=step.flow_ul_s, location=location)
            if step.kind == "blow_out" else MoveTo(location=location)])]
    else:
        raise ValueError(f"step {index + 1}: a {step.kind} does not compile")
    return Program(name=plan.name, tip_ul=plan.tip_ul, trash=plan.trash,
                   groups=groups)
