"""The second renderer for `viz.overlays` primitives: QPainter, in the widget.

`viz.overlays.draw` puts them into a numpy array with cv2. This puts the same
list onto a widget, through the frame-to-widget transform the camera view
already keeps. Nothing about the geometry is decided here — that is the point of
`overlays.items`, and a shape drawn a pixel from where cv2 would put it is the
beginning of two conventions again.

Two things are converted rather than carried:

**Colour.** `overlays` is BGR throughout, because cv2 is. Qt is RGB. The swap
happens on this line and nowhere else; getting it backwards is silent, and
turns the yellow "pickable" and the magenta "bubble" into each other's
neighbours.

**Pen width and text height.** Those stay in *device* pixels rather than being
scaled with the picture. A 4000-pixel-wide frame in a 1300-pixel widget is
scaled by about 0.33, and a two-pixel contour drawn through that transform is
two thirds of a pixel — invisible exactly when the operator is looking hardest.
Coordinates are therefore mapped by hand and the pen is set afterwards.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF

from ...viz import overlays

__all__ = ["paint", "MIN_TEXT_PX"]

# cv2's FONT_HERSHEY_SIMPLEX at fontScale 1.0 is about this tall in pixels, so
# an item's `scale` maps to a pixel height through it.
CV_FONT_PX = 22.0

# Below this a label is not a label. A scaled-down frame would otherwise render
# the floater tags at two pixels high.
MIN_TEXT_PX = 9.0


def _colour(bgr) -> QColor:
    blue, green, red = bgr
    return QColor(int(red), int(green), int(blue))


def paint(painter: QPainter, primitives, transform) -> None:
    """Draw `primitives` (frame coordinates) through `transform` onto a widget.

    `transform` is a QTransform mapping sensor pixels to widget pixels — what
    `CameraView.transform` returns, crop included.
    """
    scale = abs(transform.m11()) or 1.0
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    for item in primitives:
        colour = _colour(item.color)
        pen = QPen(colour, max(1, item.thickness))
        painter.setPen(pen)
        painter.setBrush(QBrush(colour) if item.fill
                         else Qt.BrushStyle.NoBrush)

        if isinstance(item, overlays.Circle):
            centre = transform.map(QPointF(*item.center))
            radius = item.radius * scale
            painter.drawEllipse(centre, radius, radius)

        elif isinstance(item, overlays.Rect):
            top_left = transform.map(QPointF(item.x0, item.y0))
            bottom_right = transform.map(QPointF(item.x1, item.y1))
            painter.drawRect(QRectF(top_left, bottom_right).normalized())

        elif isinstance(item, overlays.Polyline):
            polygon = QPolygonF([transform.map(QPointF(float(x), float(y)))
                                 for x, y in item.points])
            if item.closed:
                painter.drawPolygon(polygon)
            else:
                painter.drawPolyline(polygon)

        elif isinstance(item, overlays.Text):
            font = QFont(painter.font())
            font.setPixelSize(int(max(MIN_TEXT_PX,
                                      CV_FONT_PX * item.scale * scale)))
            painter.setFont(font)
            painter.drawText(transform.map(QPointF(*item.org)), item.text)

        else:
            raise TypeError(f"not a drawable overlay item: {item!r}")

    painter.restore()
