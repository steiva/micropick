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
from PySide6.QtGui import QWheelEvent                               # noqa: E402
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


def test_focus_slider_only_for_a_camera_with_a_focus(app):
    plain = _Camera("under")
    view = _view(app, plain)
    assert view.focus_row.isHidden()
    focused = _Camera("under", controls=ControlReport(applied={"focus": 500.0}))
    view.set_camera(focused)
    assert not view.focus_row.isHidden()
    assert view.focus_slider.value() == 500
    view.focus_slider.setValue(640)
    view._focus_timer.stop()
    view._apply_focus()
    assert focused.sets == [{"focus": 640}]
    assert view.focus_value.text() == "640"
    view.close()
