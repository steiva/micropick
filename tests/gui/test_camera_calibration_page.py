"""The camera calibration page: one page, the upper camera only."""

import os
import time

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.pages.calibration_camera import (             # noqa: E402
    CameraCalibration)
from micropick.gui.session import Session                        # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def session(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    s = Session(Options(mock=True))
    s.load_profile("mock")
    s.probe_robot()
    s.new_run()
    yield s
    s.shutdown()


def test_only_the_upper_camera_is_offered(app, session):
    page = CameraCalibration(session)
    session.open_camera(session.upper_camera_label)
    session.open_camera(session.lower_camera_label)
    app.processEvents()
    offered = [page.camera_choice.itemText(i)
               for i in range(page.camera_choice.count())]
    assert offered == [session.upper_camera_label]
    page.deleteLater()


def test_the_gate_says_the_upper_camera_is_missing(app, session):
    page = CameraCalibration(session)
    assert not hasattr(page, "stack")
    assert not page.start_button.isEnabled()
    assert "upper camera" in page.checks.text()
    page.deleteLater()


def test_the_parameters_start_at_the_defaults_and_reset_to_them(app, session):
    from micropick.gui.pages.calibration_camera import SWEEP_DEFAULTS
    page = CameraCalibration(session)

    def shown():
        return {"marker_side_mm": page.marker_side.value(),
                "dictionary": page.dictionary.currentText(),
                "grid_n": page.grid_n.value(), "degree": page.degree.value()}

    assert shown() == SWEEP_DEFAULTS
    page.marker_side.setValue(10.0)
    page.grid_n.setValue(9)
    page.dictionary.setCurrentText("DICT_4X4_50")
    page.defaults_button.click()
    assert shown() == SWEEP_DEFAULTS
    page.deleteLater()
