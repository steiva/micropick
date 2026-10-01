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
  matters. What matters is the picture the camera takes from there: its
  outline (the frame's edges through `PixelMap.to_robot` at that pose, back
  through `to_pixel` at the current one) and a cross at its centre. Standing
  at the pose, the outline is the picture's border.

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

# BGR, as every overlay colour. Cyan: no detection class uses it, and it
# reads on a dark dish and a bright one.
MARK = (255, 230, 0)
RADIUS_PX = 22
LABEL_SCALE = 1.4

# Points along each edge of a camera pose's outline: the map is not linear,
# so the edges are not quite straight.
EDGE_SAMPLES = 8


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


def _outline(pmap, pose_xy, gantry_xy, image_size):
    """The frame's border seen from `pose_xy`, in pixels of a frame taken at
    `gantry_xy`, or None where the map cannot invert it."""
    width, height = image_size
    w, h = width - 1, height - 1
    t = np.linspace(0.0, 1.0, EDGE_SAMPLES, endpoint=False)
    u = np.concatenate([t * w, np.full_like(t, w), (1 - t) * w,
                        np.zeros_like(t)])
    v = np.concatenate([np.zeros_like(t), t * h, np.full_like(t, h),
                        (1 - t) * h])
    deck = pmap.to_robot(u, v, pose_xy)
    out = []
    for x, y in deck:
        pixel = pmap.to_pixel(x, y, gantry_xy)
        if pixel is None:
            return None
        out.append(pixel)
    return np.asarray(out)


def _camera_items(name, pose, pmap, gantry_xy, image_size):
    """The outline of the picture taken from `pose`, a cross at its centre."""
    width, height = image_size
    pose_xy = (float(pose[0]), float(pose[1]))
    out = []
    outline = _outline(pmap, pose_xy, gantry_xy, image_size)
    if outline is not None:
        x0, y0 = outline.min(axis=0)
        x1, y1 = outline.max(axis=0)
        if x1 >= 0 and y1 >= 0 and x0 < width and y0 < height:
            out.append(overlays.Polyline(
                points=np.round(outline).astype(np.int32), closed=True,
                color=MARK, thickness=2))
    # The reference pixel is where the pose itself is seen (`to_robot` of it
    # is the gantry), so the centre needs no outline to be found.
    centre = pmap.to_pixel(*pose_xy, gantry_xy)
    if centre is not None and _inside(*centre, width, height):
        cx, cy = int(round(centre[0])), int(round(centre[1]))
        for a, b in (((cx - RADIUS_PX, cy), (cx + RADIUS_PX, cy)),
                     ((cx, cy - RADIUS_PX), (cx, cy + RADIUS_PX))):
            out.append(overlays.Polyline(points=np.array([a, b], np.int32),
                                         closed=False, color=MARK,
                                         thickness=2))
        out.append(_label(name, (cx, cy)))
    elif out:
        # Only the outline is in view: name it at its corner nearest the
        # picture's top-left that is in view, or at the picture's own.
        seen = [p for p in outline if _inside(*p, width, height)]
        corner = min(seen, key=lambda p: p[0] + p[1]) if seen else (0, 0)
        out.append(overlays.Text(
            text=str(name), org=(int(corner[0]) + 8, int(corner[1]) + 36),
            scale=LABEL_SCALE, color=MARK, thickness=2))
    return out


def mark_items(positions: dict, pmap: PixelMap, pipette_offset, gantry_xy,
               image_size) -> list:
    """The marks of every position with any part inside the frame: a ring
    for a pipette point, an outline for a camera pose."""
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
