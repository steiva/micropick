"""End-to-end tests for the picking session on mocks and synthetic frames.

No robot, no real YOLO. A `Dish` holds cuboids at known deck positions and
renders them as bright squares; a `SceneYOLO` "detects" the ones still present;
a `SceneRobot` (a MockRobot) removes the cuboid under the tip when it aspirates,
so a successful pickup actually makes a cuboid disappear from the next frame.
That is what lets the whole IDLE -> COMPLETED loop, the miss accounting and the
disk-resume all be exercised with nothing attached, in the spirit of DESIGN §8.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import cv2
import numpy as np

from micropick.config.schema import (Calibration, CameraHomography,
                                      PickingConfig, PipetteOffset, ProfileMeta)
from micropick.config.schema import PixelMap as PixelMapConfig
from micropick.config.store import Profile
from micropick.core.calibration.pixel_map import PixelMap
from micropick.core.routine import Destination, Routine
from micropick.hardware.mock import MockRobot, open_mock_camera
from micropick.workflows.picking import PickingSession, RobotState

W, H = 900, 700
CU, CV = W / 2, H / 2
K = 0.05                    # mm per pixel of the toy linear map
OBSERVE = (150.0, 150.0, 80.0)
OFFSET = (1.0, -2.0)        # pipette offset dx, dy


# ---------------------------------------------------------------------------
# a linear pixel map: to_robot(u,v,g) = g + K*(u-cu, v-cv)
# ---------------------------------------------------------------------------

def linear_pixel_map() -> PixelMap:
    s = max(W, H) / 2.0
    cfg = PixelMapConfig(
        degree=1, cu=CU, cv=CV, s=s,
        coef=[[0.0, K * s], [K * s, 0.0]], zero=[0.0, 0.0], ref=[CU, CV],
        bounds=[0.0, 0.0, float(W), float(H)], image_size=[W, H], sweep_z=0.0,
    )
    return PixelMap(cfg)


# ---------------------------------------------------------------------------
# the dish scene
# ---------------------------------------------------------------------------

class Dish:
    def __init__(self, half: int = 18):
        self.half = half
        self._cuboids: dict[int, np.ndarray] = {}
        self._miss: set[int] = set()
        self._lock = threading.Lock()
        self.observe = np.array(OBSERVE[:2], dtype=float)
        self.offset = np.array(OFFSET, dtype=float)

    def add(self, cid: int, pixel_xy, will_miss: bool = False):
        u, v = pixel_xy
        deck = self.observe + K * np.array([u - CU, v - CV]) + self.offset
        with self._lock:
            self._cuboids[cid] = deck
            if will_miss:
                self._miss.add(cid)

    def _pixel_of(self, deck):
        uv = (deck - self.observe - self.offset) / K + np.array([CU, CV])
        return uv

    def snapshot(self):
        with self._lock:
            return [(cid, self._pixel_of(deck))
                    for cid, deck in self._cuboids.items()]

    def render(self, i):
        frame = np.full((H, W, 3), 20, np.uint8)          # dark dish
        for _cid, (u, v) in self.snapshot():
            u, v = int(u), int(v)
            cv2.rectangle(frame, (u - self.half, v - self.half),
                          (u + self.half, v + self.half), (220, 220, 220), -1)
        return frame

    def remove_near(self, xy, tol=2.5):
        with self._lock:
            best, best_d = None, tol
            for cid, deck in self._cuboids.items():
                if cid in self._miss:
                    continue
                d = float(np.hypot(deck[0] - xy[0], deck[1] - xy[1]))
                if d < best_d:
                    best, best_d = cid, d
            if best is not None:
                del self._cuboids[best]


class _Arr:
    def __init__(self, a):
        self._a = np.asarray(a, dtype=float)

    def cpu(self):
        return self

    def numpy(self):
        return self._a


class _Boxes:
    def __init__(self, xyxy, conf):
        self.xyxy = _Arr(xyxy)
        self.conf = _Arr(conf)


class _Result:
    def __init__(self, boxes):
        self.boxes = boxes


class SceneYOLO:
    def __init__(self, dish: Dish):
        self.dish = dish

    def predict(self, source, **kw):
        boxes, confs = [], []
        h = self.dish.half + 2
        for _cid, (u, v) in self.dish.snapshot():
            boxes.append([u - h, v - h, u + h, v + h])
            confs.append(0.9)
        if not boxes:
            return [_Result(_Boxes(np.zeros((0, 4)), np.zeros((0,))))]
        return [_Result(_Boxes(boxes, confs))]


class SceneRobot(MockRobot):
    """A MockRobot that empties the well under the tip on aspirate."""

    def __init__(self, dish: Dish, **kw):
        super().__init__(position=(*OBSERVE,), **kw)
        self.dish = dish

    def aspirate_in_place(self, volume, flow_rate, verbose=False):
        super().aspirate_in_place(volume, flow_rate)
        self.dish.remove_near(self._pos[:2])


# ---------------------------------------------------------------------------
# assembling a session
# ---------------------------------------------------------------------------

def relaxed_config(**over) -> PickingConfig:
    """Shape/size windows opened wide: the vision maths is tested elsewhere; here
    we exercise the state machine, so every detected square should be pickable."""
    base = dict(
        cuboid_size_threshold=(1, 100_000_000),
        aspect_ratio_window=(0.5, 5.0),
        circularity_window=(0.05, 1.0),
        min_solidity=0.0, max_radial_cv=100.0,
        circle_center=(int(CU), int(CV)), circle_radius=100_000,
        minimum_distance=0.0,
        floater_check_interval=999, floater_clip_sec=0.0,
        capture_settle_s=0.0, verify_settle_s=0.0, wait_time_after_deposit=0.0,
        vol=10.0, flow_rate=50.0, max_batch=10, max_shake_retries=3,
    )
    base.update(over)
    return PickingConfig(**base)


def make_profile(cfg: PickingConfig) -> Profile:
    return Profile(
        meta=ProfileMeta(name="test"),
        calibration=Calibration(pipette_offset=PipetteOffset(dx=OFFSET[0],
                                                             dy=OFFSET[1])),
        picking=cfg, cameras={},
        positions={"observe": OBSERVE, "shake": (335.5, 223.0, 66.5)},
        path=Path("/tmp/does-not-matter"),
    )


def make_session(dish: Dish, routine: Routine, cfg: PickingConfig | None = None,
                 *, profile=None, **session_kw):
    cfg = cfg or relaxed_config()
    robot = SceneRobot(dish, noise_mm=0.0, speed_mm_s=400.0)
    camera = open_mock_camera(width=W, height=H, fps=120.0, render=dish.render)
    session = PickingSession(robot, camera, linear_pixel_map(),
                             profile or make_profile(cfg), routine,
                             SceneYOLO(dish), **session_kw)
    return session, robot, camera


def drive(session, *, max_steps=200, pause=None, stop=None, until=None):
    events = []
    for _ in range(max_steps):
        ev = session.step(pause=pause, stop=stop)
        events.append(ev)
        if until is not None and until(ev):
            break
        if session.done or session.state is RobotState.NEEDS_OPERATOR:
            break
    return events


def aspirates(robot):
    return [c for c in robot.calls if c[0] == "aspirate_in_place"]


def dispenses_in_place(robot):
    return [c for c in robot.calls if c[0] == "dispense_in_place"]


# ---------------------------------------------------------------------------
# 1. full cycle
# ---------------------------------------------------------------------------

def test_full_cycle_to_completion():
    dish = Dish()
    dish.add(1, (300, 350))
    dish.add(2, (600, 350))
    targets = [(200.0, 200.0), (210.0, 210.0)]
    routine = Routine(Destination.coordinates(targets),
                      {targets[0]: 1, targets[1]: 1})
    session, robot, camera = make_session(dish, routine)
    try:
        drive(session)
        assert session.state is RobotState.COMPLETED
        assert routine.is_done()
        assert len(aspirates(robot)) == 2          # one per target
        assert len(dispenses_in_place(robot)) == 2  # one deposit per target
    finally:
        camera.close()


# ---------------------------------------------------------------------------
# 2. pause / stop between moves inside one state
# ---------------------------------------------------------------------------

def _run_to_state(session, target_state, max_steps=50):
    for _ in range(max_steps):
        if session.state is target_state:
            return
        session.step()
    raise AssertionError(f"never reached {target_state}")


def test_stop_interrupts_mid_batch():
    dish = Dish()
    for cid, x in enumerate((250, 450, 650), start=1):
        dish.add(cid, (x, 350))
    routine = Routine(Destination.coordinates([(200.0, 200.0)]),
                      {(200.0, 200.0): 3})
    session, robot, camera = make_session(dish, routine)
    try:
        _run_to_state(session, RobotState.PICKUP_SAMPLE)
        stop = threading.Event()

        def watcher():
            while len(aspirates(robot)) < 1:
                time.sleep(0.001)
            stop.set()

        t = threading.Thread(target=watcher)
        t.start()
        ev = session.step(stop=stop)
        t.join()

        assert ev.state is RobotState.CANCELED
        assert 1 <= len(aspirates(robot)) < 3      # stopped mid-batch
    finally:
        camera.close()


def test_pause_blocks_then_resumes():
    dish = Dish()
    for cid, x in enumerate((250, 450, 650), start=1):
        dish.add(cid, (x, 350))
    routine = Routine(Destination.coordinates([(200.0, 200.0)]),
                      {(200.0, 200.0): 3})
    session, robot, camera = make_session(dish, routine)
    try:
        _run_to_state(session, RobotState.PICKUP_SAMPLE)
        pause = threading.Event()
        pause.set()                                # paused before the first move

        def release():
            time.sleep(0.3)
            pause.clear()

        t = threading.Thread(target=release)
        t.start()
        start = time.monotonic()
        ev = session.step(pause=pause)
        elapsed = time.monotonic() - start
        t.join()

        assert ev.state is RobotState.VERIFY_PICKUP  # completed the batch
        assert len(aspirates(robot)) == 3
        assert elapsed >= 0.25                        # it actually waited
    finally:
        camera.close()


# ---------------------------------------------------------------------------
# 3. interrupted then recreated session resumes from disk
# ---------------------------------------------------------------------------

def test_resume_from_disk_continues_routine(tmp_path):
    path = tmp_path / "run.json"
    targets = [(200.0, 200.0), (210.0, 210.0)]
    plan = {targets[0]: 1, targets[1]: 1}

    # session A: fill the first target, then walk away
    dish_a = Dish()
    dish_a.add(1, (300, 350))
    routine_a = Routine(Destination.coordinates(targets), plan, path=path)
    session_a, robot_a, cam_a = make_session(dish_a, routine_a)
    try:
        drive(session_a, until=lambda ev: ev.kind == "transferred")
    finally:
        cam_a.close()
    assert routine_a.remaining(targets[0]) == 0
    assert routine_a.remaining(targets[1]) == 1

    # a brand new routine loaded from disk already knows the first is done
    routine_b = Routine.load(path)
    assert routine_b.remaining(targets[0]) == 0

    # session B on a fresh dish finishes only what is left
    dish_b = Dish()
    dish_b.add(9, (600, 350))
    session_b, robot_b, cam_b = make_session(dish_b, routine_b)
    try:
        drive(session_b)
        assert session_b.state is RobotState.COMPLETED
    finally:
        cam_b.close()
    assert routine_b.is_done()
    assert len(aspirates(robot_b)) == 1            # B did not redo target 0


# ---------------------------------------------------------------------------
# 4. fix coverage
# ---------------------------------------------------------------------------

def test_needs_operator_after_max_shakes():
    dish = Dish()                                   # empty: nothing to pick
    routine = Routine(Destination.coordinates([(200.0, 200.0)]),
                      {(200.0, 200.0): 1})
    session, robot, camera = make_session(dish, routine)
    try:
        drive(session)
        assert session.state is RobotState.NEEDS_OPERATOR
        assert session._shake_retries == 3
        session.resume()
        assert session.state is RobotState.CAPTURE_FRAME
    finally:
        camera.close()


def test_keep_successful_dispenses_proportional_volume():
    dish = Dish()
    dish.add(1, (300, 350))                         # picked cleanly
    dish.add(2, (600, 350), will_miss=True)         # stays -> a miss
    routine = Routine(Destination.coordinates([(200.0, 200.0)]),
                      {(200.0, 200.0): 2})
    cfg = relaxed_config(miss_policy="keep_successful")
    session, robot, camera = make_session(dish, routine, cfg)
    try:
        # stop after the first pick cycle; the permanent miss would otherwise
        # keep the target open and it would be retried indefinitely
        drive(session, until=lambda ev: ev.kind == "transferred")
        # one held (10 ul into the well), one missed (10 ul back to the dish),
        # so the well concentration is unchanged
        volumes = sorted(c[1] for c in dispenses_in_place(robot))
        assert volumes == [10.0, 10.0]
        prog = routine._progress[(200.0, 200.0)]
        assert prog.delivered == 1 and prog.missed == 1
    finally:
        camera.close()


def test_verify_uses_full_detection_frame_not_pickable():
    # A cuboid the pipette knocked out of the shape window is gone from
    # `pickable` but still in `cuboid_df`; verify must still call it a miss.
    dish = Dish()
    routine = Routine(Destination.coordinates([(200.0, 200.0)]),
                      {(200.0, 200.0): 1})
    session, _robot, camera = make_session(dish, routine)
    try:
        import pandas as pd
        session._choice = pd.DataFrame({"cX": [400.0], "cY": [350.0]})
        session.cuboid_df = pd.DataFrame({"cX": [401.0], "cY": [351.0]})
        session.pickable = pd.DataFrame(columns=["cX", "cY"])   # filtered out
        assert session._count_misses() == 1
    finally:
        camera.close()


# ---------------------------------------------------------------------------
# 5. optional lower-camera recording
# ---------------------------------------------------------------------------

import pytest

from micropick.workflows.picking import PickingError


def _one_target_run():
    dish = Dish()
    dish.add(1, (300, 350))
    routine = Routine(Destination.coordinates([(200.0, 200.0)]),
                      {(200.0, 200.0): 1})
    return dish, routine


def test_recording_off_creates_no_recorder():
    dish, routine = _one_target_run()
    session, robot, camera = make_session(dish, routine)   # no under_cam/clip_dir
    try:
        assert session._recorder is None
        drive(session)
        assert session.state is RobotState.COMPLETED
    finally:
        camera.close()


def test_clip_dir_without_under_cam_raises():
    dish, routine = _one_target_run()
    with pytest.raises(PickingError):
        make_session(dish, routine, clip_dir="/tmp/whatever")


def test_recording_on_saves_a_clip(tmp_path):
    dish, routine = _one_target_run()
    under = open_mock_camera(width=320, height=240, fps=120.0)
    session, robot, camera = make_session(dish, routine, under_cam=under,
                                          clip_dir=tmp_path)
    saved = []
    session._recorder.save_async = lambda path, **kw: saved.append(path)
    try:
        assert session._recorder is not None
        drive(session)
        assert session.state is RobotState.COMPLETED
        assert len(saved) == 1
        assert str(tmp_path) in saved[0] and saved[0].endswith(".mp4")
        assert not session._recorder.recording        # stopped after the clip
    finally:
        session.close()
        under.close()
        camera.close()


def _profile_with_homography(cfg, matrix, gantry):
    profile = make_profile(cfg)
    profile.calibration.homography = CameraHomography(
        matrix=matrix, gantry_xy=list(gantry))
    return profile


def test_roi_marked_only_with_valid_homography(tmp_path):
    dish, routine = _one_target_run()
    under = open_mock_camera(width=320, height=240, fps=120.0)
    # identity map, valid at the observation pose (150, 150)
    profile = _profile_with_homography(
        relaxed_config(), [[1, 0, 0], [0, 1, 0], [0, 0, 1]], (150.0, 150.0))
    session, robot, camera = make_session(
        dish, routine, profile=profile, under_cam=under, clip_dir=tmp_path)
    session._recorder.save_async = lambda path, **kw: None
    try:
        drive(session)
        assert session._recorder._roi is not None       # box was placed
    finally:
        session.close()
        under.close()
        camera.close()


def test_recording_without_homography_has_no_box(tmp_path):
    dish, routine = _one_target_run()
    under = open_mock_camera(width=320, height=240, fps=120.0)
    session, robot, camera = make_session(dish, routine, under_cam=under,
                                          clip_dir=tmp_path)   # no homography
    session._recorder.save_async = lambda path, **kw: None
    try:
        drive(session)
        assert session.state is RobotState.COMPLETED    # recorded, just no box
        assert session._recorder._roi is None
    finally:
        session.close()
        under.close()
        camera.close()
