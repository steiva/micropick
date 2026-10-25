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
"""

from __future__ import annotations

import numpy as np

from ..hardware.protocols import Robot, require_ok

__all__ = ["camera_target", "tip_target", "aspirate", "dispense"]


def camera_target(pmap, u: float, v: float, gantry) -> np.ndarray:
    """The gantry XY that puts pixel (u, v) of a frame taken at `gantry`
    under the map's reference pixel."""
    return np.asarray(pmap.to_robot(u, v, np.asarray(gantry)[:2]), dtype=float)


def tip_target(pmap, u: float, v: float, gantry, offset) -> np.ndarray:
    """The XY the tip has to be at to stand over pixel (u, v)."""
    return camera_target(pmap, u, v, gantry) + np.array([offset.dx, offset.dy])


def aspirate(robot: Robot, volume: float, flow_rate: float):
    """Aspirate where the tip is. Raises if the robot declines."""
    return require_ok(robot.aspirate_in_place(float(volume), float(flow_rate),
                                              verbose=False), "aspirate")


def dispense(robot: Robot, volume: float, flow_rate: float):
    """Dispense where the tip is. Raises if the robot declines."""
    return require_ok(robot.dispense_in_place(float(volume), float(flow_rate),
                                              verbose=False), "dispense")
