"""Picking: look at the dish before committing the robot to it.

The page is numbered steps in the order they are done, each a card with a
mark (`widgets.step_card`), as on the calibration pages, ending at the run:

1. Complete the Operation checklist: what is left of it on other pages,
   each with a way there, and what this page sets up by itself while it
   is not ready yet - the detector, the upper camera, the lower camera
   for the clips.
2. Set the picking position over the dish - the camera, Go to picking
   position, Set position.
3. Set the shake position in the dish, and Shake the dish.
4. Set the dish bottom - optional, as in the checklist.
5. Analyse the dish, with the picking settings.
6. Start picking: the plate plan, the clips, Start, Pause, Stop, Resume.

The jog panel comes last. Steps 2 to 4 are marked from the Operation
checklist's own lines, so the page and the checklist cannot disagree.

The sizes are a card over the picture's bottom-left corner
(`widgets.size_panel`), shown after an analysis and by Sizes: the size
window is dragged on its histogram and saved as the picking settings'
Save does - the previous picking.json archived, old -> new in the log -
and the analysis labelled again under it without running the model. The
card's expand button opens the cuboids' figures in a window of their own
(`widgets.cuboid_stats`). A run hides it: the run judges its own frames.

The run itself comes later. What is here is everything that has to be true
before a run is worth starting, and it is the same code the run will use,
not a demonstration to be thrown away: the camera over the dish, the pose
it is looked at from, the settings the run reads, and one frame put through
the detector with the answer laid out — how many cuboids, how big they are,
and how many of them the size window would actually accept.

The analysis is drawn over the live picture, not over a held one. The
feed keeps running underneath it, so a dish that has been nudged, stirred
or refilled since shows as contours that no longer sit on their cuboids:
the cue to analyse again, visible without a second button to go back to
live.

The size window is the point of the histogram
---------------------------------------------
`cuboid_size_threshold` decides what the run will pick, and it is two
numbers in a configuration file. On a dish of real cuboids it is also the
difference between a run that fills a plate and one that finds nothing, and
nothing on screen used to say which. The histogram is every detection's
diameter with that window drawn over it, and the count inside it stated in
words. An operator who sees the population sitting to the left of the
window knows what to change and by how much, before the robot moves.

The camera and the model open themselves
---------------------------------------
Arriving at this page is the request to see the dish; see `gui.auto_camera`
for why that is not the same as reaching for hardware at startup, and what
keeps it from retrying an unplugged camera for ever. The detector is the
same: which weights this installation uses is chosen once on the Profile
page, and loading them is seconds of importing ultralytics that nobody
should have to ask for twice.

The run is the notebook's loop, with the display on this side
-------------------------------------------------------------
`PickingSession.step()` is the state machine and it is not touched here. A
worker thread calls it until the session is done, exactly as the notebook's
`worker()` does, and every event it returns carries the `PickView` that says
what to show — the live camera while the operator is being waited for, or
the frame a decision was made from with the tables measured on *that*
picture. Deciding it on this side instead is what used to draw contours
over a newer frame than the one they came from.

The status and the keys are not drawn into the frame. The notebook writes
them into the array with `overlays.annotate`; here they are the camera
view's own chrome, a box under the resolution caption, so nothing touches
the frame the session is still holding and the text is the same size
whatever the sensor's resolution.

Start is the go-ahead
---------------------
In the notebook the run is a cell, and running the cell and pressing the
key that lets the robot move are two steps because the cell is not where
the dish can be seen. Here it can, so Start asks once — dish and plate in
place, lids off, settings right — and then the session leaves idle at once.
Resume is what is left of the second step: the retry out of
needs_operator, after the operator has fixed whatever the run stopped for.

Starting needs a routine - a "plate plan" on screen - because a run with
nowhere to put a cuboid is a run that picks one up and then asks what to do
with it. The Plate plan page hands its routine to the session; this page reads it there and says above
the Start button which one it is.

The dish pose is a profile position
-----------------------------------
`observe`, beside `tip_calib`, taught the same way and driven to the same
way, because the gantry has to be somewhere particular for the dish to be
in frame and that somewhere is an installation's fact rather than a run's.
The name is the workflow's: `PickingSession` reads `profile.where("observe")`
and refuses to start without it, so teaching it here is teaching the run's
own starting pose rather than a second one that looks like it.

The dish bottom is set here too
------------------------------
`dish_bottom` is the Z every pickup height is measured up from, and this
platform has no sensor to find it: the tip is brought down onto the dish by
hand. So the page does the two ends of that. Tip over the dish centre puts
the tip over the middle of the working circle - `circle_center`, a pixel of
the picture taken at the picking position, through the pixel map and the
pipette offset - `ABOVE_BOTTOM_MM` over the dish bottom set now; the
operator jogs it down until it touches; Set dish bottom here reads Z, asks
with the old and new values side by side, saves it into the picking
settings and lifts the tip. Nothing is copied into a dialog by hand.

So is the shake pose
--------------------
`shake`, where the tip goes into the dish to stir it when too few cuboids
are isolated. It depends on how the dish sits that day, so it is taught
here, beside the dish, rather than fixed: jog the tip into the medium where
it can stir without scraping a cuboid, and Set shake position. There is one
of it - teaching again replaces it. Its buttons go through the jog
panel's queue, and Go to shake arrives as Manual control's moves do: tip
up, across at the travel height, then straight down. Shake the dish is the
run's AUTO_SHAKE on demand - the same strokes (`workflows.picking.stir`) -
and ends back at the picking position, ready to analyse again.

During a run it works while the run is paused or waits for the operator,
so a dish too crowded to pick from is stirred without a hand in the robot.
The run does it then, not the jog panel, at the checkpoint the pause holds
it at (`PickingSession.request_shake`, "A shake on request"); with cuboids
in the tip it waits until they are delivered. A paused run stays paused
after it, at the head of its cycle, and looks at the dish again.

Pickup clips
------------
Ticking Save pickup clips is the notebook's `CLIP_DIR`: the lower camera
opens, and the run records it from the approach to the end of each
aspirate, with a box round every cuboid of the batch where the homography
puts it, and writes one mp4 per pickup, named for its target well, into a
folder for the run under the Outputs folder's `clips`. Each clip is written
while it is recorded (`hardware.camera.ClipWriter`), so a pickup's frames
are never held in memory. Off by default: no writer is made, and a run does
not need the lower camera at all - unticking the box closes it, unless this
page shows it.

The clips' camera mode and crop are Settings (`clip_resolution`,
`clip_crop`; 2000x1500 and the middle half by default): fewer pixels is
more frames a second, and the pickup happens in the middle. The pipette
calibration opens the same camera in the profile's mode, the one it was
measured in, so this page opens it again in the clips' mode when it is
shown with the box ticked - and the calibration does the reverse.

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
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QMessageBox,
                               QVBoxLayout, QWidget)

from ... import paths
from ...core.calibration.pixel_map import PixelMap
from ...hardware.protocols import xyz
from ...viz import overlays
from ...workflows import manual as moves
from ...workflows.picking import PickingSession, RobotState, stir
from ..auto_camera import CameraOpener
from ..detector import wanted_model
from ..readiness import (DISH_POSITION, SHAKE_POSITION, STEPS,
                         readiness_checks, well_centre)
from ..session import Session
from ..theme import SPACING
from ..theme.factory import (card, combo_box, heading, muted_label,
                             primary_button, scroll_column, secondary_button)
from ..widgets.camera_view import CameraView
from ..widgets.card_columns import CardColumns
from ..widgets.checklist import BLOCKED, NOTE, OK, TODO, Check, Checklist
from ..widgets.done_banner import DoneBanner
from ..widgets.feed_row import FeedRow
from ..widgets.cuboid_stats import CuboidStats
from ..widgets.picking_fields import BOUNDS
from ..widgets.size_panel import SizePanel
from ..widgets.jog_panel import JogPanel
from ..widgets.step_card import StepCard, wrapped
from ..widgets.settings_form import PickingSettingsDialog
from ..workers import Worker

__all__ = ["PickingPage", "DISH_POSITION", "SHAKE_POSITION"]

TITLE = "Picking"

log = logging.getLogger(__name__)

PANEL_WIDTH = 440

# How high over the stored dish bottom Tip over the dish centre stops: room
# for a dish bottom that was set a little high, and few key presses down.
ABOVE_BOTTOM_MM = 10.0
# A new dish bottom this far from the old one is asked about twice as
# carefully: it is more often the tip resting on something else.
BIG_CHANGE_MM = 3.0

# How often the display looks at what the run is showing. The session's own
# pace is set by the robot; this is only the refresh of a picture.
VIEW_MS = 100

# What the keys do, shown on the picture and bound below. One list, so the
# overlay cannot promise a key that is not bound.
KEYS = (("Space", "resume", "resume"),
        ("P", "pause", "pause"),
        ("Esc", "stop", "stop"))

CONFIRM_START = (
    "Dish and plate in place, lids off, picking settings checked.\n\n"
    "The robot starts moving on Start.")


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
        self._last_event = ""
        # What the last run ended as, kept until another one starts. Held
        # rather than read back out of the label: a widget that decides what
        # to write by parsing what it wrote last is one refresh away from
        # forgetting it.
        self._run_message = ""
        # A shake asked of the run that it has not said it did yet.
        self._shake_asked = False
        # The last run filled the plate plan: step 6 is done until the next.
        self._filled = False
        # Where the current or last run put its clips, if it recorded any.
        self._clip_dir = None
        # Why the lower camera did not open for the clips, until retried.
        self._clips_failure = ""

        self.view = CameraView(self)

        # The steps in the order they are done, the run last: the page
        # leads to Start rather than opening on it.
        cards = [self._checklist_step(), self._dish_card(),
                 self._shake_card(), self._bottom_card(),
                 self._analysis_card(), self._run_card(),
                 self._jog_section()]
        panel = CardColumns(cards, self)

        # The sizes in a card over the picture's bottom-left corner, where
        # the size window is dragged; the cuboids in figures in a window of
        # their own, which the card's expand button opens and closes.
        self.stats = CuboidStats(self)
        self.sizes = SizePanel(self.view)
        self.sizes.follow(self.view)
        self.sizes.expand_clicked.connect(self._toggle_stats)
        self.sizes.window_moved.connect(self._size_window_moved)
        # The names the page and its tests have always used for the size
        # plot and its sentence.
        self.hist, self.window_state = (self.sizes.histogram.plot,
                                        self.sizes.histogram.state)

        body = QHBoxLayout()
        body.setSpacing(SPACING)
        body.addWidget(FeedRow(self.view, scroll_column(panel, PANEL_WIDTH)),
                       1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2, SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addLayout(body, 1)

        self._view_timer = QTimer(self)
        self._view_timer.setInterval(VIEW_MS)
        self._view_timer.timeout.connect(self._show_run_view)

        session.profile_changed.connect(self._on_profile_changed)
        session.robot_state_changed.connect(lambda _s: self._refresh())
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

    def _checklist_step(self) -> QWidget:
        self.step_checklist = StepCard(self, 1,
                                       "Complete the Operation checklist")
        self.others_state = wrapped()
        self.step_checklist.add(self.others_state)
        # What is left on the other pages, each with its button there.
        self.others = Checklist(self)
        self.others.go.connect(self.session.page_requested)
        self.step_checklist.add(self.others)
        return self.step_checklist.box

    def _dish_card(self) -> QWidget:
        self.step_dish = StepCard(self, 2, STEPS["dish_position"][0])
        box = self.step_dish.box
        box.layout().addWidget(wrapped(
            "Jog until the dish fills the picture, then Set position. Runs "
            "start and image the dish from here."))

        row = QHBoxLayout()
        row.addWidget(QLabel("Camera"))
        self.camera_choice = combo_box(self)
        self.camera_choice.currentTextChanged.connect(lambda _t: self._show_camera())
        row.addWidget(self.camera_choice, 1)
        box.layout().addLayout(row)

        buttons = QHBoxLayout()
        self.goto_button = secondary_button("Go to picking position", self)
        self.goto_button.clicked.connect(self._goto_dish)
        self.teach_button = secondary_button("Set position", self)
        self.teach_button.clicked.connect(self._teach_dish)
        buttons.addWidget(self.goto_button)
        buttons.addWidget(self.teach_button)
        box.layout().addLayout(buttons)

        self.dish_state = muted_label()
        box.layout().addWidget(self.dish_state)
        return box

    def _bottom_card(self) -> QWidget:
        self.step_bottom = StepCard(self, 4, STEPS["dish_bottom"][0])
        box = self.step_bottom.box
        box.layout().addWidget(wrapped(
            "Reference height for pickups. Move the tip over the dish "
            "center, lower it until it touches the bottom, then Set dish "
            "bottom here."))

        # One above the other: side by side in half the panel, the two
        # names did not fit their buttons.
        self.over_centre_button = secondary_button("Tip over the dish center",
                                                   self)
        self.over_centre_button.clicked.connect(self._tip_over_centre)
        self.set_bottom_button = secondary_button("Set dish bottom here", self)
        self.set_bottom_button.clicked.connect(self._set_bottom)
        box.layout().addWidget(self.over_centre_button)
        box.layout().addWidget(self.set_bottom_button)

        self.bottom_state = muted_label()
        box.layout().addWidget(self.bottom_state)
        return box

    def _shake_card(self) -> QWidget:
        self.step_shake = StepCard(self, 3, STEPS["shake_position"][0])
        box = self.step_shake.box
        box.layout().addWidget(wrapped(
            "Where a run stirs the dish when too few cuboids are isolated. "
            "Jog the tip into the medium, clear of the cuboids, then Set "
            "shake position."))

        buttons = QHBoxLayout()
        self.goto_shake_button = secondary_button("Go to shake", self)
        self.goto_shake_button.clicked.connect(self._goto_shake)
        self.teach_shake_button = secondary_button("Set shake position",
                                                   self)
        self.teach_shake_button.clicked.connect(self._teach_shake)
        buttons.addWidget(self.goto_shake_button)
        buttons.addWidget(self.teach_shake_button)
        box.layout().addLayout(buttons)

        self.shake_state = muted_label()
        box.layout().addWidget(self.shake_state)

        # Stirring by hand, here or while a run is paused.
        self.shake_button = secondary_button("Shake the dish", self)
        self.shake_button.clicked.connect(self._shake)
        box.layout().addWidget(self.shake_button)
        return box

    def _analysis_card(self) -> QWidget:
        self.step_analysis = StepCard(self, 5, "Analyze the dish")
        box = self.step_analysis.box
        box.layout().addWidget(wrapped(
            "Detect cuboids in the current picture and mark those a run "
            "would pick."))

        self.analyse_button = primary_button("Analyze the dish", self)
        self.analyse_button.clicked.connect(self._analyse)
        box.layout().addWidget(self.analyse_button)

        row = QHBoxLayout()
        self.settings_button = secondary_button("Picking settings…", self)
        self.settings_button.clicked.connect(self._settings)
        self.sizes_button = secondary_button("Sizes", self)
        self.sizes_button.setToolTip("Show the size histogram on the picture.")
        self.sizes_button.clicked.connect(self._show_sizes)
        row.addWidget(self.settings_button, 1)
        row.addWidget(self.sizes_button)
        box.layout().addLayout(row)

        self.result = QLabel("Not analyzed.")
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addWidget(self.result)
        return box

    def _run_card(self) -> QWidget:
        self.step_run = StepCard(self, 6, "Start picking")
        box = self.step_run.box

        # Which routine Start would run, from the Plate plan page, before the
        # button that commits the robot to it.
        self.routine_state = QLabel()
        self.routine_state.setWordWrap(True)
        self.routine_state.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addWidget(self.routine_state)

        # Off unless asked for: see "Pickup clips".
        self.clips_box = QCheckBox("Save pickup clips from the lower camera",
                                   self)
        self.clips_box.toggled.connect(self._clips_toggled)
        box.layout().addWidget(self.clips_box)
        self.clips_state = QLabel()
        self.clips_state.setWordWrap(True)
        self.clips_state.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addWidget(self.clips_state)

        row = QHBoxLayout()
        self.start_button = primary_button("Start picking", self)
        self.start_button.clicked.connect(self._start_run)
        self.resume_button = secondary_button("Resume", self)
        self.resume_button.clicked.connect(self._resume_run)
        row.addWidget(self.start_button)
        row.addWidget(self.resume_button)
        box.layout().addLayout(row)

        row = QHBoxLayout()
        self.pause_button = secondary_button("Pause", self)
        self.pause_button.setCheckable(True)
        self.pause_button.toggled.connect(self._pause_toggled)
        self.stop_button = secondary_button("Stop", self)
        self.stop_button.clicked.connect(self._stop_run)
        row.addWidget(self.pause_button)
        row.addWidget(self.stop_button)
        box.layout().addLayout(row)

        # A green check when a run has filled the plan.
        self.done = DoneBanner(self)
        box.layout().addWidget(self.done)
        self.run_state = QLabel()
        self.run_state.setWordWrap(True)
        self.run_state.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addWidget(self.run_state)
        return box

    def _jog_section(self) -> QWidget:
        self.jog = JogPanel(self.session, shortcut_host=self,
                            machine_controls=False,
                            collapsed=("move", "positions"), parent=self)
        self.jog.show_position_on(self.view)
        return self.jog

    # -- the dish pose --------------------------------------------------------

    def _stored_dish(self):
        profile = self.session.profile
        return None if profile is None else profile.positions.get(DISH_POSITION)

    # Both through the jog panel's queue, like the shake pose's: a worker of
    # the page's own could overlap a key press, and the panel would not read
    # the pose afterwards, so the points on the picture stayed where the
    # gantry had been.
    def _goto_dish(self) -> None:
        where = self._stored_dish()
        if (where is None or self.session.robot is None or self._busy()
                or self._running()):
            return
        motion = self.session.motion

        def job(log):
            motion.retract(log=log)
            motion.tip_to(where[:2], where[2], log=log)
            return f"at {DISH_POSITION!r}"

        if not self.jog.run_job(job):
            self.jog.tell("Robot busy.")

    def _teach_dish(self) -> None:
        if (self.session.robot is None or self.session.profile is None
                or self._busy() or self._running()):
            return
        robot, session = self.session.robot, self.session

        def job(_log):
            where = xyz(robot)
            session.remember(DISH_POSITION, where)
            return (f"{DISH_POSITION!r} taught at ({where[0]:.1f}, "
                    f"{where[1]:.1f}, {where[2]:.1f})")

        if not self.jog.run_job(job, moves=False):
            self.jog.tell("Robot busy.")

    # -- the dish bottom (Z calibration) ----------------------------------------

    def _centre_target(self):
        """(x, y) for the tip over the dish centre, or why there is none.

        The centre is `circle_center`, a pixel of the picture taken at the
        picking position, so the pixel map turns it into a deck point from
        that pose and the pipette offset puts the tip there."""
        profile = self.session.profile
        observe = self._stored_dish()
        if profile is None:
            return None, "no profile loaded"
        if observe is None:
            return None, (f"no {DISH_POSITION!r} position: the dish center is "
                          f"found from the picture taken there")
        if profile.pixel_map is None:
            return None, "no pixel map: run the camera calibration"
        offset = profile.calibration.pipette_offset
        if offset is None:
            return None, "no pipette offset: run the pipette calibration"
        pmap = PixelMap.from_config(profile.pixel_map)
        cx, cy = profile.picking.circle_center
        xy = moves.tip_target(pmap, float(cx), float(cy), observe, offset)
        return (float(xy[0]), float(xy[1])), ""

    def _tip_over_centre(self) -> None:
        if self.session.robot is None or self._running():
            return
        xy, why = self._centre_target()
        if xy is None:
            self.jog.tell(f"not possible: {why}.")
            return
        z = self.session.profile.picking.dish_bottom + ABOVE_BOTTOM_MM
        motion = self.session.motion
        refused = motion.refused((*xy, z))
        if refused:
            self.jog.tell(f"not possible: {refused}.")
            return

        def job(log):
            motion.retract(log=log)
            motion.tip_to(xy, z, log=log)
            return (f"tip {ABOVE_BOTTOM_MM:g} mm over the dish bottom, at the "
                    f"dish center: jog it down until it touches")

        if not self.jog.run_job(job, what="tip over the dish center"):
            self.jog.tell("Robot busy.")

    def _set_bottom(self) -> None:
        """Read Z where the tip is, then ask before it is saved."""
        if (self.session.robot is None or self.session.profile is None
                or self._running()):
            return
        robot = self.session.robot
        if not self.jog.run_job(lambda _log: float(xyz(robot)[2]),
                                moves=False, what="reading Z",
                                then=self._confirm_bottom):
            self.jog.tell("Robot busy.")

    def _confirm_bottom(self, z: float) -> str:
        picking = self.session.profile.picking
        old, above = picking.dish_bottom, picking.pickup_offset
        text = (f"Set the dish bottom to {z:.2f} mm?\n\n"
                f"It is {old:.2f} mm now ({z - old:+.2f} mm). Pickups will be "
                f"at {z + above:.2f} mm, {above:g} mm above it.")
        if abs(z - old) > BIG_CHANGE_MM:
            text += ("\n\nLarge change: check that the tip rests on the "
                     "dish bottom.")
        answer = QMessageBox.question(
            self, "Dish bottom", text + "\n\nThe tip is raised afterwards.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return "dish bottom not changed"
        self.session.set_dish_bottom(z)
        # After this job's own completion, which is still being delivered.
        QTimer.singleShot(0, self._lift_after_bottom)
        return f"dish bottom set to {z:.2f} mm"

    def _lift_after_bottom(self) -> None:
        if self.session.robot is None:
            return
        motion = self.session.motion

        def job(log):
            motion.retract(log=log)
            return "tip up"

        self.jog.run_job(job, what="lifting the tip")

    # -- the shake pose -------------------------------------------------------

    def _stored_shake(self):
        profile = self.session.profile
        return None if profile is None else profile.positions.get(SHAKE_POSITION)

    def _goto_shake(self) -> None:
        where = self._stored_shake()
        if where is None or self.session.robot is None or self._running():
            return
        motion = self.session.motion

        def job(log):
            motion.retract(log=log)
            motion.tip_to(where[:2], where[2], log=log)
            return f"at {SHAKE_POSITION!r}"

        if not self.jog.run_job(job):
            self.jog.tell("Robot busy.")

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
            motion.retract(log=log)
            motion.tip_to(where[:2], where[2], log=log)
            log("stirring")
            stir(robot)
            if back is None:
                motion.raise_tip(log=log)
                return "shaken"
            motion.tip_to(back[:2], back[2], log=log)
            return f"shaken; back at {DISH_POSITION!r}"

        if not self.jog.run_job(job):
            self.jog.tell("Robot busy.")

    def _can_shake_in_run(self) -> bool:
        """A run paused or waiting, with no shake of it outstanding."""
        picking = self._session
        return (self._running() and picking is not None
                and (self._pause.is_set() or self._waiting())
                and not picking.shake_pending)

    def _shake_during_run(self) -> None:
        """Ask the run to stir the dish; see "So is the shake pose"."""
        if not self._can_shake_in_run():
            return
        picking = self._session
        now = picking.can_shake_now
        picking.request_shake()
        self._shake_asked = True
        if not now:
            self._last_event = ("shake asked: the tip holds cuboids, so the "
                                "dish is shaken once they are delivered - "
                                "unpause to let the run get there")
        elif self._waiting():
            self._last_event = ("shaking the dish; the run waits for Resume "
                                "afterwards")
        else:
            self._last_event = ("shaking the dish; the run stays paused and "
                                "looks at the dish again when unpaused")
        log.info("picking run: %s", self._last_event)
        self._refresh()

    def _teach_shake(self) -> None:
        if (self.session.robot is None or self.session.profile is None
                or self._running()):
            return
        robot, session = self.session.robot, self.session

        def job(_log):
            where = xyz(robot)
            session.remember(SHAKE_POSITION, where)
            return (f"{SHAKE_POSITION!r} taught at ({where[0]:.1f}, "
                    f"{where[1]:.1f}, {where[2]:.1f})")

        if not self.jog.run_job(job, moves=False):
            self.jog.tell("Robot busy.")

    # -- looking at it --------------------------------------------------------

    def _camera(self):
        return self.session.camera(self.camera_choice.currentText())

    def _analyse(self) -> None:
        """One frame from the camera, through the detector, drawn over the
        live feed."""
        if self._busy():
            return
        profile = self.session.profile
        if profile is None:
            self.result.setText("No profile loaded.")
            return
        if self.detector.model is None:
            self.result.setText(
                "Cuboid detector not loaded."
                if self._wanted_model() else
                "No cuboid model chosen (Settings).")
            return
        camera = self._camera()
        if camera is None:
            self.result.setText("Upper camera not open.")
            return

        cfg, pixel_map = profile.picking, profile.pixel_map
        detector = self.detector

        def job(log):
            log("taking a frame")
            frame = camera.read_after(time.monotonic())
            log("detecting")
            return frame, detector.detect(frame, cfg, pixel_map)

        self.result.setText("analyzing…")
        self._run(Worker(job, what="analyzing the dish"), self._analysed)

    def _analysed(self, payload) -> None:
        frame, detection = payload
        self._frame, self._detection = frame, detection
        self.result.setText(f"analyzed at {time.strftime('%H:%M:%S')}\n"
                            f"{detection.summary}")
        log.info("analysis: %s", detection.summary.replace("\n", " | "))
        # Over the live feed, not a held frame: if the dish moves after
        # this, the contours stop sitting on the cuboids, which is the
        # sign to analyse again.
        self._draw_overlay()
        self._draw_histogram()
        self._show_sizes()
        self._refresh()

    def _draw_overlay(self) -> None:
        detection, profile = self._detection, self.session.profile
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
        histogram = self.sizes.histogram
        # Dragged only between runs: a run took its settings when it began.
        histogram.set_editable(not self._running(),
                               BOUNDS["cuboid_size_threshold"])
        bins = histogram.show_detection(
            self._detection, cfg.cuboid_size_threshold if cfg else None)
        if bins is None:
            # Nothing measured: the card and the figures go.
            self.sizes.hide()
            self.stats.hide()

    def _show_sizes(self) -> None:
        if self._detection is not None and not self._running():
            self.sizes.show()
            self.sizes.raise_()

    def _toggle_stats(self) -> None:
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
        if self._detection is None:
            self.result.setText("loading the detector…")
        worker = Worker(self.detector.load, wanted,
                        device=self.session.settings.inference_device,
                        imgsz=self.session.profile.picking.yolo_imgsz,
                        what="loading the detector")
        worker.robot = False                 # weights, not the robot
        self._run(worker, self._loaded)

    def _loaded(self, _description) -> None:
        if self._detection is None:
            self.result.setText("Not analyzed.")
        self._refresh()

    # -- the run ----------------------------------------------------------------

    def _run_problems(self) -> list[str]:
        """Everything that stops a run from starting, in sentences."""
        return [check.text for check in self._checks() if check.blocking]

    def _checks(self) -> list[Check]:
        """What has to be true before a run: the session's list
        (`gui.readiness`, shown in full on the Profile page) and what only
        this page can see. The "to do" ones of both stop Start; the notes
        are said and left to the operator."""
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

    def _well_centre(self):
        """See `readiness.well_centre`."""
        return well_centre(self.session)

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
        if self._running() or self._run_problems():
            return
        if not self._confirm_start():
            return
        # Again: the robot or the routine can have changed while the
        # question was up.
        problems = self._run_problems()
        if problems:
            self.run_state.setText("\n".join("• " + p for p in problems))
            return
        session = self.session
        profile, robot = session.profile, session.robot
        routine, camera = session.routine, self._camera()
        detector = self.detector.model
        slot = str(routine.destination.slot)
        labware_id = session.run_state.labware[slot].labware_id
        pmap = PixelMap.from_config(profile.pixel_map)
        centre = self._well_centre()
        well_centre = None if centre is None else tuple(centre.offset[:2])
        # A folder per run, so a run's clips are together and apart from the
        # last one's; the clips in it are named for their target well.
        under_cam = clip_dir = None
        if self.clips_box.isChecked():
            under_cam = self._lower_camera()
            clip_dir = paths.clips_dir() / (
                f"{time.strftime('%Y-%m-%d_%H%M%S')}_{profile.name}")
        self._clip_dir = clip_dir
        clip_crop = float(session.settings.clip_crop)
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
                                     well_centre=well_centre)
            self._session = picking
            # The confirmation was the go-ahead; see "Start is the go-ahead".
            picking.start()
            session.refresh_lights()     # start() darkened the bench
            emit(("started", picking.view, picking.state.value, "starting"))
            try:
                while not picking.done:
                    event = picking.step(pause=pause, stop=stop)
                    log(str(event))
                    emit(("event", event.view, event.state.value,
                          f"{event.kind}: {event.message}"))
            finally:
                picking.close()          # recorder detached, light restored
                session.refresh_lights()
            return picking.state.value

        self._run_message = ""
        self._filled = False
        self.done.clear()
        self.run_state.setText("starting…")
        self._run_worker = Worker(job, what="picking run")
        self._run_worker.finished.connect(self._run_finished)
        self._run_worker.failed.connect(self._run_failed)
        self._run_worker.message.connect(self._said)
        self._run_worker.start()
        self._view_timer.start()
        # The run decides from its own frames; the last analysis's sizes
        # are not what it sees, and its settings are fixed for it.
        self.sizes.hide()
        self.stats.hide()
        self._refresh()

    def _running(self) -> bool:
        return self._run_worker is not None and self._run_worker.running

    def _waiting(self) -> bool:
        return (self._running()
                and self._state_text == RobotState.NEEDS_OPERATOR.value)

    def _resume_run(self) -> None:
        """The retry out of needs_operator, once the dish has been fixed."""
        if self._session is not None and self._waiting():
            self._session.resume()
            self._refresh()

    def _pause_toggled(self, on: bool) -> None:
        self._pause.set() if on else self._pause.clear()
        if self._running():
            log.info("picking run %s", "paused" if on else "unpaused")
        self._refresh()

    def _stop_run(self) -> None:
        if self._running():
            self._stop.set()
            self.run_state.setText("Stopping after the current move…")

    def _on_event(self, payload) -> None:
        _kind, view, state, message = payload
        with self._lock:
            self._view = view
        self._state_text, self._last_event = state, message
        self._refresh()

    def _run_finished(self, state) -> None:
        self._run_worker = None
        self._session = None
        self._shake_asked = False
        self._view_timer.stop()
        self._view = None
        self._run_message = f"run {state}" + self._clips_said()
        if state == RobotState.CANCELED.value:
            # Stopped between two moves, which may be between drawing
            # cuboids in and putting them anywhere.
            self._run_message += (
                "\nThe tip may still hold liquid and cuboids: put them back "
                "into the dish (Liquid handling or Manual control) before "
                "the next run.")
        log.info("picking run %s", state)
        if state == RobotState.COMPLETED.value:
            self._filled = True
            self.done.show_done("The run filled the plate plan.")
        self._back_to_live()

    def _run_failed(self, reason: str) -> None:
        self._run_worker = None
        self._session = None
        self._shake_asked = False
        self._view_timer.stop()
        self._view = None
        self._run_message = reason + self._clips_said()
        log.error("picking run failed: %s", reason)
        self._back_to_live()

    def _clips_said(self) -> str:
        """Where the run's clips are, for the line it ends with."""
        directory, self._clip_dir = self._clip_dir, None
        return f"\nclips in {directory}" if directory is not None else ""

    def _back_to_live(self) -> None:
        """After a run: the feed again, without the run's status or its
        overlays. The last analysis went with them - the run has changed
        the dish it described."""
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
        otherwise the frame it decided on stays up unchanged. The status and
        the keys go in the view's status box rather than into the frame -
        the notebook writes them into the array with `annotate`, and the
        frame here belongs to the session.
        """
        with self._lock:
            view = self._view
        picking = self._session
        if (self._shake_asked and picking is not None
                and not picking.shake_pending):
            # Done; in a waiting state no event says so.
            self._shake_asked = False
            self._last_event = "dish shaken"
            self._refresh()
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
        # Live or held is already in the caption above this box.
        lines = [f"state: {self._state_text}",
                 f"target: {self._target()}"]
        if self._pause.is_set():
            lines.append("PAUSED")
        if self._shake_asked:
            lines.append("shake asked")
        # Drawn on the picture too, in orange; see "A cuboid that will not
        # come up" in workflows.picking.
        stuck = len(view.overlays.get("stuck", ()))
        if stuck:
            lines.append(f"stuck: {stuck} (tried again after a shake)")
        lines.append("   ".join(f"{key} {what}" for key, what, _ in KEYS))
        self.view.set_status(lines)
        # Only for a new view: the same items handed over again every tick
        # would have the view paint a full dish's contours afresh each time.
        if view is not self._overlaid:
            self._overlaid = view
            self.view.set_overlay_items(
                overlays.items(frame.shape, **view.overlays))

    def _target(self) -> str:
        routine = self.session.routine
        try:
            return str(routine.current) if routine is not None else "-"
        except Exception:                            # noqa: BLE001
            return "-"

    def _install_shortcuts(self) -> None:
        """The notebook's keys, on the window while this page is showing.

        Named in KEYS and bound from it, so the overlay cannot offer a key
        that does nothing - the mistake `jog.LAYOUT` exists to prevent.
        """
        self._shortcuts = []
        actions = {"resume": self._resume_run,
                   "pause": lambda: self.pause_button.toggle(),
                   "stop": self._stop_run}
        for key, _what, action in KEYS:
            if key == "Esc":
                # Bound by the window, to Stop for every page; listed here
                # for the picture's key box.
                continue
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(actions[action])
            self._shortcuts.append(shortcut)

    # -- settings ---------------------------------------------------------------

    def _settings(self) -> None:
        profile = self.session.profile
        if profile is None:
            self.result.setText("No profile loaded.")
            return
        dialog = PickingSettingsDialog(profile.picking,
                                       profile_name=profile.name,
                                       previous=profile.previous_picking(),
                                       parent=self)
        if dialog.exec() != PickingSettingsDialog.DialogCode.Accepted:
            return
        profile.picking = dialog.result_config
        profile.save_picking()
        log.info("picking settings saved to %s", profile.path / "picking.json")
        self.session.profile_changed.emit(profile)
        # The window may have moved, so what the last analysis means has
        # changed with it.
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
        self.result.setText(reason)
        log.error("%s", reason)
        self._refresh()

    # -- cameras -----------------------------------------------------------------

    def _wanted_camera(self) -> str | None:
        return self.session.upper_camera_label

    def _show_camera(self) -> None:
        labels = self.session.open_cameras
        current = self.camera_choice.currentText()
        self.camera_choice.blockSignals(True)
        self.camera_choice.clear()
        self.camera_choice.addItems(labels)
        wanted = current if current in labels else self._wanted_camera()
        if wanted in labels:
            self.camera_choice.setCurrentIndex(labels.index(wanted))
        self.camera_choice.blockSignals(False)
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
            self.dish_state.setText(
                f"Camera {label!r} did not open: {reason}")
        self._refresh()

    def _on_profile_changed(self, _profile) -> None:
        # A different profile is a different camera, a different dish and a
        # different window: nothing measured belongs to it.
        self.opener.forget()
        self._detection = None
        self.view.set_overlay_items([])
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

    def _refresh_steps(self, problems: list[str]) -> list[int]:
        """The steps' marks: 2 to 4 are the Operation checklist's own
        lines; 1 is what is left of it on other pages, and this page's own
        set-up while it is not ready. Returns the steps that stop Start."""
        shared = readiness_checks(self.session)
        by_text = {check.text: check for check in shared}
        for card, key in ((self.step_dish, "dish_position"),
                          (self.step_shake, "shake_position"),
                          (self.step_bottom, "dish_bottom")):
            check = by_text[STEPS[key][0]]
            card.set_state(check.state, check.detail)

        left = [check for check in shared
                if check.page != "picking" and check.blocking]
        left += [check for check in self._page_checks() if check.blocking]
        self.step_checklist.set_state(TODO if left else OK)
        self.others_state.setText(
            "All done." if not left else
            f"{len(left)} to do.")
        self.others.setVisible(bool(left))
        if left:
            self.others.show_checks(left)
        stopping = [1] if left else []
        stopping += [n for n, card in ((2, self.step_dish),
                                       (3, self.step_shake))
                     if card.state in (TODO, BLOCKED)]

        if self._detection is not None:
            self.step_analysis.set_state(OK, "Analyzed.")
        elif self.detector.model is None:
            self.step_analysis.set_state(BLOCKED, "Cuboid detector not "
                                                  "loaded.")
        elif self._camera() is None:
            self.step_analysis.set_state(BLOCKED, "Upper camera not open.")
        else:
            self.step_analysis.set_state(NOTE, "Not analyzed yet.")

        if self._filled and not self._running():
            self.step_run.set_state(OK, "The last run filled the plate plan.")
        elif problems and not self._running():
            self.step_run.set_state(BLOCKED, f"First: {problems[0]}.")
        else:
            self.step_run.set_state(TODO)
        return stopping

    def _refresh(self) -> None:
        busy = self._busy()
        connected = self.session.robot is not None
        profile = self.session.profile
        stored = self._stored_dish()

        self.goto_button.setEnabled(connected and stored is not None and not busy)
        self.teach_button.setEnabled(connected and profile is not None and not busy)
        if profile is None:
            self.dish_state.setText("No profile loaded.")
        elif stored is None:
            self.dish_state.setText("No picking position saved yet.")
        else:
            self.dish_state.setText(
                f"Saved: ({stored[0]:.1f}, {stored[1]:.1f}, "
                f"{stored[2]:.1f}).")

        running = self._running()
        if profile is None:
            self.bottom_state.setText("No profile loaded.")
        else:
            picking = profile.picking
            self.bottom_state.setText(
                f"Dish bottom {picking.dish_bottom:.2f} mm; pickups at "
                f"{picking.pickup_height:.2f} mm "
                f"(+{picking.pickup_offset:g}).")
        centre, why = self._centre_target()
        self.over_centre_button.setEnabled(
            connected and centre is not None and not running)
        self.over_centre_button.setToolTip(
            f"Move the tip over the dish center, {ABOVE_BOTTOM_MM:g} mm "
            f"above the dish bottom."
            if centre is not None else f"Unavailable: {why}.")
        self.set_bottom_button.setEnabled(
            connected and profile is not None and not running)
        self.set_bottom_button.setToolTip(
            "Save the current tip Z as the dish bottom.")
        shake = self._stored_shake()
        self.goto_shake_button.setEnabled(connected and shake is not None
                                          and not running)
        # During a run only while it is paused or waits for you.
        self.shake_button.setEnabled(
            connected and shake is not None
            and (not running or self._can_shake_in_run()))
        self.shake_button.setToolTip(
            "Stir the dish at the shake position. During a run: while "
            "paused or waiting."
            if shake is not None else
            "Unavailable: no shake position saved.")
        self.teach_shake_button.setEnabled(connected and profile is not None
                                           and not running)
        if profile is None:
            self.shake_state.setText("No profile loaded.")
        elif shake is None:
            self.shake_state.setText("No shake position saved yet.")
        else:
            self.shake_state.setText(
                f"Saved: ({shake[0]:.1f}, {shake[1]:.1f}, "
                f"{shake[2]:.1f}).")

        self.settings_button.setEnabled(profile is not None and not busy
                                        and not running)
        self.analyse_button.setEnabled(
            not busy and not running and profile is not None
            and self.detector.model is not None
            and self._camera() is not None)

        routine = self.session.routine
        self.routine_state.setText(
            "Plate plan: none."
            if routine is None else f"Plate plan:\n{routine.summary()}")

        self.clips_box.setEnabled(not running)
        if not self.clips_box.isChecked():
            self.clips_state.setText("")
        elif running and self._clip_dir is not None:
            self.clips_state.setText(f"Clips go to {self._clip_dir}")
        else:
            w, h = self._clip_mode()
            self.clips_state.setText(
                f"One clip per pickup, {w}x{h}, crop "
                f"{self.session.settings.clip_crop:g}, saved to "
                f"{paths.clips_dir(create=False)}")
        self.clips_state.setVisible(self.clips_box.isChecked())

        checks = self._checks()
        problems = [check.text for check in checks if check.blocking]
        stopping = self._refresh_steps(problems)
        self.start_button.setEnabled(not busy and not running and not problems)
        self.resume_button.setEnabled(self._waiting())
        self.pause_button.setEnabled(running)
        self.stop_button.setEnabled(running)
        if running:
            self.run_state.setText(
                f"{self._state_text} — {self._last_event}\n"
                + "  ".join(f"{key}: {what}" for key, what, _ in KEYS))
        elif problems:
            steps = ", ".join(str(n) for n in stopping) or "1"
            self.run_state.setText(
                (self._run_message + "\n\n" if self._run_message else "")
                + f"Before Start: step{'s' if len(stopping) > 1 else ''} "
                  f"{steps}.")
        else:
            self.run_state.setText(
                (self._run_message + "\n\n" if self._run_message else "")
                + "Ready to start.")
