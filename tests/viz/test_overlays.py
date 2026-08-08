"""The overlay drawing is pure: it returns a new frame and never mutates input."""
from __future__ import annotations

import numpy as np
import pandas as pd

from micropick.viz import overlays


def _square_contour(cx, cy, half=15):
    pts = [[cx - half, cy - half], [cx + half, cy - half],
           [cx + half, cy + half], [cx - half, cy + half]]
    return np.array(pts, dtype=np.int32).reshape(-1, 1, 2)


def test_annotate_draws_and_leaves_input_unchanged():
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    before = frame.copy()
    df = pd.DataFrame({"contour": [_square_contour(100, 100)],
                       "cX": [100.0], "cY": [100.0]})

    out = overlays.annotate(frame, cuboid_df=df, isolated=df,
                            floater_zones=[(150, 50)], floater_radius=20,
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
