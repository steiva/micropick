"""Picking: look at the dish before committing the robot to it.

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
folder for the run under the Outputs folder's `clips`. Off by default: no
recorder is made, and a run does not need the lower camera at all.

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
from ..session import Session
from ..theme import SPACING
from ..theme.factory import (card, combo_box, heading, primary_button,
                             scroll_column, secondary_button)
from ..widgets.camera_view import CameraView
from ..widgets.card_columns import CardColumns
from ..widgets.checklist import NOTE, OK, TODO, Check, Checklist
from ..widgets.done_banner import DoneBanner
from ..widgets.feed_row import FeedRow
from ..widgets.size_histogram import SizeHistogram
from ..widgets.jog_panel import JogPanel
from ..widgets.settings_form import PickingSettingsDialog
from ..workers import Worker

__all__ = ["PickingPage", "DISH_POSITION", "SHAKE_POSITION"]

TITLE = "Picking"

log = logging.getLogger(__name__)

PANEL_WIDTH = 440

# The profile position the dish is looked at from, beside tip_calib. The
# name is the workflow's: PickingSession reads profile.where("observe").
DISH_POSITION = "observe"

# The other pose the workflow drives to by name, when the dish has to be
# stirred to separate crowded cuboids. Checked before a run starts rather
# than met ten minutes into one.
SHAKE_POSITION = "shake"

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

# What the sentence under the histogram adds on this page: the window is
# not the last word on what a run picks, but nothing after it adds any.
HIST_AFTER = ("Only the shape windows and the spacing rule can reject one "
              "after that, so this is the most a run could pick from this "
              "frame.")

# A calibration older than this is said, not refused: an old one may be
# perfectly good, and only the operator knows whether anything was moved.
STALE_DAYS = 30


def _dated(text: str, when, page: str | None = None, place: str = ""):
    """A done check with its date, or a note when it is old."""
    if when is None:
        return Check(text)
    from datetime import datetime, timezone
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - when).days
    local = when.astimezone().strftime("%Y-%m-%d")
    if age > STALE_DAYS:
        return Check(f"{text} on {local}, {age} days ago: redo it if anything "
                     f"was moved or replaced since.", NOTE, page, place)
    return Check(f"{text} on {local}")


CONFIRM_START = (
    "Make sure the dish and the well plate are in place and their lids are "
    "off, and that the picking settings are right.\n\n"
    "The robot starts moving as soon as you press Start.")


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
        self._state_text = ""
        self._last_event = ""
        # What the last run ended as, kept until another one starts. Held
        # rather than read back out of the label: a widget that decides what
        # to write by parsing what it wrote last is one refresh away from
        # forgetting it.
        self._run_message = ""
        # A shake asked of the run that it has not said it did yet.
        self._shake_asked = False
        # Where the current or last run put its clips, if it recorded any.
        self._clip_dir = None
        # Why the lower camera did not open for the clips, until retried.
        self._clips_failure = ""

        self.view = CameraView(self)

        cards = [self._dish_card(), self._bottom_card(), self._shake_card(),
                 self._analysis_card(), self._histogram_card(),
                 self._run_card(), self._jog_section()]
        # The run first: its checklist is what an operator works down, and
        # Start is what they came for. The cards it points to follow.
        cards.insert(0, cards.pop(5))
        panel = CardColumns(cards, self)

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
        self.opener.failed.connect(self._open_failed)
        self.event_seen.connect(self._on_event)
        self._install_shortcuts()
        self._refresh()

    # -- construction --------------------------------------------------------

    def _dish_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("The dish", 2))

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

        self.dish_state = QLabel()
        self.dish_state.setWordWrap(True)
        self.dish_state.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addWidget(self.dish_state)
        return box

    def _bottom_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Dish bottom (Z)", 2))
        how = QLabel(
            "The height every pickup is measured from. With the dish in "
            "place: Tip over the dish centre, jog the tip down (PgDn; small "
            "steps at the end) until it just touches the bottom, then Set "
            "dish bottom here.")
        how.setWordWrap(True)
        box.layout().addWidget(how)

        # One above the other: side by side in half the panel, the two
        # names did not fit their buttons.
        self.over_centre_button = secondary_button("Tip over the dish centre",
                                                   self)
        self.over_centre_button.clicked.connect(self._tip_over_centre)
        self.set_bottom_button = secondary_button("Set dish bottom here", self)
        self.set_bottom_button.clicked.connect(self._set_bottom)
        box.layout().addWidget(self.over_centre_button)
        box.layout().addWidget(self.set_bottom_button)

        self.bottom_state = QLabel()
        self.bottom_state.setWordWrap(True)
        self.bottom_state.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addWidget(self.bottom_state)
        return box

    def _shake_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Shaking the dish", 2))

        self.shake_button = primary_button("Shake the dish", self)
        self.shake_button.clicked.connect(self._shake)
        box.layout().addWidget(self.shake_button)

        buttons = QHBoxLayout()
        self.goto_shake_button = secondary_button("Go to shake", self)
        self.goto_shake_button.clicked.connect(self._goto_shake)
        self.teach_shake_button = secondary_button("Set shake position",
                                                   self)
        self.teach_shake_button.clicked.connect(self._teach_shake)
        buttons.addWidget(self.goto_shake_button)
        buttons.addWidget(self.teach_shake_button)
        box.layout().addLayout(buttons)

        self.shake_state = QLabel()
        self.shake_state.setWordWrap(True)
        self.shake_state.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addWidget(self.shake_state)
        return box

    def _analysis_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Analysis", 2))

        self.analyse_button = primary_button("Analyse the dish", self)
        self.analyse_button.clicked.connect(self._analyse)
        box.layout().addWidget(self.analyse_button)

        self.settings_button = secondary_button("Picking settings…", self)
        self.settings_button.clicked.connect(self._settings)
        box.layout().addWidget(self.settings_button)

        self.result = QLabel("nothing analysed yet")
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addWidget(self.result)
        return box

    def _histogram_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Sizes", 2))
        self.histogram = SizeHistogram(
            "The shaded band is cuboid_size_threshold: what a run would "
            "accept. Analyse the dish to see what is in it.", self)
        # The names the page and its tests have always used for the two.
        self.hist, self.window_state = self.histogram.plot, self.histogram.state
        box.layout().addWidget(self.histogram)
        return box

    def _run_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("The run", 2))

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
        # Before a run: each condition with its mark and a way to it.
        self.checklist = Checklist(self)
        self.checklist.go.connect(self.session.page_requested)
        box.layout().addWidget(self.checklist)
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
        robot = self.session.robot

        def job(log):
            moves.drive_tip(robot, where[:2], where[2], None, log=log)
            return f"at {DISH_POSITION!r}"

        if not self.jog.run_job(job):
            self.jog.tell("not now: the robot is busy; Go to picking position "
                          "again when it is done.")

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
            self.jog.tell("not now: the robot is busy; Set position again "
                          "when it is done.")

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
            return None, (f"no {DISH_POSITION!r} position: the dish centre is "
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
        refused = moves.unreachable(self.session.jog_limits, (*xy, z))
        if refused:
            self.jog.tell(f"not possible: {refused}.")
            return
        robot = self.session.robot

        def job(log):
            moves.drive_tip(robot, xy, z, None, log=log)
            return (f"tip {ABOVE_BOTTOM_MM:g} mm over the dish bottom, at the "
                    f"dish centre: jog it down until it touches")

        if not self.jog.run_job(job, what="tip over the dish centre"):
            self.jog.tell("not now: the robot is busy; try again when it is "
                          "done.")

    def _set_bottom(self) -> None:
        """Read Z where the tip is, then ask before it is saved."""
        if (self.session.robot is None or self.session.profile is None
                or self._running()):
            return
        robot = self.session.robot
        if not self.jog.run_job(lambda _log: float(xyz(robot)[2]),
                                moves=False, what="reading Z",
                                then=self._confirm_bottom):
            self.jog.tell("not now: the robot is busy; Set dish bottom here "
                          "again when it is done.")

    def _confirm_bottom(self, z: float) -> str:
        picking = self.session.profile.picking
        old, above = picking.dish_bottom, picking.pickup_offset
        text = (f"Set the dish bottom to {z:.2f} mm?\n\n"
                f"It is {old:.2f} mm now ({z - old:+.2f} mm). Pickups will be "
                f"at {z + above:.2f} mm, {above:g} mm above it.")
        if abs(z - old) > BIG_CHANGE_MM:
            text += ("\n\nThat is a big change: check that the tip rests on "
                     "the bottom of the dish and not on its rim or a lid.")
        answer = QMessageBox.question(
            self, "Dish bottom", text + "\n\nThe tip goes up afterwards.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return "dish bottom not changed"
        self.session.set_dish_bottom(z)
        # After this job's own completion, which is still being delivered.
        QTimer.singleShot(0, self._lift_after_bottom)
        return f"dish bottom set to {z:.2f} mm"

    def _lift_after_bottom(self) -> None:
        robot = self.session.robot
        if robot is None:
            return

        def job(log):
            moves.raise_tip(robot, None, log=log)
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
        robot = self.session.robot

        def job(log):
            moves.drive_tip(robot, where[:2], where[2], None, log=log)
            return f"at {SHAKE_POSITION!r}"

        if not self.jog.run_job(job):
            self.jog.tell("not now: the robot is busy; Go to shake again "
                          "when it is done.")

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

        def job(log):
            z_top = moves.drive_tip(robot, where[:2], where[2], None, log=log)
            log("stirring")
            stir(robot)
            if back is None:
                moves.raise_tip(robot, z_top, log=log)
                return "shaken"
            moves.drive_tip(robot, back[:2], back[2], z_top, log=log)
            return f"shaken; back at {DISH_POSITION!r}"

        if not self.jog.run_job(job):
            self.jog.tell("not now: the robot is busy; Shake the dish again "
                          "when it is done.")

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
            self.jog.tell("not now: the robot is busy; Set shake position "
                          "again when it is done.")

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
            self.result.setText("load a profile first: the shape windows and "
                                "the dish geometry come from its picking "
                                "configuration.")
            return
        if self.detector.model is None:
            self.result.setText(
                "no detector. Choose the cuboid weights on the Profile page; "
                "this page loads them when it opens."
                if self._wanted_model() else
                "no cuboid model named in the profile. Choose one on the "
                "Profile page.")
            return
        camera = self._camera()
        if camera is None:
            self.result.setText("no camera. It opens itself when this page is "
                                "shown; if it did not, the reason is above.")
            return

        cfg, pixel_map = profile.picking, profile.pixel_map
        detector = self.detector

        def job(log):
            log("taking a frame")
            frame = camera.read_after(time.monotonic())
            log("detecting")
            return frame, detector.detect(frame, cfg, pixel_map)

        self.result.setText("analysing…")
        self._run(Worker(job, what="analysing the dish"), self._analysed)

    def _analysed(self, payload) -> None:
        frame, detection = payload
        self._frame, self._detection = frame, detection
        self.result.setText(f"analysed at {time.strftime('%H:%M:%S')}\n"
                            f"{detection.summary}")
        log.info("analysis: %s", detection.summary.replace("\n", " | "))
        # Over the live feed, not a held frame: if the dish moves after
        # this, the contours stop sitting on the cuboids, which is the
        # sign to analyse again.
        self._draw_overlay()
        self._draw_histogram()
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
        bins = self.histogram.show_detection(
            self._detection,
            profile.picking.cuboid_size_threshold if profile else None,
            after=HIST_AFTER)
        # The same bins over the picture, small: what the plot counts.
        self.view.set_histogram(bins)

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
        self._run(Worker(self.detector.load, wanted,
                         what="loading the detector"), self._loaded)

    def _loaded(self, _description) -> None:
        if self._detection is None:
            self.result.setText("nothing analysed yet")
        self._refresh()

    # -- the run ----------------------------------------------------------------

    def _run_problems(self) -> list[str]:
        """Everything that stops a run from starting, in sentences."""
        return [check.text for check in self._checks() if check.blocking]

    def _checks(self) -> list[Check]:
        """What has to be true before a run, in the order it is done, each
        with where it is done (`widgets.checklist`). The "to do" ones stop
        Start; the notes are said and left to the operator."""
        session, out = self.session, []
        profile = session.profile
        out.append(Check("Robot connected") if session.robot is not None else
                   Check("no robot: connect it on the Profile page.", TODO,
                         "profile", "Profile"))
        if profile is None:
            out.append(Check("no profile loaded.", TODO, "profile",
                             "Profile"))
            return out
        tip = session.tip.attached
        out.append(Check("A tip is on the pipette") if tip is True else Check(
            ("the robot reports no tip on the pipette" if tip is False else
             "the robot's tip state is unknown")
            + ": pick one up on the Robot & Deck page.", TODO, "labware",
            "Robot & Deck"))

        routine = session.routine
        if routine is None:
            out.append(Check(
                "no plate plan: make one on the Plate plan page. A run with "
                "nowhere to put a cuboid picks one up and then asks what to "
                "do with it.", TODO, "routine", "Plate plan"))
        elif routine.needs_confirmation:
            out.append(Check(
                "the plate plan was restored with progress on it and has not "
                "been confirmed; confirm it on the Plate plan page.", TODO,
                "routine", "Plate plan"))
        else:
            out.append(Check(f"Plate plan {getattr(routine, 'name', '')!r}"))
        if routine is not None and session.robot is not None:
            slot = str(routine.destination.slot)
            state = session.run_state
            if state is None or slot not in state.labware:
                out.append(Check(
                    f"the robot session holds nothing in slot {slot}, which "
                    f"is where this plate plan delivers. Load the plate on "
                    f"the Robot & Deck page.", TODO, "labware",
                    "Robot & Deck"))
            else:
                problem = next((p for p in session.deck_problems()
                                if p.slot == slot), None)
                out.append(Check(
                    f"slot {slot}: the plate was loaded without the module's "
                    f"offset, and a well move would hit the module. Load it "
                    f"again on the Robot & Deck page.", TODO, "labware",
                    "Robot & Deck") if problem is not None else
                    Check(f"The plate is on the deck, in slot {slot}"))
                centre = self._well_centre()
                if centre is not None:
                    x, y, _z = centre.offset
                    out.append(Check(
                        f"Well centre measured on {centre.well or 'a well'} "
                        f"(Liquid handling): deposits go {x:+.2f}, {y:+.2f} mm "
                        f"from the robot's well centre, plus the well offset"))

        calibration = profile.calibration
        if profile.pixel_map is None:
            out.append(Check("no pixel map: run the camera calibration.",
                             TODO, "calibration", "Calibration"))
        else:
            out.append(_dated("Camera calibrated",
                              profile.pixel_map.fitted_at, "calibration",
                              "Calibration"))
        if calibration.pipette_offset is None:
            out.append(Check("no pipette offset: run the pipette "
                             "calibration.", TODO, "calibration",
                             "Calibration"))
        else:
            out.append(_dated("Pipette offset measured",
                              calibration.pipette_offset.measured_at,
                              "calibration", "Calibration"))
        bottom = calibration.dish_bottom_set_at
        out.append(_dated(
            f"Dish bottom set ({profile.picking.dish_bottom:.2f} mm)", bottom)
            if bottom is not None else Check(
                f"The dish bottom ({profile.picking.dish_bottom:.2f} mm) was "
                f"never measured here: set it under Dish bottom (Z) if "
                f"cuboids are not picked up.", NOTE))

        # Both poses the workflow drives to by name. `shake` is only
        # reached when the dish needs stirring, so without this check a run
        # can start, work for ten minutes and then fail at the one moment
        # the operator is not watching.
        for name, what, done in (
                (DISH_POSITION, "park over the dish and Set position",
                 "Picking position set"),
                (SHAKE_POSITION, "jog the tip into the dish where it should "
                                 "stir and Set shake position",
                 "Shake position set")):
            out.append(Check(done) if name in profile.positions else
                       Check(f"no {name!r} position: {what}.", TODO))
        out.append(Check("Detector loaded") if self.detector.model is not None
                   else Check("no detector: choose the weights on the "
                              "Profile page.", TODO, "profile", "Profile"))
        out.append(Check("Camera open") if self._camera() is not None else
                   Check("the camera is not open.", TODO, "profile",
                         "Profile"))
        if self.clips_box.isChecked():
            if self._lower_camera() is not None:
                out.append(Check("Lower camera open for the clips"))
            else:
                w, h = self._clip_mode()
                out.append(Check(
                    self._clips_failure + "; untick Save pickup clips, or "
                    "choose another mode in Settings." if self._clips_failure
                    else f"the lower camera is not open in {w}x{h} yet, the "
                         f"clips' mode: it is being opened.", TODO))
        return out

    def _well_centre(self):
        """The measured well centre of the plate the plan delivers to, if
        the Liquid handling page measured one: the run adds its x and y to
        every deposit (`workflows.picking`, "The measured well centre")."""
        session = self.session
        routine, state = session.routine, session.run_state
        if session.profile is None or routine is None or state is None:
            return None
        entry = state.labware.get(str(routine.destination.slot))
        if entry is None:
            return None
        return session.profile.deck.well_centre(routine.destination.slot,
                                                entry.load_name)

    def _confirm_start(self) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Start picking")
        box.setText("Start the picking routine?")
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
        self.done.clear()
        self.run_state.setText("starting…")
        self._run_worker = Worker(job, what="picking run")
        self._run_worker.finished.connect(self._run_finished)
        self._run_worker.failed.connect(self._run_failed)
        self._run_worker.message.connect(self._said)
        self._run_worker.start()
        self._view_timer.start()
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
        self._refresh()

    def _stop_run(self) -> None:
        if self._running():
            self._stop.set()
            self.run_state.setText("stopping at the next move…")

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
        self.view.set_status([])
        self.view.set_overlay_items([])
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
        lines.append("   ".join(f"{key} {what}" for key, what, _ in KEYS))
        self.view.set_status(lines)
        self.view.set_overlay_items(overlays.items(frame.shape, **view.overlays))

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
            self.result.setText("no profile loaded, so there is nothing to "
                                "save settings into.")
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

    def _clips_toggled(self, on: bool) -> None:
        self._clips_failure = ""
        self.opener.forget(self.session.lower_camera_label)
        self._ensure_clip_camera()
        self._refresh()

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
                f"camera {label!r} did not open: {reason}\n"
                f"Open it from the Profile page once the reason is fixed.")
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
        session is the robot's business and a page being looked at is not."""
        if not self._running():
            self._view_timer.stop()
        super().hideEvent(event)

    # -- display ------------------------------------------------------------------

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
            self.dish_state.setText(
                f"No {DISH_POSITION!r} position yet. Jog until the dish fills "
                f"the frame, then Set position; afterwards Go to picking "
                f"position is one button.")
        else:
            self.dish_state.setText(
                f"{DISH_POSITION}: ({stored[0]:.1f}, {stored[1]:.1f}, "
                f"{stored[2]:.1f})")

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
            "Drive the tip over the middle of the working circle, "
            f"{ABOVE_BOTTOM_MM:g} mm above the dish bottom set now."
            if centre is not None else f"Not possible: {why}.")
        self.set_bottom_button.setEnabled(
            connected and profile is not None and not running)
        self.set_bottom_button.setToolTip(
            "Save the Z the tip is at now as the dish bottom, after asking.")
        shake = self._stored_shake()
        self.goto_shake_button.setEnabled(connected and shake is not None
                                          and not running)
        # During a run only while it is paused or waits for you.
        self.shake_button.setEnabled(
            connected and shake is not None
            and (not running or self._can_shake_in_run()))
        self.shake_button.setToolTip(
            "Stir the dish at the shake position. During a run: while it is "
            "paused or waits for you, done by the run itself."
            if shake is not None else
            f"Not possible: no {SHAKE_POSITION!r} position yet.")
        self.teach_shake_button.setEnabled(connected and profile is not None
                                           and not running)
        if profile is None:
            self.shake_state.setText("No profile loaded.")
        elif shake is None:
            self.shake_state.setText(
                f"No {SHAKE_POSITION!r} position yet. When too few cuboids "
                f"are isolated the run stirs the dish there: jog the tip "
                f"into the medium, clear of the cuboids, then Set shake "
                f"position.")
        else:
            self.shake_state.setText(
                f"{SHAKE_POSITION}: ({shake[0]:.1f}, {shake[1]:.1f}, "
                f"{shake[2]:.1f})")

        self.settings_button.setEnabled(profile is not None and not busy
                                        and not running)
        self.analyse_button.setEnabled(
            not busy and not running and profile is not None
            and self.detector.model is not None
            and self._camera() is not None)

        routine = self.session.routine
        self.routine_state.setText(
            "Plate plan: none. Make one on the Plate plan page."
            if routine is None else f"Plate plan:\n{routine.summary()}")

        self.clips_box.setEnabled(not running)
        if not self.clips_box.isChecked():
            self.clips_state.setText("")
        elif running and self._clip_dir is not None:
            self.clips_state.setText(f"Clips go to {self._clip_dir}")
        else:
            w, h = self._clip_mode()
            self.clips_state.setText(
                f"One clip per pickup, the lower camera at {w}x{h}, crop "
                f"{self.session.settings.clip_crop:g} (Settings), into a "
                f"folder for the run in {paths.clips_dir(create=False)}")
        self.clips_state.setVisible(self.clips_box.isChecked())

        checks = self._checks()
        problems = [check.text for check in checks if check.blocking]
        self.start_button.setEnabled(not busy and not running and not problems)
        self.resume_button.setEnabled(self._waiting())
        self.pause_button.setEnabled(running)
        self.stop_button.setEnabled(running)
        self.checklist.setVisible(not running)
        if not running:
            self.checklist.show_checks(checks)
        if running:
            self.run_state.setText(
                f"{self._state_text} — {self._last_event}\n"
                + "  ".join(f"{key}: {what}" for key, what, _ in KEYS))
        elif problems:
            todo = len(problems)
            self.run_state.setText(
                (self._run_message + "\n\n" if self._run_message else "")
                + f"Before Start: {todo} thing{'s' if todo != 1 else ''} to "
                  f"do, marked red below.")
        else:
            self.run_state.setText(
                (self._run_message + "\n\n" if self._run_message else "")
                + "Ready to start.")
