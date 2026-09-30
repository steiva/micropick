"""The profile's saved positions, drawn on the upper camera's picture.

A position is a pose of the pipette: where the gantry was when the operator
saved it with the tip on the spot. What the camera sees at that spot is the
deck point the tip was over, which is the pose less the pipette offset -
`workflows.manual.tip_target` the other way round - and the pixel it appears
at in a frame taken from the current pose is the pixel map inverted
(`PixelMap.to_pixel`). So a mark sits where the tip would go down if sent to
that position, which is what an operator aiming the next one wants to see.

The map was fitted at one height, so a position saved far above or below it
is drawn with the parallax that height difference gives: near enough to find
it, not a measurement. Only the upper camera's picture gets marks, only with
a pixel map fitted at its resolution and a pipette offset, and only while
the pose is known: during a move the marks are taken away, since they would
be drawn for where the gantry was.

`PositionMarks` is created by `JogPanel.show_position_on`, so every page with
a jog panel and a picture has the "points" box on it.
"""

from __future__ import annotations

from PySide6.QtCore import QObject

from ..core.calibration.pixel_map import PixelMap
from ..viz import overlays

__all__ = ["MARK", "mark_items", "PositionMarks"]

# BGR, as every overlay colour. Cyan: no detection class uses it, and it
# reads on a dark dish and a bright one.
MARK = (255, 230, 0)
RADIUS_PX = 22
LABEL_SCALE = 1.4


def mark_items(positions: dict, pmap: PixelMap, pipette_offset, gantry_xy,
               image_size) -> list:
    """A ring, a dot and the name for every position inside the frame."""
    width, height = image_size
    out = []
    for name, pose in sorted(positions.items()):
        x = float(pose[0]) - float(pipette_offset.dx)
        y = float(pose[1]) - float(pipette_offset.dy)
        pixel = pmap.to_pixel(x, y, gantry_xy)
        if pixel is None:
            continue
        u, v = pixel
        if not (0 <= u < width and 0 <= v < height):
            continue
        centre = (int(round(u)), int(round(v)))
        out.append(overlays.Circle(center=centre, radius=RADIUS_PX, color=MARK,
                                   thickness=2))
        out.append(overlays.Circle(center=centre, radius=3, color=MARK,
                                   thickness=-1, fill=True))
        out.append(overlays.Text(text=str(name),
                                 org=(centre[0] + RADIUS_PX + 4,
                                      centre[1] - RADIUS_PX // 2),
                                 scale=LABEL_SCALE, color=MARK, thickness=2))
    return out


class PositionMarks(QObject):
    """Keeps one view's marks up to date with the pose, the profile and the
    camera shown."""

    def __init__(self, session, jog, view):
        super().__init__(view)
        self.session = session
        self.jog = jog
        self.view = view
        jog.pose_changed.connect(lambda _pose: self.update())
        session.profile_changed.connect(lambda _p: self.update())
        view.camera_changed.connect(self.update)
        view.marks_toggled.connect(lambda _on: self.update())
        self.update()

    def _map(self):
        """The pixel map and the pipette offset, or None if marks cannot be
        drawn on what the view shows."""
        session, camera = self.session, self.view.camera
        profile = session.profile
        if profile is None or camera is None or profile.pixel_map is None:
            return None
        offset = profile.calibration.pipette_offset
        if offset is None:
            return None
        if getattr(camera, "label", None) != session.upper_camera_label:
            return None
        resolution = getattr(camera, "resolution", None)
        if resolution is not None and profile.pixel_map.check_camera(resolution):
            return None
        return PixelMap.from_config(profile.pixel_map), offset

    def update(self) -> None:
        found = self._map()
        self.view.set_marks_available(found is not None)
        pose = self.jog.pose
        if found is None or pose is None or not self.view.marks_shown:
            self.view.set_marks([])
            return
        pmap, offset = found
        positions = dict(self.session.profile.positions or {})
        self.view.set_marks(mark_items(positions, pmap, offset, pose[:2],
                                       pmap.config.image_size))
