"""Tasks as steps: what each step is, whether it is done, what it needs.

A **task** is one of the three kinds of work started from Home, and a
fixed order of **steps** (REDESIGN §4, GLOSSARY):

    Auto pick    Deck → Tip → Plan → Dish bottom → Picking
    Manual pick  Deck → Tip → Dish bottom → Work
    Liquid       Deck → Tip → Well center → Plan → Run

Deck, Tip and Dish bottom are one step each, shared: the same screen and
the same state in every task that has them.

`step_state` answers, for a step of a task, whether it is **done** now
and the **first need** not met - what the dimmed step's one banner names,
with a button that goes there. Both are read from the session each time:
nothing records that a step was visited. A step the operator confirms
(the deck checked, the dish bottom's last value taken) is confirmed for
the robot session (`Session.confirm`).

A need is typed (`Need.kind`): the robot connected ("robot"), an earlier
step ("step", `key` a step), or a Robot setup item ("setup", `key` one of
`SETUP`). Needs are checked in that order, so the banner names the thing
to fix first. The robot is a need of its own because every step that
moves it is dimmed while it is not connected, whatever else is missing.

A step done by a confirmation (`Session.confirm`) is confirmed by its
screen: Deck by its Next, Dish bottom by Save dish bottom or Use it, the
Liquid plan by Use this plan, Run by a liquid run that ended, a plate by
"Skip this plate". Well center counts the plates on the deck until the
Liquid plan says which it uses. Run does not need Well center: an
unmeasured plate is used on purpose, after a warning (REDESIGN §6.8).
"""

from __future__ import annotations

from dataclasses import dataclass

from .readiness import (CALIBRATION_POSITION, DISH_POSITION, SHAKE_POSITION,
                        tip_calibration_status)

__all__ = ["TASKS", "SETUP", "SETUP_TITLES", "OPTIONAL", "Need",
           "StepState", "missing_setup", "setup_done", "skipped_plate",
           "step_state", "steps", "title"]

# task -> (its name on screen, its steps in order)
TASKS = {
    "auto": ("Auto pick", ("deck", "tip", "plan", "dish_bottom", "picking")),
    "manual": ("Manual pick", ("deck", "tip", "dish_bottom", "work")),
    "liquid": ("Liquid", ("deck", "tip", "well_center", "liquid_plan",
                          "run")),
}

_TITLES = {
    "deck": "Deck", "tip": "Tip", "plan": "Plan",
    "dish_bottom": "Dish bottom", "picking": "Picking", "work": "Work",
    "well_center": "Well center", "liquid_plan": "Plan", "run": "Run",
}


def steps(task: str) -> list[str]:
    return list(TASKS[task][1])


def title(step: str) -> str:
    return _TITLES[step]


@dataclass(frozen=True)
class Need:
    kind: str          # "robot", "step" or "setup"
    key: str           # the step or the Robot setup item; "robot"
    text: str          # the banner's sentence


@dataclass(frozen=True)
class StepState:
    done: bool
    need: Need | None  # the first need not met, or None
    optional: bool = False


_ROBOT = Need("robot", "robot", "The robot is not connected.")

# The Robot setup items, in the order of its page (REDESIGN §5): the
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
    "dish_position": ("Set the position over the dish first.",
                      lambda s: DISH_POSITION in s.profile.positions),
    "shake_position": ("Set the shake position first.",
                       lambda s: SHAKE_POSITION in s.profile.positions),
    "calibration_position": ("Set the calibration position first.",
                             lambda s: CALIBRATION_POSITION
                             in s.profile.positions),
}

# Each Robot setup item's name on screen.
SETUP_TITLES = {
    "upper_camera": "Upper camera",
    "camera_calibration": "Camera calibration",
    "lower_camera": "Lower camera",
    "platforms": "Platforms",
    "dish_position": "Position over the dish",
    "shake_position": "Shake position",
    "calibration_position": "Calibration position",
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

# step -> (needs the robot, earlier steps, Robot setup items). Tip is
# task-dependent: Liquid does not use the camera, so its Tip needs no
# calibration (REDESIGN §4).
_NEEDS = {
    "deck": (True, (), ()),
    "tip": (True, ("deck",), ("camera_calibration", "calibration_position")),
    "plan": (False, ("deck",), ()),
    "dish_bottom": (True, ("tip",), ("dish_position",)),
    "picking": (True, ("deck", "tip", "plan", "dish_bottom"),
                ("dish_position", "shake_position")),
    "work": (True, ("deck", "tip", "dish_bottom"), ()),
    "well_center": (True, ("deck", "tip"), ()),
    "liquid_plan": (False, ("deck",), ()),
    "run": (True, ("deck", "tip", "liquid_plan"), ()),
}


def _needs(task: str, step: str) -> list[Need]:
    robot, earlier, setup = _NEEDS[step]
    if task == "liquid" and step == "tip":
        setup = ()
    out = [_ROBOT] if robot else []
    out += [Need("step", s, _STEP_NEEDS[s]) for s in earlier]
    out += [Need("setup", k, SETUP[k][0]) for k in setup]
    return out


def _met(session, task: str, need: Need) -> bool:
    if need.kind == "robot":
        return session.robot is not None
    if need.kind == "step":
        return _done(session, task, need.key)
    return setup_done(session, need.key)


def _done(session, task: str, step: str) -> bool:
    if step == "deck":
        return (session.robot is not None and session.is_confirmed("deck")
                and not session.deck_problems())
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
        plates = session.plates()
        profile = session.profile
        return bool(plates) and profile is not None and all(
            profile.deck.well_center(slot, entry.load_name) is not None
            or session.is_confirmed(skipped_plate(slot, entry.load_name))
            for slot, entry, _definition in plates)
    if step in ("liquid_plan", "run"):
        # Confirmed by Use this plan, and by a liquid run that ended.
        return session.is_confirmed("liquid_run" if step == "run"
                                    else step)
    return False


def skipped_plate(slot, load_name: str) -> str:
    """The confirmation that a plate's well center is not measured in this
    robot session, its drawing used instead (GLOSSARY, Skipped plate)."""
    return f"plate-skipped:{slot}:{load_name}"


# Steps a task can go past without: Work is a toolbox, never "done".
OPTIONAL = {"work"}


def step_state(session, task: str, step: str) -> StepState:
    """Whether `step` of `task` is done now, and its first unmet need."""
    need = next((n for n in _needs(task, step)
                 if not _met(session, task, n)), None)
    return StepState(_done(session, task, step), need, step in OPTIONAL)
