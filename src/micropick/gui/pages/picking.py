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

Starting needs a routine, because a run with nowhere to put a cuboid is a
run that picks one up and then asks what to do with it. The Routine page
hands its routine to the session; this page reads it there and says above
the Start button which one it is.

The dish pose is a profile position
-----------------------------------
`observe`, beside `tip_calib`, taught the same way and driven to the same
way, because the gantry has to be somewhere particular for the dish to be
in frame and that somewhere is an installation's fact rather than a run's.
The name is the workflow's: `PickingSession` reads `profile.where("observe")`
and refuses to start without it, so teaching it here is teaching the run's
own starting pose rather than a second one that looks like it.

The overlay is drawn by `widgets/overlay_painter` from `viz.overlays.items`,
which is the same list `viz.overlays.draw` renders with cv2 for the
notebook. One description of the geometry, two renderers.
"""

from __future__ import annotations

import logging
import threading
import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMessageBox, QVBoxLayout,
                               QWidget)

from ...core.calibration.pixel_map import PixelMap
from ...hardware.protocols import move_to, xyz
from ...viz import overlays
from ...workflows.picking import PickingSession, RobotState
from ..auto_camera import CameraOpener
from ..detector import wanted_model
from ..session import Session
from ..theme import SPACING
from ..theme.factory import (card, combo_box, heading, primary_button,
                             scroll_column, secondary_button)
from ..widgets.camera_view import CameraView
from ..widgets.card_columns import CardColumns
from ..widgets.feed_row import FeedRow
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

# How often the display looks at what the run is showing. The session's own
# pace is set by the robot; this is only the refresh of a picture.
VIEW_MS = 100

# What the keys do, shown on the picture and bound below. One list, so the
# overlay cannot promise a key that is not bound.
KEYS = (("Space", "resume", "resume"),
        ("P", "pause", "pause"),
        ("Esc", "stop", "stop"))

# Histogram colours. A plot is its own surface, like the camera viewport,
# so these are fixed; the ink follows the palette's text colour, which is
# the one role qdarktheme really varies between light and dark.
BAR = (94, 158, 235)
INSIDE = (120, 220, 130)
WINDOW_EDGE = (240, 160, 48)

HIST_BINS = 28

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

        self.view = CameraView(self)

        panel = CardColumns([self._dish_card(), self._analysis_card(),
                             self._histogram_card(), self._run_card(),
                             self._jog_section()], self)

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
        self.goto_button = secondary_button("Go to the dish", self)
        self.goto_button.clicked.connect(self._goto_dish)
        self.teach_button = secondary_button("Teach here", self)
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
        self.hist = pg.PlotWidget(background=None)
        ink = self.palette().color(QPalette.ColorRole.Text)
        for axis in ("left", "bottom"):
            self.hist.getAxis(axis).setPen(ink)
            self.hist.getAxis(axis).setTextPen(ink)
        # The plot is a readout, not something to explore. Left to itself
        # pyqtgraph keeps whatever range the last wheel turn or auto-range
        # left behind, and a plot in a scrolling column collects wheel
        # turns meant for the column - which is how it ends up too tall or
        # too narrow with an "A" button in the corner as the only way back.
        # Both axes are set from the data after every draw instead.
        view = self.hist.getPlotItem().getViewBox()
        view.setMouseEnabled(x=False, y=False)
        view.setMenuEnabled(False)
        view.wheelEvent = lambda event, axis=None: event.ignore()
        self.hist.getPlotItem().hideButtons()
        self.hist.setLabel("bottom", "diameter, µm")
        self.hist.setLabel("left", "cuboids")
        self.hist.showGrid(x=True, y=True, alpha=0.2)
        # Bounded above too: a plot widget expands, and in a tall column it
        # took every spare pixel and pushed the run card out of sight.
        self.hist.setMinimumHeight(190)
        self.hist.setMaximumHeight(260)
        box.layout().addWidget(self.hist)

        self.window_state = QLabel(
            "The shaded band is cuboid_size_threshold: what a run would "
            "accept. Analyse the dish to see what is in it.")
        self.window_state.setWordWrap(True)
        box.layout().addWidget(self.window_state)
        return box

    def _run_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("The run", 2))

        # Which routine Start would run, from the Routine page, before the
        # button that commits the robot to it.
        self.routine_state = QLabel()
        self.routine_state.setWordWrap(True)
        self.routine_state.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addWidget(self.routine_state)

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

    def _goto_dish(self) -> None:
        where = self._stored_dish()
        if where is None or self.session.robot is None or self._busy():
            return
        robot = self.session.robot
        self._run(Worker(lambda: move_to(robot, where, min_z_height=1.0)),
                  self._moved)

    def _teach_dish(self) -> None:
        if self.session.robot is None or self.session.profile is None or self._busy():
            return
        robot, session = self.session.robot, self.session
        self._run(Worker(lambda: session.remember(DISH_POSITION, xyz(robot))),
                  self._moved)

    def _moved(self, _result=None) -> None:
        self._refresh()

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
        self._run(Worker(job), self._analysed)

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
        # clear() drops the items; the ranges below are what stop the view
        # box from keeping the last dish's.
        self.hist.clear()
        detection = self._detection
        profile = self.session.profile
        if detection is None or profile is None:
            self.window_state.setText("nothing measured yet")
            return
        if not detection.classified or "diameter_microns" not in detection.df:
            # Without a pixel map there are no millimetres, so there are no
            # microns either, and a histogram of pixels would be a different
            # quantity wearing the same axis label.
            self.window_state.setText(
                "sizes need the pixel map: " + " ".join(detection.notes))
            return

        sizes = detection.df.diameter_microns.to_numpy(dtype=float)
        sizes = sizes[np.isfinite(sizes)]
        low, high = (float(v) for v in profile.picking.cuboid_size_threshold)
        if len(sizes) == 0:
            self.window_state.setText("no detections to measure")
            return

        # A dish with one cuboid, or with several of exactly one size, gives
        # numpy a zero-width range and every bin edge the same number: the
        # bars come out zero wide and the plot looks empty. Give it a span
        # to divide, centred on the value.
        span = float(sizes.max() - sizes.min())
        if span <= 0:
            centre = float(sizes[0])
            pad = max(1.0, abs(centre) * 0.1)
            edges = np.linspace(centre - pad, centre + pad, HIST_BINS + 1)
        else:
            edges = np.histogram_bin_edges(sizes, bins=HIST_BINS)
        counts, edges = np.histogram(sizes, bins=edges)
        width = float(edges[1] - edges[0]) if len(edges) > 1 else 1.0
        centres = (edges[:-1] + edges[1:]) / 2
        # Two series rather than one recoloured: a bar is inside the window
        # or it is not, and the eye should not have to compare a shade with
        # the band behind it.
        inside_bin = (centres >= low) & (centres <= high)
        for mask, colour in ((~inside_bin, BAR), (inside_bin, INSIDE)):
            if not mask.any():
                continue
            self.hist.addItem(pg.BarGraphItem(
                x=centres[mask], height=counts[mask], width=width * 0.92,
                brush=pg.mkBrush(*colour, 200), pen=pg.mkPen(*colour)))

        region = pg.LinearRegionItem(values=(low, high), movable=False,
                                     brush=pg.mkBrush(*WINDOW_EDGE, 28),
                                     pen=pg.mkPen(*WINDOW_EDGE, width=2))
        region.setZValue(-10)
        self.hist.addItem(region)
        # Both the population and the window in view, so "the cuboids are to
        # the left of the window" is a thing that can be seen rather than
        # inferred from two numbers off the edge of the plot.
        left = min(float(edges[0]), low)
        right = max(float(edges[-1]), high)
        margin = max(1.0, (right - left) * 0.05)
        self.hist.setXRange(left - margin, right + margin, padding=0)
        # And the height, for the same reason: a bar chart whose y range is
        # remembered from another dish is a bar chart of the wrong height.
        self.hist.setYRange(0, max(1, int(counts.max())) * 1.1, padding=0)

        inside = int(((sizes >= low) & (sizes <= high)).sum())
        smaller = int((sizes < low).sum())
        bigger = int((sizes > high).sum())
        self.window_state.setText(
            f"{inside} of {len(sizes)} cuboids are inside "
            f"{low:g}–{high:g} µm — {smaller} smaller, {bigger} bigger. "
            f"Only the shape windows and the spacing rule can reject one "
            f"after that, so this is the most a run could pick from this "
            f"frame.")

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
        self._run(Worker(self.detector.load, wanted), self._loaded)

    def _loaded(self, _description) -> None:
        if self._detection is None:
            self.result.setText("nothing analysed yet")
        self._refresh()

    # -- the run ----------------------------------------------------------------

    def _run_problems(self) -> list[str]:
        """Everything that stops a run from starting, in sentences."""
        session, out = self.session, []
        profile = session.profile
        if session.robot is None:
            out.append("no robot: connect it on the Profile page.")
        if profile is None:
            out.append("no profile loaded.")
            return out
        if session.routine is None:
            out.append("no routine: make a plan on the Routine page. A run "
                       "with nowhere to put a cuboid picks one up and then "
                       "asks what to do with it.")
        elif session.routine.needs_confirmation:
            out.append("the routine was restored with progress on it and has "
                       "not been confirmed; confirm it on the Routine page.")
        if profile.pixel_map is None:
            out.append("no pixel map: run the camera calibration.")
        if profile.calibration.pipette_offset is None:
            out.append("no pipette offset: run the pipette calibration.")
        # Both poses the workflow drives to by name. `shake` is only
        # reached when the dish needs stirring, so without this check a run
        # can start, work for ten minutes and then fail at the one moment
        # the operator is not watching.
        for name, what in ((DISH_POSITION, "park over the dish and Teach here"),
                           (SHAKE_POSITION,
                            "jog to where the dish is shaken and save it as "
                            f"{SHAKE_POSITION!r} in the jog panel")):
            if name not in profile.positions:
                out.append(f"no {name!r} position: {what}.")
        if self.detector.model is None:
            out.append("no detector: choose the weights on the Profile page.")
        if self._camera() is None:
            out.append("the camera is not open.")
        if session.tip.attached is not True:
            out.append("the robot reports no tip on the pipette"
                       if session.tip.attached is False else
                       "the robot's tip state is unknown")
            out[-1] += ": pick one up on the Labware page."
        if session.routine is not None and session.robot is not None:
            slot = str(session.routine.destination.slot)
            state = session.run_state
            if state is None or slot not in state.labware:
                out.append(f"the run holds nothing in slot {slot}, which is "
                           f"where this routine delivers. Load the plate on "
                           f"the Labware page.")
        return out

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
        pause, stop = self._pause, self._stop
        pause.clear()
        stop.clear()
        emit = self.event_seen.emit

        def job(log):
            """The notebook's worker loop, with the display on the other side."""
            picking = PickingSession(robot, camera, pmap, profile, routine,
                                     detector, labware_id=labware_id)
            self._session = picking
            # The confirmation was the go-ahead; see "Start is the go-ahead".
            picking.start()
            emit(("started", picking.view, picking.state.value, "starting"))
            try:
                while not picking.done:
                    event = picking.step(pause=pause, stop=stop)
                    log(str(event))
                    emit(("event", event.view, event.state.value,
                          f"{event.kind}: {event.message}"))
            finally:
                picking.close()          # recorder detached, light restored
            return picking.state.value

        self._run_message = ""
        self.run_state.setText("starting…")
        self._run_worker = Worker(job)
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
        self._view_timer.stop()
        self._view = None
        self._run_message = f"run {state}"
        log.info("picking run %s", state)
        self._back_to_live()

    def _run_failed(self, reason: str) -> None:
        self._run_worker = None
        self._session = None
        self._view_timer.stop()
        self._view = None
        self._run_message = reason
        log.error("picking run failed: %s", reason)
        self._back_to_live()

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
                                       profile_name=profile.name, parent=self)
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

    def _open_failed(self, label: str, reason: str) -> None:
        self.dish_state.setText(f"camera {label!r} did not open: {reason}\n"
                                f"Open it from the Profile page once the "
                                f"reason is fixed.")
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
        self.teach_button.setText("Re-teach here" if stored is not None
                                  else "Teach here")
        if profile is None:
            self.dish_state.setText("No profile loaded.")
        elif stored is None:
            self.dish_state.setText(
                f"No {DISH_POSITION!r} position yet. Jog until the dish fills "
                f"the frame, then Teach here; afterwards this is one button.")
        else:
            self.dish_state.setText(
                f"{DISH_POSITION}: ({stored[0]:.1f}, {stored[1]:.1f}, "
                f"{stored[2]:.1f})")

        running = self._running()
        self.settings_button.setEnabled(profile is not None and not busy
                                        and not running)
        self.analyse_button.setEnabled(
            not busy and not running and profile is not None
            and self.detector.model is not None
            and self._camera() is not None)

        routine = self.session.routine
        self.routine_state.setText(
            "Routine: none. Make a plan on the Routine page."
            if routine is None else f"Routine:\n{routine.summary()}")

        problems = self._run_problems()
        self.start_button.setEnabled(not busy and not running and not problems)
        self.resume_button.setEnabled(self._waiting())
        self.pause_button.setEnabled(running)
        self.stop_button.setEnabled(running)
        if running:
            self.run_state.setText(
                f"{self._state_text} — {self._last_event}\n"
                + "  ".join(f"{key}: {what}" for key, what, _ in KEYS))
        elif problems:
            self.run_state.setText("\n".join("• " + p for p in problems))
        else:
            self.run_state.setText(
                self._run_message or "ready to start.")
