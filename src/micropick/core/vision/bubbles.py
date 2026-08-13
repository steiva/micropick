"""Bubbles: telling a refracting sphere from a scattering solid.

Pipetting leaves air bubbles in the dish, and YOLO boxes one as readily as a
microtissue. The shape windows in `cuboids.select_pickable` cannot help: a bubble
is round, convex and symmetric, so it passes `circularity`, `solidity` and
`radial_cv` comfortably. The difference is optical, not geometric.

Two features, both read off the contour `otsu_contour` already computes for
`solidity` and `radial_cv`, so no extra image processing is needed - only the
pixels inside that contour, which the geometry path throws away:

  core_ratio = mean(r < 0.3R) / mean(0.4R <= r < 0.85R)
      A bubble refracts, so it images as a doughnut: the centre is darker than
      the rim (0.5-0.7). A solid scatters off its whole face, giving a flat
      plateau (~1.0).

  spec_ratio = p99 / median inside the mask
      A bubble focuses light into a caustic (2.0-2.3). A solid scatters
      diffusely (~1.1).

Measurement and judgement are kept apart. `bubble_features` takes pixels and
knows no thresholds; `select_bubbles` takes a table and the thresholds and never
touches an image. That split is what makes re-tuning possible without a camera:
the features are recorded for every detection (`cuboids.build_cuboid_df`), so
`select_bubbles` can be re-run over a logged table with new numbers.

Provenance of the thresholds
----------------------------
max_core_ratio 0.85 and min_spec_ratio 1.60 were measured on 4 bubble and 4
cuboid crops (2026-08) and checked for stability against a +-30% error in the
contour. That is eight samples: the separation is real, but the margin is thin -
`spec_ratio` reaches 1.41 on a cuboid against a threshold of 1.60, so 13%. Two
consequences worth knowing before trusting them.

First, `spec_ratio` is a peak over a median, so it moves with exposure and
saturates once the caustic clips at 255; it does not survive a change of optics
or illumination. Re-measure. `mask_median` is returned alongside the two ratios
so that such a drift is visible in the logged data rather than silent.

Second, the features are noise-limited at the small end. At the calibrated
0.026 mm/px, a 350-500 um object is 142-290 px in area, R = 6.7-9.6 px. The core
disc is then 12-26 pixels and `p99` sits about one pixel from the top of the
mask, which is very likely why a cuboid ever reaches 1.41 at all. Any re-tune
has to condition on `area`; `min_area_px` plus the core and ring pixel floors
below are what stop an object too small to measure from being judged.

Why the filled contour and not the Otsu mask
--------------------------------------------
`otsu_contour` has a binary mask in hand and discards it, which invites reusing
it here. It must not be: a bubble's dark core sits *below* the Otsu threshold, so
the thresholded component is an annulus with a hole - precisely the pixels
`core_ratio` has to average are the ones missing from it. Filling the external
contour puts the core back.

The same darkness bends the contour itself. `otsu_contour` picks the component
under the box's centre pixel, which for a bubble is background, so it always
falls back to the largest component; and a rim only a few pixels wide can be
broken into arcs by the morphological opening, leaving a crescent whose centroid
is off-centre. Such an object arrives here with low solidity and high radial_cv
and is rejected on shape long before its features matter, which is one reason
`cuboids.label_rejections` keeps the four stage tests as separate columns
instead of trusting a single label.
"""

from __future__ import annotations

import cv2
import numpy as np
import pandas as pd

from ...config.schema import PickingConfig

__all__ = ["FEATURES", "bubble_features", "bubble_margin", "select_bubbles"]

# What `bubble_features` measures. Named once, because `build_cuboid_df` fills
# these columns with NaN for an object it could not measure and must not have to
# know the key names by hand.
FEATURES = ("core_ratio", "spec_ratio", "mask_median")


def bubble_features(gray: np.ndarray, cnt, *, center=None, core_r: float = 0.30,
                    ring_window=(0.40, 0.85), min_area_px: int = 20):
    """Optical features of one contour -> dict, or None if not measurable.

    `gray` is the whole greyscale frame and `cnt` a contour in frame
    coordinates, exactly as `otsu_contour` returns them; the crop is taken here
    rather than by the caller, so there is one cropping convention and no shift
    bookkeeping at the call site.

    The crop is the contour's bounding box. Its extent cannot change the answer,
    because every pixel read is selected from the filled mask first and the
    radial zones only ever narrow that set - no pixel outside the contour can
    enter a mean or a percentile however much background surrounds it. So the
    tight box is chosen purely for cost: O(box) per object instead of O(frame),
    which at `yolo_max_det = 600` is the difference between a millisecond and a
    second. For the same reason R is derived from the area and the centroid from
    the moments; neither may come from the crop's shape, or the padding would
    quietly become a parameter of the measurement.

    `center` accepts the centroid `contour_metrics` has already computed, to
    avoid a second pass over the moments for every object in the frame.

    None means "cannot tell", and every caller must read it that way rather than
    as "not a bubble": too small, or too few pixels in the core or the rim to
    average. A cuboid must never be discarded because its features were
    unreadable.
    """
    area = cv2.contourArea(cnt)
    if area < min_area_px:
        return None

    x, y, w, h = cv2.boundingRect(cnt)
    crop = gray[y:y + h, x:x + w]
    if crop.size == 0:
        return None

    mask = np.zeros(crop.shape, np.uint8)
    cv2.drawContours(mask, [cnt - np.array([[[x, y]]], dtype=cnt.dtype)],
                     -1, 255, -1)
    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return None

    if center is None:
        M = cv2.moments(cnt)
        if M["m00"] == 0:
            return None
        cx, cy = M["m10"] / M["m00"] - x, M["m01"] / M["m00"] - y
    else:
        cx, cy = center[0] - x, center[1] - y

    R = np.sqrt(area / np.pi)
    v = crop[ys, xs].astype(np.float32)
    rr = np.hypot(xs - cx, ys - cy) / R

    r0, r1 = ring_window
    core = v[rr < core_r]
    ring = v[(rr >= r0) & (rr < r1)]
    # A 350 um object at 0.026 mm/px offers about a dozen core pixels, so these
    # floors are close to the working size; below them the means are noise.
    if core.size < 4 or ring.size < 8:
        return None

    ring_mean = float(ring.mean())
    median = float(np.median(v))
    if ring_mean <= 0 or median <= 0:
        return None

    return dict(core_ratio=float(core.mean()) / ring_mean,
                spec_ratio=float(np.percentile(v, 99)) / median,
                mask_median=median)


def bubble_margin(df: pd.DataFrame, cfg: PickingConfig) -> pd.Series:
    """Signed distance to the verdict, in fractions of each threshold.

    Positive is evidence of a bubble. The two features are combined the same way
    the verdict combines them, so `select_bubbles` is exactly `margin >= 0`:
    `min` under an AND, `max` under an OR. NaN where a feature was not measured,
    and NaN propagates rather than being skipped - an object judged on one
    feature because the other was unreadable would be a silent change of rule.

    This is the quantity to watch on a real run, since 0 is where an object
    flips. It is also the one column that goes stale the moment the thresholds
    are re-set; `core_ratio` and `spec_ratio` are the durable record.
    """
    d_core = ((cfg.bubble_max_core_ratio - df["core_ratio"])
              / cfg.bubble_max_core_ratio).rename("core")
    d_spec = ((df["spec_ratio"] - cfg.bubble_min_spec_ratio)
              / cfg.bubble_min_spec_ratio).rename("spec")
    pair = pd.concat([d_core, d_spec], axis=1)
    return (pair.min(axis=1, skipna=False) if cfg.bubble_require_both
            else pair.max(axis=1, skipna=False))


def select_bubbles(df: pd.DataFrame, cfg: PickingConfig) -> pd.Series:
    """Boolean mask of rows judged to be bubbles. Mirrors `select_pickable`.

    Table in, mask out, no pixels: this is what lets the thresholds be re-set
    from a log of recorded features instead of from a fresh set of crops.

    An unmeasured object is not a bubble. A NaN margin compares False, so the
    doubt falls on keeping the object - the failure that costs a well is
    discarding a microtissue, not aspirating a bubble that was missed.
    """
    if len(df) == 0 or "core_ratio" not in df:
        return pd.Series(False, index=df.index, dtype=bool)
    return bubble_margin(df, cfg) >= 0
