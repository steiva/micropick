"""A liquid handling program: groups of wells, and the steps each well gets.

The Liquid handling page builds one of these and `workflows.liquid` runs it.
No Qt, no robot: a program is data, saved as JSON beside the routines, and a
notebook can build or read one as easily as the page does.

Groups, steps, locations
------------------------
A **group** is a set of wells of one plate on the deck - chosen on the plate
map, drawn in one colour - and a list of **steps**. The run takes the groups
in order and, for every well of a group, does all of that group's steps
before moving to the next well. So "aspirate here, dispense into the waste"
written once is done for twelve wells.

Every step that touches liquid or moves says **where** itself, in a
`Location`, and goes there first: the well the group is on (`this_well`), one
fixed well of any labware on the deck (`well`, the same for every well of the
group - a reservoir, a waste), a position saved in the profile (`point`), or
where the tip already is (`here`). Putting the place on the step rather than
in a separate "move" before it means a chain reads as what it does -
"Aspirate 50 µl @ this well, bottom +1 mm" - and a step moved up or down the
list takes its place with it. `MoveTo` exists for the moves that are only
moves.

A group and a well location remember the load name of the labware they were
made on as well as its slot, so a plate swapped for another kind is refused
rather than driven into (`workflows.liquid.problems`).

What the tip holds
------------------
The run counts what is in the tip. An aspirate is done as written unless it
is a **refill** (`Aspirate.refill`): then it is skipped while the tip holds
enough for the dispenses that follow it, and otherwise tops the tip up to
its volume rather than adding the whole volume to what is left
(`aspirate_volume`). So "Refill to 200 µl from the reservoir, dispense 50 µl
into this well" fills four wells per trip, and goes back to the reservoir
only when the tip runs short. A plain aspirate is for taking liquid out of a
well, which has to happen every time.

The program says how much a tip holds (`Program.tip_ul`), and nothing may
put more in it: the run refuses such an aspirate, and `problems` finds it on
paper first.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError

__all__ = ["LEVELS", "KINDS", "ACTIONS", "Location", "Aspirate", "Dispense",
           "MoveTo", "Mix", "BlowOut", "Wait", "Pause", "Step", "Group",
           "Program", "ProgramError", "ordered_wells", "describe",
           "describe_location", "new_step", "GROUP_COLOURS",
           "needed_after", "aspirate_volume", "TIP_UL"]

# The robot's own well origins; the same as workflows.manual.WELL_LEVELS,
# restated because core does not import workflows.
LEVELS = ("top", "center", "bottom")

# Where a step goes, with the words the page shows for each.
KINDS = {"this_well": "this well", "well": "a well", "point": "a saved point",
         "here": "where the tip is"}

# Colours handed to new groups in turn: distinct on the dark plate map, and
# none of them the map's white selection ring.
GROUP_COLOURS = ("#5e9eeb", "#e8a33d", "#5cc18a", "#d65f8a", "#a58cf0",
                 "#4fc4c9", "#d8d05a", "#e06b4f")

PROGRAM_VERSION = 1

# What a tip holds unless the program says otherwise: the 200 µl tips this
# bench uses on its p300.
TIP_UL = 200.0

# Volumes are compared with this much slack: 0.1 + 0.2 is not 0.3.
VOLUME_TOL = 1e-6


class ProgramError(ValueError):
    """A program file could not be read or written."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Location(_Model):
    """Where a step happens. See the module docstring."""

    kind: Literal["this_well", "well", "point", "here"] = "this_well"
    slot: str | None = None
    load_name: str | None = None
    well: str | None = None
    point: str | None = None
    level: Literal["top", "center", "bottom"] = "top"
    offset: Annotated[list[float], Field(min_length=3, max_length=3)] = \
        Field(default_factory=lambda: [0.0, 0.0, 0.0])

    def key(self, this_well: str | None) -> tuple | None:
        """What makes two locations the same place for one well of a group;
        None for `here`, which is wherever the tip is."""
        if self.kind == "here":
            return None
        if self.kind == "point":
            return ("point", self.point, tuple(self.offset))
        well = this_well if self.kind == "this_well" else self.well
        slot = None if self.kind == "this_well" else self.slot
        return (self.kind, slot, well, self.level, tuple(self.offset))


def _flow():
    return Field(default=50.0, gt=0)


class Aspirate(_Model):
    """`refill`: skipped while the tip holds enough for the dispenses that
    follow, else the tip is topped up to `volume_ul`. See "What the tip
    holds"."""

    action: Literal["aspirate"] = "aspirate"
    volume_ul: float = Field(default=50.0, gt=0)
    flow_rate: float = _flow()
    location: Location = Field(default_factory=Location)
    refill: bool = False


class Dispense(_Model):
    """`volume_ul` None dispenses whatever the tip holds."""

    action: Literal["dispense"] = "dispense"
    volume_ul: float | None = Field(default=None, gt=0)
    flow_rate: float = _flow()
    location: Location = Field(default_factory=Location)


class MoveTo(_Model):
    action: Literal["move_to"] = "move_to"
    location: Location = Field(default_factory=Location)


class Mix(_Model):
    action: Literal["mix"] = "mix"
    cycles: int = Field(default=3, ge=1)
    volume_ul: float = Field(default=50.0, gt=0)
    flow_rate: float = _flow()
    location: Location = Field(default_factory=Location)


class BlowOut(_Model):
    action: Literal["blow_out"] = "blow_out"
    flow_rate: float = _flow()
    location: Location = Field(default_factory=lambda: Location(kind="here"))


class Wait(_Model):
    action: Literal["wait"] = "wait"
    seconds: float = Field(default=5.0, ge=0)


class Pause(_Model):
    """Hold the run until the operator presses Continue."""

    action: Literal["pause"] = "pause"
    message: str = "Check the well, then continue."


Step = Annotated[Union[Aspirate, Dispense, MoveTo, Mix, BlowOut, Wait, Pause],
                 Field(discriminator="action")]

# (action, class, name on screen), in the order the page offers them.
ACTIONS = (("aspirate", Aspirate, "Aspirate"), ("dispense", Dispense, "Dispense"),
           ("move_to", MoveTo, "Move to"), ("mix", Mix, "Mix"),
           ("blow_out", BlowOut, "Blow out"), ("wait", Wait, "Wait"),
           ("pause", Pause, "Pause for the operator"))


def new_step(action: str):
    """A step of `action` with its usual values."""
    for name, cls, _title in ACTIONS:
        if name == action:
            return cls()
    raise ValueError(f"no step {action!r}; have "
                     f"{', '.join(a for a, _c, _t in ACTIONS)}")


class Group(_Model):
    name: str
    color: str = GROUP_COLOURS[0]
    slot: str
    load_name: str
    wells: list[str] = Field(default_factory=list)
    order: Literal["by_row", "by_column"] = "by_row"
    steps: list[Step] = Field(default_factory=list)


class Program(_Model):
    version: int = PROGRAM_VERSION
    name: str = ""
    # The most the tip holds, in µl.
    tip_ul: float = Field(default=TIP_UL, gt=0)
    groups: list[Group] = Field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.groups

    def next_colour(self) -> str:
        used = {g.color for g in self.groups}
        for colour in GROUP_COLOURS:
            if colour not in used:
                return colour
        return GROUP_COLOURS[len(self.groups) % len(GROUP_COLOURS)]

    def save(self, path: str | Path) -> None:
        """Atomic, as every file this application writes: a crash mid-write
        leaves the previous program, not half of this one."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(self.model_dump_json(indent=2) + "\n")
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    @classmethod
    def load(cls, path: str | Path) -> "Program":
        path = Path(path)
        try:
            return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            raise ProgramError(f"{path} is not valid JSON: {exc}") from exc
        except ValidationError as exc:
            raise ProgramError(f"{path} is not a liquid handling program:\n"
                               f"{exc}") from exc


def ordered_wells(group: Group, ordering: list[list[str]]) -> list[str]:
    """The group's wells in its order, from the plate's ordering (a list of
    columns, top to bottom). No well name is parsed: a 1536 has two-letter
    rows. Wells the ordering does not have are left out; `problems` says so."""
    wanted = set(group.wells)
    if group.order == "by_column":
        order = [w for column in ordering for w in column]
    else:
        rows = max((len(c) for c in ordering), default=0)
        order = [column[r] for r in range(rows) for column in ordering
                 if r < len(column)]
    return [w for w in order if w in wanted]


def needed_after(steps: list, index: int) -> float | None:
    """What the dispenses after step `index` take from the tip before the
    next aspirate or blow out: the steps after it, then - for the next well
    of the group - the ones before it. None when one of them dispenses
    everything, or none dispenses at all: then no amount is enough."""
    total, dispensed = 0.0, False
    for step in steps[index + 1:] + steps[:index]:
        if step.action in ("aspirate", "blow_out"):
            break
        if step.action == "dispense":
            if step.volume_ul is None:
                return None
            total += step.volume_ul
            dispensed = True
    return total if dispensed else None


def aspirate_volume(step, steps: list, index: int, in_tip: float) -> float:
    """How much the aspirate `step`, at `index` of `steps`, takes with
    `in_tip` µl already in the tip: its volume, or for a refill nothing while
    the tip holds enough and else what tops it up to its volume."""
    if not getattr(step, "refill", False):
        return step.volume_ul
    need = needed_after(steps, index)
    if need is not None and in_tip >= need - VOLUME_TOL:
        return 0.0
    return max(0.0, step.volume_ul - in_tip)


def _mm(value: float) -> str:
    return f"{value:+g} mm"


def describe_location(location: Location) -> str:
    if location.kind == "here":
        return "where the tip is"
    x, y, z = location.offset
    shift = "".join(f", {axis} {_mm(v)}" for axis, v in (("x", x), ("y", y))
                    if v)
    if location.kind == "point":
        return f"point {location.point or '?'}" + (f" {_mm(z)}" if z else "") \
            + shift
    where = ("this well" if location.kind == "this_well" else
             f"slot {location.slot or '?'} {location.well or '?'}")
    return f"{where}, {location.level} {_mm(z)}{shift}"


def describe(step) -> str:
    """One line for the list of steps."""
    action = step.action
    if action == "aspirate":
        text = (f"Refill to {step.volume_ul:g} µl when short"
                if step.refill else f"Aspirate {step.volume_ul:g} µl")
        text += f" at {step.flow_rate:g} µl/s"
    elif action == "dispense":
        amount = "all" if step.volume_ul is None else f"{step.volume_ul:g} µl"
        text = f"Dispense {amount} at {step.flow_rate:g} µl/s"
    elif action == "move_to":
        text = "Move to"
    elif action == "mix":
        text = (f"Mix {step.cycles}× {step.volume_ul:g} µl at "
                f"{step.flow_rate:g} µl/s")
    elif action == "blow_out":
        text = f"Blow out at {step.flow_rate:g} µl/s"
    elif action == "wait":
        return f"Wait {step.seconds:g} s"
    elif action == "pause":
        return f"Pause: {step.message}"
    else:                                    # the union rules this out
        raise ValueError(f"unknown step {action!r}")
    return f"{text} @ {describe_location(step.location)}"
