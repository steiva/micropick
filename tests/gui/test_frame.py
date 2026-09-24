"""to_qimage: geometry, stride, aliasing and the lifetime rule.

No QApplication anywhere in here. QImage is a QtGui value type and needs no
application instance, which is what keeps this runnable in CI with no display.
"""

import numpy as np
import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

from PySide6.QtGui import QImage                       # noqa: E402

from micropick.gui.widgets.frame import (SOURCE_ATTR,  # noqa: E402
                                         to_qimage)


def frame(height: int, width: int) -> np.ndarray:
    return np.arange(height * width * 3, dtype=np.uint8).reshape(height, width, 3)


@pytest.mark.parametrize("height,width", [(1, 1), (3, 4), (1944, 2592), (7, 13)])
def test_geometry_and_stride(height, width):
    image = to_qimage(frame(height, width))
    assert (image.width(), image.height()) == (width, height)
    assert image.format() is QImage.Format.Format_BGR888
    # BGR888 rows are three bytes per pixel with no padding, so an odd width is
    # the case that would expose an assumed four-byte alignment.
    assert image.bytesPerLine() == 3 * width


def test_stride_follows_the_array_not_the_width():
    """A contiguous slice of a wider buffer keeps the wider row pitch."""
    wide = np.zeros((4, 20, 3), np.uint8)
    rows = wide[1:3]                       # fewer rows, same pitch, still contiguous
    assert rows.flags["C_CONTIGUOUS"]
    image = to_qimage(rows)
    assert image.bytesPerLine() == rows.strides[0] == 60
    assert (image.width(), image.height()) == (20, 2)


def test_wraps_the_array_rather_than_copying():
    source = np.zeros((2, 2, 3), np.uint8)
    image = to_qimage(source)
    source[0, 0] = (1, 2, 3)               # B, G, R
    assert image.pixelColor(0, 0).getRgb()[:3] == (3, 2, 1)


def test_the_image_holds_its_source():
    source = frame(4, 4)
    image = to_qimage(source)
    assert getattr(image, SOURCE_ATTR) is source


def test_a_view_is_refused_with_the_remedy():
    view = frame(8, 10)[2:6, 3:7]
    assert not view.flags["C_CONTIGUOUS"]
    with pytest.raises(ValueError, match="ascontiguousarray"):
        to_qimage(view)
    to_qimage(np.ascontiguousarray(view))   # and this is the remedy


@pytest.mark.parametrize("bad,match", [
    (np.zeros((4, 4), np.uint8), "HxWx3"),
    (np.zeros((4, 4, 4), np.uint8), "HxWx3"),
    (np.zeros((4, 4, 3), np.uint16), "uint8"),
])
def test_refuses_what_it_cannot_represent(bad, match):
    with pytest.raises(ValueError, match=match):
        to_qimage(bad)
