"""The Operation checklist: what has to be done before a picking run.

A fixed list of steps, in the order the work goes - connect, load a profile,
choose the cameras and the model, put a tip on, calibrate the camera and the
tip, make the plate plan, teach the positions, load the plate - from the
session alone. The
Profile page shows it as soon as it opens; the Picking page adds what only
it can see (its detector, its camera, the clips' camera) under one line for
this list, and blocks Start on anything not done in either.

The steps never change their words (`widgets.checklist`): each says what to
do, and only its mark follows the session. A step is *done* when what it
leaves behind is there, whatever came before it - a pixel map fitted last
week is done though no robot is connected today. The tip calibration is the
exception: it holds for the one tip it measured, in the robot session it was
measured in (`tip_calibration_status`). A step not done whose
earlier steps are not done either is *blocked*: dimmed, with what to do
first in its tooltip. What is particular to the moment (a date, a slot, a
model file that is missing) is the tooltip too.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .detector import STANDIN, DetectorService
from .widgets.checklist import BLOCKED, NOTE, OK, TODO, Check

__all__ = ["DISH_POSITION", "SHAKE_POSITION", "STALE_DAYS", "STEPS",
           "readiness_checks", "step", "tip_calibration_status",
           "well_centre"]

# The profile position the dish is looked at from, beside tip_calib. The
# name is the workflow's: PickingSession reads profile.where("observe").
DISH_POSITION = "observe"

# The other pose the workflow drives to by name, when the dish has to be
# stirred to separate crowded cuboids. Checked before a run starts rather
# than met ten minutes into one.
SHAKE_POSITION = "shake"

# A calibration older than this is said, not refused: an old one may be
# perfectly good, and only the operator knows whether anything was moved.
STALE_DAYS = 30

# key: (what to do, page, words on its button, the steps it needs first).
# The order is the order of the work.
STEPS = {
    "robot": ("Connect the robot", "profile", "Profile", ()),
    "profile": ("Load a profile", "profile", "Profile", ()),
    "cameras": ("Choose the upper and lower cameras", "settings", "Settings",
                ("profile",)),
    "model": ("Choose the cuboid model", "settings", "Settings",
              ("profile",)),
    "tip": ("Put a tip on the pipette", "labware", "Robot & Deck",
            ("robot",)),
    "camera_calibration": ("Calibrate the camera", "camera_calibration",
                           "Camera calibration",
                           ("profile", "robot", "cameras")),
    "tip_calibration": ("Calibrate the tip", "tip_calibration",
                        "Tip calibration",
                        ("camera_calibration", "tip")),
    "plan": ("Make a plate plan", "routine", "Plate plan", ("profile",)),
    "dish_position": ("Set the picking position over the dish", "picking",
                      "Picking", ("profile", "robot")),
    "shake_position": ("Set the shake position in the dish", "picking",
                       "Picking", ("profile", "robot")),
    "dish_bottom": ("Set the dish bottom", "picking", "Picking",
                    ("profile", "robot")),
    "plate": ("Load the plate on the deck", "labware", "Robot & Deck",
              ("robot", "plan")),
}

# Steps a run can start without: said, never in the way.
OPTIONAL = {"dish_bottom"}


def _date(when) -> tuple[str, int]:
    """("2026-08-07", age in days) of a stored time."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - when).days
    return when.astimezone().strftime("%Y-%m-%d"), age


def _dated(when, what: str) -> tuple[bool, str, bool]:
    """(done, detail, stale) for a step that leaves a date behind."""
    if when is None:
        return True, "", False
    day, age = _date(when)
    if age > STALE_DAYS:
        return True, (f"{what} on {day} ({age} days ago)."), True
    return True, f"{what} on {day}.", False


def tip_calibration_status(session) -> tuple[bool, str, bool]:
    """(done, detail, stale) of the tip calibration.

    Done only while the tip it measured is on the pipette: the offset records
    the robot session and the pick-up that put that tip on
    (`PipetteOffset.tip_pickup`). No tip, another tip, or a robot restarted
    since - a new robot session - and it is to do again. The profile keeps
    the numbers; the next calibration starts from them.
    """
    profile = session.profile
    offset = (profile.calibration.pipette_offset if profile is not None
              else None)
    if offset is None:
        return False, "There is no tip offset in this profile.", False
    tip, state = session.tip, session.run_state
    if session.robot is None or tip.attached is not True:
        return False, ("No tip on the pipette." if tip.attached is False
                       else "Tip state unknown."), False
    run_id = state.run_id if state is not None else None
    if offset.robot_session is None or offset.robot_session != run_id:
        return False, "Measured in an earlier robot session.", False
    if offset.tip_pickup is None or offset.tip_pickup != tip.pickup:
        return False, "Measured on a different tip.", False
    return _dated(offset.measured_at, "Measured on this tip")


def well_centre(session):
    """The measured well centre of the plate the plan delivers to, if the
    Liquid handling page measured one: the run adds its x and y to every
    deposit (`workflows.picking`, "The measured well centre")."""
    routine, state = session.routine, session.run_state
    if session.profile is None or routine is None or state is None:
        return None
    entry = state.labware.get(str(routine.destination.slot))
    if entry is None:
        return None
    return session.profile.deck.well_centre(routine.destination.slot,
                                            entry.load_name)


def _status(session) -> dict[str, tuple[bool, str, bool]]:
    """key -> (done, detail, stale) for every step."""
    profile, robot = session.profile, session.robot
    out: dict[str, tuple[bool, str, bool]] = {}
    out["profile"] = ((True, f"Profile {profile.name!r}.", False)
                      if profile is not None else
                      (False, "", False))
    if robot is not None:
        out["robot"] = (True, f"Robot session: {session.robot_state}.", False)
    elif session.has_connection:
        out["robot"] = (False, "Robot found; no robot session chosen.",
                        False)
    else:
        out["robot"] = (False, "Robot not connected.", False)
    if profile is None:
        for key in STEPS:
            out.setdefault(key, (False, "", False))
        return out

    upper, lower = session.upper_camera_label, session.lower_camera_label
    missing = [name for name, label in (("upper", upper), ("lower", lower))
               if label is None]
    out["cameras"] = ((True, f"Upper: {upper}; lower: {lower}.", False)
                      if not missing else
                      (False, f"Not set: {' and '.join(missing)} camera.",
                       False))

    model = profile.picking.model_file
    if not model and session.mock:
        out["model"] = (True, f"{STANDIN} (--mock).", False)
    elif not model:
        out["model"] = (False, "No cuboid model chosen.", False)
    elif model != STANDIN and model not in DetectorService.available_weights():
        out["model"] = (False, f"{model} not found on this computer.",
                        False)
    else:
        out["model"] = (True, f"{model}.", False)

    pixel_map = profile.pixel_map
    out["camera_calibration"] = (
        _dated(pixel_map.fitted_at, "Calibrated") if pixel_map is not None
        else (False, "No pixel map in this profile.", False))
    tip = session.tip.attached
    out["tip"] = ((True, "Tip on.", False)
                  if tip is True else
                  (False, "No tip." if tip is False else
                   "Tip state unknown.", False))
    out["tip_calibration"] = tip_calibration_status(session)

    for key, name in (("dish_position", DISH_POSITION),
                      ("shake_position", SHAKE_POSITION)):
        out[key] = ((True, "Set.", False) if name in profile.positions else
                    (False, "Not set.", False))
    bottom = profile.calibration.dish_bottom_set_at
    depth = f"{profile.picking.dish_bottom:.2f} mm"
    out["dish_bottom"] = (
        (True, f"{depth}, set on {_date(bottom)[0]}.", False)
        if bottom is not None else
        (False, f"{depth} (default, not measured).", False))

    routine = session.routine
    if routine is None:
        out["plan"] = (False, "No plate plan.", False)
    elif routine.needs_confirmation:
        out["plan"] = (False, "Restored plate plan: confirm it on the Plate "
                              "plan page.", False)
    else:
        out["plan"] = (True, f"Plate plan {getattr(routine, 'name', '')!r}.",
                       False)

    out["plate"] = (False, "", False)
    if routine is not None and robot is not None:
        slot = str(routine.destination.slot)
        state = session.run_state
        if state is None or slot not in state.labware:
            out["plate"] = (False, f"Slot {slot} (plate plan destination) "
                                   f"is empty.",
                            False)
        elif any(p.slot == slot for p in session.deck_problems()):
            out["plate"] = (False, f"Slot {slot}: plate loaded without the "
                                   f"module offset. Load it again.", False)
        else:
            detail = f"In slot {slot}."
            centre = well_centre(session)
            if centre is not None:
                x, y, _z = centre.offset
                detail += (f" Measured well centre "
                           f"({centre.well or 'a well'}): {x:+.2f}, "
                           f"{y:+.2f} mm.")
            out["plate"] = (True, detail, False)
    return out


def step(key: str, done: bool, detail: str, stale: bool,
         first_undone: list[str]) -> Check:
    """One step's line from its state and the steps before it not done."""
    text, page, place, _needs = STEPS.get(key, (key, None, "", ()))
    optional = key in OPTIONAL
    if done:
        state = NOTE if stale else OK
    elif first_undone:
        state = BLOCKED
        names = ", ".join(f'"{STEPS[k][0]}"' for k in first_undone)
        detail = f"First: {names}." + (f" {detail}" if detail else "")
    else:
        state = NOTE if optional else TODO
    return Check(text, state, page, place, detail, optional)


def readiness_checks(session) -> list[Check]:
    """The Operation checklist, every step, in order."""
    status = _status(session)
    out = []
    for key, (_text, _page, _place, needs) in STEPS.items():
        done, detail, stale = status[key]
        undone = [k for k in needs if not status[k][0]]
        out.append(step(key, done, detail, stale, undone))
    return out
