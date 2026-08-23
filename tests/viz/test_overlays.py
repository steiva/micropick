"""The overlays: geometry, purity, and that the split changed no pixel."""

from __future__ import annotations

import cv2
import numpy as np
import pandas as pd
import pytest

from micropick.viz import overlays

from .scene import golden_path, load_frame, scene


def _square_contour(cx, cy, half=15):
    pts = [[cx - half, cy - half], [cx + half, cy - half],
           [cx + half, cy + half], [cx - half, cy + half]]
    return np.array(pts, dtype=np.int32).reshape(-1, 1, 2)


# -- purity ------------------------------------------------------------------

def test_annotate_draws_and_leaves_input_unchanged():
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    before = frame.copy()
    df = pd.DataFrame({"contour": [_square_contour(100, 100)],
                       "cX": [100.0], "cY": [100.0]})

    out = overlays.annotate(frame, cuboid_df=df, isolated=df,
                            floater_zones=[(150, 50, 20)],
                            circle_center=(100, 100), circle_radius=80,
                            status_lines=["state analyze", "target C3"])

    assert out.shape == frame.shape
    assert out.dtype == frame.dtype
    assert np.array_equal(frame, before)      # input untouched
    assert not np.array_equal(out, before)    # something was drawn


def test_annotate_no_data_is_a_clean_copy():
    frame = np.full((50, 60, 3), 7, dtype=np.uint8)
    out = overlays.annotate(frame)
    assert np.array_equal(out, frame)
    assert out is not frame                    # a copy, not the same array


def test_annotate_tolerates_empty_frames_table():
    frame = np.zeros((40, 40, 3), dtype=np.uint8)
    empty = pd.DataFrame(columns=["contour", "cX", "cY"])
    out = overlays.annotate(frame, cuboid_df=empty, pickable=empty,
                            isolated=empty)
    assert np.array_equal(out, frame)


# -- the split changed nothing ----------------------------------------------

def test_annotate_still_produces_the_pre_split_image():
    """Pinned to an image rendered by the implementation before `items` existed.

    Regenerate deliberately with `python -m tests.viz.regenerate_golden`, never
    because the test failed: a change here is a change to what an operator sees
    on a recorded clip.
    """
    golden = cv2.imread(str(golden_path()), cv2.IMREAD_COLOR)
    assert golden is not None, f"missing golden at {golden_path()}"

    out = overlays.annotate(load_frame(), **scene())
    assert out.shape == golden.shape
    assert np.array_equal(out, golden), (
        f"{int(np.count_nonzero(np.abs(out.astype(int) - golden.astype(int)).sum(2)))}"
        f" pixels differ from the pre-split rendering")


# -- items -------------------------------------------------------------------

def test_items_reads_no_pixels():
    """It takes a shape, not a frame. The status strip's width is the only
    thing in here that needs to know how big the picture is."""
    primitives = overlays.items((972, 1296, 3), status_lines=["a"])
    backdrop = primitives[0]
    assert isinstance(backdrop, overlays.Rect)
    assert backdrop.fill and backdrop.x1 == 1296 // 5


def test_items_counts_and_types_for_a_known_scene():
    parts = scene()
    primitives = overlays.items((972, 1296, 3), **parts)

    circles = [p for p in primitives if isinstance(p, overlays.Circle)]
    polylines = [p for p in primitives if isinstance(p, overlays.Polyline)]
    rects = [p for p in primitives if isinstance(p, overlays.Rect)]
    texts = [p for p in primitives if isinstance(p, overlays.Text)]
    assert len(circles) + len(polylines) + len(rects) + len(texts) == len(primitives)

    # one dish + one per floater zone + one per chosen verify circle
    assert len(circles) == 1 + len(parts["floater_zones"]) + len(parts["chosen"])
    # one per contour in each of the four tables
    assert len(polylines) == sum(len(parts[k]) for k in
                                 ("cuboid_df", "pickable", "isolated", "bubbles"))
    # one box per chosen cuboid, plus the status backdrop
    assert len(rects) == len(parts["chosen"]) + 1
    # "floater" beside each zone, plus one per status line
    assert len(texts) == len(parts["floater_zones"]) + len(parts["status_lines"])

    assert all(len(p.points.shape) == 2 and p.points.shape[1] == 2
               for p in polylines)


def test_items_order_is_the_drawing_order():
    """Order is load-bearing: the choice goes over the class colours, and the
    bubble magenta over both. A renderer must not sort."""
    primitives = overlays.items((972, 1296, 3), **scene())
    colours = [p.color for p in primitives]

    last_class = max(i for i, c in enumerate(colours)
                     if c in (overlays.ALL, overlays.PICKABLE, overlays.ISOLATED)
                     and isinstance(primitives[i], overlays.Polyline))
    first_bubble = min(i for i, c in enumerate(colours) if c == overlays.BUBBLE)
    first_chosen = min(i for i, p in enumerate(primitives)
                       if p.color == overlays.CHOSEN)
    assert first_bubble > last_class
    assert first_chosen > first_bubble


def test_items_of_nothing_is_nothing():
    assert overlays.items((100, 100, 3)) == []


# -- the renderer ------------------------------------------------------------

def test_draw_refuses_something_that_is_not_an_item():
    frame = np.zeros((10, 10, 3), np.uint8)
    with pytest.raises(TypeError, match="not a drawable overlay item"):
        overlays.draw(frame, [object()])


def test_draw_is_in_place_and_annotate_is_not():
    frame = np.zeros((60, 60, 3), np.uint8)
    same = overlays.draw(frame, overlays.items(frame.shape,
                                               circle_center=(30, 30),
                                               circle_radius=20))
    assert same is frame
    assert frame.any()
