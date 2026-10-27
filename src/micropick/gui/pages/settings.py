"""Settings of this computer's application: the robot's address, and where
what the application produces is put.

A sheet (`SettingsSheet`) opened from the gear at the end of the top bar,
over whatever screen is shown, outside the tasks: this is set once and
seldom looked at again. Three sections: General (all below), Advanced (the
robot session by hand, `AdvancedPanel`) and the Log. The values are
`config.app_settings`, one file beside `profiles/`, not a profile's - the
same robot and calibration are reached at 169.254.x.x over a cable and at
another address over Wi-Fi.

The robot's address is typed as a person would - an IP, a name, host:port,
even a whole URL - and Test connection asks the robot at it who it is
(`OpentronsAPI.health`) before anything is saved, so a wrong address is
found here and not as a robot that is never found. It cannot be changed
while the robot is connected; saved while it is not, the connection
monitor looks for it there from its next look.

The folders: Outputs (plate plans, liquid programs, presets, clips), Logs
(run logs) and Images (pictures saved from a camera view). Empty is the
default under the root. A folder is created when it is saved, so one that
cannot be made is refused here rather than at the first file.

Pickup clips: the lower camera's mode and crop while a picking run records
(`gui.pages.picking`, "Pickup clips"). The modes offered are the ones the
loaded profile lists for that camera.

Tip calibration: how it measures, and the starting offset (dx, dy) it
looks for the tip at while the profile has no measured one.

Keys: the keys that move the robot and Manual control's aspirate and
dispense (`gui.keys`), one per action, for a keyboard without PgUp and
PgDn. Save refuses a key used twice, or Esc, which is Stop.

Inference: Auto, CPU or GPU for the machine learning models
(`core.vision.yolo`), with the GPUs found and what each model loaded so far
runs on. A change unloads the cuboid detector, so the next page that needs
it loads it on the new device.

Beside them, in a column of its own, what belongs to the loaded profile and
is set as seldom: which machine learning models it uses, and what its
calibration holds. These are written into the profile the moment they are
chosen, as they always were, so Save and Revert are the left column's
only. Which attached camera is the upper and which the lower is chosen in
Robot setup's camera sheets (`pages.camera_sheet`).
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QGridLayout,
                               QHBoxLayout, QKeySequenceEdit, QLabel,
                               QLineEdit, QStackedWidget, QVBoxLayout,
                               QWidget)

from ... import paths
from ...config.app_settings import AppSettings, settings_path
from ...core.vision import yolo
from .. import keys
from ..detector import STANDIN, DetectorService
from ..marker_watch import DICTIONARIES
from ..session import Session
from ..theme import SPACING
from ..theme.factory import (card, combo_box, double_spin_box, heading,
                             muted_label, primary_button, scroll_column,
                             secondary_button, spin_box)
from ..widgets.controls import Segmented
from ..widgets.overlays import SheetWindow
from ..workers import Worker
from .log import LogPage

__all__ = ["AdvancedPanel", "SettingsPage", "SettingsSheet", "TITLE",
           "parse_address"]

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

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        column.addWidget(self._robot_card())
        column.addWidget(self._folders_card())
        column.addWidget(self._clips_card())
        column.addWidget(self._sweep_card())
        column.addWidget(self._tip_card())
        column.addWidget(self._inference_card())
        column.addWidget(self._keys_card())
        column.addStretch(1)

        profile_column = QVBoxLayout()
        profile_column.setContentsMargins(0, 0, 0, 0)
        profile_column.setSpacing(SPACING)
        self.profile_note = _label()
        profile_column.addWidget(self.profile_note)
        profile_column.addWidget(self._models_card())
        profile_column.addWidget(self._calibration_card())
        profile_column.addStretch(1)

        # Readable columns, not the window's width: these are a few fields.
        # The computer's on the left, the profile's on the right, the pair
        # centered.
        columns = QHBoxLayout()
        columns.setSpacing(SPACING * 2)
        columns.addStretch(1)
        for layout in (column, profile_column):
            holder = QWidget(self)
            holder.setLayout(layout)
            columns.addWidget(scroll_column(holder, COLUMN_WIDTH))
        columns.addStretch(1)

        # Save and Revert at the bottom right, outside the columns: always
        # in view however far the left column is scrolled, which is where a
        # change was made. What was saved, or why not, beside them.
        bar = QHBoxLayout()
        self.save_state = _label()
        self.save_state.setAlignment(Qt.AlignmentFlag.AlignRight
                                     | Qt.AlignmentFlag.AlignVCenter)
        bar.addWidget(self.save_state, 1)
        self.revert_button = secondary_button("Revert", self)
        self.revert_button.setToolTip("Discard unsaved changes.")
        self.revert_button.clicked.connect(self.revert)
        self.save_button = primary_button("Save", self)
        self.save_button.setToolTip("Save this computer's settings (left "
                                    "column). Profile settings save "
                                    "immediately.")
        self.save_button.clicked.connect(self._save)
        bar.addWidget(self.revert_button)
        bar.addWidget(self.save_button)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2,
                                 SPACING * 2)
        outer.setSpacing(SPACING)
        outer.addLayout(columns, 1)
        outer.addLayout(bar)

        session.robot_state_changed.connect(self._refresh)
        session.settings_changed.connect(lambda _s: self._show())
        session.profile_changed.connect(self._on_profile_changed)
        self._show()
        self._on_profile_changed(session.profile)

    # -- construction --------------------------------------------------------

    def _robot_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Robot", 2))
        box.layout().addWidget(_label(
            "Robot network address: 169.254.x.x over the direct cable, or "
            "the address shown in the Opentrons app."))
        grid = QGridLayout()
        grid.addWidget(QLabel("Address"), 0, 0)
        self.host = QLineEdit(self)
        self.host.setPlaceholderText("169.254.241.245 (the default)")
        self.host.setToolTip("IP address or host name, optionally :port. "
                             "Empty: default.")
        self.host.textEdited.connect(lambda _t: self._edited())
        grid.addWidget(self.host, 0, 1)
        grid.addWidget(QLabel("Port"), 1, 0)
        self.port = spin_box(self)
        self.port.setRange(1, 65535)
        self.port.setToolTip("Robot server port (default 31950).")
        self.port.valueChanged.connect(lambda _v: self._edited())
        grid.addWidget(self.port, 1, 1, Qt.AlignmentFlag.AlignLeft)
        box.layout().addLayout(grid)
        row = QHBoxLayout()
        self.test_button = secondary_button("Test connection", self)
        self.test_button.setToolTip("Query the robot at this address.")
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
            "Output locations. Empty: default folder under the data root."))
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
            default_button.setToolTip("Use the default folder.")
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

    def _sweep_card(self) -> QWidget:
        """The camera calibration sweep's parameters: the bench's marker,
        and how densely the sweep samples. Used by the Camera calibration
        page, which says which ones it will sweep with."""
        box = card(self)
        box.layout().addWidget(heading("Camera calibration sweep", 2))
        box.layout().addWidget(_label(
            "Calibration marker and sweep sampling."))
        self.marker_side = double_spin_box(self)
        self.marker_side.setRange(1.0, 60.0)
        self.marker_side.setDecimals(2)
        self.marker_side.setSingleStep(0.1)
        self.marker_side.setSuffix(" mm")
        self.dictionary = combo_box(self)
        self.dictionary.addItems(DICTIONARIES)
        self.grid_n = spin_box(self)
        self.grid_n.setRange(4, 15)
        self.degree = spin_box(self)
        # Below 3 is refused by fit_pixel_map rather than silently useless:
        # radial distortion is cubic in image coordinates, so a quadratic
        # reduces exactly to the affine fit it is meant to improve on.
        self.degree.setRange(3, 5)
        grid = QGridLayout()
        for row, (label, widget, hint) in enumerate((
                ("Marker side", self.marker_side,
                 "Printed marker size; checks the fitted scale."),
                ("Dictionary", self.dictionary,
                 "ArUco dictionary of the printed marker."),
                ("Grid", self.grid_n,
                 "Poses per axis."),
                ("Degree", self.degree,
                 "Polynomial degree of the fit (3 recommended)."))):
            grid.addWidget(QLabel(label), 2 * row, 0)
            grid.addWidget(widget, 2 * row, 1, Qt.AlignmentFlag.AlignLeft)
            grid.addWidget(muted_label(hint), 2 * row + 1, 1)
        grid.setColumnStretch(1, 1)
        box.layout().addLayout(grid)
        for widget in (self.marker_side, self.grid_n, self.degree):
            widget.valueChanged.connect(lambda _v: self._edited())
        self.dictionary.currentIndexChanged.connect(lambda _i: self._edited())
        row = QHBoxLayout()
        self.sweep_defaults_button = secondary_button("Reset to defaults",
                                                      self)
        defaults = AppSettings()
        self.sweep_defaults_button.setToolTip(
            f"Marker side {defaults.sweep_marker_side_mm:g} mm, "
            f"{defaults.sweep_dictionary}, grid {defaults.sweep_grid_n}, "
            f"degree {defaults.sweep_degree}.")
        self.sweep_defaults_button.clicked.connect(self._sweep_defaults)
        row.addWidget(self.sweep_defaults_button)
        row.addStretch(1)
        box.layout().addLayout(row)
        return box

    def _tip_card(self) -> QWidget:
        """How the tip calibration measures. Used by the Tip calibration
        page, which says which of these it will use."""
        box = card(self)
        box.layout().addWidget(heading("Tip calibration", 2))
        self.tip_type = QLineEdit(self)
        self.tip_type.setPlaceholderText("from the tip rack")
        self.tip_type.textEdited.connect(lambda _t: self._edited())
        self.tip_frames = spin_box(self)
        self.tip_frames.setRange(1, 30)
        self.tip_frames.valueChanged.connect(lambda _v: self._edited())
        # The starting offset, measured roughly with a ruler.
        offset = QWidget(self)
        offset.setObjectName("plain")
        offset_row = QHBoxLayout(offset)
        offset_row.setContentsMargins(0, 0, 0, 0)
        self.tip_dx = double_spin_box(self)
        self.tip_dy = double_spin_box(self)
        for name, spin in (("dx", self.tip_dx), ("dy", self.tip_dy)):
            spin.setRange(-300.0, 300.0)
            spin.setDecimals(3)
            spin.setSingleStep(0.1)
            spin.setSuffix(" mm")
            spin.valueChanged.connect(lambda _v: self._edited())
            offset_row.addWidget(QLabel(name))
            offset_row.addWidget(spin)
        offset_row.addStretch(1)
        grid = QGridLayout()
        for row, (label, widget, hint) in enumerate((
                ("Tip type", self.tip_type,
                 "Recorded with the offset. Empty: the tip rack's type."),
                ("Frames", self.tip_frames,
                 "Frames averaged per reading."),
                ("Start offset", offset,
                 "Approximate camera-to-tip offset, used while the profile "
                 "has no measured one."))):
            grid.addWidget(QLabel(label), 2 * row, 0)
            if widget in (self.tip_type, offset):  # the whole width
                grid.addWidget(widget, 2 * row, 1)
            else:
                grid.addWidget(widget, 2 * row, 1, Qt.AlignmentFlag.AlignLeft)
            grid.addWidget(muted_label(hint), 2 * row + 1, 1)
        grid.setColumnStretch(1, 1)
        box.layout().addLayout(grid)
        self.tip_verify = QCheckBox("Verify after the correction", self)
        self.tip_verify.setToolTip("Measure again at the corrected offset.")
        self.tip_touch_up = QCheckBox(
            "Manual adjustment at the end", self)
        self.tip_touch_up.setToolTip("Nudge the tip onto the crosshair "
                                     "before saving.")
        for box_ in (self.tip_verify, self.tip_touch_up):
            box_.toggled.connect(lambda _on: self._edited())
            box.layout().addWidget(box_)
        return box

    def _keys_card(self) -> QWidget:
        """The keys that drive the robot; see `gui.keys`."""
        box = card(self)
        box.layout().addWidget(heading("Keys", 2))
        box.layout().addWidget(_label(
            "Keys for moving the robot and for Manual control. Click a "
            "field and press a key."))
        grid = QGridLayout()
        self.key_edits: dict[str, QKeySequenceEdit] = {}
        for row, action in enumerate(keys.ACTIONS):
            grid.addWidget(QLabel(action.label), row, 0)
            edit = QKeySequenceEdit(self)
            edit.setMaximumSequenceLength(1)
            edit.keySequenceChanged.connect(lambda _k: self._edited())
            grid.addWidget(edit, row, 1)
            self.key_edits[action.id] = edit
        grid.setColumnStretch(1, 1)
        box.layout().addLayout(grid)
        row = QHBoxLayout()
        self.keys_defaults_button = secondary_button("Reset to defaults", self)
        self.keys_defaults_button.clicked.connect(self._keys_defaults)
        row.addWidget(self.keys_defaults_button)
        row.addStretch(1)
        box.layout().addLayout(row)
        return box

    def _show_keys(self, bindings: dict) -> None:
        for action_id, edit in self.key_edits.items():
            edit.blockSignals(True)
            edit.setKeySequence(keys.sequences(bindings, action_id)[0])
            edit.blockSignals(False)

    def _keys_defaults(self) -> None:
        self._show_keys({})
        self._edited()

    def _typed_keys(self) -> dict[str, str]:
        """The keys that differ from the defaults; raises ValueError when
        one is missing, Esc, or used twice."""
        out = {}
        for action_id, edit in self.key_edits.items():
            typed = edit.keySequence()
            default = keys.sequences({}, action_id)[0]
            if typed != default:
                out[action_id] = typed.toString(
                    QKeySequence.SequenceFormat.PortableText)
        problems = keys.problems(out)
        if problems:
            raise ValueError(" ".join(problems))
        return out

    def _show_tip(self, settings: AppSettings) -> None:
        widgets = (self.tip_frames, self.tip_verify, self.tip_touch_up,
                   self.tip_dx, self.tip_dy)
        for widget in widgets:
            widget.blockSignals(True)
        self.tip_type.setText(settings.tip_cal_tip_type)
        self.tip_frames.setValue(settings.tip_cal_frames)
        self.tip_dx.setValue(settings.tip_cal_start_dx)
        self.tip_dy.setValue(settings.tip_cal_start_dy)
        self.tip_verify.setChecked(settings.tip_cal_verify)
        self.tip_touch_up.setChecked(settings.tip_cal_touch_up)
        for widget in widgets:
            widget.blockSignals(False)

    def _show_sweep(self, settings: AppSettings) -> None:
        widgets = (self.marker_side, self.dictionary, self.grid_n,
                   self.degree)
        for widget in widgets:
            widget.blockSignals(True)
        self.marker_side.setValue(settings.sweep_marker_side_mm)
        index = self.dictionary.findText(settings.sweep_dictionary)
        self.dictionary.setCurrentIndex(max(0, index))
        self.grid_n.setValue(settings.sweep_grid_n)
        self.degree.setValue(settings.sweep_degree)
        for widget in widgets:
            widget.blockSignals(False)

    def _sweep_defaults(self) -> None:
        self._show_sweep(AppSettings())
        self._edited()

    def _inference_card(self) -> QWidget:
        """Where the models run: see `core.vision.yolo`."""
        box = card(self)
        box.layout().addWidget(heading("Inference", 2))
        box.layout().addWidget(_label(
            "Where the machine learning models run."))
        grid = QGridLayout()
        grid.addWidget(QLabel("Device"), 0, 0)
        self.inference_device = combo_box(self)
        for text, value in (("Auto", "auto"), ("CPU", "cpu"), ("GPU", "gpu")):
            self.inference_device.addItem(text, value)
        self.inference_device.setToolTip(
            "Auto measures each device on the first load and uses the "
            "faster.")
        self.inference_device.currentIndexChanged.connect(
            lambda _i: self._edited())
        grid.addWidget(self.inference_device, 0, 1,
                       Qt.AlignmentFlag.AlignLeft)
        grid.setColumnStretch(2, 1)
        box.layout().addLayout(grid)
        self.inference_state = _label()
        box.layout().addWidget(self.inference_state)
        return box

    def _show_inference(self) -> None:
        """The GPUs found, and what each model loaded so far runs on."""
        found = yolo.gpus()
        lines = [f"GPU: {', '.join(found)}." if found else "No GPU found."]
        lines += [f"{name}: {loaded}." for name, loaded in yolo.LOADED.items()]
        self.inference_state.setText("\n".join(lines))

    def _clips_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Pickup clips", 2))
        box.layout().addWidget(_label(
            "Lower camera mode and crop for pickup clips. Smaller modes "
            "record at a higher frame rate."))
        grid = QGridLayout()
        grid.addWidget(QLabel("Resolution"), 0, 0)
        self.clip_resolution = combo_box(self)
        self.clip_resolution.setToolTip(
            "Lower camera modes from the profile.")
        self.clip_resolution.currentIndexChanged.connect(
            lambda _i: self._edited())
        grid.addWidget(self.clip_resolution, 0, 1, Qt.AlignmentFlag.AlignLeft)
        grid.addWidget(QLabel("Crop"), 1, 0)
        self.clip_crop = double_spin_box(self)
        self.clip_crop.setRange(0.1, 1.0)
        self.clip_crop.setSingleStep(0.05)
        self.clip_crop.setDecimals(2)
        self.clip_crop.setToolTip("Side of the centered square kept, as a "
                                  "fraction of the frame height.")
        self.clip_crop.valueChanged.connect(lambda _v: self._edited())
        grid.addWidget(self.clip_crop, 1, 1, Qt.AlignmentFlag.AlignLeft)
        grid.setColumnStretch(2, 1)
        box.layout().addLayout(grid)
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
        self._show_sweep(settings)
        self._show_tip(settings)
        self._show_keys(settings.keys)
        self.inference_device.blockSignals(True)
        self.inference_device.setCurrentIndex(
            self.inference_device.findData(settings.inference_device))
        self.inference_device.blockSignals(False)
        self._show_inference()
        self.save_state.setText(
            self.session.settings_note
            or (f"Saved in {settings_path()}." if settings_path().is_file()
                else "Defaults (not saved yet)."))
        self._refresh()

    def revert(self) -> None:
        """Revert, and the sheet's ×: the saved settings back in the
        fields."""
        self._show()

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
                           clip_crop=round(self.clip_crop.value(), 2),
                           sweep_marker_side_mm=round(
                               self.marker_side.value(), 2),
                           sweep_dictionary=self.dictionary.currentText(),
                           sweep_grid_n=int(self.grid_n.value()),
                           sweep_degree=int(self.degree.value()),
                           tip_cal_tip_type=self.tip_type.text().strip(),
                           tip_cal_frames=int(self.tip_frames.value()),
                           tip_cal_verify=self.tip_verify.isChecked(),
                           tip_cal_touch_up=self.tip_touch_up.isChecked(),
                           tip_cal_start_dx=round(self.tip_dx.value(), 3),
                           tip_cal_start_dy=round(self.tip_dy.value(), 3),
                           inference_device=self.inference_device.currentData(),
                           keys=self._typed_keys())

    def _edited(self) -> None:
        self.save_state.setText("Unsaved changes.")
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
        self.save_state.setText(f"Saved in {settings_path()}.")

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

    def _on_profile_changed(self, profile) -> None:
        self._fill_clip_modes()
        self.profile_note.setText(
            "No profile loaded." if profile is None else
            f"Profile <b>{profile.name}</b>: saved immediately.")
        self._reload_models()
        self.calibration.setText(
            "\n".join(calibration_lines(profile)) if profile is not None
            else "No profile loaded.")

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
            ""
            if not missing else
            f"Not found in {paths.ml_models_dir()}: {', '.join(missing)}.")

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
            self.robot_state.setText("Mock mode: no robot.")
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
            f"No answer: {short}")
        log.error("robot test: %s", reason)
        self._refresh()

    # -- display -------------------------------------------------------------

    def _refresh(self, _arg=None) -> None:
        connected = self.session.robot is not None
        testing = self._worker is not None and self._worker.running
        for widget in (self.host, self.port):
            widget.setEnabled(not connected)
        self.test_button.setEnabled(not testing)
        if connected and not testing:
            self.robot_state.setText(
                f"Connected to {self.session.robot_address}.")
        elif not self.robot_state.text():
            self.robot_state.setText(f"Looking for the robot at "
                                     f"{self.session.robot_address}.")


class AdvancedPanel(QWidget):
    """The robot session, by hand: Start new robot session (it homes, so
    it asks, as Home the robot does) and Disconnect robot session, which
    lets the session go and keeps the connection monitor off it until
    Continue robot session (`Session.release_run`). Both claim the robot's
    lease and are refused while it is held."""

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._worker: Worker | None = None
        self._done_text = ""                     # said when the job ends
        box = card(self)
        box.layout().addWidget(heading("Robot session", 2))
        self.session_line = _label()
        box.layout().addWidget(self.session_line)
        row = QHBoxLayout()
        self.new_button = secondary_button("Start new robot session", self)
        self.new_button.clicked.connect(self._new)
        self.disconnect_button = secondary_button("Disconnect robot session",
                                                  self)
        self.disconnect_button.clicked.connect(self._disconnect)
        self.continue_button = secondary_button("Continue robot session",
                                                self)
        self.continue_button.setToolTip("Carry on with the robot's session.")
        self.continue_button.clicked.connect(session.continue_run)
        for button in (self.new_button, self.disconnect_button,
                       self.continue_button):
            row.addWidget(button)
        row.addStretch(1)
        box.layout().addLayout(row)
        self.state = _label()
        box.layout().addWidget(self.state)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(box)
        column.addStretch(1)
        holder = QWidget(self)
        holder.setLayout(column)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)
        outer.addWidget(scroll_column(holder, COLUMN_WIDTH))
        outer.addStretch(1)

        # Bound methods, not lambdas: the session's signals come from a
        # worker too, and Qt queues only a bound method onto this thread.
        session.robot_state_changed.connect(self.refresh)
        session.robot_busy_changed.connect(self.refresh)
        session.lock_changed.connect(self.refresh)
        self.refresh()

    def refresh(self, _arg=None) -> None:
        session = self.session
        holder = session.robot_lease.holder
        refusal = (f"Robot busy: {holder}." if holder is not None else
                   "Robot locked." if session.locked else "")
        state = session.run_state
        if session.robot is not None and state is not None:
            self.session_line.setText(f"Robot session {state.label}.")
        elif session.released is not None:
            self.session_line.setText("Robot session disconnected.")
        else:
            self.session_line.setText("No robot session.")
        answers = session.robot is not None or (
            session.has_connection and session.robot_answers is True
            and state is not None)
        self.new_button.setEnabled(answers and not refusal)
        self.new_button.setToolTip(
            refusal or ("Start a new robot session with an empty deck."
                        if answers else "Robot not connected."))
        self.disconnect_button.setVisible(session.released is None)
        self.disconnect_button.setEnabled(session.robot is not None
                                          and not refusal)
        self.disconnect_button.setToolTip(
            refusal or ("Let go of the robot's session."
                        if session.robot is not None else
                        "Robot not connected."))
        self.continue_button.setVisible(session.released is not None)

    def _claim(self, what: str):
        lease = self.session.robot_lease.claim(what)
        if lease is None:
            self.state.setText(self.session.robot_lease.refusal)
        return lease

    def _run(self, job, *args, what: str, lease, done: str) -> None:
        worker = Worker(job, *args, what=what, lease=lease)
        self._done_text = done
        worker.finished.connect(self._finished)
        worker.failed.connect(self._failed)
        self._worker = worker
        log.info("%s", what)
        worker.start()
        self.refresh()

    def _new(self) -> None:
        from .home import new_session_dialog
        dialog = new_session_dialog(self.session, self,
                                    title="Start new robot session")
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        name = dialog.textValue().strip() or None
        what = "new robot session, then home"
        lease = self._claim(what)
        if lease is not None:
            self.state.setText("Homing.")
            self._run(self.session.new_run, name, what=what, lease=lease,
                      done="New robot session started.")

    def _disconnect(self) -> None:
        what = "disconnecting the robot session"
        lease = self._claim(what)
        if lease is not None:
            self._run(self.session.release_run, what=what, lease=lease,
                      done="Robot session disconnected.")

    def _finished(self, _result=None) -> None:
        self._worker = None
        self.state.setText(self._done_text)
        self.refresh()

    def _failed(self, reason: str) -> None:
        self._worker = None
        self.state.setText(reason)
        log.error("robot session: %s", reason)
        self.refresh()


# The sheet's sections: (key, words on screen).
SECTIONS = (("general", "General"), ("advanced", "Advanced"), ("log", "Log"))


class SettingsSheet(SheetWindow):
    """Settings, opened from the gear over whatever is shown (REDESIGN
    §12.15): this computer's settings and the profile's (General), the
    robot session by hand (Advanced), and the log. General keeps its own
    Save and Revert; closing it, by × or by going elsewhere, discards what
    was not saved."""

    def __init__(self, session: Session, host: QWidget):
        super().__init__(host, TITLE, buttons=False)
        self.sections = Segmented([title for _key, title in SECTIONS],
                                  parent=self.body_widget)
        self.sections.setFixedWidth(360)
        self.form = SettingsPage(session, self.body_widget)
        self.form.layout().setContentsMargins(0, 0, 0, 0)
        self.advanced = AdvancedPanel(session, self.body_widget)
        self.log = LogPage(session, self.body_widget)
        self.log.layout().setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget(self.body_widget)
        for widget in (self.form, self.advanced, self.log):
            self.stack.addWidget(widget)
        self.sections.changed.connect(self.stack.setCurrentIndex)
        self.body.setSpacing(SPACING * 2)
        self.body.addWidget(self.sections, 0, Qt.AlignmentFlag.AlignHCenter)
        self.body.addWidget(self.stack, 1)
        self.canceled.connect(self.form.revert)

    def discard(self) -> None:
        """Closed by going elsewhere: as ×, what was not saved is gone."""
        if not self.isHidden():
            self.hide()
            self.form.revert()

    def section(self) -> str:
        return SECTIONS[self.stack.currentIndex()][0]

    def show_section(self, key: str) -> None:
        index = [k for k, _title in SECTIONS].index(key)
        self.stack.setCurrentIndex(index)
        self.sections.set_current(index)

    def open_on(self, key: str = "general") -> None:
        self.show_section(key)
        self.advanced.refresh()
        self.open()
