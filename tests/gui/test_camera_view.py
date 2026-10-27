"""CameraView: zoom about the cursor, the crosshair default, the focus slider.

A fake camera with a fixed frame, offscreen. What is checked is the
transform - the sensor pixel under the cursor stays under it through a zoom
- and the presence of the two on-picture controls, not pixels.
"""

import os

import numpy as np
import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt                      # noqa: E402
from PySide6.QtGui import QMouseEvent, QWheelEvent                  # noqa: E402
from PySide6.QtWidgets import QApplication                          # noqa: E402

from micropick.gui.widgets.camera_view import ZOOM_MAX, CameraView  # noqa: E402
from micropick.hardware.camera import ControlReport                 # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _Camera:
    def __init__(self, label, size=(800, 600), crop=1.0, controls=None):
        self.label = label
        self.resolution = size
        self.crop = crop
        self.frame_count = 0
        self.controls = controls or ControlReport()
        self._frame = np.zeros((size[1], size[0], 3), np.uint8)
        self.sets = []

    def read(self):
        return True, self._frame

    def set_controls(self, controls):
        self.sets.append(dict(controls))
        report = ControlReport(applied=dict(controls))
        self.controls.applied.update(controls)
        return report

    def get_control(self, name):
        return self.controls.applied.get(name)


def _view(app, camera):
    view = CameraView()
    view.resize(400, 300)
    view.show()
    view.set_camera(camera)
    view._tick()
    app.processEvents()
    return view


def _wheel(app, view, pos, notches):
    event = QWheelEvent(pos, view.mapToGlobal(pos), QPoint(0, 0),
                        QPoint(0, int(120 * notches)), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    app.sendEvent(view, event)
    app.processEvents()


def _click(app, view, point):
    """Press and release, then let the double-click interval run out: a
    click is only sent once it cannot be the start of a double click."""
    for kind, buttons in ((QMouseEvent.Type.MouseButtonPress,
                           Qt.MouseButton.LeftButton),
                          (QMouseEvent.Type.MouseButtonRelease,
                           Qt.MouseButton.NoButton)):
        event = QMouseEvent(kind, point, view.mapToGlobal(point),
                            Qt.MouseButton.LeftButton, buttons,
                            Qt.KeyboardModifier.NoModifier)
        app.sendEvent(view, event)
    view._click_timer.setInterval(0)
    if view._click_timer.isActive():
        view._click_timer.start(0)
    app.processEvents()
    app.processEvents()


def test_zoom_keeps_the_pixel_under_the_cursor(app):
    view = _view(app, _Camera("over"))
    pos = QPointF(120.0, 200.0)
    before = view.transform.inverted()[0].map(pos)
    _wheel(app, view, pos, 4)
    assert view.zoom > 1.0
    after = view.transform.inverted()[0].map(pos)
    assert abs(after.x() - before.x()) < 1.0 and abs(after.y() - before.y()) < 1.0
    # The image on screen is the visible cut-out, sized to the widget, not
    # the whole frame at sixteen times its size.
    _wheel(app, view, pos, 40)
    assert view.zoom == ZOOM_MAX
    assert view._image.width() <= view.width() + 2 * ZOOM_MAX
    view.close()


def test_double_click_resets_and_wheel_below_one_is_clamped(app):
    view = _view(app, _Camera("over"))
    _wheel(app, view, QPointF(50.0, 50.0), 3)
    assert view.zoom > 1.0
    _wheel(app, view, QPointF(50.0, 50.0), -30)
    assert view.zoom == 1.0
    base = view._box
    assert base == view._shown
    view.close()


def test_crosshair_default_follows_the_label_and_is_remembered(app):
    view = _view(app, _Camera("overview_cam"))
    assert view.crosshair is True
    view.set_camera(_Camera("underview_cam"))
    assert view.crosshair is False
    view.crosshair = True
    view.set_camera(_Camera("overview_cam"))
    view.set_camera(_Camera("underview_cam"))
    assert view.crosshair is True                    # the operator's choice held
    view.close()


def test_a_click_reports_the_sensor_pixel_under_it(app):
    """The check page compares a click with a detection, and detections are
    in sensor pixels: the view is the only thing that knows about the
    widget's scale, its letterbox and the camera's crop."""
    view = _view(app, _Camera("over", (800, 600)))
    seen = []
    view.clicked.connect(lambda u, v: seen.append((u, v)))
    # Send the click where a known sensor pixel is drawn, and expect that
    # pixel back.
    # Not the outermost pixel: it maps to the drawn rectangle's own edge,
    # where a rounded widget coordinate can land a pixel outside it. A
    # crosshair that close to the frame edge is outside the map's coverage
    # anyway.
    for sensor in [(400.0, 300.0), (120.0, 90.0), (780.0, 580.0)]:
        point = view.transform.map(QPointF(*sensor))
        _click(app, view, point)
        assert seen, f"no click reported for {sensor}"
        assert abs(seen[-1][0] - sensor[0]) < 2 and abs(seen[-1][1] - sensor[1]) < 2
    view.close()


def test_a_click_survives_zoom_and_crop(app):
    view = _view(app, _Camera("under", (800, 600), crop=0.5))
    seen = []
    view.clicked.connect(lambda u, v: seen.append((u, v)))
    _wheel(app, view, QPointF(view.width() * 0.4, view.height() * 0.6), 3)
    assert view.zoom > 1.0
    # A cropped view shows the centered square, so pick a pixel inside it.
    sensor = (400.0, 300.0)
    point = view.transform.map(QPointF(*sensor))
    _click(app, view, point)
    assert seen and abs(seen[-1][0] - sensor[0]) < 2 and abs(seen[-1][1] - sensor[1]) < 2
    view.close()


def test_a_click_outside_the_picture_is_not_a_click_on_it(app):
    view = _view(app, _Camera("over", (800, 200)))     # letterboxed top and bottom
    seen = []
    view.clicked.connect(lambda u, v: seen.append((u, v)))
    _click(app, view, QPointF(view.width() / 2, 2.0))
    assert seen == []
    view.close()


def _right_click(app, view, point):
    for kind, buttons in ((QMouseEvent.Type.MouseButtonPress,
                           Qt.MouseButton.RightButton),
                          (QMouseEvent.Type.MouseButtonRelease,
                           Qt.MouseButton.NoButton)):
        app.sendEvent(view, QMouseEvent(kind, point, view.mapToGlobal(point),
                                        Qt.MouseButton.RightButton, buttons,
                                        Qt.KeyboardModifier.NoModifier))
    app.processEvents()


def test_a_right_click_reports_the_sensor_pixel_and_is_not_a_click(app):
    view = _view(app, _Camera("over", (800, 600)))
    right, left = [], []
    view.right_clicked.connect(lambda u, v: right.append((u, v)))
    view.clicked.connect(lambda u, v: left.append((u, v)))
    _right_click(app, view, view.transform.map(QPointF(120.0, 90.0)))
    assert len(right) == 1
    assert abs(right[0][0] - 120.0) < 2 and abs(right[0][1] - 90.0) < 2
    view._click_timer.start(0)
    app.processEvents()
    assert left == []
    view.close()


def test_a_size_histogram_is_drawn_over_the_picture_without_failing(app):
    """Painted onto an offscreen widget with a frame: the drawing path runs
    end to end, and the toggle appears with the histogram."""
    import numpy as np
    import pandas as pd
    from micropick.gui.detector import Detection
    from micropick.gui.widgets.camera_view import CameraView
    from micropick.gui.widgets.size_histogram import size_bins
    df = pd.DataFrame({"diameter_microns": [300.0, 320.0, 600.0]})
    empty = df.iloc[0:0]
    detection = Detection(np.empty((0, 4)), np.empty(0), df, empty, empty,
                          empty, {}, classified=True, standin=False)
    view = CameraView()
    view.resize(900, 700)
    view.hold(np.zeros((480, 640, 3), dtype=np.uint8))
    view.set_histogram(size_bins(detection, (250, 500)))
    view.grab()                                       # paints
    view.set_histogram(None)
    assert view._histogram is None


def test_a_snapshot_is_the_frame_itself_in_the_images_folder(app, tmp_path,
                                                             monkeypatch):
    import time

    import cv2
    import numpy as np

    from micropick import paths
    from micropick.gui.widgets.camera_view import CameraView
    from micropick.viz import overlays
    monkeypatch.setenv(paths.ENV_VAR, str(tmp_path))
    paths.set_overrides(images=str(tmp_path / "pics"))
    try:
        frame = np.random.default_rng(0).integers(
            0, 255, (240, 320, 3), dtype=np.uint8)
        view = CameraView()
        view.resize(640, 480)
        view.hold(frame)
        # An overlay on the picture: it must not be in the file.
        view.set_overlay_items([overlays.Circle(center=(100, 100), radius=30,
                                                color=(0, 0, 255))])
        saved = []
        view.snapshot_saved.connect(saved.append)
        view.save_snapshot()
        end = time.monotonic() + 5
        while not saved and time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)
        assert saved, "nothing was saved"
        path = saved[0]
        assert path.startswith(str(tmp_path / "pics"))
        assert np.array_equal(cv2.imread(path), frame)
        assert any("saved:" in line for line in view._flash)
    finally:
        paths.set_overrides()


def test_a_press_on_a_widget_over_the_picture_is_no_click(app):
    """The jog pad's card, a TipGauge, a page's button: a click on one,
    even beside its buttons, never reaches the picture as a click."""
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QFrame, QPushButton, QVBoxLayout

    view = _view(app, _Camera("over", (800, 600)))
    seen = []
    view.clicked.connect(lambda u, v: seen.append((u, v)))
    view.right_clicked.connect(lambda u, v: seen.append((u, v)))
    card = QFrame()
    QVBoxLayout(card).addWidget(QPushButton("Up"))
    card.setFixedSize(card.sizeHint())
    view.add_corner_widget(card)
    top_left = QFrame()
    top_left.setFixedSize(80, 40)
    view.add_top_left_widget(top_left)
    card.show()
    top_left.show()
    app.processEvents()
    view._click_timer.setInterval(0)
    for widget in (card, top_left):
        beside = QPoint(4, 4)                 # in its margin, by no button
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, beside)
        QTest.mouseClick(widget, Qt.MouseButton.RightButton,
                         Qt.KeyboardModifier.NoModifier, beside)
        # The same spot, as the picture would be told of it.
        _click(app, view, QPointF(widget.mapTo(view, beside)))
        _right_click(app, view, QPointF(widget.mapTo(view, beside)))
    app.processEvents()
    assert seen == []
    view.close()


def test_without_a_pixel_map_a_click_moves_nothing(app):
    """Before the first camera calibration a pixel has no direction on the
    deck: the mouse row says to use the arrows and a click is not sent."""
    view = _view(app, _Camera("over", (800, 600)))
    mapped = [False]
    view.set_click_to_move("Click   move the camera there",
                           lambda: mapped[0])
    seen = []
    view.clicked.connect(lambda u, v: seen.append((u, v)))
    point = view.transform.map(QPointF(400.0, 300.0))

    assert "Click   move the camera there" not in view.mouse_help
    assert "Use the arrows to move the camera." in view.mouse_help
    _click(app, view, point)
    assert seen == []

    mapped[0] = True
    assert view.mouse_help[0] == "Click   move the camera there"
    assert "Use the arrows to move the camera." not in view.mouse_help
    _click(app, view, point)
    assert len(seen) == 1
    view.close()
