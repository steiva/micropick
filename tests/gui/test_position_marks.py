"""Saved positions drawn on the picture: where, and only when they can be."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from micropick.config.schema import PipetteOffset                 # noqa: E402
from micropick.config.schema import PixelMap as PixelMapConfig    # noqa: E402
from micropick.core.calibration.pixel_map import PixelMap         # noqa: E402
from micropick.gui.position_marks import mark_items               # noqa: E402
from micropick.viz import overlays                                # noqa: E402

W, H, K = 900, 700, 0.05                # mm per pixel of a toy linear map


def _map() -> PixelMap:
    s = W / 2
    return PixelMap(PixelMapConfig(
        degree=1, cu=W / 2, cv=H / 2, s=s,
        coef=[[0.0, K * s], [K * s, 0.0]], zero=[0.0, 0.0],
        ref=[W / 2, H / 2], bounds=[0.0, 0.0, float(W), float(H)],
        image_size=[W, H], sweep_z=0.0))


def _centres(items):
    return [i.center for i in items
            if isinstance(i, overlays.Circle) and not i.fill]


def test_a_position_is_drawn_where_the_tip_would_go_down():
    offset = PipetteOffset(dx=2.0, dy=-1.0)
    # The tip pose (102, 99) is over the deck point (100, 100): under the
    # reference pixel while the gantry is at (100, 100), and 10 mm = 200 px
    # to the right of it once the gantry has moved 10 mm the other way.
    items = mark_items({"a": (102.0, 99.0, 30.0)}, _map(), offset,
                       (100.0, 100.0), (W, H))
    assert _centres(items) == [(450, 350)]
    items = mark_items({"a": (102.0, 99.0, 30.0)}, _map(), offset,
                       (90.0, 100.0), (W, H))
    assert _centres(items) == [(650, 350)]
    assert any(isinstance(i, overlays.Text) and i.text == "a" for i in items)


def test_a_position_outside_the_frame_is_not_drawn():
    items = mark_items({"far": (500.0, 500.0, 0.0)}, _map(),
                       PipetteOffset(dx=0.0, dy=0.0), (100.0, 100.0), (W, H))
    assert items == []
