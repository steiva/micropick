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
leaves behind is there, whatever came before it - a pipette offset measured
last week is done though no robot is connected today. A step not done whose
earlier steps are not done either is *blocked*: dimmed, with what to do
first in its tooltip. What is particular to the moment (a date, a slot, a
model file that is missing) is the tooltip too.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .detector import STANDIN, DetectorService
from .widgets.checklist import BLOCKED, NOTE, OK, TODO, Check

__all__ = ["DISH_POSITION", "SHAKE_POSITION", "STALE_DAYS", "STEPS",
           "readiness_checks", "step", "well_centre"]

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
        return True, (f"{what} on {day}, {age} days ago: redo it if anything "
                      f"was moved or replaced since."), True
    return True, f"{what} on {day}.", False


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
    out["profile"] = ((True, f"Profile {profile.name!r} is loaded.", False)
                      if profile is not None else
                      (False, "Choose one under Load profile and press "
                              "Load.", False))
    if robot is not None:
        out["robot"] = (True, f"Robot session: {session.robot_state}.", False)
    elif session.has_connection:
        out["robot"] = (False, "The robot answers, but no robot session is "
                               "chosen: start a new one.", False)
    else:
        out["robot"] = (False, "Turn the robot on, wait for it to start, "
                               "then press Connect.", False)
    if profile is None:
        for key in STEPS:
            out.setdefault(key, (False, "", False))
        return out

    upper, lower = session.upper_camera_label, session.lower_camera_label
    missing = [name for name, label in (("upper", upper), ("lower", lower))
               if label is None]
    out["cameras"] = ((True, f"Upper: {upper}; lower: {lower}.", False)
                      if not missing else
                      (False, f"The profile does not say which camera is the "
                              f"{' and the '.join(missing)} one.", False))

    model = profile.picking.model_file
    if not model and session.mock:
        out["model"] = (True, f"{STANDIN} (--mock).", False)
    elif not model:
        out["model"] = (False, "No cuboid model is named in the profile.",
                        False)
    elif model != STANDIN and model not in DetectorService.available_weights():
        out["model"] = (False, f"{model} is not on this computer: copy it "
                               f"in, or choose another.", False)
    else:
        out["model"] = (True, f"{model}.", False)

    pixel_map = profile.pixel_map
    out["camera_calibration"] = (
        _dated(pixel_map.fitted_at, "Calibrated") if pixel_map is not None
        else (False, "There is no pixel map in this profile.", False))
    tip = session.tip.attached
    out["tip"] = ((True, "The robot reports a tip on the pipette.", False)
                  if tip is True else
                  (False, "The robot reports no tip on the pipette."
                   if tip is False else
                   "The robot's tip state is unknown.", False))
    offset = profile.calibration.pipette_offset
    out["tip_calibration"] = (
        _dated(offset.measured_at, "Measured") if offset is not None
        else (False, "There is no tip offset in this profile.", False))

    for key, name in (("dish_position", DISH_POSITION),
                      ("shake_position", SHAKE_POSITION)):
        out[key] = ((True, "Set.", False) if name in profile.positions else
                    (False, f"No {name!r} position in the profile.", False))
    bottom = profile.calibration.dish_bottom_set_at
    depth = f"{profile.picking.dish_bottom:.2f} mm"
    out["dish_bottom"] = (
        (True, f"{depth}, set on {_date(bottom)[0]}.", False)
        if bottom is not None else
        (False, f"{depth}, never measured here: set it if cuboids are not "
                f"picked up.", False))

    routine = session.routine
    if routine is None:
        out["plan"] = (False, "A run with nowhere to put a cuboid picks one "
                              "up and then asks what to do with it.", False)
    elif routine.needs_confirmation:
        out["plan"] = (False, "The plate plan was restored with progress on "
                              "it: confirm it on the Plate plan page.",
                       False)
    else:
        out["plan"] = (True, f"Plate plan {getattr(routine, 'name', '')!r}.",
                       False)

    out["plate"] = (False, "", False)
    if routine is not None and robot is not None:
        slot = str(routine.destination.slot)
        state = session.run_state
        if state is None or slot not in state.labware:
            out["plate"] = (False, f"The robot session holds nothing in slot "
                                   f"{slot}, where the plate plan delivers.",
                            False)
        elif any(p.slot == slot for p in session.deck_problems()):
            out["plate"] = (False, f"Slot {slot}: the plate was loaded "
                                   f"without the module's offset, and a well "
                                   f"move would hit the module. Load it "
                                   f"again.", False)
        else:
            detail = f"In slot {slot}."
            centre = well_centre(session)
            if centre is not None:
                x, y, _z = centre.offset
                detail += (f" Well centre measured on "
                           f"{centre.well or 'a well'} (Liquid handling): "
                           f"deposits go {x:+.2f}, {y:+.2f} mm from the "
                           f"robot's well centre, plus the well offset.")
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
