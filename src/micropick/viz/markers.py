"""Drawing what the calibrations look at, as primitives.

Two targets, one module: the ArUco marker the camera sweep tracks, and the
crosshair disc the pipette offset is measured against and the check page
drives to. Both are things a detector found on a live frame and an operator
needs to see it found, and both are drawn by the same two renderers.

Geometry only, like `overlays.items`, and for the same reason: the camera
calibration shows this on a Qt widget and a notebook would draw it with cv2,
and where the outline goes must not be decided twice. Both renderers already
take `overlays.Item`, so this module is a handful of them and no drawing.

What it is for
--------------
Placing the marker under the camera is a physical act done by eye, and the
one thing the eye cannot check is the thing that matters: a marker lying
face down, or printed through the back of the paper, is **mirrored**, and a
mirrored marker is never detected — the dictionary has no entry for it. On
screen that looks exactly like bad lighting, a wrong dictionary, or a marker
just outside the frame. So the feed says which of those it is, and when the
marker is found it shows the four corners it was found by and which one is
the marker's own first corner, since that is what says which way up it is.

Corner order is ArUco's: index 0 is the marker's own top-left as printed,
then clockwise. Rotating the marker under the camera does not change its id
and does not change that order — the corners travel instead, so corner 0 is
where "up" went.
"""

from __future__ import annotations

import numpy as np

from .overlays import Circle, Item, Polyline, Text

__all__ = ["items", "crosshairs", "rotation_deg", "orientation", "FOUND",
           "FIRST_CORNER", "TOP_EDGE", "REFERENCE", "OUTSIDE", "CHOSEN"]

# BGR, as everything in viz is.
FOUND = (0, 255, 0)          # green: the outline it was detected by
TOP_EDGE = (0, 255, 255)     # yellow: the marker's own top edge
FIRST_CORNER = (255, 255, 255)   # white: corner 0, the marker's top-left
REFERENCE = (0, 0, 255)      # red: the pixel map's reference pixel
OUTSIDE = (120, 120, 120)    # grey: detected, but outside the fitted area
CHOSEN = (255, 255, 255)     # white: the one the operator picked, as in
                             # overlays.CHOSEN - white is "a decision", not
                             # "a class of thing", in both files

# The text is sized from the marker rather than from the frame: the frame is
# 2592 px wide and the widget is a third of that, so a fixed scale is either
# unreadable on screen or enormous in a recorded frame.
_TEXT_FRACTION = 1 / 6.0
_CV_FONT_PX = 22.0           # cv2's FONT_HERSHEY_SIMPLEX at scale 1.0


def rotation_deg(corners) -> float:
    """Clockwise angle of the marker's top edge, in image coordinates.

    0 is the marker as printed, +90 is a quarter turn clockwise on screen,
    180 is upside down. Positive is clockwise because v grows downwards.
    """
    c = np.asarray(corners, dtype=float).reshape(4, 2)
    dx, dy = c[1] - c[0]
    return float(np.degrees(np.arctan2(dy, dx)))


def orientation(corners) -> str:
    """The rotation in words, to the nearest quarter turn."""
    angle = rotation_deg(corners)
    quarter = int(round(angle / 90.0)) % 4
    return ("as printed", "a quarter turn clockwise", "upside down",
            "a quarter turn anticlockwise")[quarter]


def items(corners, marker_id: int | None = None, *,
          label: str | None = None) -> list[Item]:
    """Primitives for one detected marker, in frame pixels.

    `label` replaces the default caption, which is the id and the rotation.
    """
    c = np.asarray(corners, dtype=float).reshape(4, 2)
    side = float(max(np.linalg.norm(c[i] - c[(i + 1) % 4]) for i in range(4)))

    out: list[Item] = [
        Polyline(points=np.round(c).astype(np.int32), closed=True,
                 color=FOUND, thickness=2),
        # The top edge over the outline, so which way up reads without
        # having to find the white dot first.
        Polyline(points=np.round(c[:2]).astype(np.int32), closed=False,
                 color=TOP_EDGE, thickness=3),
        Circle(center=tuple(np.round(c[0]).astype(int)),
               radius=max(3, int(side / 14)), color=FIRST_CORNER,
               thickness=-1, fill=True),
    ]

    caption = label
    if caption is None:
        caption = (f"id {marker_id}" if marker_id is not None else "marker")
        caption += f"  ·  {orientation(c)}"
    text_px = max(12.0, side * _TEXT_FRACTION)
    top = c[np.argmin(c[:, 1])]
    out.append(Text(text=caption,
                    org=(int(round(c[:, 0].min())),
                         int(round(top[1] - text_px * 0.4))),
                    scale=text_px / _CV_FONT_PX, color=FOUND, thickness=2))
    return out


# ---------------------------------------------------------------------------
# the crosshair disc
# ---------------------------------------------------------------------------

def crosshairs(points, *, reference=None, chosen: int | None = None,
               radius_px: float = 14.0, outside=()) -> list[Item]:
    """The disc's crosshairs as the detector found them.

    `points` is (n, 2) pixels. `reference` is the pixel map's reference
    pixel, drawn as the red cross the notebook drew, because it is where
    the tip would land for a pose with no offset and the eye needs
    something fixed to judge against. `chosen` indexes the point the
    operator clicked, drawn white and larger - a decision, not a class of
    thing. `outside` are points the map does not cover, drawn faintly:
    they are detections, so leaving them out would look like a detector
    that missed them, and they cannot be driven to.
    """
    out: list[Item] = []
    if reference is not None:
        rx, ry = (int(round(v)) for v in reference)
        arm = int(round(radius_px * 2))
        out.append(Polyline(points=np.array([[rx - arm, ry], [rx + arm, ry]],
                                            np.int32),
                            closed=False, color=REFERENCE, thickness=2))
        out.append(Polyline(points=np.array([[rx, ry - arm], [rx, ry + arm]],
                                            np.int32),
                            closed=False, color=REFERENCE, thickness=2))

    for point in np.asarray(outside, dtype=float).reshape(-1, 2):
        out.append(Circle(center=tuple(np.round(point).astype(int)),
                          radius=int(radius_px), color=OUTSIDE, thickness=1))

    for index, point in enumerate(np.asarray(points, dtype=float).reshape(-1, 2)):
        picked = index == chosen
        out.append(Circle(center=tuple(np.round(point).astype(int)),
                          radius=int(radius_px * (1.6 if picked else 1.0)),
                          color=CHOSEN if picked else FOUND,
                          thickness=3 if picked else 2))
    return out
