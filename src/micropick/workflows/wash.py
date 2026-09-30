"""Washing cuboids: drawing the liquid off wells that already hold one.

Notebook 03 as functions, with no Qt in them: every one takes the robot and
the numbers and blocks, so the Liquid handling page runs them on its jog
panel's worker and a notebook can call them directly.

The whole job is one move repeated: the tip near the bottom of a well, to
one side of the middle where the cuboid is not, and a slow draw. Everything
else exists to get that position right and to keep the tip from
overflowing.

Where the tip goes
------------------
A `WellPreset` holds two different offsets, kept apart. `centre` is where
the well's real centre and top are, relative to the robot's own idea of the
well top - measured once, the same for every well of the plate. `shift` is
the deliberate move away from that centre, a millimetre or two, so the tip
sits by the wall instead of over the cuboid. The drawing height is the real
top minus the well's depth plus `above_bottom_mm`, all read off the pose the
robot reports when it is above the well, so nothing absolute is stored.

Pause and stop
--------------
`pause` and `stop` are `threading.Event`s, checked between any two robot
moves (`_gate`), as the picking run does: a pause takes effect at once and
a stop raises `Stopped`. What is in the tip and which wells were drawn from
live in a `WashState` the caller holds, so after a stop it still knows that
the tip is full and which wells are done - a run carried on afterwards
empties the tip first and skips them, and a well is never drawn from twice
by accident: with its liquid gone, a second draw is the one that lifts the
cuboid.
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..hardware.protocols import (Robot, move_relative, move_to, require_ok,
                                  xyz)
from . import manual as moves

__all__ = ["Stopped", "WashState", "Waste", "SHAKE_UL", "SHAKE_FLOW",
           "centre_offset", "wash_offset", "go_to_well", "to_draw_height",
           "draw_from", "empty_tip", "put_back", "run"]

# The notebook's shake: a little air in and out over the waste after a blow
# out, which knocks off the drop left hanging on the tip.
SHAKE_UL = 10.0
SHAKE_FLOW = 50.0

LOG_FIELDS = ["time", "n", "well", "volume", "in_tip", "note"]


class Stopped(RuntimeError):
    """The operator stopped the run. Not a failure: `WashState` says where."""


@dataclass
class WashState:
    """What the tip holds and which wells have been drawn from.

    Owned by the caller and changed in place, so it survives a stop. `z_top`
    is the top of the Z travel with the tip that is fitted; see
    `workflows.manual.raise_tip`.
    """

    in_tip: float = 0.0
    done: list[str] = field(default_factory=list)
    last_well: str | None = None
    z_top: float | None = None


@dataclass(frozen=True)
class Waste:
    """The well the drawn liquid is emptied into."""

    labware_id: str
    well: str


def _gate(pause, stop) -> None:
    """Checkpoint between two moves. Blocks while paused, raises on stop."""
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


def centre_offset(preset, z: float = 0.0) -> tuple[float, float, float]:
    """From the robot's well top to the real centre, `z` mm above the rim."""
    cx, cy, cz = preset.centre or (0.0, 0.0, 0.0)
    return (float(cx), float(cy), float(cz) + float(z))


def wash_offset(preset, z: float = 0.0) -> tuple[float, float, float]:
    """The same, with the shift off the centre added."""
    cx, cy, cz = centre_offset(preset, z)
    sx, sy = preset.shift
    return (cx + float(sx), cy + float(sy), cz)


def go_to_well(robot: Robot, labware_id: str, well: str, offset,
               state: WashState, *, log=None):
    """The tip up, then to `offset` from the robot's top of `well`. Returns
    the pose reached."""
    state.z_top = moves.drive_to_well(robot, labware_id, well, "top", offset,
                                      state.z_top, log=log)
    return xyz(robot)


def _draw_z(pose_z: float, preset, settings) -> float:
    """The drawing height, from the pose `approach_mm` above the real top."""
    top = pose_z - settings.approach_mm
    return top - float(preset.depth_mm) + settings.above_bottom_mm


def to_draw_height(robot: Robot, labware_id: str, well: str, preset,
                   settings, state: WashState, *, raise_first: bool = True,
                   pause=None, stop=None, log=None) -> float:
    """Above the well at the wash position, then straight down to the
    drawing height. Returns that height.

    `raise_first` for a trip from elsewhere on the deck; from the well next
    door the robot's own path over the plate is enough, as in the notebook.
    """
    offset = wash_offset(preset, settings.approach_mm)
    _gate(pause, stop)
    if raise_first:
        pose = go_to_well(robot, labware_id, well, offset, state, log=log)
    else:
        _say(log, f"tip to well {well}")
        require_ok(robot.move_to_well(labware_id, well, well_location="top",
                                      offset=offset, verbose=False),
                   f"move to {well}")
        pose = xyz(robot)
    z = _draw_z(pose[2], preset, settings)
    _gate(pause, stop)
    _say(log, f"down to z {z:.2f}")
    move_to(robot, (pose[0], pose[1], z), min_z_height=z - 1.0,
            force_direct=True)
    return z


def _rise(robot: Robot, settings, pause=None, stop=None) -> None:
    """Slowly out of the liquid, then clear of the well."""
    for _ in range(settings.lift_steps):
        _gate(pause, stop)
        move_relative(robot, "z", settings.lift_step_mm)
        time.sleep(settings.lift_pause_s)
    _gate(pause, stop)
    move_relative(robot, "z", settings.rise_mm)


def draw_from(robot: Robot, labware_id: str, well: str, preset, settings,
              state: WashState, *, raise_first: bool = True, pause=None,
              stop=None, log=None) -> None:
    """Down beside the cuboid, draw slowly, lift slowly, clear the well.

    The state changes the moment the draw is done, not after the lift, so a
    stop during the lift still knows the tip is full and the well is done.
    """
    to_draw_height(robot, labware_id, well, preset, settings, state,
                   raise_first=raise_first, pause=pause, stop=stop, log=log)
    _gate(pause, stop)
    volume, rate = settings.volume_ul, settings.flow_rate
    _say(log, f"draw {volume:g} µl from {well} at {rate:g} µl/s")
    moves.aspirate(robot, volume, rate)
    state.in_tip += volume
    state.last_well = well
    if well not in state.done:
        state.done.append(well)
    _rise(robot, settings, pause, stop)


def empty_tip(robot: Robot, waste: Waste, settings, state: WashState, *,
              pause=None, stop=None, log=None) -> None:
    """Empty the tip into the waste and shake the hanging drop off.

    Also run with nothing in the tip, at the start of every run, as the
    notebook does: the blow out and the shake leave the plunger where the
    first draw expects it.
    """
    offset = (0.0, 0.0, settings.waste_height_mm)
    _gate(pause, stop)
    go_to_well(robot, waste.labware_id, waste.well, offset, state, log=log)
    rate = settings.empty_flow_rate
    if state.in_tip > 0:
        _gate(pause, stop)
        _say(log, f"empty {state.in_tip:g} µl into the waste")
        moves.dispense(robot, state.in_tip, rate)
        state.in_tip = 0.0
    _gate(pause, stop)
    require_ok(robot.blow_out(waste.labware_id, waste.well,
                              well_location="top", offset=offset,
                              flow_rate=rate), "blow out")
    require_ok(robot.aspirate(waste.labware_id, waste.well,
                              well_location="top", offset=offset,
                              volume=SHAKE_UL, flow_rate=SHAKE_FLOW),
               "shake up")
    require_ok(robot.dispense(waste.labware_id, waste.well,
                              well_location="top", offset=offset,
                              volume=SHAKE_UL, flow_rate=SHAKE_FLOW),
               "shake down")
    state.in_tip = 0.0
    _gate(pause, stop)
    move_relative(robot, "z", settings.rise_mm)


def put_back(robot: Robot, labware_id: str, preset, settings,
             state: WashState, *, log=None) -> str:
    """Return what the tip holds to the last well it was drawn from.

    For a draw that took the cuboid with it: the liquid goes back where it
    came from, at the same spot, as slowly as it was drawn, and the well
    counts as not washed again. Returns the well.
    """
    well = state.last_well
    if well is None or state.in_tip <= 0:
        raise RuntimeError("nothing in the tip to put back")
    to_draw_height(robot, labware_id, well, preset, settings, state, log=log)
    _say(log, f"put {state.in_tip:g} µl back into {well}")
    moves.dispense(robot, state.in_tip, settings.flow_rate)
    state.in_tip = 0.0
    if well in state.done:
        state.done.remove(well)
    move_relative(robot, "z", settings.rise_mm)
    return well


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


def run(robot: Robot, labware_id: str, wells: list[str], preset, settings,
        waste: Waste, state: WashState, *, pause=None, stop=None, log=None,
        on_progress=None, on_paused=None, log_path: Path | None = None) -> None:
    """Wash `wells` in order, skipping those already in `state.done`.

    Starts by emptying the tip - whatever a stopped run left in it - and
    ends by emptying it again; in between it goes to the waste whenever the
    next draw would overflow the tip. With `pause_after_first`, the first
    well of a plate (nothing done yet) is followed by a pause, set here on
    the caller's own event so its Continue is the same button as always;
    `on_paused(well)` says why. `on_progress(n, total, well)` after every
    draw. The tip is raised however this ends.
    """
    total = len(wells)
    first_of_plate = not state.done
    try:
        empty_tip(robot, waste, settings, state, pause=pause, stop=stop,
                  log=log)
        came_from_waste = True
        for n, well in enumerate(wells, 1):
            if well in state.done:
                continue
            if state.in_tip + settings.volume_ul > settings.tip_capacity_ul:
                empty_tip(robot, waste, settings, state, pause=pause,
                          stop=stop, log=log)
                came_from_waste = True
            draw_from(robot, labware_id, well, preset, settings, state,
                      raise_first=came_from_waste, pause=pause, stop=stop,
                      log=log)
            came_from_waste = False
            _log_row(log_path, n=n, well=well, volume=settings.volume_ul,
                     in_tip=state.in_tip)
            _say(log, f"[{n}/{total}] {well} (tip holds {state.in_tip:g} µl)")
            if on_progress is not None:
                on_progress(n, total, well)
            if first_of_plate and settings.pause_after_first and pause is not None:
                first_of_plate = False
                pause.set()
                if on_paused is not None:
                    on_paused(well)
        if state.in_tip > 0:
            empty_tip(robot, waste, settings, state, pause=pause, stop=stop,
                      log=log)
    finally:
        if state.in_tip > 0:
            _log_row(log_path, note=f"ended holding {state.in_tip:g} ul")
        try:
            require_ok(robot.retract_axis("leftZ", verbose=False), "retract")
        except Exception:                            # noqa: BLE001
            pass
