"""Hands-on moves from a picture: where a click sends the camera or the tip.

What Manual control does with a click, and the liquid handling beside it,
with no Qt in it: every function takes the robot and the numbers and blocks,
so a page runs them in a worker and a notebook can call them directly. The
moves themselves - the travel height, the top of Z - are `workflows.motion`.

Two targets, one map
--------------------
The pixel map says which deck point sits under a pixel, relative to the
gantry pose the frame was taken at, and it is zero at the map's reference
pixel. So the gantry pose that puts a clicked point under that reference
pixel - "move the camera there" - is simply the deck point itself
(`camera_target`). The tip is a separate calibration, the pipette offset
from the reference point to the tip, added on top (`tip_target`) - the
calibration check's `pixel_to_robot`.

How far down a target may go
----------------------------
A height typed for a target (`under_the_bottom`) is checked against the
bottom of its slot, apart from the jog envelope: a jog step, and Dish
bottom's lowering, have to be able to go down to a touch. Under a bottom,
not on it, since pressing lightly on it stays possible: by less under a
measured one, which is known better than a drawing's.

How the tip leaves
------------------
After Aspirate or Dispense the tip goes up where it stands (`retract`): at
once, after a delay, or not at all, as the card says (`Retract`). The delay
is the operator's to end, and a stop during it leaves the tip down: it is
what was asked for by someone looking at the tip in the liquid.
"""

from __future__ import annotations

import threading
from typing import NamedTuple

import numpy as np

from ..hardware.protocols import Robot, require_ok
from .motion import Motion

__all__ = ["camera_target", "tip_target", "aspirate", "dispense",
           "under_the_bottom", "UNDER_MEASURED_BOTTOM_MM",
           "UNDER_DRAWN_BOTTOM_MM", "Retract", "retract"]

# How far under a slot's bottom a target may go.
UNDER_MEASURED_BOTTOM_MM = 1.0
UNDER_DRAWN_BOTTOM_MM = 2.0


class Retract(NamedTuple):
    """How the tip leaves: whether it goes up, and after how long."""
    rises: bool = True
    delay_s: float = 0.0


def retract(motion: Motion, how: Retract, stopped: threading.Event, *,
            log=None) -> bool:
    """The tip up as `how` says; True if it went up. `stopped` ends a delay
    with the tip down."""
    if not how.rises:
        return False
    if how.delay_s:
        if log is not None:
            log(f"waiting {how.delay_s:g} s before going up")
        if stopped.wait(how.delay_s):
            return False
    motion.rise(log=log)
    return True


def camera_target(pmap, u: float, v: float, gantry) -> np.ndarray:
    """The gantry XY that puts pixel (u, v) of a frame taken at `gantry`
    under the map's reference pixel."""
    return np.asarray(pmap.to_robot(u, v, np.asarray(gantry)[:2]), dtype=float)


def tip_target(pmap, u: float, v: float, gantry, offset) -> np.ndarray:
    """The XY the tip has to be at to stand over pixel (u, v)."""
    return camera_target(pmap, u, v, gantry) + np.array([offset.dx, offset.dy])


def under_the_bottom(z: float, bottom: float, slot, *,
                     measured: bool) -> str:
    """Why a target at `z` is too far under the `bottom` of `slot`, or "":
    the words of a sentence, as `motion.refusal`, for the caller to finish."""
    limit = UNDER_MEASURED_BOTTOM_MM if measured else UNDER_DRAWN_BOTTOM_MM
    if round(bottom - z, 6) <= limit:
        return ""
    which = "measured" if measured else "drawing's"
    return f"Z {round(z, 2):g} is below the {which} bottom in slot {slot}"


def aspirate(robot: Robot, volume: float, flow_rate: float):
    """Aspirate where the tip is. Raises if the robot declines."""
    return require_ok(robot.aspirate_in_place(float(volume), float(flow_rate),
                                              verbose=False), "aspirate")


def dispense(robot: Robot, volume: float, flow_rate: float):
    """Dispense where the tip is. Raises if the robot declines."""
    return require_ok(robot.dispense_in_place(float(volume), float(flow_rate),
                                              verbose=False), "dispense")
