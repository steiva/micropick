"""Shared setup for the session notebooks.

Both `01_robot_session` and `02_bench_checks` do `from bench_setup import *`
instead of repeating the imports and the bring-up boilerplate. Importing this
module fixes `MICROPICK_ROOT` first (from its own location, one level above the
notebooks) so `micropick` resolves the repository whatever the working
directory, then re-exports the common names and a handful of helpers.

Nothing here opens a device on import; the helpers do that on request.
"""

from __future__ import annotations

import os as _os
from pathlib import Path as _Path

# Point the package at the repository root before importing it, so a fresh
# kernel needs no manual environment setup. setdefault, so an explicit override
# still wins.
_os.environ.setdefault("MICROPICK_ROOT", str(_Path(__file__).resolve().parent.parent))

import json          # noqa: E402
import os            # noqa: E402
import time          # noqa: E402

import cv2           # noqa: E402
import numpy as np   # noqa: E402

from opentrons_api import ot2_api                                  # noqa: E402

from micropick import paths                                        # noqa: E402
from micropick.config import store                                 # noqa: E402
from micropick.config.labware import (LabwareError,                # noqa: E402
                                      local_definitions,
                                      resolve_definition)
from micropick.config.schema import (Calibration, CameraSpec,       # noqa: E402
                                      PickingConfig, PipetteOffset,
                                      ProfileMeta)
from micropick.config.schema import PixelMap as _PixelMapConfig      # noqa: E402
from micropick.config.store import Profile                          # noqa: E402
from micropick.core import routine as rt                           # noqa: E402
from micropick.core.calibration.homography import Homography       # noqa: E402
from micropick.core.calibration.pixel_map import PixelMap, compare_degrees  # noqa: E402
from micropick.core.routine import (Destination, Routine,          # noqa: E402
                                    RoutineError, empty_plate_table,
                                    plan_from_table)
from micropick.core.vision import cuboids as vision                # noqa: E402
from micropick.core.vision.standin import (StandInDetector,        # noqa: E402
                                            blob_boxes)
from micropick.hardware import labware                             # noqa: E402
from micropick.hardware.camera import CameraManager, Recorder      # noqa: E402
from micropick.hardware.labware import loaded_labware              # noqa: E402
from micropick.hardware.mock import MockRobot, open_mock_camera    # noqa: E402
from micropick.hardware.protocols import goto_xy, move_to, xyz     # noqa: E402
from micropick.viz import overlays                                 # noqa: E402
from micropick.viz.window import FrameWindow, fit_size             # noqa: E402
from micropick.workflows.calibrate_camera import calibrate_camera  # noqa: E402
from micropick.workflows.calibrate_homography import calibrate_homography  # noqa: E402
from micropick.workflows.calibrate_pipette import (TipDetector,    # noqa: E402
                                                   calibrate_pipette_offset)
from micropick.workflows.jog import JogController, Limits, jog_in_window  # noqa: E402
from micropick.workflows.picking import (PickingError,             # noqa: E402
                                         PickingSession, RobotState)

__all__ = [
    # stdlib / third party the cells use directly
    "json", "os", "time", "cv2", "np",
    # micropick surface
    "ot2_api", "paths", "store", "CameraSpec", "PickingConfig", "PipetteOffset",
    "LabwareError", "local_definitions", "resolve_definition", "labware",
    "loaded_labware", "rt", "Destination", "Routine", "RoutineError",
    "empty_plate_table", "plan_from_table", "vision", "overlays",
    "FrameWindow", "fit_size",
    "PixelMap", "compare_degrees", "Homography", "calibrate_camera",
    "calibrate_pipette_offset", "calibrate_homography", "TipDetector",
    "CameraManager", "Recorder", "MockRobot", "open_mock_camera",
    "goto_xy", "move_to", "xyz", "JogController", "Limits", "jog_in_window",
    "PickingSession", "RobotState", "PickingError",
    # helpers defined below
    "require", "verdict", "show", "heatmap", "load_profile", "connect_robot",
    "open_cameras", "bench_dir", "bench_frame_path", "bench_clip_path",
    "ensure_bench_data", "load_bench_clip", "blob_boxes", "StandInDetector",
    "BENCH_BLOBS",
    "REPO_ROOT", "Profile", "stub_pixel_map", "stub_profile", "mock_session",
]

REPO_ROOT = _Path(_os.environ["MICROPICK_ROOT"])


# ---------------------------------------------------------------------------
# verdicts and display
# ---------------------------------------------------------------------------

def require(condition, message: str) -> None:
    """Fail with a plain message when a precondition (hardware, weights, a file)
    is missing, instead of a deep traceback from wherever it is first used."""
    if not condition:
        raise RuntimeError(message)


def verdict(label: str, ok: bool, *, expected=None, got=None, note: str = "") -> bool:
    """Print a single, explicit pass/fail for a check. Returns ok."""
    print(f"\n[{'PASS' if ok else 'FAIL'}] {label}")
    if expected is not None:
        print(f"    expected: {expected}")
    if got is not None:
        print(f"    got:      {got}")
    if note:
        print(f"    note: {note}")
    return ok


def show(image, title: str = "") -> None:
    """Display a BGR or grey image inline, without a cv2 window or matplotlib.

    Falls back to writing a PNG under outputs/ when not in a notebook, so the
    same code works when a check body is run as a script.
    """
    if title:
        print(title)
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        print("(could not encode image)")
        return
    try:
        from IPython.display import Image, display
        display(Image(data=buf.tobytes()))
    except Exception:
        out = paths.images_dir() / f"bench_{int(time.time()*1000)}.png"
        out.write_bytes(buf.tobytes())
        print(f"(no inline display; wrote {out})")


def heatmap(gray) -> "np.ndarray":
    """A colour-mapped, contrast-stretched view of a scalar map (e.g. variance)."""
    arr = np.asarray(gray, dtype=np.float32)
    lo, hi = float(arr.min()), float(arr.max())
    norm = np.zeros_like(arr) if hi <= lo else (arr - lo) / (hi - lo)
    return cv2.applyColorMap((norm * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)


# ---------------------------------------------------------------------------
# bring-up helpers
# ---------------------------------------------------------------------------

def load_profile(name: str = "lab_main"):
    """Load a profile by name, with a clear message if it does not exist."""
    known = store.list_profiles()
    require(name in known,
            f"no profile {name!r}; available: {', '.join(known) or 'none'}")
    return store.load_profile(name)


def connect_robot(*, create_run: bool = True, load_pipette: bool = True,
                  home: bool = False):
    """An OpentronsAPI brought up ready to move. Reads/loads only; never a
    picking move. `home` is off by default because it is slow and loud."""
    api = ot2_api.OpentronsAPI()
    if home:
        api.home_robot()
    if create_run:
        api.create_run()
    if load_pipette:
        api.load_pipette()
    return api


def open_cameras(profile):
    """A CameraManager for the profile's cameras. The caller opens the ones it
    needs and closes them afterwards."""
    return CameraManager.from_profile(profile)


def stub_pixel_map() -> "PixelMap":
    """A trivial linear pixel map, for constructing a session on mocks."""
    s = 1500.0
    return PixelMap(_PixelMapConfig(
        degree=1, cu=1296, cv=972, s=s,
        coef=[[0.0, 0.02 * s], [0.02 * s, 0.0]], zero=[0.0, 0.0], ref=[1296, 972],
        bounds=[0.0, 0.0, 2592.0, 1944.0], image_size=[2592, 1944], sweep_z=0.0))


def stub_profile() -> "Profile":
    """A minimal in-memory profile, enough to build a PickingSession on mocks."""
    return Profile(
        meta=ProfileMeta(name="stub"),
        calibration=Calibration(pipette_offset=PipetteOffset(dx=0.0, dy=0.0)),
        picking=PickingConfig(), cameras={},
        positions={"observe": (150.0, 150.0, 80.0), "shake": (335.5, 223.0, 66.5)},
        path=paths.outputs_dir() / "stub")


def mock_session(routine=None, **session_kw) -> "PickingSession":
    """A PickingSession wired entirely to mocks, for checks that must not move a
    real robot (e.g. asserting recording is off). Returns the session; its mock
    camera thread is a daemon and needs no explicit close for a quick check."""
    routine = routine or Routine(
        Destination.coordinates([(150.0, 150.0)]), {(150.0, 150.0): 1})
    return PickingSession(MockRobot(), open_mock_camera(width=2592, height=1944),
                          stub_pixel_map(), stub_profile(), routine, object(),
                          **session_kw)


# ---------------------------------------------------------------------------
# bench test data (synthetic, committed, regenerable)
# ---------------------------------------------------------------------------

# The blobs drawn into dish.png: (cx, cy, radius, kind). "good" ones are the
# well-formed, well-separated cuboids a run should pick; the others exist so the
# selection filters have something to reject.
BENCH_BLOBS = [
    (400, 300, 15, "good"),
    (760, 320, 15, "good"),
    (560, 620, 15, "good"),
    (900, 640, 15, "good"),
    (300, 640, 42, "oversized"),        # too big for the size window
    (650, 200, 14, "too_close"),        # this pair sits inside min spacing
    (686, 210, 14, "too_close"),
]
_DISH = (648, 486, 460)                  # cx, cy, radius of the dish in dish.png
_DISH_SIZE = (1296, 972)                 # w, h


def bench_dir() -> "_Path":
    d = paths.fixtures_dir() / "bench"
    d.mkdir(parents=True, exist_ok=True)
    return d


def bench_frame_path() -> "_Path":
    return bench_dir() / "dish.png"


def bench_clip_path() -> "_Path":
    return bench_dir() / "floaters.npz"


def _make_dish_frame() -> "np.ndarray":
    w, h = _DISH_SIZE
    frame = np.full((h, w, 3), 18, np.uint8)
    cx, cy, r = _DISH
    cv2.circle(frame, (cx, cy), r, (40, 40, 40), -1)          # dish floor
    cv2.circle(frame, (cx, cy), r, (90, 90, 90), 3)           # rim
    for bx, by, br, _kind in BENCH_BLOBS:
        cv2.circle(frame, (bx, by), br, (215, 215, 215), -1)
    return cv2.GaussianBlur(frame, (3, 3), 0)


def _make_floater_clip(n: int = 12) -> "np.ndarray":
    w, h = 400, 300
    frames = np.full((n, h, w), 30, np.uint8)
    for i in range(n):
        # one region flickers bright/dark; the rest is static
        frames[i, 140:160, 190:210] = 230 if i % 2 else 30
    return frames


def ensure_bench_data() -> dict:
    """Create the synthetic frame and clip if they are missing; return paths.

    They are committed, but regenerated deterministically so a checkout without
    the binaries still works.
    """
    frame_path, clip_path = bench_frame_path(), bench_clip_path()
    if not frame_path.exists():
        cv2.imwrite(str(frame_path), _make_dish_frame())
    if not clip_path.exists():
        np.savez_compressed(clip_path, frames=_make_floater_clip())
    return {"frame": frame_path, "clip": clip_path,
            "dish": _DISH, "blobs": BENCH_BLOBS}


def load_bench_clip() -> list:
    """The floater clip as a list of greyscale frames."""
    ensure_bench_data()
    with np.load(bench_clip_path()) as data:
        return [f for f in data["frames"]]


# ---------------------------------------------------------------------------
# a stand-in detector, for checks when the real YOLO weights are absent
# ---------------------------------------------------------------------------

# Re-exported, not defined here. It is a pure function of an array and lives in
# core/vision/standin.py, where the GUI can reach it too; a copy in this file
# was a copy nothing but a notebook could import.
