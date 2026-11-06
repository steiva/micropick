"""Tasks as steps: what each step is, whether it is done, what it needs.

A **task** is one of the three kinds of work started from Home, and a
order of **steps** (REDESIGN §4, GLOSSARY):

    Auto pick    Deck → Tip → Dish bottom → Plan → Well center → Run
    Manual pick  Deck → Tip → Well center → Dish bottom → Run
    Liquid       Deck → Tip → Well center → Plan → Run

The last step of every task is titled Run (the Run step, #65); in code
it stays "picking", "work" and "run".

Deck, Tip and Dish bottom are one step each, shared: the same screen and
the same state in every task that has them.

`step_state` answers, for a step of a task, whether it is **done** now
and the **first need** not met - what the dimmed step's one banner names,
with a button that goes there. Both are read from the session each time:
nothing records that a step was visited. A step the operator confirms
(the deck checked, the dish bottom's last value taken) is confirmed for
the robot session (`Session.confirm`).

A need is typed (`Need.kind`): the robot connected ("robot"), an earlier
step ("step", `key` a step), a plate on the deck ("plate", `key` the Deck
step, where one is put), or a Robot setup item ("setup", `key` one of
`SETUP`). Needs are checked in that order, so the banner names the thing
to fix first. The robot is a need of its own because every step that
moves it is dimmed while it is not connected, whatever else is missing.

Deck is done by fact (#39): the robot connected, something on the deck
and no deck problem - no confirmation, so a change to the deck clears
nothing. A step done by a confirmation (`Session.confirm`) is confirmed
by its screen: Dish bottom by Save dish bottom or Use it, the
Liquid plan by Use this plan, Run by a liquid run that ended. Well center
counts the plates on the deck (`well_center_plates`), the same in every
task, and is done while there are some and every one is measured,
measurements of earlier days included; a new plate takes the mark away.
Run does not need Well center: an unmeasured plate is used on purpose,
after a warning (REDESIGN §6.8). Well center is optional in every task
(#63, #64, #132): a plate not measured uses its drawing. A plan is no need
of it (#130).

Manual pick's steps are the same whatever is chosen (#131). Well center
and Dish bottom are optional there, Dish bottom staying dimmed under its
banner without the position over the dish, and Run needs only the Deck and
the Tip. The plate chosen on Well center (`Session.chosen_plate`) is no
need of any step.
"""

from __future__ import annotations

from dataclasses import dataclass

from .readiness import (CALIBRATION_POSITION, DISH_POSITION, SHAKE_POSITION,
                        tip_calibration_status)

__all__ = ["TASKS", "SETUP", "SETUP_TITLES", "MARKED_OPTIONAL", "OPTIONAL",
           "Need", "SETUP_POSITIONS", "StepState", "marked_optional",
           "missing_setup", "optional", "setup_done", "step_state", "steps", "title",
           "well_center_plates"]

# task -> (its name on screen, its steps in order)
TASKS = {
    "auto": ("Auto pick", ("deck", "tip", "dish_bottom", "plan",
                           "well_center", "picking")),
    "manual": ("Manual pick", ("deck", "tip", "well_center",
                               "dish_bottom", "work")),
    "liquid": ("Liquid", ("deck", "tip", "well_center", "liquid_plan",
                          "run")),
}

_TITLES = {
    "deck": "Deck", "tip": "Tip", "plan": "Plan",
    "dish_bottom": "Dish bottom",
    "picking": "Run", "work": "Run",
    "well_center": "Well center", "liquid_plan": "Plan", "run": "Run",
}


def steps(task: str) -> list[str]:
    return list(TASKS[task][1])


def title(step: str) -> str:
    return _TITLES[step]


@dataclass(frozen=True)
class Need:
    kind: str          # "robot", "step", "plate" or "setup"
    key: str           # the step or the Robot setup item; "robot"
    text: str          # the banner's sentence


@dataclass(frozen=True)
class StepState:
    done: bool
    need: Need | None  # the first need not met, or None
    optional: bool = False


_ROBOT = Need("robot", "robot", "The robot is not connected.")
# A plate plan is made for a plate the robot holds; it is put on at Deck.
_PLATE = Need("plate", "deck", "Put a plate on the deck first.")

# The Robot setup items that are a stored position, and its name in the
# profile: on screen by slot and coordinates, cleared by Reset setup.
SETUP_POSITIONS = {
    "calibration_position": CALIBRATION_POSITION,
    "shake_position": SHAKE_POSITION,
    "dish_position": DISH_POSITION,
}

# The Robot setup items, in the order of its page (REDESIGN §5, #33: each
# position's prerequisites set by the one before it - the tip calibration
# starts at the calibration position, the shake position needs a calibrated
# tip): the
# sentence a step's banner says when it needs one, and whether it is set,
# from the session (its profile is loaded).
SETUP = {
    "upper_camera": ("Choose the upper camera first.",
                     lambda s: s.upper_camera_label is not None),
    "camera_calibration": ("Calibrate the camera first.",
                           lambda s: s.profile.pixel_map is not None),
    "lower_camera": ("Choose the lower camera first.",
                     lambda s: s.lower_camera_label is not None),
    "platforms": ("Set the platforms first.",
                  lambda s: bool(s.profile.deck.modules)),
    "calibration_position": ("Set the calibration position first.",
                             lambda s: CALIBRATION_POSITION
                             in s.profile.positions),
    "shake_position": ("Set the shake position first.",
                       lambda s: SHAKE_POSITION in s.profile.positions),
    "dish_position": ("Set the position over the dish first.",
                      lambda s: DISH_POSITION in s.profile.positions),
}

# Each Robot setup item's name on screen.
SETUP_TITLES = {
    "upper_camera": "Upper camera",
    "camera_calibration": "Camera calibration",
    "lower_camera": "Lower camera",
    "platforms": "Platforms",
    "calibration_position": "Calibration position",
    "shake_position": "Shake position",
    "dish_position": "Position over the dish",
}


def setup_done(session, item: str) -> bool:
    """Whether the Robot setup item is set now."""
    return session.profile is not None and SETUP[item][1](session)


def missing_setup(session) -> list[str]:
    """The Robot setup items not set yet, in the page's order."""
    return [item for item in SETUP if not setup_done(session, item)]


# What a step says it needs from an earlier one.
_STEP_NEEDS = {
    "deck": "Check the deck first.",
    "tip": "Put a tip on first.",
    "plan": "Make the plan first.",
    "dish_bottom": "Find the dish bottom first.",
    "liquid_plan": "Make the plan first.",
}
_TIP_CALIBRATION = Need("step", "tip", "Calibrate the tip first.")

# step -> (needs the robot, earlier steps, Robot setup items). Plan needs
# a plate on the deck, not the Deck step (`_PLATE_NEEDED`). Tip is
# task-dependent: Liquid does not use the camera, so its Tip needs no
# calibration (REDESIGN §4).
_NEEDS = {
    "deck": (True, (), ()),
    "tip": (True, ("deck",), ("camera_calibration", "calibration_position")),
    "plan": (False, (), ()),
    "dish_bottom": (True, ("tip",), ("dish_position",)),
    "picking": (True, ("deck", "tip", "dish_bottom", "plan"),
                ("shake_position", "dish_position")),
    "work": (True, ("deck", "tip"), ()),
    "well_center": (True, ("deck", "tip"), ()),
    "liquid_plan": (False, ("deck",), ()),
    "run": (True, ("deck", "tip", "liquid_plan"), ()),
}

_PLATE_NEEDED = {"plan"}

def _needs(session, task: str, step: str) -> list[Need]:
    robot, earlier, setup = _NEEDS[step]
    if task == "liquid" and step == "tip":
        setup = ()
    out = [_ROBOT] if robot else []
    out += [Need("step", s, _STEP_NEEDS[s]) for s in earlier]
    if step in _PLATE_NEEDED:
        out.append(_PLATE)
    out += [Need("setup", k, SETUP[k][0]) for k in setup]
    return out


def _met(session, task: str, need: Need) -> bool:
    if need.kind == "robot":
        return session.robot is not None
    if need.kind == "step":
        return _done(session, task, need.key)
    if need.kind == "plate":
        return bool(session.plates())
    return setup_done(session, need.key)


def _done(session, task: str, step: str) -> bool:
    if step == "deck":
        state = session.run_state
        return (session.robot is not None and state is not None
                and bool(state.labware) and not session.deck_problems())
    if step == "tip":
        if task == "liquid":
            return session.robot is not None and session.tip.attached is True
        return tip_calibration_status(session)[0]
    if step == "plan":
        # A plan restored from the last run is confirmed with Use this plan.
        routine = session.routine
        return routine is not None and not routine.needs_confirmation
    if step == "dish_bottom":
        return session.is_confirmed("dish_bottom")
    if step == "picking":
        routine = session.routine
        return routine is not None and routine.is_done()
    if step == "well_center":
        # The session imports this module: here, not at the top.
        from .session import MEASURED
        plates = well_center_plates(session)
        return bool(plates) and all(
            session.well_center_status(slot, entry.load_name) == MEASURED
            for slot, entry, _definition in plates)
    if step == "liquid_plan":
        return not session.liquid_plan_problems()
    if step == "run":
        # Confirmed by a liquid run that ended.
        return session.is_confirmed("liquid_run")
    return False


def well_center_plates(session) -> list:
    """The plates the Well center step offers, as `Session.plates` gives
    them: all on the deck, in every task."""
    return session.plates()


# Steps a task can go past without, by task: Work is a toolbox, never
# "done"; Well center is optional everywhere, a plate not measured using
# its drawing; Manual pick may not use the dish at all.
OPTIONAL = {"auto": {"well_center"},
            "manual": {"well_center", "dish_bottom", "work"},
            "liquid": {"well_center"}}


# Steps whose number is in a dashed ring until done. A list of its own:
# Manual pick's Run can be passed but reads as a step like any other.
MARKED_OPTIONAL = {"auto": {"well_center"},
                   "manual": {"well_center", "dish_bottom"},
                   "liquid": {"well_center"}}


def marked_optional(task: str, step: str) -> bool:
    return step in MARKED_OPTIONAL[task]


def optional(task: str, step: str) -> bool:
    return step in OPTIONAL[task]


def step_state(session, task: str, step: str) -> StepState:
    """Whether `step` of `task` is done now, and its first unmet need."""
    need = next((n for n in _needs(session, task, step)
                 if not _met(session, task, n)), None)
    # With a tip on, what the Tip step still wants is its calibration.
    if (need is not None and (need.kind, need.key) == ("step", "tip")
            and session.tip.attached is True):
        need = _TIP_CALIBRATION
    return StepState(_done(session, task, step), need,
                     optional(task, step))
