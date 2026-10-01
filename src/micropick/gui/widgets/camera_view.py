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

Zoom is a view property too
---------------------------
The wheel zooms about the cursor, the middle button drags the zoomed picture
about, and a double click resets. Panning moves the same centre the wheel
does, and `_zoomed` clamps it, so the picture never leaves a gap at its
edge. Zooming changes nothing but the transform: the frame handed to
detection is the whole sensor frame as before, and at 4x on a 4000 px frame
the widget does not resample 16000 px — only the part of the frame that is
on screen is cut out and scaled to the widget, so the cost stays what the
widget's size makes it.

A click is only a click once it is not a double click
-----------------------------------------------------
`clicked` can move the robot, and the double click that resets the zoom
begins with a click. So `clicked` is sent a double-click interval after the
release, and a double click in that time cancels it: resetting the zoom
never sends the gantry anywhere.

On the picture, two small controls
----------------------------------
A crosshair toggle, on by default for a camera whose label does not say
"under" and off for one that does: the crosshair marks the reference pixel of
the upper camera, and on the lower camera it sits over the crosshair disc the
operator is trying to see. And a focus slider, only for a camera whose driver
took a `focus` control at open — the lower camera has a motorised lens and its
focus is set from the profile, which is the wrong place to be tuning it by
trial. The slider sets it on the device live and reads it back; the profile is
written from the Profile page, deliberately a separate act.

A third, "points", appears only while a page has marks to offer
(`set_marks_available`): the profile's saved positions drawn where they are
on the deck (`gui.position_marks`). Off by default, and remembered per camera
like the crosshair. The marks are a layer of their own, apart from
`set_overlay_items`, so a page's detections and the saved points never
overwrite each other.

And a fourth, "sizes", while a page has handed over a size histogram
(`set_histogram`, the bins of `widgets.size_histogram`): the cuboids'
diameters against the size window, small and translucent in the
bottom-left corner. Chrome like the caption - drawn in widget pixels,
never into the frame, and not a widget, so a click on it still reaches the
picture. On by default when it appears.
"""

from __future__ import annotations

import time

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QTransform
from PySide6.QtWidgets import (QApplication, QCheckBox, QHBoxLayout, QLabel,
                               QSlider, QWidget)

from ...core.vision.cuboids import center_crop_box
from . import overlay_painter
from .frame import to_qimage
from .size_histogram import (INSIDE, INSIDE_ALPHA, OUTSIDE, OUTSIDE_ALPHA,
                             WINDOW_EDGE)

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
CAPTION_PAD = 6
CAPTION_GAP = 6
# White, thin and translucent: a reference to aim with, not a mark on the
# dish. Solid red hid the few pixels it was being aimed at and read as
# one of the detection colours.
CROSSHAIR = QColor(255, 255, 255, 150)
CROSSHAIR_WIDTH = 1
CROSSHAIR_ARM = 30

FPS_WINDOW_S = 0.5

DEFAULT_ASPECT = 4 / 3           # the upper camera, 2592x1944

ZOOM_MIN, ZOOM_MAX = 1.0, 16.0
ZOOM_STEP = 1.25                 # per wheel notch

# UVC focus ranges differ by device; this covers the ones seen here (the
# Arducam takes 0-1023). A value outside is still shown, clamped.
FOCUS_RANGE = (0, 1023)
FOCUS_SETTLE_MS = 60             # coalesce slider moves into one set()

# The size histogram over the picture: its box, in widget pixels.
HIST_BOX = (300, 150)

# The label of a camera whose crosshair is off unless asked for.
UNDER_WORD = "under"


class CameraView(QWidget):
    """Shows one camera. Owns no device and closes nothing."""

    frame_shown = Signal()
    # The shape of what is shown changed - another camera, another crop.
    # `FeedRow` listens, so the picture's share of the page follows it.
    aspect_changed = Signal()
    # A left click on the picture, in **sensor** pixels: the same
    # coordinates detection produces and `transform` maps, so a page can
    # compare a click with a detection without knowing about zoom or crop.
    # Not emitted for a click outside the picture, or on the controls that
    # sit over it - those are widgets and take their own clicks.
    clicked = Signal(float, float)
    # Another camera, or none, is now shown.
    camera_changed = Signal()
    # The "points" box was ticked or cleared.
    marks_toggled = Signal(bool)

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
        self._overlay: list = []
        self._marks: list = []
        self._histogram = None                    # size_histogram.SizeBins
        self._position: list[str] = []
        self._status: list[str] = []
        self._help: list[str] = []
        self._lost: str | None = None             # the camera's `lost`

        # Zoom about a point: the view pixel that sits at the widget's centre.
        self._zoom = 1.0
        self._centre: tuple[float, float] | None = None
        self._shown = QRect()                     # where the image lands
        self._pan_from: QPointF | None = None     # middle button held here

        # See "A click is only a click once it is not a double click".
        self._pending_click: tuple[float, float] | None = None
        self._after_double = False                # its release is not a click
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._send_click)

        # Remembered per camera label, so a choice made on one feed of the
        # lower camera holds on every other feed of it.
        self._crosshair_choice: dict[str, bool] = {}
        self._marks_choice: dict[str, bool] = {}
        self._marks_available = False
        self._build_controls()

        self._timer = QTimer(self)
        self._timer.setInterval(max(1, 1000 // REFRESH_HZ))
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _build_controls(self) -> None:
        """The two controls that sit on the picture. Children of the widget,
        laid out by hand in resizeEvent: the picture underneath is drawn by
        paintEvent and has no layout to join."""
        self.crosshair_box = QCheckBox("crosshair", self)
        self.crosshair_box.setChecked(True)
        # Neither control takes keyboard focus: the keys over a feed belong
        # to the jog panel, and a slider with focus that also stepped on
        # the arrows would be two things moving on one key.
        self.crosshair_box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.crosshair_box.toggled.connect(self._crosshair_toggled)
        self.crosshair_box.setStyleSheet(
            f"QCheckBox {{ color: rgb({CAPTION_FG.red()},{CAPTION_FG.green()},"
            f"{CAPTION_FG.blue()}); background: rgba(0,0,0,140); "
            f"padding: 4px 6px; border-radius: 4px; }}")

        self.marks_box = QCheckBox("points", self)
        self.marks_box.setChecked(False)
        self.marks_box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.marks_box.setToolTip("Show the positions saved in the profile "
                                  "where they are on the deck.")
        self.marks_box.toggled.connect(self._marks_toggled)
        self.marks_box.setStyleSheet(self.crosshair_box.styleSheet())
        self.marks_box.hide()

        self.sizes_box = QCheckBox("sizes", self)
        self.sizes_box.setChecked(True)
        self.sizes_box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sizes_box.setToolTip("Show the cuboids' sizes against the size "
                                  "window over the picture.")
        self.sizes_box.toggled.connect(lambda _on: self.update())
        self.sizes_box.setStyleSheet(self.crosshair_box.styleSheet())
        self.sizes_box.hide()

        self.focus_row = QWidget(self)
        row = QHBoxLayout(self.focus_row)
        row.setContentsMargins(6, 2, 6, 2)
        row.setSpacing(6)
        self.focus_label = QLabel("focus", self.focus_row)
        self.focus_slider = QSlider(Qt.Orientation.Horizontal, self.focus_row)
        self.focus_slider.setRange(*FOCUS_RANGE)
        self.focus_slider.setSingleStep(1)
        self.focus_slider.setPageStep(10)
        self.focus_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # The wheel over the slider steps the focus, not the zoom: the
        # slider takes the event before the view sees it.
        self.focus_slider.valueChanged.connect(self._focus_moved)
        self.focus_value = QLabel("", self.focus_row)
        self.focus_value.setMinimumWidth(70)
        row.addWidget(self.focus_label)
        row.addWidget(self.focus_slider, 1)
        row.addWidget(self.focus_value)
        self.focus_row.setStyleSheet(
            f"QLabel {{ color: rgb({CAPTION_FG.red()},{CAPTION_FG.green()},"
            f"{CAPTION_FG.blue()}); }} "
            f"QWidget#focusrow {{ background: rgba(0,0,0,140); "
            f"border-radius: 4px; }}")
        self.focus_row.setObjectName("focusrow")
        self.focus_row.setAutoFillBackground(False)
        self.focus_row.hide()

        self._focus_timer = QTimer(self)
        self._focus_timer.setSingleShot(True)
        self._focus_timer.setInterval(FOCUS_SETTLE_MS)
        self._focus_timer.timeout.connect(self._apply_focus)
        self._focus_pending: int | None = None
        # Both appear with a camera; without one there is only the placeholder.
        self.crosshair_box.hide()
        self._place_controls()

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
        self._lost = None
        self._zoom = 1.0
        self._centre = None
        self._sync_controls()
        self.update()
        self.camera_changed.emit()

    # -- the controls on the picture -----------------------------------------

    def _label(self) -> str:
        return str(getattr(self._camera, "label", "") or "")

    def _sync_controls(self) -> None:
        """Crosshair from memory or from the label; focus slider from the
        device, and only when the device took a focus at open."""
        label = self._label()
        wanted = self._crosshair_choice.get(
            label, UNDER_WORD not in label.lower())
        self.crosshair_box.blockSignals(True)
        self.crosshair_box.setChecked(wanted)
        self.crosshair_box.blockSignals(False)
        self.crosshair_box.setVisible(self._camera is not None)
        self.marks_box.blockSignals(True)
        self.marks_box.setChecked(self._marks_choice.get(label, False))
        self.marks_box.blockSignals(False)
        self.marks_box.setVisible(self._camera is not None
                                  and self._marks_available)
        self.sizes_box.setVisible(self._camera is not None
                                  and self._histogram is not None)

        controls = getattr(self._camera, "controls", None)
        applied = getattr(controls, "applied", {}) or {}
        has_focus = "focus" in applied and hasattr(self._camera, "set_controls")
        self.focus_row.setVisible(has_focus)
        if has_focus:
            current = self._camera.get_control("focus")
            value = int(round(current if current is not None else applied["focus"]))
            self.focus_slider.blockSignals(True)
            self.focus_slider.setValue(max(FOCUS_RANGE[0], min(FOCUS_RANGE[1], value)))
            self.focus_slider.blockSignals(False)
            self.focus_value.setText(str(value))
        self._place_controls()

    @property
    def crosshair(self) -> bool:
        return self.crosshair_box.isChecked()

    @crosshair.setter
    def crosshair(self, on: bool) -> None:
        self.crosshair_box.setChecked(bool(on))

    def _crosshair_toggled(self, on: bool) -> None:
        if self._camera is not None:
            self._crosshair_choice[self._label()] = bool(on)
        self.update()

    @property
    def marks_shown(self) -> bool:
        return self.marks_box.isVisible() and self.marks_box.isChecked()

    def set_marks_available(self, available: bool) -> None:
        """Whether there are marks to offer, which is what shows the box."""
        available = bool(available)
        if available == self._marks_available:
            return
        self._marks_available = available
        self.marks_box.setVisible(self._camera is not None and available)
        self._place_controls()
        self.update()

    def set_marks(self, primitives) -> None:
        """The marks layer, in sensor coordinates like `set_overlay_items`;
        drawn while the "points" box is ticked."""
        self._marks = list(primitives or ())
        self.update()

    def set_histogram(self, bins) -> None:
        """The size histogram to draw over the picture, or None. See the
        module docstring."""
        self._histogram = bins
        self.sizes_box.setVisible(self._camera is not None and bins is not None)
        self._place_controls()
        self.update()

    def _marks_toggled(self, on: bool) -> None:
        if self._camera is not None:
            self._marks_choice[self._label()] = bool(on)
        self.marks_toggled.emit(bool(on))
        self.update()

    def _focus_moved(self, value: int) -> None:
        """Coalesced: a drag produces dozens of values a second and the
        device takes one control transfer at a time."""
        self._focus_pending = int(value)
        self.focus_value.setText(f"{value} …")
        self._focus_timer.start()

    def _apply_focus(self) -> None:
        value, self._focus_pending = self._focus_pending, None
        if value is None or self._camera is None:
            return
        report = self._camera.set_controls({"focus": value})
        if "focus" in report.rejected:
            asked, got = report.rejected["focus"]
            self.focus_value.setText(f"{got:g} (asked {asked:g})")
        else:
            self.focus_value.setText(f"{report.applied.get('focus', value):g}")

    def _place_controls(self) -> None:
        margin = 8
        hint = self.crosshair_box.sizeHint()
        self.crosshair_box.move(self.width() - hint.width() - margin, margin)
        self.crosshair_box.resize(hint)
        # The toggles stack down the top-right corner, the shown ones only.
        top = margin + hint.height() + margin // 2
        for box in (self.marks_box, self.sizes_box):
            size = box.sizeHint()
            box.move(self.width() - size.width() - margin, top)
            box.resize(size)
            if box.isVisible():
                top += size.height() + margin // 2
        height = self.focus_row.sizeHint().height()
        width = min(360, max(200, self.width() - 2 * margin))
        self.focus_row.setGeometry(margin, self.height() - height - margin,
                                   width, height)

    # -- zoom ----------------------------------------------------------------

    @property
    def zoom(self) -> float:
        return self._zoom

    def wheelEvent(self, event) -> None:
        if self._image is None:
            return
        notches = event.angleDelta().y() / 120.0
        if not notches:
            return
        factor = ZOOM_STEP ** notches
        new = max(ZOOM_MIN, min(ZOOM_MAX, self._zoom * factor))
        if new == self._zoom:
            return
        # Keep the view pixel under the cursor where it is.
        cursor = event.position()
        scale_old = self._box.width() / max(1, self._view_size[0])
        px = (cursor.x() - self._box.x()) / scale_old
        py = (cursor.y() - self._box.y()) / scale_old
        scale_new = scale_old * new / self._zoom
        self._zoom = new
        if new == ZOOM_MIN:
            self._centre = None
        else:
            # The view pixel at the widget centre, with the cursor pinned.
            centre = self.rect().center()
            self._centre = (px + (centre.x() - cursor.x()) / scale_new,
                            py + (centre.y() - cursor.y()) / scale_new)
        self._dirty = True
        self._rebuild()
        self.update()
        event.accept()

    def mousePressEvent(self, event) -> None:
        if (event.button() == Qt.MouseButton.MiddleButton
                and self._image is not None and self._zoom != ZOOM_MIN):
            self._pan_from = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._pan_from is None:
            super().mouseMoveEvent(event)
            return
        position = event.position()
        delta = position - self._pan_from
        self._pan_from = position
        scale = self._box.width() / max(1, self._view_size[0])
        if self._centre is not None and scale > 0:
            # The picture follows the hand, so the centre moves against it.
            self._centre = (self._centre[0] - delta.x() / scale,
                            self._centre[1] - delta.y() / scale)
            self._dirty = True
            self._rebuild()
            self.update()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._pan_from is not None:
            self._pan_from = None
            self.unsetCursor()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._after_double:
            self._after_double = False
        elif event.button() == Qt.MouseButton.LeftButton and self._image is not None:
            position = event.position()
            if self._shown.contains(position.toPoint()):
                inverse, ok = self._transform.inverted()
                if ok:
                    point = inverse.map(position)
                    self._pending_click = (point.x(), point.y())
                    self._click_timer.start(
                        QApplication.doubleClickInterval())
        super().mouseReleaseEvent(event)

    def _send_click(self) -> None:
        click, self._pending_click = self._pending_click, None
        if click is not None:
            self.clicked.emit(*click)

    def mouseDoubleClickEvent(self, event) -> None:
        # The first click of the pair is not a click; see the module notes.
        self._click_timer.stop()
        self._pending_click = None
        self._after_double = True
        if self._zoom != ZOOM_MIN:
            self._zoom = ZOOM_MIN
            self._centre = None
            self._dirty = True
            self._rebuild()
            self.update()
        super().mouseDoubleClickEvent(event)

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

    def set_overlay_items(self, primitives) -> None:
        """Draw these over the picture, in **sensor** coordinates.

        They come from `viz.overlays.items`, which is also what the cv2
        renderer takes, so the same list drawn either way lands in the same
        place. Nothing is drawn into the frame: at 4000x3000 that is 36 MB of
        copy per repaint, and the overlay would then be resampled along with
        the picture instead of staying a crisp line over it.
        """
        self._overlay = list(primitives or ())
        self.update()

    def set_position(self, lines) -> None:
        """Where the gantry is, in a box under the caption. Empty removes it.

        On the picture rather than in a panel beside it: the operator jogging
        is looking here, and a readout at the other side of the window is one
        they have to look away to check. `JogPanel.position_changed` is what
        normally feeds it.
        """
        self._position = [str(line) for line in lines or ()]
        self.update()

    def set_help(self, lines) -> None:
        """What the keys and the mouse do here, in a box in the bottom-right
        corner. Empty removes it."""
        self._help = [str(line) for line in lines or ()]
        self.update()

    def set_status(self, lines) -> None:
        """Lines of text under the caption: what a page is doing, where the
        caption says what the camera is. Chrome like the caption, so it is
        drawn in widget pixels and reads the same at any frame size or zoom
        rather than being scaled along with the picture. Empty removes it."""
        self._status = [str(line) for line in lines or ()]
        self.update()

    @property
    def aspect(self) -> float:
        """Width over height of what is shown; the upper camera's 4:3
        until there is a frame to measure."""
        width, height = self._view_size
        return width / height if width > 0 and height > 0 else DEFAULT_ASPECT

    @property
    def transform(self) -> QTransform:
        """Sensor pixels to widget pixels. Identity while nothing is shown."""
        return QTransform(self._transform)

    def sensor_to_widget(self, x: float, y: float) -> QPointF:
        return self._transform.map(QPointF(float(x), float(y)))

    # -- the pull loop -------------------------------------------------------

    def _tick(self) -> None:
        camera = self._camera
        if camera is None:
            return
        # Looked at even while a frame is held: a run's picture stays put,
        # but whether the camera behind it is there is news either way.
        lost = getattr(camera, "lost", None)
        if lost != self._lost:
            self._lost = lost
            self.update()
        if not self._live:
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
        if (width, height) != self._view_size:
            self._view_size = (width, height)
            self.aspect_changed.emit()

        if self._zoom != ZOOM_MIN:
            box = self._zoomed(box, width, height)
        self._box = box
        scale = box.width() / width

        # Only what is on screen is resampled. At zoom 1 that is the whole
        # frame to the box; zoomed in it is the visible part of the frame to
        # the widget, which costs the same whatever the zoom.
        shown_rect = box.intersected(self.rect())
        if shown_rect.isEmpty():
            shown_rect = box
        sx0 = int((shown_rect.x() - box.x()) / scale)
        sy0 = int((shown_rect.y() - box.y()) / scale)
        sx1 = min(width, int(np.ceil((shown_rect.right() + 1 - box.x()) / scale)))
        sy1 = min(height, int(np.ceil((shown_rect.bottom() + 1 - box.y()) / scale)))
        part = view[sy0:sy1, sx0:sx1]
        # The rect the cut-out lands on, in whole widget pixels, so that the
        # image's edge and the cut-out's edge are the same pixel.
        target = QRect(int(round(box.x() + sx0 * scale)),
                       int(round(box.y() + sy0 * scale)),
                       max(1, int(round((sx1 - sx0) * scale))),
                       max(1, int(round((sy1 - sy0) * scale))))
        self._shown = target

        pw = part.shape[1]
        if pw > RESIZE_ABOVE * max(1, target.width()):
            # INTER_AREA is the correct filter for a large reduction, and it
            # also lands the array contiguous, which to_qimage requires.
            shown = cv2.resize(part, (target.width(), target.height()),
                               interpolation=cv2.INTER_AREA)
        elif self._zoom != ZOOM_MIN and pw < target.width():
            # Enlarging: linear, so pixels read as pixels rather than as
            # blur, and it lands contiguous like the reduction does.
            shown = cv2.resize(part, (target.width(), target.height()),
                               interpolation=cv2.INTER_LINEAR)
        else:
            # A crop is a slice and so is not contiguous. This is the one path
            # where the copy is real, and it is a small frame by construction.
            shown = part if part.flags["C_CONTIGUOUS"] else np.ascontiguousarray(part)

        self._image = to_qimage(shown)
        self._transform = (QTransform()
                           .translate(box.x(), box.y())
                           .scale(scale, scale)
                           .translate(-x0, -y0))

    def _zoomed(self, base: QRect, width: int, height: int) -> QRect:
        """The fitted box enlarged by the zoom and placed so that `_centre`
        sits at the widget's centre, then clamped so no edge of the frame
        leaves a gap while the frame is larger than the widget."""
        scale = base.width() / width * self._zoom
        w, h = width * scale, height * scale
        if self._centre is None:
            self._centre = (width / 2.0, height / 2.0)
        cx, cy = self._centre
        centre = self.rect().center()
        x = centre.x() - cx * scale
        y = centre.y() - cy * scale
        avail = self.rect()
        if w >= avail.width():
            x = min(avail.x(), max(avail.x() + avail.width() - w, x))
        else:
            x = avail.x() + (avail.width() - w) / 2
        if h >= avail.height():
            y = min(avail.y(), max(avail.y() + avail.height() - h, y))
        else:
            y = avail.y() + (avail.height() - h) / 2
        # Write the clamp back, so the next wheel starts from where we are.
        self._centre = ((centre.x() - x) / scale, (centre.y() - y) / scale)
        return QRectF(x, y, w, h).toRect()

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
        self._place_controls()

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
            # Where the gantry is does not depend on there being a picture.
            self._draw_boxes(painter, [self._lost_lines(), self._position,
                                       self._status])
            self._draw_help(painter)
            painter.end()
            return

        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawImage(self._shown, self._image)
        if self._overlay:
            overlay_painter.paint(painter, self._overlay, self._transform)
        if self._marks and self.marks_shown:
            overlay_painter.paint(painter, self._marks, self._transform)
        if self.crosshair_box.isChecked():
            self._draw_crosshair(painter, self._box)
        if self._histogram is not None and self.sizes_box.isChecked():
            self._draw_histogram(painter)
        self._draw_caption(painter)
        self._draw_help(painter)
        painter.end()

    def _draw_placeholder(self, painter: QPainter) -> None:
        painter.setPen(QPen(QColor(120, 120, 128)))
        painter.setFont(QFont(self.font().family(), 11))
        text = ("no camera" if self._camera is None else
                "waiting for the first frame")
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)

    def _draw_crosshair(self, painter: QPainter, box: QRect) -> None:
        painter.setPen(QPen(CROSSHAIR, CROSSHAIR_WIDTH))
        cx, cy = box.center().x(), box.center().y()
        painter.drawLine(cx - CROSSHAIR_ARM, cy, cx + CROSSHAIR_ARM, cy)
        painter.drawLine(cx, cy - CROSSHAIR_ARM, cx, cy + CROSSHAIR_ARM)

    def _draw_caption(self, painter: QPainter) -> None:
        width, height = self._view_size
        parts = [f"{width}×{height}"]
        crop = float(getattr(self._camera, "crop", 1.0)) if self._camera else 1.0
        if crop != 1.0:
            parts.append(f"crop {crop:g}")
        if self._zoom != ZOOM_MIN:
            parts.append(f"zoom {self._zoom:.2g}×")
        parts.append("held" if not self._live else f"{self._fps:.0f} fps")

        # The caption, then where the gantry is, then what the page is
        # doing: boxes of one kind, stacked down the corner.
        self._draw_boxes(painter, [["   ".join(parts)], self._lost_lines(),
                                   self._position, self._status])

    def _draw_histogram(self, painter: QPainter) -> None:
        """The size histogram, small, in the bottom-left corner: clear of
        the help on the right and of the focus slider when there is one.
        The same bins as the page's plot, so the two count alike."""
        bins = self._histogram
        width, height = HIST_BOX
        if self.width() < 2 * width or self.height() < 2 * height:
            return                                # no room on a small view
        bottom = (self.focus_row.geometry().top() - 8
                  if self.focus_row.isVisible() else self.height() - 8)
        box = QRect(8, bottom - height, width, height)
        painter.fillRect(box, CAPTION_BG)
        painter.setFont(QFont(self.font().family(), 9))
        metrics = painter.fontMetrics()
        line = metrics.height()
        pad = CAPTION_PAD
        # The plot area: under a line for the largest count, over a line for
        # the window's edges and a line for the sentence.
        plot = QRect(box.x() + pad, box.y() + pad + line, width - 2 * pad,
                     height - 2 * pad - 3 * line)
        x0, x1 = bins.x_range
        scale = plot.width() / (x1 - x0)

        def x_of(value: float) -> float:
            return plot.left() + (value - x0) * scale

        band = QRectF(x_of(bins.low), plot.top(),
                      x_of(bins.high) - x_of(bins.low), plot.height())
        painter.fillRect(band, QColor(*WINDOW_EDGE, 45))
        painter.setPen(QPen(QColor(*WINDOW_EDGE), 1.5))
        for edge in (bins.low, bins.high):
            painter.drawLine(QPointF(x_of(edge), plot.top()),
                             QPointF(x_of(edge), plot.bottom()))
        peak = max(1, int(bins.counts.max()))
        bar_width = max(1.0, bins.width * scale * 0.92)
        painter.setPen(Qt.PenStyle.NoPen)
        for centre, count, inside in zip(bins.centres, bins.counts,
                                         bins.inside_bins):
            if not count:
                continue
            bar_height = plot.height() * count / peak
            colour = (QColor(*INSIDE, INSIDE_ALPHA) if inside
                      else QColor(*OUTSIDE, OUTSIDE_ALPHA))
            painter.fillRect(QRectF(x_of(centre) - bar_width / 2,
                                    plot.bottom() - bar_height,
                                    bar_width, bar_height), colour)
        painter.setPen(QPen(CAPTION_FG))
        painter.drawText(QRect(plot.left(), box.y() + pad, plot.width(), line),
                         Qt.AlignmentFlag.AlignLeft, f"sizes, µm  ·  max {peak}")
        for edge in (bins.low, bins.high):
            text = f"{edge:g}"
            half = metrics.horizontalAdvance(text) / 2
            painter.drawText(QPointF(x_of(edge) - half,
                                     plot.bottom() + line), text)
        painter.drawText(QRect(plot.left(), plot.bottom() + line + 2,
                               plot.width(), line),
                         Qt.AlignmentFlag.AlignLeft,
                         f"{bins.inside} of {bins.total} inside  ·  "
                         f"{bins.smaller} smaller, {bins.bigger} bigger")

    def _lost_lines(self) -> list[str]:
        """Under the caption while the camera is being opened again: the
        picture above is its last frame, not a live one."""
        if not self._lost:
            return []
        return [self._lost, "the picture is the last frame before it"]

    def _draw_boxes(self, painter: QPainter, groups: list[list[str]]) -> None:
        painter.setFont(QFont(self.font().family(), 10))
        painter.setPen(QPen(CAPTION_FG))
        top = 8
        for lines in groups:
            if lines:
                top = self._draw_box(painter, top, lines) + CAPTION_GAP

    def _draw_help(self, painter: QPainter) -> None:
        """The keys box, in the bottom-right corner, clear of the focus
        slider on the left and the crosshair toggle at the top."""
        if not self._help:
            return
        painter.setFont(QFont(self.font().family(), 10))
        painter.setPen(QPen(CAPTION_FG))
        metrics = painter.fontMetrics()
        width = (max(metrics.horizontalAdvance(line) for line in self._help)
                 + 2 * CAPTION_PAD)
        height = metrics.height() * len(self._help) + 2 * CAPTION_PAD
        self._draw_box(painter, self.height() - 8 - height, self._help,
                       left=self.width() - 8 - width)

    def _draw_box(self, painter: QPainter, top: int, lines: list[str],
                  left: int = 8) -> int:
        """One translucent box of left-aligned lines; returns its bottom."""
        metrics = painter.fontMetrics()
        pad = CAPTION_PAD
        width = max(metrics.horizontalAdvance(line) for line in lines)
        box = QRect(left, top, width + 2 * pad,
                    metrics.height() * len(lines) + 2 * pad)
        painter.fillRect(box, CAPTION_BG)
        for i, line in enumerate(lines):
            painter.drawText(QRect(box.x() + pad,
                                   box.y() + pad + i * metrics.height(),
                                   width, metrics.height()),
                             Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignVCenter, line)
        return box.bottom() + 1
