"""Running a liquid handling program.

The program is `core.liquid`: groups of wells and the steps each well gets.
This is what does them, with no Qt in it: every function takes the robot and
the data and blocks, so the Liquid handling page runs them on its jog panel's
worker and a notebook can call them directly.

Getting there
-------------
In a well - `this_well` or `a well` - an aspirate, a dispense, a mix and a
blow out are the robot's well-based commands, which take the tip there
themselves along the robot's own path over the deck, up out of the last
well and over whatever stands between. No retract before each of them: that
put the Z axis all the way up and down again for every step of every well.
The tip is raised only when the robot did not put it where it is - at a
saved point, or unknown at the start of a run - since its path starts from
the last place it knows (`_leave_for_well`). A Move to a well is the
robot's `move_to_well`, the same way.

Where a plate's well centre was measured (`config.schema.WellCentre`,
handed in as `Plate.centre`), its x and y are added to every well command's
offset on that plate, so a step's sideways offset is from the real centre.
Heights stay the robot's for top, center and bottom.

The measured bottom (`core.liquid.MEASURED_BOTTOM`) is the measured rim less
the depth set for the plate (`Plate.depth`), and it is not a well command:
the robot refuses any well command whose point lies below the bottom of the
well as its labware definition has it ("OperationLocationNotInWellError"),
and a measured bottom is often just that. So it is reached as notebook 03
did (`_descend`): a `move_to_well` to over the measured rim, the pose read
there, the engine's `prepareToAspirate` while the tip is still above the
liquid, and one straight `move_to_coordinates` down to the absolute Z; the
liquid commands there are the in-place ones.

Leaving a well the robot does not know the tip is in
----------------------------------------------------
After a move by coordinates - down to a measured bottom, or the small rises
of a slow lift - the robot no longer knows the tip is in a well, and its
next well command plans from nowhere: up to its travel height, over, down.
Out of the well the tip was in, that was up, down to the top of the same
well, and up again to go anywhere. So the tip leaves such a well with one
`move_to_well` to its top, `force_direct`: straight up, and the robot knows
again which well it stands over (`_lift_out`, `LiquidState.loose`). From
there it finds the next well by its own path, and a next step in the same
well is a short move within it.

A saved point is the robot's coordinates, not a place it plans a path to,
so it is reached by the rules of manual control (`workflows.manual`): the
tip raised to the travel height, one straight move across, then down,
never higher than `reachable_z`; and the liquid commands there are the
in-place ones. A step whose location is the one the tip already stands at
does not move. `here` is wherever the tip is, and its commands are in place.

Pause, stop and carrying on
---------------------------
`pause` and `stop` are `threading.Event`s checked between any two robot
commands (`_gate`), as the picking run does: a pause takes effect at once and
a stop raises `Stopped`. What the tip holds, which wells are finished and how
far the well in hand got live in a `LiquidState` the caller keeps, so a run
started again after a stop carries on from the step after the last one done -
a chain is never repeated from its first aspirate by accident. The tip is
raised however a run ends; the first `here` after that goes back to where the
tip was before it was raised, so a dispense does not happen in the air above
the well it was meant for.

What the tip holds
------------------
`LiquidState.in_tip` counts it, across wells, groups and runs. A refill
aspirate takes what `core.liquid.aspirate_volume` says - nothing while the
tip holds enough for the dispenses after it, else what tops it up - and is
not driven to when it takes nothing. An auto empty dispense is skipped, and
not driven to, while the tip can take the next aspirate
(`core.liquid.empties_now`); when it does empty, it is a dispense of
everything and a blow out, as below. No aspirate and no mix may put more in
the tip than the program's `tip_ul`: the run raises `Overfill` before the
tip moves, and `problems` plays the run through on paper to say so first.

Blowing out
-----------
The robot refuses an aspirate in place straight after a blow out until the
plunger is prepared again. In a well - `this_well`, `a well`, a measured
bottom, or `here` while the tip is still where such a step put it - the
blow out is done where the step is, and then the tip goes straight up to
the top of that well for the washing notebook's shake: a well-based 10 µl
in and out that prepares the plunger by itself and knocks the drop off. At
the top, because a well-based aspirate with the plunger not ready first
goes to the top to prepare it and then back down to where it was asked:
done at the bottom, that was the tip down into the liquid again. Anywhere
else it is a blow out in place and then the engine's `prepareToAspirate`
(`hardware.protocols.prepare_to_aspirate`).
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..core.liquid import (MEASURED_BOTTOM, Group, Location, Program,
                           aspirate_volume, empties_now, needed_after,
                           ordered_wells, refills)
from ..hardware.protocols import (Robot, move_relative, move_to,
                                  prepare_to_aspirate, require_ok, xyz)
from . import manual as moves

__all__ = ["Stopped", "Overfill", "Plate", "LiquidState", "problems",
           "plan", "go",
           "do_step", "run", "run_step", "slots_used", "points_used"]

LOG_FIELDS = ["time", "group", "well", "step", "in_tip", "note"]

# The washing notebook's shake after a blow out in a well: a little air in
# and out, which knocks off the hanging drop and - being a well-based
# aspirate - leaves the plunger ready for the next aspirate in place.
SHAKE_UL = 10.0
SHAKE_FLOW = 50.0

# How far over the measured rim the tip stops before going straight down
# to the measured bottom.
APPROACH_MM = 2.0

# Volumes are compared with this much slack: 0.1 + 0.2 is not 0.3.
VOLUME_TOL = 1e-6


class Stopped(RuntimeError):
    """The operator stopped the run. Not a failure: `LiquidState` says where."""


class Overfill(RuntimeError):
    """A step would put more in the tip than it holds."""


@dataclass(frozen=True)
class Plate:
    """Labware the run holds, as a program needs it. `centre` is its
    measured well centre, (x, y, z) from the robot's own well top, or None
    where it was not measured; `depth` the depth set for its wells, for the
    measured bottom."""

    slot: str
    labware_id: str
    load_name: str
    ordering: list
    centre: tuple | None = None
    depth: float | None = None

    @property
    def has_bottom(self) -> bool:
        """Whether its measured bottom is known: a centre and a depth."""
        return self.centre is not None and self.depth is not None

    @property
    def wells(self) -> set[str]:
        return {w for column in self.ordering for w in column}


@dataclass
class LiquidState:
    """What the tip holds and how far the program got. Owned by the caller
    and changed in place, so it survives a stop.

    `done` holds (group index, well) for finished wells; `resume` the index
    of the next step for a well that was stopped halfway. `at` is the key of
    the location the tip stands at within one job (None: unknown, so the
    next location is driven to); `last` is the last location driven to and
    its well, for the first `here` after the tip was raised. `z_top` is the
    top of the Z travel with the tip that is fitted (`manual.raise_tip`).
    `loose`: the tip is in a well but got there by coordinates, so the robot
    does not know it; see "Leaving a well the robot does not know the tip is
    in".
    """

    in_tip: float = 0.0
    done: list[tuple[int, str]] = field(default_factory=list)
    resume: dict[tuple[int, str], int] = field(default_factory=dict)
    at: tuple | None = None
    last: tuple[Location, Group, str] | None = None
    z_top: float | None = None
    loose: bool = False

    def reset(self) -> None:
        """For a run from the start. What is in the tip is still there."""
        self.done = []
        self.resume = {}
        self.at = None
        self.last = None
        self.loose = False


def _gate(pause, stop) -> None:
    """Checkpoint between two commands. Blocks while paused, raises on stop."""
    if stop is not None and stop.is_set():
        raise Stopped("stopped")
    if pause is not None:
        while pause.is_set():
            if stop is not None and stop.is_set():
                raise Stopped("stopped")
            pause.wait(0.05)


def _say(log, text: str) -> None:
    if log is not None:
        log(text)


def _locations(program: Program):
    """(group index, group, step number, location) for every step that has
    one."""
    for gi, group in enumerate(program.groups):
        for si, step in enumerate(group.steps, 1):
            location = getattr(step, "location", None)
            if location is not None:
                yield gi, group, si, location


def slots_used(program: Program) -> set[str]:
    """Every slot the program sends the tip to."""
    slots = {g.slot for g in program.groups}
    slots |= {loc.slot for _gi, _g, _si, loc in _locations(program)
              if loc.kind == "well" and loc.slot}
    return slots


def points_used(program: Program) -> set[str]:
    return {loc.point for _gi, _g, _si, loc in _locations(program)
            if loc.kind == "point" and loc.point}


def _plate_problem(plates: dict[str, Plate], slot: str | None,
                   load_name: str | None) -> str:
    if not slot:
        return "no labware chosen"
    plate = plates.get(slot)
    if plate is None:
        return (f"slot {slot} holds nothing now; load {load_name} there on the "
                f"Robot & Deck page")
    if load_name and plate.load_name != load_name:
        return (f"slot {slot} holds {plate.load_name}, not {load_name} it was "
                f"made for")
    return ""


def problems(program: Program, plates: dict[str, Plate],
             positions: dict, *, volumes: bool = True,
             in_tip: float = 0.0) -> list[str]:
    """What stops the program from running as written, in words. Only what
    the data says; the page adds the robot, the tip and the deck. Without
    `volumes` the run is not played through for the tip's contents - for one
    step tried on its own, whose dispense may be of liquid already drawn.
    `in_tip` is what the tip holds when the run starts."""
    out = []
    if not program.groups:
        return ["the program has no groups: select wells on the plate and "
                "make one."]
    for group in program.groups:
        name = f"group {group.name!r}"
        if not group.wells:
            out.append(f"{name} has no wells.")
        if not group.steps:
            out.append(f"{name} has no steps.")
        why = _plate_problem(plates, group.slot, group.load_name)
        if why:
            out.append(f"{name}: {why}.")
        else:
            missing = [w for w in group.wells if w not in plates[group.slot].wells]
            if missing:
                out.append(f"{name}: {group.load_name} has no well "
                           f"{', '.join(missing[:5])}.")
    for _gi, group, si, location in _locations(program):
        where = f"group {group.name!r}, step {si}"
        if location.level == MEASURED_BOTTOM and location.kind in (
                "this_well", "well"):
            slot = group.slot if location.kind == "this_well" else location.slot
            plate = plates.get(slot)
            if plate is not None and not plate.has_bottom:
                out.append(f"{where}: the measured bottom of slot {slot} is "
                           f"not known - measure its well centre and set the "
                           f"well depth on the Wells card.")
        if location.kind == "well":
            why = _plate_problem(plates, location.slot, location.load_name)
            if why:
                out.append(f"{where}: {why}.")
            elif not location.well:
                out.append(f"{where}: no well chosen.")
            elif location.well not in plates[location.slot].wells:
                out.append(f"{where}: slot {location.slot} has no well "
                           f"{location.well}.")
        elif location.kind == "point":
            if not location.point:
                out.append(f"{where}: no point chosen.")
            elif location.point not in positions:
                out.append(f"{where}: there is no saved point "
                           f"{location.point!r} in the profile.")
    if volumes:
        out += _volume_problems(program, plates, in_tip)
    return out


def _volume_problems(program: Program, plates: dict[str, Plate],
                     in_tip: float = 0.0) -> list[str]:
    """The run played through on paper: a dispense of more than the tip
    holds at that moment, or an aspirate or a mix of more than it can take,
    is a program written wrong."""
    out, tip = [], program.tip_ul
    for group in program.groups:
        steps = group.steps
        plate = plates.get(group.slot)
        wells = (ordered_wells(group, plate.ordering) if plate is not None
                 else list(group.wells))
        for si, step in enumerate(steps, 1):
            if not refills(step):
                continue
            where = f"group {group.name!r}, step {si}"
            if step.volume_ul > tip + VOLUME_TOL:
                return out + [f"{where}: refills to {step.volume_ul:g} µl, "
                              f"but a tip holds {tip:g} µl."]
            need = needed_after(steps, si - 1)
            if need is not None and need > step.volume_ul + VOLUME_TOL:
                return out + [f"{where}: the dispenses after it take "
                              f"{need:g} µl, more than the {step.volume_ul:g} "
                              f"µl it refills to."]
        for wi, well in enumerate(wells):
            last_well = wi == len(wells) - 1
            for si, step in enumerate(steps, 1):
                where = f"group {group.name!r}, step {si} at {well}"
                if step.action == "aspirate":
                    in_tip += aspirate_volume(step, steps, si - 1, in_tip)
                    if in_tip > tip + VOLUME_TOL:
                        return out + [
                            f"{where}: the tip would hold {in_tip:g} µl, "
                            f"more than the {tip:g} µl it takes."]
                elif step.action == "mix":
                    if in_tip + step.volume_ul > tip + VOLUME_TOL:
                        return out + [
                            f"{where}: mixing {step.volume_ul:g} µl with "
                            f"{in_tip:g} µl in the tip is more than the "
                            f"{tip:g} µl it takes."]
                elif step.action == "dispense" and step.auto_empty:
                    if empties_now(steps, si - 1, in_tip, tip, last_well):
                        in_tip = 0.0
                elif step.action == "dispense":
                    volume = in_tip if step.volume_ul is None else step.volume_ul
                    if volume > in_tip + VOLUME_TOL:
                        return out + [
                            f"{where}: dispenses {volume:g} µl, but the tip "
                            f"holds {in_tip:g} µl then."]
                    in_tip -= volume
                elif step.action == "blow_out":
                    in_tip = 0.0
    return out


def plan(program: Program, plates: dict[str, Plate]) -> list[tuple[int, Group, str]]:
    """(group index, group, well) in the order the run visits them."""
    out = []
    for gi, group in enumerate(program.groups):
        plate = plates.get(group.slot)
        if plate is None:
            raise ValueError(f"group {group.name!r}: slot {group.slot} holds "
                             f"nothing")
        out += [(gi, group, well) for well in ordered_wells(group, plate.ordering)]
    return out


def go(robot: Robot, location: Location, group: Group, well: str,
       plates: dict[str, Plate], positions: dict, state: LiquidState, *,
       log=None, prepare: bool = False) -> None:
    """The tip to `location`, for `well` of `group`. See "Getting there".
    `prepare`: on the way to a measured bottom, prepare the plunger for an
    aspirate in place while the tip is above the liquid."""
    if location.kind == "here":
        if state.at is None and state.last is not None:
            # The tip was raised since the last location: back there first.
            last, last_group, last_well = state.last
            _say(log, "back to where the tip was")
            go(robot, last, last_group, last_well, plates, positions, state,
               log=log)
        return
    key = location.key(well)
    if key is not None and key == state.at:
        return
    if location.kind == "point":
        x, y, z = positions[location.point]
        ox, oy, oz = location.offset
        _lift_out(robot, state, plates, log)
        state.z_top = moves.drive_tip(robot, (x + ox, y + oy), z + oz,
                                      state.z_top, log=log)
    elif location.level == MEASURED_BOTTOM:
        _leave_for_well(robot, state, plates, log)
        _descend(robot, location, group, well, plates, log, prepare)
        state.at, state.last = key, (location, group, well)
        state.loose = True
        return
    else:
        place = _well_target(location, group, well, plates, state)
        _leave_for_well(robot, state, plates, log)
        _say(log, f"tip to {_where(place)}")
        _in_well(robot, "move_to_well", place, verbose=False)
    state.at = key
    state.last = (location, group, well)
    state.loose = False


def _leave_for_well(robot: Robot, state: LiquidState,
                    plates: dict[str, Plate], log) -> None:
    """Before a command the robot moves into a well by itself: the tip up
    first, unless a well command of the robot's put it where it is - out
    of a well it reached by coordinates by one (`_lift_out`). See "Getting
    there"."""
    _lift_out(robot, state, plates, log)
    if state.at is None or state.at[0] not in ("this_well", "well"):
        state.z_top = moves.raise_tip(robot, state.z_top, log=log)


def _lift_out(robot: Robot, state: LiquidState, plates: dict[str, Plate],
              log) -> None:
    """If the tip is in a well the robot does not know about (`loose`),
    straight up to that well's top (`_to_top`). Then the robot has put it
    where it is, as after any well command."""
    if (not state.loose or state.at is None
            or state.at[0] not in ("this_well", "well") or state.last is None):
        return
    _to_top(robot, state, plates, state.last, log)


def _to_top(robot: Robot, state: LiquidState, plates: dict[str, Plate],
            where: tuple, log):
    """The tip straight up (or down) to the top of the well of `where` -
    (location, group, well) - over the same point of it: a `move_to_well`
    with `force_direct`, so it is one straight line and the robot knows the
    well afterwards. At the measured rim where that is above the robot's
    top. Returns the well target there, for a well command at the top."""
    location, group, well = where
    plate, target = _plate_well(location, group, well, plates)
    x, y, _z = (float(v) for v in location.offset)
    cx, cy, cz = (float(v) for v in (plate.centre or (0.0, 0.0, 0.0)))
    up = round(max(0.0, cz), 3)
    _say(log, f"straight up to the top of {target}")
    offset = (round(x + cx, 3), round(y + cy, 3), up)
    require_ok(robot.move_to_well(
        plate.labware_id, target, well_location="top", offset=offset,
        force_direct=True, verbose=False), "move to well")
    top = location.model_copy(update={"level": "top", "offset": [x, y, up]})
    state.at = top.key(well)
    state.last = (top, group, well)
    state.loose = False
    return (plate.labware_id, target, "top", offset)


def _plate_well(location: Location, group: Group, well: str,
                plates: dict[str, Plate]):
    """(plate, well name) a well location means for `well` of `group`."""
    if location.kind == "this_well":
        return plates[group.slot], well
    return plates[location.slot], location.well


def _descend(robot: Robot, location: Location, group: Group, well: str,
             plates: dict[str, Plate], log, prepare: bool) -> None:
    """The tip to the measured bottom of a well, plus the location's
    offset: over the measured rim with the robot's own well move, then
    straight down to the absolute Z. See "Getting there"."""
    plate, target = _plate_well(location, group, well, plates)
    if not plate.has_bottom:
        raise ValueError(f"slot {plate.slot}: the measured bottom needs the "
                         f"well centre and the well depth")
    x, y, z = (float(v) for v in location.offset)
    cx, cy, cz = (float(v) for v in plate.centre)
    over = cz + APPROACH_MM
    _say(log, f"tip over {target}, {APPROACH_MM:g} mm above the measured rim")
    require_ok(robot.move_to_well(
        plate.labware_id, target, well_location="top",
        offset=(round(x + cx, 3), round(y + cy, 3), round(over, 3)),
        verbose=False), "move to well")
    px, py, pz = xyz(robot)
    if prepare:
        # Above the liquid: preparing moves the plunger, and in the well it
        # would blow into it. With liquid in the tip it does nothing.
        prepare_to_aspirate(robot)
    bottom = pz - over + cz - float(plate.depth) + z
    _say(log, f"down to the measured bottom {z:+g} mm (z {bottom:.2f})")
    move_to(robot, (px, py, bottom), min_z_height=bottom, force_direct=True)


def _in_well(robot: Robot, command: str, place, **params):
    """One of the robot's well-based commands, at `place` (`_well_target`)."""
    labware_id, well, level, offset = place
    return require_ok(getattr(robot, command)(
        labware_id, well, well_location=level, offset=offset, **params),
        command.replace("_", " "))


def _where(place) -> str:
    _labware_id, well, level, offset = place
    return f"well {well} ({level} {offset[2]:+g} mm)"


def _overfill(step, state: LiquidState, capacity: float | None) -> None:
    """Raise before the tip moves if `step` would overfill it."""
    if capacity is None or step.action not in ("aspirate", "mix"):
        return
    after = state.in_tip + step.volume_ul
    if after > capacity + VOLUME_TOL:
        what = "aspirating" if step.action == "aspirate" else "mixing"
        raise Overfill(
            f"the tip holds {state.in_tip:g} µl; {what} {step.volume_ul:g} µl "
            f"more would make {after:g} µl, past the {capacity:g} µl it "
            f"takes. Dispense or blow out first, or mark the tip empty if it "
            f"is.")


def do_step(robot: Robot, step, group: Group, well: str,
            plates: dict[str, Plate], positions: dict, state: LiquidState, *,
            pause=None, stop=None, log=None, on_paused=None,
            capacity: float | None = None) -> str:
    """One step for one well. Returns what was done, for the log. With
    `capacity`, an aspirate or a mix that would overfill the tip raises
    `Overfill` before anything moves."""
    action = step.action
    if action == "wait":
        _say(log, f"wait {step.seconds:g} s")
        end = time.monotonic() + step.seconds
        while time.monotonic() < end:
            _gate(pause, stop)
            time.sleep(min(0.05, max(0.0, end - time.monotonic())))
        return f"waited {step.seconds:g} s"
    if action == "pause":
        if pause is not None:
            _say(log, f"paused: {step.message}")
            pause.set()
            if on_paused is not None:
                on_paused(step.message)
            _gate(pause, stop)
        return "paused"

    _overfill(step, state, capacity)
    _gate(pause, stop)
    location = step.location
    # In a well the command takes the tip there itself; anywhere else the
    # tip is driven there first. See "Getting there".
    place = None
    if (location.kind in ("this_well", "well") and action != "move_to"
            and location.level != MEASURED_BOTTOM):
        place = _well_target(location, group, well, plates, state)
        if location.key(well) != state.at or state.loose:
            _leave_for_well(robot, state, plates, log)
    else:
        # An aspirate in place needs the plunger ready; an empty tip is made
        # ready on the way down to a measured bottom.
        prepare = (action in ("aspirate", "mix")
                   and state.in_tip <= VOLUME_TOL)
        go(robot, location, group, well, plates, positions, state, log=log,
           prepare=prepare)
    _gate(pause, stop)
    if action == "move_to":
        return "moved"
    # A blow out away from a well command may still be in one: `here`
    # after a well step, or a measured bottom.
    blow_in = _well_of(location, group, well, state)

    def blow() -> None:
        _blow_out(robot, step, blow_in, state, plates, log)

    if place is not None:
        state.at = location.key(well)
        state.last = (location, group, well)
    # After a blow out in a well the tip is at its top (`_blow_out`), and
    # that is where `state` already says it is.
    return _liquid(robot, step, place, blow, state, pause, stop, log)


def _aspirate(robot: Robot, place, volume: float, rate: float) -> None:
    if place is None:
        moves.aspirate(robot, volume, rate)
    else:
        _in_well(robot, "aspirate", place, volume=float(volume),
                 flow_rate=float(rate))


def _dispense(robot: Robot, place, volume: float, rate: float) -> None:
    if place is None:
        moves.dispense(robot, volume, rate)
    else:
        _in_well(robot, "dispense", place, volume=float(volume),
                 flow_rate=float(rate))


def _slow_lift(robot: Robot, step, state: LiquidState, pause, stop,
               log) -> None:
    """The aspirate's slow lift, if it has one: small rises with a wait
    between, so the liquid going up with the tip does not pull a cuboid.
    Moves by coordinates, so the tip is `loose` afterwards and leaves the
    well straight up (`_lift_out`)."""
    if not step.lift_steps:
        return
    state.loose = state.at is not None and state.at[0] in ("this_well", "well")
    _say(log, f"slow lift: {step.lift_steps} × {step.lift_step_mm:g} mm, "
              f"{step.lift_pause_s:g} s apart")
    for _ in range(step.lift_steps):
        _gate(pause, stop)
        move_relative(robot, "z", float(step.lift_step_mm))
        time.sleep(float(step.lift_pause_s))


def _liquid(robot: Robot, step, place, blow, state: LiquidState, pause,
            stop, log) -> str:
    """The liquid part of a step: in the well `place` with the robot's
    well-based commands, or where the tip is when `place` is None. `blow()`
    blows out (`_blow_out`)."""
    action = step.action
    at = f" in {_where(place)}" if place is not None else ""
    if action == "aspirate":
        _say(log, f"aspirate {step.volume_ul:g} µl at {step.flow_rate:g} "
                  f"µl/s{at}")
        _aspirate(robot, place, step.volume_ul, step.flow_rate)
        state.in_tip += step.volume_ul
        _slow_lift(robot, step, state, pause, stop, log)
        return f"aspirated {step.volume_ul:g} µl"
    if action == "dispense" and step.auto_empty:
        volume = state.in_tip
        if volume > VOLUME_TOL:
            _say(log, f"empty the tip: {volume:g} µl at {step.flow_rate:g} "
                      f"µl/s{at}")
            _dispense(robot, place, volume, step.flow_rate)
        _say(log, f"blow out at {step.flow_rate:g} µl/s{at}")
        blow()
        state.in_tip = 0.0
        return f"emptied {volume:g} µl"
    if action == "dispense":
        volume = state.in_tip if step.volume_ul is None else step.volume_ul
        if volume <= VOLUME_TOL:
            _say(log, "dispense: the tip holds nothing")
            return "nothing to dispense"
        _say(log, f"dispense {volume:g} µl at {step.flow_rate:g} µl/s{at}")
        _dispense(robot, place, volume, step.flow_rate)
        state.in_tip = max(0.0, state.in_tip - volume)
        return f"dispensed {volume:g} µl"
    if action == "mix":
        for n in range(step.cycles):
            _gate(pause, stop)
            _say(log, f"mix {n + 1}/{step.cycles}: {step.volume_ul:g} µl{at}")
            _aspirate(robot, place, step.volume_ul, step.flow_rate)
            _dispense(robot, place, step.volume_ul, step.flow_rate)
        return f"mixed {step.cycles}×"
    if action == "blow_out":
        _say(log, f"blow out at {step.flow_rate:g} µl/s{at}")
        blow()
        state.in_tip = 0.0
        return "blown out"
    raise ValueError(f"unknown step {action!r}")


def _well_of(location: Location, group: Group, well: str,
             state: LiquidState):
    """(location, group, well) of the well the tip is in for this
    location, a measured bottom included; None when it is not in one."""
    if location.kind == "here":
        if state.at is None or state.last is None:
            return None
        location, group, well = state.last
    if location.kind not in ("this_well", "well"):
        return None
    return location, group, well


def _well_target(location: Location, group: Group, well: str,
                 plates: dict[str, Plate], state: LiquidState):
    """(labware id, well, level, offset) of the well the tip stands in for
    this location, or None when it is not in one."""
    if location.kind == "here":
        if state.at is None or state.last is None:
            return None
        location, group, well = state.last
    if location.kind not in ("this_well", "well"):
        return None
    if location.level == MEASURED_BOTTOM:
        # Not reachable by a well command; see "Getting there".
        return None
    plate, target = _plate_well(location, group, well, plates)
    x, y, z = (float(v) for v in location.offset)
    cx, cy, _cz = plate.centre or (0.0, 0.0, 0.0)
    return (plate.labware_id, target, location.level,
            (round(x + float(cx), 3), round(y + float(cy), 3), round(z, 3)))


def _blow_out(robot: Robot, step, where, state: LiquidState,
              plates: dict[str, Plate], log) -> None:
    """See "Blowing out". `where` is `_well_of` the step: the well the tip
    is in, or None."""
    rate = float(step.flow_rate)
    if where is None:
        require_ok(robot.blow_out_in_place(flow_rate=rate), "blow out")
        prepare_to_aspirate(robot)
        return
    location, group, well = where
    target = _well_target(location, group, well, plates, state)
    if target is None or state.loose:
        # A measured bottom, or the tip moved by coordinates since: where it
        # is, which is where the step left it.
        require_ok(robot.blow_out_in_place(flow_rate=rate), "blow out")
    else:
        labware_id, target_well, level, offset = target
        require_ok(robot.blow_out(labware_id, target_well, flow_rate=rate,
                                  well_location=level, offset=offset),
                   "blow out")
    labware_id, target_well, level, offset = _to_top(robot, state, plates,
                                                     where, log)
    top = dict(well_location=level, offset=offset)
    _say(log, f"plunger reset at the top of {target_well}")
    require_ok(robot.aspirate(labware_id, target_well, volume=SHAKE_UL,
                              flow_rate=SHAKE_FLOW, **top), "shake up")
    require_ok(robot.dispense(labware_id, target_well, volume=SHAKE_UL,
                              flow_rate=SHAKE_FLOW, **top), "shake down")


def _log_row(path: Path | None, **row) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        if fresh:
            writer.writeheader()
        writer.writerow({"time": time.strftime("%Y-%m-%d %H:%M:%S"),
                         **{k: row.get(k, "") for k in LOG_FIELDS
                            if k != "time"}})


def _retract(robot: Robot, state: LiquidState) -> None:
    state.at = None
    state.loose = False
    try:
        require_ok(robot.retract_axis("leftZ", verbose=False), "retract")
    except Exception:                                # noqa: BLE001
        pass


def run(robot: Robot, program: Program, plates: dict[str, Plate],
        positions: dict, state: LiquidState, *, pause=None, stop=None,
        log=None, on_well=None, on_progress=None, on_paused=None,
        log_path: Path | None = None) -> None:
    """Do the program, skipping the wells in `state.done` and starting a
    stopped well at its next step. `on_well(gi, well)` as a well is begun,
    `on_progress(done, total, gi, well)` as it is finished. The tip is
    raised however this ends."""
    jobs = plan(program, plates)
    total = len(jobs)
    capacity = program.tip_ul
    # The last well of each group: an auto empty there always empties. The
    # first: a group may pause after it.
    last = {gi: well for gi, _group, well in jobs}
    first_well = {}
    for gi, _group, well in jobs:
        first_well.setdefault(gi, well)
    state.at = None
    try:
        for gi, group, well in jobs:
            if (gi, well) in state.done:
                continue
            if on_well is not None:
                on_well(gi, well)
            first = state.resume.get((gi, well), 0)
            for si in range(first, len(group.steps)):
                step = group.steps[si]
                if step.action == "pause":
                    # Counted as done once it holds the run: a stop while
                    # paused and a Continue must not pause here again.
                    state.resume[(gi, well)] = si + 1
                if step.action == "aspirate":
                    step, said = _sized(step, group.steps, si, state, log)
                elif (step.action == "dispense" and step.auto_empty
                      and not empties_now(group.steps, si, state.in_tip,
                                          capacity, last[gi] == well)):
                    step, said = None, (
                        f"no emptying: the tip holds {state.in_tip:g} µl, "
                        f"room for the next aspirate")
                    _say(log, said)
                if step is None:
                    state.resume[(gi, well)] = si + 1
                    _log_row(log_path, group=group.name, well=well,
                             step=si + 1, in_tip=f"{state.in_tip:g}",
                             note=said)
                    continue
                said = do_step(robot, step, group, well, plates, positions,
                               state, pause=pause, stop=stop, log=log,
                               on_paused=on_paused, capacity=capacity)
                state.resume[(gi, well)] = si + 1
                _log_row(log_path, group=group.name, well=well, step=si + 1,
                         in_tip=f"{state.in_tip:g}", note=said)
            state.resume.pop((gi, well), None)
            state.done.append((gi, well))
            _say(log, f"[{len(state.done)}/{total}] {group.name} {well} done "
                      f"(tip holds {state.in_tip:g} µl)")
            if on_progress is not None:
                on_progress(len(state.done), total, gi, well)
            if (group.pause_after_first and well == first_well[gi]
                    and pause is not None):
                # After the well is counted done, so a Stop here and a
                # Continue do not pause on it again.
                message = (f"check {well}, the first well of {group.name}, "
                           f"then Continue")
                _say(log, f"paused: {message}")
                pause.set()
                if on_paused is not None:
                    on_paused(message)
                _gate(pause, stop)
    finally:
        if state.in_tip > VOLUME_TOL:
            _log_row(log_path, note=f"ended holding {state.in_tip:g} ul")
        _retract(robot, state)


def _sized(step, steps: list, index: int, state: LiquidState, log):
    """An aspirate as much as it takes now (`aspirate_volume`), or None and
    why when it takes nothing."""
    amount = aspirate_volume(step, steps, index, state.in_tip)
    if amount <= VOLUME_TOL:
        said = (f"no refill: the tip holds {state.in_tip:g} µl, enough for "
                f"{needed_after(steps, index):g} µl")
        _say(log, said)
        return None, said
    if abs(amount - step.volume_ul) > VOLUME_TOL:
        _say(log, f"refill: the tip holds {state.in_tip:g} µl, topping up to "
                  f"{step.volume_ul:g} µl")
        step = step.model_copy(update={"volume_ul": amount})
    return step, ""


def run_step(robot: Robot, step, group: Group, well: str,
             plates: dict[str, Plate], positions: dict, state: LiquidState, *,
             log=None, capacity: float | None = None) -> str:
    """One step for one well, to try it: the tip is left where the step put
    it, so it can be looked at. Always drives to the location, since
    anything may have moved the tip since the last job. A refill takes what
    it would in a run, from what the tip holds now."""
    state.at = None
    state.last = None
    state.loose = False
    if step.action == "aspirate":
        index = next((i for i, s in enumerate(group.steps) if s is step),
                     None)
        if index is None and step in group.steps:
            index = group.steps.index(step)
        if index is not None:
            step, said = _sized(step, group.steps, index, state, log)
            if step is None:
                return said
    return do_step(robot, step, group, well, plates, positions, state,
                   log=log, capacity=capacity)
