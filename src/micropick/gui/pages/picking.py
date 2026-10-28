"""Picking (REDESIGN §6.5): Auto pick's last step, the run itself.

On the left the dish picture, with "23 cuboids found" on it and, over its
top-right corner, **Camera to dish center**: the camera to the position
over the dish. During a run it works only while the run is paused or waits
for the operator - the tip has to stay where the run left it otherwise -
and reads "Pause to move the camera" until then.

On the right, top to bottom and with no scrolling:

1. **Analyze the dish** and **Shake the dish**, side by side, and under
   them "Lights go off while analyzing.": the rail lights put highlights
   on the dish, so the analysis switches them off for its frame and back
   on after if they were on, as a run does (#51), a failed one included.
2. Card **Cuboid sizes** (its summary the size window, "250-500 µm"):
   the histogram whose white lines are dragged to change what is picked,
   the pickup height and volume ("0.5 mm above the bottom · 15 µl"),
   **More settings** (the picking settings, a sheet over the page; after
   a change, "Analyze again." under Analyze while cuboids found under the
   old settings are shown) and **All measures** (the cuboids' figures in
   a window of their own, `widgets.cuboid_stats`).
3. Card **Plate · slot N** ("12 of 24 wells"): the whole plate as a mini
   map, the planned wells before a run, filled and planned in their
   colors once something is filled, the current well ringed during one.

Both cards are shown at once and neither folds (#52). The jog panel is
last (its pad floats over the picture, folded until opened).

The footer is the run's status (`run_status`, `footer_buttons`, read by
the shell): a CAPS tag - READY, PICKING, PAUSED, WAITING FOR YOU, DONE,
NOT READY - and one sentence, the run's own (`PickingSession.status`)
while it runs. Its buttons are Back and **Start picking** between runs,
**Pause** / **Resume** (one button, yellow while paused or waiting) and
**Stop run** during one. While the run waits for the operator the window
flashes in the taskbar (the shell's, for every run).

The position over the dish and the shake position are Robot setup's
(`pages.position_sheet`); the step's banner leads there while they are not
set. What only this page sets up - the detector, the upper camera, the
lower camera for the clips - opens by itself, and while it has not, the
footer says NOT READY with the first of it.

The size window is the point of the histogram
---------------------------------------------
`cuboid_size_threshold` decides what the run will pick, and it is two
numbers in a configuration file. On a dish of real cuboids it is also the
difference between a run that fills a plate and one that finds nothing, and
nothing on screen used to say which. The histogram is every detection's
diameter with that window drawn over it, and the count inside it stated in
words. Dragged, it is saved as the picking settings' Save saves - the
previous picking.json archived, old -> new in the log - and the analysis
labeled again under it without running the model. A run hides nothing but
fixes it: the run took its settings when it began.

The analysis is drawn over the live picture, not over a held one. The
feed keeps running underneath it, so a dish that has been nudged, stirred
or refilled since shows as contours that no longer sit on their cuboids:
the cue to analyze again, visible without a second button to go back to
live.

The camera and the model open themselves
---------------------------------------
Arriving at this page is the request to see the dish; see `gui.auto_camera`
for why that is not the same as reaching for hardware at startup, and what
keeps it from retrying an unplugged camera for ever. The detector is the
same: which weights this installation uses is chosen once in Settings, and
loading them is seconds of importing ultralytics that nobody should have to
ask for twice.

The run is the notebook's loop, with the display on this side
-------------------------------------------------------------
`PickingSession.step()` is the state machine and it is not touched here. A
worker thread calls it until the session is done, exactly as the notebook's
`worker()` does, and every event it returns carries the `PickView` that says
what to show - the live camera while the operator is being waited for, or
the frame a decision was made from with the tables measured on *that*
picture. Deciding it on this side instead is what used to draw contours
over a newer frame than the one they came from.

Start is the go-ahead
---------------------
In the notebook the run is a cell, and running the cell and pressing the
key that lets the robot move are two steps because the cell is not where
the dish can be seen. Here it can, so Start asks once - dish and plate in
place, lids off, settings right - and then the session leaves idle at once.
Resume out of a wait is what is left of the second step: the retry out of
needs_operator, after the operator has fixed whatever the run stopped for.

Shake the dish
--------------
The run's AUTO_SHAKE on demand - the same strokes (`workflows.picking.stir`)
at the shake position - ending back at the position over the dish, ready to
analyze again. During a run it works while the run is paused or waits for
the operator, so a dish too crowded to pick from is stirred without a hand
in the robot. The run does it then, not the jog panel, at the checkpoint
the pause holds it at (`PickingSession.request_shake`, "A shake on
request"); with cuboids in the tip it waits until they are delivered. A
paused run stays paused after it, at the head of its cycle, and looks at
the dish again.

Camera to dish center during a run
----------------------------------
Paused or waiting, the run holds the robot's lease; the move goes through
the jog panel under a loan of it (`JogPanel.lend`), as the pipette
calibration's touch-up does, and the loan ends with the move. Only where a
shake could be done now (`PickingSession.can_shake_now`): the tip holds
nothing and nothing decided so far depends on where the gantry stands - the
next picture is taken at the position over the dish again. Pause and
Resume wait for the move.

Pickup clips
------------
Not offered on screen (#52): only a developer needs them. The switch is
kept, never shown, and off, so what follows is there to be shown again.
Save pickup clips is the notebook's `CLIP_DIR`: the lower camera opens, and
the run records it from the approach to the end of each aspirate, with a
box round every cuboid of the batch where the homography puts it, and
writes one mp4 per pickup, named for its target well, into a folder for the
run under the Outputs folder's `clips`. Each clip is written while it is
recorded (`hardware.camera.ClipWriter`). Off by default: no writer is made,
and a run does not need the lower camera at all - switching it off closes
it, unless this page shows it.

The clips' camera mode and crop are Settings (`clip_resolution`,
`clip_crop`; 2000x1500 and the middle half by default). The pipette
calibration opens the same camera in the profile's mode, the one it was
measured in, so this page opens it again in the clips' mode when it is
shown with the switch on - and the calibration does the reverse.

The overlay is drawn by `widgets/overlay_painter` from `viz.overlays.items`,
which is the same list `viz.overlays.draw` renders with cv2 for the
notebook. One description of the geometry, two renderers.
"""

from __future__ import annotations

import logging
import threading
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMessageBox, QVBoxLayout,
                               QWidget)

from ... import paths
from ...core.calibration.pixel_map import PixelMap
from ...hardware.protocols import set_lights
from ...viz import overlays
from ...workflows import well_center
from ...workflows.picking import PickingSession, RobotState, stir
from ..auto_camera import CameraOpener
from ..detector import wanted_model
from ..readiness import DISH_POSITION, SHAKE_POSITION, readiness_checks
from ..session import Session
from ..theme import COLORS, SPACING
from ..theme.factory import (card, compact, danger_button, heading,
                             muted_label, primary_button, quiet_button,
                             scroll_column, secondary_button)
from ..widgets.camera_view import CameraView
from ..widgets.checklist import BLOCKED, OK, TODO, Check
from ..widgets.controls import Switch
from ..widgets.cuboid_stats import CuboidStats
from ..widgets.feed_row import FeedRow
from ..widgets.fold_card import FoldCard
from ..widgets.jog_panel import JogPanel
from ..widgets.picking_fields import BOUNDS
from ..widgets.plate_grid import PlateGrid
from ..widgets.settings_form import PickingSettingsSheet
from ..widgets.size_histogram import SizeHistogram
from ..workers import Worker

__all__ = ["PickingPage", "DISH_POSITION", "SHAKE_POSITION"]

TITLE = "Picking"

log = logging.getLogger(__name__)

PANEL_WIDTH = 440
HISTOGRAM_WIDTH = 380

# How often the display looks at what the run is showing. The session's own
# pace is set by the robot; this is only the refresh of a picture.
VIEW_MS = 100

# The page's keys, bound below and listed on the jog pad's Keys.
KEYS = (("Space", "resume", "resume"),
        ("P", "pause or resume", "pause"))

CONFIRM_START = (
    "Dish and plate in place, lids off, picking settings checked.\n\n"
    "The robot starts moving on Start.")

LIGHTS_OFF = "Lights go off while analyzing."
ANALYZE_AGAIN = "Analyze again."

CENTER = "Camera to dish center"
CENTER_WAIT = "Pause to move the camera"


def unfilled_wells(routine) -> list[str]:
    """The plan's wells still short of their cuboids, in the plate's order;
    none for a destination that is not a plate."""
    destination = routine.destination
    if not getattr(destination, "is_plate", False):
        return []
    return [w for column in destination.ordering for w in column
            if w in routine.plan and routine.remaining(w)]


def and_list(names: list[str], most: int = 8) -> str:
    """"A7", "A7 and A18", "A1, A2 and A3"; past `most`, "A1, ..., H1
    and 5 more"."""
    if len(names) > most:
        return f"{', '.join(names[:most])} and {len(names) - most} more"
    if len(names) < 2:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


class PickingPage(QWidget):
    # Emitted from the run's thread for every transition. A signal, so the
    # widgets are touched by the thread that owns them.
    event_seen = Signal(object)

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.detector = session.detector
        self.opener = CameraOpener(session, self)
        self._worker: Worker | None = None
        self._frame = None
        self._detection = None

        # The run. `pause` and `stop` are threading.Events because that is
        # what the workflow takes and it must not learn about Qt; `_view` is
        # the last PickView the session handed over, read by the timer.
        self._run_worker: Worker | None = None
        self._session: PickingSession | None = None
        self._pause = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._view = None
        self._overlaid = None                     # the view its overlays are from
        self._state_text = ""
        # The run's own sentence (`PickingSession.status`), as last said.
        self._status = ""
        self._stopping = False
        # What the last run ended as, kept until another one starts: the
        # footer's sentence between runs.
        self._run_message = ""
        # A shake asked of the run that it has not said it did yet.
        self._shake_asked = False
        # The last run filled the plate plan: DONE until the next.
        self._filled = False
        # The plan the last run ended on, for "N wells were not filled".
        self._ended_on = None
        # Where the current or last run put its clips, if it recorded any.
        self._clip_dir = None
        # Why the lower camera did not open for the clips, until retried.
        self._clips_failure = ""
        # A camera move under the run's lease, until it ends.
        self._centering = False
        # The step's first unmet need, from the shell (`set_need`).
        self._need: str | None = None

        self.view = CameraView(self)
        self.center_button = compact(secondary_button(CENTER, self))
        self.center_button.clicked.connect(self._goto_dish)
        # Opaque: an outlined button over a bright dish cannot be read.
        self.center_button.setStyleSheet(
            f"QPushButton {{ background: {COLORS['raised']}; }}")
        self.view.add_top_widget(self.center_button)
        self.stats = CuboidStats(self)

        panel = QWidget(self)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        row = QHBoxLayout()
        self.analyse_button = primary_button("Analyze the dish", panel)
        self.analyse_button.clicked.connect(self._analyse)
        self.shake_button = secondary_button("Shake the dish", panel)
        self.shake_button.clicked.connect(self._shake)
        row.addWidget(self.analyse_button, 1)
        row.addWidget(self.shake_button, 1)
        column.addLayout(row)
        self.lights_line = muted_label(LIGHTS_OFF, panel)
        column.addWidget(self.lights_line)
        # The settings changed since the cuboids shown were found.
        self.again_line = muted_label(ANALYZE_AGAIN, panel)
        self.again_line.hide()
        column.addWidget(self.again_line)
        self._stale = False
        column.addWidget(self._sizes_card(panel))
        column.addWidget(self._plate_card(panel))
        column.addWidget(self._unfilled_card(panel))

        # Never shown, and off: see "Pickup clips".
        self.clips_box = Switch("Save pickup clips", parent=panel)
        self.clips_box.toggled.connect(self._clips_toggled)
        self.clips_box.hide()
        self.clips_state = muted_label("", panel)
        self.clips_state.hide()
        self.settings_sheet: PickingSettingsSheet | None = None
        column.addWidget(self._jog_section())
        column.addStretch(1)

        # The run's buttons, shown in the window's footer (`footer_buttons`).
        self.start_button = primary_button("Start picking", self)
        self.start_button.clicked.connect(self._start_run)
        self.pause_button = secondary_button("Pause", self)
        self.pause_button.clicked.connect(self._pause_clicked)
        self.stop_button = danger_button("Stop run", self)
        self.stop_button.clicked.connect(self._stop_run)
        for button in (self.start_button, self.pause_button,
                       self.stop_button):
            button.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2,
                                  SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addWidget(FeedRow(self.view, scroll_column(panel, PANEL_WIDTH)),
                         1)

        self._view_timer = QTimer(self)
        self._view_timer.setInterval(VIEW_MS)
        self._view_timer.timeout.connect(self._show_run_view)

        session.profile_changed.connect(self._on_profile_changed)
        session.robot_state_changed.connect(lambda _s: self._refresh())
        session.robot_busy_changed.connect(self._on_robot_busy)
        session.routine_changed.connect(lambda _r: self._refresh())
        session.settings_changed.connect(self._on_settings_changed)
        # The window's Stop, and Esc: see `shell.MainWindow._stop`.
        session.stop_requested.connect(self._stop_run)
        session.camera_opened.connect(lambda _l: self._show_camera())
        session.camera_closed.connect(lambda _l: self._show_camera())
        session.camera_closed.connect(self._reopen_clip_camera)
        self.opener.failed.connect(self._open_failed)
        self.event_seen.connect(self._on_event)
        self._install_shortcuts()
        self._refresh()

    # -- construction --------------------------------------------------------

    def _sizes_card(self, parent: QWidget) -> FoldCard:
        self.sizes = FoldCard("Cuboid sizes", fold=False, parent=parent)
        body = self.sizes.body.layout()
        self.histogram = SizeHistogram(
            "Drag the white lines to change which sizes are picked.",
            self.sizes)
        # Narrower than the panel can be and a little taller than it was:
        # both cards fit with it (#52).
        self.histogram.plot.setMinimumHeight(160)
        self.histogram.plot.setMaximumHeight(190)
        self.histogram.setMaximumWidth(HISTOGRAM_WIDTH)
        self.histogram.window_moved.connect(self._size_window_moved)
        body.addWidget(self.histogram, 0, Qt.AlignmentFlag.AlignHCenter)
        # The names the page and its tests have always used for the size
        # plot and its sentence.
        self.hist, self.window_state = (self.histogram.plot,
                                        self.histogram.state)
        self.pickup_line = muted_label("", self.sizes)
        body.addWidget(self.pickup_line)
        row = QHBoxLayout()
        self.settings_button = compact(secondary_button("More settings",
                                                        self.sizes))
        self.settings_button.clicked.connect(self._settings)
        self.stats_button = compact(quiet_button("All measures", self.sizes))
        self.stats_button.clicked.connect(self._toggle_stats)
        row.addWidget(self.settings_button)
        row.addWidget(self.stats_button)
        row.addStretch(1)
        body.addLayout(row)
        return self.sizes

    def _plate_card(self, parent: QWidget) -> FoldCard:
        self.plate_card = FoldCard("Plate", fold=False, parent=parent)
        body = self.plate_card.body.layout()
        self.plate = PlateGrid(mini=True, parent=self.plate_card)
        body.addWidget(self.plate)
        legend = QHBoxLayout()
        self.legend = {}
        for name, color in (("Filled", "plate"), ("Planned", "planned")):
            label = QLabel(f'<span style="color: {COLORS[color]}">'
                           f'&#9632;</span> {name}', self.plate_card)
            legend.addWidget(label)
            self.legend[name] = label
        legend.addStretch(1)
        body.addLayout(legend)
        return self.plate_card

    def _unfilled_card(self, parent: QWidget) -> QWidget:
        """After a run that left wells of its plan empty: which, and Finish
        by hand - Manual pick's Work, the session as it is (plate, plan,
        dish bottom, tip), Dispense on those wells."""
        self.unfilled = card(parent)
        self.unfilled_title = heading("", 2, parent=self.unfilled)
        self.unfilled_wells = muted_label("", self.unfilled)
        self.unfilled_wells.setWordWrap(True)
        self.finish_button = primary_button("Finish by hand", self.unfilled)
        self.finish_button.clicked.connect(
            lambda: self.session.task_requested.emit("manual", "work"))
        layout = self.unfilled.layout()
        layout.addWidget(self.unfilled_title)
        layout.addWidget(self.unfilled_wells)
        layout.addWidget(self.finish_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.unfilled.hide()
        return self.unfilled

    def _show_unfilled(self) -> None:
        routine = self.session.routine
        wells = (unfilled_wells(routine)
                 if routine is not None and routine is self._ended_on
                 and not self._running() else [])
        self.unfilled.setVisible(bool(wells))
        if wells:
            self.unfilled_title.setText(
                "1 well was not filled" if len(wells) == 1
                else f"{len(wells)} wells were not filled")
            self.unfilled_wells.setText(f"{and_list(wells)}.")

    def _jog_section(self) -> QWidget:
        self.jog = JogPanel(self.session, shortcut_host=self,
                            machine_controls=False,
                            parent=self)
        self.jog.show_position_on(self.view)
        # Folded until asked for: the dish is what this page is looked at for.
        self.jog.pad.set_folded(True)
        self.jog.add_help([f"{key.lower()}   {what}"
                           for key, what, _ in KEYS])
        return self.jog

    # -- the moves ------------------------------------------------------------

    def _stored_dish(self):
        profile = self.session.profile
        return None if profile is None else profile.positions.get(DISH_POSITION)

    def _stored_shake(self):
        profile = self.session.profile
        return None if profile is None else profile.positions.get(SHAKE_POSITION)

    def _can_center(self) -> bool:
        """Camera to dish center now: between runs while the robot is free,
        during one while it is paused or waits, the tip empty."""
        if self._stored_dish() is None or self.session.robot is None:
            return False
        if self.jog.busy:
            return False
        if not self._running():
            return not self.session.robot_lease.busy
        picking = self._session
        return (picking is not None and self._held()
                and picking.can_shake_now and not picking.shake_pending)

    # Through the jog panel's queue: a worker of the page's own could overlap
    # a key press, and the panel would not read the pose afterwards.
    def _goto_dish(self) -> None:
        """The camera to the position over the dish. During a run under its
        lease: see "Camera to dish center during a run"."""
        if not self._can_center():
            return
        where = self._stored_dish()
        motion = self.session.motion

        def job(log):
            motion.tip_to(where[:2], where[2], log=log)
            return f"at {DISH_POSITION!r}"

        if self._running():
            self.jog.lend(self._run_worker.lease)
            self._centering = True
        if not self.jog.run_job(job):
            self._end_centering()
            self.jog.tell(self.session.robot_lease.refusal)
        self._refresh()

    def _end_centering(self) -> None:
        if self._centering:
            self._centering = False
            self.jog.end_loan()

    def _shake(self) -> None:
        """What the run does when too few cuboids are isolated, on demand:
        the tip into the dish at `shake`, the run's own strokes, and back to
        the picking position to look again - or only up, without one."""
        if self._running():
            self._shake_during_run()
            return
        where = self._stored_shake()
        if where is None or self.session.robot is None:
            return
        robot, back = self.session.robot, self._stored_dish()
        motion = self.session.motion

        def job(log):
            motion.tip_to(where[:2], where[2], log=log)
            log("stirring")
            stir(robot)
            if back is None:
                motion.raise_tip(log=log)
                return "shaken"
            motion.tip_to(back[:2], back[2], log=log)
            return f"shaken; back at {DISH_POSITION!r}"

        if not self.jog.run_job(job):
            self.jog.tell(self.session.robot_lease.refusal)

    def _can_shake_in_run(self) -> bool:
        """A run paused or waiting, with no shake of it outstanding."""
        picking = self._session
        return (self._running() and picking is not None and self._held()
                and not picking.shake_pending and not self.jog.busy)

    def _shake_during_run(self) -> None:
        """Ask the run to stir the dish; see "Shake the dish"."""
        if not self._can_shake_in_run():
            return
        picking = self._session
        now = picking.can_shake_now
        picking.request_shake()
        self._shake_asked = True
        self._status = ("Shaking the dish." if now else
                        "The dish is shaken once the cuboids are in.")
        log.info("picking run: shake asked%s",
                 "" if now else ", after the delivery")
        self._refresh()

    # -- looking at it --------------------------------------------------------

    def _camera(self):
        label = self._wanted_camera()
        return self.session.camera(label) if label else None

    def _analyse(self) -> None:
        """One frame from the camera, through the detector, drawn over the
        live feed."""
        if self._busy():
            return
        profile, camera = self.session.profile, self._camera()
        if profile is None or self.detector.model is None or camera is None:
            return

        cfg, pixel_map = profile.picking, profile.pixel_map
        detector = self.detector
        session, robot = self.session, self.session.robot

        def job(log):
            # The bench dark for the frame, lit again as it was found; no
            # lease, as the lights switch never takes one.
            before = set_lights(robot, False) if robot is not None else None
            try:
                log("taking a frame")
                frame = camera.read_after(time.monotonic())
                log("detecting")
                return frame, detector.detect(frame, cfg, pixel_map)
            finally:
                if before:
                    set_lights(robot, True)
                if robot is not None:
                    session.refresh_lights()

        self._run(Worker(job, what="analyzing the dish"), self._analysed)

    def _analysed(self, payload) -> None:
        frame, detection = payload
        self._frame, self._detection = frame, detection
        log.info("analysis: %s", detection.summary.replace("\n", " | "))
        # Over the live feed, not a held frame: if the dish moves after
        # this, the contours stop sitting on the cuboids, which is the
        # sign to analyse again.
        self._stale = False
        self._draw_overlay()
        self._draw_histogram()
        self._refresh()

    def _found(self) -> list[str]:
        """The picture's line: how many cuboids the analysis found."""
        if self._detection is None:
            return []
        return [f"{len(self._detection.df)} cuboids found"]

    def _draw_overlay(self) -> None:
        detection, profile = self._detection, self.session.profile
        if not self._running():
            self.view.set_status(self._found())
        if detection is None or profile is None or self._frame is None:
            self.view.set_overlay_items([])
            return
        cfg = profile.picking
        # Only when the dish geometry lies on this camera's frame; a circle
        # drawn somewhere arbitrary would read as the dish.
        height, width = self._frame.shape[:2]
        fits = (0 <= cfg.circle_center[0] < width
                and 0 <= cfg.circle_center[1] < height)
        self.view.set_overlay_items(overlays.items(
            self._frame.shape,
            cuboid_df=detection.df,
            pickable=detection.pickable if detection.classified else None,
            isolated=detection.isolated if detection.classified else None,
            bubbles=detection.bubbles if detection.classified else None,
            circle_center=cfg.circle_center if fits else None,
            circle_radius=cfg.circle_radius if fits else None))

    # -- the histogram ---------------------------------------------------------

    def _draw_histogram(self) -> None:
        profile = self.session.profile
        cfg = profile.picking if profile is not None else None
        self.stats.show_detection(self._detection, cfg)
        histogram = self.histogram
        # Dragged only between runs: a run took its settings when it began.
        histogram.set_editable(not self._running(),
                               BOUNDS["cuboid_size_threshold"])
        bins = histogram.show_detection(
            self._detection, cfg.cuboid_size_threshold if cfg else None)
        if bins is None:
            # Nothing measured: no figures to show.
            self.stats.hide()

    def _toggle_stats(self) -> None:
        if self._detection is None and not self.stats.isVisible():
            return
        self.stats.setVisible(not self.stats.isVisible())
        if self.stats.isVisible():
            self.stats.raise_()

    def _size_window_moved(self, low: int, high: int) -> None:
        """The size window set on the histogram: into the picking settings,
        saved as Save in the settings saves them - the previous file
        archived, the change logged - and the analysis labelled again
        under it, without running the model again."""
        profile = self.session.profile
        if profile is None or self._running():
            self._draw_histogram()
            return
        old = tuple(profile.picking.cuboid_size_threshold)
        if (low, high) == old:
            return
        profile.picking = profile.picking.model_validate(
            {**profile.picking.model_dump(),
             "cuboid_size_threshold": (low, high)})
        profile.save_picking()
        log.info("picking settings: cuboid_size_threshold %s -> %s (dragged "
                 "on the histogram)", old, (low, high))
        if self._detection is not None:
            self._detection = self.detector.relabel(self._detection,
                                                    profile.picking)
        self._draw_overlay()
        self._draw_histogram()
        self._refresh()

    # -- the detector ----------------------------------------------------------

    def _wanted_model(self) -> str:
        return wanted_model(self.session.profile, self.session.mock)

    def _ensure_detector(self) -> None:
        """Load the profile's model once, when a page that needs it opens."""
        wanted = self._wanted_model()
        if not wanted or self._busy():
            return
        if self.detector.name == wanted:
            return
        worker = Worker(self.detector.load, wanted,
                        device=self.session.settings.inference_device,
                        imgsz=self.session.profile.picking.yolo_imgsz,
                        what="loading the detector")
        self._run(worker, self._loaded)

    def _loaded(self, _description) -> None:
        self._refresh()

    # -- the run ----------------------------------------------------------------

    def _run_problems(self) -> list[str]:
        """Everything that stops a run from starting, in sentences."""
        return [check.text for check in self._checks() if check.blocking]

    def _checks(self) -> list[Check]:
        """What has to be true before a run: the session's list
        (`gui.readiness`) and what only this page can see. The "to do" ones
        of both stop Start; the notes are left to the operator."""
        return readiness_checks(self.session) + self._page_checks()

    def _page_checks(self) -> list[Check]:
        """What only this page knows: its detector, its camera, the clips'.
        Fixed lines, as the Operation checklist's (`widgets.checklist`)."""
        session = self.session
        steps = {c.text: c for c in readiness_checks(session)}
        model_done = steps["Choose the cuboid model"].state == OK
        cameras_done = steps["Choose the upper and lower cameras"].state == OK

        def line(text, done, needs_done, first, detail, optional=False):
            if done:
                return Check(text, OK, detail=detail, optional=optional)
            if not needs_done:
                return Check(text, BLOCKED,
                             detail=f'First: "{first}".', optional=optional)
            return Check(text, TODO, detail=detail, optional=optional)

        out = [line("Load the cuboid detector", self.detector.model is not None,
                    model_done, "Choose the cuboid model",
                    "Loaded automatically from the model chosen in "
                    "Settings."),
               line("Open the upper camera", self._camera() is not None,
                    cameras_done, "Choose the upper and lower cameras",
                    "Opened automatically.")]
        clips = "Open the lower camera for pickup clips"
        if not self.clips_box.isChecked():
            out.append(Check(clips, BLOCKED, optional=True,
                             detail="Save pickup clips is off."))
        else:
            w, h = self._clip_mode()
            out.append(line(
                clips, self._lower_camera() is not None, cameras_done,
                "Choose the upper and lower cameras",
                self._clips_failure if self._clips_failure else
                f"Opened automatically at {w}x{h} (Settings)."))
        return out

    def _well_center(self):
        """The plan's plate's measured well center (`well_center.for_plan`)."""
        session = self.session
        return well_center.for_plan(session.profile, session.routine,
                                    session.run_state)

    def _confirm_start(self) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Start picking")
        box.setText("Start picking?")
        box.setInformativeText(f"{self.session.routine.summary()}\n\n"
                               f"{CONFIRM_START}")
        start = box.addButton("Start", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is start

    def _start_run(self) -> None:
        if (self._running() or self._run_problems()
                or self._need is not None):
            return
        if not self._confirm_start():
            return
        # Again: the robot or the routine can have changed while the
        # question was up.
        if self._run_problems() or self._need is not None:
            self._refresh()
            return
        # The robot for the whole run, pauses and waits included: nothing
        # else moves it until the run ends (`Session.robot_lease`). Claimed
        # before anything is prepared, and given back if that fails before
        # the run's worker has it.
        lease = self.session.robot_lease.claim("picking run", run=True,
                                               plates=self._plan_plates())
        if lease is None:
            self._run_message = self.session.robot_lease.refusal
            self._refresh()
            return
        try:
            self._launch(lease)
        except BaseException:
            worker = self._run_worker
            if not (worker is not None and worker.lease is lease
                    and worker.running):
                lease.release()
            raise

    def _plan_plates(self) -> set:
        """(slot, load name) of the plate the plan delivers into, if it is
        on the deck: its measured center stays while the run uses it."""
        plate = well_center.plan_plate(self.session.routine,
                                       self.session.run_state)
        return set() if plate is None else {plate}

    def _launch(self, lease) -> None:
        """The run on its own worker, holding `lease`."""
        session = self.session
        profile, robot = session.profile, session.robot
        routine, camera = session.routine, self._camera()
        detector = self.detector.model
        slot = str(routine.destination.slot)
        labware_id = session.run_state.labware[slot].labware_id
        pmap = PixelMap.from_config(profile.pixel_map)
        center = self._well_center()
        center_xy = None if center is None else well_center.xy(center)
        # A folder per run, so a run's clips are together and apart from the
        # last one's; the clips in it are named for their target well.
        under_cam = clip_dir = None
        if self.clips_box.isChecked():
            under_cam = self._lower_camera()
            clip_dir = paths.clips_dir() / (
                f"{time.strftime('%Y-%m-%d_%H%M%S')}_{profile.name}")
        self._clip_dir = clip_dir
        clip_crop = float(session.settings.clip_crop)
        motion = session.motion
        pause, stop = self._pause, self._stop
        pause.clear()
        stop.clear()
        emit = self.event_seen.emit

        def job(log):
            """The notebook's worker loop, with the display on the other side."""
            picking = PickingSession(robot, camera, pmap, profile, routine,
                                     detector, labware_id=labware_id,
                                     under_cam=under_cam, clip_dir=clip_dir,
                                     clip_crop=clip_crop,
                                     well_center=center_xy,
                                     motion=motion)
            self._session = picking
            # The confirmation was the go-ahead; see "Start is the go-ahead".
            picking.start()
            session.refresh_lights()     # start() darkened the bench
            emit(("started", picking.view, picking.state.value,
                  picking.status))
            try:
                while not picking.done:
                    event = picking.step(pause=pause, stop=stop)
                    log(str(event))
                    emit(("event", event.view, event.state.value,
                          event.status))
            finally:
                picking.close()          # recorder detached, light restored
                session.refresh_lights()
            return picking.state.value

        self._run_message = ""
        self._status = "Starting."
        self._stopping = False
        self._filled = False
        self._ended_on = None
        self._run_worker = Worker(job, what="picking run", lease=lease)
        self._run_worker.finished.connect(self._run_finished)
        self._run_worker.failed.connect(self._run_failed)
        self._run_worker.message.connect(self._said)
        self._run_worker.start()
        self._view_timer.start()
        # The run decides from its own frames; the last analysis's sizes
        # are not what it sees, and its settings are fixed for it.
        self.stats.hide()
        self._draw_histogram()
        self._refresh()

    def _running(self) -> bool:
        return self._run_worker is not None and self._run_worker.running

    def _on_robot_busy(self, _holder) -> None:
        self._refresh()

    def _waiting(self) -> bool:
        return (self._running()
                and self._state_text == RobotState.NEEDS_OPERATOR.value)

    def _held(self) -> bool:
        """The run is paused or waits for the operator."""
        return self._running() and (self._pause.is_set() or self._waiting())

    def run_activity(self) -> str | None:
        """For the top bar's pill: "waiting", "paused", or None."""
        if self._waiting():
            return "waiting"
        return "paused" if self._running() and self._pause.is_set() else None

    def _resume_run(self) -> None:
        """The retry out of needs_operator, once the dish has been fixed."""
        if self._session is not None and self._waiting() \
                and not self._centering:
            self._session.resume()
            self._refresh()

    def _pause_clicked(self) -> None:
        """The footer's one button: Resume out of a wait, else Pause and
        Resume. Not while the camera moves under the run's lease."""
        if not self._running() or self._centering:
            return
        if self._waiting():
            self._resume_run()
            return
        on = not self._pause.is_set()
        self._pause.set() if on else self._pause.clear()
        log.info("picking run %s", "paused" if on else "unpaused")
        self._refresh()

    def _stop_run(self) -> None:
        if self._running():
            self._stop.set()
            self._stopping = True
            self._refresh()

    def _on_event(self, payload) -> None:
        _kind, view, state, status = payload
        with self._lock:
            self._view = view
        self._state_text, self._status = state, status
        self._refresh()

    def _run_finished(self, state) -> None:
        self._end_run()
        if state == RobotState.CANCELED.value:
            # Stopped between two moves, which may be between drawing
            # cuboids in and putting them anywhere.
            self._run_message = ("Stopped. The tip may still hold cuboids: "
                                 "put them back into the dish.")
        log.info("picking run %s%s", state, self._clips_said())
        if state == RobotState.COMPLETED.value:
            self._filled = True
            self._run_message = "The plan is filled."
        self._back_to_live()

    def _run_failed(self, reason: str) -> None:
        self._end_run()
        self._run_message = reason
        log.error("picking run failed: %s%s", reason, self._clips_said())
        self._back_to_live()

    def _end_run(self) -> None:
        self._ended_on = self.session.routine
        self._run_worker = None
        self._session = None
        self._shake_asked = False
        self._stopping = False
        self._pause.clear()
        self._end_centering()
        self._view_timer.stop()
        self._view = None

    def _clips_said(self) -> str:
        """Where the run's clips are, for the log line it ends with."""
        directory, self._clip_dir = self._clip_dir, None
        return f"; clips in {directory}" if directory is not None else ""

    def _back_to_live(self) -> None:
        """After a run: the feed again, without the run's overlays. The last
        analysis went with them - the run has changed the dish it
        described."""
        self._detection = None
        self._overlaid = None
        self.view.set_status([])
        self.view.set_overlay_items([])
        self._draw_histogram()
        self.view.resume()
        self._show_camera()

    def _show_run_view(self) -> None:
        """What the session says to show, once every VIEW_MS.

        The session hands over a `PickView`; live means read the camera,
        otherwise the frame it decided on stays up unchanged, with the
        overlays measured on it. The count goes in the view's status box
        rather than into the frame - the frame belongs to the session.
        """
        if self._centering and not self.jog.busy:
            self._end_centering()
            self._refresh()
        with self._lock:
            view = self._view
        picking = self._session
        if (self._shake_asked and picking is not None
                and not picking.shake_pending):
            # Done; in a waiting state no event says so.
            self._shake_asked = False
            self._status = "Dish shaken."
            self._refresh()
        self._show_plate()
        if view is None:
            return
        if view.live:
            if not self.view.live:
                self.view.resume()
                self.view.set_camera(self._camera())
            frame = self.view.held_frame
        else:
            if view.frame is not self.view.held_frame:
                self.view.hold(view.frame)
            frame = view.frame
        if frame is None:
            return
        lines = []
        found = view.overlays.get("cuboid_df")
        if found is not None and len(found):
            lines.append(f"{len(found)} cuboids found")
        # Drawn on the picture too, in orange; see "A cuboid that will not
        # come up" in workflows.picking.
        stuck = len(view.overlays.get("stuck", ()))
        if stuck:
            lines.append(f"{stuck} stuck, tried again after a shake")
        self.view.set_status(lines)
        # Only for a new view: the same items handed over again every tick
        # would have the view paint a full dish's contours afresh each time.
        if view is not self._overlaid:
            self._overlaid = view
            self.view.set_overlay_items(
                overlays.items(frame.shape, **view.overlays))

    def _install_shortcuts(self) -> None:
        """Space and P, on the window while this page is showing; Esc is
        the window's Stop. Named in KEYS and bound from it, so the pad's
        list cannot offer a key that does nothing."""
        self._shortcuts = []
        actions = {"resume": self._resume_run, "pause": self._pause_clicked}
        for key, _what, action in KEYS:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(actions[action])
            self._shortcuts.append(shortcut)

    # -- the footer -------------------------------------------------------------

    def set_need(self, need: str | None) -> None:
        """The step's first unmet need (the shell's banner), or None: while
        there is one, Start waits and the footer says NOT READY."""
        if need != self._need:
            self._need = need
            self._refresh()

    def run_status(self) -> tuple[str, str, str]:
        """The footer's tag, sentence and tone (`Footer.STATUS_TONES`)."""
        if self._running():
            if self._stopping:
                return "Picking", "Stopping after the current move.", "busy"
            if self._waiting():
                return "Waiting for you", self._status, "warn"
            if self._pause.is_set():
                return "Paused", self._status, "warn"
            return "Picking", self._status, "busy"
        if self._filled:
            return "Done", self._run_message, "ok"
        problems = ([self._need] if self._need is not None else []) + [
            p + "." for p in self._run_problems()]
        if problems:
            return "Not ready", problems[0], "off"
        if self._run_message:
            return "Ready", self._run_message, "ready"
        found = self._found()
        if found:
            return "Ready", found[0] + ".", "ready"
        routine = self.session.routine
        wells = 0 if routine is None else len(getattr(routine, "plan", ()))
        return ("Ready", f"{wells} well{'' if wells == 1 else 's'} to fill.",
                "ready")

    def footer_buttons(self, back: QWidget) -> list[QWidget]:
        """What the footer shows on the right: Back and Start picking
        between runs, Pause or Resume and Stop run during one."""
        if self._running():
            return [self.pause_button, self.stop_button]
        return [back, self.start_button]

    # -- settings ---------------------------------------------------------------

    def _settings(self) -> None:
        """More settings: the picking settings as a sheet over the page."""
        profile = self.session.profile
        if profile is None or self._running():
            return
        if self.settings_sheet is not None:
            self.settings_sheet.deleteLater()
        self.settings_sheet = sheet = PickingSettingsSheet(
            self, profile.picking, profile_name=profile.name,
            previous=profile.previous_picking())
        sheet.saved.connect(self._settings_saved)
        sheet.open()

    def _settings_saved(self, config) -> None:
        profile = self.session.profile
        if profile is None:
            return
        # Taken before profile_changed, which lets the analysis go.
        stale = config != profile.picking and self._detection is not None
        profile.picking = config
        profile.save_picking()
        log.info("picking settings saved to %s", profile.path / "picking.json")
        self.session.profile_changed.emit(profile)
        # The cuboids on the picture were found under the old settings.
        self._stale = stale
        self._draw_histogram()
        self._refresh()

    # -- the worker --------------------------------------------------------------

    def _busy(self) -> bool:
        return self._worker is not None and self._worker.running

    def _run(self, worker: Worker, done) -> None:
        self._worker = worker
        self._done = done
        # Bound methods and a slot on this object: a lambda has no receiver, so
        # Qt would connect it directly and touch these widgets from the
        # worker's thread.
        worker.finished.connect(self._job_done)
        worker.failed.connect(self._failed)
        worker.message.connect(self._said)
        worker.start()
        self._refresh()

    def _job_done(self, payload) -> None:
        if self.sender() is not self._worker:
            return
        self._worker = None
        self._done(payload)
        self._refresh()

    def _said(self, text: str) -> None:
        log.info("%s", text)

    def _failed(self, reason: str) -> None:
        if self.sender() is not self._worker:
            return
        self._worker = None
        self._run_message = reason
        log.error("%s", reason)
        self._refresh()

    # -- cameras -----------------------------------------------------------------

    def _wanted_camera(self) -> str | None:
        return self.session.upper_camera_label

    def _show_camera(self) -> None:
        # A run decides what is on screen itself; see _show_run_view.
        if not self._running():
            camera = self._camera()
            if camera is not self.view.camera:
                # The contours were measured on the other camera's picture.
                self._detection = None
                self.view.set_overlay_items([])
            self.view.set_camera(camera)
            self.view.resume()
        self._refresh()

    def _clip_mode(self) -> tuple[int, int]:
        """The lower camera's mode for the clips: a Settings value."""
        return tuple(self.session.settings.clip_resolution)

    def _lower_camera(self):
        """The lower camera, if it is open in the clips' mode."""
        label = self.session.lower_camera_label
        camera = self.session.camera(label) if label else None
        if camera is None or tuple(camera.resolution) != self._clip_mode():
            return None
        return camera

    def _ensure_clip_camera(self) -> None:
        """Open the lower camera in the clips' mode - again, if a
        calibration left it in its own. Only while clips are asked for: the
        camera is not otherwise needed here, and a run without clips does
        not wait for it."""
        if self.clips_box.isChecked() and not self._running():
            self.opener.ensure(self.session.lower_camera_label,
                               self._clip_mode())

    def _reopen_clip_camera(self, label: str) -> None:
        """The lower camera closed - by Settings, a calibration, its own
        button - while clips are asked for: opened again in their mode."""
        if label == self.session.lower_camera_label and self.isVisible():
            self.opener.forget(label)
            self._ensure_clip_camera()

    def _clips_toggled(self, on: bool) -> None:
        self._clips_failure = ""
        self.opener.forget(self.session.lower_camera_label)
        if on:
            self._ensure_clip_camera()
        else:
            self._release_clip_camera()
        self._refresh()

    def _release_clip_camera(self) -> None:
        """Clips off: the lower camera closes, unless a run holds it or this
        page shows it. Open, its grab thread decodes every frame at the
        clips' 2000x1500, a core the run's inference could use."""
        label = self.session.lower_camera_label
        camera = self.session.camera(label) if label else None
        if (camera is None or self._running()
                or label == self.session.upper_camera_label
                or camera is self.view.camera):
            return
        self.session.close_camera(label)

    def _on_settings_changed(self, _settings) -> None:
        self.opener.forget(self.session.lower_camera_label)
        if self.isVisible():
            self._ensure_clip_camera()
        self._refresh()

    def _open_failed(self, label: str, reason: str) -> None:
        if label == self.session.lower_camera_label:
            self._clips_failure = (f"the lower camera did not open in "
                                   f"{self._clip_mode()[0]}x"
                                   f"{self._clip_mode()[1]}: {reason}")
        else:
            self._run_message = f"The upper camera did not open: {reason}"
        self._refresh()

    def _on_profile_changed(self, _profile) -> None:
        # A different profile is a different camera, a different dish and a
        # different window: nothing measured belongs to it.
        self.opener.forget()
        self._detection = None
        self._stale = False
        self.view.set_overlay_items([])
        self.view.set_status([])
        self._draw_histogram()
        self._show_camera()
        if self.isVisible():
            self._ensure_detector()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.opener.ensure(self._wanted_camera())
        self._ensure_clip_camera()
        self._ensure_detector()
        self._show_camera()

    def hideEvent(self, event) -> None:
        """A run keeps running; only the picture stops being fetched. The
        session is the robot's business and a page being looked at is not.
        The statistics window goes with the page and comes back with it."""
        if not self._running():
            self._view_timer.stop()
        self.stats.hide()
        super().hideEvent(event)

    # -- display ------------------------------------------------------------------

    def _show_plate(self) -> None:
        """The Plate card: the plan's plate, filled and planned wells in
        their colors, the current well ringed while a run is on it."""
        routine = self.session.routine
        destination = getattr(routine, "destination", None)
        if destination is None or not getattr(destination, "is_plate", False):
            self.plate_card.set_title("Plate")
            self.plate_card.set_summary("")
            if self.plate.wells:
                self.plate.set_ordering([])
            return
        self.plate_card.set_title(f"Plate · slot {destination.slot}")
        if self.plate.wells != [w for c in destination.ordering for w in c]:
            self.plate.set_ordering(destination.ordering)
        table = routine.progress_table()
        plan = routine.plan
        fills, filled = {}, 0
        for c, column in enumerate(destination.ordering):
            for r, well in enumerate(column):
                if well not in plan:
                    continue
                done = int(table.iat[r, c]) >= plan[well]
                filled += done
                fills[well] = COLORS["plate" if done else "planned"]
        self.plate.set_fills(fills)
        # A number only when more than one cuboid goes in a well (§12.11).
        self.plate.set_counts(dict(plan) if max(plan.values(), default=0) > 1
                              else {})
        self.plate.set_current(routine.current if self._running() else None)
        self.plate_card.set_summary(f"{filled} of {len(plan)} wells")
        # Before a run the plan alone; Filled once there is any.
        self.legend["Filled"].setVisible(bool(filled) or self._running())

    def _refresh(self) -> None:
        busy = self._busy()
        running = self._running()
        connected = self.session.robot is not None
        # Nothing here may move the robot while anything holds it, this
        # page's own jog or run included (`Session.robot_lease`).
        movable = connected and not self.session.robot_lease.busy
        profile = self.session.profile

        # Camera to dish center: during a run only while it is held.
        self.center_button.setText(
            CENTER_WAIT if running and not self._held() else CENTER)
        self.center_button.setEnabled(self._can_center() and not busy)
        self.center_button.setToolTip(
            "" if self._stored_dish() is not None else
            "The position over the dish is not set.")

        shake = self._stored_shake()
        # During a run only while it is paused or waits for you.
        self.shake_button.setEnabled(
            connected and shake is not None
            and ((movable and not running) or self._can_shake_in_run()))
        self.shake_button.setToolTip(
            "Stir the dish at the shake position."
            if shake is not None else "The shake position is not set.")

        self.analyse_button.setEnabled(
            not busy and not running and profile is not None
            and self.detector.model is not None
            and self._camera() is not None)
        self.settings_button.setEnabled(profile is not None and not busy
                                        and not running)
        self.stats_button.setEnabled(self._detection is not None)
        self.again_line.setVisible(self._stale and not running)
        if profile is not None:
            cfg = profile.picking
            low, high = cfg.cuboid_size_threshold
            self.sizes.set_summary(f"{low:g}–{high:g} µm")
            self.pickup_line.setText(
                f"{cfg.pickup_offset:g} mm above the bottom · {cfg.vol:g} µl")

        self.clips_box.setEnabled(not running)
        if not self.clips_box.isChecked():
            self.clips_state.setText("")
        elif self._clips_failure:
            self.clips_state.setText(self._clips_failure)
        elif running and self._clip_dir is not None:
            self.clips_state.setText(f"Clips go to {self._clip_dir}")
        else:
            w, h = self._clip_mode()
            self.clips_state.setText(
                f"One clip per pickup, {w}x{h}, to "
                f"{paths.clips_dir(create=False)}")

        self._show_plate()
        self._show_unfilled()
        problems = self._run_problems()
        self.start_button.setEnabled(movable and not busy and not running
                                     and not problems and self._need is None)
        held = self._held()
        self.pause_button.setText("Resume" if held else "Pause")
        if self.pause_button.property("tone") != ("warn" if held else None):
            self.pause_button.setProperty("tone", "warn" if held else None)
            self.pause_button.style().unpolish(self.pause_button)
            self.pause_button.style().polish(self.pause_button)
        self.pause_button.setEnabled(running and not self._centering)
        self.stop_button.setEnabled(running)
        # The shell's footer and pill read the run from here.
        self.session.run_activity_changed.emit()
