"""Running a liquid handling program.

The program is `core.liquid`: groups of wells and the steps each well gets.
This is what does them, with no Qt in it: every function takes the robot and
the data and blocks, so the Liquid handling page runs them on its jog panel's
worker and a notebook can call them directly.

Getting there
-------------
Every step with a location goes there first (`go`), by the rules of manual
control (`workflows.manual`): a well through the robot's own `move_to_well`
after the tip is raised to the travel height, a saved point by one straight
move at that height and then down, never higher than `reachable_z`. A step
whose location is the one the tip already stands at does not move: an
aspirate and a mix in the same well are not a retract apart. `here` is
wherever the tip is.

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

Blowing out
-----------
The robot refuses an aspirate in place straight after a blow out until the
plunger is prepared again. In a well - `this_well`, `a well`, or `here`
while the tip is still where such a step put it - the blow out is the
well-based command followed by the washing notebook's shake, a well-based
10 µl in and out that prepares the plunger by itself and knocks the drop
off. Anywhere else it is a blow out in place and then the engine's
`prepareToAspirate` (`hardware.protocols.prepare_to_aspirate`).
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..core.liquid import Group, Location, Program, ordered_wells
from ..hardware.protocols import Robot, prepare_to_aspirate, require_ok
from . import manual as moves

__all__ = ["Stopped", "Plate", "LiquidState", "problems", "plan", "go",
           "do_step", "run", "run_step", "slots_used", "points_used"]

LOG_FIELDS = ["time", "group", "well", "step", "in_tip", "note"]

# The washing notebook's shake after a blow out in a well: a little air in
# and out, which knocks off the hanging drop and - being a well-based
# aspirate - leaves the plunger ready for the next aspirate in place.
SHAKE_UL = 10.0
SHAKE_FLOW = 50.0

# Volumes are compared with this much slack: 0.1 + 0.2 is not 0.3.
VOLUME_TOL = 1e-6


class Stopped(RuntimeError):
    """The operator stopped the run. Not a failure: `LiquidState` says where."""


@dataclass(frozen=True)
class Plate:
    """Labware the run holds, as a program needs it."""

    slot: str
    labware_id: str
    load_name: str
    ordering: list

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
    """

    in_tip: float = 0.0
    done: list[tuple[int, str]] = field(default_factory=list)
    resume: dict[tuple[int, str], int] = field(default_factory=dict)
    at: tuple | None = None
    last: tuple[Location, Group, str] | None = None
    z_top: float | None = None

    def reset(self) -> None:
        """For a run from the start. What is in the tip is still there."""
        self.done = []
        self.resume = {}
        self.at = None
        self.last = None


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
             positions: dict, *, volumes: bool = True) -> list[str]:
    """What stops the program from running as written, in words. Only what
    the data says; the page adds the robot, the tip and the deck. Without
    `volumes` the run is not played through for the tip's contents - for one
    step tried on its own, whose dispense may be of liquid already drawn."""
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
        out += _volume_problems(program, plates)
    return out


def _volume_problems(program: Program, plates: dict[str, Plate]) -> list[str]:
    """The run played through on paper: a dispense of more than the tip
    holds at that moment is a program written wrong."""
    in_tip, out = 0.0, []
    for group in program.groups:
        plate = plates.get(group.slot)
        wells = (ordered_wells(group, plate.ordering) if plate is not None
                 else list(group.wells))
        for well in wells:
            for si, step in enumerate(group.steps, 1):
                if step.action == "aspirate":
                    in_tip += step.volume_ul
                elif step.action == "dispense":
                    volume = in_tip if step.volume_ul is None else step.volume_ul
                    if volume > in_tip + VOLUME_TOL:
                        return out + [
                            f"group {group.name!r}, step {si} at {well}: "
                            f"dispenses {volume:g} µl, but the tip holds "
                            f"{in_tip:g} µl then."]
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
       log=None) -> None:
    """The tip to `location`, for `well` of `group`. See "Getting there"."""
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
        state.z_top = moves.drive_tip(robot, (x + ox, y + oy), z + oz,
                                      state.z_top, log=log)
    else:
        slot = group.slot if location.kind == "this_well" else location.slot
        target = well if location.kind == "this_well" else location.well
        state.z_top = moves.drive_to_well(robot, plates[slot].labware_id,
                                          target, location.level,
                                          location.offset, state.z_top, log=log)
    state.at = key
    state.last = (location, group, well)


def do_step(robot: Robot, step, group: Group, well: str,
            plates: dict[str, Plate], positions: dict, state: LiquidState, *,
            pause=None, stop=None, log=None, on_paused=None) -> str:
    """One step for one well. Returns what was done, for the log."""
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

    _gate(pause, stop)
    go(robot, step.location, group, well, plates, positions, state, log=log)
    _gate(pause, stop)
    if action == "move_to":
        return "moved"
    if action == "aspirate":
        _say(log, f"aspirate {step.volume_ul:g} µl at {step.flow_rate:g} µl/s")
        moves.aspirate(robot, step.volume_ul, step.flow_rate)
        state.in_tip += step.volume_ul
        return f"aspirated {step.volume_ul:g} µl"
    if action == "dispense":
        volume = state.in_tip if step.volume_ul is None else step.volume_ul
        if volume <= VOLUME_TOL:
            _say(log, "dispense: the tip holds nothing")
            return "nothing to dispense"
        _say(log, f"dispense {volume:g} µl at {step.flow_rate:g} µl/s")
        moves.dispense(robot, volume, step.flow_rate)
        state.in_tip = max(0.0, state.in_tip - volume)
        return f"dispensed {volume:g} µl"
    if action == "mix":
        for n in range(step.cycles):
            _gate(pause, stop)
            _say(log, f"mix {n + 1}/{step.cycles}: {step.volume_ul:g} µl")
            moves.aspirate(robot, step.volume_ul, step.flow_rate)
            moves.dispense(robot, step.volume_ul, step.flow_rate)
        return f"mixed {step.cycles}×"
    if action == "blow_out":
        _say(log, f"blow out at {step.flow_rate:g} µl/s")
        _blow_out(robot, step, _well_target(step.location, group, well,
                                            plates, state))
        state.in_tip = 0.0
        return "blown out"
    raise ValueError(f"unknown step {action!r}")


def _well_target(location: Location, group: Group, well: str,
                 plates: dict[str, Plate], state: LiquidState):
    """(labware id, well, level, offset) of the well the tip stands in for
    this location, or None when it is not in one."""
    if location.kind == "here":
        if state.at is None or state.last is None:
            return None
        location, group, well = state.last
    if location.kind == "this_well":
        return (plates[group.slot].labware_id, well, location.level,
                tuple(float(v) for v in location.offset))
    if location.kind == "well":
        return (plates[location.slot].labware_id, location.well,
                location.level, tuple(float(v) for v in location.offset))
    return None


def _blow_out(robot: Robot, step, target) -> None:
    """See "Blowing out"."""
    rate = float(step.flow_rate)
    if target is None:
        require_ok(robot.blow_out_in_place(flow_rate=rate), "blow out")
        prepare_to_aspirate(robot)
        return
    labware_id, well, level, offset = target
    where = dict(well_location=level, offset=offset)
    require_ok(robot.blow_out(labware_id, well, flow_rate=rate, **where),
               "blow out")
    require_ok(robot.aspirate(labware_id, well, volume=SHAKE_UL,
                              flow_rate=SHAKE_FLOW, **where), "shake up")
    require_ok(robot.dispense(labware_id, well, volume=SHAKE_UL,
                              flow_rate=SHAKE_FLOW, **where), "shake down")


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
                said = do_step(robot, step, group, well, plates, positions,
                               state, pause=pause, stop=stop, log=log,
                               on_paused=on_paused)
                state.resume[(gi, well)] = si + 1
                _log_row(log_path, group=group.name, well=well, step=si + 1,
                         in_tip=f"{state.in_tip:g}", note=said)
            state.resume.pop((gi, well), None)
            state.done.append((gi, well))
            _say(log, f"[{len(state.done)}/{total}] {group.name} {well} done "
                      f"(tip holds {state.in_tip:g} µl)")
            if on_progress is not None:
                on_progress(len(state.done), total, gi, well)
    finally:
        if state.in_tip > VOLUME_TOL:
            _log_row(log_path, note=f"ended holding {state.in_tip:g} ul")
        _retract(robot, state)


def run_step(robot: Robot, step, group: Group, well: str,
             plates: dict[str, Plate], positions: dict, state: LiquidState, *,
             log=None) -> str:
    """One step for one well, to try it: the tip is left where the step put
    it, so it can be looked at. Always drives to the location, since
    anything may have moved the tip since the last job."""
    state.at = None
    state.last = None
    return do_step(robot, step, group, well, plates, positions, state, log=log)
