"""Drawing for the picking window: frame in, frame out.

Pure functions. They take a frame and some data and return a new annotated
frame; they open no window, read no key and touch no hardware. That is what lets
the picking session stay headless (DESIGN sections 2 and 11): the session
computes the detection tables, the notebook or a future GUI decides when and
where to draw, and this module is the only thing that knows how.

Ported from the old `draw_annotations`, minus its one bad habit: the old version
called `routine.get_next()` while drawing, so rendering a frame advanced the
routine. Nothing here changes any state, including the frame passed in — every
function works on a copy.

Colours follow the old convention (BGR): red for the dish and floaters, red for
every detection, yellow for the pickable subset, green for the isolated ones
that are actually eligible to pick. White is added for the two things that are
not a class of object but a decision: the cuboids chosen for this pickup, and
the circle around each of them inside which `verify_pickup` looks for a
detection to decide whether the cuboid actually left.
"""

from __future__ import annotations

import cv2
import numpy as np

__all__ = ["annotate", "draw_dish", "draw_contours", "draw_floaters",
           "draw_chosen", "draw_verify_zones", "draw_status"]

_ALL = (0, 0, 255)          # red
_PICKABLE = (0, 255, 255)   # yellow
_ISOLATED = (0, 255, 0)     # green
_FLOATER = (0, 0, 255)      # red
_CHOSEN = (255, 255, 255)   # white
_FONT = cv2.FONT_HERSHEY_SIMPLEX


def _contours(df):
    """The contour column as a list, or empty if the frame has none."""
    if df is None or len(df) == 0 or "contour" not in df:
        return []
    return list(df["contour"].values)


def draw_dish(frame, center, radius, color=_ALL, thickness=2):
    if center is None or radius is None:
        return frame
    cv2.circle(frame, (int(center[0]), int(center[1])), int(radius),
               color, thickness)
    return frame


def draw_contours(frame, df, color, thickness=2):
    contours = _contours(df)
    if contours:
        cv2.drawContours(frame, contours, -1, color, thickness)
    return frame


def draw_floaters(frame, zones, radius, color=_FLOATER):
    for zx, zy in zones or ():
        cv2.circle(frame, (int(zx), int(zy)), int(radius), color, 2)
        cv2.putText(frame, "floater", (int(zx) + 8, int(zy) - 8),
                    _FONT, 0.6, color, 2)
    return frame


def draw_chosen(frame, df, color=_CHOSEN, thickness=2, pad=4):
    """A box around each cuboid picked out for this pickup.

    The detection table carries contours rather than boxes (`build_cuboid_df`),
    so the rectangle is the contour's bounding box widened by `pad`: drawn on
    the contour itself the two lines sit on top of each other and the choice is
    hard to see against the green.
    """
    if df is None or len(df) == 0:
        return frame
    for cnt in _contours(df):
        x, y, w, h = cv2.boundingRect(cnt)
        cv2.rectangle(frame, (x - pad, y - pad), (x + w + pad, y + h + pad),
                      color, thickness)
    return frame


def draw_verify_zones(frame, df, radius_px, color=_CHOSEN, thickness=2):
    """The circle `verify_pickup` inspects around each chosen position.

    A detection left anywhere inside it counts the pickup as a miss, so this is
    the decision itself made visible. Thin, to read as a tolerance rather than
    as another object.
    """
    if df is None or len(df) == 0 or radius_px is None:
        return frame
    for cx, cy in df[["cX", "cY"]].values:
        cv2.circle(frame, (int(round(cx)), int(round(cy))),
                   max(1, int(round(radius_px))), color, thickness)
    return frame


def draw_status(frame, lines, color=_ISOLATED, org=(10, 40), line_h=40):
    """A text panel on a dark strip, so it stays readable over the dish."""
    lines = list(lines or ())
    if not lines:
        return frame
    x, y0 = org
    width = frame.shape[1] // 5
    cv2.rectangle(frame, (0, 0), (width, y0 + line_h * len(lines)),
                  (0, 0, 0), -1)
    for i, text in enumerate(lines):
        cv2.putText(frame, str(text), (x, y0 + i * line_h), _FONT, 1.0,
                    color, 2)
    return frame


def annotate(frame, *, cuboid_df=None, pickable=None, isolated=None,
             chosen=None, verify_radius=None, floater_zones=(),
             floater_radius=75, circle_center=None, circle_radius=None,
             status_lines=()) -> np.ndarray:
    """Return a copy of `frame` with the picking state drawn on it.

    Every argument is optional, so this works before the first detection as well
    as during a run. The input frame is never modified.

    `chosen` is the batch the session picked out (`PickingSession.choice`) and
    `verify_radius` its check radius in pixels (`verify_radius_px`); taking the
    radius from the session rather than recomputing it here is what keeps the
    drawn tolerance and the decided one the same number.
    """
    vis = frame.copy()
    if circle_center is not None and circle_radius is not None:
        draw_dish(vis, circle_center, circle_radius)
    draw_contours(vis, cuboid_df, _ALL)
    draw_contours(vis, pickable, _PICKABLE)
    draw_contours(vis, isolated, _ISOLATED)
    draw_floaters(vis, floater_zones, floater_radius)
    # the choice last: it is the decision, and it has to stay readable over the
    # class colours it sits on top of
    draw_verify_zones(vis, chosen, verify_radius)
    draw_chosen(vis, chosen)
    draw_status(vis, status_lines)
    return vis
