"""The Points card: the points of the session and what is done with them,
Manual pick's Run and Liquid's Plan alike (#96, #112, MANUAL.md §2.5).

The card is a `FoldCard` titled Points, Undo and Redo on its title row
(`Session.undo_points`), over a `PointList` whose chosen row unfolds
**Camera to** (the camera over the point, its Z unused), **Tip to** (the tip to
the point at its Z, no higher than the travel height; gray with no tip on) and
**Z from tip** (the point's Z set to the tip's now), each through the page's
`JogPanel.run_job` and checked against the jog envelope. Under the list **Point
at crosshair** (the spot under the picture's center through the pixel map, at
the travel height), **Tip to crosshair** (the tip to that spot at the travel
height, left there) and **New points get Z**: Travel height, Z of the tip now or
the last Z given (at the start), which a right click on the picture (`mark`)
takes. The last Z given is one typed into a point's Z or taken by Z from tip.

What the page adds: `point_target`, where Tip to takes the tip for a point and
why not (Manual refuses one under a slot's bottom), and `problems`, why a pixel
of the picture means no deck position now. With `use`, the chosen row has Use
point too (`used` with its name): Liquid's Plan takes it for the step.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QWidget

from ...core.calibration.pixel_map import PixelMap
from ...workflows.jog import Reached
from ...workflows.manual import tip_target
from ..camera_moves import NO_TIP_CALIBRATION, OUTSIDE_THE_MAP
from ..theme import COLORS
from ..theme.factory import muted_label, secondary_button, select, tool_button
from .fold_card import FoldCard
from .point_list import TRAVEL_TEXT, PointList

__all__ = ["LAST_Z", "NEW_Z", "POINTS_TIP", "TIP_Z", "TRAVEL_Z", "PointsCard"]

log = logging.getLogger(__name__)

POINTS_TIP = "Double-click X, Y, Z or a saved point's name to change it."
NO_MAP = "Calibrate the camera first."
# What Z a new point gets.
TRAVEL_Z, TIP_Z, LAST_Z = NEW_Z = ("Travel height", "Z of the tip now",
                                   "Last Z given")


def z_text(z) -> str:
    return TRAVEL_TEXT if z is None else f"{z:g}"


class PointsCard(FoldCard):
    """`used(name)`: Use point on the chosen row (with `use`)."""

    used = Signal(str)

    def __init__(self, session, jog, marks, parent: QWidget, *,
                 list_px: int, use: bool = False,
                 star_removable: bool = True,
                 point_target: Callable | None = None,
                 problems: Callable[[], list[str]] | None = None):
        super().__init__("Points", parent=parent)
        self.session = session
        self.jog = jog
        self.marks = marks
        self._point_target = point_target or self._reachable
        self._problems = problems or self._map_problems
        self.last_z: float | None = None
        self.undo_button = tool_button("Undo", "undo", session.undo_points,
                                       self)
        self.redo_button = tool_button("Redo", "redo", session.redo_points,
                                       self)
        self.header_row.addWidget(self.undo_button)
        self.header_row.addWidget(self.redo_button)
        layout = self.body.layout()

        points = PointList(session, self, actions=True, use=use,
                           star_removable=star_removable)
        points.setFixedHeight(list_px)
        points.setToolTip(POINTS_TIP)
        points.chosen.connect(self._chosen)
        points.z_typed.connect(self._z_typed)
        points.said.connect(jog.refuse)
        points.told.connect(jog.tell)
        self.points_list = points
        layout.addWidget(points)
        self.camera_to_button = points.camera_to
        self.camera_to_button.setToolTip(
            "Move the camera over the chosen point.")
        self.camera_to_button.clicked.connect(self.camera_to_point)
        self.tip_to_button = points.tip_to
        self.tip_to_button.clicked.connect(self.tip_to_point)
        self.memo_z = points.z_from_tip
        self.memo_z.setToolTip("Set the chosen point's Z to the tip's Z now.")
        self.memo_z.clicked.connect(self.z_from_tip)
        self.use_button = points.use_button
        if self.use_button is not None:
            self.use_button.clicked.connect(self._use)

        divider = QFrame(self)
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {COLORS['line']};")
        layout.addWidget(divider)
        self.place_memo_button = secondary_button("Point at crosshair", self)
        self.place_memo_button.clicked.connect(self.place_memo)
        self.tip_crosshair_button = secondary_button("Tip to crosshair", self)
        self.tip_crosshair_button.clicked.connect(self.tip_to_crosshair)
        row = QHBoxLayout()
        for button in (self.place_memo_button, self.tip_crosshair_button):
            row.addWidget(button)
        layout.addLayout(row)
        self.new_z = select(self)
        self.new_z.addItems(NEW_Z)
        self.new_z.setCurrentText(LAST_Z)
        self.new_z.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row = QHBoxLayout()
        row.addWidget(muted_label("New points get Z", self), 1)
        row.addWidget(self.new_z)
        layout.addLayout(row)

        session.memo_points_changed.connect(self.refresh_buttons)
        session.robot_busy_changed.connect(lambda *_a: self.refresh_buttons())
        jog.pose_changed.connect(lambda _p: self.refresh_buttons())
        self.refresh_buttons()

    # -- what is chosen ------------------------------------------------------

    def chosen_point(self) -> str | None:
        """The point chosen in the list."""
        return self.points_list.chosen_name

    def _chosen(self, name) -> None:
        if self.marks is not None:
            self.marks.choose_memo(name)
        self.refresh_buttons()

    def _use(self) -> None:
        name = self.chosen_point()
        if name is not None:
            self.used.emit(name)

    def refresh_buttons(self) -> None:
        session = self.session
        self.undo_button.setEnabled(session.point_history.can_undo)
        self.redo_button.setEnabled(session.point_history.can_redo)
        chosen = self.chosen_point() is not None
        free = session.robot is not None and not session.robot_lease.busy
        tip = session.tip.attached is True
        self.camera_to_button.setEnabled(chosen and free)
        self.tip_to_button.setEnabled(chosen and free and tip)
        self.tip_to_button.setToolTip(
            "Move the tip to the chosen point." if tip
            else "Put a tip on first.")
        self.memo_z.setEnabled(chosen and self.jog.pose is not None)
        pixel_map = session.has_pixel_map()
        self.place_memo_button.setEnabled(pixel_map)
        self.place_memo_button.setToolTip(
            "Mark the spot under the crosshair." if pixel_map
            else NO_MAP)
        lease = session.robot_lease
        self.tip_crosshair_button.setEnabled(pixel_map and free and tip)
        self.tip_crosshair_button.setToolTip(
            NO_MAP if not pixel_map
            else "Put a tip on first." if not tip
            else f"Robot busy: {lease.holder}." if lease.busy
            else "Move the tip to the spot under the crosshair.")

    # -- new points ----------------------------------------------------------

    def new_point_z(self) -> float | None:
        """A new point's Z, as New points get Z says."""
        chosen = self.new_z.currentText()
        if chosen == LAST_Z:
            return self.last_z
        pose = self.jog.pose
        if chosen == TIP_Z and pose is not None:
            return round(float(pose[2]), 2)
        return None

    def set_last_z(self, z) -> None:
        self.last_z = None if z is None else float(z)

    def _z_typed(self, z) -> None:
        self.set_last_z(z)
        self.say_problem(self.chosen_point())

    def say_problem(self, name) -> None:
        point = self.session.point(name or "")
        problem = self.session.memo_problem(point) if point else ""
        if problem:
            self.jog.refuse(f"{point.name} is not reachable: {problem}.")

    def mark(self, u: float, v: float) -> None:
        """A right click on the picture: a new point where the tip would go
        down at this pixel. Moves nothing."""
        problems = self._problems()
        if problems:
            self.jog.refuse(problems[0])
            return
        profile = self.session.profile
        offset = profile.calibration.pipette_offset
        if offset is None:
            self.jog.refuse(NO_TIP_CALIBRATION)
            return
        pose = self.jog.pose
        if pose is None:
            self.jog.refuse("Robot busy.")
            return
        pmap = PixelMap.from_config(profile.pixel_map)
        if not pmap.covers(u, v):
            self.jog.refuse(OUTSIDE_THE_MAP)
            return
        xy = tip_target(pmap, u, v, np.asarray(pose[:2]), offset)
        self.add_memo(xy, self.new_point_z())

    def place_memo(self) -> None:
        """Point at crosshair: a point under the picture's center, at the
        travel height. Moves nothing."""
        problem = self.crosshair_problem()
        if problem:
            self.jog.refuse(problem)
            return
        self.add_memo(self.crosshair_spot(self.jog.pose[:2]), None)

    def add_memo(self, xy, z) -> None:
        """A new memo point, chosen. It opens nothing: the operator's
        folding stays."""
        point = self.session.add_memo_point(xy[0], xy[1], z)
        self.points_list.choose(point.name)
        self.jog.tell(f"{point.name} added.")
        self.say_problem(point.name)

    # -- the crosshair -------------------------------------------------------

    def _map_problems(self) -> list[str]:
        session = self.session
        if session.robot is None:
            return ["Robot not connected."]
        if session.profile is None:
            return ["No profile loaded."]
        if session.profile.pixel_map is None:
            return [NO_MAP]
        return []

    def crosshair_problem(self) -> str:
        """Why the spot under the crosshair cannot be known now, or ""."""
        problems = self._problems()
        if problems:
            return problems[0]
        if self.session.profile.calibration.pipette_offset is None:
            return NO_TIP_CALIBRATION
        if self.jog.pose is None:
            return "Robot busy."
        return ""

    def crosshair_spot(self, gantry) -> np.ndarray:
        """Where the tip goes down under the crosshair with the gantry at
        `gantry`: the picture's center through the pixel map, plus the
        measured tip offset."""
        profile = self.session.profile
        config = profile.calibration.pixel_map
        return tip_target(PixelMap.from_config(config), config.cu, config.cv,
                          np.asarray(gantry, dtype=float),
                          profile.calibration.pipette_offset)

    def tip_to_crosshair(self) -> None:
        """Tip to crosshair: the tip at the spot under the picture's center,
        at the travel height. It stays there."""
        session = self.session
        problem = ("Robot not connected." if session.robot is None
                   else "Put a tip on first."
                   if session.tip.attached is not True
                   else self.crosshair_problem())
        if problem:
            self.jog.refuse(problem)
            return
        motion = session.motion
        spot = self.crosshair_spot(self.jog.pose[:2])
        travel = motion.travel_z
        why = motion.refused((spot[0], spot[1], motion.envelope.z[1]
                              if travel is None else travel))
        if why:
            self.jog.refuse(f"Not reachable: {why}.")
            return

        def job(log_):
            # No Z: as high as a move may go, which is the travel height.
            return Reached(motion.to_spot(spot, float("inf"), log=log_),
                           f"Tip at ({spot[0]:.2f}, {spot[1]:.2f}).")

        if not self.jog.run_job(job):
            self.jog.refuse(session.robot_lease.refusal)

    # -- the chosen point's moves ---------------------------------------------

    def z_from_tip(self) -> None:
        name, pose = self.chosen_point(), self.jog.pose
        if name is None or pose is None:
            return
        z = round(float(pose[2]), 2)
        self.session.change_point(name, z=z)
        self.set_last_z(z)
        self.jog.tell(f"{name}: Z {z:g}.")
        self.say_problem(name)

    def camera_to_point(self) -> None:
        """Camera to: the camera over the chosen point at the travel
        height; its Z is not used."""
        point = self.session.point(self.chosen_point() or "")
        pose = self.jog.pose            # not a read: the window would wait
        if point is None or pose is None or self.jog.busy:
            return
        offset = self.session.profile.calibration.pipette_offset
        if offset is None:
            self.jog.refuse(NO_TIP_CALIBRATION)
            return
        xy = np.array([point.x - offset.dx, point.y - offset.dy])
        why = self.session.motion.refused((xy[0], xy[1], pose[2]))
        if why:
            self.jog.refuse(f"Not reachable: {why}.")
            return
        log.info("camera over %s (%.2f, %.2f)", point.name, xy[0], xy[1])

        def job(log_):
            return Reached(self.session.motion.camera_to(xy, log=log_),
                           f"Camera over {point.name}.")

        if not self.jog.run_job(job):
            self.jog.refuse(self.session.robot_lease.refusal)

    def tip_to_point(self) -> None:
        """Tip to: the tip to the chosen point, at its Z."""
        point = self.session.point(self.chosen_point() or "")
        if point is None:
            return
        session = self.session
        if session.tip.attached is not True:
            self.jog.refuse("Put a tip on first.")
            return
        if session.robot is None or self.jog.busy:
            return
        try:
            xy, z = self._point_target(point)
        except ValueError as exc:
            self.jog.refuse(str(exc))
            return
        log.info("tip to %s (%.2f, %.2f) at z %s", point.name, point.x,
                 point.y, z_text(point.z))

        def job(log_):
            return Reached(self.session.motion.to_spot(xy, z, log=log_),
                           f"Tip at {point.name}.")

        if not self.jog.run_job(job):
            self.jog.refuse(self.session.robot_lease.refusal)

    def _reachable(self, point) -> tuple[np.ndarray, float]:
        """Where the tip goes for `point`, (xy, z), checked against the jog
        envelope. Raises ValueError with the reason."""
        pose = self.jog.pose
        check_z = point.z if point.z is not None else (
            pose[2] if pose is not None else 0.0)
        why = self.session.motion.refused((point.x, point.y, check_z))
        if why:
            raise ValueError(f"Not reachable: {why}.")
        return (np.array([point.x, point.y]),
                float("inf") if point.z is None else point.z)
