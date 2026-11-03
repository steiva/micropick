"""Running a liquid plan, and what stops one from running.

The plan is `core.liquid_plan`. Each of its steps compiles to a program and
goes through `workflows.liquid.run`, so refills, auto empty, the measured
bottom, the slow lift and pause and stop behave as they do there. No Qt in
it: the page runs `run_plan` on its jog panel's worker and a notebook can
call it directly.

What is kept between steps
--------------------------
`PlanState`, which the caller keeps: one `LiquidState` per step, so a stopped
step carries on where it stopped, and what the tip holds, carried from step
to step. The engine raises the tip at the end of every program, so a plan
step starts from the top as a run does.

The trash first
---------------
A new plan's run begins by blowing the tip out over the trash, before its
first step whatever that is: a program of its own on the carrier, not the
first step's `clear_first`. Not a plan step and not a setting. Only with
nothing done yet (`PlanState.fresh`), so a Continue does not.

What is wrong with a plan
-------------------------
`plan_problems` is `workflows.liquid.problems` per step, its words made the
operator's, under the plan's own checks. A missing trash is the plan's
problem, never a step's: steps are checked with a stand-in, so one that goes
to the trash is finished without it. Volumes are played through the steps
with `in_tip` carried, from an empty tip since the run blows it out first,
as far as the first step with a problem: past it what the tip holds is not
known.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..core.liquid import Group, Location, Program
from ..core.liquid_plan import (CARRIER_SLOT, CARRIER_WELL, SPOT_PREFIX,
                                LiquidPlan, compile_step, compiles, pairing,
                                spots)
from .liquid import LiquidState, Plate, play, problems, run, trash_problems
from .motion import Motion

__all__ = ["PlanState", "run_plan", "plan_problems"]

# The trash the steps are checked with while the plan's cannot be used.
STAND_IN_TRASH = f"{SPOT_PREFIX}trash"

# What the engine puts before a problem to say where: a step says that itself.
ENGINE_WHERE = re.compile(r"^group '.*?'(?:, step \d+(?: at \S+)?)?: ")
NO_BOTTOM = re.compile(r"^the measured bottom of slot (\S+) is not known")


@dataclass
class PlanState:
    """How far a plan got. Owned by the caller and changed in place, so it
    survives a stop. `done`: the ids of the steps finished."""

    in_tip: float = 0.0
    done: list[str] = field(default_factory=list)
    steps: dict[str, LiquidState] = field(default_factory=dict)

    @property
    def fresh(self) -> bool:
        """Whether nothing of the plan was done yet."""
        return not self.done and not any(
            s.done or s.resume for s in self.steps.values())


def _with_carrier(plates: dict[str, Plate]) -> dict[str, Plate]:
    return {**plates, CARRIER_SLOT: Plate(CARRIER_SLOT, "", CARRIER_SLOT,
                                          [[CARRIER_WELL]])}


def _on_carrier(program: Program) -> bool:
    return program.groups[0].slot == CARRIER_SLOT


def _blow_out_first(motion: Motion, plan: LiquidPlan, plates, positions,
                    state: PlanState, *, pause, stop, log) -> None:
    clearing = LiquidState(in_tip=state.in_tip)
    program = Program(tip_ul=plan.tip_ul, trash=plan.trash, groups=[Group(
        name="Trash", slot=CARRIER_SLOT, load_name=CARRIER_SLOT,
        wells=[CARRIER_WELL])])
    try:
        run(motion, program, plates, positions, clearing, pause=pause,
            stop=stop, log=log, clear_first=True)
    finally:
        state.in_tip = clearing.in_tip


def run_plan(motion: Motion, plan: LiquidPlan, plates: dict[str, Plate],
             positions: dict, state: PlanState, *, pause=None, stop=None,
             log=None, on_step=None, on_progress=None, on_paused=None,
             log_path=None) -> None:
    """Do the plan's steps in order, skipping those in `state.done`.
    `on_step(index, what)`: "started" and "done". `on_progress(done, total,
    gi, well)` as a well is finished, counted within the step; `well` is
    None for a step with no wells. `on_paused(message)` when the run holds.
    A stop raises `workflows.liquid.Stopped`, `state` saying where. Wait and
    Wait for me do not compile and raise ValueError."""
    if plan.trash is None:
        raise ValueError("a run needs the plan's trash")
    plates = _with_carrier(plates)
    positions = {**positions, **spots(plan)}
    if state.fresh:
        _blow_out_first(motion, plan, plates, positions, state, pause=pause,
                        stop=stop, log=log)
    for index, step in enumerate(plan.steps):
        if step.id in state.done:
            continue
        if on_step is not None:
            on_step(index, "started")
        program = compile_step(plan, index, plates)
        carried = state.steps.setdefault(step.id, LiquidState())
        carried.in_tip = state.in_tip

        def progress(done, total, gi, well, program=program):
            if on_progress is not None:
                on_progress(done, total, gi,
                            None if _on_carrier(program) else well)

        def paused(message, program=program, index=index):
            if on_paused is not None:
                on_paused(f"check step {index + 1}, then press Resume."
                          if _on_carrier(program) else message)

        try:
            run(motion, program, plates, positions, carried, pause=pause,
                stop=stop, log=log, on_progress=progress, on_paused=paused,
                log_path=log_path)
        finally:
            state.in_tip = carried.in_tip
        state.done.append(step.id)
        if on_step is not None:
            on_step(index, "done")


def _screen(message: str) -> str:
    """An engine's problem in a step's words."""
    message = ENGINE_WHERE.sub("", message)
    bottom = NO_BOTTOM.match(message)
    if bottom:
        return (f"The bottom of slot {bottom[1]} is not measured: measure it "
                f"on the Well center step.")
    return message[:1].upper() + message[1:]


def _unique(messages) -> list[str]:
    return list(dict.fromkeys(messages))


def _over_tip(volume: float, plan: LiquidPlan) -> list[str]:
    if volume > plan.tip_ul:
        return [f"{volume:g} µl is more than a tip holds ({plan.tip_ul:g} µl)."]
    return []


def _not_finished(step, plan: LiquidPlan, capacities: dict) -> list[str]:
    """What the step itself says is missing or cannot be, before any
    compiling."""
    if step.kind == "mix":
        return (["Choose the wells."] if step.wells.empty
                else _over_tip(step.volume_ul, plan))
    if step.kind != "move_liquid":
        return ["Choose the place."] if step.place.empty else []
    source, dest = step.source, step.dest
    if source.kind == "trash":
        return ["Take from a well or a point."]
    out = ["Choose where it comes from."] if source.empty else []
    out += ["Choose where it goes."] if dest.empty else []
    if out:
        return out
    rule = pairing(step)
    if not rule.allowed:
        return [rule.sentence]
    out = _over_tip(step.volume_ul, plan)
    holds = capacities.get(dest.slot) if dest.kind == "wells" else None
    # Into one well from many, it all adds up there.
    put = step.volume_ul * (source.count if dest.count == 1 else 1)
    if holds is not None and put > holds:
        out.append(f"{put:g} µl is more than this well holds ({holds:g} µl).")
    return out


def plan_problems(plan: LiquidPlan, plates: dict[str, Plate], positions: dict,
                  capacities: dict | None = None) -> dict[str, list[str]]:
    """What stops the plan from running, in sentences: under "plan" for the
    plan and under its id for a step, with nothing for what has none. See
    "What is wrong with a plan". `capacities`: what a well holds in µl by
    slot, the page's to give since a `Plate` does not know it."""
    out: dict[str, list[str]] = {}
    plates = _with_carrier(plates)
    positions = {**positions, **spots(plan), STAND_IN_TRASH: (0.0, 0.0, 0.0)}
    wrong = ["Add a step to the plan."] if not plan.steps else []
    trash = trash_problems(plan.trash, plates, positions)
    if plan.trash is None:
        wrong.append("Set the trash to run this plan.")
    else:
        wrong += [_screen(p) for p in trash]
    if wrong:
        out["plan"] = wrong
    checked = plan if not trash else plan.model_copy(update={
        "trash": Location(kind="point", point=STAND_IN_TRASH)})
    in_tip, playing = 0.0, True
    for index, step in enumerate(plan.steps):
        if not compiles(step):
            continue
        found = _not_finished(step, plan, capacities or {})
        if not found:
            program = compile_step(checked, index, plates)
            found = _unique(_screen(p) for p in problems(
                program, plates, positions, volumes=False, trash=False))
            if not found and playing:
                said, in_tip = play(program, plates, in_tip)
                found = [_screen(p) for p in said]
        if found:
            out[step.id] = found
            playing = False
    return out
