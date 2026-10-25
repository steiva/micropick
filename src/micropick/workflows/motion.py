"""Moving the tip across the deck: the travel height, the top of Z, reach.

One `Motion` per robot connection. Every move across the deck - Manual
control's clicks, the jog panel's Go to, the pipette calibration's, Liquid
handling, the picking run - goes through it, so the rules below live here
and nowhere else. No Qt: it takes a robot and blocks, so a page runs it in a
worker, a test on the mock, a notebook directly.

Two tops
--------
The **jog envelope's top** (`envelope`) is computed: the bare nozzle's
retracted height `Z_NOZZLE_TOP_MM` less the tip's length, less
`JOG_BELOW_TOP_MM`. It bounds jog steps and is what a memo point with no Z
is checked at. Never an overlap computed here: the robot's differs from the
installed pipette definition's.

The **measured top** (`top`) is read from the robot: retract once, read Z,
remember it. It is what the travel height is under. It depends only on the
tip that is on, so it is shared by every caller and dropped when the tip
changes (`tip_changed`) or the robot does (`set_robot`); a jog step or a
Home leaves it standing.

The tip travels high - but not at the very top
-----------------------------------------------
The travel is at `TRAVEL_BELOW_TOP_MM` under the measured top, not at it.
With a long tip the robot accepts a moveToCoordinates at the retracted
height and does nothing - the refusal `goto_xy` was written around - and a
millimetre lower it moves normally. So before each move Z is read: at the
travel height or above, the move goes ahead; below it, the axis is
retracted first (`raise_tip`). One read per move is cheaper than a retract
per move.

The same refusal meets a stored pose taught at the top - `tip_calib`,
`observe`, anything saved straight after a retract: "Destination out of
bounds in the Z-axis". So no target goes higher than the travel height
(`reachable`); such a pose is reached 1 mm under where it was taught,
which the camera on the carriage does not see.

Then one straight line
----------------------
The travel is a single `move_to_coordinates` with `force_direct`: the
gantry goes diagonally to the target at the travel height, instead of an
axis at a time (`goto_xy`, the calibration sweep's way) or up-over-down
(the robot's own arc planning, which would undo the point of having raised
the tip). A tip target then comes straight down. `move_to` reads the pose
back and raises if the robot declined the move, so a refusal is a message,
not a gantry that silently stayed put.

Threads
-------
The measured top is read and written under a lock, so a clear from the GUI
thread and a move in a worker cannot tear it. Keeping two workers from
moving the robot at once is not this module's business.
"""

from __future__ import annotations

import threading

import numpy as np

from ..core.deck import SLOT_HEIGHT, SLOT_WIDTH, slot_center
from ..hardware.protocols import Robot, move_to, require_ok, xyz
from .jog import AXES, Limits

__all__ = ["Motion", "NoRobot", "WELL_LEVELS", "TRAVEL_BELOW_TOP_MM",
           "Z_NOZZLE_TOP_MM", "JOG_BELOW_TOP_MM", "UNKNOWN_TIP_LENGTH_MM",
           "MODULE_MARGIN_MM", "envelope_for", "refusal"]

# The robot's names for where in a well an offset is measured from.
WELL_LEVELS = ("top", "center", "bottom")

# The travel height, under the measured top: see "The tip travels high".
TRAVEL_BELOW_TOP_MM = 1.0

# The reported Z wanders by hundredths; a tip that far under the travel
# height is still at it.
READ_TOL_MM = 0.05

# The top of Z for the bare nozzle: what the robot reads retracted with no
# tip (bench, 2026-10-12). A hard ceiling - a pipette driven above its
# retracted height stops answering until it is homed. The robot reports Z
# at the tip's end with a tip on, so the top is then this less the tip's
# length (`Tip.length`): the retracted height with that tip, 159.9 mm for
# the wide bore rack.
Z_NOZZLE_TOP_MM = 200.6
# Jog steps stop this far under the top: the robot refuses a target at the
# retracted height itself, and Retract still gets there.
JOG_BELOW_TOP_MM = 0.5
# The length assumed for a tip on whose rack is not known: longer than any
# rack on the bench (the XL's 91 mm), so the top is too low, never too high.
UNKNOWN_TIP_LENGTH_MM = 100.0
# A module may stand a little past its slot opening; the raised floor over
# it reaches this much further on every side.
MODULE_MARGIN_MM = 3.0


class NoRobot(RuntimeError):
    """A move was asked for with no robot connected."""


def envelope_for(tip, modules) -> Limits:
    """The jog envelope with `tip` on (anything with `attached` and
    `length`, as `gui.session.Tip`) and the profile's deck `modules`.

    Each deck module raises the floor over its slot to its own top, so a
    jog or a click cannot drive the tip into it. The labware it carries
    sits above that top, so nothing a step needs is below it. The top
    follows the tip (see `Z_NOZZLE_TOP_MM`): with a fixed 150 a gantry with
    no tip read ~200 when retracted, sat above its own envelope, and every
    step up was refused.
    """
    floors = []
    for module in modules:
        for slot in module.slots:
            cx, cy = slot_center(slot)
            half_x = SLOT_WIDTH / 2 + MODULE_MARGIN_MM
            half_y = SLOT_HEIGHT / 2 + MODULE_MARGIN_MM
            name = f"{module.name or 'module'} in slot {slot}"
            floors.append((cx - half_x, cy - half_y, cx + half_x,
                           cy + half_y, float(module.height_mm), name))
    if tip is not None and tip.attached is False:
        length = 0.0
    elif tip is not None and tip.attached and tip.length is not None:
        length = tip.length
    else:                                 # a tip of unknown length, or
        length = UNKNOWN_TIP_LENGTH_MM    # not known whether there is one
    top = Z_NOZZLE_TOP_MM - length - JOG_BELOW_TOP_MM
    return Limits(x=(0.0, 380.0), y=(0.0, 350.0), z=(0.1, top),
                  floors=tuple(floors))


def refusal(limits: Limits, position) -> str:
    """Why a target is outside `limits`, or "" if it is not.

    A Z above the top is not a reason: every move these targets are for
    goes no higher than the travel height (`Motion.reachable`), and the Z
    given is often where the tip is now - after a retract, above the jog
    top - or a pose taught there.
    """
    reasons = []
    for axis, value in zip(AXES, position):
        lo, hi = limits.bounds(axis)
        if value < lo - limits.tol:
            reasons.append(f"{axis} {value:.1f} is below the limit {lo:g}")
        elif axis != "z" and value > hi + limits.tol:
            reasons.append(f"{axis} {value:.1f} is past the limit {hi:g}")
    floor, what = limits.floor(position[0], position[1])
    if what and position[2] < floor - limits.tol:
        reasons.append(f"z {position[2]:.1f} is below the top of the {what} "
                       f"({floor:g})")
    return "; ".join(reasons)


def _tip_key(tip):
    """What about a tip changes the top: whether one is on, which, how long."""
    if tip is None:
        return None
    return (tip.attached, getattr(tip, "pickup", None), tip.length)


class Motion:
    """The robot's moves across the deck. See the module docstring.

    `modules` returns the profile's deck modules when asked, so the envelope
    is never built from a profile since replaced.
    """

    def __init__(self, robot: Robot | None = None, *, modules=None, tip=None):
        self._robot = robot
        self._modules = modules if modules is not None else (lambda: ())
        self._tip = tip
        self._top: float | None = None
        self._lock = threading.Lock()

    # -- what the moves depend on ---------------------------------------------

    @property
    def robot(self) -> Robot | None:
        return self._robot

    def set_robot(self, robot: Robot | None) -> None:
        """Another robot connection, or none: its top is not known yet."""
        self._robot = robot
        self._forget_top()

    def tip_changed(self, tip) -> None:
        """The tip on the pipette, as last read. Another tip, or none, has
        another top; the same tip read again keeps it."""
        if _tip_key(tip) != _tip_key(self._tip):
            self._forget_top()
        self._tip = tip

    @property
    def top(self) -> float | None:
        """The measured top of Z with the tip that is on, or None."""
        with self._lock:
            return self._top

    @property
    def envelope(self) -> Limits:
        """The jog envelope: see "Two tops"."""
        return envelope_for(self._tip, self._modules())

    def refused(self, position) -> str:
        """Why the tip cannot be sent to `position`, or ""."""
        return refusal(self.envelope, position)

    # -- moves ----------------------------------------------------------------

    def retract(self, *, log=None) -> float:
        """The tip to the top, and the top measured. Returns it."""
        robot = self._need_robot()
        if log is not None:
            log("retracting leftZ")
        require_ok(robot.retract_axis("leftZ", verbose=False), "retract")
        top = xyz(robot)[2]
        with self._lock:
            self._top = top if self._top is None else max(top, self._top)
            return self._top

    def raise_tip(self, *, log=None) -> float:
        """Make sure the tip is at the travel height or above; return the
        top. Retracts only when the top is not known or the tip is below the
        travel height."""
        robot = self._need_robot()
        top = self.top
        if top is not None and xyz(robot)[2] >= _travel(top) - READ_TOL_MM:
            return top
        return self.retract(log=log)

    @property
    def travel_z(self) -> float | None:
        """The height a move across the deck is made at, or None while the
        top is not measured."""
        top = self.top
        return None if top is None else _travel(top)

    def reachable(self, pose) -> np.ndarray:
        """`pose`, no higher than the travel height once the top is known.
        A pose taught at the top of Z - a camera pose, saved after a
        retract - is out of bounds for a moveToCoordinates, and 1 mm under
        it is where it can be reached."""
        pose = np.array(pose, dtype=float)
        top = self.top
        if top is not None:
            pose[2] = min(float(pose[2]), _travel(top))
        return pose

    def camera_to(self, xy, *, log=None) -> None:
        """The tip up, then the gantry to `xy` at the travel height."""
        robot = self._need_robot()
        top = self.raise_tip(log=log)
        if log is not None:
            log(f"camera to ({xy[0]:.2f}, {xy[1]:.2f})")
        _straight(robot, xy, _travel(top))

    def tip_to(self, xy, z: float, *, log=None) -> None:
        """The tip up, across to `xy`, then down to `z`, or to the travel
        height if `z` is above it."""
        robot = self._need_robot()
        top = self.raise_tip(log=log)
        travel = _travel(top)
        z = min(float(z), travel)
        if log is not None:
            log(f"tip to ({xy[0]:.2f}, {xy[1]:.2f}) at z {z:g}")
        _straight(robot, xy, travel)
        if z < travel:
            _straight(robot, xy, z)

    def to_well(self, labware_id: str, well: str, level: str, offset, *,
                log=None) -> None:
        """The tip up, then into a well of a labware the robot session holds.

        `level` is the robot's own well origin - top, center or bottom - and
        `offset` (x, y, z) is from it. Not force_direct: going from the dish
        to a plate crosses other labware, and the robot's own path over the
        deck is the one that knows how tall each of them is.
        """
        if level not in WELL_LEVELS:
            raise ValueError(f"well level must be one of {WELL_LEVELS}, "
                             f"got {level!r}")
        robot = self._need_robot()
        self.raise_tip(log=log)
        if log is not None:
            log(f"tip to well {well} ({level} {offset[2]:+g} mm)")
        require_ok(robot.move_to_well(labware_id, well, well_location=level,
                                      offset=tuple(float(v) for v in offset),
                                      verbose=False), "move to well")

    # -- inside ---------------------------------------------------------------

    def _forget_top(self) -> None:
        with self._lock:
            self._top = None

    def _need_robot(self) -> Robot:
        robot = self._robot
        if robot is None:
            raise NoRobot("no robot connected")
        return robot


def _travel(top: float) -> float:
    return top - TRAVEL_BELOW_TOP_MM


def _straight(robot: Robot, xy, z: float) -> None:
    """One direct move, verified; see "Then one straight line"."""
    move_to(robot, (float(xy[0]), float(xy[1]), float(z)), min_z_height=1.0,
            force_direct=True)
