"""Picking. So far: a frame, a detector, and what it found.

The run itself comes later. What is here is the part that has to work before a
run is worth starting — open a picture, put a detector behind it, and look at
the result the way the picking window shows it — and it is the same code the
run will use, not a demonstration to be thrown away.

The overlay is drawn by `widgets/overlay_painter` from `viz.overlays.items`,
which is the same list `viz.overlays.draw` renders with cv2 for the notebook.
One description of the geometry, two renderers.
"""

from __future__ import annotations

import logging

import cv2
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel,
                               QVBoxLayout, QWidget)

from ... import paths
from ...viz import overlays
from ..detector import STANDIN, DetectorService
from ..session import Session
from ..theme import SPACING
from ..theme.factory import card, heading, primary_button, secondary_button
from ..widgets.camera_view import CameraView
from ..workers import Worker

__all__ = ["PickingPage"]

TITLE = "Picking"

log = logging.getLogger(__name__)

PANEL_WIDTH = 420


class PickingPage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.detector = DetectorService(self)
        self._worker: Worker | None = None
        self._frame = None
        self._detection = None

        self.view = CameraView(self)
        # A still picture, not a camera: `hold` is the same switch the picking
        # session throws when the frame a decision was made on has to stay put.
        self.view.live = False

        panel = QWidget(self)
        panel.setFixedWidth(PANEL_WIDTH)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        column.addWidget(self._source_card())
        column.addWidget(self._detector_card())
        column.addWidget(self._result_card())
        column.addStretch(1)

        body = QHBoxLayout()
        body.setSpacing(SPACING)
        body.addWidget(self.view, 1)
        body.addWidget(panel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2, SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addWidget(heading(TITLE, 1))
        layout.addLayout(body, 1)

        session.profile_changed.connect(lambda _p: self._refresh())
        self._reload_weights()
        self._refresh()

    # -- construction --------------------------------------------------------

    def _source_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Frame", 2))
        self.open_button = secondary_button("Open frame from file…", self)
        self.open_button.clicked.connect(self._open)
        box.layout().addWidget(self.open_button)
        self.frame_label = QLabel("no frame")
        self.frame_label.setWordWrap(True)
        box.layout().addWidget(self.frame_label)
        return box

    def _detector_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Detector", 2))
        self.weights = QComboBox(self)
        box.layout().addWidget(self.weights)

        row = QHBoxLayout()
        self.load_button = secondary_button("Load", self)
        self.load_button.clicked.connect(self._load)
        self.detect_button = primary_button("Detect", self)
        self.detect_button.clicked.connect(self._detect)
        row.addWidget(self.load_button)
        row.addWidget(self.detect_button)
        row.addStretch(1)
        box.layout().addLayout(row)

        # Shown, not logged: a stand-in result looks exactly like a real one.
        self.detector_state = QLabel(self.detector.description)
        self.detector_state.setWordWrap(True)
        box.layout().addWidget(self.detector_state)
        return box

    def _result_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Result", 2))
        self.result = QLabel("nothing detected yet")
        self.result.setWordWrap(True)
        box.layout().addWidget(self.result)
        return box

    # -- actions -------------------------------------------------------------

    def _reload_weights(self) -> None:
        self.weights.clear()
        found = DetectorService.available_weights()
        # The stand-in last: it works everywhere, and offering it first would
        # make it the default on a bench that has real weights installed.
        self.weights.addItems([*found, STANDIN])
        if found:
            self.weights.setCurrentIndex(0)

    def _open(self) -> None:
        start = paths.fixtures_dir() / "bench"
        name, _ = QFileDialog.getOpenFileName(
            self, "Open a frame", str(start if start.is_dir() else paths.root()),
            "Images (*.png *.jpg *.jpeg *.tif *.tiff);;All files (*)")
        if not name:
            return
        frame = cv2.imread(name, cv2.IMREAD_COLOR)
        if frame is None:
            self.frame_label.setText(f"could not read {name}")
            return
        self._frame = frame
        self._detection = None
        height, width = frame.shape[:2]
        self.frame_label.setText(f"{name}\n{width}×{height}")
        self.view.set_overlay_items([])
        self.view.hold(frame)
        self.result.setText("nothing detected yet")
        log.info("opened %s (%dx%d)", name, width, height)
        self._refresh()

    def _load(self) -> None:
        name = self.weights.currentText()
        if not name or self._busy():
            return
        self.detector_state.setText(f"loading {name}…")
        self._run(Worker(self.detector.load, name), self._loaded)

    def _loaded(self, description) -> None:
        self.detector_state.setText(str(description))
        self._refresh()

    def _detect(self) -> None:
        if self._frame is None or self.detector.model is None or self._busy():
            return
        profile = self.session.profile
        cfg = profile.picking if profile is not None else None
        if cfg is None:
            self.result.setText("load a profile first: the shape windows and "
                                "the dish geometry come from its picking "
                                "configuration.")
            return
        self.result.setText("detecting…")
        self._run(Worker(self.detector.detect, self._frame, cfg,
                         profile.pixel_map), self._detected)

    def _detected(self, detection) -> None:
        self._detection = detection
        self.result.setText(detection.summary)
        log.info("detection: %s", detection.summary.replace("\n", " | "))

        profile = self.session.profile
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
        self._refresh()

    # -- the worker ----------------------------------------------------------

    def _busy(self) -> bool:
        return self._worker is not None and self._worker.running

    def _run(self, worker: Worker, done) -> None:
        self._worker = worker
        # Bound methods and a slot on this object: a lambda has no receiver, so
        # Qt would connect it directly and touch these widgets from the
        # worker's thread.
        worker.finished.connect(done)
        worker.finished.connect(self._cleared)
        worker.failed.connect(self._failed)
        worker.message.connect(self._said)
        worker.start()
        self._refresh()

    def _cleared(self, _result=None) -> None:
        self._worker = None
        self._refresh()

    def _said(self, text: str) -> None:
        log.info("%s", text)

    def _failed(self, reason: str) -> None:
        self._worker = None
        self.result.setText(reason)
        self.detector_state.setText(self.detector.description)
        log.error("%s", reason)
        self._refresh()

    # -- display -------------------------------------------------------------

    def _refresh(self) -> None:
        busy = self._busy()
        self.open_button.setEnabled(not busy)
        self.weights.setEnabled(not busy)
        self.load_button.setEnabled(not busy and self.weights.count() > 0)
        self.detect_button.setEnabled(
            not busy and self._frame is not None
            and self.detector.model is not None)
