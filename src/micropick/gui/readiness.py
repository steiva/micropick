"""What has to be true before the robot can do a picking run.

One list, from the session alone: robot, tip, plate plan and plate,
cameras, calibration, dish bottom, taught positions, the cuboid model. The
Profile page shows it as soon as a profile is loaded, so the day's work
starts from what is still missing; the Picking page adds what only it can
see (its detector loaded, its camera open, the clips' camera) and blocks
Start on any "to do" of either.

Each check says where it is done (`widgets.checklist`). The texts are the
operator's: a "to do" is a sentence that ends in what to do.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .detector import STANDIN, DetectorService
from .widgets.checklist import NOTE, TODO, Check

__all__ = ["DISH_POSITION", "SHAKE_POSITION", "STALE_DAYS", "dated",
           "readiness_checks", "well_centre"]

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


def dated(text: str, when, page: str | None = None, place: str = ""):
    """A done check with its date, or a note when it is old."""
    if when is None:
        return Check(text)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - when).days
    local = when.astimezone().strftime("%Y-%m-%d")
    if age > STALE_DAYS:
        return Check(f"{text} on {local}, {age} days ago: redo it if anything "
                     f"was moved or replaced since.", NOTE, page, place)
    return Check(f"{text} on {local}")


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


def readiness_checks(session) -> list[Check]:
    """Everything before a run that the session can tell, in the order it
    is done."""
    out = []
    profile = session.profile
    out.append(
        Check("Robot connected") if session.robot is not None else
        Check("the robot answers, but no robot session is chosen: start a "
              "new one or continue the current one on the Profile page.",
              TODO, "profile", "Profile") if session.has_connection else
        Check("no robot: connect it on the Profile page.", TODO, "profile",
              "Profile"))
    if profile is None:
        out.append(Check("no profile loaded.", TODO, "profile", "Profile"))
        return out

    upper, lower = session.upper_camera_label, session.lower_camera_label
    out.append(Check("Upper and lower cameras chosen")
               if upper is not None and lower is not None else
               Check("the profile does not say which camera is the "
                     + ("upper and which the lower one" if upper is None
                        and lower is None else
                        "upper one" if upper is None else "lower one")
                     + ": choose in Settings.", TODO, "settings",
                     "Settings"))

    tip = session.tip.attached
    out.append(Check("A tip is on the pipette") if tip is True else Check(
        ("the robot reports no tip on the pipette" if tip is False else
         "the robot's tip state is unknown")
        + ": pick one up on the Robot & Deck page.", TODO, "labware",
        "Robot & Deck"))

    routine = session.routine
    if routine is None:
        out.append(Check(
            "no plate plan: make one on the Plate plan page. A run with "
            "nowhere to put a cuboid picks one up and then asks what to "
            "do with it.", TODO, "routine", "Plate plan"))
    elif routine.needs_confirmation:
        out.append(Check(
            "the plate plan was restored with progress on it and has not "
            "been confirmed; confirm it on the Plate plan page.", TODO,
            "routine", "Plate plan"))
    else:
        out.append(Check(f"Plate plan {getattr(routine, 'name', '')!r}"))
    if routine is not None and session.robot is not None:
        slot = str(routine.destination.slot)
        state = session.run_state
        if state is None or slot not in state.labware:
            out.append(Check(
                f"the robot session holds nothing in slot {slot}, which "
                f"is where this plate plan delivers. Load the plate on "
                f"the Robot & Deck page.", TODO, "labware",
                "Robot & Deck"))
        else:
            problem = next((p for p in session.deck_problems()
                            if p.slot == slot), None)
            out.append(Check(
                f"slot {slot}: the plate was loaded without the module's "
                f"offset, and a well move would hit the module. Load it "
                f"again on the Robot & Deck page.", TODO, "labware",
                "Robot & Deck") if problem is not None else
                Check(f"The plate is on the deck, in slot {slot}"))
            centre = well_centre(session)
            if centre is not None:
                x, y, _z = centre.offset
                out.append(Check(
                    f"Well centre measured on {centre.well or 'a well'} "
                    f"(Liquid handling): deposits go {x:+.2f}, {y:+.2f} mm "
                    f"from the robot's well centre, plus the well offset"))

    calibration = profile.calibration
    if profile.pixel_map is None:
        out.append(Check("no pixel map: run the camera calibration.",
                         TODO, "calibration", "Calibration"))
    else:
        out.append(dated("Camera calibrated", profile.pixel_map.fitted_at,
                         "calibration", "Calibration"))
    if calibration.pipette_offset is None:
        out.append(Check("no pipette offset: run the pipette calibration.",
                         TODO, "calibration", "Calibration"))
    else:
        out.append(dated("Pipette offset measured",
                         calibration.pipette_offset.measured_at,
                         "calibration", "Calibration"))
    bottom = calibration.dish_bottom_set_at
    out.append(dated(
        f"Dish bottom set ({profile.picking.dish_bottom:.2f} mm)", bottom)
        if bottom is not None else Check(
            f"The dish bottom ({profile.picking.dish_bottom:.2f} mm) was "
            f"never measured here: set it under Dish bottom (Z) on the "
            f"Picking page if cuboids are not picked up.", NOTE))

    # Both poses the workflow drives to by name. `shake` is only reached
    # when the dish needs stirring, so without this check a run can start,
    # work for ten minutes and then fail at the one moment the operator is
    # not watching.
    for name, what, done in (
            (DISH_POSITION, "park over the dish and Set position on the "
                            "Picking page", "Picking position set"),
            (SHAKE_POSITION, "jog the tip into the dish where it should "
                             "stir and Set shake position on the Picking "
                             "page", "Shake position set")):
        out.append(Check(done) if name in profile.positions else
                   Check(f"no {name!r} position: {what}.", TODO, "picking",
                         "Picking"))

    model = profile.picking.model_file
    if not model and not session.mock:
        out.append(Check("no cuboid model chosen: choose one in Settings.",
                         TODO, "settings", "Settings"))
    elif model and model != STANDIN and \
            model not in DetectorService.available_weights():
        out.append(Check(f"the cuboid model {model!r} is not on this "
                         f"computer: copy it in, or choose another in "
                         f"Settings.", TODO, "settings", "Settings"))
    else:
        out.append(Check(f"Cuboid model {model or STANDIN}"))
    return out
