"""Pixel to robot mapping for a gantry-mounted camera.

Fitted from one robot-driven sweep of a static marker. Lens distortion, scale
and perspective are absorbed by a single polynomial map, so there are no camera
intrinsics, no chessboard and no separate undistortion stage: frames are used
raw. Re-run the sweep after any change to focus, zoom, camera height or the
height of the observed plane.

Geometry
--------
The camera rides on the gantry, so there is no fixed pixel to deck mapping. What
is invariant is the offset from the gantry to whatever sits under a pixel:

    deck_point = gantry_pose_at_capture + f(pixel)

f is normalised to zero at a reference pixel, so it describes optics alone. The
constant relating the reference pixel to the pipette tip is a separate
calibration and is applied by the caller.

Fitting with several tracked points
-----------------------------------
Each detected marker corner is its own fixed deck point, with its own unknown
deck position. Fitting them jointly means one shared polynomial constrained by
four times as much data, and the tracked corners reach closer to the frame edges
than the marker centre can, which is where distortion is strongest. The unknown
deck positions enter as one free constant per track and are solved alongside the
polynomial. This makes the problem linear.

DEGREE 3 IS THE MINIMUM. Radial distortion displaces a point by x * k1 * r^2,
which is cubic in image coordinates, so a quadratic polynomial reduces exactly
to an affine fit and buys nothing.

This module imports no hardware and no display code.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

from ...config.schema import PixelMap as PixelMapConfig

__all__ = ["PixelMap", "FitReport", "fit_pixel_map", "cross_validate",
           "compare_degrees", "basis"]

MIN_USEFUL_DEGREE = 3


# ---------------------------------------------------------------------------
# polynomial basis
# ---------------------------------------------------------------------------

def basis(u, v, cu: float, cv: float, s: float, degree: int) -> np.ndarray:
    """Monomials of total degree 1..degree in normalised pixel coordinates.

    No constant term: it would be degenerate against the per-track constants.
    Normalisation keeps the design matrix well conditioned at degree 3 and up.
    """
    un = (np.asarray(u, dtype=float) - cu) / s
    vn = (np.asarray(v, dtype=float) - cv) / s
    return np.column_stack([un ** i * vn ** (d - i)
                            for d in range(1, degree + 1)
                            for i in range(d + 1)])


def n_basis_terms(degree: int) -> int:
    return sum(d + 1 for d in range(1, degree + 1))


# ---------------------------------------------------------------------------
# runtime map
# ---------------------------------------------------------------------------

class PixelMap:
    """Applies a fitted map. Wraps the serialisable config with cached arrays."""

    def __init__(self, config: PixelMapConfig):
        self.config = config
        self._coef = np.asarray(config.coef, dtype=float)
        self._zero = np.asarray(config.zero, dtype=float)

    @classmethod
    def from_config(cls, config: PixelMapConfig) -> "PixelMap":
        return cls(config)

    def to_config(self) -> PixelMapConfig:
        return self.config

    # -- evaluation ---------------------------------------------------------

    def offset_mm(self, u, v):
        """Millimetre offset of a pixel from the reference pixel."""
        c = self.config
        out = basis(u, v, c.cu, c.cv, c.s, c.degree) @ self._coef - self._zero
        return out[0] if out.shape[0] == 1 else out

    def to_robot(self, u, v, gantry_xy):
        """Deck coordinates of a pixel, in the frame the robot reports.

        gantry_xy must be the pose read at the moment the frame was captured.
        Any pipette offset is added by the caller: it is a separate calibration
        and does not belong to the optical map.
        """
        g = np.asarray(gantry_xy, dtype=float)
        g = g[..., :2] if g.ndim > 1 else g[:2]
        return g + self.offset_mm(u, v)

    def mm_per_px(self, u, v, eps: float = 1.0) -> tuple[float, float]:
        """Local scale along u and v, from the Jacobian.

        Use this instead of one global ratio: with real optics the scale varies
        by several percent across the frame, so a single number misreports
        object sizes near the edges.
        """
        pts = np.array([[u, v], [u + eps, v], [u, v + eps]], dtype=float)
        p0, pu, pv = np.atleast_2d(self.offset_mm(pts[:, 0], pts[:, 1]))
        return (float(np.linalg.norm(pu - p0) / eps),
                float(np.linalg.norm(pv - p0) / eps))

    def covers(self, u, v, margin_px: float = 0.0) -> bool:
        """Whether a pixel is inside the swept area. Outside it the polynomial
        extrapolates and degrades quickly, so callers should check."""
        return self.config.covers(float(u), float(v), margin_px)


# ---------------------------------------------------------------------------
# fitting
# ---------------------------------------------------------------------------

@dataclass
class FitReport:
    """Diagnostics for one fit. Residuals are in millimetres.

    A residual here is the spread of a fixed deck point recovered from many
    different gantry poses, so it measures the map directly and needs no ground
    truth. It also contains robot repeatability, which is not separable.
    """

    degree: int
    n_poses: int
    n_points: int
    resid_mean_um: float
    resid_max_um: float
    holdout_mean_um: float
    holdout_max_um: float
    track_side_mm: float | None
    coverage: tuple[float, float, float, float]
    coverage_frac: tuple[float, float]
    scale_centre_um: float
    scale_edge_um: float

    # Per pose and per track, in micrometres, shape (n_poses, n_tracks). The
    # mean and the max above cannot tell one pose that flew off from forty-nine
    # drifting a little, and those are different faults with different
    # remedies: the first is a sweep to repeat, the second a loose mount or a
    # focus that moved. Optional and last, so nothing that builds a FitReport
    # positionally is affected; the report is transient and never serialised.
    resid_um: np.ndarray | None = None

    @property
    def scale_variation_pct(self) -> float:
        return 100.0 * (self.scale_edge_um / self.scale_centre_um - 1.0)

    def __str__(self) -> str:
        u0, v0, u1, v1 = self.coverage
        return "\n".join([
            f"degree {self.degree}, {self.n_poses} poses, {self.n_points} points",
            f"  residual   mean {self.resid_mean_um:7.1f}  max {self.resid_max_um:7.1f} um",
            f"  held out   mean {self.holdout_mean_um:7.1f}  max {self.holdout_max_um:7.1f} um",
            f"  coverage   u [{u0:.0f}, {u1:.0f}]  v [{v0:.0f}, {v1:.0f}]  "
            f"({self.coverage_frac[0]*100:.0f} % x {self.coverage_frac[1]*100:.0f} % of frame)",
            f"  scale      centre {self.scale_centre_um:.2f}  edge {self.scale_edge_um:.2f} um/px "
            f"({self.scale_variation_pct:+.1f} %)",
            f"  track side {self.track_side_mm:.4f} mm" if self.track_side_mm else "",
        ]).rstrip()


def _solve(track_px: np.ndarray, gantry: np.ndarray, image_size, degree: int):
    """Joint least squares: shared polynomial plus one free constant per track."""
    n_poses, n_tracks, _ = track_px.shape
    p = track_px.reshape(-1, 2)
    pose = np.repeat(gantry, n_tracks, axis=0)
    track = np.tile(np.arange(n_tracks), n_poses)

    cu, cv = image_size[0] / 2.0, image_size[1] / 2.0
    s = max(image_size) / 2.0

    B = basis(p[:, 0], p[:, 1], cu, cv, s, degree)
    D = np.zeros((len(p), n_tracks))
    D[np.arange(len(p)), track] = 1.0
    A = np.hstack([B, D])

    n_unknowns = A.shape[1]
    if len(p) < n_unknowns + n_tracks:
        raise ValueError(
            f"degree {degree} with {n_tracks} tracks needs more than "
            f"{n_unknowns} observations, got {len(p)}"
        )

    sol = np.column_stack([np.linalg.lstsq(A, -pose[:, j], rcond=None)[0]
                           for j in (0, 1)])
    n_poly = B.shape[1]
    resid = np.linalg.norm(A @ sol + pose, axis=1).reshape(n_poses, n_tracks)
    return sol[:n_poly], -sol[n_poly:], (cu, cv, s), resid


def fit_pixel_map(
    track_px: np.ndarray,
    gantry: np.ndarray,
    image_size: tuple[int, int],
    *,
    degree: int = 3,
    ref_px: tuple[float, float] | None = None,
    sweep_z: float | None = None,
    marker_side_mm: float | None = None,
    cv_folds: int = 7,
) -> tuple[PixelMap, FitReport]:
    """Fit a map from a sweep.

    track_px    (n_poses, n_tracks, 2) pixel of each tracked point per pose.
                Track order must be stable across poses, which is what
                detectMarkers guarantees for the corners of one marker id.
    gantry      (n_poses, 2) gantry XY read back at each pose.
    image_size  (width, height).
    marker_side_mm  nominal side of the tracked square, used only to report an
                independent check: the map never uses it, so agreement between
                nominal and recovered side is evidence the sweep was good.
    """
    track_px = np.asarray(track_px, dtype=float)
    gantry = np.asarray(gantry, dtype=float)
    if track_px.ndim != 3 or track_px.shape[2] != 2:
        raise ValueError(f"track_px must be (n_poses, n_tracks, 2), got {track_px.shape}")
    if len(track_px) != len(gantry):
        raise ValueError(f"{len(track_px)} poses of pixels but {len(gantry)} of gantry")
    if degree < MIN_USEFUL_DEGREE:
        raise ValueError(
            f"degree {degree} cannot represent radial distortion and reduces to an "
            f"affine fit; use {MIN_USEFUL_DEGREE} or more"
        )

    coef, anchors, (cu, cv, s), resid = _solve(track_px, gantry, image_size, degree)
    ref = np.array(ref_px if ref_px is not None else (cu, cv), dtype=float)
    zero = (basis(ref[0:1], ref[1:2], cu, cv, s, degree) @ coef)[0]

    p = track_px.reshape(-1, 2)
    bounds = (float(p[:, 0].min()), float(p[:, 1].min()),
              float(p[:, 0].max()), float(p[:, 1].max()))

    side = None
    n_tracks = track_px.shape[1]
    if n_tracks == 4:
        a = anchors - zero
        side = float(np.mean([np.linalg.norm(a[k] - a[(k + 1) % 4]) for k in range(4)]))

    config = PixelMapConfig(
        degree=degree, cu=cu, cv=cv, s=s,
        coef=coef.tolist(), zero=zero.tolist(), ref=ref.tolist(),
        bounds=list(bounds), image_size=[int(image_size[0]), int(image_size[1])],
        sweep_z=float(sweep_z) if sweep_z is not None else float("nan"),
        marker_side_mm=marker_side_mm,
        marker_side_measured_mm=side,
        n_poses=len(gantry),
        fitted_at=datetime.now(timezone.utc),
    )
    pmap = PixelMap(config)

    holdout = cross_validate(track_px, gantry, image_size,
                             degree=degree, folds=cv_folds)
    w, h = image_size
    sc = float(np.mean(pmap.mm_per_px(w / 2, h / 2)))
    se = float(np.mean(pmap.mm_per_px(bounds[0], bounds[1])))

    report = FitReport(
        degree=degree, n_poses=len(gantry), n_points=len(p),
        resid_mean_um=float(resid.mean() * 1000), resid_max_um=float(resid.max() * 1000),
        holdout_mean_um=float(np.nanmean(holdout) * 1000),
        holdout_max_um=float(np.nanmax(holdout) * 1000),
        track_side_mm=side, coverage=bounds,
        coverage_frac=((bounds[2] - bounds[0]) / w, (bounds[3] - bounds[1]) / h),
        scale_centre_um=sc * 1000, scale_edge_um=se * 1000,
        resid_um=resid * 1000,
    )
    config.holdout_mean_um = report.holdout_mean_um
    config.holdout_max_um = report.holdout_max_um
    return pmap, report


def cross_validate(track_px, gantry, image_size, *, degree: int = 3,
                   folds: int = 7, seed: int = 0) -> np.ndarray:
    """Leave-poses-out cross validation, in millimetres.

    Whole poses are held out rather than individual points: tracked points
    within one pose share the same gantry reading and are correlated, so
    splitting inside a pose would report an optimistic score.
    """
    track_px = np.asarray(track_px, dtype=float)
    gantry = np.asarray(gantry, dtype=float)
    n_poses, n_tracks, _ = track_px.shape
    idx = np.arange(n_poses)
    np.random.default_rng(seed).shuffle(idx)

    err = np.full((n_poses, n_tracks), np.nan)
    for f in range(folds):
        test = idx[f::folds]
        train = np.setdiff1d(idx, test)
        if len(train) < folds:
            continue
        coef, anchors, (cu, cv, s), _ = _solve(track_px[train], gantry[train],
                                               image_size, degree)
        for i in test:
            pred = gantry[i] + basis(track_px[i][:, 0], track_px[i][:, 1],
                                     cu, cv, s, degree) @ coef
            err[i] = np.linalg.norm(pred - anchors, axis=1)
    return err


def compare_degrees(track_px, gantry, image_size, degrees=(1, 2, 3, 4),
                    *, marker_side_mm: float | None = None) -> str:
    """Table of fit quality by degree, for choosing one before saving.

    Degrees below 3 are included on purpose: seeing 1 and 2 give identical
    numbers confirms the data behaves as radial distortion should.
    """
    lines = ["degree |    residual, um    |    held out, um    | track side"]
    for d in degrees:
        try:
            coef, anchors, (cu, cv, s), resid = _solve(
                np.asarray(track_px, float), np.asarray(gantry, float), image_size, d)
            ho = cross_validate(track_px, gantry, image_size, degree=d)
            side = ""
            if np.asarray(track_px).shape[1] == 4:
                a = anchors
                side = f" | {np.mean([np.linalg.norm(a[k] - a[(k+1) % 4]) for k in range(4)]):.4f} mm"
            lines.append(f"  {d}    | {resid.mean()*1000:7.1f} / {resid.max()*1000:7.1f}  "
                         f"| {np.nanmean(ho)*1000:7.1f} / {np.nanmax(ho)*1000:7.1f}{side}")
        except ValueError as exc:
            lines.append(f"  {d}    | {exc}")
    if marker_side_mm:
        lines.append(f"nominal track side {marker_side_mm:.4f} mm")
    return "\n".join(lines)