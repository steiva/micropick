"""Cuboid detection: frames in, tables and masks out.

The picking pipeline turns a dish image into a table of candidate microtissues
and a subset judged safe to pick. Each step here is a plain function that takes
a frame (or a var-map, or a DataFrame) and returns a DataFrame, a mask or a
list. There are no windows, no cameras and no keyboard: this module is `core`
and obeys the layering in DESIGN section 2, so the whole pipeline runs on
synthetic pixels with no robot attached.

YOLO is not loaded here. `detect_boxes` takes an already-constructed
`ultralytics.YOLO` object, so the weights, the device and their lifetime belong
to the caller and the module stays importable without ultralytics installed.

Colour order
------------
Frames are OpenCV-order **BGR** throughout, the same array `build_cuboid_df`
greyscales. `detect_boxes` is the single point that hands a frame to the model,
and it converts to RGB there, because YOLO expects RGB. In the old notebook the
standalone predicts converted explicitly while the pipeline did not, so the
model silently saw swapped channels on the pipeline path; doing the conversion
in one place removes that split.

Ported from the pre-rewrite `main.py`. Fixes made in the move are noted at the
functions they touch.
"""

from __future__ import annotations

import cv2
import numpy as np
import pandas as pd
from scipy import ndimage
from scipy.spatial import cKDTree
from skimage.filters import threshold_multiotsu

from ...config.schema import PickingConfig
from . import bubbles

__all__ = [
    "detect_boxes", "otsu_contour", "contour_metrics", "build_cuboid_df",
    "add_derived", "select_pickable", "roi_mask",
    "REJECT_REASONS", "STAGE_COLUMNS", "label_rejections", "reject_counts",
    "temporal_variance", "analyze_mask", "check_distance_from_center",
    "detect_floater_zones", "in_zones", "drop_in_zones",
    "center_crop", "center_crop_box",
]

# Why a detection is not eligible, in the order the reasons are decided. A row
# takes the first one that applies, so the reasons partition the detections and
# their counts add up to the number of rows - which is what makes "the filter ate
# every cuboid" distinguishable from "there were no bubbles".
#
# `bubble` comes before `floater` deliberately. A bubble drifts, so it is nearly
# always inside a floater zone as well; ordering floater first would label almost
# every bubble `floater` and drive the bubble count to zero exactly when bubbles
# are the problem. Nothing is lost either way, because `label_rejections` also
# returns the four tests as independent columns, so no count depends on the order
# and it can be changed later without invalidating recorded data.
REJECT_REASONS = ("shape", "bubble", "floater", "crowded")

# The four tests behind those reasons, kept per row. `shape_ok` is the only one
# stated positively, because that is the direction `select_pickable` answers in.
STAGE_COLUMNS = ("shape_ok", "is_bubble", "in_floater_zone", "crowded")


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------

def detect_boxes(model, frame: np.ndarray, cfg: PickingConfig):
    """Run YOLO and return (boxes_xyxy, confidences) as numpy arrays.

    `frame` is OpenCV-order BGR; it is converted to RGB here, in the one place
    that touches the model, because YOLO was trained on RGB. `model` is a
    ready-to-use `ultralytics.YOLO`; nothing is loaded inside this module.
    """
    rgb = frame[..., ::-1]
    r = model.predict(rgb, imgsz=cfg.yolo_imgsz, conf=cfg.yolo_conf,
                      iou=cfg.yolo_iou, max_det=cfg.yolo_max_det,
                      verbose=False)[0]
    return r.boxes.xyxy.cpu().numpy(), r.boxes.conf.cpu().numpy()


def otsu_contour(gray: np.ndarray, box, pad: int = 6, open_k: int = 3):
    """Otsu inside one box -> the contour in frame coordinates, or None.

    The component covering the box centre is taken, so the cuboid in the middle
    wins over a rim or a neighbour touching the box from the side.
    """
    H, W = gray.shape
    x1, y1, x2, y2 = (int(v) for v in box)
    X1, Y1 = max(0, x1 - pad), max(0, y1 - pad)
    X2, Y2 = min(W, x2 + pad), min(H, y2 + pad)
    crop = gray[Y1:Y2, X1:X2]
    if crop.size == 0:
        return None

    _, m = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if open_k > 1:
        m = cv2.morphologyEx(
            m, cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_k, open_k)))

    n, lbl, stats, _ = cv2.connectedComponentsWithStats(m)
    if n <= 1:
        return None
    h, w = m.shape
    lab = lbl[h // 2, w // 2]
    if lab == 0:
        lab = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))

    cnts, _ = cv2.findContours((lbl == lab).astype(np.uint8) * 255,
                               cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cnts:
        return None
    cnt = max(cnts, key=cv2.contourArea)
    return (cnt + np.array([[X1, Y1]], dtype=np.int32)).astype(np.int32)


def contour_metrics(cnt):
    """Geometry of one contour (frame coordinates) -> dict, or None if degenerate.

    radial_cv is the spread of vertex distance from the centroid, a symmetry
    measure that accepts both a circle and a square while rejecting ragged blobs.
    """
    area = cv2.contourArea(cnt)
    per = cv2.arcLength(cnt, True)
    if area <= 0 or per <= 0:
        return None
    M = cv2.moments(cnt)
    if M['m00'] == 0:
        return None

    cx, cy = M['m10'] / M['m00'], M['m01'] / M['m00']
    (_, _), (rw, rh), _ = cv2.minAreaRect(cnt)
    hull_a = cv2.contourArea(cv2.convexHull(cnt))
    pts = cnt.reshape(-1, 2).astype(np.float32)
    rad = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)

    return dict(
        cX=cx, cY=cy, contour=cnt, area=area,
        aspect_ratio=max(rw, rh) / max(min(rw, rh), 1e-6),
        circularity=4 * np.pi * area / (per * per),
        solidity=area / hull_a if hull_a > 0 else 0.0,
        radial_cv=float(rad.std() / rad.mean()) if rad.mean() > 0 else 1.0,
    )


def build_cuboid_df(gray: np.ndarray, boxes, confs, roi_mask=None,
                    pad: int = 6, open_k: int = 3,
                    bubble_core_r: float = 0.30,
                    bubble_ring_window=(0.40, 0.85),
                    bubble_min_area_px: int = 20) -> pd.DataFrame:
    """Boxes -> a DataFrame of contour geometry and optics, one row per box.

    `roi_mask` drops objects whose centre falls outside the working zone.
    `open_k` is threaded through to `otsu_contour`; in the old code the config
    value existed but never reached the morphology step.

    The bubble features are measured here, on the same contour the geometry comes
    from, and unconditionally: whether they cause a rejection is decided later by
    `label_rejections`. The thresholds they will be judged against rest on eight
    crops, so a run has to accumulate features for objects that are kept as well
    as ones that are dropped, or there is never any data to replace them with.

    Rows dropped above - no Otsu component, a degenerate contour, a centre
    outside the ROI - leave no trace here, so a count of reasons adds up to
    `len(df)` and not to `len(boxes)`. Callers that report one should report both.
    """
    rows = []
    for box, cf in zip(boxes, confs):
        cnt = otsu_contour(gray, box, pad=pad, open_k=open_k)
        if cnt is None:
            continue
        mt = contour_metrics(cnt)
        if mt is None:
            continue
        if roi_mask is not None and roi_mask[int(mt['cY']), int(mt['cX'])] == 0:
            continue
        mt['conf'] = float(cf)

        f = bubbles.bubble_features(gray, cnt, center=(mt['cX'], mt['cY']),
                                    core_r=bubble_core_r,
                                    ring_window=bubble_ring_window,
                                    min_area_px=bubble_min_area_px)
        # Written even when unmeasurable, so the columns cannot go missing on a
        # frame where every object degenerates - which would leave the label step
        # below with nothing to read and turn a bad frame into a crash.
        for key in bubbles.FEATURES:
            mt[key] = np.nan if f is None else f[key]
        rows.append(mt)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# derived quantities and selection
# ---------------------------------------------------------------------------

def add_derived(df: pd.DataFrame, size_ratio: float, one_d_ratio: float,
                circle_center) -> pd.DataFrame:
    """Physical size, nearest-neighbour distance and distance to the dish centre.

    size_ratio and one_d_ratio are passed in rather than read from a global; the
    pixel map that supersedes them is applied by the caller, not here.
    """
    if len(df) == 0:
        return df
    df['diameter_microns'] = 2 * np.sqrt((df.area * size_ratio * 10e5) / np.pi)

    xy = df[['cX', 'cY']].values
    if len(xy) > 1:
        d, _ = cKDTree(xy).query(xy, k=2)
        df['min_dist'] = d[:, 1]
    else:
        df['min_dist'] = np.inf
    df['min_dist_mm'] = df.min_dist * one_d_ratio

    df['distance_to_center'] = np.hypot(df.cX - circle_center[0],
                                        df.cY - circle_center[1])
    return df


def select_pickable(df: pd.DataFrame, cfg: PickingConfig):
    """Boolean mask of rows inside every shape and position window."""
    return (
        df.diameter_microns.between(*cfg.cuboid_size_threshold) &
        df.aspect_ratio.between(*cfg.aspect_ratio_window) &
        df.circularity.between(*cfg.circularity_window) &
        (df.solidity >= cfg.min_solidity) &
        (df.radial_cv <= cfg.max_radial_cv) &
        (df.distance_to_center <= cfg.circle_radius)
    )


def label_rejections(df: pd.DataFrame, cfg: PickingConfig,
                     floater_zones=()) -> pd.DataFrame:
    """Why each detection is not eligible: the four tests, and one reason.

    Returns a frame indexed like `df` carrying `STAGE_COLUMNS` as independent
    booleans, the bubble `margin`, and a `reject_reason` string that is `""` for
    an object that passed everything. Must run after `add_derived`, whose
    spacing and dish-distance columns two of the tests read.

    A rejected object is meant to stay in the table wearing its reason rather
    than vanish from a filtered copy: without that, a frame with no bubbles and a
    frame whose cuboids were all eaten by the filter look identical, and at 13%
    of margin on `spec_ratio` the second is a real possibility.

    Both the label and the raw tests are returned because one object can fail
    several at once - a bubble also floats, and a bubble whose rim the
    morphological opening broke also fails on shape. Any single label therefore
    undercounts something. With the booleans present every count is recoverable:
    how many bubbles were seen at all, and how many objects the filter alone
    cost, which are different numbers and both worth having.

    `bubble_filter_enabled` gates only the rejection. `is_bubble` and `margin`
    are filled in either way, so a run with the filter off still records how many
    bubbles were there and what switching it on would have cost.
    """
    if len(df) == 0:
        return pd.DataFrame({
            **{c: pd.Series(dtype=bool) for c in STAGE_COLUMNS},
            "margin": pd.Series(dtype=float),
            "reject_reason": pd.Series(dtype=object),
        })

    shape_ok = np.asarray(select_pickable(df, cfg), dtype=bool)
    is_bubble = np.asarray(bubbles.select_bubbles(df, cfg), dtype=bool)
    in_zone = in_zones(df, floater_zones, cfg.floater_zone_radius_px)
    crowded = np.asarray(df.min_dist_mm <= cfg.minimum_distance, dtype=bool)

    tests = {"shape": ~shape_ok,
             "bubble": is_bubble & bool(cfg.bubble_filter_enabled),
             "floater": in_zone,
             "crowded": crowded}
    reason = np.select([tests[r] for r in REJECT_REASONS], REJECT_REASONS,
                       default="")

    return pd.DataFrame({
        "shape_ok": shape_ok, "is_bubble": is_bubble,
        "in_floater_zone": in_zone, "crowded": crowded,
        "margin": bubbles.bubble_margin(df, cfg).values,
        # object dtype, not the fixed-width string numpy hands back: a longer
        # reason added later would otherwise be silently truncated
        "reject_reason": reason.astype(object),
    }, index=df.index)


def reject_counts(df: pd.DataFrame) -> dict:
    """How many rows carry each reason, and how many carry none.

    Zeroes are spelled out. A missing `bubble` key reads as "the filter was not
    running" when it means "the filter rejected nothing", and telling those two
    apart is the whole point of counting. Plain ints, because a numpy integer
    renders as `np.int64(4)` inside a printed event.
    """
    empty = {"accepted": 0, **{r: 0 for r in REJECT_REASONS}}
    if len(df) == 0 or "reject_reason" not in df:
        return empty
    r = df["reject_reason"]
    return {"accepted": int((r == "").sum()),
            **{name: int((r == name).sum()) for name in REJECT_REASONS}}


def roi_mask(gray: np.ndarray, cfg: PickingConfig,
             one_d_ratio: float) -> np.ndarray:
    """Filled circle of the working zone, grown by the minimum spacing."""
    mask = np.zeros(gray.shape, np.uint8)
    cv2.circle(mask, cfg.circle_center,
               cfg.circle_radius + int(cfg.minimum_distance / one_d_ratio),
               255, -1)
    return mask


# ---------------------------------------------------------------------------
# floaters: motion between frames
# ---------------------------------------------------------------------------

def temporal_variance(frames, downscale: int = 4, step: int = 3) -> np.ndarray:
    """Per-pixel std of brightness over time. `frames` is a list of greyscale
    arrays. Computed with Welford's method so a long clip needs no frame stack."""
    mean = None
    M2 = None
    n = 0
    frames = list(frames)
    for f in frames[::step]:
        if downscale > 1:
            f = cv2.resize(f, (f.shape[1] // downscale, f.shape[0] // downscale))
        f = f.astype(np.float32)
        if mean is None:
            mean = np.zeros_like(f)
            M2 = np.zeros_like(f)
        n += 1
        delta = f - mean
        mean += delta / n
        M2 += delta * (f - mean)
    return np.sqrt(M2 / max(n - 1, 1))


def analyze_mask(var_map: np.ndarray, scale: float = 1.0, min_area: int = 3,
                 mad_k: float = 5.0, require_floor: bool = True):
    """Threshold a variance map into moving blobs. Always returns (mask, df).

    require_floor=True keeps a robust MAD floor as a hard minimum, so Otsu cannot
    drop the threshold below it and invent blobs on a still frame. An empty
    result is a zero mask and a DataFrame with the right columns.
    """
    cols = ['label', 'x', 'y', 'area_px', 'area_video_px',
            'mean_var', 'max_var', 'bbox_w', 'bbox_h', 'elongation']
    empty = (np.zeros(var_map.shape, dtype=bool), pd.DataFrame(columns=cols))

    med = np.median(var_map)
    mad = np.median(np.abs(var_map - med))
    t_floor = med + mad_k * 1.4826 * mad

    try:
        t_multi = threshold_multiotsu(var_map, classes=3)[1]
    except ValueError:
        t_multi = 0.0

    if require_floor:
        t = max(t_multi, t_floor)
    else:
        t = t_multi if t_multi > 0 else t_floor

    if var_map.max() <= t:
        return empty

    mask = var_map > t
    mask = ndimage.binary_closing(mask, iterations=5)

    labels, n = ndimage.label(mask)
    if n == 0:
        return empty

    idx = range(1, n + 1)
    centroids = ndimage.center_of_mass(mask, labels, idx)
    areas = ndimage.sum(mask, labels, idx)
    mean_var = ndimage.mean(var_map, labels, idx)
    max_var = ndimage.maximum(var_map, labels, idx)
    slices = ndimage.find_objects(labels)

    rows = []
    for i, (cy, cx) in enumerate(centroids):
        if areas[i] < min_area:
            continue
        sl = slices[i]
        h = sl[0].stop - sl[0].start
        w = sl[1].stop - sl[1].start
        elong = max(w, h) / max(min(w, h), 1)
        rows.append({
            'label': i + 1,
            'x': cx * scale, 'y': cy * scale,
            'area_px': areas[i], 'area_video_px': areas[i] * scale * scale,
            'mean_var': mean_var[i], 'max_var': max_var[i],
            'bbox_w': w, 'bbox_h': h, 'elongation': elong,
        })

    if not rows:
        return empty

    df = pd.DataFrame(rows).sort_values('area_px', ascending=False).reset_index(drop=True)
    return mask, df


def check_distance_from_center(df: pd.DataFrame, img_shape, r_max: float = 930,
                               scale: float = 1.0) -> pd.DataFrame:
    """Flag rows whose (x, y) sit further than r_max from the frame centre."""
    H, W = img_shape[:2]
    cx, cy = (W / 2.0) * scale, (H / 2.0) * scale

    df = df.copy()
    df['dist_from_center'] = np.sqrt((df['x'] - cx) ** 2 + (df['y'] - cy) ** 2)
    df['too_far'] = df['dist_from_center'] > r_max
    return df


def detect_floater_zones(frames, downscale: int = 2, step: int = 1,
                         scale: float | None = None, min_area: int = 3,
                         mad_k: float = 5.0):
    """A clip -> a list of (x, y) floater centres in video pixels, else empty.

    The frame shape used for the centre test comes from frames[0]; the old code
    read an undeclared module-level `frame` here and raised NameError the moment
    a floater passed the earlier filters.
    """
    frames = list(frames)
    if len(frames) < 2:
        return []
    if scale is None:
        scale = float(downscale)

    var_map = temporal_variance(frames, downscale=downscale, step=step)
    _mask, df = analyze_mask(var_map, scale=scale, min_area=min_area, mad_k=mad_k)
    df = df[df['elongation'] < 2.5]
    df = check_distance_from_center(df, frames[0].shape, r_max=800)
    df = df[~df['too_far']]

    if len(df) == 0:
        return []
    return list(zip(df.x, df.y))


def in_zones(df: pd.DataFrame, zones, radius_px: float) -> np.ndarray:
    """Boolean array: does each row's centre fall inside any floater zone?

    An object exactly at radius_px counts as inside, which is the complement of
    the strict `>` that decided what to keep when this was only ever a filter.
    """
    hit = np.zeros(len(df), bool)
    if len(df) == 0 or not zones:
        return hit
    xy = df[['cX', 'cY']].values
    for zx, zy in zones:
        hit |= np.hypot(xy[:, 0] - zx, xy[:, 1] - zy) <= radius_px
    return hit


def drop_in_zones(df: pd.DataFrame, zones, radius_px: float) -> pd.DataFrame:
    """Drop rows whose centre lies within radius_px of any floater zone.

    The picking session goes through `label_rejections` instead, which records
    the reason on the row rather than removing it. Kept for a caller that wants
    the plain filter and no bookkeeping.
    """
    if len(df) == 0 or not zones:
        return df
    return df[~in_zones(df, zones, radius_px)]


# ---------------------------------------------------------------------------
# frame utility
# ---------------------------------------------------------------------------

def center_crop_box(shape, frac: float = 0.5, ref: str = 'width'):
    """Where the centred square crop sits: (x0, y0, side), in full-frame pixels.

    Separate from center_crop because the origin is needed without a frame to
    hand: anything drawn on a cropped view has to be shifted by it, and the
    shift is computed once from the camera's resolution rather than per frame.
    """
    H, W = shape[:2]
    base = {'width': W, 'height': H, 'min': min(H, W)}[ref]
    side = min(int(base * frac), H, W)
    return (W - side) // 2, (H - side) // 2, side


def center_crop(img: np.ndarray, frac: float = 0.5, ref: str = 'width'):
    """Centred square crop. Returns (crop, x0, y0) so detections can be mapped
    back to full-frame coordinates. Deduplicated from three identical copies."""
    x0, y0, side = center_crop_box(img.shape, frac, ref)
    return img[y0:y0 + side, x0:x0 + side], x0, y0
