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


def test_the_sweep_parameters_are_the_ones_in_settings(app, session):
    from micropick.gui.pages.calibration_camera import (SWEEP_DEFAULTS,
                                                         sweep_parameters)
    assert sweep_parameters(session.settings) == SWEEP_DEFAULTS
    page = CameraCalibration(session)
    assert "6.8 mm" in page.parameters.text()
    session.set_settings(session.settings.model_copy(
        update={"sweep_grid_n": 9}))
    assert "grid 9" in page.parameters.text()
    page.deleteLater()


def _report(holdout=24.0, resid_max=60.0, side=6.772, frac=(0.9, 0.85)):
    from micropick.core.calibration.pixel_map import FitReport
    return FitReport(degree=3, n_poses=49, n_points=196, resid_mean_um=10.0,
                     resid_max_um=resid_max, holdout_mean_um=holdout,
                     holdout_max_um=80.0, track_side_mm=side,
                     coverage=(0, 0, 100, 100), coverage_frac=frac,
                     scale_centre_um=26.0, scale_edge_um=28.8)


def test_the_first_real_sweep_is_good():
    from micropick.gui.pages.calibration_camera import GOOD, judge
    verdict = judge(_report(), 6.8)
    assert verdict.level == GOOD
    assert verdict.line.startswith("Good:")
    assert "marker side 0.4 % off" in verdict.line


def test_the_worst_check_decides_and_is_named():
    from micropick.gui.pages.calibration_camera import (ACCEPTABLE, REDO,
                                                        judge)
    acceptable = judge(_report(side=6.7127), 6.8)          # degree 2's 1.3 %
    assert acceptable.level == ACCEPTABLE
    assert "marker side 1.3 % off (above 1 % off)" in acceptable.line
    redo = judge(_report(holdout=213.0, frac=(0.5, 0.9)), 6.8)   # affine
    assert redo.level == REDO
    assert "held-out error 213.0 µm (over 80 µm)" in redo.line
    assert "50 % of the frame covered (under 60 %)" in redo.line
    assert redo.line.endswith("sweep again.")


def test_without_a_recovered_side_the_side_is_not_judged():
    from micropick.gui.pages.calibration_camera import judge
    verdict = judge(_report(side=None), 6.8)
    assert all(c[0] != "marker side" for c in verdict.checks)
