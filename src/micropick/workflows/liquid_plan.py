"""Running a liquid plan, and what stops one from running.

The plan is `core.liquid_plan`. Each of its steps compiles to a program and
goes through `workflows.liquid.run`, so refills, auto empty, the measured
bottom, the slow rise and pause and stop behave as they do there. No Qt in
it: the page runs `run_plan` on its jog panel's worker and a notebook can
call it directly.

What is kept between steps
--------------------------
`PlanState`, which the caller keeps: one `LiquidState` per step, so a stopped
step carries on where it stopped, what the tip holds, carried from step to
step, and what a stopped Wait has left. The engine raises the tip at the end
of every program, so a plan step starts from the top as a run does. Repeat is
`PlanState.redo` and the step run again: from its first well, with what the
tip holds now.

The trash first
---------------
A new plan's run begins by blowing the tip out over the trash, before its
first step whatever that is: a program of its own on the carrier, not the
first step's `clear_first`. Not a plan step and not a setting. Only from the
first step with nothing done yet (`PlanState.fresh`), so a Continue does not,
even after a stop before the first well.

Waiting
-------
Wait and Wait for me do not compile: they are done here, through the engine's
`gate`, so Pause and Stop hold them as they hold a well. A Wait counts down
only while it is not paused, and a stopped one carries on with the time it
had left (`PlanState.wait_left`). Wait for me counts as done before it holds
the run, as the engine's pause does: a stop there and a Continue do not wait
again.

`on_step` says "waiting" when the plan itself holds the run (Wait for me,
pause after the first well) and "paused" when the operator's Pause does. The
engine does not tell one from the other, or say when a Pause takes hold, so
it gets the pause through `_WatchedPause`.

One step at a time
------------------
`after_step(index)` is asked after each step but the last and says what comes
next: `NEXT`, `REPEAT` (the step again from its first well) or `FINISH` (the
run ends there, the rest still to do, so a Continue carries on). It blocks: the
run is still the run, holding the robot while the operator looks, and nothing
moves until it returns (the engine raised the tip at the step's end).
`StepChoice` is the wait for a caller on another thread; a stop raises Stopped
out of it, with the step done.

Try once
--------
`first_pair` does the first well of the step's first group with `do_step`,
not `run`: `run` raises the tip however it ends, and the tip stays where the
step put it, to be looked at. No trash blow-out and no emptying at the end, so
what the tip holds can be said; a refill takes what it would in a run. Nothing
of it counts as the plan's progress, only what the tip holds. It needs the
trash only for a step going there (`goes_to_trash`). Refused with a sentence
as a ValueError.

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

import queue
import re
import time
from dataclasses import dataclass, field

from ..core.liquid import (VOLUME_TOL, Group, Location, Program,
                           aspirate_volume)
from ..core.liquid_plan import (CARRIER_SLOT, CARRIER_WELL, SPOT_PREFIX,
                                LiquidPlan, compile_step, compiles,
                                goes_to_trash, pairing, spots)
from .liquid import (LiquidState, Plate, Stopped, do_step, gate, play, problems,
                     run, trash_problems)
from .motion import Motion

__all__ = ["FINISH", "NEXT", "REPEAT", "PlanState", "StepChoice", "run_plan",
           "plan_problems", "compile_plan", "try_refusal"]

# The trash the steps are checked with while the plan's cannot be used.
STAND_IN_TRASH = f"{SPOT_PREFIX}trash"

# What the engine puts before a problem to say where: a step says that itself.
ENGINE_WHERE = re.compile(r"^group '.*?'(?:, step \d+(?: at \S+)?)?: ")
NO_BOTTOM = re.compile(r"^the measured bottom of slot (\S+) is not known")

# A plan's pause after the first well, said as the Run step says it.
FIRST_WELL_DONE = "First well done. Check it, then continue."

# How often a Wait looks at Pause and Stop.
WAIT_SLICE_S = 0.05

# What comes after a step. See "One step at a time".
NEXT = "next"
REPEAT = "repeat"
FINISH = "finish"


@dataclass
class PlanState:
    """How far a plan got. Owned by the caller and changed in place, so it
    survives a stop. `done`: the ids of the steps finished. `cleared`: the
    trash blow-out was done. `wait_left`: the seconds a Wait begun has left,
    by step id."""

    in_tip: float = 0.0
    done: list[str] = field(default_factory=list)
    steps: dict[str, LiquidState] = field(default_factory=dict)
    cleared: bool = False
    wait_left: dict[str, float] = field(default_factory=dict)

    @property
    def fresh(self) -> bool:
        """Whether nothing of the plan was done yet."""
        return not (self.cleared or self.done or self.wait_left) and not any(
            s.done or s.resume for s in self.steps.values())

    def redo(self, step_id: str) -> None:
        """The step as if not begun, for Repeat and Start the step over.
        What the tip holds stays."""
        self.done = [done for done in self.done if done != step_id]
        self.steps.pop(step_id, None)
        self.wait_left.pop(step_id, None)


class StepChoice:
    """The operator's answer between two steps: given on one thread, waited
    for on the run's."""

    def __init__(self) -> None:
        self._answers: queue.SimpleQueue[str] = queue.SimpleQueue()

    def answer(self, what: str) -> None:
        self._answers.put(what)

    def wait(self, stop) -> str:
        """The answer. A stop raises Stopped."""
        while not stop.is_set():
            try:
                return self._answers.get(timeout=WAIT_SLICE_S)
            except queue.Empty:
                pass
        raise Stopped


class _WatchedPause:
    """The run's pause, as the engine uses it, saying when the operator's
    holds the run. See "Waiting"."""

    def __init__(self, event, on_held) -> None:
        self.event = event
        self.on_held = on_held
        self.said = False

    def set(self) -> None:
        self.said = True
        self.event.set()

    def is_set(self) -> bool:
        held = self.event.is_set()
        if held and not self.said:
            self.said = True
            self.on_held()
        self.said = self.said and held
        return held


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


def _wait(step, state: PlanState, *, pause, stop, log) -> None:
    left = state.wait_left.get(step.id, step.seconds)
    if log is not None:
        log(f"wait {left:g} s")
    while left > 0:
        state.wait_left[step.id] = left
        gate(pause, stop)
        begun = time.monotonic()
        if stop is not None:
            stop.wait(min(WAIT_SLICE_S, left))
        else:
            time.sleep(min(WAIT_SLICE_S, left))
        left -= time.monotonic() - begun
    state.wait_left.pop(step.id, None)


def _wait_for_me(step, *, pause, stop, log, waiting) -> None:
    if pause is None:
        return
    if log is not None:
        log(f"paused: {step.message}")
    pause.set()
    waiting(step.message)
    gate(pause, stop)


def try_refusal(plan: LiquidPlan, index: int, plates: dict, positions: dict,
                capacities: dict | None = None) -> str | None:
    """Why the step at `index` cannot be tried once, in a sentence. See "Try
    once"."""
    step = plan.steps[index]
    if not compiles(step):
        return "There is nothing to try in this step."
    if goes_to_trash(step):
        if plan.trash is None:
            return "Set the trash to try this step."
        wrong = trash_problems(plan.trash, _with_carrier(plates),
                               {**positions, **spots(plan)})
        if wrong:
            return _screen(wrong[0])
    found = plan_problems(plan, plates, positions, capacities).get(step.id)
    return found[0] if found else None


def _first_pair(motion: Motion, plan: LiquidPlan, index: int, plates: dict,
                positions: dict, state: PlanState, *, pause, stop,
                log) -> str | None:
    """The first well of the step's first group. See "Try once". Returns the
    well, None on the carrier."""
    program = compile_step(plan, index, plates)
    group = program.groups[0]
    well = group.wells[0]
    trying = LiquidState(in_tip=state.in_tip)
    try:
        for si, step in enumerate(group.steps):
            if step.action == "aspirate":
                amount = aspirate_volume(step, group.steps, si, trying.in_tip)
                if amount <= VOLUME_TOL:
                    continue
                step = step.model_copy(update={"volume_ul": amount})
            do_step(motion, step, group, well, plates, positions, trying,
                    pause=pause, stop=stop, log=log, capacity=plan.tip_ul)
    finally:
        state.in_tip = trying.in_tip
    return None if _on_carrier(program) else well


def run_plan(motion: Motion, plan: LiquidPlan, plates: dict[str, Plate],
             positions: dict, state: PlanState, *, pause=None, stop=None,
             log=None, on_step=None, on_progress=None, on_paused=None,
             log_path=None, only: int | None = None,
             first_pair: bool = False, after_step=None) -> None:
    """Do the plan's steps in order, skipping those in `state.done`; with
    `only`, that index alone. With `first_pair` too, Try once on it: see
    "Try once". `on_step(index, what)`: "started", "done", and "waiting" or
    "paused" as the run is held (see "Waiting"). `on_progress(done, total,
    gi, well)` as a well is finished, counted within the step; `well` is
    None for a step with no wells. `on_paused(message)` when the plan holds
    the run. `after_step`: see "One step at a time". A stop raises
    `workflows.liquid.Stopped`, `state` saying where."""
    current = 0 if only is None else only

    def report(what: str) -> None:
        if on_step is not None:
            on_step(current, what)

    if pause is not None:
        pause = _WatchedPause(pause, lambda: report("paused"))
    plates = _with_carrier(plates)
    positions = {**positions, **spots(plan)}
    if first_pair:
        if only is None:
            raise ValueError("first_pair needs the step, as only")
        refusal = try_refusal(plan, only, plates, positions)
        if refusal:
            raise ValueError(refusal)
        report("started")
        well = _first_pair(motion, plan, only, plates, positions, state,
                           pause=pause, stop=stop, log=log)
        if on_progress is not None:
            on_progress(1, 1, 0, well)
        report("done")
        return
    if plan.trash is None:
        raise ValueError("a run needs the plan's trash")
    if state.fresh and current == 0:
        _blow_out_first(motion, plan, plates, positions, state, pause=pause,
                        stop=stop, log=log)
        state.cleared = True
    chosen = list(range(len(plan.steps))) if only is None else [only]
    at = 0
    while at < len(chosen):
        index = chosen[at]
        step = plan.steps[index]
        if step.id in state.done:
            at += 1
            continue
        current = index
        report("started")
        _do_step(motion, plan, index, plates, positions, state, pause=pause,
                 stop=stop, log=log, report=report, on_progress=on_progress,
                 on_paused=on_paused, log_path=log_path)
        report("done")
        last = at + 1 == len(chosen)
        then = NEXT if last or after_step is None else after_step(index)
        if then == FINISH:
            return
        if then == REPEAT:
            state.redo(step.id)
        else:
            at += 1


def _do_step(motion: Motion, plan: LiquidPlan, index: int, plates: dict,
             positions: dict, state: PlanState, *, pause, stop, log, report,
             on_progress, on_paused, log_path) -> None:
    """The step at `index`, counted done in `state` as it ends."""
    step = plan.steps[index]
    if step.kind == "wait_for_me":
        state.done.append(step.id)

        def waiting(message):
            if on_paused is not None:
                on_paused(message)
            report("waiting")

        _wait_for_me(step, pause=pause, stop=stop, log=log, waiting=waiting)
        return
    if step.kind == "wait":
        _wait(step, state, pause=pause, stop=stop, log=log)
        state.done.append(step.id)
        return
    program = compile_step(plan, index, plates)
    carried = state.steps.setdefault(step.id, LiquidState())
    carried.in_tip = state.in_tip

    def progress(done, total, gi, well):
        if on_progress is not None:
            on_progress(done, total, gi,
                        None if _on_carrier(program) else well)

    def paused(_message):
        if on_paused is not None:
            on_paused(f"Check step {index + 1}, then continue."
                      if _on_carrier(program) else FIRST_WELL_DONE)
        report("waiting")

    try:
        run(motion, program, plates, positions, carried, pause=pause,
            stop=stop, log=log, on_progress=progress, on_paused=paused,
            log_path=log_path)
    finally:
        state.in_tip = carried.in_tip
    state.done.append(step.id)


def compile_plan(plan: LiquidPlan, plates: dict[str, Plate]) -> tuple[
        dict[str, Program], dict[str, Plate]]:
    """Every step that compiles, as a program by the step's id, with the
    `plates` they need. A plan with problems does not compile."""
    plates = _with_carrier(plates)
    return {step.id: compile_step(plan, index, plates)
            for index, step in enumerate(plan.steps) if compiles(step)}, plates


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
