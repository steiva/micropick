"""One window, sized by the picture in it.

Every window in this project used to be opened at 1348x1011 — 4:3, and just
short of a 1080p screen. Two of the four things shown are not 4:3: the lower
camera carries a view crop of 0.5, so its frame is square and arrived stretched
into a landscape window, and it was dragged back into shape by hand every time.

The aspect comes from the frame, never from configuration. A window is a view of
a picture whose shape the camera, the crop and the mode between them decide;
anything written down separately is a second opinion that goes stale.

The budget is what the window may occupy on the screen, and it is a budget
rather than a size because it bounds both directions: a wide frame fills it
horizontally, a tall one vertically, and neither is cropped or stretched to make
it fit. Nothing here queries the display, so it is a stated assumption about the
screen rather than a measurement.

`cv2` is imported inside the functions, as in `workflows/jog.py`: the module
stays importable where there is no GUI at all.
"""

from __future__ import annotations

__all__ = ["BUDGET", "fit_size", "FrameWindow"]

# 0.8 of 1080p, leaving room for the title bar and whatever else is on screen.
BUDGET = (1536, 864)


def fit_size(frame_shape, budget: tuple[int, int] = BUDGET) -> tuple[int, int]:
    """The largest window of the frame's own aspect that fits inside `budget`.

    Takes a shape rather than a frame so it can be called on a size that has no
    array behind it yet, and stays a pure function with nothing to mock.
    """
    h, w = (int(v) for v in frame_shape[:2])
    if w <= 0 or h <= 0:
        raise ValueError(f"frame shape {tuple(frame_shape)} has no area")
    scale = min(budget[0] / w, budget[1] / h)
    return max(1, round(w * scale)), max(1, round(h * scale))


class FrameWindow:
    """A named window that keeps the shape of what it is showing.

    Re-fitted whenever the frame's shape changes, not only when it is opened:
    the first read can fail and be stood in for by a placeholder, a crop can be
    applied part-way through, and a camera can be reopened at another mode. A
    window fitted once to whatever arrived first is the same hardcoded size with
    an extra step.
    """

    def __init__(self, name: str, *, budget: tuple[int, int] = BUDGET):
        self.name = name
        self.budget = budget
        self.size: tuple[int, int] | None = None
        self._shape: tuple[int, int] | None = None

    def fit(self, frame_shape) -> tuple[int, int]:
        """Open the window, or resize it, for a frame of this shape."""
        import cv2

        shape = tuple(int(v) for v in frame_shape[:2])
        if shape == self._shape:
            return self.size
        self.size = fit_size(shape, self.budget)
        if self._shape is None:
            cv2.namedWindow(self.name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self.name, *self.size)
        self._shape = shape
        return self.size

    def show(self, frame) -> None:
        import cv2

        self.fit(frame.shape)
        cv2.imshow(self.name, frame)

    def close(self) -> None:
        import cv2

        if self._shape is not None:
            cv2.destroyWindow(self.name)
            self._shape = None

    def __enter__(self) -> "FrameWindow":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
