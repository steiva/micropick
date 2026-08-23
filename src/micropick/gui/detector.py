"""The one thing that owns a detector.

A model is a file of weights, seconds to load and hundreds of milliseconds to
run, and both of those in the GUI thread are a frozen window. Both therefore
happen in a `Worker`; this class holds no thread of its own and every method on
it blocks, like the session's, so it can be exercised with no event loop.

When the weights are not there
------------------------------
`core/vision/standin.py` finds bright blobs. It is not the model and the
interface says so on every screen that shows its output — `description` is
written to be displayed, not logged. A stand-in result looks exactly like a real
one, which is the whole reason it has to be labelled: an operator comparing two
runs must not have to remember which had weights installed.

The cuboid weights have no home in the profile — unlike
`TipTarget.model_file`, the notebook carries the name in a cell — so the file is
chosen from whatever is in `ml_models/` rather than guessed at.

Scale, or nothing
-----------------
The shape windows are in micrometres, so `add_derived` needs millimetres per
pixel, and that comes from the profile's pixel map. Two things have to hold
before it may be used: the profile must have one, and the frame must be the size
it was fitted at. A map fitted at 4000x3000 and applied to a 2000x1500 frame puts
every point at half its coordinate — DESIGN section 3 on camera modes, which
cost a whole run of the machine in silence. Without both, the detections come
back unclassified and the reason is on the screen.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import cv2
import numpy as np
import pandas as pd
from PySide6.QtCore import QObject, Signal

from .. import paths
from ..config.schema import PickingConfig
from ..core.calibration.pixel_map import PixelMap
from ..core.vision import cuboids
from ..core.vision.standin import IS_STANDIN_NOTE, StandInDetector

__all__ = ["DetectorService", "Detection", "STANDIN"]

log = logging.getLogger(__name__)

# What the weights chooser calls the stand-in. Not a filename, so it cannot be
# confused with one.
STANDIN = "(stand-in: bright blobs, no model)"


@dataclass
class Detection:
    """One frame's worth of detection, and what could not be worked out."""

    boxes: np.ndarray
    confs: np.ndarray
    df: pd.DataFrame
    pickable: pd.DataFrame
    isolated: pd.DataFrame
    bubbles: pd.DataFrame
    counts: dict
    classified: bool
    standin: bool
    notes: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        lines = [f"{len(self.boxes)} boxes, {len(self.df)} with a contour"]
        if self.classified:
            lines.append(f"pickable {len(self.pickable)}, "
                         f"isolated {len(self.isolated)}, "
                         f"bubbles {len(self.bubbles)}")
            lines.append("  ".join(f"{k} {v}" for k, v in self.counts.items()))
        return "\n".join(lines + self.notes)


class DetectorService(QObject):
    changed = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._model = None
        self._name: str | None = None

    # -- what is available ---------------------------------------------------

    @staticmethod
    def available_weights() -> list[str]:
        """Every `.pt` in ml_models/. The directory is not created on demand:
        a missing one is a real problem and an empty folder that looks correct
        is worse than an error."""
        directory = paths.ml_models_dir()
        if not directory.is_dir():
            return []
        return sorted(p.name for p in directory.glob("*.pt"))

    @property
    def model(self):
        return self._model

    @property
    def is_standin(self) -> bool:
        return isinstance(self._model, StandInDetector)

    @property
    def description(self) -> str:
        """Written to be shown to an operator, not logged."""
        if self._model is None:
            return "no detector loaded"
        if self.is_standin:
            return IS_STANDIN_NOTE
        return f"model: {self._name}"

    # -- loading -------------------------------------------------------------

    def load(self, name: str) -> str:
        """Blocking: importing ultralytics alone takes seconds. Use a Worker."""
        if name == STANDIN:
            self._model, self._name = StandInDetector(), STANDIN
            log.info("using the %s", IS_STANDIN_NOTE)
            self.changed.emit()
            return self.description

        path = paths.ml_models_dir() / name
        if not path.is_file():
            raise FileNotFoundError(
                f"no weights at {path}. Put the .pt file in "
                f"{paths.ml_models_dir()}; weights are not tracked in the "
                f"repository.")
        from ultralytics import YOLO          # seconds, and optional

        self._model, self._name = YOLO(str(path)), name
        log.info("loaded cuboid weights %s", name)
        self.changed.emit()
        return self.description

    # -- running -------------------------------------------------------------

    def detect(self, frame: np.ndarray, cfg: PickingConfig,
               pixel_map=None) -> Detection:
        """Blocking: this is the inference. Use a Worker."""
        if self._model is None:
            raise RuntimeError("no detector loaded")

        boxes, confs = cuboids.detect_boxes(self._model, frame, cfg)
        gray = (cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3
                else frame)

        df = cuboids.build_cuboid_df(gray, boxes, confs,
                                     pad=cfg.otsu_pad, open_k=cfg.otsu_open_k)
        notes: list[str] = []
        if self.is_standin:
            notes.append(IS_STANDIN_NOTE)

        one_d = self._mm_per_px(frame, pixel_map, notes)
        if one_d is None or len(df) == 0:
            empty = df.iloc[0:0]
            return Detection(boxes, confs, df, empty, empty, empty, {},
                             classified=False, standin=self.is_standin,
                             notes=notes)

        df = cuboids.add_derived(df, one_d ** 2, one_d, cfg.circle_center)
        labels = cuboids.label_rejections(df, cfg)
        df = df.join(labels)

        # The same three selections `PickingSession._analyze` makes, and for the
        # same reason: every detection is labelled once and pickable/isolated
        # are selections on that label (DESIGN section 11). Written here rather
        # than imported because the session owns a run and this owns a frame;
        # if a third caller appears it should become a helper in
        # core/vision/cuboids.py instead of a third copy.
        pickable = df[df.reject_reason.isin(("", "crowded"))].copy()
        isolated = df[df.reject_reason == ""].copy()
        bubbles = df[df.is_bubble].copy()
        return Detection(boxes, confs, df, pickable, isolated, bubbles,
                         cuboids.reject_counts(df), classified=True,
                         standin=self.is_standin, notes=notes)

    @staticmethod
    def _mm_per_px(frame, pixel_map, notes: list[str]) -> float | None:
        """Millimetres per pixel at the dish centre, or None with the reason.

        Refuses a map fitted at another resolution instead of scaling it. The
        two camera modes on this rig share a field of view and could in
        principle be rescaled, but a mode that crops the sensor scales the axes
        unequally, and telling those apart is not something to do silently on a
        frame someone opened from disk.
        """
        if pixel_map is None:
            notes.append("no pixel map in the profile, so nothing is measured "
                         "in millimetres and the shape windows cannot run: "
                         "detections only.")
            return None
        height, width = frame.shape[:2]
        # `profile.pixel_map` hands back the stored schema model, not the
        # runtime PixelMap; `from_config` below is what wraps it.
        fitted = tuple(pixel_map.image_size)
        if (width, height) != fitted:
            notes.append(
                f"the pixel map was fitted at {fitted[0]}x{fitted[1]} and this "
                f"frame is {width}x{height}. Refusing to rescale it: "
                f"detections only.")
            return None
        pmap = PixelMap.from_config(pixel_map)
        return float(np.mean(pmap.mm_per_px(width / 2, height / 2)))
