"""`--mock`: the blade as a microscope would see it, on the mock robot.

The robot's pose puts the template's apex on a pixel through the notebook's
last map, so what a calibration measures can be compared with what was put
in. A frame is drawn unmirrored: `rig.Mirrored` turns it, as the real one.
"""

from __future__ import annotations

import cv2
import numpy as np

from ..hardware.camera import BackgroundCamera
from ..hardware.mock import FakeVideoCapture, MockRobot
from .calibration import APEX_FROM_CENTER, PixelToRobot, load_template
from .rig import RESOLUTION, Mirrored

__all__ = ["DEVICE", "TRUE_MAP", "mock_robot", "open_mock_microscope"]

DEVICE = "Mock microscope"
# Of mirrored frames, as every map here.
TRUE_MAP = PixelToRobot(((0.0038953175, -0.0000685829, 291.0435928),
                         (-0.0001803626, -0.0038690407, 135.2691247)))
START = (294.0, 133.2, 99.5)
TOP_MM = 160.0


def mock_robot() -> MockRobot:
    return MockRobot(position=START, noise_mm=0.001, top_mm=TOP_MM)


def _render(robot: MockRobot):
    template = cv2.cvtColor(load_template(), cv2.COLOR_GRAY2BGR)
    height, width = template.shape[:2]
    frame_w, frame_h = RESOLUTION

    def render(_index: int) -> np.ndarray:
        frame = np.full((frame_h, frame_w, 3), 12, np.uint8)
        pose = robot.get_position(verbose=False)[0]
        u, v = TRUE_MAP.pixel(pose["x"], pose["y"])
        x = int(round(u - APEX_FROM_CENTER[0] - width / 2))
        y = int(round(v - APEX_FROM_CENTER[1] - height / 2))
        x0, y0 = max(x, 0), max(y, 0)
        x1, y1 = min(x + width, frame_w), min(y + height, frame_h)
        if x0 < x1 and y0 < y1:
            frame[y0:y1, x0:x1] = template[y0 - y:y1 - y, x0 - x:x1 - x]
        return np.ascontiguousarray(frame[:, ::-1])

    return render


def open_mock_microscope(robot: MockRobot) -> Mirrored:
    capture = FakeVideoCapture(*RESOLUTION, fps=20.0, render=_render(robot))
    return Mirrored(BackgroundCamera(capture, label="microscope",
                                     resolution=RESOLUTION, warmup=1))
