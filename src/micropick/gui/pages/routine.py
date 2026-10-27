"""The Plan step of Auto pick (REDESIGN §6.3): which wells of which plate
get how many cuboids, and in what order.

**Left:** the plate, chosen with the `MiniDeckDropdown` - the plates the
robot session holds, by slot - and `PlateTools` (Undo, Redo, Select all,
Clear) over the `PlateGrid`. The wells chosen on the grid are the plan;
the gestures are not explained on screen (§3).

**Right:** Start from **A template** or **New plan**. A template is a named
file (`config.templates`, `Session.plate_templates`): a short list that
scrolls inside its own box, each "<name>" over "24 wells · saved Oct 20",
and Open from file… for one from anywhere. A click puts it on a plate of
its kind - the one chosen if it is, else the first slot holding one.
Then **Cuboids per well** (a stepper), **Fill order** as two tiles, and the
summary: "24 wells × 1 cuboid", **Use this plan** and then "Plan ready".
Save as template asks for a name inline, for a plan not from a template;
Save changes to "<name>" writes over the template the plan came from once
it was changed. Neither asks "are you sure".

**Counts.** One number for the wells chosen, the stepper's. A template
whose wells differ keeps its own counts (`_counts`) until the stepper is
changed, which sets every chosen well to its number; a well added to the
plan takes the stepper's.

**Use this plan** makes the `Routine` a run delivers into, with fresh
progress, and hands it to the session (`Session.set_routine`): Plan is
done while it is the session's (`tasks.step_state`). Its file - the run's
progress, which lets a run carry on after a restart - is written under
`Outputs/routines` by itself and never shown here: an unfinished run is
for Picking to offer. A change to the plan after Use this plan - the
wells, the count, the order, the plate, a template opened - lets the
session's go (`Session.set_routine(None)`): Plan is not done until Use
this plan again, so Picking never runs a plan other than the one shown.
Not while a picking run has the robot, and not for what the page shows by
itself (a plate reloaded, the plan in use put back on screen).

Ctrl+Z / Ctrl+Y undo and redo a change of the wells chosen, Ctrl+A chooses
them all.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout, QWidget)

from ... import paths
from ...config import templates
from ...config.labware import LabwareError
from ...core.routine import Destination, PlanPreset, Routine, RoutineError
from ..labware_kinds import facts, kind
from ..session import Session, SessionError
from ..theme import COLORS, SPACING
from ..theme.factory import (caption, card, muted_label, primary_button,
                             secondary_button, spin_box)
from ..theme.icons import icon
from ..widgets.banner import Banner
from ..widgets.mini_deck import DeckTile, MiniDeckDropdown
from ..widgets.plate_grid import PlateGrid, PlateTools
from ..widgets.toast import show_toast

__all__ = ["RoutinePage"]

# On screen a Routine is a "plan" (REDESIGN §12.9): the step's name.
TITLE = "Plan"

log = logging.getLogger(__name__)

PANEL_WIDTH = 420

# The templates box: about three rows high, scrolling inside itself.
TEMPLATE_ROWS = 3
TEMPLATE_ROW_HEIGHT = 46

NO_PLATE = "There is no plate on the deck. Add it on the Deck step."
CHOOSE_WELLS = "Choose the wells on the plate."
NO_WELLS = "No wells chosen yet."
PLAN_READY = "Plan ready"

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
        # A template's counts that differ from the stepper's (see the module
        # docstring).
        self._counts: dict[str, int] = {}
        self.strategy = "by_column"
        # Above 0 while the page puts a plan on screen by itself: not a
        # change of the plan in use.
        self._quiet = 0

        self.plate = PlateGrid(parent=self)
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
            shortcut.activated.connect(handler)
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
        self.template_list.setFixedHeight(TEMPLATE_ROWS * TEMPLATE_ROW_HEIGHT
                                          + 4)
        self.template_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.template_list.itemClicked.connect(self._template_clicked)
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
        self.per_well = spin_box(self)
        self.per_well.setRange(1, 99)
        self.per_well.setValue(1)
        self.per_well.setFixedWidth(96)
        self.per_well.valueChanged.connect(self._per_well_changed)
        row.addWidget(self.per_well)
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
        self.use_button.clicked.connect(self._use)
        box.layout().addWidget(self.use_button)
        self.ready = Banner(PLAN_READY, tone="ok", parent=box)
        box.layout().addWidget(self.ready)

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
        self.save_changes_button = secondary_button("Save changes", box)
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
            plan, counts = self.plate.selection, self._counts
            self._set_destination(destination, plan)
            self._counts = counts
        else:
            # Another kind: the wells chosen mean nothing on it.
            self._close_template()
            self._set_destination(destination)
        self._touched()

    def _loaded_plates_by_slot(self) -> list[tuple[str, str]]:
        return [(slot, load_name)
                for slot, load_name, _d in self._loaded_plates()]

    def _set_destination(self, destination: Destination | None,
                         wells=()) -> None:
        """A plate, with `wells` chosen. Undo starts again on it."""
        self._quiet += 1
        try:
            self.destination = destination
            self.dropdown.set_slot(destination.slot if destination else None)
            self.plate.set_ordering(destination.ordering if destination
                                    else [])
            self.plate.set_selection(wells, record=False)
            self._counts = {}
        finally:
            self._quiet -= 1

    # -- the plan ------------------------------------------------------------

    def plan(self) -> dict[str, int]:
        """The plan on screen: the wells chosen, each with its count."""
        n = self.per_well.value()
        return {w: self._counts.get(w, n) for w in self.plate.selection}

    def _per_well_changed(self, _value: int) -> None:
        self._counts = {}
        self._touched()

    def _set_strategy(self, strategy: str) -> None:
        self.strategy = strategy
        self._touched()

    def _touched(self) -> None:
        """The plan on screen changed: the session's lets go once it
        differs (see the module docstring)."""
        if (not self._quiet and self.session.routine is not None
                and self.session.robot_lease.holder != "picking run"
                and not self._in_use()):
            log.info("plan changed after Use this plan: not in use any more")
            self.session.set_routine(None)
        self._refresh()

    def _show_plan(self, plan: dict[str, int], strategy: str) -> None:
        """`plan` as the stepper and the wells chosen: the stepper at its
        most common count, the others kept."""
        common = Counter(plan.values()).most_common(1)[0][0] if plan else 1
        self._quiet += 1
        try:
            self.per_well.blockSignals(True)
            self.per_well.setValue(common)
            self.per_well.blockSignals(False)
            self.plate.set_selection(set(plan))
            self._counts = {w: n for w, n in plan.items() if n != common}
            self.strategy = strategy
        finally:
            self._quiet -= 1

    def _show_routine(self, routine: Routine) -> None:
        """The plan in use, as it was used."""
        self._set_destination(routine.destination)
        self._show_plan(routine.plan, routine.strategy)

    def _in_use(self) -> bool:
        """Whether the plan on screen is the one the session runs."""
        routine, destination = self.session.routine, self.destination
        return (routine is not None and destination is not None
                and not routine.needs_confirmation
                and routine.destination.slot == destination.slot
                and routine.destination.load_name == destination.load_name
                and routine.plan == self.plan()
                and routine.strategy == self.strategy)

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
        """The template on a plate of its kind: the one chosen if it is,
        else the first slot holding one. A path is a file from anywhere
        (Open from file…), named as the list would name it."""
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
        slots = [slot for slot, load_name in self._loaded_plates_by_slot()
                 if load_name == preset.load_name]
        current = self.destination
        slot = (str(current.slot) if current is not None
                and str(current.slot) in slots
                else (slots[0] if slots else None))
        if slot is None:
            self._say(f'No plate on the deck is the kind "{template.name}" '
                      f"is for.")
            return
        try:
            if current is None or str(current.slot) != slot:
                self._set_destination(preset.destination(int(slot)))
            plan = preset.plan_for(self.destination)
        except (RoutineError, LabwareError) as exc:
            self._say(str(exc))
            return
        self._show_plan(plan, preset.strategy)
        self.template = template
        self._opened = (self.plan(), self.strategy)
        self._say("")
        log.info("plan from template %r on slot %s", template.name, slot)
        self._touched()

    def _close_template(self) -> None:
        self.template = None
        self._opened = None

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
        self.no_plate.setVisible(not self.session.plates())
        self.plate.setEnabled(has_plate)
        self.tools.setEnabled(has_plate)
        self.plate.set_counts({w: n for w, n in plan.items() if n > 1})

        self.template_tile.setChecked(self._template_mode)
        self.new_tile.setChecked(not self._template_mode)
        self.template_box.setVisible(self._template_mode)
        listed = self.template_list.count() > 0
        self.template_list.setVisible(listed)
        self.no_templates.setVisible(not listed)
        self.choose_wells.setVisible(not self._template_mode and has_plate
                                     and not plan)
        self.message.setVisible(bool(self.message.text()))

        for strategy, button in self.order_tiles.items():
            button.setChecked(strategy == self.strategy)
        self.summary.setText(summary(plan))
        in_use = self._in_use()
        self.use_button.setEnabled(has_plate and bool(plan) and not in_use)
        self.ready.setVisible(in_use)

        naming = self.name_row.isVisible()
        self.save_as_button.setVisible(self.template is None and not naming)
        self.save_as_button.setEnabled(has_plate and bool(plan))
        edited = self._edited()
        self.save_changes_button.setVisible(edited)
        if self.template is not None:
            self.save_changes_button.setText(
                f'Save changes to "{self.template.name}"')
