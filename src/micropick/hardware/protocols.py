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

__all__ = ["Camera", "Robot", "xyz"]


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
