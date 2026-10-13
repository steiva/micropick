"""A YOLO detector exported to ONNX, run by ONNX Runtime.

PyTorch on a CPU is the slowest way to run these models, and it is the only
one ultralytics offers a computer without an NVIDIA card. ONNX Runtime runs
the same network on any x86 CPU (its kernels use AVX2 and AVX-512, AMD's as
well as Intel's) and, through DirectML on Windows, on any DirectX 12 GPU -
NVIDIA, AMD or Intel, built in or not. Which one is used is `core.vision.yolo`'s
business; this module runs a session it is given.

The same answers as ultralytics
-------------------------------
`OnnxYolo.predict` is the part of `ultralytics.YOLO.predict` that
`cuboids.detect_boxes` and the tip detector use, and it is written to give
the same boxes for the same arguments:

- the frame is letterboxed as ultralytics does for a `.pt` model: scaled to
  fit `imgsz`, padded with grey 114 only to the next multiple of the stride
  ("rect"), so a 4000x3000 frame at 1536 goes in as 1536x1152. A model
  exported with a fixed input size is padded to its square instead, which is
  what ultralytics does for one too;
- the array is taken to be BGR and flipped to RGB, because that is what
  ultralytics does with a numpy array whatever the caller meant;
- best class per box over `conf`, then class-aware non-maximum suppression
  with torchvision's rule (a box goes when its IoU with a kept one is over
  `iou`), at most `max_det` kept, boxes mapped back to the frame and
  clipped to it.

A detection model (`task` "detect") is all this reads: four box numbers and
a score per class for every anchor.
"""

from __future__ import annotations

import ast
import math

import cv2
import numpy as np

__all__ = ["OnnxYolo", "letterbox", "nms"]

_PAD = 114
_MAX_NMS = 30000          # candidates kept for NMS, as ultralytics
_MAX_WH = 7680            # class offset, so classes never suppress each other


class OnnxYolo:
    """An ONNX Runtime session with `predict` and `names`, as a YOLO."""

    def __init__(self, session, *, path: str = ""):
        self.session = session
        self.path = path
        meta = session.get_modelmeta().custom_metadata_map
        task = meta.get("task", "detect")
        if task != "detect":
            raise ValueError(f"{path}: a {task!r} model; only detection "
                             f"models run here")
        self.names = (ast.literal_eval(meta["names"]) if "names" in meta
                      else {})
        self.stride = int(meta.get("stride", 32))
        inp = session.get_inputs()[0]
        self._input = inp.name
        h, w = inp.shape[2], inp.shape[3]
        # A fixed size is numbers; an exported dynamic one is names.
        self.fixed = ((int(h), int(w))
                      if isinstance(h, int) and isinstance(w, int) else None)

    @property
    def provider(self) -> str:
        """The execution provider doing the work, e.g. CPUExecutionProvider."""
        return self.session.get_providers()[0]

    def predict(self, source=None, *, imgsz: int = 640, conf: float = 0.25,
                iou: float = 0.7, max_det: int = 300, **_ignored):
        """One frame in, a one-item list of results out, as ultralytics.
        `save`, `show`, `verbose` and the like are accepted and ignored."""
        frame = np.asarray(source)
        if frame.ndim == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        size = math.ceil(int(imgsz) / self.stride) * self.stride
        if self.fixed is not None:
            if self.fixed != (size, size):
                raise ValueError(
                    f"{self.path} was exported at {self.fixed[1]}x"
                    f"{self.fixed[0]} and is asked for {size}: export it "
                    f"again at that size, or with a dynamic input")
            image = letterbox(frame, size, self.stride, rect=False)
        else:
            image = letterbox(frame, size, self.stride, rect=True)
        blob = image[..., ::-1].transpose(2, 0, 1)[None]          # BGR->RGB, NCHW
        blob = np.ascontiguousarray(blob, dtype=np.float32) / 255.0
        out = self.session.run(None, {self._input: blob})[0][0]   # (4+nc, anchors)
        boxes, scores, classes = _decode(out, conf, iou, max_det)
        boxes = _to_frame(boxes, image.shape[:2], frame.shape[:2])
        return [_Result(boxes, scores, classes, self.names)]

    def __repr__(self) -> str:
        return f"<OnnxYolo {self.path} on {self.provider}>"


def letterbox(frame: np.ndarray, size: int, stride: int = 32, *,
              rect: bool = True) -> np.ndarray:
    """ultralytics' LetterBox: fit into size x size keeping the aspect,
    padding with grey centred; with `rect` only to the next multiple of
    `stride`."""
    h, w = frame.shape[:2]
    r = min(size / h, size / w)
    new_w, new_h = round(w * r), round(h * r)
    dw, dh = size - new_w, size - new_h
    if rect:
        dw, dh = dw % stride, dh % stride
    dw, dh = dw / 2, dh / 2
    if (w, h) != (new_w, new_h):
        frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    top, bottom = round(dh - 0.1), round(dh + 0.1)
    left, right = round(dw - 0.1), round(dw + 0.1)
    return cv2.copyMakeBorder(frame, top, bottom, left, right,
                              cv2.BORDER_CONSTANT, value=(_PAD,) * 3)


def nms(boxes: np.ndarray, scores: np.ndarray, iou: float) -> np.ndarray:
    """Indices kept, best first: torchvision.ops.nms in numpy."""
    order = np.argsort(-scores, kind="stable")
    x1, y1, x2, y2 = boxes.T
    areas = (x2 - x1) * (y2 - y1)
    keep = []
    while order.size:
        i = order[0]
        keep.append(i)
        rest = order[1:]
        w = np.clip(np.minimum(x2[i], x2[rest]) - np.maximum(x1[i], x1[rest]),
                    0, None)
        h = np.clip(np.minimum(y2[i], y2[rest]) - np.maximum(y1[i], y1[rest]),
                    0, None)
        inter = w * h
        overlap = inter / (areas[i] + areas[rest] - inter)
        order = rest[~(overlap > iou)]
    return np.asarray(keep, dtype=np.int64)


def _decode(out: np.ndarray, conf: float, iou: float, max_det: int):
    """(4+nc, anchors) -> boxes xyxy, scores, classes after NMS, in the
    network's input pixels."""
    pred = out.T                                         # (anchors, 4+nc)
    cls = pred[:, 4:]
    best = cls.max(axis=1)
    pred, cls, best = pred[best > conf], cls[best > conf], best[best > conf]
    if not len(pred):
        empty = np.zeros((0,), np.float32)
        return np.zeros((0, 4), np.float32), empty, empty
    j = cls.argmax(axis=1)
    cx, cy, w, h = pred[:, 0], pred[:, 1], pred[:, 2], pred[:, 3]
    boxes = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)
    if len(best) > _MAX_NMS:
        top = np.argsort(-best, kind="stable")[:_MAX_NMS]
        boxes, best, j = boxes[top], best[top], j[top]
    keep = nms(boxes + (j * _MAX_WH)[:, None], best, iou)[:max_det]
    return (boxes[keep].astype(np.float32), best[keep].astype(np.float32),
            j[keep].astype(np.float32))


def _to_frame(boxes: np.ndarray, net_shape, frame_shape) -> np.ndarray:
    """ultralytics' scale_boxes and clip_boxes."""
    h1, w1 = net_shape
    h0, w0 = frame_shape
    gain = min(h1 / h0, w1 / w0)
    pad_x = round((w1 - round(w0 * gain)) / 2 - 0.1)
    pad_y = round((h1 - round(h0 * gain)) / 2 - 0.1)
    boxes = boxes.copy()
    boxes[:, [0, 2]] = ((boxes[:, [0, 2]] - pad_x) / gain).clip(0, w0)
    boxes[:, [1, 3]] = ((boxes[:, [1, 3]] - pad_y) / gain).clip(0, h0)
    return boxes


# ---------------------------------------------------------------------------
# the little of ultralytics' Results that the callers touch
# ---------------------------------------------------------------------------

class _Array(np.ndarray):
    """A numpy array that also answers `.cpu().numpy()`, as a tensor does."""

    def cpu(self) -> _Array:
        return self

    def numpy(self) -> np.ndarray:
        return np.asarray(self)


def _arr(a: np.ndarray) -> _Array:
    return np.asarray(a).view(_Array)


class _Box:
    def __init__(self, xyxy, conf, cls):
        self.xyxy = _arr(xyxy[None])
        self.conf = _arr(conf[None])
        self.cls = _arr(cls[None])


class _Boxes:
    def __init__(self, xyxy, conf, cls):
        self.xyxy = _arr(xyxy)
        self.conf = _arr(conf)
        self.cls = _arr(cls)

    def __len__(self) -> int:
        return len(self.conf)

    def __iter__(self):
        for i in range(len(self)):
            yield _Box(self.xyxy[i], self.conf[i], self.cls[i])


class _Result:
    def __init__(self, xyxy, conf, cls, names):
        self.boxes = _Boxes(xyxy, conf, cls)
        self.names = names
