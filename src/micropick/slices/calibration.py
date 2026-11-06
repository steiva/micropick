"""From a pixel of the microscope's picture to the robot's X and Y.

The microscope stands still and the tool moves in its picture, so one
affine map holds for the whole field, with no gantry pose added: where the
main program's `PixelMap` says what lies under a camera that rides on the
gantry, this says where the robot has to be for the tool's point to sit on
a pixel. The tool's point is the apex of its V, found by matching
`blade_template.png`. The frames are the mirrored ones the operator sees
(`rig.Mirrored`), so every pixel here is one of those.

What a bad map does
-------------------
The notebook this replaces fitted whatever it got, and the map is applied
to clicks. Run through its code, two points left over from an earlier frame
sent a corner of the picture to X 523, and a template stuck on something
that does not move to X -706: a fit of four near-equal pixels has an
enormous scale. So nothing is stored unless `judge` passes, and a click is
checked again by `click_refusal`. Three things it did are gone on purpose:

- a pair is the pose read and the pixel found at that pose, never matched up
  by sorting, which made a turned camera look right and move the wrong way;
- a point not found stops the calibration, where the notebook kept the
  point of the frame before;
- the match is the best one, not `groupRectangles`, which drops a match that
  stands alone: a sharp peak is what a good match looks like.

The limits are constants, to be set on the bench. No Qt and no robot here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

__all__ = ["CalibrationRefused", "Match", "PixelToRobot", "TEMPLATE_PATH",
           "SPACING_MM", "SPACING_RANGE_MM", "MATCH_THRESHOLD", "check",
           "click_refusal", "find_blade", "fit", "judge", "load_template",
           "offsets", "room_refusal", "verify"]

TEMPLATE_PATH = Path(__file__).with_name("blade_template.png")
# The V's apex, from the template's center.
APEX_FROM_CENTER = (200, 0)
MATCH_THRESHOLD = 0.7

# How far from the start the four points are, each way.
SPACING_MM = 0.5
SPACING_RANGE_MM = (0.1, 1.0)
# For the room check before anything has been measured: the scale of the
# microscope as last calibrated by the notebook.
NOMINAL_MM_PER_PX = 0.0039

MM_PER_PX_RANGE = (0.001, 0.020)
MAX_SCALE_DIFFERENCE = 0.10          # between the two axes, as a fraction
MAX_SKEW_DEG = 5.0                   # of the axes from perpendicular
MAX_RESIDUAL_MM = 0.02               # a point's distance from the fit
MAX_VERIFY_MM = 0.02                 # the point back at the start


class CalibrationRefused(RuntimeError):
    """Why a calibration is not used, as a sentence for the operator."""


@dataclass(frozen=True)
class Match:
    """The template found: its top left corner and size, its score, and
    `point`, the V's apex."""

    x: int
    y: int
    width: int
    height: int
    score: float

    @property
    def point(self) -> tuple[float, float]:
        return (self.x + self.width / 2 + APEX_FROM_CENTER[0],
                self.y + self.height / 2 + APEX_FROM_CENTER[1])


def load_template() -> np.ndarray:
    template = cv2.imread(str(TEMPLATE_PATH), cv2.IMREAD_GRAYSCALE)
    if template is None:
        raise CalibrationRefused(f"The template {TEMPLATE_PATH} is missing.")
    return template


def find_blade(frame: np.ndarray, template: np.ndarray,
               threshold: float = MATCH_THRESHOLD) -> Match | None:
    """The best match in `frame`, or None under `threshold` or with a
    frame smaller than the template."""
    gray = frame if frame.ndim == 2 else cv2.cvtColor(frame,
                                                      cv2.COLOR_BGR2GRAY)
    height, width = template.shape[:2]
    if gray.shape[0] < height or gray.shape[1] < width:
        return None
    result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
    _low, score, _low_at, (x, y) = cv2.minMaxLoc(result)
    if not np.isfinite(score) or score < threshold:
        return None
    return Match(int(x), int(y), width, height, float(score))


def offsets(spacing_mm: float = SPACING_MM) -> list[tuple[float, float]]:
    """The four points from the start, in the notebook's order."""
    s = float(spacing_mm)
    return [(s, s), (s, -s), (-s, -s), (-s, s)]


def room_refusal(match: Match, frame_size, spacing_mm: float,
                 mm_per_px: float = NOMINAL_MM_PER_PX) -> str:
    """Why the template would leave the picture on the way round, or "".
    Looked at before the robot moves. Whole, since `matchTemplate` finds
    none of a template cut by the edge. Each axis either way: which way a
    robot move goes on the picture is what is about to be measured."""
    if not SPACING_RANGE_MM[0] <= spacing_mm <= SPACING_RANGE_MM[1]:
        return (f"The step has to be {SPACING_RANGE_MM[0]:g} to "
                f"{SPACING_RANGE_MM[1]:g} mm.")
    shift = spacing_mm / mm_per_px
    width, height = frame_size
    if (match.x - shift < 0 or match.y - shift < 0
            or match.x + match.width + shift > width
            or match.y + match.height + shift > height):
        return "Move the blade closer to the center of the picture."
    return ""


@dataclass(frozen=True)
class PixelToRobot:
    """robot (x, y) = matrix @ (u, v, 1). `matrix` is 2 x 3."""

    matrix: tuple

    @property
    def array(self) -> np.ndarray:
        return np.array(self.matrix, dtype=float).reshape(2, 3)

    def robot(self, u: float, v: float) -> np.ndarray:
        return self.array @ np.array((u, v, 1.0))

    def pixel(self, x: float, y: float) -> np.ndarray:
        """The pixel the tool's point is on with the robot at (x, y)."""
        m = self.array
        return np.linalg.solve(m[:, :2], np.array((x, y)) - m[:, 2])

    @property
    def scales(self) -> tuple[float, float]:
        """mm per pixel along the picture's two axes."""
        m = self.array
        return (float(np.linalg.norm(m[:, 0])),
                float(np.linalg.norm(m[:, 1])))

    @property
    def mm_per_px(self) -> float:
        return float(np.mean(self.scales))

    @property
    def skew_deg(self) -> float:
        """How far the picture's axes are from perpendicular on the deck."""
        m = self.array
        lengths = np.linalg.norm(m[:, 0]) * np.linalg.norm(m[:, 1])
        if lengths == 0:
            return 90.0
        cosine = float(m[:, 0] @ m[:, 1]) / lengths
        return abs(90.0 - float(np.degrees(np.arccos(np.clip(cosine, -1, 1)))))


def fit(pairs) -> tuple[PixelToRobot, float]:
    """The least squares map of `pairs` ((x, y) read, (u, v) found) and the
    largest distance of a pair from it, in mm."""
    robot = np.array([p[0] for p in pairs], dtype=float).reshape(-1, 2)
    pixels = np.array([p[1] for p in pairs], dtype=float).reshape(-1, 2)
    if len(robot) < 3:
        raise CalibrationRefused("Too few points to calibrate.")
    design = np.column_stack((pixels, np.ones(len(pixels))))
    solution, _res, rank, _sv = np.linalg.lstsq(design, robot, rcond=None)
    if rank < 3:
        raise CalibrationRefused(
            "The blade did not move in the picture: the match is on "
            "something that stands still.")
    residual = float(np.linalg.norm(design @ solution - robot, axis=1).max())
    return PixelToRobot(tuple(map(tuple, solution.T.tolist()))), residual


def check(pixel_map: PixelToRobot) -> str:
    """Why this is no map of this microscope, or "". Also for one read back
    from the file."""
    x_scale, y_scale = pixel_map.scales
    low, high = MM_PER_PX_RANGE
    if not (low <= x_scale <= high and low <= y_scale <= high):
        return (f"The scale came out as {x_scale * 1000:.1f} and "
                f"{y_scale * 1000:.1f} µm per pixel, outside "
                f"{low * 1000:g} to {high * 1000:g}: the match is not on the "
                f"blade.")
    if abs(x_scale - y_scale) / max(x_scale, y_scale) > MAX_SCALE_DIFFERENCE:
        return (f"The two axes differ in scale, {x_scale * 1000:.2f} and "
                f"{y_scale * 1000:.2f} µm per pixel: a point was matched "
                f"wrongly.")
    if pixel_map.skew_deg > MAX_SKEW_DEG:
        return (f"The axes are {pixel_map.skew_deg:.1f}° from perpendicular: "
                f"a point was matched wrongly.")
    return ""


def judge(pairs) -> tuple[PixelToRobot, float]:
    """`fit`, refused unless it can be trusted with a click."""
    pixel_map, residual = fit(pairs)
    problem = check(pixel_map)
    if problem:
        raise CalibrationRefused(problem)
    if residual > MAX_RESIDUAL_MM:
        raise CalibrationRefused(
            f"The points are {residual:.3f} mm off the fit, over "
            f"{MAX_RESIDUAL_MM:g} mm. Nothing was saved.")
    return pixel_map, residual


def verify(pixel_map: PixelToRobot, pose_xy, pixel) -> float:
    """The robot back at the start, where the map puts the blade seen at
    `pixel` against where the robot is. Returns the distance, in mm."""
    gap = float(np.linalg.norm(pixel_map.robot(*pixel)
                               - np.asarray(pose_xy, dtype=float)))
    if gap > MAX_VERIFY_MM:
        raise CalibrationRefused(
            f"Back at the start the blade is {gap:.3f} mm from where the fit "
            f"puts it, over {MAX_VERIFY_MM:g} mm. Nothing was saved.")
    return gap


def click_refusal(pixel_map: PixelToRobot, pose_xy, target_xy,
                  frame_size) -> str:
    """Why a click may not send the tool straight to `target_xy`, or "".

    A click goes in one straight line at the height the tool is at, so the
    tool has to be in the picture already: from over a plate that line
    crosses the deck. And a click in the picture cannot be further from
    the tool than the picture is across, whatever the map says."""
    width, height = frame_size
    u, v = pixel_map.pixel(*pose_xy)
    if not (0 <= u <= width and 0 <= v <= height):
        return "The tool is not in the picture. Use Go to sample first."
    across = np.hypot(width, height) * pixel_map.mm_per_px
    if np.linalg.norm(np.asarray(target_xy, dtype=float)
                      - np.asarray(pose_xy, dtype=float)) > across:
        return "That is outside the picture."
    return ""
