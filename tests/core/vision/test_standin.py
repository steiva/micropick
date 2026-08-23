"""The stand-in detector, and the shape `detect_boxes` expects of a model.

The adapter exists so `core/vision/cuboids.py` needs no branch for it, and that
only holds while `detect_boxes` keeps asking for `.boxes.xyxy.cpu().numpy()`.
This pins that, so a change there fails here rather than at a desk with no
weights installed.
"""

from __future__ import annotations

import cv2
import numpy as np

from micropick.config.schema import PickingConfig
from micropick.core.vision import cuboids
from micropick.core.vision.standin import StandInDetector, blob_boxes


def dish(blobs=((40, 30, 8), (120, 90, 8), (200, 40, 20))) -> np.ndarray:
    frame = np.full((150, 260, 3), 20, np.uint8)
    for x, y, r in blobs:
        cv2.circle(frame, (x, y), r, (220, 220, 220), -1)
    return frame


def test_blob_boxes_finds_the_blobs_and_calls_them_certain():
    gray = cv2.cvtColor(dish(), cv2.COLOR_BGR2GRAY)
    boxes, confs = blob_boxes(gray)
    assert len(boxes) == 3
    assert boxes.dtype == np.float32 and boxes.shape[1] == 4
    # Every confidence is 1.0 and means nothing; that is why callers label it.
    assert np.array_equal(confs, np.ones(3, np.float32))


def test_blob_boxes_on_a_blank_frame_returns_empty_arrays_not_none():
    boxes, confs = blob_boxes(np.zeros((40, 40), np.uint8))
    assert boxes.shape == (0, 4) and confs.shape == (0,)


def test_min_area_drops_the_small_ones():
    gray = cv2.cvtColor(dish(), cv2.COLOR_BGR2GRAY)
    assert len(blob_boxes(gray, min_area=40)[0]) == 3
    # only the r=20 blob survives an area floor above the r=8 ones
    assert len(blob_boxes(gray, min_area=600)[0]) == 1


def test_detect_boxes_takes_it_with_no_branch():
    """The duck-typing that keeps cuboids.py free of a stand-in special case."""
    frame = dish()
    boxes, confs = cuboids.detect_boxes(StandInDetector(), frame, PickingConfig())
    assert boxes.shape == (3, 4) and confs.shape == (3,)


def test_max_det_is_the_one_argument_it_honours():
    frame = dish()
    cfg = PickingConfig(yolo_max_det=2)
    boxes, _ = cuboids.detect_boxes(StandInDetector(), frame, cfg)
    assert len(boxes) == 2


def test_it_greys_the_rgb_it_is_handed():
    """detect_boxes converts BGR to RGB for the model. The stand-in has to
    undo that rather than read a channel and hope."""
    frame = dish()
    direct = blob_boxes(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))[0]
    through = cuboids.detect_boxes(StandInDetector(), frame, PickingConfig())[0]
    assert np.array_equal(direct, through)
