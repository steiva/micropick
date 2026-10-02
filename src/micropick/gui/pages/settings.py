"""Settings of this computer's application: the robot's address, and where
what the application produces is put.

Opened from the gear in the status bar, not from a tab: the tabs are the
work, and this is set once and seldom looked at again. The values are
`config.app_settings`, one file beside `profiles/`, not a profile's - the
same robot and calibration are reached at 169.254.x.x over a cable and at
another address over Wi-Fi.

The robot's address is typed as a person would - an IP, a name, host:port,
even a whole URL - and Test connection asks the robot at it who it is
(`OpentronsAPI.health`) before anything is saved, so a wrong address is
found here and not as a Connect that hangs. It cannot be changed while the
robot is connected; a new one is used at the next Connect.

The folders: Outputs (plate plans, liquid programs, presets, clips), Logs
(run logs) and Images (pictures saved from a camera view). Empty is the
default under the root. A folder is created when it is saved, so one that
cannot be made is refused here rather than at the first file.

Pickup clips: the lower camera's mode and crop while a picking run records
(`gui.pages.picking`, "Pickup clips"). The modes offered are the ones the
loaded profile lists for that camera.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QVBoxLayout, QWidget)

from ... import paths
from ...config.app_settings import AppSettings, settings_path
from ..session import Session
from ..theme import SPACING
from ..theme.factory import (card, combo_box, double_spin_box, heading,
                             primary_button, secondary_button, spin_box)
from ..workers import Worker

__all__ = ["SettingsPage", "TITLE", "parse_address"]

TITLE = "Settings"

log = logging.getLogger(__name__)

TEST_TIMEOUT_S = 5.0

# (setting, label, what goes there, the default's folder under the root)
FOLDERS = (("outputs_dir", "Outputs",
            "plate plans, liquid handling programs, presets, clips",
            "outputs"),
           ("logs_dir", "Logs", "run logs", "logs"),
           ("images_dir", "Images", "pictures saved from a camera view",
            "outputs/images"))


def parse_address(text: str) -> tuple[str, int | None]:
    """(host, port or None) from what was typed, through the robot wrapper's
    own parser so the page and Connect read an address alike. Raises
    ValueError for one that is not an address."""
    try:
        from opentrons_api.ot2_api import parse_address as wrapper_parse
    except ImportError:                          # the wrapper is not there
        text = text.strip()
        if not text or " " in text:
            raise ValueError(f"not a robot address: {text!r}") from None
        return text, None
    host, port = wrapper_parse(text)
    has_port = ":" in text.split("//")[-1]
    return host, port if has_port else None


def _label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class SettingsPage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._worker: Worker | None = None

        column = QVBoxLayout()
        column.setSpacing(SPACING)
        column.addWidget(self._robot_card())
        column.addWidget(self._folders_card())
        column.addWidget(self._clips_card())
        row = QHBoxLayout()
        self.save_button = primary_button("Save", self)
        self.save_button.clicked.connect(self._save)
        self.revert_button = secondary_button("Revert", self)
        self.revert_button.setToolTip("Back to what is saved.")
        self.revert_button.clicked.connect(self._show)
        row.addWidget(self.save_button)
        row.addWidget(self.revert_button)
        row.addStretch(1)
        column.addLayout(row)
        self.save_state = _label()
        column.addWidget(self.save_state)
        column.addStretch(1)

        # A readable column, not the window's width: these are a few fields.
        outer = QHBoxLayout(self)
        outer.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2,
                                 SPACING * 2)
        holder = QWidget(self)
        holder.setLayout(column)
        holder.setMaximumWidth(720)
        outer.addWidget(holder, 1)
        outer.addStretch(0)

        session.robot_state_changed.connect(lambda _s: self._refresh())
        session.settings_changed.connect(lambda _s: self._show())
        session.profile_changed.connect(lambda _p: self._fill_clip_modes())
        self._show()

    # -- construction --------------------------------------------------------

    def _robot_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Robot", 2))
        box.layout().addWidget(_label(
            "Where the robot is on the network. Over the direct cable this is "
            "usually 169.254.x.x; over Wi-Fi, the address the Opentrons app "
            "shows for the robot."))
        grid = QGridLayout()
        grid.addWidget(QLabel("Address"), 0, 0)
        self.host = QLineEdit(self)
        self.host.setPlaceholderText("169.254.241.245 (the default)")
        self.host.setToolTip("An IP address or a name, optionally with "
                             ":port. Empty is the default.")
        self.host.textEdited.connect(lambda _t: self._edited())
        grid.addWidget(self.host, 0, 1)
        grid.addWidget(QLabel("Port"), 1, 0)
        self.port = spin_box(self)
        self.port.setRange(1, 65535)
        self.port.setToolTip("The robot server's port; 31950 unless it was "
                             "changed on the robot.")
        self.port.valueChanged.connect(lambda _v: self._edited())
        grid.addWidget(self.port, 1, 1, Qt.AlignmentFlag.AlignLeft)
        box.layout().addLayout(grid)
        row = QHBoxLayout()
        self.test_button = secondary_button("Test connection", self)
        self.test_button.setToolTip("Ask the robot at this address who it is. "
                                    "Nothing is saved or moved.")
        self.test_button.clicked.connect(self._test)
        row.addWidget(self.test_button)
        row.addStretch(1)
        box.layout().addLayout(row)
        self.robot_state = _label()
        box.layout().addWidget(self.robot_state)
        return box

    def _folders_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Folders", 2))
        box.layout().addWidget(_label(
            "Where what the application produces is put. Empty is the default "
            "folder under the data root; a new folder is used from the next "
            "file on."))
        grid = QGridLayout()
        self.folders: dict[str, QLineEdit] = {}
        for row, (key, title, what, default) in enumerate(FOLDERS):
            name = QLabel(title)
            name.setToolTip(what)
            grid.addWidget(name, 2 * row, 0)
            line = QLineEdit(self)
            line.setPlaceholderText(
                "images, inside the Outputs folder" if key == "images_dir"
                else str(paths.root() / default))
            line.setToolTip(what)
            line.textEdited.connect(lambda _t: self._edited())
            grid.addWidget(line, 2 * row, 1)
            browse = secondary_button("Browse…", self)
            browse.clicked.connect(lambda _c=False, k=key: self._browse(k))
            grid.addWidget(browse, 2 * row, 2)
            default_button = secondary_button("Default", self)
            default_button.setToolTip("Back to the default folder.")
            default_button.clicked.connect(
                lambda _c=False, k=key: self._default_folder(k))
            grid.addWidget(default_button, 2 * row, 3)
            note = QLabel(what)
            note.setWordWrap(True)
            grid.addWidget(note, 2 * row + 1, 1, 1, 3)
            self.folders[key] = line
        grid.setColumnStretch(1, 1)
        box.layout().addLayout(grid)
        return box

    def _clips_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Pickup clips", 2))
        box.layout().addWidget(_label(
            "The lower camera's mode while a picking run records pickup "
            "clips, and how much of the middle of the frame is kept. A smaller "
            "mode records more frames a second. The pipette calibration "
            "always opens the camera in the profile's own mode."))
        grid = QGridLayout()
        grid.addWidget(QLabel("Resolution"), 0, 0)
        self.clip_resolution = combo_box(self)
        self.clip_resolution.setToolTip(
            "The modes the profile lists for the lower camera.")
        self.clip_resolution.currentIndexChanged.connect(
            lambda _i: self._edited())
        grid.addWidget(self.clip_resolution, 0, 1, Qt.AlignmentFlag.AlignLeft)
        grid.addWidget(QLabel("Crop"), 1, 0)
        self.clip_crop = double_spin_box(self)
        self.clip_crop.setRange(0.1, 1.0)
        self.clip_crop.setSingleStep(0.05)
        self.clip_crop.setDecimals(2)
        self.clip_crop.setToolTip("The side of the centred square kept, as a "
                                  "fraction of the frame's height; 1 is the "
                                  "whole frame.")
        self.clip_crop.valueChanged.connect(lambda _v: self._edited())
        grid.addWidget(self.clip_crop, 1, 1, Qt.AlignmentFlag.AlignLeft)
        grid.setColumnStretch(2, 1)
        box.layout().addLayout(grid)
        return box

    def _clip_modes(self, saved) -> list[tuple[int, int]]:
        """The lower camera's modes from the loaded profile, and the saved
        one whatever the profile says."""
        modes = []
        session = self.session
        profile, label = session.profile, session.lower_camera_label
        spec = profile.cameras.get(label) if profile and label else None
        if spec is not None:
            modes = [tuple(int(v) for v in mode) for mode in spec.resolutions]
        saved = tuple(int(v) for v in saved)
        if saved not in modes:
            modes.append(saved)
        return sorted(set(modes))

    def _fill_clip_modes(self, selected=None) -> None:
        """The modes into the chooser, `selected` (else the one chosen now)
        chosen. Again on a profile change: the modes are the profile's."""
        if selected is None:
            selected = (self.clip_resolution.currentData()
                        or self.session.settings.clip_resolution)
        selected = tuple(int(v) for v in selected)
        modes = self._clip_modes(selected)
        self.clip_resolution.blockSignals(True)
        self.clip_resolution.clear()
        for mode in modes:
            self.clip_resolution.addItem(f"{mode[0]} x {mode[1]}", mode)
        self.clip_resolution.setCurrentIndex(modes.index(selected))
        self.clip_resolution.blockSignals(False)

    # -- the values ----------------------------------------------------------

    def _show(self) -> None:
        """The saved settings into the fields."""
        settings = self.session.settings
        self.host.setText(settings.robot_host or "")
        self.port.blockSignals(True)
        self.port.setValue(settings.robot_port)
        self.port.blockSignals(False)
        for key, line in self.folders.items():
            line.setText(getattr(settings, key) or "")
        self._fill_clip_modes(settings.clip_resolution)
        self.clip_crop.blockSignals(True)
        self.clip_crop.setValue(settings.clip_crop)
        self.clip_crop.blockSignals(False)
        self.save_state.setText(
            self.session.settings_note
            or (f"Saved in {settings_path()}." if settings_path().is_file()
                else f"The defaults: nothing is saved yet. Save writes "
                     f"{settings_path()}."))
        self._refresh()

    def _typed(self) -> AppSettings:
        """The fields as settings; raises ValueError with what is wrong."""
        host = self.host.text().strip() or None
        port = int(self.port.value())
        if host is not None:
            _host, typed_port = parse_address(host)
            if typed_port is not None:
                port = typed_port
        folders = {key: (line.text().strip() or None)
                   for key, line in self.folders.items()}
        return AppSettings(robot_host=host, robot_port=port, **folders,
                           clip_resolution=self.clip_resolution.currentData(),
                           clip_crop=round(self.clip_crop.value(), 2))

    def _edited(self) -> None:
        self.save_state.setText("Changed: Save keeps it.")
        self._refresh()

    def _save(self) -> None:
        try:
            settings = self._typed()
        except ValueError as exc:
            self.save_state.setText(f"Not saved: {exc}")
            return
        for key, title, _what, _default in FOLDERS:
            folder = getattr(settings, key)
            if folder:
                try:
                    Path(folder).expanduser().mkdir(parents=True,
                                                    exist_ok=True)
                except OSError as exc:
                    self.save_state.setText(f"Not saved: the {title} folder "
                                            f"cannot be made: {exc}")
                    return
        self.session.set_settings(settings)
        self.save_state.setText(
            f"Saved in {settings_path()}."
            + (" The robot's address is used at the next Connect."
               if self.session.robot is None else ""))

    def _browse(self, key: str) -> None:
        line = self.folders[key]
        start = line.text().strip() or line.placeholderText()
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder",
                                                  start)
        if folder:
            line.setText(folder)
            self._edited()

    def _default_folder(self, key: str) -> None:
        self.folders[key].clear()
        self._edited()

    # -- testing the address -------------------------------------------------

    def _test(self) -> None:
        if self._worker is not None and self._worker.running:
            return
        if self.session.mock:
            self.robot_state.setText("Running with --mock: there is no robot "
                                     "on the network to ask.")
            return
        try:
            settings = self._typed()
        except ValueError as exc:
            self.robot_state.setText(str(exc))
            return
        host, port = settings.robot_host, settings.robot_port

        def job():
            from opentrons_api.ot2_api import OpentronsAPI
            api = OpentronsAPI(host=host, port=port)
            return api.BASE_URL, api.health(timeout=TEST_TIMEOUT_S)

        self.robot_state.setText("asking the robot…")
        worker = Worker(job, what="asking the robot")
        self._worker = worker
        worker.finished.connect(self._tested)
        worker.failed.connect(self._test_failed)
        worker.start()
        self._refresh()

    def _tested(self, payload) -> None:
        self._worker = None
        url, health = payload
        name = health.get("name", "a robot")
        version = health.get("api_version", "?")
        self.robot_state.setText(f"{name} answers at {url} (robot server "
                                 f"{version}).")
        log.info("robot %s answers at %s", name, url)
        self._refresh()

    def _test_failed(self, reason: str) -> None:
        self._worker = None
        short = reason.splitlines()[0][:300]
        self.robot_state.setText(
            f"No answer: {short}\nCheck the address, that the robot is on and "
            f"finished starting up, and the cable or the network.")
        log.error("robot test: %s", reason)
        self._refresh()

    # -- display -------------------------------------------------------------

    def _refresh(self) -> None:
        connected = self.session.has_connection
        testing = self._worker is not None and self._worker.running
        for widget in (self.host, self.port):
            widget.setEnabled(not connected)
        self.test_button.setEnabled(not testing)
        if connected and not testing:
            self.robot_state.setText(
                f"Connected to {self.session.robot_address}. Disconnect on "
                f"the Profile page to change the address.")
        elif not self.robot_state.text():
            self.robot_state.setText(f"Connect goes to "
                                     f"{self.session.robot_address}.")
