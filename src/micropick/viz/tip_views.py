"""What the tip calibration saw, drawn: every detection of every frame.

The tip calibration averages several frames of each camera into one reading
(`workflows.calibrate_pipette.TipDetector.view`). When the reading is wrong,
the number alone says nothing about why: a tip that was out of focus, a
reflection the model took for a tip, a crosshair hidden by the tip. So each
reading is saved as two pictures - the last frame as it came, and the same
frame with the boxes of *all* its frames over it - for a person to look at.

Colors (BGR): green for a crosshair the model found, cyan ringed for the one
taken as the center, red for a tip box, orange and thicker for the tip a frame
kept (its most confident), a yellow cross for the median of those, which is
the tip the routine measured from. Each box is labeled with its frame and
confidence ("f3 tip 0.43"), so a tip that wanders between frames reads as
boxes with different frame numbers in different places.

Geometry as `overlays` items, drawn by `overlays.draw`, as everything else.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .overlays import Circle, Item, Polyline, Rect, Text, draw

__all__ = ["view_items", "save_view"]

POINT = (0, 200, 0)          # a crosshair
CENTER = (255, 255, 0)       # the crosshair taken as the center
TIP = (0, 0, 255)            # any tip box
KEPT = (0, 140, 255)         # the tip a frame kept
MEDIAN = (0, 255, 255)       # the tip measured from
BAND = (0, 0, 0)


def _cross(xy, size: int, color, thickness: int) -> list[Item]:
    x, y = (int(round(v)) for v in xy)
    return [Polyline(points=np.array([[x - size, y], [x + size, y]], np.int32),
                     closed=False, color=color, thickness=thickness),
            Polyline(points=np.array([[x, y - size], [x, y + size]], np.int32),
                     closed=False, color=color, thickness=thickness)]


def view_items(frame_shape, detections, *, tip_label: str = "tip",
               center=None, tip=None, lines=(), first: int = 1) -> list[Item]:
    """The boxes of every frame, then the reading, then the header.

    `detections` is one list per frame; a frame that gave no usable reading
    still has its boxes drawn. `center` and `tip` are the reading's (None
    where there is none), `lines` the header's text, `first` the number of
    the first frame given (one frame drawn as it comes is labeled its own).
    """
    h, w = frame_shape[:2]
    s = max(1, round(w / 1000))             # 4 px lines on a 4000 px frame
    font = 0.5 * s
    out: list[Item] = []
    for i, dets in enumerate(detections, start=first):
        tips = [d for d in dets if d.label == tip_label]
        kept = max(tips, key=lambda d: d.conf) if tips else None
        for d in dets:
            is_tip = d.label == tip_label
            color = (KEPT if d is kept else TIP) if is_tip else POINT
            thick = s * (2 if d is kept else 1)
            if d.box is not None:
                x0, y0, x1, y1 = (int(round(v)) for v in d.box)
            else:
                x, y = (int(round(v)) for v in d.xy)
                x0, y0, x1, y1 = x - 10 * s, y - 10 * s, x + 10 * s, y + 10 * s
            out.append(Rect(x0=x0, y0=y0, x1=x1, y1=y1, color=color,
                            thickness=thick))
            if is_tip:
                out.append(Text(text=f"f{i} {d.label} {d.conf:.2f}",
                                org=(x0, max(y0 - 4 * s, 12 * s)),
                                color=color, thickness=s, scale=font))
    if center is not None:
        c = tuple(int(round(v)) for v in center)
        out.append(Circle(center=c, radius=30 * s, color=CENTER,
                          thickness=2 * s))
    if tip is not None:
        out += _cross(tip, 40 * s, MEDIAN, 2 * s)
    if lines:
        line_h = 30 * s
        out.append(Rect(x0=0, y0=0, x1=w, y1=line_h * len(lines) + 10 * s,
                        color=BAND, fill=True))
        for k, text in enumerate(lines, start=1):
            out.append(Text(text=text, org=(10 * s, line_h * k),
                            color=(255, 255, 255), thickness=s,
                            scale=font * 1.6))
    return out


def _write(path: Path, image: np.ndarray) -> None:
    # imencode and write_bytes, not imwrite: imwrite fails silently on a
    # path cv2 cannot encode (a user folder with a non-ASCII name).
    ok, data = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise OSError(f"could not encode {path.name}")
    path.write_bytes(data.tobytes())


def save_view(folder: Path, name: str, frame: np.ndarray, detections, *,
              tip_label: str = "tip", center=None, tip=None,
              lines=()) -> list[Path]:
    """`<name>.jpg` (the frame as it came) and `<name>_detections.jpg`
    into `folder`, made if needed. Returns the two paths."""
    folder.mkdir(parents=True, exist_ok=True)
    raw = folder / f"{name}.jpg"
    marked = folder / f"{name}_detections.jpg"
    _write(raw, frame)
    image = draw(frame.copy(), view_items(
        frame.shape, detections, tip_label=tip_label, center=center, tip=tip,
        lines=lines))
    _write(marked, image)
    return [raw, marked]
