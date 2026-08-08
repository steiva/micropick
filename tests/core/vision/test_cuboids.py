"""Tests for the cuboid vision pipeline on synthetic pixels, no robot, no YOLO.

Mirrors the approach DESIGN section 8 takes with MarkerScene: build real arrays
and run the real functions, so the maths is exercised without hardware. YOLO is
replaced by a duck-typed fake, which also proves the module needs no ultralytics.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from micropick.config.schema import PickingConfig
from micropick.core.vision import cuboids as cb


# ---------------------------------------------------------------------------
# a fake YOLO: enough surface for detect_boxes, no ultralytics
# ---------------------------------------------------------------------------

class _Arr:
    def __init__(self, a):
        self._a = np.asarray(a)

    def cpu(self):
        return self

    def numpy(self):
        return self._a


class _FakeBoxes:
    def __init__(self, xyxy, conf):
        self.xyxy = _Arr(xyxy)
        self.conf = _Arr(conf)


class _FakeResult:
    def __init__(self, boxes):
        self.boxes = boxes


class FakeYOLO:
    def __init__(self, xyxy, conf):
        self._xyxy, self._conf = xyxy, conf
        self.received = None

    def predict(self, img, **kw):
        self.received = img
        return [_FakeResult(_FakeBoxes(self._xyxy, self._conf))]


# ---------------------------------------------------------------------------
# detect_boxes: BGR -> RGB happens once, in here
# ---------------------------------------------------------------------------

def test_detect_boxes_converts_bgr_to_rgb_and_unpacks():
    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    frame[..., 0] = 1   # B
    frame[..., 1] = 2   # G
    frame[..., 2] = 3   # R
    model = FakeYOLO([[10, 10, 20, 20]], [0.9])

    boxes, confs = cb.detect_boxes(model, frame, PickingConfig())

    assert np.array_equal(model.received, frame[..., ::-1])  # model saw RGB
    assert model.received[0, 0, 0] == 3                      # R first now
    assert boxes.shape == (1, 4) and confs.shape == (1,)


# ---------------------------------------------------------------------------
# otsu_contour
# ---------------------------------------------------------------------------

def test_otsu_contour_finds_bright_square():
    gray = np.full((200, 200), 30, dtype=np.uint8)
    gray[80:120, 80:120] = 200
    cnt = cb.otsu_contour(gray, box=(80, 80, 120, 120), pad=6)

    assert cnt is not None
    m = cb.contour_metrics(cnt)
    assert abs(m['cX'] - 99.5) < 3 and abs(m['cY'] - 99.5) < 3


def test_otsu_contour_empty_crop_returns_none():
    gray = np.full((200, 200), 30, dtype=np.uint8)
    # box fully to the lower-right of the frame -> clamped crop has zero width
    assert cb.otsu_contour(gray, box=(250, 250, 260, 260)) is None


# ---------------------------------------------------------------------------
# contour_metrics
# ---------------------------------------------------------------------------

def test_contour_metrics_on_a_known_square():
    cnt = np.array([[[80, 80]], [[120, 80]], [[120, 120]], [[80, 120]]],
                   dtype=np.int32)
    m = cb.contour_metrics(cnt)

    assert m['area'] == pytest.approx(1600.0)
    assert m['aspect_ratio'] == pytest.approx(1.0, abs=0.02)
    assert m['circularity'] == pytest.approx(np.pi / 4, abs=0.02)
    assert m['solidity'] == pytest.approx(1.0, abs=0.02)
    assert m['radial_cv'] < 0.01           # every corner equidistant from centre


def test_contour_metrics_rejects_degenerate():
    line = np.array([[[0, 0]], [[10, 0]]], dtype=np.int32)   # zero area
    assert cb.contour_metrics(line) is None


# ---------------------------------------------------------------------------
# add_derived
# ---------------------------------------------------------------------------

def test_add_derived_distances_and_diameter():
    df = pd.DataFrame({'cX': [100.0, 100.0], 'cY': [100.0, 140.0],
                       'area': [1600.0, 1600.0]})
    out = cb.add_derived(df, size_ratio=1e-6, one_d_ratio=0.02,
                         circle_center=(100, 100))

    assert out['min_dist'].tolist() == pytest.approx([40.0, 40.0])
    assert out['min_dist_mm'].tolist() == pytest.approx([0.8, 0.8])
    assert out['distance_to_center'].tolist() == pytest.approx([0.0, 40.0])
    expected_d = 2 * np.sqrt((1600.0 * 1e-6 * 10e5) / np.pi)
    assert out['diameter_microns'].iloc[0] == pytest.approx(expected_d)


def test_add_derived_empty_is_noop():
    df = pd.DataFrame(columns=['cX', 'cY', 'area'])
    assert len(cb.add_derived(df, 1e-6, 0.02, (0, 0))) == 0


# ---------------------------------------------------------------------------
# select_pickable
# ---------------------------------------------------------------------------

def test_select_pickable_windows():
    cfg = PickingConfig()   # size (250,500), aspect (1,1.3), circ (0.6,1),
                            # solidity>=0.9, radial_cv<=0.22, circle_radius 900
    df = pd.DataFrame({
        'diameter_microns':  [300.0, 600.0, 300.0, 300.0],
        'aspect_ratio':      [1.10,  1.10,  1.10,  1.10],
        'circularity':       [0.80,  0.80,  0.80,  0.80],
        'solidity':          [0.95,  0.95,  0.50,  0.95],
        'radial_cv':         [0.10,  0.10,  0.10,  0.10],
        'distance_to_center':[100.0, 100.0, 100.0, 1000.0],
    })
    mask = cb.select_pickable(df, cfg)
    assert mask.tolist() == [True, False, False, False]


# ---------------------------------------------------------------------------
# analyze_mask
# ---------------------------------------------------------------------------

def test_analyze_mask_finds_one_blob():
    var_map = np.zeros((100, 100), dtype=np.float32)
    var_map[45:55, 45:55] = 100.0
    mask, df = cb.analyze_mask(var_map, scale=1.0)

    assert mask.sum() > 0
    assert len(df) == 1
    assert set(df.columns) >= {'x', 'y', 'area_px', 'elongation'}
    assert abs(df['x'].iloc[0] - 49.5) < 3 and abs(df['y'].iloc[0] - 49.5) < 3


def test_analyze_mask_flat_map_is_empty():
    var_map = np.full((50, 50), 5.0, dtype=np.float32)   # no signal above floor
    mask, df = cb.analyze_mask(var_map)
    assert not mask.any()
    assert len(df) == 0
    assert list(df.columns)   # keeps the column contract even when empty


# ---------------------------------------------------------------------------
# detect_floater_zones (exercises the frames[0].shape fix)
# ---------------------------------------------------------------------------

def _flickering_clip(n=6, size=200):
    frames = []
    for i in range(n):
        f = np.zeros((size, size), dtype=np.uint8)
        f[95:105, 95:105] = 255 if i % 2 else 0   # one region toggles
        frames.append(f)
    return frames


def test_detect_floater_zones_finds_moving_region():
    zones = cb.detect_floater_zones(_flickering_clip(), downscale=2, step=1)
    assert len(zones) == 1
    zx, zy = zones[0]
    assert abs(zx - 100) < 12 and abs(zy - 100) < 12


def test_detect_floater_zones_static_is_empty():
    still = [np.full((200, 200), 40, dtype=np.uint8) for _ in range(6)]
    assert cb.detect_floater_zones(still, downscale=2) == []


def test_detect_floater_zones_single_frame_is_empty():
    # fewer than two frames returns [] before touching frames[0].shape
    assert cb.detect_floater_zones([np.zeros((10, 10), np.uint8)]) == []


# ---------------------------------------------------------------------------
# drop_in_zones
# ---------------------------------------------------------------------------

def test_drop_in_zones_removes_near_and_keeps_far():
    df = pd.DataFrame({'cX': [100.0, 500.0], 'cY': [100.0, 500.0]})
    kept = cb.drop_in_zones(df, zones=[(100, 100)], radius_px=50)
    assert kept['cX'].tolist() == [500.0]


# ---------------------------------------------------------------------------
# center_crop
# ---------------------------------------------------------------------------

def test_center_crop_is_centred_and_square():
    img = np.zeros((100, 200, 3), dtype=np.uint8)
    crop, x0, y0 = cb.center_crop(img, frac=0.5, ref='width')
    assert crop.shape[0] == crop.shape[1]      # square
    assert (x0, y0) == ((200 - 100) // 2, (100 - 100) // 2)
