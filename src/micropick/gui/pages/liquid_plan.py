"""Liquid's Plan step (REDESIGN §6.8): the liquid plan, built from blocks.

A liquid plan (`core.liquid.Program`, GLOSSARY) is groups of wells, each
with the steps every one of its wells gets; `workflows.liquid` runs them
group by group, each well's steps before the next well.

**Left:** the plate, chosen with the `MiniDeckDropdown`, its tiles saying
whether the plate's well center was measured. A plate not measured (nor
skipped on the Well center step) shows a warning with **Measure now**
(the Well center step) and **Use anyway**: it is not blocked, the robot
then uses the plate's drawing. `PlateTools` over the `PlateGrid`, its
wells filled in their group's color; "N wells selected" and **Make a
group**, which takes the wells out of any other group of that plate (a
well is in one group of its plate at most).

**Right:** Start from **A template** or **New plan**, as on Auto pick's
Plan (`pages.routine`): the templates box, Open from file…, a click puts
the template's groups in hand. **Take liquid from** In a well / At a
point / By hand is where "+ Aspirate" takes from: the well or the saved
point chosen under it, or By hand - a pause for the operator to bring the
tip, then an aspirate there. The groups, in the order they run, as fold
cards dragged by their header to run earlier or later: each step one line
with **Edit**, which unfolds the step's fields (`widgets.liquid_steps`),
**Try once** (that step, for the selected well or the group's first,
through a jog panel that is never shown) and **Delete step**; "+ Aspirate",
"+ Dispense", "+ Mix", More… for the rest. **What to run**: all groups,
one after another, or only one. The trash (Start blows the tip out there
first) and what a tip holds are folded under them.

**Use this plan** hands a copy and the groups to run to the session
(`Session.set_liquid_plan`): Liquid's Plan is done, and Run runs it. A
plan changed after it is shown with Use this plan again. Save as template
and Save changes to "<name>" behave as Auto pick's (`config.templates`).

The plan is saved as it is edited, into `outputs/liquid/current.json`;
the next start keeps only its trash: groups are chosen for the plates on
the deck that day.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QByteArray, QMimeData, QSize, Qt, QTimer
from PySide6.QtGui import QDrag, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QPushButton,
                               QVBoxLayout, QWidget)

from ... import paths
from ...config import templates
from ...core.liquid import (ACTIONS, Aspirate, Group, Location, Pause,
                            Program, ProgramError, describe, new_step,
                            ordered_wells)
from ...workflows import liquid
from ..labware_kinds import facts, kind
from ..session import MEASURED, NOT_MEASURED, Session, SessionError
from ..theme import SPACING
from ..theme.factory import (caption, card, combo_box, compact,
                             danger_text_button,
                             double_spin_box, muted_label, primary_button,
                             scroll_column, secondary_button)
from ..widgets.banner import Banner
from ..widgets.controls import Segmented
from ..widgets.fold_card import FoldCard
from ..widgets.jog_panel import JogPanel
from ..widgets.liquid_steps import LocationEditor, StepEditor
from ..widgets.mini_deck import DeckTile, MiniDeckDropdown
from ..widgets.plate_grid import PlateGrid, PlateTools
from ..widgets.toast import show_toast

__all__ = ["LiquidPlanPage", "programs_dir", "CURRENT_FILE"]

TITLE = "Plan"

log = logging.getLogger(__name__)

PANEL_WIDTH = 460
TEMPLATE_ROWS = 3
TEMPLATE_ROW_HEIGHT = 46
# The plan is saved this long after the last edit, so typing "300" is one
# write of 300 and not three.
SAVE_MS = 400
CURRENT_FILE = "current.json"

NO_PLATE = "There is no plate on the deck. Add it on the Deck step."
NOT_MEASURED_TEXT = ("The well center of this plate was not measured. The "
                     "robot will use the plate's drawing.")
PLAN_READY = "Plan ready"
BY_HAND = "Bring the tip to the liquid, then continue."

# Take liquid from: (on screen, the location kind "+ Aspirate" takes).
SOURCES = (("In a well", "well"), ("At a point", "point"),
           ("By hand", "here"))
# The steps with a button of their own; the rest are under More….
FIRST_ACTIONS = ("aspirate", "dispense", "mix")
# The steps that need a tip on. A move does not.
LIQUID_ACTIONS = ("aspirate", "dispense", "mix", "blow_out")
GROUP_MIME = "application/x-micropick-liquid-group"


def programs_dir() -> Path:
    directory = paths.outputs_dir() / "liquid"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def summary(program: Program, groups=None) -> str:
    """"2 groups · 24 wells", of the groups that run."""
    chosen = liquid.chosen(program, groups).groups
    if not chosen:
        return "No groups yet."
    wells = sum(len(g.wells) for g in chosen)
    return (("1 group" if len(chosen) == 1 else f"{len(chosen)} groups")
            + f" · {wells} well" + ("" if wells == 1 else "s"))


# -- what a page with liquid steps checks ------------------------------------

def plates_for(session: Session) -> dict[str, liquid.Plate]:
    """The plates on the deck as a run sees them: each with its measured
    well center, if any."""
    profile, out = session.profile, {}
    for slot, entry, definition in session.plates():
        center = (profile.deck.well_center(slot, entry.load_name)
                  if profile is not None else None)
        out[slot] = liquid.Plate(
            slot, entry.labware_id, entry.load_name, definition.ordering,
            tuple(center.offset) if center is not None else None,
            center.depth_mm if center is not None else None)
    return out


def positions_for(session: Session) -> dict:
    profile = session.profile
    return dict(profile.positions or {}) if profile is not None else {}


def robot_problems(session: Session, *, tip: bool = True) -> list[str]:
    if session.robot is None:
        return ["the robot is not connected."]
    if tip and session.tip.attached is not True:
        return ["no tip on: put one on on the Tip step."
                if session.tip.attached is False else
                "the robot's tip state is unknown: connect again."]
    return []


def reach_problems(session: Session, program: Program) -> list[str]:
    """Saved points out of the robot's reach, and deck problems in the
    slots the program goes to."""
    out, positions = [], positions_for(session)
    for name in sorted(liquid.points_used(program)):
        if name in positions:
            why = session.motion.refused(positions[name])
            if why:
                out.append(f"point {name!r} is outside the soft limits: "
                           f"{why}.")
    slots = liquid.slots_used(program)
    out += [p.describe() for p in session.deck_problems() if p.slot in slots]
    return out


def plate_choices(session: Session) -> list[tuple]:
    """What a location can name: (slot, load name, text, wells row by
    row) per labware on the deck."""
    out = []
    for slot, _entry, definition in session.plates():
        rows = max((len(c) for c in definition.ordering), default=0)
        wells = [column[r] for r in range(rows)
                 for column in definition.ordering if r < len(column)]
        out.append((slot, definition.load_name,
                    f"slot {slot} — {definition.display_name}", wells))
    return out


class _GroupCard(FoldCard):
    """A group's fold card; its header drags it to another place in the
    run order."""

    def __init__(self, page: "LiquidPlanPage", index: int, group: Group):
        super().__init__(group.name, summary=f"{len(group.wells)} wells",
                         parent=page)
        self.page, self.index = page, index
        self._press = None
        self.header.setStyleSheet(f"color: {group.color};")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (self._press is None or (event.position() - self._press)
                .manhattanLength() < QApplication.startDragDistance()):
            return super().mouseMoveEvent(event)
        self._press = None
        drag = QDrag(self)
        data = QMimeData()
        data.setData(GROUP_MIME, QByteArray(str(self.index).encode()))
        drag.setMimeData(data)
        drag.setPixmap(self.grab())
        drag.exec(Qt.DropAction.MoveAction)


class _GroupColumn(QWidget):
    """The group cards, a drop on it moving the dragged one there."""

    def __init__(self, page: "LiquidPlanPage"):
        super().__init__(page)
        self.page = page
        self.setAcceptDrops(True)
        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(0, 0, 0, 0)
        self.column.setSpacing(SPACING)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(GROUP_MIME):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(GROUP_MIME):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        source = int(bytes(event.mimeData().data(GROUP_MIME)).decode())
        y = event.position().y()
        cards = self.page.group_cards
        to = sum(1 for c in cards if c.geometry().center().y() < y
                 and c.index != source)
        event.acceptProposedAction()
        self.page.move_group(source, to)


class LiquidPlanPage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.program = self._load_current()
        self.slot: str | None = None             # the plate on the grid
        self.template: templates.Template | None = None
        self._opened: Program | None = None      # the template as opened
        self._template_mode = False
        self._accepted: set[tuple[str, str]] = set()   # Use anyway
        self._editing: tuple[int, int] | None = None   # (group, step)
        self.only_group: int | None = None       # What to run; None: all
        self.state = liquid.LiquidState()        # Try once's tip
        self.group_cards: list[_GroupCard] = []
        self._line: QLabel | None = None         # the edited step's line

        # Try once goes through it; never shown, so it binds no keys.
        self.jog = JogPanel(session, shortcut_host=self,
                            machine_controls=False, parent=self)
        self.jog.hide()
        self.jog.position_changed.connect(self._tried)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_MS)
        self._save_timer.timeout.connect(self._save_current)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2,
                                  SPACING * 2)
        layout.setSpacing(SPACING * 2)
        layout.addLayout(self._left(), 1)
        panel = QWidget(self)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        self._start_from(column)
        self._source(column)
        self._groups(column)
        self._what_to_run(column)
        column.addWidget(self._trash_card())
        column.addStretch(1)
        right = QVBoxLayout()
        right.setSpacing(SPACING)
        right.addWidget(scroll_column(panel, PANEL_WIDTH), 1)
        right.addWidget(self._summary_card())
        layout.addLayout(right)

        session.robot_state_changed.connect(lambda _s: self._deck_changed())
        session.labware_changed.connect(lambda _s: self._deck_changed())
        session.profile_changed.connect(lambda _p: self._deck_changed())
        session.liquid_plan_changed.connect(lambda _p: self._refresh())
        # A plate skipped on the Well center step, a plan used.
        session.confirmations_changed.connect(self._deck_changed)
        session.robot_busy_changed.connect(lambda _h: self._refresh())
        self._install_shortcuts()
        self._deck_changed()
        self._show_groups()

    # -- construction --------------------------------------------------------

    def _left(self) -> QVBoxLayout:
        self.plate = PlateGrid(parent=self)
        self.plate.selection_changed.connect(lambda _s: self._refresh())
        self.dropdown = MiniDeckDropdown(self, placeholder="Choose the plate")
        self.dropdown.slot_chosen.connect(lambda s: self.choose_slot(str(s)))
        self.tools = PlateTools(self.plate, self)

        left = QVBoxLayout()
        left.setSpacing(SPACING)
        top = QHBoxLayout()
        top.addWidget(self.dropdown)
        top.addStretch(1)
        top.addWidget(self.tools)
        left.addLayout(top)
        self.no_plate = Banner(NO_PLATE, tone="warn", parent=self)
        self.no_plate.add_action("Back to Deck").clicked.connect(
            lambda: self.session.page_requested.emit("labware"))
        left.addWidget(self.no_plate)
        self.not_measured = Banner(NOT_MEASURED_TEXT, tone="warn",
                                   parent=self)
        self.measure_button = self.not_measured.add_action("Measure now")
        self.measure_button.clicked.connect(
            lambda: self.session.page_requested.emit("well_center"))
        self.use_anyway_button = self.not_measured.add_action("Use anyway")
        self.use_anyway_button.clicked.connect(self._use_anyway)
        left.addWidget(self.not_measured)
        left.addWidget(self.plate, 1)
        row = QHBoxLayout()
        self.selected = muted_label("", self)
        row.addWidget(self.selected, 1)
        self.make_group_button = primary_button("Make a group", self)
        self.make_group_button.clicked.connect(self.make_group)
        row.addWidget(self.make_group_button)
        left.addLayout(row)
        return left

    def _tile(self, text: str, height: int) -> QPushButton:
        button = QPushButton(text, self)
        button.setObjectName("tile")
        button.setCheckable(True)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setMinimumHeight(height)
        return button

    def _start_from(self, column: QVBoxLayout) -> None:
        column.addWidget(caption("Start from", self))
        row = QHBoxLayout()
        self.template_tile = self._tile("A template\nSaved earlier", 64)
        self.new_tile = self._tile("New plan\nChoose wells", 64)
        self.template_tile.clicked.connect(lambda: self._set_mode(True))
        self.new_tile.clicked.connect(lambda: self._set_mode(False))
        row.addWidget(self.template_tile)
        row.addWidget(self.new_tile)
        column.addLayout(row)

        self.template_box = QWidget(self)
        box = QVBoxLayout(self.template_box)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(SPACING // 2)
        box.addWidget(caption("Saved templates", self))
        self.template_list = QListWidget(self.template_box)
        self.template_list.setFixedHeight(TEMPLATE_ROWS * TEMPLATE_ROW_HEIGHT
                                          + 4)
        self.template_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.template_list.itemClicked.connect(
            lambda item: self.open_template(
                item.data(Qt.ItemDataRole.UserRole)))
        box.addWidget(self.template_list)
        self.no_templates = muted_label("No templates saved yet.",
                                        self.template_box)
        box.addWidget(self.no_templates)
        self.open_file_button = secondary_button("Open from file…",
                                                 self.template_box)
        self.open_file_button.clicked.connect(self._open_file)
        box.addWidget(self.open_file_button,
                      alignment=Qt.AlignmentFlag.AlignLeft)
        column.addWidget(self.template_box)
        self.message = muted_label("", self)
        column.addWidget(self.message)

    def _source(self, column: QVBoxLayout) -> None:
        column.addWidget(caption("Take liquid from", self))
        self.source = Segmented([text for text, _kind in SOURCES],
                                parent=self)
        self.source.changed.connect(lambda _i: self._source_chosen())
        column.addWidget(self.source)
        self.source_where = LocationEditor(self)
        self.source_where.limit_kinds(("well",))
        column.addWidget(self.source_where)
        self.by_hand_note = muted_label(
            "The robot waits for you to bring the tip, then aspirates.",
            self)
        column.addWidget(self.by_hand_note)

    def _groups(self, column: QVBoxLayout) -> None:
        column.addWidget(caption("Groups, in the order they run", self))
        self.group_column = _GroupColumn(self)
        column.addWidget(self.group_column)
        self.no_groups = muted_label("Choose wells and make a group.", self)
        column.addWidget(self.no_groups)
        # One editor, moved under the step being edited.
        self.editor = StepEditor(self)
        self.editor.changed.connect(self._step_edited)
        self.edit_row = QWidget(self)
        row = QHBoxLayout(self.edit_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.try_button = secondary_button("Try once", self.edit_row)
        self.try_button.setToolTip("Do this step now, for the selected well "
                                   "or the group's first.")
        self.try_button.clicked.connect(self.try_once)
        self.delete_step_button = danger_text_button("Delete step",
                                                     self.edit_row)
        self.delete_step_button.clicked.connect(self.delete_step)
        row.addWidget(self.try_button)
        row.addStretch(1)
        row.addWidget(self.delete_step_button)
        self.try_state = muted_label("", self)
        for widget in (self.editor, self.edit_row, self.try_state):
            widget.hide()

    def _what_to_run(self, column: QVBoxLayout) -> None:
        column.addWidget(caption("What to run", self))
        self.scope = Segmented(["All groups, one after another",
                                "Only one group"], parent=self)
        self.scope.changed.connect(lambda _i: self._scope_chosen())
        column.addWidget(self.scope)
        self.only_choice = combo_box(self)
        self.only_choice.currentIndexChanged.connect(
            lambda _i: self._scope_chosen())
        column.addWidget(self.only_choice)

    def _trash_card(self) -> QWidget:
        self.trash_card = FoldCard("Trash and tip", parent=self)
        body = self.trash_card.body.layout()
        body.addWidget(muted_label("Start blows the tip out here first.",
                                   self))
        self.trash = LocationEditor(self)
        self.trash.limit_kinds(("well", "point"))
        self.trash.changed.connect(self._trash_edited)
        body.addWidget(self.trash)
        row = QHBoxLayout()
        row.addWidget(QLabel("A tip holds", self))
        self.tip_volume = double_spin_box(self)
        self.tip_volume.setRange(1.0, 1000.0)
        self.tip_volume.setDecimals(0)
        self.tip_volume.setSingleStep(10.0)
        self.tip_volume.setSuffix(" µl")
        self.tip_volume.valueChanged.connect(self._tip_volume_edited)
        row.addWidget(self.tip_volume)
        row.addStretch(1)
        body.addLayout(row)
        return self.trash_card

    def _summary_card(self) -> QWidget:
        box = card(self)
        box.setFixedWidth(PANEL_WIDTH)
        self.summary = muted_label("", box)
        box.layout().addWidget(self.summary)
        self.problem = muted_label("", box)
        box.layout().addWidget(self.problem)
        self.use_button = primary_button("Use this plan", box)
        self.use_button.clicked.connect(self.use)
        box.layout().addWidget(self.use_button)
        self.ready = Banner(PLAN_READY, tone="ok", parent=box)
        box.layout().addWidget(self.ready)
        self.save_as_button = secondary_button("Save as template", box)
        self.save_as_button.clicked.connect(self._ask_name)
        box.layout().addWidget(self.save_as_button)
        self.name_row = QWidget(box)
        row = QHBoxLayout(self.name_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.name_field = QLineEdit(self.name_row)
        self.name_field.setPlaceholderText("Template name")
        self.name_field.returnPressed.connect(self.save_as)
        save = primary_button("Save", self.name_row)
        save.clicked.connect(self.save_as)
        cancel = secondary_button("Cancel", self.name_row)
        cancel.clicked.connect(self._cancel_name)
        row.addWidget(self.name_field, 1)
        row.addWidget(save)
        row.addWidget(cancel)
        self.name_row.hide()
        box.layout().addWidget(self.name_row)
        self.save_changes_button = secondary_button("Save changes", box)
        self.save_changes_button.clicked.connect(self.save_changes)
        box.layout().addWidget(self.save_changes_button)
        self.template_note = muted_label("", box)
        box.layout().addWidget(self.template_note)
        return box

    def _install_shortcuts(self) -> None:
        """Undo, Redo and Select all of the grid, window-wide while the page
        is showing."""
        self._shortcuts = []
        for sequence, handler in ((QKeySequence.StandardKey.Undo,
                                   self.plate.undo),
                                  (QKeySequence("Ctrl+Y"), self.plate.redo),
                                  (QKeySequence.StandardKey.Redo,
                                   self.plate.redo),
                                  (QKeySequence.StandardKey.SelectAll,
                                   self.plate.select_all)):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(handler)
            shortcut.setEnabled(False)
            self._shortcuts.append(shortcut)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        for shortcut in self._shortcuts:
            shortcut.setEnabled(True)
        self._reload_templates()
        self._offer_choices()

    def hideEvent(self, event) -> None:
        for shortcut in self._shortcuts:
            shortcut.setEnabled(False)
        super().hideEvent(event)

    # -- the plan file -------------------------------------------------------

    def _load_current(self) -> Program:
        path = programs_dir() / CURRENT_FILE
        if not path.is_file():
            return Program()
        try:
            return Program(trash=Program.load(path).trash)
        except ProgramError as exc:
            log.error("liquid plan: could not read %s: %s", path, exc)
            return Program()

    def _save_current(self) -> None:
        try:
            self.program.save(programs_dir() / CURRENT_FILE)
        except OSError as exc:
            log.error("liquid plan: could not save it: %s", exc)

    def _changed(self) -> None:
        self._save_timer.start()
        self._show_groups()

    # -- the plate -----------------------------------------------------------

    def _plates(self) -> dict[str, tuple]:
        return {slot: (entry, definition)
                for slot, entry, definition in self.session.plates()}

    def _shown(self) -> tuple[str, str] | None:
        """(slot, load name) of the plate on the grid."""
        held = self._plates()
        if self.slot is None or self.slot not in held:
            return None
        return self.slot, held[self.slot][0].load_name

    def _measured(self, slot: str, load_name: str) -> bool:
        """Measured, or skipped on purpose on the Well center step: no
        warning either way."""
        return (self.session.well_center_status(slot, load_name)
                != NOT_MEASURED)

    def _deck_changed(self) -> None:
        plates = self.session.plates()
        status = self.session.well_center_status
        self.dropdown.set_tiles([
            DeckTile(int(slot), name=definition.display_name,
                     kind=kind(definition), facts=facts(definition),
                     **({"note": "Measured", "note_tone": "ok"}
                        if status(slot, entry.load_name) == MEASURED else
                        {"note": "Not measured", "note_tone": "warn"}))
            for slot, entry, definition in plates if slot.isdigit()])
        if self._shown() is None:
            self.slot = None
            self.choose_slot(plates[0][0] if plates else None)
        self._offer_choices()
        self._refresh()

    def choose_slot(self, slot: str | None) -> None:
        held = self._plates()
        if slot is not None and slot not in held:
            return
        self.slot = slot
        self.dropdown.set_slot(int(slot) if slot and slot.isdigit() else None)
        self.plate.set_ordering(held[slot][1].ordering if slot else [])
        self._paint()
        self._refresh()

    def _use_anyway(self) -> None:
        shown = self._shown()
        if shown is not None:
            self._accepted.add(shown)
            log.info("liquid plan: slot %s used with its drawing", shown[0])
        self._refresh()

    def _paint(self) -> None:
        shown, fills = self._shown(), {}
        for group in self.program.groups:
            if (group.slot, group.load_name) == shown:
                fills.update({w: group.color for w in group.wells})
        self.plate.set_fills(fills)

    def make_group(self) -> None:
        shown, wells = self._shown(), self.plate.selection
        if shown is None or not wells:
            return
        slot, load_name = shown
        self._take_wells(slot, load_name, wells)
        name = f"Group {len(self.program.groups) + 1}"
        self.program.groups.append(Group(
            name=name, color=self.program.next_colour(), slot=slot,
            load_name=load_name,
            wells=[w for w in self.plate.wells if w in wells]))
        log.info("liquid plan: %s of %d wells in slot %s", name, len(wells),
                 slot)
        self.plate.set_selection(set())
        self._editing = None
        self._changed()

    def _take_wells(self, slot: str, load_name: str, wells: set[str]) -> None:
        for group in self.program.groups:
            if (group.slot, group.load_name) == (slot, load_name):
                group.wells = [w for w in group.wells if w not in wells]

    # -- templates -----------------------------------------------------------

    def _set_mode(self, template: bool) -> None:
        if not template and self.template is not None:
            # A new plan: the template's groups go, the trash stays. Groups
            # made by hand stay: nothing was opened over them.
            self._adopt(Program(trash=self.program.trash,
                                tip_ul=self.program.tip_ul))
        self._template_mode = template
        if template:
            self._reload_templates()
        self._refresh()

    def _reload_templates(self) -> None:
        self.template_list.clear()
        for template in self.session.liquid_templates():
            item = QListWidgetItem(f"{template.name}\n{template.facts()}")
            item.setData(Qt.ItemDataRole.UserRole, template)
            item.setSizeHint(QSize(0, TEMPLATE_ROW_HEIGHT))
            self.template_list.addItem(item)
        self._refresh()

    def _open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open a template",
            str(templates.templates_dir(templates.LIQUID_PLANS)),
            "Templates (*.json)")
        if path:
            self.open_template(path)

    def open_template(self, template: templates.Template | str | Path) -> None:
        """The template's groups, trash and tip in hand. A path is a file
        from anywhere (Open from file…)."""
        try:
            if not isinstance(template, templates.Template):
                template = templates.liquid_template(Path(template))
            program = Program.load(template.path)
        except (ProgramError, OSError) as exc:
            self._say(str(exc))
            return
        self._adopt(program)
        self.template = template
        self._opened = program.model_copy(deep=True)
        self._say("")
        log.info("liquid plan from template %r", template.name)
        # The map goes to the plate of its first group, when it is here.
        if program.groups and program.groups[0].slot in self._plates():
            self.choose_slot(program.groups[0].slot)
        self._refresh()

    def _adopt(self, program: Program) -> None:
        self.program = program
        self.template = None
        self._opened = None
        self._editing = None
        self.only_group = None
        self._save_current()
        self._show_groups()
        self._paint()

    def _edited(self) -> bool:
        return (self.template is not None
                and self._opened is not None
                and self._opened.model_dump(exclude={"name"})
                != self.program.model_dump(exclude={"name"}))

    def _ask_name(self) -> None:
        self.name_field.clear()
        self.template_note.setText("")
        self.name_row.show()
        self.save_as_button.hide()
        self.name_field.setFocus()

    def _cancel_name(self) -> None:
        self.name_row.hide()
        self.template_note.setText("")
        self._refresh()

    def save_as(self) -> None:
        try:
            template = self.session.save_liquid_template(
                self.program, self.name_field.text())
        except (SessionError, OSError) as exc:
            self.template_note.setText(str(exc))
            return
        self.name_row.hide()
        self.template_note.setText("")
        self.template = template
        self._opened = self.program.model_copy(deep=True)
        self._reload_templates()
        show_toast(self, f'Template "{template.name}" saved.')

    def save_changes(self) -> None:
        if not self._edited():
            return
        try:
            self.template = self.session.save_liquid_template_changes(
                self.template, self.program)
        except (ProgramError, OSError) as exc:
            self.template_note.setText(str(exc))
            return
        self._opened = self.program.model_copy(deep=True)
        self._reload_templates()
        show_toast(self, f'Changes saved to "{self.template.name}".')

    # -- take liquid from ------------------------------------------------------

    def _source_kind(self) -> str:
        return SOURCES[self.source.current()][1]

    def _source_chosen(self) -> None:
        kind_ = self._source_kind()
        if kind_ != "here":
            self.source_where.limit_kinds((kind_,))
            self.source_where.set_location(Location(kind=kind_))
        self._refresh()

    def _source_location(self) -> Location:
        """Where "+ Aspirate" takes from: the well or point chosen, else
        each well of the group until one is."""
        kind_ = self._source_kind()
        if kind_ == "here":
            return Location(kind="here")
        where = self.source_where.location()
        return where if (where.slot or where.point) else Location()

    # -- groups and steps --------------------------------------------------------

    def _show_groups(self) -> None:
        """The group cards from the program, the open ones kept open."""
        open_ = {c.index for c in self.group_cards if c.is_open()}
        for widget in (self.editor, self.edit_row, self.try_state):
            widget.setParent(self)
            widget.hide()
        for old in self.group_cards:
            self.group_column.column.removeWidget(old)
            old.deleteLater()
        self.group_cards = []
        self._line = None
        for gi, group in enumerate(self.program.groups):
            card_ = _GroupCard(self, gi, group)
            self._fill_group(card_, gi, group)
            card_.set_open(gi in open_ or (self._editing is not None
                                           and self._editing[0] == gi)
                           or gi == len(self.program.groups) - 1
                           and not open_)
            self.group_column.column.addWidget(card_)
            self.group_cards.append(card_)
        self.only_choice.blockSignals(True)
        self.only_choice.clear()
        for group in self.program.groups:
            self.only_choice.addItem(group.name)
        if self.only_group is not None:
            if self.only_group >= len(self.program.groups):
                self.only_group = None
                self.scope.set_current(0)
            else:
                self.only_choice.setCurrentIndex(self.only_group)
        self.only_choice.blockSignals(False)
        self.trash.blockSignals(True)
        self.trash.set_location(self.program.trash or Location(kind="well"))
        self.trash.blockSignals(False)
        self.tip_volume.blockSignals(True)
        self.tip_volume.setValue(self.program.tip_ul)
        self.tip_volume.blockSignals(False)
        self._paint()
        self._refresh()

    def _fill_group(self, card_: _GroupCard, gi: int, group: Group) -> None:
        body = card_.body.layout()
        body.addWidget(muted_label(f"Slot {group.slot} · "
                                   f"{len(group.wells)} wells", card_))
        for si, step in enumerate(group.steps):
            row = QHBoxLayout()
            line = QLabel(f"{si + 1}. {describe(step, group.slot)}", card_)
            line.setWordWrap(True)
            row.addWidget(line, 1)
            edit = compact(secondary_button("Edit", card_))
            edit.clicked.connect(lambda _c=False, g=gi, s=si: self.edit_step(g, s))
            row.addWidget(edit, alignment=Qt.AlignmentFlag.AlignTop)
            body.addLayout(row)
            if self._editing == (gi, si):
                self._line = line
                self.editor.set_step(step)
                for widget in (self.editor, self.edit_row, self.try_state):
                    body.addWidget(widget)
                    widget.show()
        row = QHBoxLayout()
        for action in FIRST_ACTIONS:
            title = next(t for a, _c, t in ACTIONS if a == action)
            button = secondary_button(f"+ {title}", card_)
            button.clicked.connect(
                lambda _c=False, g=gi, a=action: self.add_step(g, a))
            row.addWidget(button)
        more = secondary_button("More…", card_)
        menu = QMenu(more)
        for action, _cls, title in ACTIONS:
            if action not in FIRST_ACTIONS:
                menu.addAction(title, lambda g=gi, a=action:
                               self.add_step(g, a))
        more.setMenu(menu)
        row.addWidget(more)
        body.addLayout(row)
        row = QHBoxLayout()
        order = combo_box(card_)
        order.addItem("Row by row", "by_row")
        order.addItem("Column by column", "by_column")
        order.setCurrentIndex(max(0, order.findData(group.order)))
        order.currentIndexChanged.connect(
            lambda _i, g=group, o=order: self._set_order(g, o.currentData()))
        row.addWidget(order)
        pause = QCheckBox("Pause after the first well", card_)
        pause.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        pause.setChecked(group.pause_after_first)
        pause.toggled.connect(lambda on, g=group: self._set_pause(g, on))
        row.addWidget(pause)
        body.addLayout(row)
        delete = danger_text_button("Delete group", card_)
        delete.clicked.connect(lambda _c=False, g=gi: self.delete_group(g))
        body.addWidget(delete, alignment=Qt.AlignmentFlag.AlignLeft)

    def _set_order(self, group: Group, order: str) -> None:
        group.order = order
        self._save_timer.start()
        self._refresh()

    def _set_pause(self, group: Group, on: bool) -> None:
        group.pause_after_first = bool(on)
        self._save_timer.start()
        self._refresh()

    def move_group(self, source: int, to: int) -> None:
        """The group at `source` runs at place `to` instead."""
        groups = self.program.groups
        if not 0 <= source < len(groups):
            return
        to = max(0, min(to, len(groups) - 1))
        if to == source:
            return
        groups.insert(to, groups.pop(source))
        self._editing = None
        self.only_group = None
        self.scope.set_current(0)
        self._changed()

    def delete_group(self, gi: int) -> None:
        del self.program.groups[gi]
        self._editing = None
        self.only_group = None
        self.scope.set_current(0)
        self._changed()

    def add_step(self, gi: int, action: str) -> None:
        steps = self.program.groups[gi].steps
        if action == "aspirate":
            where = self._source_location()
            if where.kind == "here":
                steps.append(Pause(message=BY_HAND))
            steps.append(Aspirate(location=where))
        else:
            steps.append(new_step(action))
        self._editing = (gi, len(steps) - 1)
        self._changed()

    def edit_step(self, gi: int, si: int) -> None:
        self._editing = None if self._editing == (gi, si) else (gi, si)
        self._show_groups()

    def delete_step(self) -> None:
        if self._editing is None:
            return
        gi, si = self._editing
        del self.program.groups[gi].steps[si]
        self._editing = None
        self._changed()

    def _step_edited(self, step) -> None:
        if self._editing is None:
            return
        gi, si = self._editing
        self.program.groups[gi].steps[si] = step
        if self._line is not None:
            self._line.setText(f"{si + 1}. "
                               f"{describe(step, self.program.groups[gi].slot)}")
        self._save_timer.start()
        self._refresh()

    def _trash_edited(self) -> None:
        where = self.trash.location()
        self.program.trash = where if (where.slot or where.point) else None
        self._save_timer.start()
        self._refresh()

    def _tip_volume_edited(self, value: float) -> None:
        self.program.tip_ul = float(value)
        self._save_timer.start()
        self._refresh()

    def _offer_choices(self) -> None:
        plates = plate_choices(self.session)
        points = list(positions_for(self.session))
        for editor in (self.editor, self.trash, self.source_where):
            editor.blockSignals(True)
            editor.set_choices(plates, points)
            editor.blockSignals(False)
        measured = {(slot, entry.load_name)
                    for slot, entry, _d in self.session.plates()
                    if self.session.profile is not None
                    and self.session.profile.deck.well_center(
                        slot, entry.load_name) is not None}
        group = (self.program.groups[self._editing[0]]
                 if self._editing is not None else None)
        self.editor.where.set_measured(
            measured, (group.slot, group.load_name) if group else None)

    # -- what to run -------------------------------------------------------------

    def _scope_chosen(self) -> None:
        if self.scope.current() == 1 and self.program.groups:
            self.only_group = max(0, self.only_choice.currentIndex())
        else:
            self.only_group = None
        self._refresh()

    def groups_to_run(self) -> list[int] | None:
        return None if self.only_group is None else [self.only_group]

    # -- try once ------------------------------------------------------------------

    def _try_well(self, group: Group) -> str | None:
        plate = plates_for(self.session).get(group.slot)
        if plate is None:
            return None
        wells = ordered_wells(group, plate.ordering)
        chosen = ([w for w in wells if w in self.plate.selection]
                  if self._shown() == (group.slot, group.load_name) else [])
        return (chosen or wells or [None])[0]

    def try_problems(self) -> list[str]:
        if self._editing is None:
            return ["choose a step."]
        gi, si = self._editing
        group = self.program.groups[gi]
        step = group.steps[si]
        out = robot_problems(self.session, tip=step.action in LIQUID_ACTIONS)
        single = Program(groups=[group.model_copy(update={"steps": [step]})])
        out += liquid.problems(single, plates_for(self.session),
                               positions_for(self.session), volumes=False,
                               trash=False)
        out += reach_problems(self.session, single)
        return out

    def try_once(self) -> None:
        if self._editing is None or self.jog.busy or self.try_problems():
            return
        gi, si = self._editing
        group = self.program.groups[gi]
        step, well = group.steps[si], self._try_well(group)
        motion, state = self.session.motion, self.state
        plates, positions = plates_for(self.session), positions_for(self.session)
        capacity = self.program.tip_ul

        def job(log_):
            said = liquid.run_step(motion, step, group, well, plates,
                                   positions, state, log=log_,
                                   capacity=capacity)
            return f"{describe(step).split(' @ ')[0]} for {well}: {said}."

        log.info("liquid plan: trying step %d of %s for %s", si + 1,
                 group.name, well)
        if not self.jog.run_job(job):
            self.try_state.setText(self.session.robot_lease.refusal)
        self._refresh()

    def _tried(self, lines) -> None:
        """Every answer of the panel's, a job's end included: its first
        line is the pose, the rest what was said."""
        self.try_state.setText("\n".join(lines[1:]))
        self._refresh()

    # -- use this plan -----------------------------------------------------------

    def problems(self) -> list[str]:
        """What the plan lacks to be used, from its data and the deck."""
        out = liquid.problems(self.program, plates_for(self.session),
                              positions_for(self.session),
                              groups=self.groups_to_run())
        return out + reach_problems(
            self.session, liquid.chosen(self.program, self.groups_to_run()))

    def _in_use(self) -> bool:
        plan = self.session.liquid_plan
        return (plan is not None and plan == self.program
                and self.session.liquid_groups == self.groups_to_run()
                and self.session.is_confirmed("liquid_plan"))

    def use(self) -> None:
        if self.problems():
            self._refresh()
            return
        self._save_current()
        self.session.set_liquid_plan(self.program, self.groups_to_run())
        show_toast(self, PLAN_READY)
        self._refresh()

    # -- display -----------------------------------------------------------------

    def _say(self, text: str) -> None:
        self.message.setText(text)
        self.message.setVisible(bool(text))

    def _refresh(self) -> None:
        shown = self._shown()
        selection = self.plate.selection
        self.no_plate.setVisible(not self.session.plates())
        self.plate.setEnabled(shown is not None)
        self.tools.setEnabled(shown is not None)
        self.not_measured.setVisible(
            shown is not None and shown not in self._accepted
            and not self._measured(*shown))
        n = len(selection)
        self.selected.setText(f"{n} well{'' if n == 1 else 's'} selected")
        self.make_group_button.setEnabled(shown is not None and bool(n))

        self.template_tile.setChecked(self._template_mode)
        self.new_tile.setChecked(not self._template_mode)
        self.template_box.setVisible(self._template_mode)
        listed = self.template_list.count() > 0
        self.template_list.setVisible(listed)
        self.no_templates.setVisible(not listed)
        self.message.setVisible(bool(self.message.text()))

        by_hand = self._source_kind() == "here"
        self.source_where.setVisible(not by_hand)
        self.by_hand_note.setVisible(by_hand)
        self.no_groups.setVisible(not self.program.groups)
        self.only_choice.setVisible(self.only_group is not None)
        self.scope.buttons[1].setEnabled(bool(self.program.groups))

        if self._editing is not None:
            problems = self.try_problems()
            self.try_button.setEnabled(
                not problems and not self.jog.busy
                and not self.session.robot_lease.busy)
            self.try_button.setToolTip(
                ("To try it: " + problems[0]) if problems else
                "Do this step now, for the selected well or the group's "
                "first.")
        self.try_state.setVisible(self._editing is not None
                                  and bool(self.try_state.text()))

        self.summary.setText(summary(self.program, self.groups_to_run()))
        problems = self.problems() if self.program.groups else []
        self.problem.setText(problems[0].capitalize() if problems else "")
        self.problem.setVisible(bool(problems))
        in_use = self._in_use()
        self.use_button.setEnabled(bool(self.program.groups) and not problems
                                   and not in_use)
        self.ready.setVisible(in_use)
        naming = self.name_row.isVisible()
        self.save_as_button.setVisible(self.template is None and not naming)
        self.save_as_button.setEnabled(bool(self.program.groups))
        edited = self._edited()
        self.save_changes_button.setVisible(edited)
        if self.template is not None:
            self.save_changes_button.setText(
                f'Save changes to "{self.template.name}"')
