"""Liquid handling: moving liquid between wells, built from blocks.

A program (`core.liquid`) is a list of groups. A group is wells of one plate
on the deck, chosen on the plate map and drawn in one colour, and the steps
every one of those wells gets - Aspirate, Dispense, Move to, Mix, Blow out,
Wait, Pause - each saying where it happens: the group's well, one fixed well
of any labware on the deck, a position saved in the profile, or where the tip
is. The run takes the groups top to bottom and, for each well of a group,
does all its steps before the next well (`workflows.liquid`).

The page, top to bottom
-----------------------
The plate map is the labware the run holds: select wells with the mouse (as
on the Plate plan page), make a group of them or add them to the chosen one. A
well belongs to one group of its plate at most, so adding it to one takes it
out of another. Under the groups, the chosen group's steps, and under those
the chosen step's fields. "Try this step" does that one step for one well -
the selected one if it is in the group, else the group's first - and leaves
the tip there to be looked at.

Saved points are the profile's positions, the same list as the jog panel's
Positions: jog the tip to the spot, save it there, and it is on offer as a
location at once. The "points" box on the picture draws them where they are.

The program is saved as it is edited, into `outputs/liquid/current.json`,
and comes back on the next start; Save as and Open keep named copies beside
it.

Running it
----------
Through the jog panel's queue like every robot command on a page with a
panel (`JogPanel.run_job`), so while it runs no key and no button can move
the robot. Pause and Stop are `threading.Event`s the run checks between
commands. What the tip holds and how far the run got are kept on the page
(`LiquidState`) across a stop: Continue carries on from the step after the
last one done, Start always begins again from the first well. Changing the
groups or their wells forgets that progress; changing a step's numbers does
not, so a flow rate can be corrected between Stop and Continue.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QColorDialog, QFileDialog, QHBoxLayout,
                               QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox,
                               QVBoxLayout, QWidget)

from ... import paths
from ...core.liquid import (ACTIONS, Group, Program, ProgramError, describe,
                            new_step, ordered_wells)
from ...core.routine import Destination, RoutineError
from ...workflows import liquid
from ...workflows import manual as moves
from ..auto_camera import CameraOpener
from ..session import Session
from ..theme import SPACING
from ..theme.factory import (card, combo_box, heading, primary_button,
                             scroll_column, secondary_button)
from ..widgets.camera_view import CameraView
from ..widgets.card_columns import CardColumns
from ..widgets.feed_row import FeedRow
from ..widgets.jog_panel import JogPanel
from ..widgets.liquid_steps import StepEditor
from ..widgets.plate_view import PlateView

__all__ = ["LiquidHandlingPage", "programs_dir", "CURRENT_FILE"]

TITLE = "Liquid handling"

log = logging.getLogger(__name__)

PANEL_WIDTH = 460
PLATE_HEIGHT = 220
LIST_HEIGHT = 120

# The program is saved this long after the last edit, so typing "300" is one
# write of 300 and not three.
SAVE_MS = 400

CURRENT_FILE = "current.json"

# (key, what it does, handler). One list, bound below and shown in the
# picture's key box.
KEYS = (("P", "pause / continue the run", "_pause_key"),
        ("Esc", "stop the run", "_stop_run"))

CONFIRM_START = (
    "Make sure every plate is in place with its lid off, and a tip is on the "
    "pipette.\n\nThe robot starts moving as soon as you press Start.")

# The steps that need a tip on the pipette. A move does not.
LIQUID_ACTIONS = ("aspirate", "dispense", "mix", "blow_out")


def programs_dir():
    directory = paths.outputs_dir() / "liquid"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _swatch(colour: str) -> QIcon:
    pixmap = QPixmap(12, 12)
    pixmap.fill(QColor(colour))
    return QIcon(pixmap)


def _list(parent: QWidget) -> QListWidget:
    widget = QListWidget(parent)
    # PageUp and PageDown are the Z axis here; a list with focus would
    # scroll on them instead.
    widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    widget.setMinimumHeight(LIST_HEIGHT)
    return widget


class LiquidHandlingPage(QWidget):
    # What a job on the jog panel's thread has to tell this page: (kind,
    # payload). A signal, so the widgets are touched by their own thread.
    job_said = Signal(object)

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.program = self._load_current()
        self.state = liquid.LiquidState()
        self._path = None                    # the last Open or Save as
        self._pause = threading.Event()
        self._stop = threading.Event()
        self._running = False
        self._can_carry_on = False
        self._checking: str | None = None
        self._progress = ""
        self._outcome = ""
        self._current: tuple[int, str] | None = None
        self._log_path = None
        self._loading = False
        # session.plates(), read when the deck changes rather than on every
        # refresh: each entry is a definition file resolved from disk.
        self._deck = session.plates()

        self.opener = CameraOpener(session, self)
        self.view = CameraView(self)
        self.jog = JogPanel(session, shortcut_host=self, parent=self)
        self.jog.show_position_on(self.view)
        self.jog.add_help([f"{key.lower()}   {what}" for key, what, _ in KEYS])
        # Emitted whenever a job ends, done or failed: the one moment the
        # buttons that were greyed for it can come back.
        self.jog.position_changed.connect(lambda _lines: self._refresh())

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_MS)
        self._save_timer.timeout.connect(self._save_current)

        self._editing = [self._plate_card(), self._groups_card(),
                         self._steps_card(), self._program_card()]
        panel = CardColumns(self._editing + [self._run_card(), self.jog], self)
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
        session.profile_changed.connect(lambda _p: self._offer_choices())
        session.robot_state_changed.connect(lambda _s: self._deck_changed())
        session.labware_changed.connect(lambda _s: self._deck_changed())
        session.tip_changed.connect(lambda _t: self._lose_top())
        session.camera_opened.connect(lambda _l: self._show_camera())
        session.camera_closed.connect(lambda _l: self._show_camera())
        self._install_shortcuts()
        self._reload_plates()
        self._show_groups()
        self._offer_choices()

    # -- construction --------------------------------------------------------

    def _plate_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Wells", 2))
        box.layout().addWidget(_label(
            "Select wells on the plate - click, drag a box, or click a row or "
            "column label; Ctrl adds, Shift removes - then make a group of "
            "them."))
        row = QHBoxLayout()
        row.addWidget(QLabel("Plate"))
        self.plate_choice = combo_box(self)
        self.plate_choice.setToolTip("The labware the robot holds. Load more "
                                     "on the Robot & Deck page.")
        self.plate_choice.currentIndexChanged.connect(
            lambda _i: self._show_plate())
        row.addWidget(self.plate_choice, 1)
        box.layout().addLayout(row)

        self.plate = PlateView(self)
        self.plate.setFixedHeight(PLATE_HEIGHT)
        self.plate.selection_changed.connect(lambda _s: self._refresh())
        box.layout().addWidget(self.plate)

        row = QHBoxLayout()
        self.new_group_button = primary_button("New group", self)
        self.new_group_button.setToolTip("A new group of the selected wells.")
        self.new_group_button.clicked.connect(self._new_group)
        self.add_wells_button = secondary_button("Add to group", self)
        self.add_wells_button.setToolTip("Add the selected wells to the chosen "
                                         "group, taking them out of any other.")
        self.add_wells_button.clicked.connect(self._add_wells)
        self.remove_wells_button = secondary_button("Remove", self)
        self.remove_wells_button.setToolTip("Take the selected wells out of "
                                            "every group.")
        self.remove_wells_button.clicked.connect(self._remove_wells)
        for button in (self.new_group_button, self.add_wells_button,
                       self.remove_wells_button):
            row.addWidget(button)
        box.layout().addLayout(row)
        self.plate_state = _label()
        box.layout().addWidget(self.plate_state)
        return box

    def _groups_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Groups", 2))
        box.layout().addWidget(_label("Run top to bottom; each well of a group "
                                      "gets all of its steps before the next "
                                      "well."))
        self.groups = _list(self)
        self.groups.currentRowChanged.connect(lambda _r: self._group_chosen())
        box.layout().addWidget(self.groups)

        row = QHBoxLayout()
        self.rename_group_button = secondary_button("Rename…", self)
        self.rename_group_button.clicked.connect(self._rename_group)
        self.colour_button = secondary_button("Colour…", self)
        self.colour_button.clicked.connect(self._recolour_group)
        self.delete_group_button = secondary_button("Delete", self)
        self.delete_group_button.clicked.connect(self._delete_group)
        for button in (self.rename_group_button, self.colour_button,
                       self.delete_group_button):
            row.addWidget(button)
        box.layout().addLayout(row)

        row = QHBoxLayout()
        self.group_up = secondary_button("↑", self)
        self.group_up.setToolTip("Run this group earlier.")
        self.group_up.clicked.connect(lambda: self._move_group(-1))
        self.group_down = secondary_button("↓", self)
        self.group_down.setToolTip("Run this group later.")
        self.group_down.clicked.connect(lambda: self._move_group(+1))
        self.order_choice = combo_box(self)
        self.order_choice.addItem("Row by row", "by_row")
        self.order_choice.addItem("Column by column", "by_column")
        self.order_choice.setToolTip("Row by row goes A1, A2, A3 ... then B1. "
                                     "Column by column goes A1, B1, C1 ... "
                                     "then A2.")
        self.order_choice.currentIndexChanged.connect(self._order_chosen)
        row.addWidget(self.group_up)
        row.addWidget(self.group_down)
        row.addWidget(self.order_choice, 1)
        box.layout().addLayout(row)
        return box

    def _steps_card(self) -> QWidget:
        box = card(self)
        self.steps_heading = heading("Steps", 2)
        box.layout().addWidget(self.steps_heading)
        self.steps = _list(self)
        self.steps.currentRowChanged.connect(lambda _r: self._step_chosen())
        box.layout().addWidget(self.steps)

        row = QHBoxLayout()
        self.add_step_button = primary_button("Add step ▾", self)
        menu = QMenu(self.add_step_button)
        for action, _cls, title in ACTIONS:
            menu.addAction(title, lambda a=action: self._add_step(a))
        self.add_step_button.setMenu(menu)
        self.step_up = secondary_button("↑", self)
        self.step_up.clicked.connect(lambda: self._move_step(-1))
        self.step_down = secondary_button("↓", self)
        self.step_down.clicked.connect(lambda: self._move_step(+1))
        self.copy_step_button = secondary_button("Copy", self)
        self.copy_step_button.setToolTip("Put a copy of this step under it.")
        self.copy_step_button.clicked.connect(self._copy_step)
        self.delete_step_button = secondary_button("Delete", self)
        self.delete_step_button.clicked.connect(self._delete_step)
        for button in (self.add_step_button, self.step_up, self.step_down,
                       self.copy_step_button, self.delete_step_button):
            row.addWidget(button)
        box.layout().addLayout(row)

        self.editor = StepEditor(self)
        self.editor.changed.connect(self._step_edited)
        box.layout().addWidget(self.editor)

        self.try_button = secondary_button("Try this step", self)
        self.try_button.setToolTip(
            "Do this one step now, for the selected well if it is in the "
            "group, else for the group's first well. The tip stays where the "
            "step leaves it.")
        self.try_button.clicked.connect(self._try_step)
        box.layout().addWidget(self.try_button)
        self.steps_state = _label()
        box.layout().addWidget(self.steps_state)
        return box

    def _program_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Program", 2))
        row = QHBoxLayout()
        self.new_program_button = secondary_button("New", self)
        self.new_program_button.clicked.connect(self._new_program)
        self.open_button = secondary_button("Open…", self)
        self.open_button.clicked.connect(self._open_program)
        self.save_as_button = secondary_button("Save as…", self)
        self.save_as_button.clicked.connect(self._save_as)
        for button in (self.new_program_button, self.open_button,
                       self.save_as_button):
            row.addWidget(button)
        box.layout().addLayout(row)
        self.program_state = _label()
        box.layout().addWidget(self.program_state)
        return box

    def _run_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Run", 2))
        self.start_button = primary_button("Start", self)
        self.start_button.setToolTip("Run the program from the first well. "
                                     "Asks before the robot moves.")
        self.start_button.clicked.connect(self._start)
        box.layout().addWidget(self.start_button)
        row = QHBoxLayout()
        self.pause_button = secondary_button("Pause", self)
        self.pause_button.setToolTip("Hold the robot after the command it is "
                                     "doing. Key: P")
        self.pause_button.clicked.connect(self._pause_run)
        self.continue_button = secondary_button("Continue", self)
        self.continue_button.setToolTip("Go on after a pause, or after Stop "
                                        "from the step after the last one "
                                        "done. Key: P")
        self.continue_button.clicked.connect(self._continue)
        self.stop_button = secondary_button("Stop", self)
        self.stop_button.setToolTip("End the run after the command the robot "
                                    "is doing; the tip is raised and keeps "
                                    "what it holds. Key: Esc")
        self.stop_button.clicked.connect(self._stop_run)
        for button in (self.pause_button, self.continue_button,
                       self.stop_button):
            row.addWidget(button)
        box.layout().addLayout(row)
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

    def _plates(self) -> dict[str, liquid.Plate]:
        return {slot: liquid.Plate(slot, entry.labware_id, entry.load_name,
                                   definition.ordering)
                for slot, entry, definition in self._deck}

    def _positions(self) -> dict:
        profile = self.session.profile
        return dict(profile.positions or {}) if profile is not None else {}

    def _group_index(self) -> int | None:
        row = self.groups.currentRow()
        return row if 0 <= row < len(self.program.groups) else None

    def _group(self) -> Group | None:
        index = self._group_index()
        return None if index is None else self.program.groups[index]

    def _step_index(self) -> int | None:
        group, row = self._group(), self.steps.currentRow()
        return row if group is not None and 0 <= row < len(group.steps) else None

    def _shown_plate(self):
        """(slot, load name) of the plate on the map, or None."""
        data = self.plate_choice.currentData()
        return None if data is None else (data[0], data[1].load_name)

    def _on_shown_plate(self, group: Group) -> bool:
        return self._shown_plate() == (group.slot, group.load_name)

    # -- the program file ----------------------------------------------------

    def _load_current(self) -> Program:
        path = programs_dir() / CURRENT_FILE
        if not path.is_file():
            return Program()
        try:
            return Program.load(path)
        except ProgramError as exc:
            log.error("liquid handling: could not read %s: %s", path, exc)
            return Program()

    def _save_current(self) -> None:
        try:
            self.program.save(programs_dir() / CURRENT_FILE)
        except OSError as exc:
            log.error("liquid handling: could not save the program: %s", exc)

    def _changed(self, forget: str = "") -> None:
        """After any edit. `forget` "all" drops the run's progress (the
        groups or their wells changed, so the indices no longer mean the
        same wells); "steps" only the halfway points of wells."""
        if forget == "all":
            self.state.done = []
            self.state.resume = {}
            self._can_carry_on = False
        elif forget == "steps":
            self.state.resume = {}
        self._save_timer.start()
        self._show_groups()

    def _confirm_replace(self, what: str) -> bool:
        if self.program.empty:
            return True
        answer = QMessageBox.question(
            self, what, "Replace the program on the page? It is kept only if "
            "it was saved with Save as.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def _adopt(self, program: Program, path=None) -> None:
        self.program = program
        self._path = path
        self.state.reset()
        self._can_carry_on = False
        self._outcome = ""
        self._save_current()
        self._show_groups(select=0)

    def _new_program(self) -> None:
        if not self._running and self._confirm_replace("New program"):
            self._adopt(Program())

    def _open_program(self) -> None:
        if self._running or not self._confirm_replace("Open a program"):
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open a program", str(programs_dir()),
            "Liquid handling programs (*.json)")
        if not path:
            return
        try:
            program = Program.load(path)
        except (ProgramError, OSError) as exc:
            QMessageBox.warning(self, "Open a program", str(exc))
            return
        log.info("liquid handling: opened %s", path)
        self._adopt(program, path)

    def _save_as(self) -> None:
        start = self._path or str(programs_dir() / f"{self.program.name or 'program'}.json")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save the program", start,
            "Liquid handling programs (*.json)")
        if not path:
            return
        if not path.endswith(".json"):
            path += ".json"
        self.program.name = Path(path).stem
        try:
            self.program.save(path)
        except OSError as exc:
            QMessageBox.warning(self, "Save the program", str(exc))
            return
        self._path = path
        self._save_current()
        log.info("liquid handling: saved %s", path)
        self._refresh()

    # -- the plate map ---------------------------------------------------------

    def _reload_plates(self) -> None:
        shown = self._shown_plate()
        self._deck = self.session.plates()
        self._loading = True
        self.plate_choice.clear()
        for slot, _entry, definition in self._deck:
            self.plate_choice.addItem(f"slot {slot} — {definition.display_name}",
                                      (slot, definition))
        for index in range(self.plate_choice.count()):
            slot, definition = self.plate_choice.itemData(index)
            if shown == (slot, definition.load_name):
                self.plate_choice.setCurrentIndex(index)
        self._loading = False
        self._show_plate()

    def _show_plate(self) -> None:
        if self._loading:
            return
        data = self.plate_choice.currentData()
        destination = None
        if data is not None:
            slot, definition = data
            try:
                destination = Destination.from_definition(definition, int(slot))
            except (RoutineError, ValueError) as exc:
                log.error("liquid handling: cannot draw slot %s: %s", slot, exc)
        self.plate.set_destination(destination)
        group = self._group()
        if group is not None and self._on_shown_plate(group):
            self.plate.set_selection(group.wells)
        self._paint_plate()
        self._refresh()

    def _paint_plate(self) -> None:
        shown = self._shown_plate()
        colours, done = {}, set()
        for gi, group in enumerate(self.program.groups):
            if (group.slot, group.load_name) != shown:
                continue
            for well in group.wells:
                colours[well] = group.color
                if (gi, well) in self.state.done:
                    done.add(well)
        self.plate.set_colours(colours)
        self.plate.set_done(done)
        current = self._current
        self.plate.set_current(
            current[1] if current is not None
            and current[0] < len(self.program.groups)
            and (self.program.groups[current[0]].slot,
                 self.program.groups[current[0]].load_name) == shown
            else None)

    def _deck_changed(self) -> None:
        self._lose_top()
        self._reload_plates()
        self._offer_choices()

    # -- groups ----------------------------------------------------------------

    def _show_groups(self, select: int | None = None) -> None:
        """The list from the program, the chosen row kept."""
        row = self.groups.currentRow() if select is None else select
        self._loading = True
        self.groups.clear()
        for group in self.program.groups:
            text = (f"{group.name} — slot {group.slot}, {len(group.wells)} "
                    f"wells, {len(group.steps)} steps")
            self.groups.addItem(QListWidgetItem(_swatch(group.color), text))
        if self.program.groups:
            self.groups.setCurrentRow(max(0, min(row, len(self.program.groups) - 1)))
        self._loading = False
        self._group_chosen(follow=select is not None)

    def _group_chosen(self, follow: bool = True) -> None:
        if self._loading:
            return
        group = self._group()
        self._loading = True
        if group is not None:
            self.order_choice.setCurrentIndex(
                max(0, self.order_choice.findData(group.order)))
        self._loading = False
        if follow and group is not None:
            # The map goes to the group's plate, with its wells selected.
            for index in range(self.plate_choice.count()):
                slot, definition = self.plate_choice.itemData(index)
                if (slot, definition.load_name) == (group.slot, group.load_name):
                    if index != self.plate_choice.currentIndex():
                        self.plate_choice.setCurrentIndex(index)
                    break
            if self._on_shown_plate(group):
                self.plate.set_selection(group.wells)
        self._show_steps()
        self._paint_plate()

    def _take_wells(self, slot: str, load_name: str, wells: set[str],
                    keep: Group | None = None) -> None:
        """Out of every group of that plate but `keep`: a well is in one
        group of its plate at most."""
        for group in self.program.groups:
            if group is not keep and (group.slot, group.load_name) == (slot, load_name):
                group.wells = [w for w in group.wells if w not in wells]

    def _new_group(self) -> None:
        shown, wells = self._shown_plate(), self.plate.selection
        if self._running or shown is None or not wells:
            return
        slot, load_name = shown
        self._take_wells(slot, load_name, wells)
        name = f"Group {len(self.program.groups) + 1}"
        self.program.groups.append(Group(
            name=name, color=self.program.next_colour(), slot=slot,
            load_name=load_name,
            wells=[w for w in self.plate.wells if w in wells]))
        log.info("liquid handling: %s of %d wells in slot %s", name, len(wells),
                 slot)
        self._changed("all")
        self._show_groups(select=len(self.program.groups) - 1)

    def _add_wells(self) -> None:
        group, wells = self._group(), self.plate.selection
        if self._running or group is None or not wells \
                or not self._on_shown_plate(group):
            return
        self._take_wells(group.slot, group.load_name, wells, keep=group)
        group.wells = [w for w in self.plate.wells
                       if w in wells or w in set(group.wells)]
        self._changed("all")

    def _remove_wells(self) -> None:
        shown, wells = self._shown_plate(), self.plate.selection
        if self._running or shown is None or not wells:
            return
        self._take_wells(*shown, wells)
        self._changed("all")

    def _rename_group(self) -> None:
        group = self._group()
        if group is None or self._running:
            return
        name, ok = QInputDialog.getText(self, "Rename group", "Name:",
                                        text=group.name)
        if ok and name.strip():
            group.name = name.strip()
            self._changed()

    def _recolour_group(self) -> None:
        group = self._group()
        if group is None or self._running:
            return
        colour = QColorDialog.getColor(QColor(group.color), self, "Group colour")
        if colour.isValid():
            group.color = colour.name()
            self._changed()

    def _delete_group(self) -> None:
        index = self._group_index()
        if index is None or self._running:
            return
        answer = QMessageBox.question(
            self, "Delete group",
            f"Delete {self.program.groups[index].name!r} and its steps?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        del self.program.groups[index]
        self._changed("all")
        self._show_groups(select=max(0, index - 1))

    def _move_group(self, by: int) -> None:
        index = self._group_index()
        if index is None or self._running:
            return
        other = index + by
        if not 0 <= other < len(self.program.groups):
            return
        groups = self.program.groups
        groups[index], groups[other] = groups[other], groups[index]
        self._changed("all")
        self._show_groups(select=other)

    def _order_chosen(self, _index: int) -> None:
        group = self._group()
        if self._loading or group is None:
            return
        group.order = self.order_choice.currentData()
        self._changed("all")

    # -- steps -------------------------------------------------------------------

    def _show_steps(self, select: int | None = None) -> None:
        group = self._group()
        row = self.steps.currentRow() if select is None else select
        self._loading = True
        self.steps.clear()
        if group is not None:
            for n, step in enumerate(group.steps, 1):
                self.steps.addItem(f"{n}. {describe(step)}")
            if group.steps:
                self.steps.setCurrentRow(max(0, min(row, len(group.steps) - 1)))
        self.steps_heading.setText(f"Steps of {group.name}" if group is not None
                                   else "Steps")
        self._loading = False
        self._step_chosen()

    def _step_chosen(self) -> None:
        if self._loading:
            return
        group, index = self._group(), self._step_index()
        self.editor.set_step(group.steps[index] if index is not None else None)
        self._refresh()

    def _add_step(self, action: str) -> None:
        group = self._group()
        if group is None or self._running:
            return
        index = self._step_index()
        at = len(group.steps) if index is None else index + 1
        group.steps.insert(at, new_step(action))
        self._changed("steps")
        self._show_steps(select=at)

    def _copy_step(self) -> None:
        group, index = self._group(), self._step_index()
        if index is None or self._running:
            return
        group.steps.insert(index + 1, group.steps[index].model_copy(deep=True))
        self._changed("steps")
        self._show_steps(select=index + 1)

    def _delete_step(self) -> None:
        group, index = self._group(), self._step_index()
        if index is None or self._running:
            return
        del group.steps[index]
        self._changed("steps")
        self._show_steps(select=max(0, index - 1))

    def _move_step(self, by: int) -> None:
        group, index = self._group(), self._step_index()
        if index is None or self._running:
            return
        other = index + by
        if not 0 <= other < len(group.steps):
            return
        group.steps[index], group.steps[other] = group.steps[other], group.steps[index]
        self._changed("steps")
        self._show_steps(select=other)

    def _step_edited(self, step) -> None:
        group, index = self._group(), self._step_index()
        if index is None:
            return
        group.steps[index] = step
        item = self.steps.item(index)
        if item is not None:
            item.setText(f"{index + 1}. {describe(step)}")
        self._save_timer.start()
        self._refresh()

    def _offer_choices(self) -> None:
        """The labware and the saved points a location can name."""
        plates = []
        for slot, _entry, definition in self._deck:
            destination_rows = max((len(c) for c in definition.ordering),
                                   default=0)
            wells = [column[r] for r in range(destination_rows)
                     for column in definition.ordering if r < len(column)]
            plates.append((slot, definition.load_name,
                           f"slot {slot} — {definition.display_name}", wells))
        self.editor.set_choices(plates, list(self._positions()))
        self._refresh()

    def _try_well(self, group: Group) -> str | None:
        plate = self._plates().get(group.slot)
        if plate is None:
            return None
        wells = ordered_wells(group, plate.ordering)
        chosen = [w for w in wells if w in self.plate.selection] \
            if self._on_shown_plate(group) else []
        return (chosen or wells or [None])[0]

    def _try_problems(self) -> list[str]:
        group, index = self._group(), self._step_index()
        if index is None:
            return ["choose a step."]
        step = group.steps[index]
        out = self._robot_problems(tip=step.action in LIQUID_ACTIONS)
        single = Program(groups=[group.model_copy(update={"steps": [step]})])
        out += liquid.problems(single, self._plates(), self._positions(),
                               volumes=False)
        out += self._reach_problems(single)
        return out

    def _try_step(self) -> None:
        if self._running or self.jog.busy or self._try_problems():
            return
        group, index = self._group(), self._step_index()
        step, well = group.steps[index], self._try_well(group)
        robot, state = self.session.robot, self.state
        plates, positions = self._plates(), self._positions()

        def job(log_):
            said = liquid.run_step(robot, step, group, well, plates, positions,
                                   state, log=log_)
            return f"{describe(step).split(' @ ')[0]} for {well}: {said}."

        log.info("liquid handling: trying step %d of %s for %s", index + 1,
                 group.name, well)
        if not self.jog.run_job(job):
            self.jog.tell("not now: the robot is busy.")

    # -- checks -------------------------------------------------------------------

    def _robot_problems(self, *, tip: bool = True) -> list[str]:
        session, out = self.session, []
        if session.robot is None:
            out.append("no robot: connect it on the Profile page.")
            return out
        if tip and session.tip.attached is not True:
            out.append("no tip on the pipette: pick one up on the Robot & Deck "
                       "page." if session.tip.attached is False else
                       "the robot's tip state is unknown: connect again.")
        return out

    def _reach_problems(self, program: Program) -> list[str]:
        out, positions = [], self._positions()
        limits = self.session.jog_limits
        for name in sorted(liquid.points_used(program)):
            if name in positions:
                why = moves.unreachable(limits, positions[name])
                if why:
                    out.append(f"point {name!r} is outside the soft limits: "
                               f"{why}.")
        slots = liquid.slots_used(program)
        out += [p.describe() for p in self.session.deck_problems()
                if p.slot in slots]
        return out

    def _run_problems(self) -> list[str]:
        out = self._robot_problems()
        out += liquid.problems(self.program, self._plates(), self._positions())
        out += self._reach_problems(self.program)
        return out

    # -- running ---------------------------------------------------------------------

    def _total(self) -> int:
        try:
            return len(liquid.plan(self.program, self._plates()))
        except ValueError:
            return 0

    def _confirm_start(self) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Start")
        groups = self.program.groups
        box.setText(f"Run {len(groups)} group{'s' if len(groups) != 1 else ''}"
                    f", {self._total()} wells?")
        lines = [f"{g.name}: {len(g.wells)} wells in slot {g.slot}, "
                 f"{len(g.steps)} steps" for g in groups]
        again = ("\n\nWells done earlier count as not done: the run starts "
                 "again from the first well." if self.state.done else "")
        box.setInformativeText("\n".join(lines) + again + "\n\n" + CONFIRM_START)
        start = box.addButton("Start", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is start

    def _start(self) -> None:
        if self._running or self.jog.busy or self._run_problems():
            return
        if not self._confirm_start():
            return
        if self._run_problems():                 # changed while asking
            self._refresh()
            return
        self.state.reset()
        self._log_path = (paths.logs_dir() / f"liquid_"
                          f"{time.strftime('%Y%m%d_%H%M%S')}.csv")
        self._launch()

    def _continue(self) -> None:
        if self._running:
            if self._pause.is_set():
                self._pause.clear()
                self._checking = None
                log.info("liquid handling: continued")
                self._refresh()
            return
        if (self._can_carry_on and not self.jog.busy
                and not self._run_problems()):
            self._launch()

    def _launch(self) -> None:
        robot, emit = self.session.robot, self.job_said.emit
        # A copy: the page may not be edited during the run, but a copy
        # makes that a rule of the data rather than of the buttons.
        program = self.program.model_copy(deep=True)
        plates, positions, state = self._plates(), self._positions(), self.state
        pause, stop, log_path = self._pause, self._stop, self._log_path
        pause.clear()
        stop.clear()

        def job(log_):
            try:
                liquid.run(robot, program, plates, positions, state,
                           pause=pause, stop=stop, log=log_,
                           on_well=lambda gi, well: emit(("well", (gi, well))),
                           on_progress=lambda n, total, gi, well:
                           emit(("progress", (n, total, gi, well))),
                           on_paused=lambda message: emit(("paused", message)),
                           log_path=log_path)
            except liquid.Stopped:
                emit(("ended", "stopped"))
                return "run stopped."
            except Exception as exc:
                emit(("ended", f"failed: {exc}"))
                raise
            emit(("ended", "done"))
            return "run finished."

        if not self.jog.run_job(job):
            self.jog.tell("not now: the robot is busy.")
            return
        self._running = True
        self._can_carry_on = False
        self._checking = None
        self._outcome = ""
        self._progress = "starting"
        log.info("liquid handling: running %d groups (%d wells already done)",
                 len(program.groups), len(state.done))
        self._refresh()

    def _pause_run(self) -> None:
        if self._running and not self._pause.is_set():
            self._pause.set()
            log.info("liquid handling: paused")
            self._refresh()

    def _pause_key(self) -> None:
        if self._running and not self._pause.is_set():
            self._pause_run()
        else:
            self._continue()

    def _stop_run(self) -> None:
        if self._running and not self._stop.is_set():
            self._stop.set()
            log.info("liquid handling: stop asked")
            self._refresh()

    def _on_job_said(self, payload) -> None:
        kind, value = payload
        if kind == "well":
            self._current = value
            gi, well = value
            name = (self.program.groups[gi].name
                    if gi < len(self.program.groups) else "?")
            self._progress = f"{name} {well}"
        elif kind == "progress":
            n, total, _gi, _well = value
            self._progress += f" done ({n} of {total})"
        elif kind == "paused":
            self._checking = value
        elif kind == "ended":
            self._running = False
            self._checking = None
            self._current = None
            self._pause.clear()
            self._can_carry_on = value != "done" and self._total() > len(self.state.done)
            self._outcome = {"done": "Finished.",
                             "stopped": "Stopped."}.get(value, value.capitalize())
            log.info("liquid handling: %s", value)
        self._paint_plate()
        self._refresh()

    # -- keeping up with the session -----------------------------------------------

    def _lose_top(self) -> None:
        """The top of the travel depends on the tip and on the run."""
        self.state.z_top = None

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
        for box in self._editing:
            box.setEnabled(not running)

        # The plate.
        shown, selection = self._shown_plate(), self.plate.selection
        group = self._group()
        self.new_group_button.setEnabled(shown is not None and bool(selection))
        self.add_wells_button.setEnabled(
            group is not None and bool(selection) and self._on_shown_plate(group))
        self.remove_wells_button.setEnabled(shown is not None and bool(selection))
        if self.plate_choice.count() == 0:
            self.plate_state.setText(
                "No labware on the deck: load it on the Robot & Deck page."
                if self.session.robot is not None else
                "No robot: connect it on the Profile page to see its plates.")
        elif group is not None and not self._on_shown_plate(group):
            self.plate_state.setText(
                f"{group.name} is on slot {group.slot} ({group.load_name}), "
                f"which the robot does not hold now.")
        else:
            self.plate_state.setText(f"{len(selection)} wells selected."
                                     if selection else "")
        self.plate_state.setVisible(bool(self.plate_state.text()))

        # Groups and steps.
        has_group = group is not None
        index = self._group_index()
        for button in (self.rename_group_button, self.colour_button,
                       self.delete_group_button, self.order_choice,
                       self.add_step_button):
            button.setEnabled(has_group)
        self.group_up.setEnabled(has_group and index > 0)
        self.group_down.setEnabled(has_group
                                   and index < len(self.program.groups) - 1)
        step = self._step_index()
        has_step = step is not None
        for button in (self.copy_step_button, self.delete_step_button):
            button.setEnabled(has_step)
        self.step_up.setEnabled(has_step and step > 0)
        self.step_down.setEnabled(has_step and step < len(group.steps) - 1)
        self.editor.setVisible(has_step)
        try_problems = self._try_problems() if has_step else []
        self.try_button.setEnabled(idle and has_step and not try_problems)
        if not has_group:
            self.steps_state.setText("Make a group first: select wells on the "
                                     "plate and press New group.")
        elif not group.steps:
            self.steps_state.setText("No steps yet: Add step. Each step says "
                                     "where it happens.")
        elif try_problems and idle:
            self.steps_state.setText("To try it: " + try_problems[0])
        else:
            self.steps_state.setText("")
        self.steps_state.setVisible(bool(self.steps_state.text()))

        # The program.
        name = self.program.name or "not saved under a name"
        self.program_state.setText(
            f"{name}: {len(self.program.groups)} groups. Saved as you edit; "
            f"Save as keeps a named copy.")

        # The run.
        problems = self._run_problems()
        paused = running and self._pause.is_set()
        self.start_button.setEnabled(idle and not problems)
        self.pause_button.setEnabled(running and not paused)
        self.continue_button.setEnabled(
            paused or (idle and self._can_carry_on and not problems))
        self.stop_button.setEnabled(running)
        total, done = self._total(), len(self.state.done)
        in_tip = (f"The tip holds {self.state.in_tip:g} µl."
                  if self.state.in_tip > 0 else "The tip is empty.")
        if running:
            if self._checking:
                text = f"Paused: {self._checking}\nPress Continue to go on."
            elif paused:
                text = "Paused. Press Continue to go on."
            elif self._stop.is_set():
                text = "Stopping after the command the robot is doing…"
            else:
                text = f"Running: {self._progress}."
            text += f"\n{done} of {total} wells done. {in_tip}"
        elif problems:
            text = "\n".join("• " + p for p in problems)
        else:
            text = ((self._outcome + " ") if self._outcome else "") + \
                f"{done} of {total} wells done. {in_tip}"
            if self._can_carry_on:
                text += "\nContinue carries on from where the run stopped."
        self.run_state.setText(text)

        if running:
            lines = [f"liquid handling: {self._progress}",
                     f"{done} of {total} wells done",
                     f"in the tip: {self.state.in_tip:g} µl"]
            if paused:
                lines.append("PAUSED")
            self.view.set_status(lines)
        else:
            self.view.set_status([])
