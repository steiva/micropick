"""The tip calibration refuses what it cannot trust, and shows what it saw.

A real `TipDetector` over a fake model: the five crosshairs of the disc at the
frame's center, the tip where each test puts it. The routine runs on a
MockRobot, so the refusals are judged by what was saved (nothing) and the
pictures by the files written.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from micropick.config.schema import (Calibration, CameraSpec, PickingConfig,
                                     ProfileMeta, TipTarget)
from micropick.config.schema import PixelMap as PixelMapConfig
from micropick.config.store import Profile, load_profile
from micropick.core.calibration.pixel_map import PixelMap
from micropick.hardware.mock import MockRobot
from micropick.viz.tip_views import view_items
from micropick.viz.overlays import Rect, Text
from micropick.workflows.calibrate_pipette import (Detection, TipDetector,
                                                   TipCalibrationError,
                                                   calibrate_pipette_offset)

W, H = 640, 480
R = 100                      # crosshair radius in pixels: 0.2025 mm/px


class _Camera:
    def __init__(self, label):
        self.label = label
        self.resolution = (W, H)

    def read_after(self, _t, **_):
        return np.full((H, W, 3), 80, np.uint8)


def _box(label, x, y, conf, half=8):
    return SimpleNamespace(xyxy=[(x - half, y - half, x + half, y + half)],
                           cls=[0 if label == "point" else 1], conf=[conf])


class _Model:
    """The disc at the center, and a tip from `tip_at(frame index)`."""

    names = {0: "point", 1: "tip"}

    def __init__(self, tip_at):
        self.tip_at = tip_at
        self.calls = 0

    def predict(self, **_):
        cx, cy = W / 2, H / 2
        boxes = [_box("point", cx + dx, cy + dy, 0.9)
                 for dx, dy in ((0, 0), (R, 0), (-R, 0), (0, R), (0, -R))]
        tip = self.tip_at(self.calls)
        self.calls += 1
        if tip is not None:
            boxes.append(_box("tip", *tip, 0.6))
        return [SimpleNamespace(boxes=boxes)]


def _profile(tmp_path):
    pmap = PixelMapConfig(degree=3, cu=W / 2, cv=H / 2, s=1000.0,
                          coef=[[0.0, 0.0]] * 9, zero=[0.0, 0.0],
                          ref=[W / 2, H / 2], bounds=[0.0, 0.0, W, H],
                          image_size=[W, H], sweep_z=100.0)
    spec = CameraSpec(device_name="d", default_resolution=[W, H],
                      resolutions=[[W, H]])
    profile = Profile(meta=ProfileMeta(name="t"),
                      calibration=Calibration(pixel_map=pmap,
                                              tip_target=TipTarget()),
                      picking=PickingConfig(),
                      cameras={"over": spec, "under": spec},
                      positions={}, path=tmp_path)
    profile.save()
    return profile


def _run(tmp_path, tip_at, **kw):
    profile = _profile(tmp_path)
    detector = TipDetector(_Model(tip_at), imgsz=W)
    lines = []
    images = tmp_path / "pictures"
    call = lambda: calibrate_pipette_offset(            # noqa: E731
        MockRobot(position=(150.0, 150.0, 67.1), noise_mm=0.0),
        _Camera("over"), _Camera("under"), detector,
        PixelMap.from_config(profile.pixel_map),
        target=profile.calibration.tip_target, current_offset=(16.0, 60.0),
        frames=5, settle_s=0.0, profile=profile, images_to=images,
        log=lines.append, **kw)
    return profile, images, lines, call


def test_a_tip_found_in_different_places_is_refused_before_moving(tmp_path):
    # The upper camera's 5 frames, then the lower's: there the tip wanders
    # 60 px per frame, 12 mm apart (the 43 mm calibration of 2026-11-09).
    profile, images, _lines, call = _run(
        tmp_path, lambda i: (100 + 60 * (i - 5), 100) if i >= 5 else None)
    with pytest.raises(TipCalibrationError, match="different places"):
        call()
    assert profile.calibration.pipette_offset is None
    names = sorted(p.name for p in images.iterdir())
    assert names == ["1_upper.jpg", "1_upper_detections.jpg",
                     "2_lower.jpg", "2_lower_detections.jpg"]


def test_a_check_finding_the_tip_far_off_is_refused(tmp_path):
    # Steady, but where the gantry goes does not move it: after the
    # correction the check finds it 20 px = 4 mm off still.
    profile, images, _lines, call = _run(
        tmp_path, lambda i: (W / 2 + 20, H / 2) if i >= 5 else None)
    with pytest.raises(TipCalibrationError, match="from the crosshair"):
        call()
    assert profile.calibration.pipette_offset is None
    assert (images / "3_check_detections.jpg").is_file()


def test_a_reading_that_fails_still_leaves_its_pictures(tmp_path):
    profile, images, _lines, call = _run(tmp_path, lambda i: None)
    with pytest.raises(TipCalibrationError, match="not detected"):
        call()
    assert (images / "2_lower_detections.jpg").is_file()


def test_every_tip_box_is_drawn_with_its_frame_and_confidence():
    frames = [[Detection("point", np.array([10.0, 10.0]), 0.9, (5, 5, 15, 15)),
               Detection("tip", np.array([50.0, 50.0]), 0.3, (45, 45, 55, 55)),
               Detection("tip", np.array([80.0, 50.0]), 0.7, (75, 45, 85, 55))],
              [Detection("tip", np.array([200.0, 50.0]), 0.5)]]
    items = view_items((480, 640, 3), frames, tip=np.array([60.0, 50.0]),
                       lines=["2_lower"])
    labels = [i.text for i in items if isinstance(i, Text)]
    assert labels[:3] == ["f1 tip 0.30", "f1 tip 0.70", "f2 tip 0.50"]
    rects = [i for i in items if isinstance(i, Rect) and not i.fill]
    assert len(rects) == 4
    # The tip a frame kept is drawn thicker than the one it passed over.
    assert rects[2].thickness > rects[1].thickness


# -- by hand -------------------------------------------------------------------

def _start_by_hand(tmp_path, tip_at, *, under_finds_disc=True, **kw):
    from micropick.workflows.calibrate_pipette import start_by_hand
    profile = _profile(tmp_path)
    model = _Model(tip_at)
    if not under_finds_disc:
        upper_predict = model.predict

        def predict(**k):
            # The upper camera's 5 frames, then nothing.
            if model.calls >= 5:
                model.calls += 1
                return [SimpleNamespace(boxes=[])]
            return upper_predict(**k)
        model.predict = predict
    robot = MockRobot(position=(150.0, 150.0, 67.1), noise_mm=0.0)
    readings = []

    def on_frame(name, index, _frame, _dets):
        # Each frame as it is detected; a reading named once.
        if index == 1:
            readings.append(name)
    start = start_by_hand(
        robot, _Camera("over"), _Camera("under"),
        TipDetector(model, imgsz=W), PixelMap.from_config(profile.pixel_map),
        target=profile.calibration.tip_target, current_offset=(16.0, 60.0),
        frames=5, settle_s=0.0, profile=profile, log=lambda _l: None,
        on_frame=on_frame, on_move=lambda: readings.append("move"), **kw)
    return profile, robot, start, readings


def test_by_hand_leaves_the_tip_where_the_offset_puts_it_and_saves_nothing(
        tmp_path):
    # No tip is ever found: by hand never looks for one.
    profile, robot, start, readings = _start_by_hand(tmp_path, lambda i: None)
    # The disc is at the frame's center, the picture's center maps to the
    # pose the reading was taken at: the crosshair is at (150, 150).
    assert np.allclose(start.target_xy, (150.0, 150.0), atol=1e-6)
    pose = robot.get_position(verbose=False)[0]
    assert (round(pose["x"], 3), round(pose["y"], 3), round(pose["z"], 1)) \
        == (166.0, 210.0, 67.1)
    assert readings == ["1_upper", "move", "2_lower", "move"]
    assert start.under_view is not None
    assert profile.calibration.pipette_offset is None


def test_by_hand_without_the_disc_in_the_lower_camera_carries_on(tmp_path):
    _profile_, robot, start, readings = _start_by_hand(
        tmp_path, lambda i: None, under_finds_disc=False)
    assert start.under_view is None
    assert readings == ["1_upper", "move", "2_lower", "move"]
    pose = robot.get_position(verbose=False)[0]
    assert (round(pose["x"], 3), round(pose["y"], 3)) == (166.0, 210.0)


def test_the_offset_by_hand_is_where_the_tip_was_left(tmp_path):
    from micropick.workflows.calibrate_pipette import save_by_hand
    profile, _robot, start, _readings = _start_by_hand(tmp_path, lambda i: None)
    offset = save_by_hand(start, (166.5, 209.25), profile=profile,
                          tip_type="vwr", robot_session="r1", tip_pickup="p1")
    assert (round(offset.dx, 3), round(offset.dy, 3)) == (16.5, 59.25)
    assert offset.method == "manual"
    assert offset.residual_mm is None
    assert offset.tip_type == "vwr"
    saved = load_profile("t", directory=tmp_path).calibration.pipette_offset
    assert (round(saved.dx, 3), saved.method) == (16.5, "manual")


def test_each_frame_is_handed_on_as_it_is_detected(tmp_path):
    frames = []
    _profile_, _images, _lines, call = _run(
        tmp_path, lambda i: (W / 2 + 40, H / 2) if i >= 5 else None,
        on_frame=lambda name, i, frame, dets: frames.append(
            (name, i, frame.shape, len(dets))))
    with pytest.raises(TipCalibrationError):
        call()          # the check finds the tip where it was: refused
    assert [f[:2] for f in frames[:6]] == [("1_upper", i) for i in range(1, 6)] \
        + [("2_lower", 1)]
    assert frames[0][2:] == ((H, W, 3), 5)
    assert frames[5][3] == 6                     # the tip as well
