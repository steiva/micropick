"""Moving the upper camera from a Robot setup sheet: over a slot, or onto
the point clicked on the picture.

The camera calibration sheet and the position sheets move the camera the
same way, through their jog panel's `run_job` (so a move never overlaps
a key press) and the session's `Motion.camera_to` (the tip up, across at
the travel height).

**Over a slot** is the slot's center less the camera's distance from the
tip: measured by the tip calibration once there is one, else the start
offset in Settings (`AppSettings.tip_cal_start_offset`) - it only has to
bring the slot into view.

**A click** goes through the pixel map. Before the first camera
calibration a pixel has no direction on the deck, so a click moves
nothing and the panel says to use the arrows.

**The tip onto a click** (the shake position's hole) is the camera's
target plus the measured tip offset, as Manual pick's Work moves the tip
from the picture, and only inside the calibrated area: outside it the map
extrapolates. The gantry at the travel height is the tip at it, so the
move is a camera move to the tip's target.

**A position's slot** is the one the camera is over for the camera
positions (`position_marks.CAMERA_POSITIONS`: centered by the camera,
stored as the tip's pose, so the tip is a slot away), the tip's for the
others.

A target the gantry cannot reach at the height it crosses the deck at (or
where it is, while that is not measured) is not moved to; the panel says
why in a toast.
"""

from __future__ import annotations

import logging

import numpy as np

from ..core.calibration.pixel_map import PixelMap
from ..core.deck import nearest_slot, slot_center
from ..workflows.jog import Reached
from ..workflows.manual import camera_target, tip_target
from .position_marks import CAMERA_POSITIONS

__all__ = ["NO_TIP_CALIBRATION", "OUTSIDE_THE_MAP", "camera_offset",
           "camera_slot", "move_over_slot", "move_tip_to_pixel", "move_to_pixel",
           "out_of_reach", "pose_slot"]

log = logging.getLogger(__name__)

NO_TIP_CALIBRATION = "Calibrate the tip first."
OUTSIDE_THE_MAP = "That spot is outside the calibrated area."


def camera_offset(session) -> np.ndarray:
    """From the camera to the tip: measured, else Settings' start."""
    profile = session.profile
    offset = (profile.calibration.pipette_offset
              if profile is not None else None)
    if offset is not None:
        return np.array([offset.dx, offset.dy])
    return np.array(session.settings.tip_cal_start_offset)


def pose_slot(session, name: str, xy) -> int:
    """The slot the stored pose `name` at `xy` is in, in words an operator
    knows: the camera's for a camera position, else the tip's."""
    if name in CAMERA_POSITIONS:
        return camera_slot(session, xy)
    return nearest_slot(float(xy[0]), float(xy[1]))


def camera_slot(session, xy) -> int:
    """The slot the camera is over with the gantry at `xy`."""
    x, y = np.array([float(xy[0]), float(xy[1])]) + camera_offset(session)
    return nearest_slot(x, y)


def out_of_reach(session, jog, xy, what: str) -> bool:
    """Refused, and True, when the gantry cannot go to `xy` at the height
    it crosses the deck at."""
    motion, pose = session.motion, jog.pose
    z = motion.travel_z if motion.travel_z is not None else (
        pose[2] if pose is not None else None)
    why = motion.refused((xy[0], xy[1], z)) if z is not None else ""
    if why:
        jog.refuse(f"{what} is out of reach: {why}.")
    return bool(why)


def move_over_slot(session, jog, slot: int, *, then=None) -> bool:
    """The camera over the center of `slot`; `then(said)` once there.
    False when nothing was started."""
    xy = np.asarray(slot_center(slot)) - camera_offset(session)
    if out_of_reach(session, jog, xy, f"Slot {slot}"):
        return False
    motion = session.motion

    def job(log_):
        return Reached(motion.camera_to(xy, log=log_),
                       f"Camera over slot {slot}.")

    log.info("camera to slot %s (%.1f, %.1f)", slot, xy[0], xy[1])
    return jog.run_job(job, what=f"moving the camera over slot {slot}",
                       then=then)


def move_to_pixel(session, jog, u: float, v: float, *,
                  no_map: str) -> bool:
    """The camera onto pixel (u, v) of the picture, through the pixel map;
    without one, `no_map` is said and nothing moves."""
    profile, robot = session.profile, session.robot
    if profile is None or profile.pixel_map is None:
        jog.refuse(no_map)
        return False
    if robot is None:
        return False
    pmap = PixelMap.from_config(profile.pixel_map)
    # The pose the panel shows, not one read here: a read is a robot
    # command, and the window would wait on it (#36).
    gantry = jog.pose
    if gantry is None:
        return False
    xy = camera_target(pmap, u, v, gantry)
    if out_of_reach(session, jog, xy, "That point"):
        return False
    motion = session.motion

    def job(log_):
        return Reached(motion.camera_to(xy, log=log_))

    return jog.run_job(job, what="centering the camera")


def tip_at_pixel(session, jog, u: float, v: float, *, no_map: str):
    """Where the tip goes down at pixel (u, v) of the picture, through the
    pixel map and the measured tip offset. Without a map or a tip
    calibration, said, and None."""
    profile, robot = session.profile, session.robot
    if profile is None or profile.pixel_map is None:
        jog.refuse(no_map)
        return None
    offset = profile.calibration.pipette_offset
    if offset is None:
        jog.refuse(NO_TIP_CALIBRATION)
        return None
    if robot is None:
        return None
    pmap = PixelMap.from_config(profile.pixel_map)
    if not pmap.covers(u, v):
        jog.refuse(OUTSIDE_THE_MAP)
        return None
    gantry = jog.pose                    # not a read: see move_to_pixel
    if gantry is None:
        return None
    return tip_target(pmap, u, v, gantry[:2], offset)


def move_tip_to_pixel(session, jog, u: float, v: float, *, no_map: str,
                      then=None) -> bool:
    """The tip over pixel (u, v) of the picture at the travel height;
    `then(said)` once there. See `tip_at_pixel`: nothing moves without it."""
    xy = tip_at_pixel(session, jog, u, v, no_map=no_map)
    if xy is None or out_of_reach(session, jog, xy, "That point"):
        return False
    motion = session.motion

    def job(log_):
        return Reached(motion.camera_to(xy, log=log_),
                       "Tip over the point clicked.")

    log.info("tip to (%.2f, %.2f) at the travel height", xy[0], xy[1])
    return jog.run_job(job, what="moving the tip over the point clicked",
                       then=then)
