"""Interfaces the rest of the package programs against.

These are typing.Protocol, so nothing has to inherit from them: any object with
matching methods satisfies the type. That is what lets a workflow take either
the real robot or a mock without either knowing about the other.

Method names deliberately mirror the Opentrons wrapper rather than inventing
tidier ones. A translation layer would mean the notebook and the workflows
speak different dialects of the same API, and every reader would have to hold
both in mind. There is one robot and it is not changing, so matching it is
cheaper than abstracting it.

The bodies are `...`, the Ellipsis object used as a placeholder. The protocol
exists for its signatures; nothing here ever runs.
"""

from __future__ import annotations

from typing import Any, Protocol, Sequence, runtime_checkable

import numpy as np

__all__ = ["Camera", "Robot", "xyz", "move_to", "move_relative", "goto_xy",
           "MoveFailed", "preflight"]


@runtime_checkable
class Camera(Protocol):
    """A source of frames that stays open.

    read() returns the most recent frame, which may predate whatever the caller
    just did. read_after() is the one to use when a frame has to reflect
    something that has already happened, such as a completed move.
    """

    def read(self) -> tuple[bool, np.ndarray | None]:
        ...

    def read_after(self, t: float, *, skip: int = 0,
                   timeout: float = 2.0) -> np.ndarray:
        ...

    @property
    def resolution(self) -> tuple[int, int]:
        ...

    def close(self) -> None:
        ...


@runtime_checkable
class Robot(Protocol):
    """The part of the Opentrons wrapper the workflows use.

    get_position reports the pose actually reached, not the one commanded, so
    positioning error never enters a calibration fit.
    """

    def move_to_coordinates(self, coordinates: Sequence[float],
                            min_z_height: float | None = None,
                            verbose: bool = True) -> Any:
        ...

    def get_position(self, verbose: bool = True
                     ) -> tuple[dict[str, float], object]:
        """Returns (coordinates, response). The coordinates are a dict of x, y
        and z, hence the [0] everywhere; the second element is the raw HTTP
        response and is not used here."""
        ...


def xyz(robot: Robot) -> tuple[float, float, float]:
    """Current pose as a tuple, read by key.

    The usual `robot.get_position(verbose=False)[0].values()` depends on the
    order the wrapper happens to build its dictionary. If a future version returns
    the keys in another order, that idiom silently swaps axes and every
    coordinate afterwards is wrong with nothing to show for it. Reading by name
    costs nothing and cannot do that.
    """
    p = robot.get_position(verbose=False)[0]
    return float(p["x"]), float(p["y"]), float(p["z"])


class MoveFailed(RuntimeError):
    """The robot did not end up where it was told to go.

    The wrapper only raises on an HTTP error, but the robot answers 201 to a
    command it then declines to execute, with the reason buried in the body. So
    a refused move is silent, and the first visible symptom appears much later
    and looks like something else entirely. Every move here is therefore
    verified against the pose read back afterwards.

    The refusal seen in practice: with a long tip fitted, the robot accepts
    moveToCoordinates at the height the tip already sits at and does nothing,
    while relative moves at the same height work normally. Anything that only
    needs to travel in XY should use goto_xy for that reason.
    """


def _fmt(point) -> str:
    return "(" + ", ".join(f"{float(v):.2f}" for v in point) + ")"


def _command_status(response) -> tuple[str | None, dict | None]:
    """Pull status and error out of a command response, tolerating anything."""
    try:
        import json
        body = json.loads(response.text)["data"]
        return body.get("status"), body.get("error")
    except Exception:
        return None, None


def move_to(robot: Robot, coordinates, *, min_z_height: float | None = None,
            tolerance_mm: float = 0.1, verbose: bool = False):
    """Absolute move, verified. Returns the pose actually reached."""
    target = np.asarray(coordinates, dtype=float)
    kwargs = {"verbose": verbose}
    if min_z_height is not None:
        kwargs["min_z_height"] = min_z_height
    response = robot.move_to_coordinates(tuple(target), **kwargs)

    status, error = _command_status(response)
    reached = np.array(xyz(robot))
    gap = float(np.linalg.norm(reached - target))
    if status == "failed" or gap > tolerance_mm:
        detail = ""
        if error:
            detail = f"\n  robot said: {str(error)[:300]}"
        raise MoveFailed(
            f"asked for {_fmt(target)}, ended at {_fmt(reached)} "
            f"({gap:.2f} mm off{', status ' + status if status else ''})."
            f"{detail}"
        )
    return reached


def move_relative(robot: Robot, axis: str, distance: float, *,
                  tolerance_mm: float = 0.1, verbose: bool = False):
    """Relative move, verified. Returns the pose actually reached."""
    before = np.array(xyz(robot))
    robot.move_relative(axis, distance, verbose=verbose)
    reached = np.array(xyz(robot))
    moved = reached["xyz".index(axis)] - before["xyz".index(axis)]
    if abs(moved - distance) > tolerance_mm:
        raise MoveFailed(
            f"asked to move {distance:+.3f} mm in {axis}, moved {moved:+.3f} mm "
            f"(from {_fmt(before)} to {_fmt(reached)})"
        )
    return reached


def goto_xy(robot: Robot, x: float, y: float, *, tolerance_mm: float = 0.1,
            skip_below_mm: float = 0.002, verbose: bool = False):
    """Travel to an XY position without naming a Z.

    The camera sits at a fixed height, so a calibration sweep is pure XY
    motion. Expressing it as relative moves says exactly that, and avoids
    supplying a Z the caller does not care about, which is what the robot
    refuses to act on when a long tip is fitted.

    The step for each axis is computed from the pose read back, not from the
    plan, so a short move is absorbed by the next one instead of shifting
    everything after it. Axes already in place are skipped, which halves the
    traffic on a raster where only one axis changes at a time.
    """
    target = (float(x), float(y))
    for axis, want in zip("xy", target):
        current = xyz(robot)["xy".index(axis)]
        delta = want - current
        if abs(delta) < skip_below_mm:
            continue
        move_relative(robot, axis, delta, tolerance_mm=tolerance_mm,
                      verbose=verbose)
    reached = xyz(robot)
    gap = float(np.hypot(reached[0] - target[0], reached[1] - target[1]))
    if gap > tolerance_mm:
        raise MoveFailed(f"asked for XY {_fmt(target)}, ended at "
                         f"{_fmt(reached[:2])} ({gap:.2f} mm off)")
    return reached


def preflight(robot: Robot, *, probe_mm: float = 0.5, log=print) -> None:
    """Check the robot moves at all, before a routine depends on it.

    Costs one small move and turns a refusal into an immediate, explicit error
    instead of a puzzling failure several steps later. Uses a relative move,
    which is what the routines themselves use.
    """
    start = xyz(robot)
    try:
        move_relative(robot, "x", probe_mm)
        move_relative(robot, "x", -probe_mm)
    except MoveFailed as exc:
        raise MoveFailed(
            f"the robot will not move from {_fmt(start)}.\n{exc}\n"
            f"  Check that the run and the pipette are loaded, and that the "
            f"robot has been homed since power on."
        ) from None
    log(f"  robot responds at {_fmt(start)}")
