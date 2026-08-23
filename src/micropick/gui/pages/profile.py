"""Choosing a profile, connecting the robot, opening cameras.

Everything here goes through the session. The page knows which buttons exist
and what state they should be in; it does not know whether the robot behind
them is an HTTP client or a mock, and it never opens a device itself.

The two slow calls — connecting and opening a camera — run in a `Worker`. An
HTTP round trip to the robot and a camera warm-up are both seconds, and in the
GUI thread each of them is a frozen window.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QVBoxLayout,
                               QWidget)

from ...config.store import LegacyProfileError, list_profiles
from ..session import MOCK_PROFILE_NAME, Session
from ..theme import SPACING
from ..theme.factory import card, heading, primary_button, secondary_button
from ..workers import Worker

__all__ = ["ProfilePage"]

TITLE = "Profile"

log = logging.getLogger(__name__)


def _calibration_state(profile) -> str:
    pixel_map = profile.pixel_map
    if pixel_map is None:
        return "not calibrated — run the camera sweep"
    parts = [f"pixel map: degree {pixel_map.degree}"]
    if pixel_map.n_poses is not None:
        parts.append(f"{pixel_map.n_poses} poses")
    if pixel_map.holdout_mean_um is not None:
        parts.append(f"held-out {pixel_map.holdout_mean_um:.1f} µm mean")
    if profile.calibration.pipette_offset is None:
        parts.append("no pipette offset")
    return ", ".join(parts)


class _CameraRow(QWidget):
    """One camera of the profile: what it is, and one button to open or close."""

    def __init__(self, page: "ProfilePage", label: str, spec, parent=None):
        super().__init__(parent)
        self.label = label
        self.page = page

        width, height = spec.default_resolution
        text = f"{label} — {width}×{height}"
        if spec.crop != 1.0:
            text += f", view crop {spec.crop:g}"

        self.name = QLabel(text)
        self.button = secondary_button("Open", self)
        self.button.clicked.connect(self._toggle)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.name)
        row.addStretch(1)
        row.addWidget(self.button)

    def _toggle(self) -> None:
        if self.page.session.camera(self.label) is None:
            self.page.open_camera(self.label)
        else:
            self.page.close_camera(self.label)

    def refresh(self, busy: bool) -> None:
        is_open = self.page.session.camera(self.label) is not None
        self.button.setText("Close" if is_open else "Open")
        # Closing is instant and safe while something else is opening; opening
        # is not, so only that half waits.
        self.button.setEnabled(is_open or not busy)


class ProfilePage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._worker: Worker | None = None
        self._camera_rows: list[_CameraRow] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2, SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addWidget(heading(TITLE, 1))
        layout.addWidget(self._profile_card())
        layout.addWidget(self._robot_card())
        layout.addWidget(self._cameras_card())

        # Full width, selectable, and never truncated: LegacyProfileError's own
        # text says what to do about it, so it is shown as it comes rather than
        # summarised into a line that does not.
        self.error = QLabel()
        self.error.setWordWrap(True)
        # Selectable, so a path or a key name in the message can be copied out
        # rather than retyped from the screen.
        self.error.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.error.hide()
        layout.addWidget(self.error)
        layout.addStretch(1)

        session.profile_changed.connect(self._on_profile_changed)
        session.robot_state_changed.connect(lambda _s: self.refresh())
        session.camera_opened.connect(lambda _l: self.refresh())
        session.camera_closed.connect(lambda _l: self.refresh())

        self.reload_profile_list()
        self.refresh()

    # -- construction --------------------------------------------------------

    def _profile_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Installation", 2))

        self.chooser = QComboBox(self)
        self.load_button = primary_button("Load", self)
        self.load_button.clicked.connect(self.load_selected)
        row = QHBoxLayout()
        row.addWidget(self.chooser, 1)
        row.addWidget(self.load_button)
        box.layout().addLayout(row)

        self.profile_path = QLabel()
        self.profile_path.setWordWrap(True)
        self.calibration = QLabel()
        self.calibration.setWordWrap(True)
        box.layout().addWidget(self.profile_path)
        box.layout().addWidget(self.calibration)
        return box

    def _robot_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Robot", 2))
        self.robot_state = QLabel()
        box.layout().addWidget(self.robot_state)

        self.connect_button = primary_button("Connect", self)
        self.connect_button.clicked.connect(self._connect)
        self.disconnect_button = secondary_button("Disconnect", self)
        self.disconnect_button.clicked.connect(self.session.disconnect_robot)
        row = QHBoxLayout()
        row.addWidget(self.connect_button)
        row.addWidget(self.disconnect_button)
        row.addStretch(1)
        box.layout().addLayout(row)
        return box

    def _cameras_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Cameras", 2))
        self.no_cameras = QLabel("No profile loaded.")
        self.no_cameras.setWordWrap(True)
        box.layout().addWidget(self.no_cameras)
        self._cameras_box = box
        return box

    # -- actions -------------------------------------------------------------

    def reload_profile_list(self) -> None:
        self.chooser.clear()
        names = list_profiles()
        if self.session.mock:
            # First, because in mock mode it is the one that works.
            names = [MOCK_PROFILE_NAME] + [n for n in names
                                           if n != MOCK_PROFILE_NAME]
        self.chooser.addItems(names)
        if self.session.profile is not None:
            index = self.chooser.findText(self.session.profile.name)
            if index >= 0:
                self.chooser.setCurrentIndex(index)

    def load_selected(self) -> None:
        """Load whatever the chooser is showing. Also the start-up path."""
        name = self.chooser.currentText()
        if not name:
            self._show_error("There are no profiles under profiles/.")
            return
        self._clear_error()
        try:
            self.session.load_profile(name)
        except LegacyProfileError as exc:
            # Shown in full: this message is the instructions.
            log.error("%s", exc)
            self._show_error(str(exc))
        except Exception as exc:                     # noqa: BLE001
            log.error("could not load profile %r: %s", name, exc)
            self._show_error(str(exc))

    def _connect(self) -> None:
        self._clear_error()
        self._run(Worker(self.session.connect_robot),
                  "connecting the robot")

    def open_camera(self, label: str) -> None:
        self._clear_error()
        self._run(Worker(self.session.open_camera, label),
                  f"opening camera {label!r}")

    def close_camera(self, label: str) -> None:
        try:
            self.session.close_camera(label)
        except Exception as exc:                     # noqa: BLE001
            self._show_error(str(exc))

    def _run(self, worker: Worker, what: str) -> None:
        if self._worker is not None and self._worker.running:
            return
        self._worker = worker
        worker.message.connect(lambda text: log.info("%s", text))
        worker.finished.connect(lambda _r: self._worker_done())
        worker.failed.connect(self._worker_failed)
        log.info("%s", what)
        worker.start()
        self.refresh()

    def _worker_done(self) -> None:
        self._worker = None
        self.refresh()

    def _worker_failed(self, reason: str) -> None:
        self._worker = None
        self._show_error(reason)
        self.refresh()

    # -- display -------------------------------------------------------------

    def _on_profile_changed(self, profile) -> None:
        self.reload_profile_list()
        self._rebuild_camera_rows()
        self.refresh()

    def _rebuild_camera_rows(self) -> None:
        layout = self._cameras_box.layout()
        for row in self._camera_rows:
            layout.removeWidget(row)
            row.deleteLater()
        self._camera_rows = []

        profile = self.session.profile
        if profile is None or not profile.cameras:
            self.no_cameras.setText(
                "No profile loaded." if profile is None else
                f"Profile {profile.name!r} lists no cameras.")
            self.no_cameras.show()
            return

        self.no_cameras.hide()
        for label in sorted(profile.cameras):
            row = _CameraRow(self, label, profile.cameras[label], self)
            layout.addWidget(row)
            self._camera_rows.append(row)

    def refresh(self) -> None:
        busy = self._worker is not None and self._worker.running
        session = self.session
        profile = session.profile

        if profile is None:
            self.profile_path.setText("No profile loaded.")
            self.calibration.setText("")
        else:
            temporary = ("  (temporary: created by --mock, safe to delete)"
                         if session.uses_mock_profile() else "")
            self.profile_path.setText(f"{profile.path}{temporary}")
            self.calibration.setText(_calibration_state(profile))

        self.load_button.setEnabled(not busy and self.chooser.count() > 0)
        self.chooser.setEnabled(not busy)

        connected = session.robot is not None
        self.robot_state.setText(f"State: {session.robot_state}")
        self.connect_button.setEnabled(not connected and not busy)
        self.disconnect_button.setEnabled(connected and not busy)

        for row in self._camera_rows:
            row.refresh(busy)

    def _show_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.show()

    def _clear_error(self) -> None:
        self.error.clear()
        self.error.hide()
