"""The stand-in tip detector: the arithmetic the mock calibration converges on.

No Qt here. The stand-in is judged by running the real routine against it on
a MockRobot with fabricated cameras: the offset it saves must be the starting
one plus the stand-in's fixed error, plus whatever a touch-up moved.
"""

import numpy as np
import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

from micropick.config.schema import (Calibration, CameraSpec,  # noqa: E402
                                     PickingConfig, ProfileMeta, TipTarget)
from micropick.config.schema import PixelMap as PixelMapConfig  # noqa: E402
from micropick.config.store import Profile                    # noqa: E402
from micropick.core.calibration.pixel_map import PixelMap    # noqa: E402
from micropick.gui.tip_detector import (STANDIN_ERROR_MM,    # noqa: E402
                                        StandInTipDetector,
                                        load_tip_detector)
from micropick.hardware.mock import MockRobot                # noqa: E402
from micropick.workflows.calibrate_pipette import (           # noqa: E402
    calibrate_pipette_offset)


class _Camera:
    def __init__(self, label, size):
        self.label = label
        self.resolution = size
        self.crop = 1.0

    def read_after(self, t, **_):
        return np.zeros((self.resolution[1], self.resolution[0], 3), np.uint8)


def _identity_map(size):
    """A degree-3 map with every coefficient zero: any pixel maps to the
    gantry pose, so to_robot(centre) is where the robot stands. Enough for
    the routine's geometry, and it covers the whole frame."""
    w, h = size
    return PixelMapConfig(degree=3, cu=w / 2, cv=h / 2, s=1000.0,
                          coef=[[0.0, 0.0]] * 9, zero=[0.0, 0.0],
                          ref=[w / 2, h / 2], bounds=[0.0, 0.0, w, h],
                          image_size=[w, h], sweep_z=100.0)


def _profile(tmp_path, size=(640, 480)):
    profile = Profile(
        meta=ProfileMeta(name="t"),
        calibration=Calibration(pixel_map=_identity_map(size),
                                tip_target=TipTarget()),
        picking=PickingConfig(),
        cameras={"over": CameraSpec(device_name="o", default_resolution=list(size),
                                    resolutions=[list(size)]),
                 "under": CameraSpec(device_name="u", default_resolution=list(size),
                                     resolutions=[list(size)])},
        positions={}, path=tmp_path)
    profile.save()
    return profile


def test_standin_converges_on_start_plus_error(tmp_path):
    profile = _profile(tmp_path)
    robot = MockRobot(position=(150.0, 150.0, 67.1), noise_mm=0.0)
    over, under = _Camera("over", (640, 480)), _Camera("under", (640, 480))
    detector = StandInTipDetector(robot, profile.calibration.tip_target)
    pmap = PixelMap.from_config(profile.pixel_map)
    lines = []
    result = calibrate_pipette_offset(
        robot, over, under, detector, pmap, target=profile.calibration.tip_target,
        current_offset=(16.0, 60.0), frames=3, settle_s=0.0, profile=profile,
        log=lines.append)
    dx, dy = STANDIN_ERROR_MM
    assert result.offset.dx == pytest.approx(16.0 + dx, abs=1e-6)
    assert result.offset.dy == pytest.approx(60.0 + dy, abs=1e-6)
    assert result.residual_mm == pytest.approx(0.0, abs=1e-6)
    assert result.offset.method == "auto"
    assert profile.calibration.pipette_offset.dx == result.offset.dx


def test_touch_up_moves_are_part_of_the_offset(tmp_path):
    profile = _profile(tmp_path)
    robot = MockRobot(position=(150.0, 150.0, 67.1), noise_mm=0.0)
    over, under = _Camera("over", (640, 480)), _Camera("under", (640, 480))
    detector = StandInTipDetector(robot, profile.calibration.tip_target)
    pmap = PixelMap.from_config(profile.pixel_map)

    def nudge(robot, camera, view):
        robot.move_relative("x", 0.05, verbose=False)

    result = calibrate_pipette_offset(
        robot, over, under, detector, pmap, target=profile.calibration.tip_target,
        current_offset=(16.0, 60.0), frames=3, settle_s=0.0, profile=profile,
        manual_touch_up=nudge, log=lambda *_: None)
    assert result.offset.dx == pytest.approx(16.0 + STANDIN_ERROR_MM[0] + 0.05, abs=1e-6)
    assert result.offset.method == "auto+manual"


def test_loader_names_the_missing_weights(tmp_path, monkeypatch):
    profile = _profile(tmp_path)
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="tip_detector_v1.pt"):
        load_tip_detector(profile)
    standin = load_tip_detector(profile, mock=True, robot=MockRobot())
    assert standin.is_standin
