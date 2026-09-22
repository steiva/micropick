"""Picking: look at the dish before committing the robot to it.

The run itself comes later. What is here is everything that has to be true
before a run is worth starting, and it is the same code the run will use,
not a demonstration to be thrown away: the camera over the dish, the pose
it is looked at from, the settings the run reads, and one frame put through
the detector with the answer laid out — how many cuboids, how big they are,
and how many of them the size window would actually accept.

The size window is the point of the histogram
---------------------------------------------
`cuboid_size_threshold` decides what the run will pick, and it is two
numbers in a configuration file. On a dish of real cuboids it is also the
difference between a run that fills a plate and one that finds nothing, and
nothing on screen used to say which. The histogram is every detection's
diameter with that window drawn over it, and the count inside it stated in
words. An operator who sees the population sitting to the left of the
window knows what to change and by how much, before the robot moves.

The camera opens itself
-----------------------
Arriving at this page is the request to see the dish; see `gui.auto_camera`
for why that is not the same as reaching for hardware at startup, and what
keeps it from retrying an unplugged camera for ever.

The dish pose is a profile position
-----------------------------------
`dish` beside `tip_calib`, taught the same way and driven to the same way,
because the gantry has to be somewhere particular for the dish to be in
frame and that somewhere is an installation's fact rather than a run's. The
jog panel is here to teach it and to nudge it; the button is for the
hundred times afterwards.

The overlay is drawn by `widgets/overlay_painter` from `viz.overlays.items`,
which is the same list `viz.overlays.draw` renders with cv2 for the
notebook. One description of the geometry, two renderers.
"""

from __future__ import annotations

import logging
import time

import cv2
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QVBoxLayout,
                               QWidget)

from ... import paths
from ...hardware.protocols import move_to, xyz
from ...viz import overlays
from ..auto_camera import CameraOpener
from ..detector import STANDIN, DetectorService
from ..session import Session
from ..theme import SPACING
from ..theme.factory import (Section, card, combo_box, heading, primary_button,
                             scroll_column, secondary_button)
from ..widgets.camera_view import CameraView
from ..widgets.jog_panel import JogPanel
from ..widgets.settings_form import PickingSettingsDialog
from ..workers import Worker

__all__ = ["PickingPage", "DISH_POSITION"]

TITLE = "Picking"

log = logging.getLogger(__name__)

PANEL_WIDTH = 440

# The profile position the dish is looked at from, beside tip_calib. The
# name is the one a notebook would use for the same pose.
DISH_POSITION = "dish"

# Histogram colours. A plot is its own surface, like the camera viewport,
# so these are fixed; the ink follows the palette's text colour, which is
# the one role qdarktheme really varies between light and dark.
BAR = (94, 158, 235)
INSIDE = (120, 220, 130)
WINDOW_EDGE = (240, 160, 48)

HIST_BINS = 28


class PickingPage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.detector = DetectorService(self)
        self.opener = CameraOpener(session, self)
        self._worker: Worker | None = None
        self._frame = None
        self._detection = None

        self.view = CameraView(self)

        panel = QWidget(self)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        column.addWidget(self._dish_card())
        column.addWidget(self._analysis_card())
        column.addWidget(self._histogram_card())
        column.addWidget(self._detector_section())
        column.addWidget(self._jog_section(), 1)

        body = QHBoxLayout()
        body.setSpacing(SPACING)
        body.addWidget(self.view, 1)
        body.addWidget(scroll_column(panel, PANEL_WIDTH))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2, SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addWidget(heading(TITLE, 1))
        layout.addLayout(body, 1)

        session.profile_changed.connect(self._on_profile_changed)
        session.robot_state_changed.connect(lambda _s: self._refresh())
        session.camera_opened.connect(lambda _l: self._show_camera())
        session.camera_closed.connect(lambda _l: self._show_camera())
        self.opener.failed.connect(self._open_failed)
        self._reload_weights()
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

        row = QHBoxLayout()
        self.analyse_button = primary_button("Analyse the dish", self)
        self.analyse_button.clicked.connect(self._analyse)
        self.live_button = secondary_button("Back to live", self)
        self.live_button.clicked.connect(self._resume)
        row.addWidget(self.analyse_button)
        row.addWidget(self.live_button)
        box.layout().addLayout(row)

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
        self.hist.setLabel("bottom", "diameter, µm")
        self.hist.setLabel("left", "cuboids")
        self.hist.showGrid(x=True, y=True, alpha=0.2)
        self.hist.setMinimumHeight(190)
        box.layout().addWidget(self.hist)

        self.window_state = QLabel(
            "The shaded band is cuboid_size_threshold: what a run would "
            "accept. Analyse the dish to see what is in it.")
        self.window_state.setWordWrap(True)
        box.layout().addWidget(self.window_state)
        return box

    def _detector_section(self) -> QWidget:
        """Folded: the weights are chosen once and then not thought about."""
        box = Section("Detector", collapsed=True, parent=self)
        self.weights = combo_box(self)
        box.body.layout().addWidget(self.weights)

        row = QHBoxLayout()
        self.load_button = secondary_button("Load", self)
        self.load_button.clicked.connect(self._load)
        self.open_button = secondary_button("Frame from file…", self)
        self.open_button.setToolTip(
            "Analyse a saved picture instead of the camera: how a detector "
            "is compared against a frame whose answer is already known.")
        self.open_button.clicked.connect(self._open)
        row.addWidget(self.load_button)
        row.addWidget(self.open_button)
        box.body.layout().addLayout(row)

        # Shown, not logged: a stand-in result looks exactly like a real one.
        self.detector_state = QLabel(self.detector.description)
        self.detector_state.setWordWrap(True)
        box.body.layout().addWidget(self.detector_state)
        return box

    def _jog_section(self) -> QWidget:
        self.jog = JogPanel(self.session, shortcut_host=self,
                            machine_controls=False,
                            collapsed=("move", "positions"), parent=self)
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
        """One frame from the camera, through the detector, held on screen."""
        if self._busy():
            return
        profile = self.session.profile
        if profile is None:
            self.result.setText("load a profile first: the shape windows and "
                                "the dish geometry come from its picking "
                                "configuration.")
            return
        if self.detector.model is None:
            self.result.setText("no detector loaded — open Detector below and "
                                "load the weights.")
            return
        camera = self._camera()
        if camera is None and self._frame is None:
            self.result.setText("no camera and no frame. The camera opens "
                                "itself when this page is shown; if it did "
                                "not, the reason is above.")
            return

        cfg, pixel_map = profile.picking, profile.pixel_map
        detector, frame = self.detector, self._frame

        def job(log):
            grabbed = frame
            if camera is not None:
                log("taking a frame")
                grabbed = camera.read_after(time.monotonic())
            log("detecting")
            return grabbed, detector.detect(grabbed, cfg, pixel_map)

        self.result.setText("analysing…")
        self._run(Worker(job), self._analysed)

    def _analysed(self, payload) -> None:
        frame, detection = payload
        self._frame, self._detection = frame, detection
        self.result.setText(detection.summary)
        log.info("analysis: %s", detection.summary.replace("\n", " | "))
        # Held, not live: the numbers and the overlay belong to this frame
        # and no other, which is the same switch the picking session throws.
        self.view.hold(frame)
        self._draw_overlay()
        self._draw_histogram()
        self._refresh()

    def _resume(self) -> None:
        self._detection = None
        self.view.set_overlay_items([])
        self.view.resume()
        self._show_camera()
        self.result.setText("live. Analyse the dish to measure it.")
        self._refresh()

    def _draw_overlay(self) -> None:
        detection, profile = self._detection, self.session.profile
        if detection is None or profile is None or self._frame is None:
            self.view.set_overlay_items([])
            return
        cfg = profile.picking
        # Only when the frame is the size the dish geometry describes; on a
        # picture opened from disk it usually is not, and a circle drawn
        # somewhere arbitrary would read as the dish.
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

    def _reload_weights(self) -> None:
        self.weights.clear()
        found = DetectorService.available_weights()
        # The stand-in last: it works everywhere, and offering it first would
        # make it the default on a bench that has real weights installed.
        self.weights.addItems([*found, STANDIN])
        if found:
            self.weights.setCurrentIndex(0)

    def _load(self) -> None:
        name = self.weights.currentText()
        if not name or self._busy():
            return
        self.detector_state.setText(f"loading {name}…")
        self._run(Worker(self.detector.load, name), self._loaded)

    def _loaded(self, description) -> None:
        self.detector_state.setText(str(description))
        self._refresh()

    def _open(self) -> None:
        start = paths.fixtures_dir() / "bench"
        name, _ = QFileDialog.getOpenFileName(
            self, "Open a frame", str(start if start.is_dir() else paths.root()),
            "Images (*.png *.jpg *.jpeg *.tif *.tiff);;All files (*)")
        if not name:
            return
        frame = cv2.imread(name, cv2.IMREAD_COLOR)
        if frame is None:
            self.result.setText(f"could not read {name}")
            return
        self._frame, self._detection = frame, None
        height, width = frame.shape[:2]
        self.view.set_overlay_items([])
        self.view.hold(frame)
        self.camera_choice.setCurrentIndex(-1)
        self.result.setText(f"{name}\n{width}×{height} — analyse it below.")
        log.info("opened %s (%dx%d)", name, width, height)
        self._refresh()

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
        self.detector_state.setText(self.detector.description)
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
        if self._detection is None:
            self.view.set_camera(self._camera())
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

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.opener.ensure(self._wanted_camera())
        self._show_camera()

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

        self.weights.setEnabled(not busy)
        self.load_button.setEnabled(not busy and self.weights.count() > 0)
        self.open_button.setEnabled(not busy)
        self.settings_button.setEnabled(profile is not None and not busy)
        self.analyse_button.setEnabled(
            not busy and profile is not None
            and self.detector.model is not None
            and (self._camera() is not None or self._frame is not None))
        self.live_button.setEnabled(not busy and self._detection is not None)
