"""A detector for a desk, when the trained weights are not there.

`blob_boxes` is a threshold-and-contour finder. It is **not** the cuboid model
and does not pretend to be one: it finds bright blobs, calls every one of them a
detection with confidence 1.0, and knows nothing about what a microtissue looks
like. What it is for is letting the half of the pipeline below detection — Otsu
contours, the shape windows, the bubble features, the selection — run at a desk
with no `ultralytics` installed and no `.pt` file to hand.

It lived in `notebooks/bench_setup.py`, which made it unreachable from anything
that is not a notebook. It is a pure function of an array, touches no hardware
and no window, so `core/vision` is where it belongs; the GUI needs it for the
same reason the bench checks did, and a second copy over there would be two
stand-ins to keep honest.

`StandInDetector` is the same function behind the small part of the
`ultralytics.YOLO` surface that `cuboids.detect_boxes` uses — `predict(...)`
returning something whose `.boxes.xyxy` and `.boxes.conf` answer `.cpu()` and
`.numpy()`. That way `detect_boxes` takes it with no branch and
`core/vision/cuboids.py` needs no change at all.

**Anything using it has to say so.** Its output is shaped like a detection and
is not one, and a result presented as the model's would be a measurement of the
wrong thing reported as the right thing.
"""

from __future__ import annotations

import cv2
import numpy as np

__all__ = ["blob_boxes", "StandInDetector", "IS_STANDIN_NOTE"]

IS_STANDIN_NOTE = (
    "stand-in detector: bright blobs, not the trained model. "
    "Confidences are 1.0 and mean nothing."
)


def blob_boxes(gray, *, min_area: int = 40):
    """Bounding boxes of bright blobs, as (boxes_xyxy, confidences).

    A crude threshold-and-contour finder, NOT the trained model. It lets the
    Otsu/shape/selection half of the pipeline run at a desk when the cuboid YOLO
    weights are not installed; the caller states plainly that it was used.
    """
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in contours:
        if cv2.contourArea(c) < min_area:
            continue
        x, y, w, h = cv2.boundingRect(c)
        boxes.append([x, y, x + w, y + h])
    if not boxes:
        return np.zeros((0, 4), np.float32), np.zeros((0,), np.float32)
    return np.array(boxes, np.float32), np.ones(len(boxes), np.float32)


# ---------------------------------------------------------------------------
# the little of ultralytics that detect_boxes actually touches
# ---------------------------------------------------------------------------

class _Tensorish:
    """Enough of a torch tensor for `.cpu().numpy()` and nothing more."""

    def __init__(self, array: np.ndarray):
        self._array = array

    def cpu(self) -> "_Tensorish":
        return self

    def numpy(self) -> np.ndarray:
        return self._array


class _Boxes:
    def __init__(self, xyxy: np.ndarray, conf: np.ndarray):
        self.xyxy = _Tensorish(xyxy)
        self.conf = _Tensorish(conf)


class _Result:
    def __init__(self, boxes: _Boxes):
        self.boxes = boxes


class StandInDetector:
    """`blob_boxes` wearing the shape `detect_boxes` expects of a model."""

    def __init__(self, min_area: int = 40):
        self.min_area = int(min_area)

    def predict(self, image, *, imgsz=None, conf=None, iou=None,
                max_det=None, verbose=False, **_ignored):
        """Takes what `detect_boxes` passes and uses almost none of it.

        `detect_boxes` hands over RGB, because that is what the trained model
        was trained on; this greys it and thresholds. `imgsz`, `conf` and `iou`
        are accepted and ignored, which is honest rather than sloppy: there is
        no confidence to threshold on and no non-maximum suppression to tune.
        `max_det` is applied, since it is a cap on how much comes back and that
        much is meaningful.
        """
        image = np.asarray(image)
        gray = (cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3
                else image)
        boxes, confs = blob_boxes(gray, min_area=self.min_area)
        if max_det is not None and len(boxes) > max_det:
            boxes, confs = boxes[:max_det], confs[:max_det]
        return [_Result(_Boxes(boxes, confs))]

    def __repr__(self) -> str:
        return f"<StandInDetector min_area={self.min_area}>"
