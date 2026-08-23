"""The one annotated scene the regression test is pinned to.

Separate from the test so that regenerating the golden image and asserting
against it are provably the same inputs. Everything here is deterministic: no
randomness, no dependence on the machine, and no import from `notebooks/`,
which tests may not reach into.

The blobs mirror the ones drawn into `tests/fixtures/bench/dish.png` by
`bench_setup.ensure_bench_data`, so the contours sit on real image content
rather than on empty background. They are restated rather than imported for
that reason.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pandas as pd

from micropick import paths

# (cx, cy, radius) of the blobs in dish.png, and the dish itself.
BLOBS = [(400, 300, 15), (760, 320, 15), (560, 620, 15), (900, 640, 15),
         (300, 640, 42), (650, 200, 14), (686, 210, 14)]
DISH = (648, 486, 460)


def frame_path() -> Path:
    return paths.fixtures_dir() / "bench" / "dish.png"


def golden_path() -> Path:
    return paths.fixtures_dir() / "overlays_golden.png"


def _contour(cx: int, cy: int, radius: int) -> np.ndarray:
    """A closed 24-gon. Deterministic, and shaped like what Otsu returns."""
    angles = np.linspace(0.0, 2.0 * np.pi, 24, endpoint=False)
    points = np.stack([cx + radius * np.cos(angles),
                       cy + radius * np.sin(angles)], axis=1)
    return np.round(points).astype(np.int32).reshape(-1, 1, 2)


def _table(indices) -> pd.DataFrame:
    rows = [BLOBS[i] for i in indices]
    return pd.DataFrame({
        "contour": [_contour(*row) for row in rows],
        "cX": [float(row[0]) for row in rows],
        "cY": [float(row[1]) for row in rows],
    })


def scene() -> dict:
    """Every branch of `annotate` at once, so the golden covers all of them."""
    return {
        "cuboid_df": _table(range(len(BLOBS))),
        "pickable": _table([0, 1, 2, 3, 5, 6]),
        "isolated": _table([0, 1, 2, 3]),
        "bubbles": _table([5]),
        "chosen": _table([0, 2]),
        "verify_radius": 34,
        "floater_zones": [(300, 640, 55), (900, 640, 30)],
        "circle_center": (DISH[0], DISH[1]),
        "circle_radius": DISH[2],
        "status_lines": ["state analyze", "target C3", "held 2"],
    }


def load_frame() -> np.ndarray:
    path = frame_path()
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if frame is None:
        raise FileNotFoundError(
            f"{path} is missing; regenerate it with "
            f"bench_setup.ensure_bench_data()")
    return frame
