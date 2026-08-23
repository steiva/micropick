"""A camera frame as a QImage, without paying for it twice.

One function, and the whole point is what it does *not* do. A 4000x3000 BGR
frame is 36 MB. `cvtColor` to RGB is a second 36 MB and a full pass over the
image, thirty times a second, for a channel order Qt can read directly:
`Format_BGR888` is exactly what OpenCV already has in memory. So the QImage is
built over the array in place and nothing is converted and nothing is copied.

What that costs is a lifetime rule
----------------------------------
The QImage points into the numpy array's memory. If the array is freed while
the QImage is still alive, the QImage is left pointing at released memory —
that is a segfault, not an exception, and it happens at paint time, far from
the line that dropped the array.

`to_qimage` therefore attaches the array to the QImage it returns
(`SOURCE_ATTR`), so holding the image holds the buffer and there is nothing for
a caller to remember. PySide6 6.11 also keeps a reference of its own, which was
measured rather than assumed; it is undocumented and has moved between
versions, so this does not lean on it.

A non-contiguous array — which is what a centre crop is, being a slice — is
refused by PySide6 with a plain ValueError rather than accepted and misread, so
that failure needs no help. It is re-raised here with the fix in the message,
because the caller is the one that knows whether the copy is affordable.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtGui import QImage

__all__ = ["to_qimage", "SOURCE_ATTR"]

# The attribute the source array is parked on. Named rather than inlined so a
# reader who finds it on a QImage can search for it and find this file.
SOURCE_ATTR = "_micropick_source"


def to_qimage(bgr: np.ndarray) -> QImage:
    """Wrap a contiguous HxWx3 uint8 BGR array. No conversion, no copy.

    The returned image keeps the array alive; drop the image and both go.
    """
    if bgr.ndim != 3 or bgr.shape[2] != 3:
        raise ValueError(
            f"expected an HxWx3 BGR frame, got shape {bgr.shape}. Greyscale "
            f"has no Format_BGR888 equivalent; convert it first.")
    if bgr.dtype != np.uint8:
        raise ValueError(
            f"expected uint8, got {bgr.dtype}. Format_BGR888 is one byte per "
            f"channel and a wider dtype would be read as several pixels.")
    if not bgr.flags["C_CONTIGUOUS"]:
        raise ValueError(
            "expected a C-contiguous array; this one is a view, which is what "
            "a centre crop returns. Wrap it in np.ascontiguousarray at the "
            "call site, where it is known whether that copy is affordable.")

    height, width = bgr.shape[:2]
    # strides[0], not 3 * width: the source may be a contiguous slice of a
    # wider buffer, and Qt is told the row pitch rather than made to assume it.
    image = QImage(bgr, width, height, bgr.strides[0],
                   QImage.Format.Format_BGR888)
    setattr(image, SOURCE_ATTR, bgr)
    return image
