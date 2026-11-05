"""Undo and Redo of the points: the last moves, in memory (#96).

A move is any change of the memo or saved points, wherever it was made: the
points are the session's, and a list that skipped a move made on another
page would undo around it. What is kept of a move is every point as it was
(`Session._points_now`), not what was done, so a move backwards is the same
for an add, a delete, a rename and a Save, and a saved point brought back is
as it was, in its place in the list.

This keeps the states and knows nothing of what is in them; `Session` takes
them and puts them back.
"""

from __future__ import annotations

from collections import deque

__all__ = ["DEPTH", "PointHistory"]

# How many moves can be undone.
DEPTH = 3


class PointHistory:
    def __init__(self) -> None:
        self._back: deque = deque(maxlen=DEPTH)
        self._forward: list = []

    @property
    def can_undo(self) -> bool:
        return bool(self._back)

    @property
    def can_redo(self) -> bool:
        return bool(self._forward)

    def record(self, before) -> None:
        """A move was made from `before`: what was undone stays undone."""
        self._back.append(before)
        self._forward.clear()

    def undo(self, now):
        """The state to go back to; `now` is what Redo comes back to."""
        before = self._back.pop()
        self._forward.append(now)
        return before

    def redo(self, now):
        """The state to go forward to; `now` is what Undo comes back to."""
        after = self._forward.pop()
        self._back.append(now)
        return after

    def clear(self) -> None:
        self._back.clear()
        self._forward.clear()
