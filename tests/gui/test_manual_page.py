"""Manual control: click modes, the lower camera, the deck mini-map and the
sizes after Detect cuboids."""

import os

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.detector import Detection                     # noqa: E402
from micropick.gui.pages.manual import ManualPage                # noqa: E402
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


@pytest.fixture
def page(app, session):
    p = ManualPage(session)
    yield p
    p.deleteLater()


def _detection(sizes):
    df = pd.DataFrame({"diameter_microns": np.asarray(sizes, dtype=float)})
    empty = df.iloc[0:0]
    return Detection(np.empty((0, 4)), np.empty(0), df, empty, empty, empty,
                     {}, classified=True, standin=False)


def test_detected_sizes_go_to_the_shared_histogram(page):
    page.session.profile.picking.cuboid_size_threshold = (250, 500)
    assert page.sizes.toggle.isChecked() is False          # folded
    page._cuboids = _detection([300, 320, 600])
    page._show_sizes()
    assert page.histogram.state.text().startswith(
        "2 of 3 cuboids are inside 250–500 µm")
    page._forget_targets()
    assert page.histogram.state.text() == "nothing measured yet"


W, H, K = 2592, 1944, 0.026


def _calibrate(session):
    """A linear toy map over the whole frame and a pipette offset."""
    from micropick.config.schema import PipetteOffset
    from micropick.config.schema import PixelMap as PixelMapConfig
    s = W / 2
    profile = session.profile
    profile.calibration.pixel_map = PixelMapConfig(
        degree=1, cu=W / 2, cv=H / 2, s=s,
        coef=[[0.0, K * s], [K * s, 0.0]], zero=[0.0, 0.0],
        ref=[W / 2, H / 2], bounds=[100.0, 100.0, W - 100.0, H - 100.0],
        image_size=[W, H], sweep_z=0.0)
    profile.calibration.pipette_offset = PipetteOffset(dx=10.0, dy=-5.0)


def _open_upper(app, page, session):
    session.open_camera(session.upper_camera_label)
    app.processEvents()
    page._refresh_cameras()


def _wait(app, page):
    import time
    end = time.monotonic() + 5
    while page.jog.busy and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def test_tip_mode_sends_the_tip_over_the_clicked_point(app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    _wait(app, page)
    page.armed.setChecked(True)
    page.click_tip.setChecked(True)
    session.robot.move_to_coordinates((150.0, 150.0, 90.0), verbose=False)
    x0, y0, _ = session.robot.get_position(verbose=False)[0].values()
    # 100 px right of the reference pixel is 2.6 mm in x; the tip is the
    # pipette offset further.
    page._clicked(W / 2 + 100, H / 2)
    _wait(app, page)
    pos = session.robot.get_position()[0]
    assert pos["x"] == pytest.approx(x0 + 100 * K + 10.0, abs=0.05)
    assert pos["y"] == pytest.approx(y0 - 5.0, abs=0.05)


def test_tip_mode_refuses_outside_the_map(app, page, session):
    _calibrate(session)
    _open_upper(app, page, session)
    _wait(app, page)
    page.armed.setChecked(True)
    page.click_tip.setChecked(True)
    before = dict(session.robot.get_position()[0])
    page._clicked(20, 20)                       # outside the bounds
    _wait(app, page)
    assert dict(session.robot.get_position()[0]) == pytest.approx(before)
    assert "outside the calibrated area" in page.jog.position_lines[-1]
