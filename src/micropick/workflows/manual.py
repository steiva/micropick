"""Hands-on moves from a picture: the camera or the tip to where was clicked.

What Manual control does with a click, and the liquid handling beside it,
with no Qt in it: every function takes the robot and the numbers and blocks,
so a page runs them in a worker and a notebook can call them directly.

The tip travels high
--------------------
Any move across the deck starts with the tip at the top of its travel. The
top is not a number in the profile - it depends on the tip that is fitted,
which changes the Z the robot reports - so it is measured: retract once, read
Z, and remember it (`raise_tip`). Before every later move Z is read again,
and if the tip is below the top, it is retracted first. One read per move is
cheaper than a retract per move, and a retract per move is the only other
way to be sure.

Then one straight line
----------------------
With the tip up, the travel is a single `move_to_coordinates` with
`force_direct`: the gantry goes diagonally to the target at the height it
is at, instead of an axis at a time (`goto_xy`, the calibration sweep's
way) or up-over-down (the robot's own arc planning, which would undo the
point of having raised the tip). A tip target then comes straight down.
`move_to` reads the pose back and raises if the robot declined the move,
so a refusal is a message, not a gantry that silently stayed put.

Two targets, one map
--------------------
The pixel map says which deck point sits under a pixel, relative to the
gantry pose the frame was taken at, and it is zero at the map's reference
pixel. So the gantry pose that puts a clicked point under that reference
pixel - "move the camera there" - is simply the deck point itself
(`camera_target`). The tip is a separate calibration, the pipette offset
from the reference point to the tip, added on top (`tip_target`) - the
calibration check's `pixel_to_robot`.
"""

from __future__ import annotations

import numpy as np

from ..hardware.protocols import Robot, move_to, require_ok, xyz
from .jog import AXES, Limits

__all__ = ["RAISED_TOL_MM", "raise_tip", "camera_target", "tip_target",
           "unreachable", "drive_camera", "drive_tip", "aspirate", "dispense"]

# How far below the measured top the tip may be and still count as raised:
# the reported Z wanders by hundredths after a retract, and a retract for
# that would be a retract before every move.
RAISED_TOL_MM = 0.5


def raise_tip(robot: Robot, z_top: float | None, *, log=None,
              tol: float = RAISED_TOL_MM) -> float:
    """Make sure the tip is at the top of its travel; return that top.

    With `z_top` unknown, retract and measure it. Otherwise read Z and retract
    only if the tip is below the top by more than `tol`.
    """
    if z_top is not None and xyz(robot)[2] >= z_top - tol:
        return z_top
    if log is not None:
        log("retracting leftZ")
    require_ok(robot.retract_axis("leftZ", verbose=False), "retract")
    top = xyz(robot)[2]
    return top if z_top is None else max(top, z_top)


def camera_target(pmap, u: float, v: float, gantry) -> np.ndarray:
    """The gantry XY that puts pixel (u, v) of a frame taken at `gantry`
    under the map's reference pixel."""
    return np.asarray(pmap.to_robot(u, v, np.asarray(gantry)[:2]), dtype=float)


def tip_target(pmap, u: float, v: float, gantry, offset) -> np.ndarray:
    """The XY the tip has to be at to stand over pixel (u, v)."""
    return camera_target(pmap, u, v, gantry) + np.array([offset.dx, offset.dy])


def unreachable(limits: Limits, position) -> str:
    """Why a target is outside the working envelope, or "" if it is not."""
    reasons = []
    for axis, value in zip(AXES, position):
        lo, hi = limits.bounds(axis)
        if value < lo - limits.tol:
            reasons.append(f"{axis} {value:.1f} is below the limit {lo:g}")
        elif value > hi + limits.tol:
            reasons.append(f"{axis} {value:.1f} is past the limit {hi:g}")
    return "; ".join(reasons)


def drive_camera(robot: Robot, xy, z_top: float | None, *, log=None) -> float:
    """The tip up, then the gantry to `xy`. Returns the known top."""
    z_top = raise_tip(robot, z_top, log=log)
    if log is not None:
        log(f"camera to ({xy[0]:.2f}, {xy[1]:.2f})")
    _straight(robot, xy, xyz(robot)[2])
    return z_top


def drive_tip(robot: Robot, xy, z: float, z_top: float | None, *,
              log=None) -> float:
    """The tip up, across to `xy`, then down to `z`. Returns the known top."""
    z_top = raise_tip(robot, z_top, log=log)
    if log is not None:
        log(f"tip to ({xy[0]:.2f}, {xy[1]:.2f}) at z {z:g}")
    _straight(robot, xy, xyz(robot)[2])
    _straight(robot, xy, z)
    return z_top


def _straight(robot: Robot, xy, z: float) -> None:
    """One direct move, verified; see "Then one straight line"."""
    move_to(robot, (float(xy[0]), float(xy[1]), float(z)), min_z_height=1.0,
            force_direct=True)


def aspirate(robot: Robot, volume: float, flow_rate: float):
    """Aspirate where the tip is. Raises if the robot declines."""
    return require_ok(robot.aspirate_in_place(float(volume), float(flow_rate),
                                              verbose=False), "aspirate")


def dispense(robot: Robot, volume: float, flow_rate: float):
    """Dispense where the tip is. Raises if the robot declines."""
    return require_ok(robot.dispense_in_place(float(volume), float(flow_rate),
                                              verbose=False), "dispense")
