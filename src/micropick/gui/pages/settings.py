"""Settings of this computer's application: the robot's address, and where
what the application produces is put.

Opened from the gear at the end of the tab row, not from a tab: the tabs
are the work, and this is set once and seldom looked at again. The values are
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

Beside them, in a column of its own, what belongs to the loaded profile and
is set as seldom: which attached camera is the upper (overview) and which
the lower (underview) one, which machine learning models it uses, and what
its calibration holds. These are written into the profile the moment they
are chosen, as they always were, so Save and Revert are the left column's
only. Choosing a camera that is the other role's swaps the two; the
cameras attached to this computer are listed with their modes, looked up
again whenever the page is shown.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QVBoxLayout, QWidget)

from ... import paths
from ...config.app_settings import AppSettings, settings_path
from ...hardware import devices as camera_devices
from ..detector import STANDIN, DetectorService
from ..session import Session
from ..theme import SPACING
from ..theme.factory import (card, combo_box, double_spin_box, heading,
                             muted_label, primary_button, scroll_column,
                             secondary_button, spin_box)
from ..workers import Worker, any_running

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

# Each column's width; two of them side by side fit a 1400 px window.
COLUMN_WIDTH = 600

# The two cameras, as the operator knows them: (role, words on screen).
ROLES = (("upper", "Upper camera (overview)"),
         ("lower", "Lower camera (underview)"))


def calibration_lines(profile) -> list[str]:
    """What the profile's calibration holds, a line per part."""
    pixel_map = profile.pixel_map
    if pixel_map is None:
        lines = ["Pixel map: none - run the camera sweep."]
    else:
        parts = [f"degree {pixel_map.degree}"]
        if pixel_map.n_poses is not None:
            parts.append(f"{pixel_map.n_poses} poses")
        if pixel_map.holdout_mean_um is not None:
            parts.append(f"held-out {pixel_map.holdout_mean_um:.1f} µm mean")
        if pixel_map.fitted_at is not None:
            parts.append(f"fitted {pixel_map.fitted_at:%Y-%m-%d}")
        lines = ["Pixel map: " + ", ".join(parts) + "."]
    offset = profile.calibration.pipette_offset
    if offset is None:
        lines.append("Pipette offset: none - calibrate the pipette.")
    else:
        when = (f", measured {offset.measured_at:%Y-%m-%d}"
                if offset.measured_at is not None else "")
        lines.append(f"Pipette offset: dx {offset.dx:.3f} mm, dy "
                     f"{offset.dy:.3f} mm ({offset.method}{when}).")
    return lines


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
        self._devices: list[camera_devices.Device] = []
        self._modes: dict[str, list[camera_devices.Mode]] = {}
        self._devices_problem = ""

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
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

        profile_column = QVBoxLayout()
        profile_column.setContentsMargins(0, 0, 0, 0)
        profile_column.setSpacing(SPACING)
        self.profile_note = _label()
        profile_column.addWidget(self.profile_note)
        profile_column.addWidget(self._cameras_card())
        profile_column.addWidget(self._models_card())
        profile_column.addWidget(self._calibration_card())
        profile_column.addStretch(1)

        # Readable columns, not the window's width: these are a few fields.
        # The computer's on the left, the profile's on the right, the pair
        # centred.
        outer = QHBoxLayout(self)
        outer.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2,
                                 SPACING * 2)
        outer.setSpacing(SPACING * 2)
        outer.addStretch(1)
        for layout in (column, profile_column):
            holder = QWidget(self)
            holder.setLayout(layout)
            outer.addWidget(scroll_column(holder, COLUMN_WIDTH))
        outer.addStretch(1)

        session.robot_state_changed.connect(lambda _s: self._refresh())
        session.settings_changed.connect(lambda _s: self._show())
        session.profile_changed.connect(self._on_profile_changed)
        session.camera_opened.connect(lambda _l: self._show_cameras())
        session.camera_closed.connect(lambda _l: self._show_cameras())
        self._show()
        self._on_profile_changed(session.profile)

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

    def _cameras_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Cameras", 2))
        box.layout().addWidget(_label(
            "Which of the cameras attached to this computer looks down on "
            "the deck and which up from under the dish. Choosing the other "
            "camera's device swaps the two."))
        grid = QGridLayout()
        self.role_combo: dict[str, object] = {}
        self.role_info: dict[str, QLabel] = {}
        for i, (role, title) in enumerate(ROLES):
            grid.addWidget(QLabel(title), 2 * i, 0)
            combo = combo_box(self)
            # activated, not currentIndexChanged: only the operator's choice
            # writes the profile, never the page filling the list.
            combo.activated.connect(lambda _i, r=role: self._device_chosen(r))
            grid.addWidget(combo, 2 * i, 1)
            info = muted_label()
            grid.addWidget(info, 2 * i + 1, 1)
            self.role_combo[role] = combo
            self.role_info[role] = info
        grid.setColumnStretch(1, 1)
        box.layout().addLayout(grid)
        self.cameras_state = _label()
        box.layout().addWidget(self.cameras_state)

        box.layout().addWidget(heading("Attached to this computer", 3))
        self.attached = QVBoxLayout()
        self.attached.setSpacing(0)
        box.layout().addLayout(self.attached)
        row = QHBoxLayout()
        self.rescan_button = secondary_button("Look again", self)
        self.rescan_button.setToolTip("List the attached cameras again, after "
                                      "plugging one in or out.")
        self.rescan_button.clicked.connect(self._scan_devices)
        row.addWidget(self.rescan_button)
        row.addStretch(1)
        box.layout().addLayout(row)
        return box

    def _models_card(self) -> QWidget:
        """Which weights this installation uses, chosen once.

        Here rather than on the pages that run them: an installation has one
        cuboid detector and one tip detector, the choice is a property of
        the bench and not of a run, and a page that offered it would be
        asking the same question every time it was opened. The pages load
        whatever is named here when they need it.
        """
        box = card(self)
        box.layout().addWidget(heading("Machine learning models", 2))
        self.cuboid_model = combo_box(self)
        self.cuboid_model.currentTextChanged.connect(self._cuboid_model_chosen)
        self.tip_model = combo_box(self)
        self.tip_model.currentTextChanged.connect(self._tip_model_chosen)
        grid = QGridLayout()
        grid.addWidget(QLabel("Model for cuboids"), 0, 0)
        grid.addWidget(self.cuboid_model, 0, 1)
        grid.addWidget(QLabel("Model for tip detection"), 1, 0)
        grid.addWidget(self.tip_model, 1, 1)
        grid.setColumnStretch(1, 1)
        box.layout().addLayout(grid)
        self.models_note = _label()
        box.layout().addWidget(self.models_note)
        return box

    def _calibration_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Calibration", 2))
        self.calibration = _label()
        box.layout().addWidget(self.calibration)
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

    # -- the profile's -------------------------------------------------------

    def showEvent(self, event) -> None:
        """The attached cameras are looked up each time the page is shown:
        a camera plugged in since is the reason to come here."""
        super().showEvent(event)
        self._scan_devices()

    def _on_profile_changed(self, profile) -> None:
        self._fill_clip_modes()
        self.profile_note.setText(
            "Load a profile on the Profile page to choose its cameras and "
            "models." if profile is None else
            f"The profile <b>{profile.name}</b>: what is chosen here is "
            f"saved in it at once.")
        self._show_cameras()
        self._reload_models()
        self.calibration.setText(
            "\n".join(calibration_lines(profile)) if profile is not None
            else "No profile loaded.")

    def _role_label(self, role: str) -> str | None:
        """The profile's camera that plays `role`, by its label."""
        session = self.session
        return (session.upper_camera_label if role == "upper"
                else session.lower_camera_label)

    def _attached_as(self, label: str) -> str | None:
        """The attached device the profile's camera `label` resolves to, as
        it would at open, or None when it resolves to none or to several."""
        spec = self.session.profile.cameras[label]
        try:
            return camera_devices.find_device(spec.device_name,
                                              devices=self._devices).name
        except camera_devices.DeviceError:
            return None

    def _scan_devices(self) -> None:
        try:
            self._devices = camera_devices.list_devices()
            self._devices_problem = ""
        except camera_devices.DeviceError as exc:
            self._devices, self._devices_problem = [], str(exc)
        self._modes = {d.name: camera_devices.device_modes(d.index)
                       for d in self._devices}
        self._show_cameras()

    def _show_cameras(self) -> None:
        session, profile = self.session, self.session.profile
        roles = {role: self._role_label(role) for role, _title in ROLES}

        for role, _title in ROLES:
            combo, info = self.role_combo[role], self.role_info[role]
            label = roles[role]
            combo.clear()
            if profile is None:
                combo.setEnabled(False)
                info.setText("No profile loaded.")
                continue
            if label is None:
                # A profile started empty: the camera is made when one is
                # chosen (`Session.add_camera`).
                combo.addItem("Choose a camera…", None)
                for device in self._devices:
                    combo.addItem(device.name, device.name)
                combo.setCurrentIndex(0)
                combo.setEnabled(bool(self._devices))
                info.setText("Not set in this profile yet: choose which "
                             "attached camera it is.")
                continue
            spec = profile.cameras[label]
            current = self._attached_as(label)
            for device in self._devices:
                combo.addItem(device.name, device.name)
            if current is None:
                combo.insertItem(0, f"{spec.device_name} (not attached)",
                                 spec.device_name)
                combo.setCurrentIndex(0)
            else:
                combo.setCurrentIndex(combo.findData(current))
            combo.setEnabled(bool(self._devices))
            info.setText(self._role_text(label, spec, current))

        self._fill_attached(roles)

    def _role_text(self, label: str, spec, device: str | None) -> str:
        """What the profile opens this camera as, and how it is now."""
        width, height = spec.default_resolution
        parts = [f"Opens at {width}×{height}"
                 + (f" {spec.fourcc}" if spec.fourcc else "")]
        if spec.crop != 1.0:
            parts.append(f"view crop {spec.crop:g}")
        focus = spec.controls.get("focus")
        if isinstance(focus, (int, float)):
            parts.append(f"focus {focus:g}")
        text = ", ".join(parts) + "."
        sizes = {(m.width, m.height) for m in self._modes.get(device, [])}
        if sizes and (width, height) not in sizes:
            text += f" This camera does not offer {width}×{height}."
        camera = self.session.camera(label)
        if camera is not None:
            text += " Open now at {}×{}.".format(*camera.resolution)
        return text

    def _fill_attached(self, roles: dict[str, str | None]) -> None:
        while self.attached.count():
            widget = self.attached.takeAt(0).widget()
            if widget is not None:
                # Hidden at once: deleteLater waits for the event loop, and
                # until then the label would be drawn where it was.
                widget.hide()
                widget.deleteLater()
        if not self._devices:
            self.attached.addWidget(_label(
                self._devices_problem or "No camera is attached."))
            return
        profile = self.session.profile
        used = {}
        if profile is not None:
            for role, title in ROLES:
                if roles[role] is not None:
                    name = self._attached_as(roles[role])
                    if name is not None:
                        used[name] = title.split(" (")[0].lower()
        names = [d.name for d in self._devices]
        for i, device in enumerate(self._devices):
            if i:
                self.attached.addSpacing(SPACING)
            modes = self._modes.get(device.name, [])
            text = f"<b>{device.name}</b>"
            if device.name in used:
                text += f" - the {used[device.name]}"
            if names.count(device.name) > 1:
                text += (" - two cameras carry this name, and neither can be "
                         "told apart from the other")
            name = _label(text)
            self.attached.addWidget(name)
            if modes:
                biggest = modes[0]
                fastest = max(m.fps for m in modes)
                detail = muted_label(
                    f"{len({(m.width, m.height) for m in modes})} sizes, up to "
                    f"{biggest.width}×{biggest.height}, up to {fastest:.0f} "
                    f"frames a second (hover for the list)")
                # On both lines: the muted one is disabled, and a disabled
                # widget's tooltip is not to be relied on.
                for widget in (name, detail):
                    widget.setToolTip("\n".join(str(m) for m in modes))
                self.attached.addWidget(detail)

    def _device_chosen(self, role: str) -> None:
        profile = self.session.profile
        label = self._role_label(role)
        name = self.role_combo[role].currentData()
        if profile is None or name is None:
            return
        if any_running():
            self.cameras_state.setText("Not now: something is running. "
                                       "Choose again when it has finished.")
            self._show_cameras()
            return
        title = dict(ROLES)[role]
        if label is None:
            self._add_camera(role, name)
            return
        was = self._attached_as(label) or profile.cameras[label].device_name
        assignment = {label: name}
        other_role = "lower" if role == "upper" else "upper"
        other = self._role_label(other_role)
        swapped = False
        if other is not None and other != label and \
                self._attached_as(other) == name:
            assignment[other] = was
            swapped = True
        try:
            self.session.assign_camera_devices(assignment)
        except Exception as exc:                     # noqa: BLE001
            self.cameras_state.setText(f"Not changed: {exc}")
            self._show_cameras()
            return
        self.cameras_state.setText(
            f"{title}: {name}."
            + (f" The two were swapped: {dict(ROLES)[other_role].lower()} "
               f"is now {was}." if swapped else ""))

    def _add_camera(self, role: str, name: str) -> None:
        """The profile's first camera for `role`, on the device `name`."""
        title = dict(ROLES)[role]
        other_role = "lower" if role == "upper" else "upper"
        other = self._role_label(other_role)
        if other is not None and self._attached_as(other) == name:
            self.cameras_state.setText(
                f"{name} is already the {dict(ROLES)[other_role].lower()}. "
                f"Choose another camera there first.")
            self._show_cameras()
            return
        sizes = [(m.width, m.height) for m in self._modes.get(name, [])]
        try:
            label = self.session.add_camera(role, name, sizes)
        except Exception as exc:                     # noqa: BLE001
            self.cameras_state.setText(f"Not changed: {exc}")
            self._show_cameras()
            return
        spec = self.session.profile.cameras[label]
        self.cameras_state.setText(
            f"{title}: {name}, added to the profile; it opens at "
            "{}×{}.".format(*spec.default_resolution))

    def _reload_models(self) -> None:
        """What is in ml_models/, plus the stand-in for the cuboid detector.

        The tip detector has no stand-in here: `gui.tip_detector` makes one
        for --mock, and offering it on the bench would be offering to
        calibrate a pipette against invented crosshairs.
        """
        found = DetectorService.available_weights()
        profile = self.session.profile
        cuboid = profile.picking.model_file if profile else ""
        tip = profile.calibration.tip_target.model_file if profile else ""

        for widget, current, extra in ((self.cuboid_model, cuboid, [STANDIN]),
                                       (self.tip_model, tip, [])):
            names = [*found, *extra]
            # A name in the profile that is not in ml_models/ is shown
            # anyway, and marked: a profile that points at weights this
            # machine does not have is a fact worth seeing, not a silently
            # reset setting.
            if current and current not in names:
                names.insert(0, current)
            widget.blockSignals(True)
            widget.clear()
            widget.addItems(names)
            index = widget.findText(current)
            widget.setCurrentIndex(index if index >= 0 else -1)
            widget.setEnabled(profile is not None)
            widget.blockSignals(False)

        missing = [name for name in (cuboid, tip)
                   if name and name != STANDIN and name not in found]
        self.models_note.setText(
            "No profile loaded." if profile is None else
            "The pages that need a model load whichever is named here."
            if not missing else
            f"Not in {paths.ml_models_dir()}: {', '.join(missing)}. "
            f"Weights are not tracked in the repository; copy the file in, "
            f"or choose another.")

    def _cuboid_model_chosen(self, name: str) -> None:
        profile = self.session.profile
        if profile is None or not name or profile.picking.model_file == name:
            return
        profile.picking.model_file = name
        profile.save_picking()
        log.info("cuboid detector for %r: %s", profile.name, name)
        self.session.profile_changed.emit(profile)

    def _tip_model_chosen(self, name: str) -> None:
        profile = self.session.profile
        if profile is None or not name:
            return
        target = profile.calibration.tip_target
        if target.model_file == name:
            return
        target.model_file = name
        # backup=False: choosing a model is not a measurement, and archiving
        # the calibration on every combo change would bury the sweeps that
        # are worth keeping.
        profile.save_calibration(backup=False)
        log.info("tip detector for %r: %s", profile.name, name)
        self.session.profile_changed.emit(profile)

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
                f"Connected to {self.session.robot_address}. Press Disconnect "
                f"robot session on the Profile page to change the address.")
        elif not self.robot_state.text():
            self.robot_state.setText(f"Connect goes to "
                                     f"{self.session.robot_address}.")
