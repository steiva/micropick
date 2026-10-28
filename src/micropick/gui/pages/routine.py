"""The Plan step of Auto pick (REDESIGN §6.3): which wells of which plate
get how many cuboids, and in what order.

**Left:** the plate, chosen with the `MiniDeckDropdown` - the plates the
robot session holds, by slot - and `PlateTools` (Undo, Redo, Select all,
Clear) over the `PlateGrid` in its planning mode: the wells planned are
the plan, each drawn with its count; the gestures are not explained on
screen (§3).

**Right:** Start from **A template** or **New plan**. A template is a named
file (`config.templates`, `Session.plate_templates`): every one is listed,
each "<name>" over "24 wells · saved Oct 20", and Open from file… for one
from anywhere. A click on one with no plate of its type on the deck says
"Put a <type> on the deck." with Go to Deck and loads nothing; else the
mini deck slot chooser asks which plate it goes on - even when only one
fits, the operator says where (#44) - and a click there puts it on that
plate. Then **Cuboids per well**, **Fill order** as two tiles, and the
summary: "24 wells × 1 cuboid" and **Use this plan**. Save as template
asks for a name inline, for a plan not from a template; Save changes to
template writes over the template the plan came from once it was
changed. Neither asks "are you sure".

**Counts** (#43). The Cuboids field sets the wells selected on the grid
and no other: a click selects a well (planning it if it was not), so one
well can take 5 and the next 3. Selected wells that differ show the field
empty, "mixed"; a number typed sets them all. With nothing selected, it is
the count the next well planned takes.

**Use this plan** (#45) makes the `Routine` a run delivers into, with
fresh progress, and hands it to the session (`Session.set_routine`): Plan
is done while it is the session's (`tasks.step_state`). The plan is then
in use: the grid, the plate, the counts, the order and the templates are
read-only and the button is **Edit plan**, which lets the session's go
(`Session.set_routine(None)`) - Plan is not done until Use this plan again,
so Picking never runs a plan other than the one shown, nor a half-edited
one. Not while a picking run has the robot. Its file - the run's progress,
which lets a run carry on after a restart - is written under
`Outputs/routines` by itself and never shown here: an unfinished run is
for Picking to offer.

Picking done's **Choose plan** (`Session.plan_chosen`) puts the plan the
run filled back on screen, not in use: Use this plan makes it again with
nothing filled.

Ctrl+Z / Ctrl+Y undo and redo a change of the plan, Ctrl+A chooses every
well.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIntValidator, QKeySequence, QShortcut
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout, QWidget)

from ... import paths
from ...config import templates
from ...config.labware import LabwareError, resolve_definition
from ...core.routine import Destination, PlanPreset, Routine, RoutineError
from ..labware_kinds import facts, kind
from ..session import Session, SessionError
from ..theme import COLORS, SPACING
from ..theme.factory import (caption, card, muted_label, primary_button,
                             secondary_button)
from ..theme.icons import icon
from ..widgets.banner import Banner
from ..widgets.mini_deck import DeckTile, MiniDeckDropdown
from ..widgets.plate_grid import PlateGrid, PlateTools
from ..widgets.slot_chooser import SlotChooser
from ..widgets.toast import show_toast

__all__ = ["RoutinePage"]

# On screen a Routine is a "plan" (REDESIGN §12.9): the step's name.
TITLE = "Plan"

log = logging.getLogger(__name__)

PANEL_WIDTH = 420

# The templates box: a row per template, scrolling inside itself only
# when the panel has no more room; never under three rows high.
TEMPLATE_ROWS = 3
TEMPLATE_ROW_HEIGHT = 46
MAX_COUNT = 99

NO_PLATE = "There is no plate on the deck. Add it on the Deck step."
CHOOSE_WELLS = "Choose the wells on the plate."
NO_WELLS = "No wells chosen yet."
PLAN_READY = "Plan ready"
MIXED = "mixed"
WHICH_PLATE = "Which plate does it go on?"

# The two fill orders, as tiles: (strategy, label, icon).
ORDERS = (("by_row", "Row by row", "rows"),
          ("by_column", "Column by column", "columns"))


def routines_dir() -> Path:
    directory = paths.outputs_dir() / "routines"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def summary(plan: dict[str, int]) -> str:
    """"24 wells × 1 cuboid", or the total when the wells differ."""
    if not plan:
        return NO_WELLS
    wells = f"{len(plan)} well" if len(plan) == 1 else f"{len(plan)} wells"
    counts = set(plan.values())
    if len(counts) == 1:
        n = counts.pop()
        return f"{wells} × {n} cuboid" + ("" if n == 1 else "s")
    return f"{wells}, {sum(plan.values())} cuboids"


class RoutinePage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.destination: Destination | None = None
        # The template the plan came from, and the plan as it was opened,
        # to tell an edited one.
        self.template: templates.Template | None = None
        self._opened: tuple[dict[str, int], str] | None = None
        # The template a slot is being chosen for.
        self._pending: tuple[templates.Template, PlanPreset] | None = None
        # "Put a <type> on the deck." for the template clicked last.
        self._missing = ""
        self.strategy = "by_column"
        # Above 0 while the page puts a plan on screen by itself: not a
        # change of the plan in use.
        self._quiet = 0

        self.plate = PlateGrid(planning=True, parent=self)
        self.plate.selection_changed.connect(lambda _s: self._touched())
        self.dropdown = MiniDeckDropdown(self, placeholder="Choose the plate")
        self.dropdown.slot_chosen.connect(self._choose_slot)
        self.tools = PlateTools(self.plate, self)

        left = QVBoxLayout()
        left.setSpacing(SPACING)
        top = QHBoxLayout()
        top.addWidget(self.dropdown)
        top.addStretch(1)
        top.addWidget(self.tools)
        left.addLayout(top)
        self.no_plate = Banner(NO_PLATE, tone="warn", parent=self)
        self.back_to_deck = self.no_plate.add_action("Back to Deck")
        self.back_to_deck.clicked.connect(
            lambda: self.session.page_requested.emit("labware"))
        left.addWidget(self.no_plate)
        left.addWidget(self.plate, 1)

        panel = QWidget(self)
        panel.setFixedWidth(PANEL_WIDTH)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        self._start_from(column)
        self._counts_and_order(column)
        column.addStretch(1)
        column.addWidget(self._summary_card())

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2,
                                  SPACING * 2)
        layout.setSpacing(SPACING * 2)
        layout.addLayout(left, 1)
        layout.addWidget(panel)

        session.robot_state_changed.connect(lambda _s: self._reload_plates())
        session.labware_changed.connect(lambda _s: self._reload_plates())
        session.routine_changed.connect(lambda _r: self._refresh())
        session.plan_chosen.connect(self._plan_chosen)
        # Edit plan is gray while a picking run has the robot.
        session.robot_busy_changed.connect(lambda _h: self._refresh())
        self._install_shortcuts()
        self._reload_plates()

    def _install_shortcuts(self) -> None:
        """Window-wide while the page is showing, as every page's keys are:
        enabled only on screen, so no hidden page claims the key."""
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
            shortcut.activated.connect(
                lambda h=handler: self.plate.isEnabled() and h())
            shortcut.setEnabled(False)
            self._shortcuts.append(shortcut)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        for shortcut in self._shortcuts:
            shortcut.setEnabled(True)
        # A template saved elsewhere, or copied into the folder.
        self._reload_templates()

    def hideEvent(self, event) -> None:
        for shortcut in self._shortcuts:
            shortcut.setEnabled(False)
        super().hideEvent(event)

    # -- construction --------------------------------------------------------

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
        self.template_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.template_list.itemClicked.connect(self._template_clicked)
        box.addWidget(self.template_list)
        self.no_templates = muted_label("No templates saved yet.",
                                        self.template_box)
        box.addWidget(self.no_templates)
        # A template whose plate is not on the deck.
        self.missing_plate = Banner("", tone="warn", parent=self.template_box)
        self.go_to_deck = self.missing_plate.add_action("Go to Deck")
        self.go_to_deck.clicked.connect(
            lambda: self.session.page_requested.emit("labware"))
        self.missing_plate.hide()
        box.addWidget(self.missing_plate)
        # Which plate a template goes on.
        self.which_plate = QWidget(self.template_box)
        which = QVBoxLayout(self.which_plate)
        which.setContentsMargins(0, 0, 0, 0)
        which.setSpacing(SPACING // 2)
        which.addWidget(caption(WHICH_PLATE, self.which_plate))
        self.slot_chooser = SlotChooser(self.which_plate)
        self.slot_chooser.slot_clicked.connect(self._template_slot)
        which.addWidget(self.slot_chooser,
                        alignment=Qt.AlignmentFlag.AlignLeft)
        self.which_plate.hide()
        box.addWidget(self.which_plate)
        self.open_file_button = secondary_button("Open from file…",
                                                 self.template_box)
        self.open_file_button.clicked.connect(self._open_file)
        box.addWidget(self.open_file_button,
                      alignment=Qt.AlignmentFlag.AlignLeft)
        column.addWidget(self.template_box)

        self.choose_wells = Banner(CHOOSE_WELLS, parent=self)
        column.addWidget(self.choose_wells)
        # What a template could not do ("No plate of this kind…").
        self.message = muted_label("", self)
        column.addWidget(self.message)
        self._template_mode = False
        self.new_tile.setChecked(True)

    def _counts_and_order(self, column: QVBoxLayout) -> None:
        column.addWidget(caption("Cuboids per well", self))
        row = QHBoxLayout()
        self.cuboids = QLineEdit("1", self)
        self.cuboids.setValidator(QIntValidator(1, MAX_COUNT, self.cuboids))
        self.cuboids.setFixedWidth(96)
        self.cuboids.textEdited.connect(self._count_typed)
        row.addWidget(self.cuboids)
        row.addStretch(1)
        column.addLayout(row)

        column.addWidget(caption("Fill order", self))
        row = QHBoxLayout()
        self.order_tiles: dict[str, QPushButton] = {}
        for strategy, label, name in ORDERS:
            button = self._tile(label, 56)
            button.setIcon(icon(name, COLORS["text-2"], 24))
            button.setIconSize(QSize(24, 24))
            button.clicked.connect(
                lambda _c=False, s=strategy: self._set_strategy(s))
            self.order_tiles[strategy] = button
            row.addWidget(button)
        column.addLayout(row)

    def _summary_card(self) -> QWidget:
        box = card(self)
        self.summary = muted_label(NO_WELLS, box)
        box.layout().addWidget(self.summary)
        self.use_button = primary_button("Use this plan", box)
        self.use_button.clicked.connect(self._use_or_edit)
        box.layout().addWidget(self.use_button)

        self.save_as_button = secondary_button("Save as template", box)
        self.save_as_button.clicked.connect(self._ask_name)
        box.layout().addWidget(self.save_as_button)
        # Save as template's name, asked here rather than in a dialog.
        self.name_row = QWidget(box)
        row = QHBoxLayout(self.name_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.name_field = QLineEdit(self.name_row)
        self.name_field.setPlaceholderText("Template name")
        self.name_field.returnPressed.connect(self._save_as)
        self.save_name_button = primary_button("Save", self.name_row)
        self.save_name_button.clicked.connect(self._save_as)
        self.cancel_name_button = secondary_button("Cancel", self.name_row)
        self.cancel_name_button.clicked.connect(self._cancel_name)
        row.addWidget(self.name_field, 1)
        row.addWidget(self.save_name_button)
        row.addWidget(self.cancel_name_button)
        self.name_row.hide()
        box.layout().addWidget(self.name_row)
        self.save_changes_button = secondary_button(
            "Save changes to template", box)
        self.save_changes_button.clicked.connect(self._save_changes)
        box.layout().addWidget(self.save_changes_button)
        self.template_note = muted_label("", box)
        box.layout().addWidget(self.template_note)
        return box

    # -- the plate -----------------------------------------------------------

    def _loaded_plates(self) -> list[tuple[str, str, str]]:
        """(slot, load_name, display name) for every labware in the run that
        something could be delivered into, nearest slot first."""
        return [(slot, entry.load_name, definition.display_name)
                for slot, entry, definition in self.session.plates()]

    def _reload_plates(self) -> None:
        """The plates the robot session holds, in the dropdown. The plate
        chosen stays while it is there; else the plan in use, else the
        first plate."""
        self._quiet += 1
        try:
            self._show_plates()
        finally:
            self._quiet -= 1

    def _show_plates(self) -> None:
        plates = self.session.plates()
        self.dropdown.set_tiles([
            DeckTile(int(slot), name=definition.display_name,
                     kind=kind(definition), facts=facts(definition))
            for slot, _entry, definition in plates if slot.isdigit()])
        held = {slot: entry.load_name for slot, entry, _d in plates}
        current = self.destination
        if current is not None and held.get(str(current.slot)) \
                == current.load_name:
            self._refresh()
            return
        routine = self.session.routine
        if routine is not None and held.get(
                str(routine.destination.slot)) == routine.destination.load_name:
            self._show_routine(routine)
        elif plates:
            self._choose_slot(int(plates[0][0]))
        else:
            self._set_destination(None)
        self._refresh()

    def _choose_slot(self, slot: int) -> None:
        if self.destination is not None and self.destination.slot == slot:
            return
        load_name = dict(self._loaded_plates_by_slot()).get(str(slot))
        if load_name is None:
            return
        try:
            destination = Destination.from_labware(load_name, slot)
        except (RoutineError, LabwareError) as exc:
            self._say(str(exc))
            log.error("%s", exc)
            return
        if self.destination is not None \
                and self.destination.load_name == load_name:
            # The same kind of plate in another slot: the same plan on it.
            self._set_destination(destination, self.plate.plan)
        else:
            # Another kind: the wells chosen mean nothing on it.
            self._close_template()
            self._set_destination(destination)
        self._touched()

    def _loaded_plates_by_slot(self) -> list[tuple[str, str]]:
        return [(slot, load_name)
                for slot, load_name, _d in self._loaded_plates()]

    def _set_destination(self, destination: Destination | None,
                         plan=None) -> None:
        """A plate, with `plan` on it. Undo starts again on it."""
        self._quiet += 1
        try:
            self.destination = destination
            self.dropdown.set_slot(destination.slot if destination else None)
            self.plate.set_ordering(destination.ordering if destination
                                    else [])
            self.plate.set_plan(plan or {}, record=False)
        finally:
            self._quiet -= 1

    # -- the plan ------------------------------------------------------------

    def plan(self) -> dict[str, int]:
        """The plan on screen: the wells planned, each with its count."""
        return self.plate.plan

    def _count_typed(self, text: str) -> None:
        if text.isdigit() and 1 <= int(text) <= MAX_COUNT:
            self.plate.set_count(int(text))

    def _set_strategy(self, strategy: str) -> None:
        self.strategy = strategy
        self._touched()

    def _touched(self) -> None:
        """The plan on screen changed: the session's lets go once it
        differs. The page is read-only while it is in use, so this is only
        for a change made from elsewhere."""
        if (not self._quiet and self.session.routine is not None
                and self.session.robot_lease.holder != "picking run"
                and not self._in_use()):
            log.info("plan changed after Use this plan: not in use any more")
            self.session.set_routine(None)
        self._refresh()

    def _show_plan(self, plan: dict[str, int], strategy: str) -> None:
        """`plan` on the grid, nothing selected."""
        self._quiet += 1
        try:
            self.plate.set_plan(plan)
            self.strategy = strategy
        finally:
            self._quiet -= 1

    def _show_routine(self, routine: Routine) -> None:
        """The plan in use, as it was used."""
        self._set_destination(routine.destination)
        self._show_plan(routine.plan, routine.strategy)

    def _plan_chosen(self, routine: Routine) -> None:
        """Choose plan: `routine`'s plate and plan on screen again, for Use
        this plan; the session has let it go."""
        held = {slot: entry.load_name
                for slot, entry, _d in self.session.plates()}
        if held.get(str(routine.destination.slot)) \
                == routine.destination.load_name:
            self._show_routine(routine)
        self._refresh()

    def _in_use(self) -> bool:
        """Whether the plan on screen is the one the session runs."""
        routine, destination = self.session.routine, self.destination
        return (routine is not None and destination is not None
                and not routine.needs_confirmation
                and routine.destination.slot == destination.slot
                and routine.destination.load_name == destination.load_name
                and routine.plan == self.plan()
                and routine.strategy == self.strategy)

    def _use_or_edit(self) -> None:
        if self._in_use():
            self._edit()
        else:
            self._use()

    def _edit(self) -> None:
        """Edit plan: the plan is not in use any more (Plan not done)."""
        if self.session.robot_lease.holder == "picking run":
            return
        log.info("plan edited: not in use any more")
        self.session.set_routine(None)

    def _use(self) -> None:
        plan = self.plan()
        if self.destination is None or not plan:
            return
        now = datetime.now()
        # A routine needs a label for its plate; the slot and the time
        # tell one plate's from the next.
        name = (self.template.name if self.template is not None else
                f"slot {self.destination.slot}") + f" · {now:%Y-%m-%d %H:%M}"
        try:
            routine = Routine(self.destination, plan, name=name,
                              strategy=self.strategy,
                              path=routines_dir()
                              / f"{now:%Y%m%d-%H%M%S}-slot{self.destination.slot}.json")
            routine.save()
        except (RoutineError, OSError) as exc:
            self._say(str(exc))
            log.error("%s", exc)
            return
        log.info("plan in use: %s", routine.summary().replace("\n", " | "))
        self.session.set_routine(routine)
        show_toast(self, PLAN_READY)

    # -- templates -----------------------------------------------------------

    def _set_mode(self, template: bool) -> None:
        if not template and (self._template_mode or self.template is not None):
            # A new plan: the template is closed and its wells cleared - a
            # change Undo takes back.
            self._close_template()
            self.plate.clear()
        self._template_mode = template
        if template:
            self._reload_templates()
        self._refresh()

    def _reload_templates(self) -> None:
        self.template_list.clear()
        for template in self.session.plate_templates():
            item = QListWidgetItem(f"{template.name}\n{template.facts()}")
            item.setData(Qt.ItemDataRole.UserRole, template)
            item.setSizeHint(QSize(0, TEMPLATE_ROW_HEIGHT))
            self.template_list.addItem(item)
        self._refresh()

    def _template_clicked(self, item: QListWidgetItem) -> None:
        self.open_template(item.data(Qt.ItemDataRole.UserRole))

    def _open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open a template",
            str(templates.templates_dir(templates.PLATE_PLANS)),
            "Templates (*.json)")
        if path:
            self.open_template(path)

    def open_template(self, template: templates.Template | str | Path) -> None:
        """Ask which plate `template` goes on: the plates of its type on
        the deck, on the slot chooser, else say to put one there. A path is
        a file from anywhere (Open from file…), named as the list would
        name it."""
        path = (template.path if isinstance(template, templates.Template)
                else Path(template))
        try:
            preset = PlanPreset.load(path)
            if not isinstance(template, templates.Template):
                template = templates.Template(
                    preset.name or path.stem, path, len(preset.plan),
                    datetime.fromtimestamp(path.stat().st_mtime))
        except (RoutineError, OSError) as exc:
            self._say(str(exc))
            return
        self._say("")
        slots = {int(slot) for slot, load_name
                 in self._loaded_plates_by_slot()
                 if load_name == preset.load_name and slot.isdigit()}
        if not slots:
            self._pending = None
            self._missing = (f"Put a {_type_name(preset.load_name)} "
                             f"on the deck.")
            self.missing_plate.set_text(self._missing)
            self._refresh()
            return
        self._pending, self._missing = (template, preset), ""
        self.slot_chooser.set_tiles(self.dropdown.tiles.values())
        self.slot_chooser.set_marked(None)
        for slot in self.slot_chooser.slots:
            self.slot_chooser.button(slot).setEnabled(slot in slots)
        self._refresh()

    def _template_slot(self, slot: int) -> None:
        """The template asked for goes on the plate in `slot`."""
        if self._pending is None:
            return
        template, preset = self._pending
        try:
            destination = preset.destination(slot)
            plan = preset.plan_for(destination)
        except (RoutineError, LabwareError) as exc:
            self._say(str(exc))
            return
        self._pending = None
        if self.destination is None or self.destination.slot != slot:
            self._set_destination(destination)
        self._show_plan(plan, preset.strategy)
        self.template = template
        self._opened = (self.plan(), self.strategy)
        log.info("plan from template %r on slot %s", template.name, slot)
        self._touched()

    def _close_template(self) -> None:
        self.template = None
        self._opened = None
        self._pending, self._missing = None, ""

    def _edited(self) -> bool:
        return (self.template is not None
                and self._opened != (self.plan(), self.strategy))

    def _preset(self) -> PlanPreset | None:
        try:
            return PlanPreset.from_plan(self.destination, self.plan(),
                                        strategy=self.strategy)
        except RoutineError as exc:
            self._say(str(exc))
            return None

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

    def _save_as(self) -> None:
        preset = self._preset()
        if preset is None:
            return
        try:
            template = self.session.save_plate_template(
                preset, self.name_field.text())
        except (SessionError, RoutineError, OSError) as exc:
            self.template_note.setText(str(exc))
            return
        self.name_row.hide()
        self.template_note.setText("")
        self.template = template
        self._opened = (self.plan(), self.strategy)
        self._reload_templates()
        show_toast(self, f'Template "{template.name}" saved.')

    def _save_changes(self) -> None:
        if not self._edited():
            return
        preset = self._preset()
        if preset is None:
            return
        try:
            self.template = self.session.save_plate_template_changes(
                self.template, preset)
        except (RoutineError, OSError) as exc:
            self.template_note.setText(str(exc))
            return
        self._opened = (self.plan(), self.strategy)
        self._reload_templates()
        show_toast(self, f'Changes saved to "{self.template.name}".')

    # -- display -------------------------------------------------------------

    def _say(self, text: str) -> None:
        self.message.setText(text)
        self.message.setVisible(bool(text))

    def _refresh(self) -> None:
        has_plate = self.destination is not None
        plan = self.plan()
        in_use = self._in_use()
        editable = has_plate and not in_use
        self.no_plate.setVisible(not self.session.plates())
        for widget in (self.plate, self.tools, self.cuboids,
                       self.template_tile, self.new_tile, self.template_box,
                       *self.order_tiles.values()):
            widget.setEnabled(editable)
        self.dropdown.setEnabled(not in_use)
        self._show_count()

        self.template_tile.setChecked(self._template_mode)
        self.new_tile.setChecked(not self._template_mode)
        self.template_box.setVisible(self._template_mode)
        listed = self.template_list.count()
        self.template_list.setVisible(bool(listed))
        self.template_list.setMinimumHeight(
            min(listed, TEMPLATE_ROWS) * TEMPLATE_ROW_HEIGHT + 4)
        self.template_list.setMaximumHeight(listed * TEMPLATE_ROW_HEIGHT + 4)
        self.no_templates.setVisible(not listed)
        self.missing_plate.setVisible(bool(self._missing))
        self.which_plate.setVisible(self._pending is not None)
        self.choose_wells.setVisible(not self._template_mode and has_plate
                                     and not plan)
        self.message.setVisible(bool(self.message.text()))

        for strategy, button in self.order_tiles.items():
            button.setChecked(strategy == self.strategy)
        self.summary.setText(summary(plan))
        self.use_button.setText("Edit plan" if in_use else "Use this plan")
        self.use_button.setEnabled(
            self.session.robot_lease.holder != "picking run" if in_use
            else has_plate and bool(plan))

        naming = self.name_row.isVisible()
        self.save_as_button.setVisible(self.template is None and not naming)
        self.save_as_button.setEnabled(has_plate and bool(plan))
        self.save_changes_button.setVisible(self._edited())

    def _show_count(self) -> None:
        """The Cuboids field: the selection's count, empty and "mixed"
        when they differ, else the next well's."""
        plan = self.plan()
        counts = {plan[w] for w in self.plate.selection if w in plan}
        mixed = len(counts) > 1
        text = ("" if mixed else str(counts.pop() if counts
                                     else self.plate.new_count))
        self.cuboids.setPlaceholderText(MIXED if mixed else "")
        if self.cuboids.text() != text:
            self.cuboids.setText(text)


def _type_name(load_name: str) -> str:
    """A plate type as the deck names it, else its load name."""
    try:
        return resolve_definition(load_name).display_name
    except LabwareError:
        return load_name
