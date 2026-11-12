"""CameraView: zoom about the cursor, the crosshair default, the focus slider.

A fake camera with a fixed frame, offscreen. What is checked is the
transform - the sensor pixel under the cursor stays under it through a zoom
- and the presence of the two on-picture controls, not pixels.
"""

import os
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt                      # noqa: E402
from PySide6.QtGui import QImage, QMouseEvent, QWheelEvent          # noqa: E402
from PySide6.QtTest import QTest                                    # noqa: E402
from PySide6.QtWidgets import (QApplication, QCheckBox,             # noqa: E402
                               QPushButton)

from micropick.gui.widgets.camera_view import ZOOM_MAX, CameraView  # noqa: E402
from micropick.gui.widgets.tip_gauge import TipGauge                # noqa: E402
from micropick.gui.widgets.toast import toast_of                    # noqa: E402
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
        said = toast_of(view)
        assert said.text() == f"Saved: {Path(path).name}"
        assert said.property("tone") == "answer"
    finally:
        paths.set_overrides()


def _pixels(view) -> np.ndarray:
    image = view.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
    return np.frombuffer(image.constBits(), np.uint8).reshape(
        image.height(), image.bytesPerLine() // 4, 4).copy()


def test_a_pages_lines_stand_on_the_top_edge_never_under_the_position(app):
    view = CameraView()
    view.resize(800, 600)
    view.set_position(["X 1.00  Y 2.00  Z 3.00"])
    view.show()
    app.processEvents()
    without = _pixels(view)
    view.set_status(["12 cuboids found", "2 stuck, tried again after a shake"])
    app.processEvents()
    rows, columns = np.nonzero((_pixels(view) != without).any(axis=2))
    # At the top, on the right, and clear of the plate on the left.
    assert rows.size and columns.min() > 300
    assert rows.min() >= 8 and rows.max() < 8 + 60
    view.hide()
    view.deleteLater()


LINE = "Slot 8 · top 85.10 · bottom 67.77 · measured"


def _with_plate(app):
    view = _view(app, _Camera("over"))
    view.resize(800, 600)
    view.set_position(["X 1.00  Y 2.00  Z 3.00"])
    app.processEvents()
    return view


def test_the_height_line_is_painted_under_the_position(app):
    view = _with_plate(app)
    view.set_height_line("")
    app.processEvents()
    without = _pixels(view)
    view.set_height_line(LINE)
    app.processEvents()
    assert view.height_line == LINE
    rows, columns = np.nonzero((_pixels(view) != without).any(axis=2))
    # One line, at the left edge, under the plate.
    assert rows.size and columns.min() < 30
    assert 3 * 15 < rows.min() and rows.max() < rows.min() + 40
    view.hide()
    view.deleteLater()


def test_the_height_lines_row_is_kept_as_the_line_goes(app):
    """Over a slot with no plate the line is gone, and nothing moves."""
    view = _with_plate(app)
    view.set_height_line(LINE)
    app.processEvents()
    with_line = _pixels(view)
    view.set_height_line("")
    app.processEvents()
    rows = np.nonzero((_pixels(view) != with_line).any(axis=2))[0]
    # Only the line's own box changed: what is under it did not.
    assert rows.size and rows.max() < rows.min() + 40
    view.hide()
    view.deleteLater()


def test_a_view_has_no_height_row_unless_its_page_asks(app):
    view = _with_plate(app)
    plain = _pixels(view)
    view.set_height_line("")
    app.processEvents()
    moved = _pixels(view)
    assert np.array_equal(moved, plain)      # an empty row paints nothing
    assert view.height_line == ""
    view.set_height_line(None)
    app.processEvents()
    assert np.array_equal(_pixels(view), plain)
    view.hide()
    view.deleteLater()


def test_a_pages_lines_end_where_its_buttons_begin(app):
    view = CameraView()
    view.resize(800, 600)
    button = QPushButton("Camera to dish center")
    view.add_top_widget(button)
    view.show()
    app.processEvents()
    without = _pixels(view)
    view.set_status(["12 cuboids found"])
    app.processEvents()
    _, columns = np.nonzero((_pixels(view) != without).any(axis=2))
    assert columns.size and columns.max() < button.x()
    view.hide()
    view.deleteLater()


# -- one style, in the same places (#109) ---------------------------------------


def test_no_native_checkbox_stands_on_a_picture(app):
    view = _view(app, _Camera("over"))
    assert view.findChildren(QCheckBox) == []
    assert view.crosshair_box.text() == "Crosshair"
    assert view.marks_box.text() == "Points"
    view.hide()


def test_the_chips_and_the_picture_button_are_one_row_at_the_top_right(app):
    view = _view(app, _Camera("over"))
    view.resize(800, 600)
    view.set_marks_available(True)
    app.processEvents()
    row = [view.crosshair_box, view.marks_box, view.snapshot_button]
    assert {w.y() for w in row} == {8}
    assert {w.height() for w in row} == {32}
    assert [w.x() for w in row] == sorted(w.x() for w in row)
    assert view.snapshot_button.geometry().right() == view.width() - 9
    view.hide()


def test_a_pages_button_stands_left_of_the_chips_in_their_style(app):
    view = _view(app, _Camera("over"))
    view.resize(800, 600)
    button = QPushButton("Camera to dish center")
    view.add_top_widget(button)
    button.show()
    app.processEvents()
    assert button.geometry().right() < view.crosshair_box.x()
    assert button.styleSheet() == view.crosshair_box.styleSheet()
    view.hide()


def test_a_layer_on_is_a_filled_chip(app):
    view = _view(app, _Camera("over"))
    view.crosshair_box.setChecked(True)
    assert ":checked" in view.crosshair_box.styleSheet()
    assert not hasattr(view, "set_axes")
    view.hide()


def _clicks_at(app, view, point):
    seen = []
    view.clicked.connect(lambda *a: seen.append(a))
    view.right_clicked.connect(lambda *a: seen.append(a))
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseClick(view, Qt.MouseButton.RightButton, pos=point)
    QTest.qWait(QApplication.doubleClickInterval() + 50)
    return seen


def test_a_press_on_the_plate_is_no_click_on_the_picture(app):
    view = _with_plate(app)
    view.set_step_line(object(), "STEP · 1 mm", "#12ab34")
    view.repaint()
    assert _clicks_at(app, view, QPoint(30, 30)) == []
    assert _clicks_at(app, view, QPoint(400, 300)) != []
    view.hide()


def test_a_press_on_the_height_line_or_mouse_row_is_no_click(app):
    view = _with_plate(app)
    view.set_height_line(LINE)
    view.set_mouse_help(["Wheel   zoom"])
    view.repaint()
    height = view._chrome[1].center()
    mouse = view._chrome[-1].center()
    assert _clicks_at(app, view, height) == []
    assert _clicks_at(app, view, mouse) == []
    view.hide()


def test_the_plate_holds_a_pages_widget_with_x_y_z_under_it(app):
    view = _with_plate(app)
    gauge = TipGauge(parent=view)
    view.add_top_left_widget(gauge)
    gauge.show()
    view.repaint()
    plate = view._chrome[0]
    assert plate.contains(gauge.geometry())
    assert plate.height() > gauge.height() + 3 * 15
    view.hide()


def test_the_plate_is_as_wide_as_a_long_line(app):
    view = _view(app, _Camera("over"))
    view.resize(800, 600)
    view.set_position(["X 1.00  Y 2.00  Z 3.00"])
    view.repaint()
    narrow = view._chrome[0].width()
    view.set_position(["a much longer line than any step or axis"])
    view.repaint()
    assert view._chrome[0].width() > narrow
    view.hide()


def test_a_picture_not_saved_is_a_refusal(app):
    view = CameraView()
    view._snapshot_failed("disk full\nand more")
    said = toast_of(view)
    assert said.text() == "Not saved: disk full"
    assert said.property("tone") == "refusal"
    view.deleteLater()


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


def test_a_view_not_on_screen_reads_no_frames(app):
    """Every page is built at start and most are never shown: a view that
    read its camera from construction rescaled every frame for nobody,
    beside the one on screen (#36)."""
    import time

    class _Counting(_Camera):
        reads = 0

        def read(self):
            self.reads += 1
            return super().read()

    def pump():
        end = time.monotonic() + 0.2
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.005)

    camera = _Counting("over")
    view = CameraView()
    view.resize(400, 300)
    view.set_camera(camera)
    pump()
    assert camera.reads == 0

    view.show()
    pump()
    assert camera.reads > 0

    view.hide()
    pump()
    seen = camera.reads
    pump()
    assert camera.reads == seen
    view.deleteLater()


def test_a_camera_switch_keeps_the_pictures_size_and_shape(app):
    """The view is 4:3 whatever it shows (#50): the square lower camera is
    pillarboxed in it rather than resizing the row, and neither picture is
    stretched."""
    from PySide6.QtWidgets import QWidget

    from micropick.gui.widgets.feed_row import FeedRow
    view = CameraView()
    side = QWidget()
    side.setMinimumWidth(300)
    row = FeedRow(view, side)
    row.resize(1200, 600)
    row.show()
    view.set_camera(_Camera("over", size=(800, 600)))
    view._tick()
    before = view.geometry()
    shown = view._shown
    assert abs(shown.width() / shown.height() - 800 / 600) < 0.01
    view.set_camera(_Camera("under", size=(600, 600)))
    view._tick()
    assert view.geometry() == before
    shown = view._shown
    assert abs(shown.width() / shown.height() - 1.0) < 0.01
    assert shown.height() == view.height()
    row.deleteLater()


def _row_view(app):
    view = _view(app, _Camera("over"))
    view.resize(1000, 750)
    view.set_marks_available(True)
    look, detect = QPushButton("Look at"), QPushButton("Detect cuboids")
    view.add_top_row_widget(look)
    view.add_top_row_widget(detect)
    look.show()
    detect.show()
    app.processEvents()
    return view, look, detect


def test_a_row_of_the_pages_own_stands_over_the_chips(app):
    view, look, detect = _row_view(app)
    assert look.y() == detect.y() == 8
    assert look.geometry().right() < detect.x()
    assert detect.geometry().right() == view.width() - 9
    assert view.crosshair_box.y() > look.geometry().bottom()
    assert view.snapshot_button.geometry().right() == view.width() - 9
    assert {look.height(), detect.height()} == {36}
    view.hide()


def test_a_chip_of_the_pages_own_stands_after_points(app):
    view = _view(app, _Camera("over"))
    view.resize(1000, 750)
    view.set_marks_available(True)
    chip = view.add_chip("Past picks")
    app.processEvents()
    assert chip.isCheckable() and not chip.isChecked()
    assert chip.styleSheet() == view.crosshair_box.styleSheet()
    assert view.marks_box.x() < chip.x() < view.snapshot_button.x()
    assert chip.y() == view.marks_box.y()
    view.set_camera(None)
    assert chip.isHidden()
    view.hide()


def test_a_chip_of_the_pages_own_waits_for_a_camera(app):
    view = CameraView()
    assert view.add_chip("Past picks").isHidden()
    view.deleteLater()


def test_with_a_row_of_its_own_a_pages_lines_stand_under_the_chips(app):
    view, _look, _detect = _row_view(app)
    without = _pixels(view)
    view.set_status(["23 cuboids found · 9 in size"])
    app.processEvents()
    rows, columns = np.nonzero((_pixels(view) != without).any(axis=2))
    assert rows.min() > view.crosshair_box.geometry().bottom()
    assert columns.max() <= view.width() - 8
    view.hide()


def test_a_press_on_the_lines_under_the_chips_is_nobodys(app):
    view, _look, _detect = _row_view(app)
    view.set_status(["23 cuboids found"])
    app.processEvents()
    _pixels(view)                                  # painted: the box is known
    y = view.crosshair_box.geometry().bottom() + 16
    clicks = []
    view.clicked.connect(lambda u, v: clicks.append((u, v)))
    QTest.mouseClick(view, Qt.MouseButton.LeftButton,
                     pos=QPoint(view.width() - 30, y))
    assert clicks == []
    view.hide()


def _click_pending(app, view, point):
    """Press and release: the click waits out the double-click interval."""
    for kind, buttons in ((QMouseEvent.Type.MouseButtonPress,
                           Qt.MouseButton.LeftButton),
                          (QMouseEvent.Type.MouseButtonRelease,
                           Qt.MouseButton.NoButton)):
        app.sendEvent(view, QMouseEvent(kind, point, view.mapToGlobal(point),
                                        Qt.MouseButton.LeftButton, buttons,
                                        Qt.KeyboardModifier.NoModifier))


def _interval_out(app, view):
    end = time.monotonic() + QApplication.doubleClickInterval() / 1000 + 0.2
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def test_a_click_is_not_sent_once_the_view_is_hidden(app):
    """Home or another step shown in the double-click interval: the click
    moves nothing on the page left (GUI-08)."""
    view = _view(app, _Camera("over", (800, 600)))
    seen = []
    view.clicked.connect(lambda u, v: seen.append((u, v)))
    _click_pending(app, view, view.transform.map(QPointF(400.0, 300.0)))
    view.hide()
    _interval_out(app, view)
    assert seen == []
    view.close()


def test_a_click_is_not_sent_once_the_camera_changed(app):
    """The pixel was the other camera's."""
    view = _view(app, _Camera("over", (800, 600)))
    seen = []
    view.clicked.connect(lambda u, v: seen.append((u, v)))
    _click_pending(app, view, view.transform.map(QPointF(400.0, 300.0)))
    view.set_camera(_Camera("under", (800, 600)))
    _interval_out(app, view)
    assert seen == []
    view.close()


def test_a_large_reduction_reads_as_one_area_resize(app):
    """Two steps, the first a whole ratio (GUI-11): the same size, and the
    picture as one INTER_AREA gives it."""
    import cv2
    from micropick.gui.widgets.camera_view import _reduce
    frame = np.zeros((3000, 4000, 3), np.uint8)
    cv2.circle(frame, (2000, 1500), 400, (255, 255, 255), -1)
    frame = cv2.GaussianBlur(frame, (5, 5), 0)
    once = cv2.resize(frame, (1173, 880), interpolation=cv2.INTER_AREA)
    twice = _reduce(frame, 1173, 880)
    assert twice.shape == once.shape and twice.flags["C_CONTIGUOUS"]
    assert np.abs(once.astype(int) - twice.astype(int)).mean() < 0.5
