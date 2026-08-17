"""Floaters, from centroid motion inside fixed per-object windows.

The variance-map detector this replaces asked a threshold computed from the map
to answer an absolute question: is anything moving at all? Multi-Otsu always has
a top class, so on a still dish it labelled the whole dish. That is not a tuning
failure — no threshold derived from the set it partitions can return "nothing
here".

The fix is a different comparison, not a better threshold. Every object is
scored against a number that does not depend on the clip it is in: a noise floor
measured once from clips of cuboids known to be still. Nothing exceeds it on a
still dish, so the empty dish answers itself.

What the answer is for
----------------------
A floating cuboid is not wanted at all — not because the tip would miss it, but
because it is unsuitable for the assay. There is therefore no upper bound on the
threshold: sensitivity is free, and the only constraint is the noise floor
below. This is why no picking tolerance appears anywhere in this module.

The second purpose matters as much as the first. Knowing *where* the motion is
lets a region around it be held back from picking, because a floater sitting in
a clump today may drift out of it and present itself as an isolated, well-formed
candidate a minute later. So every detection is scored, including the ones in
clumps that could never be picked as they stand.

Why the measurements look the way they do
-----------------------------------------
Geometry, not photometry. Brightness variance is in DN squared and moves with
exposure, gain and lighting, so no constant can be written for it. Centroid
displacement converts to microns through the pixel map, and microns are
comparable across sessions.

One window per detection, never merged. Merging boxes into per-clump windows was
tried and made things worse in both directions: it hid the floaters that matter
most, and it tripled the noise floor, because the intensity-weighted centroid of
several blobs moves when brightness shifts between them even though nothing
does. Measured on a dense dish: 2.2 um merged against 0.8 um per-box on the same
frames.

No thresholding inside the window. Taking the centroid of an Otsu component put
the floor on *stationary* cuboids into the hundreds of microns, because the
component covering the centre jumps between blobs. There is nothing to flicker
in an intensity-weighted centroid.

A Gaussian weight, not a flat disc. A neighbour clipping the edge of the window
contributes in proportion to its weight there, so tapering the weight to the
edge suppresses it. Measured: p95 1.61 um flat against 1.22 um Gaussian, and the
crowded objects stopped being the noisy ones.

Calibrate on negatives only. Still cuboids bound the false-positive rate, which
is the error that deadlocks a run: flag everything and the candidate table
empties, the shake retries, and the routine gives up. They say nothing about
misses, but a missed floater costs one empty pickup that `verify_pickup` catches
downstream, and the miss rate can be recovered later from pickup logs without
ever staging a floater.

Layering: this is `core`. Detection enters as boxes the caller already has,
frames as greyscale arrays, scale as microns per pixel. No ultralytics, no
camera, no display, so it runs on synthetic pixels (DESIGN §2).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

__all__ = [
    "Window", "make_windows", "merge_duplicates", "soft_centroid",
    "MotionAccumulator", "Baseline", "fit_baseline", "classify", "Verdict",
    "exclusion_zones", "TABLE_COLUMNS",
]

TABLE_COLUMNS = [
    "obj", "box", "x", "y", "win_w", "win_h", "nn_px", "crowded",
    "n_frames", "n_present", "present_early", "escape_frac",
    "rms_um", "range_um", "speed_um_s", "mass_cv", "usable",
]


# ---------------------------------------------------------------------------
# windows
# ---------------------------------------------------------------------------

@dataclass
class Window:
    """One fixed measurement window, tied to one detection."""

    x0: int
    y0: int
    w: int
    h: int
    box: int = -1
    nn_px: float = float("inf")

    @property
    def centre(self) -> tuple[float, float]:
        return self.x0 + (self.w - 1) / 2.0, self.y0 + (self.h - 1) / 2.0

    @property
    def crowded(self) -> bool:
        return self.nn_px < max(self.w, self.h)


def merge_duplicates(boxes, confs=None, *, centre_frac: float = 0.5):
    """Drop detections that are the same object found twice. Returns keep indices.

    `yolo_iou` is deliberately high so NMS does not fuse touching cuboids, which
    lets near-identical boxes survive on one object. The test here is centre
    distance normalised by diameter rather than IoU, because IoU conflates
    disagreement about position with disagreement about size: two boxes on one
    cuboid differ mostly in size, while two touching cuboids have centres a full
    diameter apart. A cuboid is a solid body, so centres closer than a fraction
    of a diameter are one body seen twice — an absolute, physical criterion.

    Worth doing outside floater detection too: a duplicate gives its original a
    near-zero `min_dist` in `add_derived`, so a good cuboid is dropped from
    `isolated` silently, with nothing in the log to say why.
    """
    boxes = np.asarray(boxes, dtype=float).reshape(-1, 4)
    n = len(boxes)
    if n == 0:
        return np.zeros(0, dtype=int)
    confs = (np.ones(n) if confs is None
             else np.asarray(confs, dtype=float).reshape(-1))

    cx = (boxes[:, 0] + boxes[:, 2]) / 2
    cy = (boxes[:, 1] + boxes[:, 3]) / 2
    diam = np.sqrt(np.maximum(boxes[:, 2] - boxes[:, 0], 1)
                   * np.maximum(boxes[:, 3] - boxes[:, 1], 1))

    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i in range(n):
        for j in range(i + 1, n):
            if np.hypot(cx[i] - cx[j], cy[i] - cy[j]) < centre_frac * min(diam[i], diam[j]):
                ra, rb = find(i), find(j)
                if ra != rb:
                    parent[ra] = rb

    groups: dict[int, list[int]] = {}
    for k in range(n):
        groups.setdefault(find(k), []).append(k)
    # the most confident box wins; averaging with a poor box drags the centre,
    # and the centre is the quantity being measured
    return np.array(sorted(g[int(np.argmax(confs[g]))] for g in groups.values()))


def make_windows(boxes, frame_shape, *, pad_frac: float = 1.2,
                 min_side: int = 24) -> list[Window]:
    """One window per detection. Boxes are never merged.

    `pad_frac` is small on purpose: the window should hold its own object and
    little else. Widening it to 1.6 measurably raised the noise floor by letting
    more of the neighbours in.

    A window that would run off the frame is dropped — a truncated window makes
    the centroid move when the object does not — and so is one below `min_side`,
    where there are too few pixels for a stable centroid.
    """
    boxes = np.asarray(boxes, dtype=float).reshape(-1, 4)
    if len(boxes) == 0:
        return []
    cx = (boxes[:, 0] + boxes[:, 2]) / 2
    cy = (boxes[:, 1] + boxes[:, 3]) / 2

    if len(boxes) > 1:
        d = np.hypot(cx[:, None] - cx[None, :], cy[:, None] - cy[None, :])
        np.fill_diagonal(d, np.inf)
        nn = d.min(axis=1)
    else:
        nn = np.array([np.inf])

    H, W = frame_shape[:2]
    out: list[Window] = []
    for i, b in enumerate(boxes):
        hw = (b[2] - b[0]) / 2 * pad_frac
        hh = (b[3] - b[1]) / 2 * pad_frac
        x0, y0 = int(np.floor(cx[i] - hw)), int(np.floor(cy[i] - hh))
        x1, y1 = int(np.ceil(cx[i] + hw)), int(np.ceil(cy[i] + hh))
        if x1 - x0 < min_side or y1 - y0 < min_side:
            continue
        if x0 < 0 or y0 < 0 or x1 > W or y1 > H:
            continue
        out.append(Window(x0, y0, x1 - x0, y1 - y0, box=i, nn_px=float(nn[i])))
    return out


# ---------------------------------------------------------------------------
# centroid
# ---------------------------------------------------------------------------

def gaussian_weight(h: int, w: int, sigma_frac: float = 0.20) -> np.ndarray:
    """Radial taper for one window, peaking at the centre.

    `sigma_frac` is in units of the window side. Too wide and neighbours at the
    edge still count; too narrow and the object's own far side is clipped, which
    makes the centroid follow whichever part of the cuboid is brighter this
    frame. 0.20 measured best over a dense dish, with 0.30 close behind.
    """
    yy, xx = np.mgrid[:h, :w]
    r2 = (((xx - (w - 1) / 2) / (sigma_frac * w)) ** 2
          + ((yy - (h - 1) / 2) / (sigma_frac * h)) ** 2)
    return np.exp(-r2 / 2.0).astype(np.float32)


def soft_centroid(crop: np.ndarray, weight: np.ndarray | None = None, *,
                  power: float = 2.0) -> tuple[float, float, float]:
    """Intensity-weighted centroid of one crop, with no thresholding.

    Returns (x, y, mass) in crop pixels. `mass` is the total weight and is what
    later decides whether the object is still in the window. Squaring the
    background-subtracted intensity concentrates weight on the object without
    any decision being made about what belongs to it.
    """
    g = crop.astype(np.float32)
    w = np.clip(g - np.median(g), 0.0, None) ** power
    if weight is not None:
        w = w * weight
    mass = float(w.sum())
    if not mass > 0:
        return np.nan, np.nan, 0.0
    h, wd = crop.shape[:2]
    x = float(w.sum(axis=0) @ np.arange(wd, dtype=np.float32) / mass)
    y = float(w.sum(axis=1) @ np.arange(h, dtype=np.float32) / mass)
    return x, y, mass


# ---------------------------------------------------------------------------
# accumulation
# ---------------------------------------------------------------------------

class MotionAccumulator:
    """Feed it frames; ask it for a table.

    Windows are fixed at detection time and never follow their object. A window
    that tracked the object would subtract the very motion being measured, and a
    floater leaving its window is the strongest signal available — it has to
    show up as absence, not as a re-centred crop.

    Frames are not retained. Three floats per window per frame are, so a
    twenty-frame window over two hundred objects costs tens of kilobytes where
    the frames themselves would cost hundreds of megabytes.
    """

    def __init__(self, windows: list[Window], *, warmup: int = 3,
                 sigma_frac: float = 0.20, power: float = 2.0):
        self.windows = windows
        self.warmup = max(1, warmup)
        self.power = power
        self.sigma_frac = sigma_frac
        self._w = [gaussian_weight(win.h, win.w, sigma_frac) for win in windows]
        self._xy: list[list[tuple[float, float]]] = [[] for _ in windows]
        self._mass: list[list[float]] = [[] for _ in windows]
        self.times: list[float] = []

    @property
    def n_frames(self) -> int:
        return len(self.times)

    def update(self, gray: np.ndarray, t: float | None = None) -> None:
        """One greyscale frame. Cheap enough to run in the camera thread."""
        self.times.append(float(len(self.times)) if t is None else float(t))
        for i, win in enumerate(self.windows):
            crop = gray[win.y0:win.y0 + win.h, win.x0:win.x0 + win.w]
            x, y, m = soft_centroid(crop, self._w[i], power=self.power)
            self._xy[i].append((x, y))
            self._mass[i].append(m)

    def table(self, um_per_px: float, *, mass_frac: float = 0.4,
              inner_frac: float = 0.75, min_present: int = 5,
              remove_common_motion: bool = True) -> pd.DataFrame:
        """One row per window, displacement in microns.

        `mass_frac` and `inner_frac` decide presence: the object counts as in
        its window while its weight stays above that fraction of what it had
        during warm-up and its centroid stays inside the inner part of the
        window. Both refer to the object's own first frames — YOLO put a box
        there, so it was present at t=0 by construction. That is an anchor
        outside the current distribution rather than a partition of it, which is
        the whole point of the redesign.
        """
        n_t, n_r = self.n_frames, len(self.windows)
        if n_t == 0 or n_r == 0:
            out = pd.DataFrame(columns=TABLE_COLUMNS)
            out.attrs.update(common_rms_um=0.0, window_s=0.0, n_frames=n_t)
            return out

        xy = np.array(self._xy, dtype=float)              # (n_r, n_t, 2)
        mass = np.array(self._mass, dtype=float)          # (n_r, n_t)
        w0 = min(self.warmup, n_t)
        span = float(self.times[-1] - self.times[0]) if n_t > 1 else 0.0

        ref = np.nanmedian(mass[:, :w0], axis=1)
        ref = np.where(np.isfinite(ref) & (ref > 0), ref, np.inf)

        inside = np.ones((n_r, n_t), dtype=bool)
        for i, win in enumerate(self.windows):
            d = np.hypot(xy[i, :, 0] - (win.w - 1) / 2.0,
                         xy[i, :, 1] - (win.h - 1) / 2.0)
            inside[i] = d < inner_frac * min(win.w, win.h) / 2.0
        present = (mass > mass_frac * ref[:, None]) & inside & np.isfinite(xy[..., 0])

        # Common-mode removal. A camera that creeps or a deck that is nudged
        # moves every window's contents by the same vector; the median over
        # objects estimates it, and a floater — a minority — cannot drag it.
        # Its size is reported rather than swallowed, because the one case where
        # it is not a nuisance is a dish still sloshing after a shake.
        common_rms_um = 0.0
        if remove_common_motion and n_r >= 3:
            seen = np.where(present[..., None], xy, np.nan)
            with np.errstate(invalid="ignore"):
                ok = present.any(axis=1)
                rel = np.full_like(seen, np.nan)
                rel[ok] = seen[ok] - np.nanmean(seen[ok], axis=1, keepdims=True)
                enough = present.sum(axis=0) >= 3
                common = np.zeros((n_t, 2), dtype=float)
                if enough.any():
                    common[enough] = np.nanmedian(rel[:, enough], axis=0)
            common = np.nan_to_num(common)
            common_rms_um = float(np.sqrt((common ** 2).sum(axis=1).mean()) * um_per_px)
            xy = xy - common[None, :, :]

        rows = []
        for i, win in enumerate(self.windows):
            p = present[i]
            early = float(p[:w0].mean())
            q = xy[i][p]
            if len(q) >= min_present:
                dev = q - q.mean(axis=0)
                rms = float(np.sqrt((dev ** 2).sum(axis=1).mean()) * um_per_px)
                rng = float(np.hypot(*(q.max(axis=0) - q.min(axis=0))) * um_per_px)
            else:
                rms = rng = np.nan
            mm = mass[i][np.isfinite(mass[i])]
            rows.append({
                "obj": i, "box": win.box,
                "x": win.centre[0], "y": win.centre[1],
                "win_w": win.w, "win_h": win.h,
                "nn_px": win.nn_px, "crowded": win.crowded,
                "n_frames": n_t, "n_present": int(p.sum()),
                "present_early": early,
                # only meaningful once the object was there to begin with
                "escape_frac": (float(1.0 - p[w0:].mean())
                                if early > 0.5 and n_t > w0 else np.nan),
                "rms_um": rms, "range_um": rng,
                # a lower bound on speed, used to size the exclusion zone
                "speed_um_s": (rng / span if span > 0 and np.isfinite(rng) else np.nan),
                "mass_cv": (float(mm.std() / mm.mean())
                            if mm.size > 1 and mm.mean() > 0 else np.nan),
                # Presence at the start is the whole test. An object that was
                # there and left is fully measured — leaving *is* the reading —
                # so it must not be demoted to `unknown` for want of frames to
                # take an rms over. `rms_um` is simply NaN in that case.
                "usable": bool(early > 0.5),
            })
        out = pd.DataFrame(rows, columns=TABLE_COLUMNS)
        out.attrs.update(common_rms_um=common_rms_um, window_s=span, n_frames=n_t)
        return out


# ---------------------------------------------------------------------------
# baseline
# ---------------------------------------------------------------------------

@dataclass
class Baseline:
    """The noise floor, measured on cuboids known to be still."""

    rms_p50_um: float
    rms_p95_um: float
    rms_max_um: float
    n_objects: int
    n_clips: int
    window_s: float = 0.0
    n_frames: int = 0

    def threshold_um(self, k: float = 4.0, floor_um: float = 10.0) -> float:
        """Where to put the line: `k` times the worst still object, never below
        `floor_um`.

        From the maximum rather than a percentile, which is affordable only
        because there is no upper bound to trade against — a floater is unwanted
        at any amplitude, so the threshold is pushed as low as the floor allows
        and no lower. `floor_um` guards the degenerate case of a clip with too
        few objects to have a meaningful maximum.

        Screen on `usable` before fitting: on still clips the largest values
        otherwise come from windows where the object was never measurable, and
        one of those sets the threshold an order of magnitude too high.
        """
        return max(k * self.rms_max_um, floor_um)

    def __str__(self) -> str:
        return (f"baseline: p50 {self.rms_p50_um:.2f}, p95 {self.rms_p95_um:.2f}, "
                f"max {self.rms_max_um:.2f} um ({self.n_objects} objects, "
                f"{self.n_clips} clips, {self.window_s:.1f} s window)")


def fit_baseline(tables, *, window_s: float = 0.0) -> Baseline:
    """Pool the usable rows of several still clips into one noise floor.

    Several clips, not one: the floor has to cover the working range of dish
    positions and object counts, and one clip cannot show whether it reproduces.
    """
    frames = [t[t["usable"] & t["rms_um"].notna()] for t in tables if len(t)]
    pooled = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    v = pooled["rms_um"].dropna().to_numpy() if len(pooled) else np.zeros(0)
    if v.size == 0:
        raise ValueError("no usable objects in the still clips")
    return Baseline(
        rms_p50_um=float(np.percentile(v, 50)),
        rms_p95_um=float(np.percentile(v, 95)),
        rms_max_um=float(v.max()),
        n_objects=int(v.size), n_clips=len(frames), window_s=window_s,
        n_frames=int(pooled["n_frames"].median()))


# ---------------------------------------------------------------------------
# decision
# ---------------------------------------------------------------------------

@dataclass
class Verdict:
    trusted: bool = True
    note: str = ""
    n_objects: int = 0
    n_floaters: int = 0
    n_unknown: int = 0
    threshold_um: float = 0.0
    median_rms_um: float = float("nan")
    drift_ratio: float = 1.0
    common_um: float = 0.0

    def __str__(self) -> str:
        head = "trusted" if self.trusted else "NOT TRUSTED"
        return (f"[{head}] {self.n_floaters} floaters, {self.n_unknown} unknown, "
                f"of {self.n_objects}; threshold {self.threshold_um:.1f} um, "
                f"median {self.median_rms_um:.2f} um, common {self.common_um:.1f} um"
                + (f" — {self.note}" if self.note else ""))


def classify(df: pd.DataFrame, baseline: Baseline, *, k: float = 4.0,
             floor_um: float = 10.0, escape_max: float = 0.25,
             max_flagged_frac: float = 0.35, max_drift_ratio: float = 3.0,
             max_common_um: float = 100.0) -> tuple[pd.DataFrame, Verdict]:
    """Score a table against the baseline. Adds `is_floater` and `state`.

    `state` is three-valued. A window whose object was never measurable is
    `unknown`, not `still`: the caller must keep it out of the candidates rather
    than assume the best, and it must not count towards the flagged fraction
    either, or a handful of bad windows would condemn a good clip.

    Every detection is scored, crowded ones included. A floater in a clump is
    exactly the one worth knowing about, since it may drift out and present
    itself as a clean candidate before the next measurement.
    """
    out = df.copy()
    thr = baseline.threshold_um(k=k, floor_um=floor_um)
    v = Verdict(n_objects=len(out), threshold_um=thr)

    if len(out) == 0:
        out["is_floater"] = pd.Series(dtype=bool)
        out["state"] = pd.Series(dtype=object)
        v.note = "no windows"
        return out, v

    usable = out["usable"].to_numpy(dtype=bool)
    escaped = usable & (out["escape_frac"].fillna(0.0).to_numpy() > escape_max)
    moved = usable & (out["rms_um"].fillna(0.0).to_numpy() > thr)

    out["is_floater"] = escaped | moved
    out["state"] = np.where(~usable, "unknown",
                            np.where(escaped, "escaped",
                                     np.where(moved, "moving", "still")))

    v.n_unknown = int((~usable).sum())
    v.n_floaters = int(out["is_floater"].sum())
    if usable.any():
        v.median_rms_um = float(out.loc[usable, "rms_um"].median())
    v.common_um = float(df.attrs.get("common_rms_um", 0.0))

    # Has the measurement itself changed? The median over all objects is
    # dominated by still ones, so it should reproduce the baseline. If it does
    # not, exposure, focus or vibration moved, and the absolute threshold is no
    # longer the number it was calibrated to be. This camera is known to ignore
    # the auto-exposure control on the default backend, so the check earns its
    # place.
    if baseline.rms_p50_um > 0 and np.isfinite(v.median_rms_um):
        v.drift_ratio = v.median_rms_um / baseline.rms_p50_um

    flagged_frac = v.n_floaters / max(int(usable.sum()), 1)

    if v.common_um > max_common_um:
        v.trusted = False
        v.note = (f"everything moved together by {v.common_um:.0f} um; the gantry "
                  f"is still ringing, the deck was knocked, or the dish has not "
                  f"settled after a shake")
    elif v.drift_ratio > max_drift_ratio:
        v.trusted = False
        v.note = (f"noise floor is {v.drift_ratio:.1f}x the baseline; re-measure "
                  f"the baseline before trusting the threshold")
    elif flagged_frac > max_flagged_frac and v.n_floaters > 2:
        v.trusted = False
        v.note = (f"{flagged_frac:.0%} of measurable objects flagged; the dish "
                  f"has not settled, or the clip is bad")
    elif v.n_unknown > 0.5 * len(out):
        v.trusted = False
        v.note = f"{v.n_unknown} of {len(out)} windows not measurable"

    if not v.trusted:
        # a broken measurement is no information, never "discard everything"
        out["is_floater"] = False
        out["state"] = "unknown"

    return out, v


# ---------------------------------------------------------------------------
# exclusion
# ---------------------------------------------------------------------------

def exclusion_zones(df: pd.DataFrame, um_per_px: float, *,
                    horizon_s: float = 60.0, min_radius_um: float = 500.0,
                    max_radius_um: float = 5000.0) -> list[tuple[float, float, float]]:
    """Flagged objects -> (x, y, radius_px) circles that must not be picked from.

    The radius is where the floater could have reached by the next measurement,
    from its own observed speed rather than one global constant: a drifter earns
    a wide circle and one trembling in place a narrow one. `horizon_s` should be
    the interval between floater checks, so the zone expires with the reading
    that produced it instead of aging silently.

    A floater whose window it escaped has no bounded speed — it left. Those get
    `max_radius_um`, since nothing observed says where it stopped.
    """
    if len(df) == 0 or "is_floater" not in df:
        return []
    zones = []
    for r in df[df["is_floater"]].itertuples():
        if getattr(r, "state", "") == "escaped" or not np.isfinite(r.speed_um_s):
            radius_um = max_radius_um
        else:
            radius_um = r.speed_um_s * horizon_s
        radius_um = float(np.clip(radius_um, min_radius_um, max_radius_um))
        zones.append((float(r.x), float(r.y), radius_um / um_per_px))
    return zones