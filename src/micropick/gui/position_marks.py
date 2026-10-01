"""The profile's saved positions, drawn on the upper camera's picture.

A position is a gantry pose, and a pose means one of two things, which the
marks draw differently:

- **A pipette point** - most positions: `shake`, the liquid-handling points,
  anything saved with the jog panel. The operator saved it with the tip on
  the spot, so what matters is the deck point the tip was over, which is the
  pose less the pipette offset - `workflows.manual.tip_target` the other way
  round - and the pixel it appears at in a frame taken from the current pose
  is the pixel map inverted (`PixelMap.to_pixel`). A ring: the tip goes down
  here, which is what an operator aiming the next one wants to see.
- **A camera pose** - the names in `CAMERA_POSITIONS`, each set on its own
  page and read by a workflow as where the upper camera looks from. A ring
  where the tip would go would sit a pipette offset away from anything that
  matters. What matters is the centre of the picture the camera takes from
  there, which is the pose itself with no offset: a square of the ring's
  size, open at the middle of each side - a viewfinder - with a dot in it.
  Standing at the pose, it is at the picture's centre.

Both are the same green; the shape says which is which.

Which is which is decided by the name, never by the operator: a camera pose
is only taught by the button of the page that uses it, and the jog panel
refuses those names, so there is no switch to set wrong.

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

import numpy as np
from PySide6.QtCore import QObject

from ..core.calibration.pixel_map import PixelMap
from ..viz import overlays

__all__ = ["MARK", "CAMERA_POSITIONS", "mark_items", "PositionMarks"]

# The camera poses, by name, with the tab that sets each. The names are the
# workflows': `PickingSession` reads `observe` (`pages.picking.DISH_POSITION`)
# and the pipette calibration `tip_calib` (`pages.calibration_pipette.
# POSITION_NAME`).
CAMERA_POSITIONS = {"observe": "Picking", "tip_calib": "Pipette"}

# BGR, as every overlay colour. A light green, for both kinds: paler than
# the pure green of the isolated cuboids, so the two are not confused.
MARK = (120, 255, 120)
RADIUS_PX = 22
# Half the gap in the middle of each side of a camera pose's square.
GAP_PX = 8
LABEL_SCALE = 1.4


def _inside(u, v, width, height) -> bool:
    return 0 <= u < width and 0 <= v < height


def _label(name, at) -> overlays.Text:
    return overlays.Text(text=str(name),
                         org=(at[0] + RADIUS_PX + 4, at[1] - RADIUS_PX // 2),
                         scale=LABEL_SCALE, color=MARK, thickness=2)


def _tip_items(name, pose, pmap, pipette_offset, gantry_xy, image_size):
    """A ring and a dot where the tip goes down."""
    width, height = image_size
    x = float(pose[0]) - float(pipette_offset.dx)
    y = float(pose[1]) - float(pipette_offset.dy)
    pixel = pmap.to_pixel(x, y, gantry_xy)
    if pixel is None or not _inside(*pixel, width, height):
        return []
    centre = (int(round(pixel[0])), int(round(pixel[1])))
    return [overlays.Circle(center=centre, radius=RADIUS_PX, color=MARK,
                            thickness=2),
            overlays.Circle(center=centre, radius=3, color=MARK,
                            thickness=-1, fill=True),
            _label(name, centre)]


def _camera_items(name, pose, pmap, gantry_xy, image_size):
    """A square open at the middle of each side, the ring's size, where the
    centre of the camera's picture would be, and a dot in it."""
    width, height = image_size
    # The reference pixel is where the pose itself is seen (`to_robot` of it
    # is the gantry), so the pose is the deck point to draw.
    pixel = pmap.to_pixel(float(pose[0]), float(pose[1]), gantry_xy)
    if pixel is None or not _inside(*pixel, width, height):
        return []
    cx, cy = int(round(pixel[0])), int(round(pixel[1]))
    r, g = RADIUS_PX, GAP_PX
    out = []
    # Each corner as an L of two arms stopping short of the side's middle.
    for sx in (-1, 1):
        for sy in (-1, 1):
            corner = (cx + sx * r, cy + sy * r)
            out.append(overlays.Polyline(
                points=np.array([(cx + sx * g, cy + sy * r), corner,
                                 (cx + sx * r, cy + sy * g)], np.int32),
                closed=False, color=MARK, thickness=2))
    out.append(overlays.Circle(center=(cx, cy), radius=3, color=MARK,
                               thickness=-1, fill=True))
    out.append(_label(name, (cx, cy)))
    return out


def mark_items(positions: dict, pmap: PixelMap, pipette_offset, gantry_xy,
               image_size) -> list:
    """The marks of every position inside the frame: a ring for a pipette
    point, a viewfinder square for a camera pose."""
    out = []
    for name, pose in sorted(positions.items()):
        if name in CAMERA_POSITIONS:
            out += _camera_items(name, pose, pmap, gantry_xy, image_size)
        else:
            out += _tip_items(name, pose, pmap, pipette_offset, gantry_xy,
                              image_size)
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
