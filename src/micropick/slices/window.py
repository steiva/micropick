"""Slice picking's one window: the microscope's picture and a column of cards.

One job at a time: `_run` claims the robot's lease, and a claim refused is
said, never queued. A left click on the picture sends the tool there through
`Rig.to_pixel`, a right click to the saved sample position. The keys are the
main program's (`gui.keys`, as bound in Settings) and this page's own
(`OWN_KEYS`), and none works while a field has the keyboard. Esc is Stop: the
first asks the job to stop at its next safe point, a second within 10 s holds
the robot (`OpentronsAPI.hold`) until the job has ended.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime

import cv2
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QDialog,
                               QDialogButtonBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QVBoxLayout, QWidget)

from .. import paths
from ..config.labware import LabwareError, resolve_definition
from ..gui import keys as key_actions
from ..gui.labware_kinds import catalogue, facts, kind
from ..gui.theme import SPACING
from ..gui.theme.factory import (card, danger_button, heading, labeled,
                                 muted_label, number_field, primary_button,
                                 reserve, scroll_column, secondary_button,
                                 select)
from ..gui.widgets.camera_view import CameraView
from ..gui.widgets.feed_row import FeedRow
from ..gui.widgets.fold_card import FoldCard
from ..gui.widgets.jog_pad import JogPad, step_chip_text, step_ink
from ..gui.widgets.jog_panel import TYPING_HELP
from ..gui.widgets.mini_deck import DeckTile
from ..gui.widgets.slot_chooser import SlotChooser
from ..gui.widgets.tip_gauge import TipGauge
from ..gui.widgets.toast import show_toast
from ..gui.widgets.well_picker import WellPicker
from ..gui.workers import Worker
from ..hardware import devices
from ..viz import overlays
from ..workflows.jog import Reached
from . import calibration
from .mock import DEVICE as MOCK_DEVICE
from .mock import open_mock_microscope
from .rig import RESOLUTION, Rig, SliceRefused, open_microscope

__all__ = ["SliceWindow"]

log = logging.getLogger(__name__)

SIDE_WIDTH = 440
LOOK_MS = 3000
SECOND_STOP_S = 10.0
FREQUENT = ("opentrons_96_tiprack_300ul", "corning_96_wellplate_360ul_flat",
            "corning_24_wellplate_3.4ml_flat",
            "corning_384_wellplate_112ul_flat", "vwr_96_tiprack_200ul_xl")
# Letters a Windows folder name cannot hold.
NOT_IN_A_FOLDER = re.compile(r'[<>:"/\\|?*]')
OWN_KEYS = (("B", "Blow out", "_blow_out"),
            ("R", "Return over the aspirate point", "_return"),
            ("G", "Go to the well", "_go_to_well"),
            ("S", "Save frame", "_save_frame"))
MOUSE_HELP = ("Click   tool goes there", "Right-click   go to sample",
              "Wheel   zoom", "Middle drag   pan", "Double-click   fit")


class _LabwareDialog(QDialog):
    """What stands in a slot: a search over every definition, the usual ones
    first. `chosen` is the load name, "" for Empty slot."""

    def __init__(self, slot: int, current: str | None, parent: QWidget):
        super().__init__(parent)
        self.setWindowTitle(f"Slot {slot}")
        self.resize(460, 520)
        self.chosen: str | None = None
        _custom, known = catalogue()
        names = ([n for n in FREQUENT if n in known]
                 + sorted(n for n in known if n not in FREQUENT))
        column = QVBoxLayout(self)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search labware")
        self.search.textChanged.connect(self._filter)
        column.addWidget(self.search)
        self.list = QListWidget(self)
        for name in names:
            definition = known[name]
            item = QListWidgetItem(f"{definition.display_name}\n"
                                   f"{facts(definition)}")
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.list.addItem(item)
            if name == current:
                self.list.setCurrentItem(item)
        self.list.itemDoubleClicked.connect(lambda _item: self._accept())
        column.addWidget(self.list, 1)
        buttons = QDialogButtonBox(self)
        put = buttons.addButton("Put in slot",
                                QDialogButtonBox.ButtonRole.AcceptRole)
        put.setDefault(True)
        empty = buttons.addButton("Empty slot",
                                  QDialogButtonBox.ButtonRole.DestructiveRole)
        empty.setEnabled(current is not None)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        put.clicked.connect(self._accept)
        empty.clicked.connect(self._empty)
        buttons.rejected.connect(self.reject)
        column.addWidget(buttons)

    def _filter(self, text: str) -> None:
        words = text.lower().split()
        for row in range(self.list.count()):
            item = self.list.item(row)
            found = f"{item.text()} {item.data(Qt.ItemDataRole.UserRole)}"
            item.setHidden(not all(w in found.lower() for w in words))

    def _accept(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self.chosen = item.data(Qt.ItemDataRole.UserRole)
            self.accept()

    def _empty(self) -> None:
        self.chosen = ""
        self.accept()


class SliceWindow(QMainWindow):
    def __init__(self, rig: Rig, bindings: dict | None = None):
        super().__init__()
        self.setWindowTitle("Slice picking")
        self.rig = rig
        self.state = rig.state
        self.bindings = bindings or {}
        self.camera = None
        self.device: str | None = None
        self._worker = None
        self._opening = None
        self._looking = None
        self._reading = None
        self._lighting = None
        self._deck_shown = None
        self._tip_was_on = False
        self._stopped_at: float | None = None
        self._robot_word = "Connecting to the robot…"
        self._pose: tuple | None = None
        self._target: tuple | None = None

        self.view = CameraView()
        self.view.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.view.set_mouse_help(MOUSE_HELP)
        self.view.clicked.connect(self._picture_clicked)
        self.view.right_clicked.connect(lambda _u, _v: self._to_sample())

        self.gauge = TipGauge(capacity_ul=300.0)
        self.view.add_top_left_widget(self.gauge)
        self.light_button = secondary_button("Light")
        self.light_button.setCheckable(True)
        self.light_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.light_button.clicked.connect(self._toggle_lights)
        self.view.add_top_widget(self.light_button)
        self.stop_button = danger_button("STOP")
        self.stop_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.stop_button.clicked.connect(self._stop)
        self.view.add_top_widget(self.stop_button)

        self.pad = JogPad()
        self.pad.set_bindings(self.bindings)
        self.pad.set_key_lines(self._key_lines())
        self.pad.jog.connect(self._jog)
        self.pad.undo.connect(self._undo)
        self.pad.step_changed.connect(self._step_changed)
        self.view.add_corner_widget(self.pad)
        self._step_changed(self.pad.step)

        inner = QWidget()
        inner.setObjectName("plain")
        inner.setFixedWidth(SIDE_WIDTH)
        column = QVBoxLayout(inner)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        column.addWidget(self._camera_card())
        column.addWidget(self._setup_card())
        column.addWidget(self._calibration_card())
        column.addWidget(self._liquid_card())
        column.addWidget(self._well_card())
        column.addWidget(self._frames_card())
        column.addStretch(1)
        side = scroll_column(inner, SIDE_WIDTH)
        side.setMinimumWidth(SIDE_WIDTH + 16)

        self.setCentralWidget(FeedRow(self.view, side))
        self._bind_keys()
        QApplication.instance().focusChanged.connect(self._focus_moved)

        self._monitor = QTimer(self)
        self._monitor.setInterval(LOOK_MS)
        self._monitor.timeout.connect(self._look)
        self._monitor.start()
        self._look()
        self._fill_devices()
        self._refresh()

    # -- the cards -----------------------------------------------------------

    def _camera_card(self) -> QWidget:
        box = card(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Camera", box))
        self.devices = select(box)
        self.devices.activated.connect(self._device_chosen)
        row.addWidget(self.devices, 1)
        box.layout().addLayout(row)
        return box

    def _setup_card(self) -> QWidget:
        self.setup = FoldCard("Setup", open=True, parent=self)
        body = self.setup.body.layout()

        robot = QHBoxLayout()
        self.robot_line = muted_label("", self.setup)
        robot.addWidget(self.robot_line, 1)
        self.home_button = secondary_button("Home and connect", self.setup)
        self.home_button.clicked.connect(self._home)
        robot.addWidget(self.home_button)
        body.addLayout(robot)

        self.deck = SlotChooser(self.setup)
        self.deck.slot_clicked.connect(self._slot_clicked)
        body.addWidget(self.deck)

        platforms = QHBoxLayout()
        self.platform_slots = QLineEdit(
            ", ".join(map(str, self.state.platform_slots)), self.setup)
        self.platform_height = number_field(
            self.setup, self.state.platform_height_mm, (0.0, 150.0), step=0.1)
        self.platform_save = secondary_button("Save", self.setup)
        self.platform_save.clicked.connect(self._save_platforms)
        self.platform_slots.returnPressed.connect(self._save_platforms)
        platforms.addWidget(labeled("Platform slots", self.platform_slots), 1)
        platforms.addWidget(labeled("Height, mm", self.platform_height))
        platforms.addWidget(self.platform_save, 0,
                            Qt.AlignmentFlag.AlignBottom)
        body.addLayout(platforms)

        tip = QHBoxLayout()
        self.rack = select(self.setup)
        self.tip_well = QLineEdit(self.state.tip_well, self.setup)
        self.tip_well.setFixedWidth(64)
        self.tip_well.returnPressed.connect(self.tip_well.clearFocus)
        tip.addWidget(labeled("Tip rack", self.rack), 1)
        tip.addWidget(labeled("Tip", self.tip_well))
        body.addLayout(tip)

        tip_buttons = QHBoxLayout()
        self.pick_up_button = secondary_button("Pick up", self.setup)
        self.pick_up_button.clicked.connect(self._pick_up)
        self.drop_button = secondary_button("Drop in place", self.setup)
        self.drop_button.clicked.connect(
            lambda: self._run(self.rig.drop_in_place, "dropping the tip"))
        self.trash_button = secondary_button("Drop in trash", self.setup)
        self.trash_button.clicked.connect(
            lambda: self._run(self.rig.drop_in_trash, "dropping the tip"))
        for button in (self.pick_up_button, self.drop_button,
                       self.trash_button):
            tip_buttons.addWidget(button)
        body.addLayout(tip_buttons)
        self.tip_line = muted_label("", self.setup)
        reserve(self.tip_line)
        body.addWidget(self.tip_line)
        return self.setup

    def _calibration_card(self) -> QWidget:
        box = card(self)
        column = box.layout()
        column.addWidget(heading("Calibration", 3, box))
        self.map_line = muted_label("", box)
        self.map_line.setWordWrap(True)
        column.addWidget(self.map_line)
        self.calibrate_button = primary_button("Calibrate", box)
        self.calibrate_button.clicked.connect(self._calibrate)
        column.addWidget(self.calibrate_button)
        self.sample_line = muted_label("", box)
        column.addWidget(self.sample_line)
        row = QHBoxLayout()
        self.save_sample_button = secondary_button("Save position", box)
        self.save_sample_button.clicked.connect(
            lambda: self._run(self.rig.save_sample, "saving the position"))
        self.sample_button = secondary_button("Go to sample", box)
        self.sample_button.clicked.connect(self._to_sample)
        row.addWidget(self.save_sample_button)
        row.addWidget(self.sample_button)
        column.addLayout(row)
        return box

    def _liquid_card(self) -> QWidget:
        box = card(self)
        column = box.layout()
        column.addWidget(heading("Liquid", 3, box))
        fields = QHBoxLayout()
        self.volume = number_field(box, self.state.volume_ul, (0.1, 300.0),
                                   decimals=1, step=1.0)
        self.flow = number_field(box, self.state.flow_rate, (1.0, 600.0),
                                 decimals=1, step=1.0)
        fields.addWidget(labeled("Volume, µl", self.volume))
        fields.addWidget(labeled("Flow rate, µl/s", self.flow))
        fields.addStretch(1)
        column.addLayout(fields)
        aspirate = key_actions.key_text(self.bindings, "aspirate")
        dispense = key_actions.key_text(self.bindings, "dispense")
        self.aspirate_button = secondary_button(f"Aspirate ({aspirate})", box)
        self.dispense_button = secondary_button(f"Dispense ({dispense})", box)
        self.blow_button = secondary_button("Blow out (B)", box)
        self.return_button = secondary_button("Return (R)", box)
        self.aspirate_button.clicked.connect(self._aspirate)
        self.dispense_button.clicked.connect(self._dispense)
        self.blow_button.clicked.connect(self._blow_out)
        self.return_button.clicked.connect(self._return)
        for pair in ((self.aspirate_button, self.dispense_button),
                     (self.blow_button, self.return_button)):
            row = QHBoxLayout()
            for button in pair:
                button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                row.addWidget(button)
            column.addLayout(row)
        return box

    def _well_card(self) -> QWidget:
        box = card(self)
        column = box.layout()
        column.addWidget(heading("Well", 3, box))
        self.wells = WellPicker("accent", 380, box)
        column.addWidget(self.wells)
        offsets = QHBoxLayout()
        self.offsets = [number_field(box, value, (-50.0, 100.0), step=0.1)
                        for value in self.state.well_offset]
        for name, field in zip("XYZ", self.offsets):
            offsets.addWidget(labeled(f"{name}, mm", field))
        offsets.addStretch(1)
        column.addLayout(offsets)
        self.well_button = secondary_button("Go to the well (G)", box)
        self.well_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.well_button.clicked.connect(self._go_to_well)
        column.addWidget(self.well_button)
        return box

    def _frames_card(self) -> QWidget:
        box = card(self)
        column = box.layout()
        column.addWidget(heading("Frames", 3, box))
        self.folder = QLineEdit(self.state.folder, box)
        self.folder.setPlaceholderText("Experiment folder")
        self.folder.returnPressed.connect(self.folder.clearFocus)
        column.addWidget(self.folder)
        self.frame_button = secondary_button("Save frame (S)", box)
        self.frame_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.frame_button.clicked.connect(self._save_frame)
        column.addWidget(self.frame_button)
        return box

    # -- jobs ----------------------------------------------------------------

    def tell(self, text: str) -> None:
        show_toast(self, text)

    def refuse(self, text: str) -> None:
        show_toast(self, text, refusal=True)

    def _run(self, fn, what: str, *, run: bool = False, then=None,
             **kwargs) -> bool:
        """`fn` on a worker under the lease, or a refusal."""
        if not self.rig.ready:
            self.refuse("Connect the robot first.")
            return False
        lease = self.rig.lease.claim(what, run=run)
        if lease is None:
            self.refuse(self.rig.lease.refusal)
            return False
        worker = Worker(fn, lease=lease, what=what, **kwargs)
        worker.finished.connect(lambda result: self._job_done(result, then))
        worker.failed.connect(self._job_failed)
        self._worker = worker
        self._stopped_at = None
        worker.start()
        self._refresh()
        return True

    def _job_ended(self) -> None:
        self._worker = None
        self._stopped_at = None
        api = self.rig.api
        if api is not None:
            api.hold.clear()

    def _job_done(self, result, then) -> None:
        self._job_ended()
        if isinstance(result, Reached):
            self._pose = tuple(result.pose)
            if result.said:
                self.tell(result.said)
        if then is not None:
            then(result)
        self._refresh()

    def _job_failed(self, reason: str) -> None:
        self._job_ended()
        self._target = None
        self.refuse(reason)
        self._read_pose()
        self._refresh()

    def _read_pose(self) -> None:
        """Where the robot is. A read: no lease, and no job to fail."""
        if not self.rig.ready or self._reading is not None:
            return
        worker = Worker(self.rig.pose, background=True, what="reading the pose")
        worker.finished.connect(self._pose_read)
        worker.failed.connect(self._pose_not_read)
        self._reading = worker
        worker.start()

    def _pose_read(self, pose) -> None:
        self._reading = None
        self._pose = tuple(pose)
        self._refresh()

    def _pose_not_read(self, reason: str) -> None:
        self._reading = None
        log.warning("the pose could not be read: %s", reason)

    def _stop(self) -> None:
        worker = self._worker
        if worker is None:
            return
        now = time.monotonic()
        if (self._stopped_at is not None
                and now - self._stopped_at <= SECOND_STOP_S):
            self.rig.api.hold.set()
            self.refuse("Robot held until the job ends.")
            return
        self._stopped_at = now
        worker.request_cancel()
        self.tell("Stopping…")

    # -- the robot -----------------------------------------------------------

    def _look(self) -> None:
        if self._looking is not None or self.rig.lease.busy:
            return
        worker = Worker(self.rig.look, background=True, what="looking")
        worker.finished.connect(self._looked)
        worker.failed.connect(self._no_answer)
        self._looking = worker
        worker.start()

    def _looked(self, word: str) -> None:
        self._looking = None
        was_ready = bool(self._pose)
        self._robot_word = ("Connected." if word == "ready"
                            else "Robot needs to home.")
        if word == "ready" and not was_ready:
            self._read_pose()
        elif word != "ready":
            self._pose = None
        self._refresh()

    def _no_answer(self, _reason: str) -> None:
        self._looking = None
        if self.rig.lease.busy:
            return
        self._robot_word = "Connecting to the robot…"
        self.rig.forget()
        self.rig.lights = None
        self._pose = None
        self._refresh()

    def _home(self) -> None:
        answer = QMessageBox.question(
            self, "Home and connect?",
            "The robot will move to its home position. Keep hands out of "
            "the robot. A new robot session forgets a tip that is on.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.rig.api is None:
            self.refuse("The robot does not answer.")
            return
        lease = self.rig.lease.claim("homing", run=True)
        if lease is None:
            self.refuse(self.rig.lease.refusal)
            return
        self._robot_word = "Homing…"
        worker = Worker(self.rig.new_session, lease=lease, what="homing")
        worker.finished.connect(lambda result: self._homed(result))
        worker.failed.connect(self._job_failed)
        self._worker = worker
        worker.start()
        self._refresh()

    def _homed(self, result) -> None:
        self._robot_word = "Connected."
        self._job_done(result, None)

    def _toggle_lights(self) -> None:
        worker = Worker(self.rig.toggle_lights, background=True,
                        what="switching the lights")
        worker.finished.connect(lambda _result: self._lights_done())
        worker.failed.connect(self._lights_failed)
        self._lighting = worker
        worker.start()
        self._refresh()

    def _lights_done(self) -> None:
        self._lighting = None
        self._refresh()

    def _lights_failed(self, reason: str) -> None:
        self.refuse(reason)
        self._lights_done()

    # -- deck and tip --------------------------------------------------------

    def _slot_clicked(self, slot: int) -> None:
        if not self.rig.ready:
            self.refuse("Connect the robot first.")
            return
        if slot == 12:
            self.refuse("Slot 12 is the trash.")
            return
        entry = self.rig.labware.get(str(slot))
        dialog = _LabwareDialog(slot, entry.load_name if entry else None, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.chosen:
            self._run(lambda: self.rig.load(dialog.chosen, slot),
                      "loading labware")
        else:
            self._run(lambda: self.rig.unload(slot), "emptying the slot")

    def _save_platforms(self) -> None:
        text = self.platform_slots.text()
        try:
            slots = sorted({int(word) for word in re.split(r"[,\s]+", text)
                            if word})
        except ValueError:
            self.refuse("Write the platform's slots as numbers: 5, 8, 9.")
            return
        if any(not 1 <= slot <= 11 for slot in slots):
            self.refuse("Slots go from 1 to 11.")
            return
        height = self.platform_height.value()
        self.platform_slots.clearFocus()
        self._run(lambda: self.rig.set_platforms(slots, height),
                  "saving the platforms", then=lambda _r: self.tell(
                      "Platforms saved."))

    def _pick_up(self) -> None:
        slot = self.rack.currentData()
        well = self.tip_well.text().strip().upper()
        if slot is None:
            self.refuse("Put a tip rack on the deck.")
            return
        self._run(lambda: self.rig.pick_up(slot, well), "picking up a tip")

    # -- liquid --------------------------------------------------------------

    def _keep_fields(self) -> None:
        state = self.state
        state.volume_ul = self.volume.value()
        state.flow_rate = self.flow.value()
        state.well_offset = tuple(f.value() for f in self.offsets)
        state.folder = self.folder.text().strip()

    def _aspirate(self) -> None:
        self._keep_fields()
        self._run(lambda: self.rig.aspirate(self.state.volume_ul,
                                            self.state.flow_rate),
                  "aspirating")

    def _dispense(self) -> None:
        self._keep_fields()
        self._run(lambda: self.rig.dispense(self.state.volume_ul,
                                            self.state.flow_rate),
                  "dispensing")

    def _blow_out(self) -> None:
        self._run(self.rig.blow_out, "blowing out")

    def _return(self) -> None:
        self._run(self.rig.return_up, "returning")

    def _to_sample(self) -> None:
        self._run(self.rig.to_sample, "going to the sample")

    def _go_to_well(self) -> None:
        self._keep_fields()
        chosen = self.wells.chosen()
        if chosen is None or not chosen[3]:
            self.refuse("Choose a well.")
            return
        _slot, labware_id, _definition, well = chosen
        offset = self.state.well_offset
        self._run(lambda: self.rig.to_well(labware_id, well, offset),
                  "going to the well")

    # -- the picture ---------------------------------------------------------

    def _size(self) -> tuple[int, int]:
        return tuple(self.camera.resolution) if self.camera else RESOLUTION

    def _picture_clicked(self, u: float, v: float) -> None:
        self.setFocus()
        if self.camera is None:
            return
        if self._run(lambda: self.rig.to_pixel(self.device, self._size(), u, v),
                     "moving the tool"):
            self._target = (u, v)

    def _calibrate(self) -> None:
        if self.camera is None:
            self.refuse("Choose a camera first.")
            return
        self._target = None
        self._run(self.rig.calibrate, "calibrating", run=True,
                  camera=self.camera, device=self.device,
                  spacing_mm=calibration.SPACING_MM)

    def _save_frame(self) -> None:
        self._keep_fields()
        name = self.state.folder
        if NOT_IN_A_FOLDER.search(name):
            self.refuse('A folder name cannot have < > : " / \\ | ? *')
            return
        ok, frame = self.camera.read() if self.camera else (False, None)
        if not ok:
            self.refuse("No picture to save.")
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        where = ""
        if self._pose is not None:
            where = "_X{:.2f}_Y{:.2f}_Z{:.2f}".format(*self._pose)
        folder = paths.images_dir() / (name or "slices")
        path = folder / f"frame_{stamp}{where}.png"
        frame = frame.copy()

        def write():
            folder.mkdir(parents=True, exist_ok=True)
            ok, data = cv2.imencode(".png", frame)
            if not ok:
                raise OSError("the frame could not be encoded")
            path.write_bytes(data.tobytes())

        worker = Worker(write, background=True, what="saving a frame")
        worker.finished.connect(lambda _r: self.tell(f"Saved {path.name}"))
        worker.failed.connect(self.refuse)
        self._saving = worker
        worker.start()

    # -- the camera ----------------------------------------------------------

    def _fill_devices(self) -> None:
        names = ([MOCK_DEVICE] if self.rig.mock
                 else [d.name for d in devices.list_devices()])
        self.devices.clear()
        self.devices.addItems(names)
        wanted = next((n for n in (self.state.device, "Digital Microscope",
                                   MOCK_DEVICE) if n in names), None)
        self.devices.setCurrentIndex(names.index(wanted) if wanted else -1)
        if wanted:
            self._open_device(wanted)

    def _device_chosen(self, _index: int) -> None:
        name = self.devices.currentText()
        if name and name != self.device:
            self._open_device(name)

    def _open_device(self, name: str) -> None:
        if self._opening is not None:
            return
        if self.rig.lease.holding_run:
            self.refuse("Robot busy.")
            self.devices.setCurrentText(self.device or "")
            return
        old, self.camera = self.camera, None
        self.view.set_camera(None)
        self.device = None

        def opened():
            if old is not None:
                old.close()
            return (open_mock_microscope(self.rig.api) if self.rig.mock
                    else open_microscope(name))

        worker = Worker(opened, background=True, what="opening the camera")
        worker.finished.connect(lambda camera: self._camera_open(name, camera))
        worker.failed.connect(self._camera_failed)
        self._opening = worker
        worker.start()

    def _camera_open(self, name: str, camera) -> None:
        self._opening = None
        self.camera, self.device = camera, name
        self.state.device = name
        self.view.set_camera(camera)
        self._refresh()

    def _camera_failed(self, reason: str) -> None:
        self._opening = None
        self.refuse(reason)
        self._refresh()

    # -- keys ----------------------------------------------------------------

    def _key_lines(self) -> list[str]:
        moved = [f"{key_actions.key_text(self.bindings, action)}   {what}"
                 for action, what in (("aspirate", "Aspirate"),
                                      ("dispense", "Dispense"))]
        own = [f"{key}   {what}" for key, what, _slot in OWN_KEYS]
        return [*moved, *own, "Esc   Stop"]

    def _bind(self, sequence, slot) -> None:
        shortcut = QShortcut(sequence, self)
        shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        shortcut.activated.connect(lambda: self._key(slot))

    def _bind_keys(self) -> None:
        for axis, sign, action in (("x", -1, "x-"), ("x", 1, "x+"),
                                   ("y", 1, "y+"), ("y", -1, "y-"),
                                   ("z", 1, "z+"), ("z", -1, "z-")):
            for sequence in key_actions.sequences(self.bindings, action):
                self._bind(sequence, lambda a=axis, s=sign: self._jog(a, s))
        for action, slot in (("step_up", lambda: self.pad.step_by(1)),
                             ("step_down", lambda: self.pad.step_by(-1)),
                             ("undo", self._undo),
                             ("help", self.pad.keys.toggle),
                             ("aspirate", self._aspirate),
                             ("dispense", self._dispense)):
            for sequence in key_actions.sequences(self.bindings, action):
                self._bind(sequence, slot)
        for key, _what, name in OWN_KEYS:
            self._bind(QKeySequence(key), getattr(self, name))
        stop = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        stop.setContext(Qt.ShortcutContext.WindowShortcut)
        stop.activated.connect(self._escape)

    def _typing(self) -> bool:
        focused = QApplication.focusWidget()
        return isinstance(focused, (QLineEdit, QAbstractSpinBox))

    def _key(self, slot) -> None:
        if not self._typing():
            slot()

    def _escape(self) -> None:
        if self._typing():
            QApplication.focusWidget().clearFocus()
        self._stop()

    def _focus_moved(self, _old, _new) -> None:
        self.pad.set_note("\n".join(TYPING_HELP) if self._typing() else "")

    # -- jogging -------------------------------------------------------------

    def _jog(self, axis: str, sign: int) -> None:
        # A key held down repeats: a step while one runs is dropped.
        if self.rig.lease.busy or not self.rig.ready:
            return
        jog = self.rig.jog

        def step():
            return self._stepped(jog.move(axis, sign), jog)

        self._run(step, "jogging")

    def _undo(self) -> None:
        if self.rig.lease.busy or not self.rig.ready:
            return
        jog = self.rig.jog

        def step():
            return self._stepped(jog.undo(), jog)

        self._run(step, "undoing a step")

    @staticmethod
    def _stepped(result, jog) -> Reached:
        if not result.ok:
            raise SliceRefused(result.reason)
        return Reached(result.position or jog.position, result.reason or None)

    def _step_changed(self, mm: float) -> None:
        if self.rig.jog is not None:
            self.rig.jog.step = min(self.rig.jog.steps,
                                    key=lambda s: abs(s - mm))
        self.view.set_step_line(self, step_chip_text(mm), step_ink(mm))

    # -- what is shown -------------------------------------------------------

    def _map_line(self) -> str:
        pixel_map = self.rig.pixel_map(self.device, self._size())
        if pixel_map is None:
            return "Not calibrated."
        when = self.state.map_measured_at
        date = when.astimezone().strftime("%d %b %H:%M") if when else "?"
        return f"Calibrated {date} · {pixel_map.mm_per_px * 1000:.2f} µm/px"

    def _show_marks(self, pixel_map) -> None:
        marks = []
        if pixel_map is not None and self._pose is not None:
            u, v = pixel_map.pixel(*self._pose[:2])
            marks.append(overlays.Circle(center=(int(u), int(v)), radius=14,
                                         color=(0, 255, 0), thickness=2))
        if self._target is not None:
            u, v = self._target
            marks.append(overlays.Circle(center=(int(u), int(v)), radius=5,
                                         color=(0, 0, 255), fill=True))
        self.view.set_overlay_items(marks)

    def _show_deck(self) -> None:
        """What depends on the labware's definitions, read from disk: only
        when the deck or the tip changed, not at each jog step."""
        rig = self.rig
        shown = (rig.ready, rig.tip.slot, tuple(sorted(
            (slot, entry.labware_id) for slot, entry in rig.labware.items())))
        if shown == self._deck_shown:
            return
        self._deck_shown = shown
        held = self.rack.currentData()
        self.rack.clear()
        if rig.ready:
            for slot, _entry in rig.tip_racks():
                self.rack.addItem(f"Slot {slot}", slot)
        self.rack.setCurrentIndex(max(self.rack.findData(held), 0))
        self.wells.set_plates(rig.plates() if rig.ready else [])
        self.gauge.set_capacity(rig.tip_capacity)
        tiles = []
        for slot, entry in self.rig.labware.items():
            try:
                definition = resolve_definition(entry.load_name)
            except LabwareError:
                tiles.append(DeckTile(int(slot), name=entry.load_name))
                continue
            tiles.append(DeckTile(int(slot), name=definition.display_name,
                                  kind=kind(definition),
                                  facts=facts(definition)))
        self.deck.set_tiles(tiles)

    def _refresh(self) -> None:
        rig = self.rig
        free = rig.ready and not rig.lease.busy
        tip_on = rig.tip.attached is True
        reason = rig.lease.holder or self._robot_word
        self.robot_line.setText(self._robot_word)
        self.home_button.setEnabled(rig.api is not None
                                    and not rig.lease.busy)
        self.stop_button.setEnabled(self._worker is not None)
        # The robot's answer, not the click: it is what the button shows.
        self.light_button.setChecked(rig.lights is True)
        self.light_button.setEnabled(rig.lights is not None
                                     and self._lighting is None)
        self.pad.set_enabled_reason(free, "" if free else reason)

        self._show_deck()
        for button in (self.platform_save, self.calibrate_button,
                       self.save_sample_button):
            button.setEnabled(free)
        self.pick_up_button.setEnabled(free and rig.tip.attached is False)
        for button in (self.drop_button, self.trash_button,
                       self.aspirate_button, self.blow_button):
            button.setEnabled(free and tip_on)
        self.dispense_button.setEnabled(
            free and tip_on and self.state.in_tip_ul > 0)
        self.return_button.setEnabled(free and rig.aspirated_at is not None)
        self.sample_button.setEnabled(free and self.state.sample is not None)
        self.well_button.setEnabled(free and self.wells.chosen() is not None)
        self.frame_button.setEnabled(self.camera is not None)

        self.tip_line.setText(
            "" if not rig.ready else
            ("Tip state unknown." if rig.tip.attached is None else
             f"Tip {rig.tip.well or ''} is on." if tip_on else "No tip on."))
        sample = self.state.sample
        self.sample_line.setText(
            "Sample: not set." if sample is None else
            "Sample: X {:.2f}  Y {:.2f}  Z {:.2f}".format(*sample))
        self.map_line.setText(self._map_line())

        self.gauge.set_volume(self.state.in_tip_ul)
        if self._pose is None:
            self.view.set_position([])
        else:
            self.view.set_position(
                [f"{name} {value:.2f}"
                 for name, value in zip("XYZ", self._pose)])
        self._show_marks(rig.pixel_map(self.device, self._size()))
        # Folded once, as the tip goes on: it opens again for a change of deck.
        if tip_on and not self._tip_was_on:
            self.setup.set_open(False)
        self._tip_was_on = tip_on

    def closeEvent(self, event) -> None:
        if self.rig.lease.busy:
            self.refuse("The robot is still working. Stop it first.")
            event.ignore()
            return
        self._keep_fields()
        self.rig._save()
        self._monitor.stop()
        for worker in (self._looking, self._reading, self._opening,
                       self._lighting):
            if worker is not None:
                worker.wait(3000)
        if self.camera is not None:
            self.camera.close()
        super().closeEvent(event)
