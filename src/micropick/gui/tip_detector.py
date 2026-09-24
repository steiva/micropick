"""The crosshair-and-tip detector for the pipette calibration, and its stand-in.

`workflows.calibrate_pipette.TipDetector` wraps a YOLO model named in the
profile (`TipTarget.model_file`) and is what the routine measures with. Loading
it means importing ultralytics, which is seconds and an optional extra, so it
happens inside the calibration's worker rather than when the page is built.

In `--mock` there is no disc, no lower camera and no model, and the routine
still has to run end to end: the moves, the manual touch-up, the save. The
stand-in fabricates the two views the routine asks for from the gantry pose,
with a fixed error built in, so the correction it computes is real arithmetic
on made-up pixels and the offset it saves is the starting one plus that error.
It says so on the result screen; a stand-in that produced a plausible number
without saying so would be the worst thing on this page.
"""

from __future__ import annotations

import time

import numpy as np

from .. import paths
from ..hardware.protocols import xyz
from ..workflows.calibrate_pipette import Detection, PatternView, TipDetector

__all__ = ["load_tip_detector", "StandInTipDetector", "STANDIN_ERROR_MM",
           "STANDIN_NOTE"]

# What the stand-in pretends the stored offset is wrong by. Chosen to be
# visible in the result and obviously not a measurement.
STANDIN_ERROR_MM = (0.8, -0.4)
STANDIN_NOTE = ("stand-in detector: no crosshair disc was seen. The offset is "
                "the starting one plus a fixed error of "
                f"({STANDIN_ERROR_MM[0]:+.1f}, {STANDIN_ERROR_MM[1]:+.1f}) mm, "
                "not a measurement.")


class StandInTipDetector:
    """Fabricates `PatternView`s from the gantry pose. For --mock only.

    The upper view puts the disc at the frame centre. The lower view puts
    the tip wherever the gantry would have to move to land on a target that
    is the first pose it was asked from, minus the approach shift, plus
    `error`; after the routine applies its correction the residual reads
    zero, which is what a converging calibration looks like.
    """

    is_standin = True

    def __init__(self, robot, target, *, error_mm=STANDIN_ERROR_MM,
                 mm_per_px: float = 0.01):
        self.robot = robot
        self.approach = np.asarray(target.approach_offset, dtype=float)
        self.axes = np.asarray(target.axes, dtype=float)
        self.error = np.asarray(error_mm, dtype=float)
        self.mm_per_px = mm_per_px
        self._anchor: np.ndarray | None = None

    def detect(self, frame) -> list[Detection]:
        """The five crosshairs of a disc at the frame's centre, invented.

        The check page asks for points rather than a whole view, and in
        --mock there is no disc to find. These are laid out like a real
        one so the page's geometry, its clicking and its arithmetic are
        exercised; they are not a measurement and the page says so.
        """
        height, width = frame.shape[:2]
        centre = np.array([width / 2.0, height / 2.0])
        r = min(width, height) / 5.0
        offsets = [(0.0, 0.0), (r, 0.0), (-r, 0.0), (0.0, r), (0.0, -r)]
        return [Detection("point", centre + np.array(o), 0.9) for o in offsets]

    def view(self, camera, *, spacing_mm: float, frames: int = 5,
             need_tip: bool = False, **_ignored) -> PatternView:
        frame = camera.read_after(time.monotonic())
        h, w = frame.shape[:2]
        centre = np.array([w / 2.0, h / 2.0])
        r = spacing_mm / self.mm_per_px
        neighbours = centre + np.array([[r, 0.0], [-r, 0.0], [0.0, r], [0.0, -r]])
        tip = None
        if need_tip:
            pose = np.array(xyz(self.robot)[:2], dtype=float)
            if self._anchor is None:
                self._anchor = pose - self.approach + self.error
            # The routine computes correction = axes @ (residual_px * mm/px)
            # and wants correction = anchor - pose, so residual is that,
            # taken back through the axes (an involution) and the scale.
            wanted = self._anchor - pose
            residual_px = (self.axes @ wanted) / self.mm_per_px
            tip = centre - residual_px
        return PatternView(centre=centre, neighbours=neighbours, tip=tip,
                           mm_per_px=self.mm_per_px, spread_px=0.0,
                           radius_cv=0.0, n_frames=frames, frame=frame,
                           detections=[])


def load_tip_detector(profile, *, mock: bool = False, robot=None):
    """The detector the profile names. Blocking: run it in a Worker.

    Raises FileNotFoundError for missing weights and ImportError for a
    missing ultralytics, each with the sentence that says what to do.
    """
    target = profile.calibration.tip_target
    if mock:
        return StandInTipDetector(robot, target)
    path = paths.ml_models_dir() / target.model_file
    if not path.is_file():
        raise FileNotFoundError(
            f"no tip detector weights at {path}. The profile's tip_target "
            f"names {target.model_file!r}; put that file in "
            f"{paths.ml_models_dir()} (weights are not tracked in the "
            f"repository).")
    try:
        from ultralytics import YOLO          # seconds, and optional
    except ImportError as exc:
        raise ImportError(
            "ultralytics is not installed, and the tip detector is a YOLO "
            "model; install the 'ml' extra (pip install -e \".[ml]\")") from exc
    return TipDetector(YOLO(str(path)), imgsz=target.imgsz, conf=target.conf)
