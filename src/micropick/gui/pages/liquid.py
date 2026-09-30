"""Liquid handling: for now, washing cuboids.

Notebook 03 on one page. A plate whose wells already hold cuboids is on the
deck, and the extra liquid is taken off them: the tip goes down beside the
cuboid, near the bottom, draws slowly, rises slowly, and empties itself into
a waste well whenever it would overflow. `workflows.wash` is the procedure;
this page is where its numbers are set and where it is started, paused and
stopped. Other liquid handling will join it here later, so the page is not
called "washing".

The people using it are biologists, not engineers, so every word on it says
what a thing does rather than what it is called in the code, and every
setting has a tooltip. The settings' own words live in the schema
(`WashSettings`), and the form is generated from them.

Which wells
-----------
A saved routine's plan: the wells it fills are the wells that hold a cuboid.
The page starts with the routine open on the Routine page and has its own
Open button, since washing is usually done days after picking. Nothing is
written into the routine: its progress counts cuboids delivered, and a wash
delivers none.

Where in the well - a preset
----------------------------
The centre and the top of a well are found in one act: Go above the well,
jog the tip to the middle of the well, level with its rim, Save centre and
top here. What is stored is the difference from the robot's own idea of the
well top, so it holds wherever the plate sits. The shift off the centre and
the depth of the well sit beside it, and the three together are a named
preset in the profile, made on one kind of plate and refused on another:
two plates with the same name can differ, and a 96 and a 384 always do.

Running it
----------
The run goes through the jog panel's queue like every other robot command
on a page with a panel (`JogPanel.run_job`), so while it runs no key and no
button can move the robot. Pause and Stop are `threading.Event`s the run
checks between moves, and need no queue. What the tip holds and which wells
are done are kept on the page (`WashState`) across a stop: Continue then
empties the tip and carries on with the wells left, and Put the liquid back
returns the last draw to its well - the notebook's remedy for a draw that
took the cuboid with it. Start always begins again from the first well.

By default the run pauses after the first well so the operator can look
into it before the rest of the plate is committed.
"""

from __future__ import annotations

import logging
import re
import threading
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QFileDialog, QFormLayout, QHBoxLayout,
                               QInputDialog, QLabel, QMessageBox, QVBoxLayout,
                               QWidget)

from ... import paths
from ...config.labware import LabwareError, resolve_definition
from ...config.schema import WashSettings, WellPreset
from ...core.routine import Routine, RoutineError
from ...hardware.protocols import xyz
from ...workflows import wash
from ..auto_camera import CameraOpener
from ..session import Session
from ..theme import SPACING
from ..theme.factory import (Section, card, combo_box, double_spin_box,
                             heading, primary_button, scroll_column,
                             secondary_button)
from ..widgets.camera_view import CameraView
from ..widgets.card_columns import CardColumns
from ..widgets.feed_row import FeedRow
from ..widgets.jog_panel import JogPanel
from ..widgets.plate_view import PlateView
from ..widgets.settings_form import field_widget
from .routine import routines_dir

__all__ = ["LiquidHandlingPage"]

TITLE = "Liquid handling"

log = logging.getLogger(__name__)

PANEL_WIDTH = 440

# Where "Go above the well" stops, over the rim, so a centre that is still
# wrong cannot put the tip into the wall.
ABOVE_MM = 5.0

SHIFT_RANGE = (-20.0, 20.0)
DEPTH_RANGE = (0.0, 100.0)

# The settings are saved this long after the last change, so typing "300"
# is one write of 300 and not three.
SAVE_MS = 400

PLATE_HEIGHT = 200

# (key, what it does, handler). One list, bound below and shown in the
# picture's key box.
KEYS = (("P", "pause / continue washing", "_pause_key"),
        ("Esc", "stop washing", "_stop_run"))

CONFIRM_START = (
    "Make sure the plate is in place with its lid off, the waste is in "
    "place, and a tip is on the pipette.\n\n"
    "The robot starts moving as soon as you press Start.")


def _number(parent: QWidget, span, suffix: str, tip: str):
    box = double_spin_box(parent)
    box.setRange(*span)
    box.setDecimals(2)
    box.setSingleStep(0.1)
    box.setSuffix(suffix)
    box.setMinimumWidth(110)
    box.setToolTip(tip)
    return box


def _label(text: str = "", tip: str = "") -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if tip:
        label.setToolTip(tip)
    return label


class LiquidHandlingPage(QWidget):
    # What a job on the jog panel's thread has to tell this page: (kind,
    # payload). A signal, so the widgets are touched by their own thread.
    job_said = Signal(object)

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.routine: Routine | None = None
        # True once a routine was opened here: the Routine page's then no
        # longer replaces it.
        self._own_routine = False
        self.state = wash.WashState()
        # The robot's own top of a well of this plate, (labware id, pose),
        # from the last Go above; what Save centre and top measures against.
        self._nominal = None
        self._pause = threading.Event()
        self._stop = threading.Event()
        self._running = False
        self._can_carry_on = False
        self._checking: str | None = None
        self._progress = ""
        self._outcome = ""
        self._log_path = None
        self._loading = False
        self._described_depth: dict[str, float | None] = {}

        self.opener = CameraOpener(session, self)
        self.view = CameraView(self)
        self.jog = JogPanel(session, shortcut_host=self,
                            collapsed=("positions",), parent=self)
        self.jog.show_position_on(self.view)
        self.jog.add_help([f"{key.lower()}   {what}" for key, what, _ in KEYS])
        # Emitted whenever a job ends, done or failed: the one moment the
        # buttons that were greyed for it can come back.
        self.jog.position_changed.connect(lambda _lines: self._refresh())

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_MS)
        self._save_timer.timeout.connect(self._save_settings)

        panel = CardColumns([self._wells_card(), self._position_card(),
                             self._waste_card(), self._settings_card(),
                             self._run_card(), self.jog], self)
        body = QHBoxLayout()
        body.setSpacing(SPACING)
        body.addWidget(FeedRow(self.view, scroll_column(panel, PANEL_WIDTH)),
                       1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2,
                                  SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addLayout(body, 1)

        self.job_said.connect(self._on_job_said)
        session.profile_changed.connect(lambda _p: self._on_profile_changed())
        session.robot_state_changed.connect(lambda _s: self._robot_changed())
        session.labware_changed.connect(lambda _s: self._reload_labware())
        session.tip_changed.connect(lambda _t: self._lose_top())
        session.routine_changed.connect(self._session_routine)
        session.camera_opened.connect(lambda _l: self._show_camera())
        session.camera_closed.connect(lambda _l: self._show_camera())
        self._install_shortcuts()
        self._on_profile_changed()
        if session.routine is not None:
            self._adopt(session.routine)
        self._refresh()

    # -- construction --------------------------------------------------------

    def _wells_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Cuboid washing", 2))
        box.layout().addWidget(_label(
            "Takes the extra liquid off wells that already hold a cuboid. "
            "The wells to wash are the ones planned in a saved routine."))

        self.open_button = secondary_button("Open routine…", self)
        self.open_button.setToolTip(
            "Choose the saved routine whose wells hold the cuboids. The "
            "routine open on the Routine page is used until you choose one "
            "here.")
        self.open_button.clicked.connect(self._open_routine)
        box.layout().addWidget(self.open_button)

        self.routine_state = _label()
        box.layout().addWidget(self.routine_state)

        # A picture of the plate: outlined wells get washed, filled ones
        # are done. Only to look at - choosing wells is the Routine page's.
        self.plate = PlateView(self)
        self.plate.setFixedHeight(PLATE_HEIGHT)
        self.plate.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.plate.setToolTip("Outlined wells will be washed; filled wells "
                              "are done.")
        box.layout().addWidget(self.plate)
        return box

    def _position_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Where the tip goes in a well", 2))

        row = QHBoxLayout()
        row.addWidget(QLabel("Setting"))
        self.preset_choice = combo_box(self)
        self.preset_choice.setToolTip(
            "A saved set of the numbers below - centre and top, shift and "
            "depth - for one kind of plate. Keep one per plate you use.")
        self.preset_choice.currentIndexChanged.connect(self._preset_chosen)
        row.addWidget(self.preset_choice, 1)
        self.new_preset_button = secondary_button("New…", self)
        self.new_preset_button.setToolTip(
            "Make a new setting for the plate of the open routine. It starts "
            "as a copy of the chosen one if that is for the same plate.")
        self.new_preset_button.clicked.connect(self._new_preset)
        self.delete_preset_button = secondary_button("Delete", self)
        self.delete_preset_button.setToolTip("Delete the chosen setting.")
        self.delete_preset_button.clicked.connect(self._delete_preset)
        row.addWidget(self.new_preset_button)
        row.addWidget(self.delete_preset_button)
        box.layout().addLayout(row)
        self.preset_state = _label()
        box.layout().addWidget(self.preset_state)

        box.layout().addWidget(heading("Centre and top of the well", 3))
        box.layout().addWidget(_label(
            "1. Go above the well.  2. With the arrow keys and PgUp/PgDn, "
            "bring the tip to the middle of the well, level with its rim.  "
            "3. Save centre and top here."))
        row = QHBoxLayout()
        row.addWidget(QLabel("Well"))
        self.aim_choice = combo_box(self)
        self.aim_choice.setToolTip(
            "The well to aim at. Any well of the plate will do; one with a "
            "cuboid in it is easiest to see.")
        row.addWidget(self.aim_choice, 1)
        box.layout().addLayout(row)

        self.above_button = secondary_button("Go above the well", self)
        self.above_button.setToolTip(
            f"The tip goes {ABOVE_MM:g} mm above the middle of the well, as "
            f"far as the robot and the saved setting know it.")
        self.above_button.clicked.connect(self._go_above)
        self.save_centre_button = primary_button("Save centre and top here",
                                                 self)
        self.save_centre_button.setToolTip(
            "Remember where the tip is now as the middle of the well, level "
            "with its rim. Go above the well first.")
        self.save_centre_button.clicked.connect(self._save_centre)
        self.goto_centre_button = secondary_button("Go to the saved centre",
                                                   self)
        self.goto_centre_button.setToolTip(
            "The tip goes to the saved middle of the well, level with the "
            "rim, so you can check it.")
        self.goto_centre_button.clicked.connect(self._goto_centre)
        for button in (self.above_button, self.save_centre_button,
                       self.goto_centre_button):
            box.layout().addWidget(button)
        self.centre_state = _label()
        box.layout().addWidget(self.centre_state)

        box.layout().addWidget(heading("Shift from the centre", 3))
        shift_tip = ("How far from the middle the tip goes down, so it draws "
                     "beside the cuboid instead of over it. Less than the "
                     "well's radius, more than half the cuboid's width. "
                     "Negative X is towards column 1, negative Y towards the "
                     "front of the robot.")
        self.shift_x = _number(self, SHIFT_RANGE, " mm", shift_tip)
        self.shift_y = _number(self, SHIFT_RANGE, " mm", shift_tip)
        self.shift_x.valueChanged.connect(self._shift_edited)
        self.shift_y.valueChanged.connect(self._shift_edited)
        row = QHBoxLayout()
        row.addWidget(QLabel("X"))
        row.addWidget(self.shift_x)
        row.addWidget(QLabel("Y"))
        row.addWidget(self.shift_y)
        row.addStretch(1)
        box.layout().addLayout(row)

        box.layout().addWidget(heading("Depth of the well", 3))
        self.depth = _number(
            self, DEPTH_RANGE, " mm",
            "From the rim of the well down to its bottom. The tip stops "
            "'Tip height above the bottom' above this.")
        self.depth.setSpecialValueText("not set")
        self.depth.valueChanged.connect(self._depth_edited)
        row = QHBoxLayout()
        row.addWidget(QLabel("Depth"))
        row.addWidget(self.depth)
        row.addStretch(1)
        box.layout().addLayout(row)
        self.depth_hint = _label()
        box.layout().addWidget(self.depth_hint)
        self.bottom_button = secondary_button(
            "Use the tip's height as the bottom", self)
        self.bottom_button.setToolTip(
            "Go above the well first, then lower the tip until it just "
            "touches the bottom of an empty well, and press this: the depth "
            "is measured from the saved top.")
        self.bottom_button.clicked.connect(self._measure_depth)
        box.layout().addWidget(self.bottom_button)

        self.show_draw_button = secondary_button(
            "Show where the tip will draw", self)
        self.show_draw_button.setToolTip(
            "The tip goes to the spot beside the centre and down to the "
            "height it draws from. Nothing is drawn.")
        self.show_draw_button.clicked.connect(self._show_draw)
        box.layout().addWidget(self.show_draw_button)
        return box

    def _waste_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Where the liquid goes", 2))
        box.layout().addWidget(_label(
            "A well of any labware on the deck - a reservoir, a spare plate. "
            "Load it on the Robot & Deck page first."))
        self.waste_choice = combo_box(self)
        self.waste_choice.setToolTip("The labware the drawn liquid is "
                                     "emptied into.")
        self.waste_choice.currentIndexChanged.connect(self._waste_chosen)
        self.waste_well = combo_box(self)
        self.waste_well.setToolTip("The well of that labware the liquid is "
                                   "emptied into.")
        self.waste_well.currentIndexChanged.connect(self._waste_well_chosen)
        for label, widget in (("Labware", self.waste_choice),
                              ("Well", self.waste_well)):
            row = QHBoxLayout()
            name = QLabel(label)
            name.setMinimumWidth(60)
            row.addWidget(name)
            row.addWidget(widget, 1)
            box.layout().addLayout(row)
        self.waste_state = _label()
        box.layout().addWidget(self.waste_state)
        return box

    def _settings_card(self) -> QWidget:
        box = Section("Washing settings", parent=self)
        box.body.layout().addWidget(_label(
            "Hover over a setting to see what it does. Changes are saved "
            "into the profile at once."))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._rows = {}
        defaults = WashSettings()
        for name, field in WashSettings.model_fields.items():
            extra = field.json_schema_extra or {}
            row = field_widget(name, field.annotation, getattr(defaults, name),
                               labels=extra.get("labels"),
                               unit=extra.get("unit", ""))
            label = QLabel(field.title)
            label.setWordWrap(True)
            tip = field.description
            shown = extra.get("labels", {}).get(field.default, field.default)
            if isinstance(shown, bool):
                shown = "on" if shown else "off"
            tip += f"\n\nUsual value: {shown:g}" if isinstance(shown, float) \
                else f"\n\nUsual value: {shown}"
            if extra.get("unit") and not isinstance(shown, str):
                tip += f" {extra['unit']}"
            label.setToolTip(tip)
            row.widget.setToolTip(tip)
            row.on_change(self._settings_edited)
            form.addRow(label, row.widget)
            self._rows[name] = row
        box.body.layout().addLayout(form)
        self.settings_state = _label()
        self.settings_state.hide()
        box.body.layout().addWidget(self.settings_state)
        self.defaults_button = secondary_button("Back to the usual values",
                                                self)
        self.defaults_button.setToolTip("Put every washing setting back to "
                                        "its usual value.")
        self.defaults_button.clicked.connect(self._restore_defaults)
        box.body.layout().addWidget(self.defaults_button)
        self.settings_box = box
        return box

    def _run_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Washing", 2))
        self.start_button = primary_button("Start washing", self)
        self.start_button.setToolTip(
            "Wash every planned well, from the first one. Asks before the "
            "robot moves.")
        self.start_button.clicked.connect(self._start)
        box.layout().addWidget(self.start_button)

        row = QHBoxLayout()
        self.pause_button = secondary_button("Pause", self)
        self.pause_button.setToolTip(
            "Hold the robot after the move it is making. Key: P")
        self.pause_button.clicked.connect(self._pause_run)
        self.continue_button = secondary_button("Continue", self)
        self.continue_button.setToolTip(
            "Go on after a pause - or after Stop, with the wells not washed "
            "yet (the tip is emptied first). Key: P")
        self.continue_button.clicked.connect(self._continue)
        self.stop_button = secondary_button("Stop", self)
        self.stop_button.setToolTip(
            "End the run after the move the robot is making; the tip is "
            "raised. What is in the tip stays there. Key: Esc")
        self.stop_button.clicked.connect(self._stop_run)
        for button in (self.pause_button, self.continue_button,
                       self.stop_button):
            row.addWidget(button)
        box.layout().addLayout(row)

        self.put_back_button = secondary_button("Put the liquid back", self)
        self.put_back_button.setToolTip(
            "If a cuboid came up with the liquid: return what is in the tip "
            "to the well it came from, slowly, at the same spot. That well "
            "then counts as not washed.")
        self.put_back_button.clicked.connect(self._put_back)
        self.empty_button = secondary_button("Empty the tip into the waste",
                                             self)
        self.empty_button.setToolTip(
            "Take the tip to the waste well, empty it and shake off the last "
            "drop.")
        self.empty_button.clicked.connect(self._empty_tip)
        box.layout().addWidget(self.put_back_button)
        box.layout().addWidget(self.empty_button)

        self.run_state = _label()
        box.layout().addWidget(self.run_state)
        return box

    def _install_shortcuts(self) -> None:
        """P and Esc, window-wide while this page is showing - enabled only
        on screen, so no hidden page claims the key."""
        self._shortcuts = []
        for key, _what, handler in KEYS:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(getattr(self, handler))
            shortcut.setEnabled(False)
            self._shortcuts.append(shortcut)

    # -- what is in hand -----------------------------------------------------

    def _washing(self):
        profile = self.session.profile
        return None if profile is None else profile.washing

    def _settings(self) -> WashSettings:
        washing = self._washing()
        return washing.settings if washing is not None else WashSettings()

    def _preset_name(self) -> str | None:
        name = self.preset_choice.currentData()
        return name if name else None

    def _preset(self) -> WellPreset | None:
        washing, name = self._washing(), self._preset_name()
        if washing is None or name is None:
            return None
        return washing.presets.get(name)

    def _plate_name(self) -> str | None:
        return (self.routine.destination.load_name
                if self.routine is not None else None)

    def _plate_entry(self):
        """What the run holds in the routine's slot, if it is the routine's
        plate; None otherwise."""
        if self.routine is None or self.session.robot is None:
            return None
        state = self.session.run_state
        slot = str(self.routine.destination.slot)
        entry = state.labware.get(slot) if state is not None else None
        if entry is None or entry.load_name != self._plate_name():
            return None
        return entry

    def _wells(self) -> list[str]:
        """The routine's planned wells, in the chosen order."""
        if self.routine is None:
            return []
        planned = {w for w, count in self.routine.plan.items() if count > 0}
        destination = self.routine.destination
        order = (destination.wells_by_row()
                 if self._settings().order == "by_row"
                 else destination.wells_by_column())
        return [w for w in order if w in planned]

    def _waste(self) -> wash.Waste | None:
        washing, state = self._washing(), self.session.run_state
        if (washing is None or state is None or self.session.robot is None
                or not washing.waste_slot or not washing.waste_well):
            return None
        entry = state.labware.get(washing.waste_slot)
        if entry is None:
            return None
        return wash.Waste(entry.labware_id, washing.waste_well)

    def _save_washing(self) -> None:
        profile = self.session.profile
        if profile is not None:
            profile.save_washing()

    # -- the routine ---------------------------------------------------------

    def _open_routine(self) -> None:
        if self._running:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open a routine", str(routines_dir()),
            "Routine files (*.json)")
        if not path:
            return
        try:
            routine = Routine.load(path)
        except (RoutineError, LabwareError, OSError, KeyError) as exc:
            self.routine_state.setText(f"could not open it: {exc}")
            log.error("washing: could not open %s: %s", path, exc)
            return
        self._own_routine = True
        self._adopt(routine)

    def _session_routine(self, routine) -> None:
        if routine is not None and not self._own_routine and not self._running:
            self._adopt(routine)

    def _adopt(self, routine: Routine) -> None:
        same = (self.routine is not None
                and self.routine.run_id == routine.run_id
                and self.routine.path == routine.path)
        self.routine = routine
        if not same:
            # A different plate: what was washed and where the last draw
            # came from belonged to the other one. What is in the tip is
            # still in the tip.
            self.state.done = []
            self.state.last_well = None
            self._can_carry_on = False
            self._outcome = ""
            self._nominal = None
        self.plate.set_destination(routine.destination)
        wells = self._wells()
        aim = self.aim_choice.currentText()
        self.aim_choice.clear()
        self.aim_choice.addItems(routine.destination.wells_by_row())
        start = aim if routine.destination.contains(aim) else \
            (wells[0] if wells else "")
        if start:
            self.aim_choice.setCurrentText(start)
        self._choose_preset_for_plate()
        log.info("washing: routine %r, %d wells", routine.name, len(wells))
        self._refresh()

    # -- presets ---------------------------------------------------------------

    def _reload_presets(self, wanted: str | None = None) -> None:
        washing = self._washing()
        self._loading = True
        self.preset_choice.clear()
        if washing is not None:
            # The name only: the plate it is for is on the line below, and
            # both do not fit in a combo half the panel wide.
            for name in sorted(washing.presets):
                self.preset_choice.addItem(name, name)
            wanted = wanted or washing.preset
            index = self.preset_choice.findData(wanted)
            if index >= 0:
                self.preset_choice.setCurrentIndex(index)
        self._loading = False
        self._show_preset()

    def _choose_preset_for_plate(self) -> None:
        """Keep the chosen preset if it is for the routine's plate, else the
        first one that is."""
        preset, plate = self._preset(), self._plate_name()
        washing = self._washing()
        if washing is None or plate is None:
            return
        if preset is not None and preset.plate == plate:
            return
        for index in range(self.preset_choice.count()):
            name = self.preset_choice.itemData(index)
            if washing.presets[name].plate == plate:
                self.preset_choice.setCurrentIndex(index)
                return

    def _preset_chosen(self, _index: int) -> None:
        if self._loading:
            return
        washing = self._washing()
        if washing is not None:
            washing.preset = self._preset_name()
            self._save_washing()
        self._show_preset()

    def _show_preset(self) -> None:
        """The chosen preset's numbers into the boxes, without saving."""
        preset = self._preset()
        self._loading = True
        self.shift_x.setValue(preset.shift[0] if preset else 0.0)
        self.shift_y.setValue(preset.shift[1] if preset else 0.0)
        self.depth.setValue(preset.depth_mm if preset and preset.depth_mm
                            else 0.0)
        self._loading = False
        self._refresh()

    def _new_preset(self) -> None:
        washing, plate = self._washing(), self._plate_name()
        if washing is None or plate is None:
            return
        name, ok = QInputDialog.getText(
            self, "New setting",
            f"A name for the new setting for {plate}\n"
            f"(for example the plate's brand):")
        name = name.strip()
        if not ok or not name:
            return
        if name in washing.presets:
            QMessageBox.warning(self, "New setting",
                                f"There is already a setting called {name!r}.")
            return
        current = self._preset()
        if current is not None and current.plate == plate:
            preset = current.model_copy(deep=True)
        else:
            # Numbers from another kind of plate are worse than none.
            preset = WellPreset(plate=plate)
        washing.presets[name] = preset
        washing.preset = name
        self._save_washing()
        log.info("washing: new well setting %r for %s", name, plate)
        self._reload_presets(name)

    def _delete_preset(self) -> None:
        washing, name = self._washing(), self._preset_name()
        if washing is None or name is None:
            return
        answer = QMessageBox.question(
            self, "Delete setting", f"Delete the setting {name!r}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        washing.presets.pop(name, None)
        washing.preset = None
        self._save_washing()
        log.info("washing: deleted well setting %r", name)
        self._reload_presets()
        self._choose_preset_for_plate()

    def _shift_edited(self, _value=None) -> None:
        preset = self._preset()
        if self._loading or preset is None:
            return
        preset.shift = [round(self.shift_x.value(), 3),
                        round(self.shift_y.value(), 3)]
        self._save_washing()
        self._refresh()

    def _depth_edited(self, _value=None) -> None:
        preset = self._preset()
        if self._loading or preset is None:
            return
        value = round(self.depth.value(), 3)
        preset.depth_mm = value if value > 0 else None
        self._save_washing()
        self._refresh()

    def _depth_from_definition(self) -> float | None:
        """What the plate's definition gives as the well depth. Remembered
        per plate: this is asked on every refresh, which is every jog step,
        and the definition is a file."""
        plate = self._plate_name()
        if plate is None:
            return None
        if plate not in self._described_depth:
            try:
                definition = resolve_definition(plate)
                well = definition.wells[0]
                depth = float(definition.data["wells"][well]["depth"])
            except (LabwareError, KeyError, TypeError, ValueError, IndexError):
                depth = None
            self._described_depth[plate] = depth
        return self._described_depth[plate]

    # -- moves for setting up ---------------------------------------------------

    def _move_problems(self) -> list[str]:
        """What stops the tip being sent into a well of the plate."""
        session, out = self.session, []
        if session.robot is None:
            out.append("no robot: connect it on the Profile page.")
        if session.profile is None:
            out.append("no profile loaded.")
            return out
        if self.routine is None:
            out.append("no routine: open the one whose wells hold the "
                       "cuboids.")
        elif session.robot is not None and self._plate_entry() is None:
            dest = self.routine.destination
            out.append(f"slot {dest.slot} does not hold {dest.load_name}, the "
                       f"routine's plate. Load it on the Robot & Deck page.")
        preset = self._preset()
        if preset is None:
            out.append("no setting chosen for where the tip goes: press New… "
                       "to make one.")
        elif self.routine is not None and preset.plate != self._plate_name():
            out.append(f"the chosen setting is for {preset.plate}, not for "
                       f"{self._plate_name()}. Choose or make one for this "
                       f"plate.")
        if session.tip.attached is not True:
            out.append("no tip on the pipette: pick one up on the Robot & "
                       "Deck page."
                       if session.tip.attached is False else
                       "the robot's tip state is unknown: connect again.")
        return out

    def _run_problems(self) -> list[str]:
        out = self._move_problems()
        preset = self._preset()
        if preset is not None:
            if preset.centre is None:
                out.append("the centre and top of the well are not saved yet.")
            if preset.depth_mm is None:
                out.append("the depth of the well is not set.")
        if self.routine is not None and not self._wells():
            out.append("the routine plans no wells.")
        washing = self._washing()
        if washing is not None and (not washing.waste_slot
                                    or not washing.waste_well):
            out.append("no waste chosen: say where the liquid goes.")
        elif washing is not None and self.session.robot is not None \
                and self._waste() is None:
            out.append(f"slot {washing.waste_slot}, where the waste was, "
                       f"holds nothing now. Choose the waste again.")
        slots = set()
        if self.routine is not None:
            slots.add(str(self.routine.destination.slot))
        if washing is not None and washing.waste_slot:
            slots.add(washing.waste_slot)
        for problem in self.session.deck_problems():
            if problem.slot in slots:
                out.append(problem.describe())
        return out

    def _job(self, fn, *, moves: bool = True) -> bool:
        if not self.jog.run_job(fn, moves=moves):
            self.jog.tell("not now: the robot is busy; try again when it is "
                          "done.")
            return False
        self._refresh()
        return True

    def _go_above(self) -> None:
        if self._move_problems() or self._running:
            return
        robot, state, emit = self.session.robot, self.state, self.job_said.emit
        labware_id = self._plate_entry().labware_id
        well = self.aim_choice.currentText()
        offset = wash.centre_offset(self._preset(), ABOVE_MM)

        def job(log_):
            pose = wash.go_to_well(robot, labware_id, well, offset, state,
                                   log=log_)
            nominal = tuple(float(p) - float(o) for p, o in zip(pose, offset))
            emit(("nominal", (labware_id, nominal)))
            return (f"{ABOVE_MM:g} mm above {well}. Bring the tip to the middle "
                    f"of the well, level with its rim, then Save centre and "
                    f"top here.")

        self._job(job)

    def _save_centre(self) -> None:
        if self._move_problems() or self._running or not self._nominal_here():
            return
        robot, emit = self.session.robot, self.job_said.emit
        name = self._preset_name()
        _, nominal = self._nominal

        def job(_log):
            pose = xyz(robot)
            centre = [round(float(p) - float(n), 3)
                      for p, n in zip(pose, nominal)]
            emit(("centre", (name, centre)))
            return "centre and top saved."

        self._job(job, moves=False)

    def _nominal_here(self) -> bool:
        entry = self._plate_entry()
        return (self._nominal is not None and entry is not None
                and self._nominal[0] == entry.labware_id)

    def _goto_centre(self) -> None:
        preset = self._preset()
        if (self._move_problems() or self._running or preset is None
                or preset.centre is None):
            return
        robot, state = self.session.robot, self.state
        labware_id = self._plate_entry().labware_id
        well = self.aim_choice.currentText()
        offset = wash.centre_offset(preset)

        def job(log_):
            wash.go_to_well(robot, labware_id, well, offset, state, log=log_)
            return f"at the saved centre of {well}, level with the rim."

        self._job(job)

    def _measure_depth(self) -> None:
        preset = self._preset()
        if (self._move_problems() or self._running or preset is None
                or preset.centre is None or not self._nominal_here()):
            return
        robot, emit = self.session.robot, self.job_said.emit
        name = self._preset_name()
        top = self._nominal[1][2] + preset.centre[2]

        def job(_log):
            depth = round(top - xyz(robot)[2], 3)
            if depth <= 0:
                raise RuntimeError("the tip is not below the rim of the well; "
                                   "lower it to the bottom first.")
            emit(("depth", (name, depth)))
            return f"depth of the well: {depth:g} mm."

        self._job(job, moves=False)

    def _show_draw(self) -> None:
        preset = self._preset()
        if (self._move_problems() or self._running or preset is None
                or preset.centre is None or preset.depth_mm is None):
            return
        robot, state, settings = self.session.robot, self.state, self._settings()
        labware_id = self._plate_entry().labware_id
        well = self.aim_choice.currentText()

        def job(log_):
            wash.to_draw_height(robot, labware_id, well, preset, settings,
                                state, log=log_)
            return (f"this is where the tip draws in {well}, "
                    f"{settings.above_bottom_mm:g} mm above the bottom. "
                    f"Nothing was drawn.")

        self._job(job)

    # -- the waste --------------------------------------------------------------

    def _reload_labware(self) -> None:
        washing = self._washing()
        self._loading = True
        self.waste_choice.clear()
        for slot, _entry, definition in self.session.plates():
            self.waste_choice.addItem(f"slot {slot} — {definition.display_name}",
                                      (slot, definition))
        if washing is not None and washing.waste_slot:
            for index in range(self.waste_choice.count()):
                if self.waste_choice.itemData(index)[0] == washing.waste_slot:
                    self.waste_choice.setCurrentIndex(index)
        self._loading = False
        self._show_waste_wells()
        self._refresh()

    def _show_waste_wells(self) -> None:
        washing = self._washing()
        data = self.waste_choice.currentData()
        self._loading = True
        self.waste_well.clear()
        if data is not None:
            self.waste_well.addItems(data[1].wells)
            if (washing is not None and washing.waste_slot == data[0]
                    and washing.waste_well in data[1].wells):
                self.waste_well.setCurrentText(washing.waste_well)
        self._loading = False

    def _waste_chosen(self, _index: int) -> None:
        if self._loading:
            return
        self._show_waste_wells()
        self._store_waste()

    def _waste_well_chosen(self, _index: int) -> None:
        if not self._loading:
            self._store_waste()

    def _store_waste(self) -> None:
        washing, data = self._washing(), self.waste_choice.currentData()
        if washing is None or data is None:
            return
        washing.waste_slot = data[0]
        washing.waste_well = self.waste_well.currentText() or None
        self._save_washing()
        log.info("washing: waste is slot %s %s", washing.waste_slot,
                 washing.waste_well)
        self._refresh()

    # -- settings ----------------------------------------------------------------

    def _show_settings(self) -> None:
        settings = self._settings()
        self._loading = True
        for name, row in self._rows.items():
            row.set_value(getattr(settings, name))
        self._loading = False

    def _settings_edited(self) -> None:
        if not self._loading:
            self._save_timer.start()

    def _save_settings(self) -> None:
        washing = self._washing()
        if washing is None:
            return
        try:
            settings = WashSettings(**{name: row.value()
                                       for name, row in self._rows.items()})
        except Exception as exc:                     # noqa: BLE001
            # The model's own words, cut to the sentence that matters.
            lines = [line.strip() for line in str(exc).splitlines()
                     if line.strip().startswith("Value error")]
            self.settings_state.setText(
                "Not saved: " + (lines[0].replace("Value error, ", "")
                                 if lines else str(exc)))
            self.settings_state.show()
            return
        self.settings_state.hide()
        washing.settings = settings
        self._save_washing()
        self._refresh()

    def _restore_defaults(self) -> None:
        defaults = WashSettings()
        self._loading = True
        for name, row in self._rows.items():
            row.set_value(getattr(defaults, name))
        self._loading = False
        self._save_settings()

    # -- running ---------------------------------------------------------------------

    def _remaining(self) -> list[str]:
        return [w for w in self._wells() if w not in self.state.done]

    def _confirm_start(self, wells: list[str]) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Start washing")
        box.setText(f"Wash {len(wells)} wells of {self.routine.name!r}?")
        again = ("\n\nWells washed earlier count as not washed: the run "
                 "starts again from the first well."
                 if self.state.done else "")
        box.setInformativeText(
            f"{self._plate_name()} in slot {self.routine.destination.slot}, "
            f"{self._settings().volume_ul:g} µl from each well, first "
            f"{wells[0]}, last {wells[-1]}.{again}\n\n{CONFIRM_START}")
        start = box.addButton("Start", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is start

    def _start(self) -> None:
        if self._running or self.jog.busy or self._run_problems():
            return
        if not self._confirm_start(self._wells()):
            return
        if self._run_problems():                 # changed while asking
            self._refresh()
            return
        self.state.done = []
        name = re.sub(r"[^\w.-]+", "_", self.routine.name) or "plate"
        self._log_path = (paths.logs_dir()
                          / f"wash_{name}_{time.strftime('%Y%m%d_%H%M%S')}.csv")
        self._launch()

    def _continue(self) -> None:
        if self._running:
            if self._pause.is_set():
                self._pause.clear()
                self._checking = None
                log.info("washing: continued")
                self._refresh()
            return
        if (self._can_carry_on and self._remaining() and not self.jog.busy
                and not self._run_problems()):
            self._launch()

    def _launch(self) -> None:
        robot, emit = self.session.robot, self.job_said.emit
        labware_id = self._plate_entry().labware_id
        wells, preset, settings = self._wells(), self._preset(), self._settings()
        waste, state = self._waste(), self.state
        pause, stop, log_path = self._pause, self._stop, self._log_path
        pause.clear()
        stop.clear()

        def job(log_):
            try:
                wash.run(robot, labware_id, wells, preset, settings, waste,
                         state, pause=pause, stop=stop, log=log_,
                         on_progress=lambda n, total, well:
                         emit(("progress", (n, total, well))),
                         on_paused=lambda well: emit(("paused", well)),
                         log_path=log_path)
            except wash.Stopped:
                emit(("ended", "stopped"))
                return "washing stopped."
            except Exception as exc:
                emit(("ended", f"failed: {exc}"))
                raise
            emit(("ended", "done"))
            return "washing finished."

        if not self.jog.run_job(job):
            self.jog.tell("not now: the robot is busy.")
            return
        self._running = True
        self._can_carry_on = False
        self._checking = None
        self._outcome = ""
        self._progress = "starting: emptying the tip"
        log.info("washing %d wells of %r (%d already done)", len(wells),
                 self.routine.name, len(state.done))
        self._refresh()

    def _pause_run(self) -> None:
        if self._running and not self._pause.is_set():
            self._pause.set()
            log.info("washing: paused")
            self._refresh()

    def _pause_key(self) -> None:
        if self._running and self._pause.is_set():
            self._continue()
        elif self._running:
            self._pause_run()
        else:
            self._continue()

    def _stop_run(self) -> None:
        if self._running and not self._stop.is_set():
            self._stop.set()
            log.info("washing: stop asked")
            self._refresh()

    def _put_back(self) -> None:
        state, preset = self.state, self._preset()
        if (self._running or state.in_tip <= 0 or state.last_well is None
                or self._move_problems() or preset is None
                or preset.centre is None or preset.depth_mm is None):
            return
        robot, settings = self.session.robot, self._settings()
        labware_id = self._plate_entry().labware_id

        def job(log_):
            well = wash.put_back(robot, labware_id, preset, settings, state,
                                 log=log_)
            return f"the liquid is back in {well}."

        if self._job(job):
            self._can_carry_on = True
            self._outcome = ""

    def _empty_tip(self) -> None:
        waste = self._waste()
        if (self._running or waste is None
                or self.session.tip.attached is not True):
            return
        robot, settings, state = self.session.robot, self._settings(), self.state

        def job(log_):
            wash.empty_tip(robot, waste, settings, state, log=log_)
            return "the tip is empty."

        self._job(job)

    def _on_job_said(self, payload) -> None:
        kind, value = payload
        washing = self._washing()
        if kind == "nominal":
            self._nominal = value
        elif kind == "centre" and washing is not None:
            name, centre = value
            if name in washing.presets:
                washing.presets[name].centre = centre
                self._save_washing()
                log.info("washing: centre and top of %r saved: %s", name,
                         centre)
        elif kind == "depth" and washing is not None:
            name, depth = value
            if name in washing.presets:
                washing.presets[name].depth_mm = depth
                self._save_washing()
                log.info("washing: depth of %r: %g mm", name, depth)
                self._show_preset()
        elif kind == "progress":
            n, total, well = value
            self._progress = f"washed {well} ({n} of {total})"
        elif kind == "paused":
            self._checking = value
        elif kind == "ended":
            self._running = False
            self._checking = None
            self._pause.clear()
            self._can_carry_on = value != "done" and bool(self._remaining())
            self._outcome = {"done": "Washing finished.",
                             "stopped": "Stopped."}.get(value, value.capitalize())
            log.info("washing %s", value)
        self._refresh()

    # -- keeping up with the session -----------------------------------------------

    def _on_profile_changed(self) -> None:
        self._reload_presets()
        self._choose_preset_for_plate()
        self._show_settings()
        self._reload_labware()

    def _robot_changed(self) -> None:
        self._lose_top()
        self._nominal = None
        self._reload_labware()

    def _lose_top(self) -> None:
        """The top of the travel depends on the tip and on the run."""
        self.state.z_top = None
        self._refresh()

    def _show_camera(self) -> None:
        self.view.set_camera(self.session.camera(self.session.upper_camera_label
                                                 or ""))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        for shortcut in self._shortcuts:
            shortcut.setEnabled(True)
        self.opener.ensure(self.session.upper_camera_label)
        self._show_camera()
        self._refresh()

    def hideEvent(self, event) -> None:
        """The run keeps going; only the keys are given up."""
        for shortcut in self._shortcuts:
            shortcut.setEnabled(False)
        super().hideEvent(event)

    # -- display ------------------------------------------------------------------

    def _refresh(self) -> None:
        running, busy = self._running, self.jog.busy
        idle = not running and not busy
        washing = self._washing()
        preset = self._preset()
        move_problems = self._move_problems()
        can_move = idle and not move_problems

        # The routine.
        wells = self._wells()
        if self.routine is None:
            self.routine_state.setText("No routine yet. Open the one whose "
                                       "wells hold the cuboids.")
        else:
            dest = self.routine.destination
            self.routine_state.setText(
                f"Routine {self.routine.name!r}: {len(wells)} wells of "
                f"{dest.load_name} in slot {dest.slot}.")
        self.plate.set_plan({w: 1 for w in wells})
        self.plate.set_progress({w: 1 for w in self.state.done if w in wells})
        self.open_button.setEnabled(not running)

        # The preset.
        has_profile = washing is not None
        self.preset_choice.setEnabled(has_profile and not running)
        self.new_preset_button.setEnabled(has_profile and not running
                                          and self.routine is not None)
        self.delete_preset_button.setEnabled(preset is not None and not running)
        for box in (self.shift_x, self.shift_y, self.depth):
            box.setEnabled(preset is not None and not running)
        if not has_profile:
            self.preset_state.setText("No profile loaded.")
        elif preset is None:
            self.preset_state.setText(
                "No setting chosen. Press New… to make one for this plate."
                if self.routine is not None else
                "Open a routine first: a setting belongs to a kind of plate.")
        elif self.routine is not None and preset.plate != self._plate_name():
            self.preset_state.setText(
                f"This setting is for {preset.plate}, but the routine's plate "
                f"is {self._plate_name()}. Choose or make another one.")
        else:
            self.preset_state.setText(f"For {preset.plate}.")

        self.above_button.setEnabled(can_move)
        self.save_centre_button.setEnabled(can_move and self._nominal_here())
        has_centre = preset is not None and preset.centre is not None
        self.goto_centre_button.setEnabled(can_move and has_centre)
        self.bottom_button.setEnabled(can_move and has_centre
                                      and self._nominal_here())
        self.show_draw_button.setEnabled(
            can_move and has_centre and preset.depth_mm is not None)
        if has_centre:
            x, y, z = preset.centre
            self.centre_state.setText(
                f"Saved: {x:+.2f} mm in X, {y:+.2f} mm in Y and {z:+.2f} mm in "
                f"Z from where the robot thinks the top of the well is.")
        elif preset is not None:
            self.centre_state.setText("Not saved yet.")
        else:
            self.centre_state.setText("")
        if move_problems and idle:
            self.centre_state.setText(
                self.centre_state.text()
                + ("\n" if self.centre_state.text() else "")
                + "To move the tip: " + move_problems[0])
        hint = self._depth_from_definition()
        self.depth_hint.setText(
            f"The plate's description says {hint:g} mm; check it on your "
            f"plate." if hint is not None else "")

        # The waste.
        self.waste_choice.setEnabled(has_profile and not running)
        self.waste_well.setEnabled(has_profile and not running)
        if self.waste_choice.count() == 0:
            self.waste_state.setText(
                "Nothing on the deck to empty into. Load a reservoir or a "
                "plate on the Robot & Deck page."
                if self.session.robot is not None else
                "No robot: connect it on the Profile page.")
        else:
            self.waste_state.setText("")
        self.waste_state.setVisible(bool(self.waste_state.text()))

        # The settings.
        self.settings_box.body.setEnabled(has_profile and not running)

        # The run.
        problems = self._run_problems()
        paused = running and self._pause.is_set()
        self.start_button.setEnabled(idle and not problems)
        self.pause_button.setEnabled(running and not paused)
        self.continue_button.setEnabled(
            paused or (idle and self._can_carry_on and bool(self._remaining())
                       and not problems))
        self.stop_button.setEnabled(running)
        state = self.state
        self.put_back_button.setEnabled(
            idle and state.in_tip > 0 and state.last_well is not None
            and not move_problems and has_centre
            and preset.depth_mm is not None)
        self.put_back_button.setText(
            f"Put the liquid back into {state.last_well}"
            if state.last_well and state.in_tip > 0 else "Put the liquid back")
        self.empty_button.setEnabled(idle and self._waste() is not None
                                     and self.session.tip.attached is True)

        in_tip = f"The tip holds {state.in_tip:g} µl." if state.in_tip > 0 \
            else "The tip is empty."
        done = len([w for w in state.done if w in wells])
        if running:
            if self._checking:
                text = (f"Paused after the first well. Look into "
                        f"{self._checking}: if the cuboid is still there, "
                        f"press Continue. If it came up with the liquid, "
                        f"press Stop, then Put the liquid back, and change "
                        f"the shift, the tip height or the suction speed.")
            elif paused:
                text = "Paused. Press Continue to go on."
            elif self._stop.is_set():
                text = "Stopping after the move the robot is making…"
            else:
                text = f"Washing: {self._progress}."
            text += f"\n{done} of {len(wells)} wells done. {in_tip}"
        elif problems:
            text = "\n".join("• " + p for p in problems)
        else:
            text = ((self._outcome + " ") if self._outcome else "") + \
                f"{done} of {len(wells)} wells done. {in_tip}"
            if self._can_carry_on and self._remaining():
                text += (f"\nContinue washes the {len(self._remaining())} "
                         f"wells left, emptying the tip first.")
        self.run_state.setText(text)

        if running:
            lines = [f"washing: {self._progress}",
                     f"{done} of {len(wells)} wells done",
                     f"in the tip: {state.in_tip:g} µl"]
            if paused:
                lines.append("PAUSED")
            self.view.set_status(lines)
        else:
            self.view.set_status([])
