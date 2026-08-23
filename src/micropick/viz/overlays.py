"""Drawing for the picking window: geometry first, then a renderer.

Split in two, and the split is the point.

`items(...)` turns the picking state into a list of primitives — circles,
rectangles, polylines, text — in **frame coordinates**, touching no image at
all. `draw(frame, items)` puts them into a numpy array with cv2. `annotate()`
is the two composed, and keeps the signature it always had.

Why it is split
---------------
A GUI cannot use the cv2 half. Drawing into a 4000x3000 frame every 33 ms is
not a thing that runs, and the display already scales the frame down before it
is shown, so the natural place for an overlay there is a QPainter pass over the
widget in widget coordinates. But the *geometry* — which contour is which
colour, where the verify circle goes, how wide the status panel is — must not
exist twice. Written twice it agrees for a month; `workflows/jog` has the scar.

So the geometry is computed once, here, and there are two renderers: this
module's `draw`, and the GUI's QPainter pass, which maps the same primitives
through the frame-to-widget transform the camera view already keeps.

Pure, as before. Nothing here changes any state, including the frame passed in
— `annotate` works on a copy. The old `draw_annotations` called
`routine.get_next()` while drawing, so rendering a frame advanced the routine.

Colours are **BGR**, named as such, because that is what cv2 wants and what
every constant in this file has always been. A renderer that needs RGB reverses
them at the one place it draws; getting that backwards silently is the failure
this spells out rather than leaving to a comment.

Colour convention (unchanged): red for the dish and floaters, red for every
detection, yellow for the pickable subset, green for the isolated ones that are
actually eligible to pick. White for the two things that are not a class of
object but a decision: the cuboids chosen for this pickup, and the circle around
each of them inside which `verify_pickup` looks for a detection to decide
whether the cuboid actually left.

Magenta is its own colour for objects the bubble filter recognised, rather than
red, which already means every detection: the filter's thresholds rest on eight
crops with 13% of margin on one of the two features, so an operator has to be
able to see at a glance whether it is discarding microtissues. A dish with no
magenta on it and a dish whose every cuboid the filter ate must not look the
same. It is emitted after the class colours for the same reason — with the
filter switched off a recognised bubble is still in `pickable` and `isolated`,
and the recognition is the thing worth seeing.

**Order is load-bearing.** `items` returns them in the order they must be drawn
and a renderer must not sort them: the choice goes on top of the class colours
it sits over, and magenta on top of both.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

__all__ = ["annotate", "items", "draw", "Item", "Circle", "Rect", "Polyline",
           "Text", "draw_dish", "draw_contours", "draw_floaters",
           "draw_chosen", "draw_verify_zones", "draw_status",
           "ALL", "PICKABLE", "ISOLATED", "FLOATER", "BUBBLE", "CHOSEN",
           "STATUS_BACKDROP", "FONT"]

# BGR, throughout.
ALL = (0, 0, 255)            # red
PICKABLE = (0, 255, 255)     # yellow
ISOLATED = (0, 255, 0)       # green
FLOATER = (0, 0, 255)        # red
BUBBLE = (255, 0, 255)       # magenta
CHOSEN = (255, 255, 255)     # white
STATUS_BACKDROP = (0, 0, 0)  # black

FONT = cv2.FONT_HERSHEY_SIMPLEX

# Kept under the old private names so nothing that imported them breaks.
_ALL, _PICKABLE, _ISOLATED = ALL, PICKABLE, ISOLATED
_FLOATER, _BUBBLE, _CHOSEN = FLOATER, BUBBLE, CHOSEN
_FONT = FONT


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class Item:
    """One thing to draw, in frame pixels.

    Coordinates are already integral: the rounding is geometry and belongs with
    the geometry, so both renderers place a shape on the same pixel rather than
    each rounding in its own way.
    """

    color: tuple[int, int, int]          # BGR
    thickness: int = 2
    fill: bool = False


@dataclass(frozen=True, kw_only=True)
class Circle(Item):
    center: tuple[int, int]
    radius: int


@dataclass(frozen=True, kw_only=True)
class Rect(Item):
    x0: int
    y0: int
    x1: int
    y1: int


@dataclass(frozen=True, kw_only=True)
class Polyline(Item):
    """A contour, as one shape.

    Not a list of line segments: `cv2.drawContours` draws a closed polygon and
    QPainter draws a `QPolygonF`, so splitting a contour into its fifty-odd
    edges would multiply the item count per cuboid by that much and throw away
    the one thing both renderers already know how to do.
    """

    points: np.ndarray = field(repr=False)   # (n, 2) int32
    closed: bool = True


@dataclass(frozen=True, kw_only=True)
class Text(Item):
    text: str
    org: tuple[int, int]                 # baseline-left, as cv2 and Qt agree
    scale: float = 1.0                   # cv2 font scale; a GUI maps it to pt


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------

def _contours(df):
    """The contour column as a list, or empty if the frame has none."""
    if df is None or len(df) == 0 or "contour" not in df:
        return []
    return list(df["contour"].values)


def _dish_items(center, radius, color=ALL, thickness=2) -> list[Item]:
    if center is None or radius is None:
        return []
    return [Circle(center=(int(center[0]), int(center[1])),
                   radius=int(radius), color=color, thickness=thickness)]


def _contour_items(df, color, thickness=2) -> list[Item]:
    return [Polyline(points=np.asarray(cnt).reshape(-1, 2),
                     color=color, thickness=thickness)
            for cnt in _contours(df)]


def _floater_items(zones, color=FLOATER) -> list[Item]:
    """Each zone is (x, y, radius_px) and is drawn at its own size.

    The radius is per zone because it is measured, not configured: it is how far
    that floater could drift before the next reading, from the speed it was seen
    to have. Drawing them all at one size would hide the difference between a
    drifter and one trembling in place.
    """
    out: list[Item] = []
    for zx, zy, radius_px in zones or ():
        out.append(Circle(center=(int(zx), int(zy)), radius=int(radius_px),
                          color=color, thickness=2))
        out.append(Text(text="floater", org=(int(zx) + 8, int(zy) - 8),
                        scale=0.6, color=color, thickness=2))
    return out


def _chosen_items(df, color=CHOSEN, thickness=2, pad=4) -> list[Item]:
    """A box around each cuboid picked out for this pickup.

    The detection table carries contours rather than boxes (`build_cuboid_df`),
    so the rectangle is the contour's bounding box widened by `pad`: drawn on
    the contour itself the two lines sit on top of each other and the choice is
    hard to see against the green.
    """
    out: list[Item] = []
    for cnt in _contours(df):
        x, y, w, h = cv2.boundingRect(np.asarray(cnt).reshape(-1, 1, 2))
        out.append(Rect(x0=x - pad, y0=y - pad, x1=x + w + pad, y1=y + h + pad,
                        color=color, thickness=thickness))
    return out


def _verify_items(df, radius_px, color=CHOSEN, thickness=2) -> list[Item]:
    """The circle `verify_pickup` inspects around each chosen position.

    A detection left anywhere inside it counts the pickup as a miss, so this is
    the decision itself made visible. Thin, to read as a tolerance rather than
    as another object.
    """
    if df is None or len(df) == 0 or radius_px is None:
        return []
    return [Circle(center=(int(round(cx)), int(round(cy))),
                   radius=max(1, int(round(radius_px))),
                   color=color, thickness=thickness)
            for cx, cy in df[["cX", "cY"]].values]


def _status_items(frame_shape, lines, color=ISOLATED, org=(10, 40),
                  line_h=40) -> list[Item]:
    """A text panel on a dark strip, so it stays readable over the dish.

    Takes the frame's shape rather than the frame: the strip is a fifth of the
    frame's width, and that is the only reason any of this needs to know how big
    the picture is.
    """
    lines = list(lines or ())
    if not lines:
        return []
    x, y0 = org
    width = frame_shape[1] // 5
    out: list[Item] = [Rect(x0=0, y0=0, x1=width, y1=y0 + line_h * len(lines),
                            color=STATUS_BACKDROP, thickness=-1, fill=True)]
    for i, text in enumerate(lines):
        out.append(Text(text=str(text), org=(x, y0 + i * line_h), scale=1.0,
                        color=color, thickness=2))
    return out


def items(frame_shape, *, cuboid_df=None, pickable=None, isolated=None,
          bubbles=None, chosen=None, verify_radius=None, floater_zones=(),
          circle_center=None, circle_radius=None,
          status_lines=()) -> list[Item]:
    """The picking state as primitives, in frame coordinates.

    Same arguments as `annotate` except that the frame is replaced by its
    shape, since nothing here reads a pixel. Every argument is optional, so this
    works before the first detection as well as during a run.

    The returned order is the order they must be drawn in; see the module note.
    """
    out: list[Item] = []
    out += _dish_items(circle_center, circle_radius)
    out += _contour_items(cuboid_df, ALL)
    out += _contour_items(pickable, PICKABLE)
    out += _contour_items(isolated, ISOLATED)
    # after the class colours, because with the filter off a recognised bubble is
    # still pickable and would be painted over by them
    out += _contour_items(bubbles, BUBBLE)
    out += _floater_items(floater_zones)
    # the choice last: it is the decision, and it has to stay readable over the
    # class colours it sits on top of
    out += _verify_items(chosen, verify_radius)
    out += _chosen_items(chosen)
    out += _status_items(frame_shape, status_lines)
    return out


# ---------------------------------------------------------------------------
# the cv2 renderer
# ---------------------------------------------------------------------------

def draw(frame: np.ndarray, primitives) -> np.ndarray:
    """Draw primitives into `frame`, in place, and return it.

    In place because the one caller that must not mutate its input copies
    first; a second copy per rendered frame is 36 MB at the lower camera's
    resolution.
    """
    for item in primitives:
        if isinstance(item, Circle):
            cv2.circle(frame, item.center, item.radius, item.color,
                       -1 if item.fill else item.thickness)
        elif isinstance(item, Rect):
            cv2.rectangle(frame, (item.x0, item.y0), (item.x1, item.y1),
                          item.color, -1 if item.fill else item.thickness)
        elif isinstance(item, Polyline):
            # drawContours, not polylines: this is the call the picking window
            # has always used, and its joins and end caps are what the recorded
            # clips were reviewed against.
            cv2.drawContours(frame, [item.points.reshape(-1, 1, 2)], -1,
                             item.color, item.thickness)
        elif isinstance(item, Text):
            cv2.putText(frame, item.text, item.org, FONT, item.scale,
                        item.color, item.thickness)
        else:
            raise TypeError(f"not a drawable overlay item: {item!r}")
    return frame


def annotate(frame, *, cuboid_df=None, pickable=None, isolated=None,
             bubbles=None, chosen=None, verify_radius=None, floater_zones=(),
             circle_center=None, circle_radius=None,
             status_lines=()) -> np.ndarray:
    """Return a copy of `frame` with the picking state drawn on it.

    Every argument is optional, so this works before the first detection as well
    as during a run. The input frame is never modified.

    `chosen` is the batch the session picked out (`PickingSession.choice`) and
    `verify_radius` its check radius in pixels (`verify_radius_px`); taking the
    radius from the session rather than recomputing it here is what keeps the
    drawn tolerance and the decided one the same number.

    `bubbles` is what the bubble filter recognised (`PickingSession.bubbles`),
    which is not the same set as what it rejected: a bubble that also drifts is
    labelled a floater, and with the filter off nothing is rejected at all. What
    the operator needs on the dish is that the object was recognised.
    """
    return draw(frame.copy(),
                items(frame.shape, cuboid_df=cuboid_df, pickable=pickable,
                      isolated=isolated, bubbles=bubbles, chosen=chosen,
                      verify_radius=verify_radius,
                      floater_zones=floater_zones,
                      circle_center=circle_center,
                      circle_radius=circle_radius,
                      status_lines=status_lines))


# ---------------------------------------------------------------------------
# the individual drawing helpers, kept
# ---------------------------------------------------------------------------
# Public since the port and cheap to keep. They go through the same primitives,
# so there is still exactly one description of where each shape goes.

def draw_dish(frame, center, radius, color=ALL, thickness=2):
    return draw(frame, _dish_items(center, radius, color, thickness))


def draw_contours(frame, df, color, thickness=2):
    return draw(frame, _contour_items(df, color, thickness))


def draw_floaters(frame, zones, color=FLOATER):
    return draw(frame, _floater_items(zones, color))


def draw_chosen(frame, df, color=CHOSEN, thickness=2, pad=4):
    return draw(frame, _chosen_items(df, color, thickness, pad))


def draw_verify_zones(frame, df, radius_px, color=CHOSEN, thickness=2):
    return draw(frame, _verify_items(df, radius_px, color, thickness))


def draw_status(frame, lines, color=ISOLATED, org=(10, 40), line_h=40):
    return draw(frame, _status_items(frame.shape, lines, color, org, line_h))
