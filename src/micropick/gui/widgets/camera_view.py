"""A camera, on screen.

Pull, not push
--------------
A QTimer asks the camera for its latest frame thirty times a second and drops
whatever it missed. The alternative — subscribing to the grab loop and sending
each frame on as a signal — puts a 36 MB array into Qt's event queue for every
grab, and a queue is unbounded: the moment the GUI thread falls behind, memory
and latency both climb and neither comes back. `BackgroundCamera.read()`
already hands out the newest frame and nothing else, which is exactly what a
display wants, so the display asks.

Scaling happens once, in OpenCV
-------------------------------
The upper camera is 2592x1944 and the lower one 4000x3000, against a widget of
perhaps 1300 px. Handing 36 MB to QPainter with SmoothPixmapTransform on every
repaint is not a thing that runs at thirty frames a second; `cv2.resize` with
INTER_AREA is vectorised and is the right filter for a large reduction anyway.
So a frame that is more than twice too wide is reduced before it becomes a
QImage, and the result is cached: it is recomputed when a new frame arrives or
when the widget is resized, and **never in paintEvent**, which only draws.

Live and held
-------------
`live` off means the camera is not read at all and the held frame stays on
screen, unchanged. That is `PickView` from `workflows/picking`: a decision was
made on one particular picture and the overlays measured on it belong to that
picture and no other, so a display that quietly refreshed underneath them would
draw last cycle's contours over this cycle's dish.

Coordinates
-----------
`transform` maps **sensor** pixels to widget pixels, crop included. Overlays are
therefore drawn by QPainter in widget space rather than into a 36 MB array, and
they are positioned with the coordinates detection actually produced, which are
always in whole-sensor pixels — the crop is a property of the view and applies
where a person looks and nowhere else.
"""

from __future__ import annotations

import time

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QTransform
from PySide6.QtWidgets import QWidget

from ...core.vision.cuboids import center_crop_box
from .frame import to_qimage

__all__ = ["CameraView"]

REFRESH_HZ = 30

# Reduce in OpenCV past this ratio; below it QPainter's own scaling is cheap
# enough and avoids a second resampling of an already small frame.
RESIZE_ABOVE = 2.0

# The viewport is one colour in both themes, on purpose. It is a picture frame,
# like a video player's letterbox: a light surround changes how the contents of
# a dish read, and the operator judges exposure and focus by eye through this
# widget.
BACKDROP = QColor(24, 24, 26)
CAPTION_BG = QColor(0, 0, 0, 140)
CAPTION_FG = QColor(235, 235, 235)
# Red, as in jog_in_window, so the marker means the same thing in both.
CROSSHAIR = QColor(230, 60, 60)
CROSSHAIR_ARM = 30

FPS_WINDOW_S = 0.5


class CameraView(QWidget):
    """Shows one camera. Owns no device and closes nothing."""

    frame_shown = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setAutoFillBackground(False)

        self._camera = None
        self._live = True
        self._raw: np.ndarray | None = None       # what is on screen, sensor sized
        self._image = None                        # the scaled QImage of it
        self._box = QRect()                       # where it goes in the widget
        self._view_size = (0, 0)                  # the cropped frame's own size
        self._transform = QTransform()
        self._dirty = False

        self._fps = 0.0
        self._fps_at = time.monotonic()
        self._fps_count = 0

        self._timer = QTimer(self)
        self._timer.setInterval(max(1, 1000 // REFRESH_HZ))
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # -- what it is showing --------------------------------------------------

    @property
    def camera(self):
        return self._camera

    def set_camera(self, camera) -> None:
        """Attach or detach. Passing None leaves the placeholder up."""
        if camera is self._camera:
            return
        self._camera = camera
        self._raw = None
        self._image = None
        self._fps = 0.0
        self._fps_count = getattr(camera, "frame_count", 0) if camera else 0
        self._fps_at = time.monotonic()
        self.update()

    @property
    def live(self) -> bool:
        return self._live

    @live.setter
    def live(self, value: bool) -> None:
        self._live = bool(value)
        if not self._live:
            self._fps = 0.0

    @property
    def held_frame(self) -> np.ndarray | None:
        """The frame currently on screen, in sensor pixels. Not a copy."""
        return self._raw

    def hold(self, frame: np.ndarray | None) -> None:
        """Show this frame and stop reading the camera.

        One call rather than two, because `live = False` followed by assigning a
        frame leaves a window in which the old picture is up and the new tables
        are being drawn on it.
        """
        self._live = False
        self._fps = 0.0
        self._raw = frame
        self._dirty = True
        self._rebuild()
        self.update()

    def resume(self) -> None:
        self._live = True
        self._fps_at = time.monotonic()
        self._fps_count = getattr(self._camera, "frame_count", 0)

    @property
    def transform(self) -> QTransform:
        """Sensor pixels to widget pixels. Identity while nothing is shown."""
        return QTransform(self._transform)

    def sensor_to_widget(self, x: float, y: float) -> QPointF:
        return self._transform.map(QPointF(float(x), float(y)))

    # -- the pull loop -------------------------------------------------------

    def _tick(self) -> None:
        camera = self._camera
        if camera is None or not self._live:
            return
        ok, frame = camera.read()
        if not ok or frame is None:
            return

        self._measure_fps(camera)
        if frame is self._raw and not self._dirty:
            # The camera has not produced anything new. Rebuilding would
            # resample the same pixels; repainting would draw them again.
            return
        self._raw = frame
        self._rebuild()
        self.update()
        self.frame_shown.emit()

    def _measure_fps(self, camera) -> None:
        """From frame_count. measure_fps() sleeps for a second and would take
        the GUI thread with it."""
        now = time.monotonic()
        elapsed = now - self._fps_at
        if elapsed < FPS_WINDOW_S:
            return
        count = getattr(camera, "frame_count", 0)
        self._fps = max(0.0, (count - self._fps_count) / elapsed)
        self._fps_count = count
        self._fps_at = now

    # -- scaling, exactly once per frame -------------------------------------

    def _view_of(self, frame: np.ndarray) -> tuple[np.ndarray, int, int]:
        """The part worth looking at, and where it starts on the sensor.

        Origin and crop come from one call. They were two once, and disagreed:
        `center_crop_box` answers with the centred square whatever it is asked,
        so at a crop of 1.0 it still returns a non-zero origin, and every box
        drawn was shifted by it — DESIGN section 5.
        """
        crop = float(getattr(self._camera, "crop", 1.0)) if self._camera else 1.0
        if crop == 1.0:
            return frame, 0, 0
        x0, y0, side = center_crop_box(frame.shape, crop)
        return frame[y0:y0 + side, x0:x0 + side], x0, y0

    def _rebuild(self) -> None:
        """Build the QImage and the transform. Never called from paintEvent."""
        self._dirty = False
        frame = self._raw
        if frame is None:
            self._image = None
            self._box = QRect()
            self._view_size = (0, 0)
            self._transform = QTransform()
            return

        view, x0, y0 = self._view_of(frame)
        height, width = view.shape[:2]
        box = self._fit(width, height)
        self._box = box
        self._view_size = (width, height)

        if width > RESIZE_ABOVE * max(1, box.width()):
            # INTER_AREA is the correct filter for a large reduction, and it
            # also lands the array contiguous, which to_qimage requires.
            shown = cv2.resize(view, (box.width(), box.height()),
                               interpolation=cv2.INTER_AREA)
        else:
            # A crop is a slice and so is not contiguous. This is the one path
            # where the copy is real, and it is a small frame by construction.
            shown = view if view.flags["C_CONTIGUOUS"] else np.ascontiguousarray(view)

        self._image = to_qimage(shown)
        scale = box.width() / width
        self._transform = (QTransform()
                           .translate(box.x(), box.y())
                           .scale(scale, scale)
                           .translate(-x0, -y0))

    def _fit(self, width: int, height: int) -> QRect:
        """The largest rectangle of the frame's aspect that fits, centred."""
        if width <= 0 or height <= 0:
            return QRect(0, 0, 0, 0)
        available = self.rect()
        scale = min(available.width() / width, available.height() / height)
        shown_w = max(1, int(width * scale))
        shown_h = max(1, int(height * scale))
        return QRect(available.x() + (available.width() - shown_w) // 2,
                     available.y() + (available.height() - shown_h) // 2,
                     shown_w, shown_h)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # The cached scale is only right for one widget size.
        self._rebuild()

    # -- only while it is on screen ------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fps_at = time.monotonic()
        self._fps_count = getattr(self._camera, "frame_count", 0)
        self._timer.start()

    def hideEvent(self, event) -> None:
        """A page the operator is not looking at costs nothing.

        The pages live in a QStackedWidget, so switching away hides this
        without destroying it, and a timer left running would keep reducing a
        36 MB frame thirty times a second for nobody. The camera itself is
        untouched: it belongs to the session and its grab thread carries on.
        """
        self._timer.stop()
        super().hideEvent(event)

    # -- drawing -------------------------------------------------------------

    def paintEvent(self, event) -> None:
        """Draws, and computes nothing. Everything it needs was built when the
        frame arrived or when the widget was resized."""
        painter = QPainter(self)
        painter.fillRect(self.rect(), BACKDROP)

        if self._image is None:
            self._draw_placeholder(painter)
            painter.end()
            return

        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawImage(self._box, self._image)
        self._draw_crosshair(painter, self._box)
        self._draw_caption(painter)
        painter.end()

    def _draw_placeholder(self, painter: QPainter) -> None:
        painter.setPen(QPen(QColor(120, 120, 128)))
        painter.setFont(QFont(self.font().family(), 11))
        text = ("no camera" if self._camera is None else
                "waiting for the first frame")
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)

    def _draw_crosshair(self, painter: QPainter, box: QRect) -> None:
        painter.setPen(QPen(CROSSHAIR, 2))
        cx, cy = box.center().x(), box.center().y()
        painter.drawLine(cx - CROSSHAIR_ARM, cy, cx + CROSSHAIR_ARM, cy)
        painter.drawLine(cx, cy - CROSSHAIR_ARM, cx, cy + CROSSHAIR_ARM)

    def _draw_caption(self, painter: QPainter) -> None:
        width, height = self._view_size
        parts = [f"{width}×{height}"]
        crop = float(getattr(self._camera, "crop", 1.0)) if self._camera else 1.0
        if crop != 1.0:
            parts.append(f"crop {crop:g}")
        parts.append("held" if not self._live else f"{self._fps:.0f} fps")

        painter.setFont(QFont(self.font().family(), 10))
        text = "   ".join(parts)
        metrics = painter.fontMetrics()
        pad = 6
        rect = QRect(8, 8, metrics.horizontalAdvance(text) + 2 * pad,
                     metrics.height() + 2 * pad)
        painter.fillRect(rect, CAPTION_BG)
        painter.setPen(QPen(CAPTION_FG))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
