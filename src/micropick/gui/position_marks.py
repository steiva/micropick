"""The points, drawn on the upper camera's picture.

The points are the session's (`Session.points`): the memo points, marked
on Manual control's picture and kept until the program closes, and the
saved points, the profile's positions less Robot setup's (#61): `observe`,
`shake` and `tip_calib` are each set in their own sheet and never drawn as
points. A point is where the tip goes down, in the robot's frame, so the
deck point under it is the point less the pipette offset -
`workflows.manual.tip_target` the other way round - and the pixel it
appears at in a frame taken from the current pose is the pixel map
inverted (`PixelMap.to_pixel`).

Each is a ring with a cross in it, in cyan, named with its Z when it has
one; a saved point has a filled disc in the middle too (#66). The one
chosen in Manual control's lists is drawn thicker, and one the tip cannot
go to (`Session.memo_problem`: below a deck module's top, say) in red with
a "!". A page may hide them (`PositionMarks.show_memo`): Manual Work draws
them only while the points are shown there.

And the past picks a page hands over (`PositionMarks.set_picks`): where the
tip took something, a violet ring each, the last bright and the older ones
faint (REDESIGN §6.6).

The map was fitted at one height, so a position saved far above or below it
is drawn with the parallax that height difference gives: near enough to find
it, not a measurement. Only the upper camera's picture gets marks, only with
a pixel map fitted at its resolution and a pipette offset, and only while
the pose is known: during a move the marks are taken away, since they would
be drawn for where the gantry was.

`PositionMarks` is created by `JogPanel.show_position_on`, so every page with
a jog panel and a picture has the "points" box on it. It also hands the view
the robot's axes as they lie in the picture (`axis_directions`), from the
same map: the view draws them under the position.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QObject

from ..core.calibration.pixel_map import PixelMap
from ..viz import overlays

__all__ = ["MEMO", "PICK", "CAMERA_POSITIONS", "memo_items", "pick_items", "axis_directions", "PositionMarks"]

# The camera poses, by name, with the Robot setup item that sets each: a
# camera position's slot is the camera's (`camera_moves`). The names are
# the workflows': `PickingSession` reads `observe`
# (`pages.picking.DISH_POSITION`) and the pipette calibration `tip_calib`
# (`pages.calibration_pipette.POSITION_NAME`). Not drawn: Robot setup's
# positions are not points.
CAMERA_POSITIONS = {"observe": "Position over the dish",
                    "tip_calib": "Calibration position"}

# BGR, as every overlay colour. The points': a cyan nothing else on the
# picture uses.
MEMO = (255, 220, 0)
# A point the tip cannot go to.
MEMO_BAD = (0, 0, 255)
# The last past pick, and the older ones: the same violet, dimmed.
PICK = (250, 155, 169)
PICK_OLD = (120, 75, 80)
RADIUS_PX = 22
LABEL_SCALE = 1.4


def _inside(u, v, width, height) -> bool:
    return 0 <= u < width and 0 <= v < height


def _label(name, at, color) -> overlays.Text:
    return overlays.Text(text=str(name),
                         org=(at[0] + RADIUS_PX + 4, at[1] - RADIUS_PX // 2),
                         scale=LABEL_SCALE, color=color, thickness=2)


def memo_items(points, pmap: PixelMap, pipette_offset, gantry_xy,
               image_size, chosen: str | None = None, bad=()) -> list:
    """Each point inside the frame: a ring with a cross in it where the
    tip goes down, a saved point's with a filled disc in it, and its name
    with its Z. Those named in `bad` in red, with a "!"."""
    width, height = image_size
    out = []
    for point in points:
        pixel = pmap.to_pixel(point.x - float(pipette_offset.dx),
                              point.y - float(pipette_offset.dy), gantry_xy)
        if pixel is None or not _inside(*pixel, width, height):
            continue
        cx, cy = int(round(pixel[0])), int(round(pixel[1]))
        thick = 4 if point.name == chosen else 2
        colour = MEMO_BAD if point.name in bad else MEMO
        arm = RADIUS_PX // 2
        out.append(overlays.Circle(center=(cx, cy), radius=RADIUS_PX,
                                   color=colour, thickness=thick))
        for dx, dy in ((arm, 0), (0, arm)):
            out.append(overlays.Polyline(
                points=np.array([(cx - dx, cy - dy), (cx + dx, cy + dy)],
                                np.int32),
                closed=False, color=colour, thickness=thick))
        if point.saved:
            out.append(overlays.Circle(center=(cx, cy), radius=arm // 2,
                                       color=colour, thickness=-1,
                                       fill=True))
        name = point.name if point.z is None else f"{point.name} z {point.z:g}"
        if point.name in bad:
            name += " !"
        out.append(_label(name, (cx, cy), colour))
    return out


def pick_items(picks, pmap: PixelMap, pipette_offset, gantry_xy,
               image_size) -> list:
    """A ring at each past pick inside the frame, (x, y) of the tip in the
    order taken: the last bright, the others faint."""
    width, height = image_size
    out = []
    for i, (x, y) in enumerate(picks):
        pixel = pmap.to_pixel(x - float(pipette_offset.dx),
                              y - float(pipette_offset.dy), gantry_xy)
        if pixel is None or not _inside(*pixel, width, height):
            continue
        last = i == len(picks) - 1
        out.append(overlays.Circle(
            center=(int(round(pixel[0])), int(round(pixel[1]))),
            radius=RADIUS_PX, color=PICK if last else PICK_OLD,
            thickness=3 if last else 2))
    return out


def axis_directions(pmap: PixelMap) -> dict | None:
    """Which way +X and +Y of the deck point in the picture, in pixels per
    millimetre: where a deck point 1 mm along each axis from the one at the
    reference pixel appears. Jogging +X brings what lies that way into the
    center, so the arrow is also where the view goes."""
    ref = np.asarray(pmap.config.ref, dtype=float)
    out = {}
    for name, step in (("X", (1.0, 0.0)), ("Y", (0.0, 1.0))):
        pixel = pmap.to_pixel(step[0], step[1], (0.0, 0.0))
        if pixel is None:
            return None
        out[name] = (float(pixel[0] - ref[0]), float(pixel[1] - ref[1]))
    return out


class PositionMarks(QObject):
    """Keeps one view's marks up to date with the pose, the profile and the
    camera shown.

    One per view, whatever the number of jog panels shown on it (the pipette
    page has two): each would otherwise draw from its own pose and the one
    that had not read the robot yet would wipe the other's marks. The pose is
    the last one any of them sent - it is the same gantry."""

    @classmethod
    def on(cls, session, view) -> "PositionMarks":
        """The view's marks, made the first time."""
        marks = view.findChild(cls)
        return marks if marks is not None else cls(session, view)

    def __init__(self, session, view):
        super().__init__(view)
        self.session = session
        self.view = view
        self.pose = None
        self.memo_chosen: str | None = None      # drawn thicker
        self.memo_shown = True
        self.picks: list = []                    # see set_picks
        session.profile_changed.connect(lambda _p: self.update())
        session.memo_points_changed.connect(self.update)
        view.camera_changed.connect(self.update)
        view.marks_toggled.connect(lambda _on: self.update())
        self.update()

    def follow(self, jog) -> None:
        """Take the pose from `jog` too."""
        jog.pose_changed.connect(self._set_pose)
        if jog.pose is not None:
            self._set_pose(jog.pose)

    def choose_memo(self, name: str | None) -> None:
        self.memo_chosen = name
        self.update()

    def show_memo(self, shown: bool) -> None:
        self.memo_shown = bool(shown)
        self.update()

    def set_picks(self, picks) -> None:
        """The past picks to draw, (x, y) of the tip, oldest first; empty
        draws none."""
        self.picks = list(picks or ())
        self.update()

    def _set_pose(self, pose) -> None:
        self.pose = pose
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
        self.view.set_axes(axis_directions(found[0]) if found is not None
                           else None)
        pose = self.pose
        if found is None or pose is None or not self.view.marks_shown:
            self.view.set_marks([])
            return
        pmap, offset = found
        size = pmap.config.image_size
        items = pick_items(self.picks, pmap, offset, pose[:2], size)
        if self.memo_shown:
            points = self.session.points
            items += memo_items(points, pmap, offset, pose[:2], size,
                                self.memo_chosen,
                                {p.name for p in points
                                 if self.session.memo_problem(p)})
        self.view.set_marks(items)
