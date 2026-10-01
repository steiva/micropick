"""Telling the run what is on the deck.

The robot only knows the labware it was told about. A plate set down by hand is
invisible to every `move_to_well`, and a plate loaded into the run and then
lifted off by hand is still there as far as the run is concerned. This page is
where the telling happens: a deck with a clickable slot each, a list of every
definition the robot can be given, and two acts — load this into that slot, or
take what is in that slot off the deck with `move_labware('offDeck')`.

Definitions come from the two places `config.labware` reads: the custom ones
in `labware/`, made for this rig and uploaded into the run before loading, and
the stock Opentrons ones from `opentrons-shared-data`, which the robot already
holds. Custom ones are listed first because they are the reason the list
exists; the stock list is long, so a filter box sits above it. Above both,
"Recently used": the last few load names loaded from this page, kept per
profile in `deck.json`, because a bench loads the same handful of plates
every day and the stock list is hundreds long.

Everything the page shows comes from the run state the robot reports, re-read
after every change, rather than from a record the page keeps of what it asked
for. That is the same rule `Routine.check_labware` follows: what the robot
says is on the deck is the only thing the robot will act on, and a page that
remembered its own version would be the one place those could disagree.

Tips are here too, because a tip comes from a rack in a slot and that is what
this page knows. Once the selected slot holds a tip rack, the Tip card offers
a well to take from and two ways to let go of a tip: in place, or into the
fixed trash. The well can be typed to find it faster, but only a well of the
rack is taken; they are listed row by row, A1 to A12, then B. The trash is
not in a run created over HTTP on current robot software, so the session
loads it into slot 12 the first time it is needed (`Session.ensure_trash`).

Which tip is on the pipette is the robot's answer, read from the run's
command log after connect and after every tip command (`Session.read_tip`),
never a note of what this page asked for. A tip is the first reason the
robot crashes into something, and the case that does it is an operator who
believes the robot knows about a tip it does not, or the reverse. So the
card gates on the record both ways: no pick-up while a tip is on, no drop
while there is none, and nothing at all while the record could not be read
- "unknown" is not "none". The same record drives the indicator in the
status bar, which is on every page.

A card rather than a dialog: picking up a tip is the first of several steps
that go through other pages - jog on Manual control, then come back and drop
- and a dialog would have to be dismissed to get there and reopened to
finish. The controls stay where the rack is.

**Deck modules are the one thing here that ends in a crash if forgotten.**
The picking platform and the calibration module raise the labware in their
slots by an amount the robot's deck model does not have; without being told,
it plans every well move 64 mm too low. The telling is a labware offset,
attached at load time to each labware loaded *after* the offset was
registered - which is why it is not a command to remember but a fact in the
profile (`deck.json`), registered by the session at every connect. What this
page adds is the check the other way round: the run reports the offset on
every labware it holds, and a plate in a module slot without one is drawn
in the hazard colour on the deck, named in the Deck modules card with a
button to load it again, and flagged in the status bar on every page. That
is the case that was one forgotten cell away in the notebook: a plate loaded
before the offsets, or a run carried on from a session that never set them.

A module is placed from a catalogue of module types - "Picking platform",
"Calibration module", and any the operator adds - each with the height it
usually raises the labware by. Choose the type, the height is filled in and
can still be changed, click the slot on the deck, Place. What is stored is a
`DeckModule` with its own height, as before, so the hazard check reads the
same thing it always did, and a type edited or deleted later moves nothing
that is already placed. Modules are the profile's, not the run's: they stay
where they were placed across robot sessions.

Loading and unloading are HTTP and go to a `Worker` like every blocking call.
Neither moves the gantry; picking up a tip and dropping it in the trash do.
"""

from __future__ import annotations

import logging
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QCompleter, QDialog,
                               QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QVBoxLayout, QWidget)

from ...config.labware import (LabwareDefinition, LabwareError,
                               local_definitions, shared_definitions)
from ...config.schema import (DEFAULT_MODULE_HEIGHT_MM, DeckModule,
                              ModuleType)
from ..session import Session
from ..theme import SPACING
from ..theme.factory import (card, combo_box, double_spin_box, heading,
                             primary_button, scroll_column, secondary_button)
from ..widgets.deck_view import TRASH_SLOT, DeckView
from ..workers import Worker

__all__ = ["LabwarePage"]

TITLE = "Robot & Deck"

log = logging.getLogger(__name__)

PANEL_WIDTH = 420

# The definitions list: tall enough to browse in, short enough that the
# Slot and Tip cards under it are on screen.
DEFINITIONS_HEIGHT = 195

# A definition rides on its list item under this role, so the list is the
# only place the catalogue is kept.
DEFINITION_ROLE = Qt.ItemDataRole.UserRole

HEIGHT_RANGE = (-200.0, 200.0)


def _height_box(parent: QWidget):
    box = double_spin_box(parent)
    box.setRange(*HEIGHT_RANGE)
    box.setDecimals(2)
    box.setSingleStep(0.1)
    box.setSuffix(" mm")
    box.setMinimumWidth(110)
    return box


class _TypeDialog(QDialog):
    """A name and a height: a new module for the list."""

    def __init__(self, parent: QWidget, height: float):
        super().__init__(parent)
        self.setWindowTitle("New module")
        self.name = QLineEdit(self)
        self.name.setPlaceholderText("e.g. Cooling block")
        self.height = _height_box(self)
        self.height.setValue(height)
        self.height.setToolTip("How much the module usually raises the "
                               "labware in its slot.")
        form = QFormLayout(self)
        form.addRow("Name", self.name)
        form.addRow("Height", self.height)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel,
                                   parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


class LabwarePage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._worker: Worker | None = None
        self._slot: str | None = None
        # What a load in flight is loading, so a successful one goes into
        # Recently used and a failed one does not.
        self._loading_name: str | None = None
        self._custom: dict = {}
        self._stock: dict = {}
        self._catalogue_notes: list[str] = []
        self._shown_recent: list[str] | None = None

        self.deck = DeckView(self)
        self.deck.slot_clicked.connect(self._slot_clicked)

        panel = QWidget(self)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        # In the order of use: what raises the deck, what to load, where
        # to load it, then a tip from what was loaded.
        column.addWidget(self._modules_card())
        column.addWidget(self._definitions_card())
        column.addWidget(self._slot_card())
        column.addWidget(self._tip_card())
        column.addStretch(1)

        body = QHBoxLayout()
        body.setSpacing(SPACING)
        body.addWidget(self.deck, 1)
        body.addWidget(scroll_column(panel, PANEL_WIDTH))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2, SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addLayout(body, 1)

        session.robot_state_changed.connect(self._on_robot_changed)
        session.labware_changed.connect(self._on_labware_changed)
        session.tip_changed.connect(self._on_tip_changed)
        session.profile_changed.connect(lambda _p: self._on_profile_changed())

        self._reload_definitions()
        self._show_types()
        self._refresh()

    # -- construction --------------------------------------------------------

    def _modules_card(self) -> QWidget:
        """First on the panel, because it is the one that prevents a crash."""
        box = card(self)
        box.layout().addWidget(heading("Deck modules", 2))
        self.modules_state = QLabel()
        self.modules_state.setWordWrap(True)
        self.modules_state.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addWidget(self.modules_state)

        # The hazard, when there is one: its own label so it can be bold and
        # amber, and a button that does the one thing that fixes it.
        self.hazard = QLabel()
        self.hazard.setWordWrap(True)
        self.hazard.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.hazard.hide()
        box.layout().addWidget(self.hazard)
        self.reload_button = primary_button("Load again with the offset", self)
        self.reload_button.clicked.connect(self._reload_problems)
        self.reload_button.hide()
        box.layout().addWidget(self.reload_button)

        self.modules_list = QListWidget(self)
        self.modules_list.setMaximumHeight(72)
        self.modules_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        box.layout().addWidget(self.modules_list)

        # Placing one: a type from the catalogue, its height filled in and
        # still editable, and the slot chosen on the deck.
        row = QHBoxLayout()
        row.addWidget(QLabel("Type"))
        self.module_type = combo_box(self)
        self.module_type.setToolTip("The kind of module. Its usual height is "
                                    "filled in below.")
        self.module_type.currentIndexChanged.connect(self._type_chosen)
        row.addWidget(self.module_type, 1)
        box.layout().addLayout(row)
        row = QHBoxLayout()
        self.new_type_button = secondary_button("New module…", self)
        self.new_type_button.setToolTip("Add a kind of module to the list, "
                                        "with its usual height.")
        self.new_type_button.clicked.connect(self._new_type)
        self.delete_type_button = secondary_button("Delete type", self)
        self.delete_type_button.setToolTip(
            "Take this kind of module off the list. Modules already placed "
            "stay where they are, with their heights.")
        self.delete_type_button.clicked.connect(self._delete_type)
        row.addWidget(self.new_type_button)
        row.addWidget(self.delete_type_button)
        row.addStretch(1)
        box.layout().addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Height"))
        self.module_height = _height_box(self)
        self.module_height.setToolTip("How much this module raises the "
                                      "labware in its slot.")
        row.addWidget(self.module_height)
        row.addStretch(1)
        box.layout().addLayout(row)

        buttons = QHBoxLayout()
        self.add_module_button = primary_button("Place on slot", self)
        self.add_module_button.setToolTip(
            "Put this module in the slot chosen on the deck, replacing any "
            "module there.")
        self.add_module_button.clicked.connect(self._add_module)
        self.remove_module_button = secondary_button("Remove from slot", self)
        self.remove_module_button.setToolTip("Take the module out of the slot "
                                             "chosen on the deck.")
        self.remove_module_button.clicked.connect(self._remove_module)
        buttons.addWidget(self.add_module_button)
        buttons.addWidget(self.remove_module_button)
        buttons.addStretch(1)
        box.layout().addLayout(buttons)
        return box

    def _slot_card(self) -> QWidget:
        box = card(self)
        self.slot_heading = heading("Slot", 2)
        box.layout().addWidget(self.slot_heading)
        self.slot_state = QLabel()
        self.slot_state.setWordWrap(True)
        self.slot_state.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addWidget(self.slot_state)

        row = QHBoxLayout()
        self.load_button = primary_button("Load", self)
        self.load_button.clicked.connect(self._load)
        self.remove_button = secondary_button("Remove (off deck)", self)
        self.remove_button.clicked.connect(self._remove)
        row.addWidget(self.load_button)
        row.addWidget(self.remove_button)
        row.addStretch(1)
        box.layout().addLayout(row)

        # Refusals land here rather than in a dialog. A slot the robot will
        # not load into is ordinary, and its text says what to do.
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addWidget(self.message)
        return box

    def _tip_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Tip", 2))
        self.tip_state = QLabel()
        self.tip_state.setWordWrap(True)
        box.layout().addWidget(self.tip_state)

        row = QHBoxLayout()
        row.addWidget(QLabel("Well"))
        self.tip_well = combo_box(self)
        self.tip_well.setMinimumWidth(80)
        # Typed to find a well, never to make one: the completer offers the
        # rack's wells as they are typed, and whatever is left in the box
        # when editing ends is a well of the rack or goes back to the last.
        self.tip_well.setEditable(True)
        self.tip_well.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        completer = self.tip_well.completer()
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.tip_well.lineEdit().editingFinished.connect(self._well_typed)
        row.addWidget(self.tip_well)
        self.pick_button = primary_button("Pick up tip", self)
        self.pick_button.clicked.connect(self._pick_up)
        row.addWidget(self.pick_button)
        row.addStretch(1)
        box.layout().addLayout(row)

        # A row of their own: beside the well at this panel's width the
        # buttons get about 120 px each and "Drop in trash" needs 150.
        drops = QHBoxLayout()
        self.drop_place_button = secondary_button("Drop in place", self)
        self.drop_place_button.clicked.connect(self._drop_in_place)
        self.drop_trash_button = secondary_button("Drop in trash", self)
        self.drop_trash_button.clicked.connect(self._drop_in_trash)
        drops.addWidget(self.drop_place_button)
        drops.addWidget(self.drop_trash_button)
        box.layout().addLayout(drops)
        return box

    def _definitions_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Definition", 2))
        self.filter = QLineEdit(self)
        self.filter.setPlaceholderText("filter by name…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._apply_filter)
        box.layout().addWidget(self.filter)

        self.definitions = QListWidget(self)
        self.definitions.setFixedHeight(DEFINITIONS_HEIGHT)
        self.definitions.currentItemChanged.connect(
            lambda _cur, _prev: self._refresh())
        self.definitions.itemDoubleClicked.connect(lambda _item: self._load())
        box.layout().addWidget(self.definitions)

        self.catalogue_note = QLabel()
        self.catalogue_note.setWordWrap(True)
        box.layout().addWidget(self.catalogue_note)
        return box

    # -- the catalogue -------------------------------------------------------

    def _reload_definitions(self) -> None:
        """Read the catalogue, once: neither list changes while the
        application runs, short of editing labware/ underneath it."""
        notes = []
        try:
            self._custom = local_definitions()
        except LabwareError as exc:
            self._custom = {}
            notes.append(str(exc))
        try:
            self._stock = shared_definitions()
        except LabwareError as exc:
            self._stock = {}
            notes.append(str(exc))
        notes.append(f"{len(self._custom)} custom, "
                     f"{len(set(self._stock) - set(self._custom))} stock "
                     f"definitions")
        self._catalogue_notes = notes
        self._fill_definitions()

    def _recent(self) -> list[str]:
        profile = self.session.profile
        return list(profile.deck.recent_labware) if profile is not None else []

    def _fill_definitions(self) -> None:
        """Recently used, then custom, then stock; the chosen one and the
        filter kept."""
        chosen = self.chosen_definition()
        recent = self._recent()
        self._shown_recent = recent
        self.definitions.clear()
        known = {**self._stock, **self._custom}
        used = [known[name] for name in recent if name in known]
        if used:
            self._add_group("Recently used", used, keep_order=True)
        self._add_group("Custom — labware/", self._custom.values())
        # A stock name shadowed by a custom file is the custom one, as
        # resolve_definition would have it; listing both would offer a choice
        # the loader does not make.
        self._add_group("Stock — Opentrons",
                        (d for name, d in self._stock.items()
                         if name not in self._custom))
        self.catalogue_note.setText("\n".join(self._catalogue_notes))
        self._apply_filter(self.filter.text())
        if chosen is not None:
            for index in range(self.definitions.count()):
                item = self.definitions.item(index)
                definition = item.data(DEFINITION_ROLE)
                if (definition is not None and not item.isHidden()
                        and definition.load_name == chosen.load_name):
                    self.definitions.setCurrentItem(item)
                    break

    def _add_group(self, title: str, definitions, *,
                   keep_order: bool = False) -> None:
        header = QListWidgetItem(title)
        header.setFlags(Qt.ItemFlag.NoItemFlags)
        font = header.font()
        font.setBold(True)
        header.setFont(font)
        self.definitions.addItem(header)
        if not keep_order:
            definitions = sorted(definitions,
                                 key=lambda d: d.display_name.lower())
        for definition in definitions:
            item = QListWidgetItem(
                f"{definition.display_name}   ·   {definition.load_name}")
            item.setToolTip(str(definition)
                            + (f"\n{definition.category}" if definition.category else ""))
            item.setData(DEFINITION_ROLE, definition)
            self.definitions.addItem(item)

    def _apply_filter(self, text: str) -> None:
        wanted = text.strip().lower()
        for index in range(self.definitions.count()):
            item = self.definitions.item(index)
            definition = item.data(DEFINITION_ROLE)
            if definition is None:                 # a group header stays
                item.setHidden(False)
                continue
            haystack = " ".join((definition.display_name, definition.load_name,
                                 definition.category)).lower()
            item.setHidden(bool(wanted) and wanted not in haystack)

    def chosen_definition(self) -> LabwareDefinition | None:
        item = self.definitions.currentItem()
        if item is None or item.isHidden():
            return None
        return item.data(DEFINITION_ROLE)

    # -- acting --------------------------------------------------------------

    def _slot_clicked(self, name: str) -> None:
        self._slot = name
        self.deck.select(name)
        self._show_slot_module()
        self._refresh()

    def _load(self) -> None:
        definition = self.chosen_definition()
        if definition is None or self._slot is None or self._busy():
            return
        if self.session.robot is None:
            return
        slot = int(self._slot)
        self._say("")
        self._run(Worker(self.session.load_labware, definition, slot),
                  f"loading {definition.load_name} into slot {slot}")
        self._loading_name = definition.load_name

    def _remove(self) -> None:
        if self._slot is None or self._busy() or self.session.robot is None:
            return
        slot = int(self._slot)
        self._say("")
        self._run(Worker(self.session.unload_labware, slot),
                  f"moving slot {slot} off deck")

    def _pick_up(self) -> None:
        if self._slot is None or self._busy() or self.session.robot is None:
            return
        # The chosen item, not the text in the box: that may still be a
        # half-typed name.
        well = self.tip_well.itemText(self.tip_well.currentIndex())
        if not well:
            return
        self._say("")
        self._run(Worker(self.session.pick_up_tip, self._slot, well),
                  f"picking up a tip from slot {self._slot} {well}")

    def _drop_in_place(self) -> None:
        if self._busy() or self.session.robot is None:
            return
        self._say("")
        self._run(Worker(self.session.drop_tip_in_place),
                  "dropping the tip in place")

    def _drop_in_trash(self) -> None:
        if self._busy() or self.session.robot is None:
            return
        self._say("")
        self._run(Worker(self.session.drop_tip_in_trash),
                  "dropping the tip in the trash")

    def _well_typed(self) -> None:
        """What was typed, as the rack's well of that name (any case), or
        back to the well chosen before."""
        text = self.tip_well.currentText().strip()
        index = self.tip_well.findText(text, Qt.MatchFlag.MatchFixedString)
        if index < 0:
            index = self.tip_well.currentIndex()
        self.tip_well.setCurrentIndex(index)
        self.tip_well.setEditText(self.tip_well.itemText(index))

    # -- deck modules --------------------------------------------------------

    def _module_slot(self) -> int | None:
        """The slot chosen on the deck, if a module can go there."""
        if self._slot is None or not self._slot.isdigit():
            return None
        slot = int(self._slot)
        return slot if 1 <= slot <= 11 else None

    @staticmethod
    def _without_slot(modules, slot: int) -> list[DeckModule]:
        """The modules with `slot` taken out; one left with no slot goes.
        A module placed before types, over several slots, loses only the
        slot asked for."""
        out = []
        for module in modules:
            if slot not in module.slots:
                out.append(module)
            elif len(module.slots) > 1:
                out.append(module.model_copy(update={
                    "slots": [s for s in module.slots if s != slot]}))
        return out

    def _add_module(self) -> None:
        profile, slot = self.session.profile, self._module_slot()
        name = self.module_type.currentText()
        if profile is None or slot is None or not name:
            return
        height = round(self.module_height.value(), 3)
        module = DeckModule(slots=[slot], offset=[0.0, 0.0, height], name=name)
        try:
            self.session.set_deck_modules(
                self._without_slot(profile.deck.modules, slot) + [module])
        except Exception as exc:                     # noqa: BLE001
            self._say(f"not placed: {exc}")
            return
        self._say("")
        self._refresh()

    def _remove_module(self) -> None:
        profile, slot = self.session.profile, self._module_slot()
        if profile is None or slot is None \
                or profile.deck.module_for(slot) is None:
            return
        self.session.set_deck_modules(
            self._without_slot(profile.deck.modules, slot))
        self._refresh()

    # -- module types --------------------------------------------------------

    def _show_types(self) -> None:
        """The profile's catalogue in the chooser, the chosen one kept."""
        profile = self.session.profile
        chosen = self.module_type.currentText()
        self.module_type.blockSignals(True)
        self.module_type.clear()
        if profile is not None:
            for kind in profile.deck.module_types:
                self.module_type.addItem(kind.name)
        index = self.module_type.findText(chosen)
        if self.module_type.count():
            self.module_type.setCurrentIndex(max(0, index))
        self.module_type.blockSignals(False)
        if index < 0:
            self._type_chosen()

    def _type_chosen(self, _index: int = 0) -> None:
        profile = self.session.profile
        kind = (profile.deck.module_type(self.module_type.currentText())
                if profile is not None else None)
        if kind is not None:
            self.module_height.setValue(kind.height_mm)

    def _show_slot_module(self) -> None:
        """A slot that holds a module shows it in the editor, so Place with
        a changed height is how its height is changed."""
        profile, slot = self.session.profile, self._module_slot()
        module = (profile.deck.module_for(slot)
                  if profile is not None and slot is not None else None)
        if module is None:
            return
        index = self.module_type.findText(module.name)
        if index >= 0:
            self.module_type.blockSignals(True)
            self.module_type.setCurrentIndex(index)
            self.module_type.blockSignals(False)
        self.module_height.setValue(module.height_mm)

    def _new_type(self) -> None:
        profile = self.session.profile
        if profile is None:
            return
        dialog = _TypeDialog(self, DEFAULT_MODULE_HEIGHT_MM)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = dialog.name.text().strip()
        if not name:
            return
        if profile.deck.module_type(name) is not None:
            QMessageBox.warning(self, "New module",
                                f"There is already a type called {name!r}.")
            return
        types = list(profile.deck.module_types) + [
            ModuleType(name=name, height_mm=round(dialog.height.value(), 3))]
        self.session.set_module_types(types)
        self.module_type.setCurrentText(name)

    def _delete_type(self) -> None:
        profile, name = self.session.profile, self.module_type.currentText()
        if profile is None or not name:
            return
        answer = QMessageBox.question(
            self, "Delete module type",
            f"Take {name!r} off the list of module types? Modules already "
            f"placed stay where they are.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.session.set_module_types(
            [t for t in profile.deck.module_types if t.name != name])

    def _reload_problems(self) -> None:
        """Load again every labware the run holds without its module's
        offset, one after the other in one job."""
        problems = self.session.deck_problems()
        if not problems or self._busy() or self.session.robot is None:
            return
        session = self.session

        def job():
            for problem in problems:
                session.reload_labware(problem.slot)

        self._say("")
        self._run(Worker(job), "loading again with the module offset: slots "
                  + ", ".join(p.slot for p in problems))

    def _busy(self) -> bool:
        return self._worker is not None and self._worker.running

    def _run(self, worker: Worker, what: str) -> None:
        self._worker = worker
        # Bound methods, not lambdas: Qt takes a connection's thread from the
        # receiver, and a lambda has none.
        worker.finished.connect(self._job_done)
        worker.failed.connect(self._job_failed)
        worker.message.connect(self._job_said)
        log.info("%s", what)
        worker.start()
        self._refresh()

    def _job_said(self, text: str) -> None:
        log.info("%s", text)

    def _job_done(self, _result) -> None:
        if self.sender() is not self._worker:
            return
        self._worker = None
        if self._loading_name is not None:
            self.session.remember_labware(self._loading_name)
            self._loading_name = None
            self._fill_definitions()
        self._refresh()

    def _job_failed(self, reason: str) -> None:
        if self.sender() is not self._worker:
            return
        self._worker = None
        self._loading_name = None
        self._say(reason)
        log.error("labware: %s", reason)
        self._refresh()

    # -- display -------------------------------------------------------------

    def _on_robot_changed(self, _state: str) -> None:
        if self.session.robot is None:
            self._slot = None
            self.deck.select(None)
        self._refresh()

    def _on_labware_changed(self, _state) -> None:
        self._refresh()

    def _on_profile_changed(self) -> None:
        """Another profile has other module types and another history."""
        self._show_types()
        if self._recent() != self._shown_recent:
            self._fill_definitions()
        self._refresh()

    def _on_tip_changed(self, tip) -> None:
        # A rack is used in order: after a pick-up the chooser moves on to
        # the next well, so the next press takes a fresh tip rather than an
        # empty position. The operator can still choose any well.
        if tip.returnable and tip.slot == self._slot:
            index = self.tip_well.findText(tip.well)
            if 0 <= index < self.tip_well.count() - 1:
                self.tip_well.setCurrentIndex(index + 1)
        self._refresh()

    def _fill_wells(self, definition) -> None:
        """The rack's wells, row by row; kept if unchanged so the chooser
        does not jump back to A1 on every refresh."""
        wells = (sorted(definition.wells, key=_row_first)
                 if definition is not None else [])
        current = [self.tip_well.itemText(i) for i in range(self.tip_well.count())]
        if wells == current:
            return
        self.tip_well.clear()
        self.tip_well.addItems(wells)

    def _say(self, text: str) -> None:
        self.message.setText(text)
        self.message.setVisible(bool(text))

    def _refresh(self) -> None:
        session = self.session
        busy = self._busy()
        connected = session.robot is not None
        profile = session.profile
        state = session.run_state
        held = state.labware if (connected and state is not None) else {}

        self.deck.set_labware({slot: self._describe(entry)
                               for slot, entry in held.items()})

        # -- deck modules
        modules = list(profile.deck.modules) if profile is not None else []
        # The height first: it is what the band is for, and what is left
        # when a long type name is cut short.
        self.deck.set_modules({str(slot): f"+{m.height_mm:g} mm · "
                                          f"{m.name or 'module'}"
                               for m in modules for slot in m.slots})
        problems = session.deck_problems()
        self.deck.set_problems({p.slot: p.describe() for p in problems})
        current_rows = [self.modules_list.item(i).text()
                        for i in range(self.modules_list.count())]
        wanted_rows = [m.describe() for m in modules]
        if current_rows != wanted_rows:
            self.modules_list.clear()
            self.modules_list.addItems(wanted_rows)
        if profile is None:
            self.modules_state.setText("No profile loaded.")
        elif not modules:
            self.modules_state.setText(
                "No modules in the profile. If anything on the deck raises "
                "the labware in its slot - the picking platform, the "
                "calibration module - place it here (type, height, click its "
                "slot on the deck), or the robot will plan its well moves to "
                "the slot floor and hit it.")
        elif connected:
            self.modules_state.setText(
                "Registered for loading: every labware loaded into these "
                "slots from now on carries the module's offset.")
        else:
            self.modules_state.setText(
                "Registered on the wrapper at connect; the offset attaches "
                "to labware as it is loaded.")
        if problems:
            self.hazard.setText(
                "<b style='color:#f0a030'>"
                + "<br>".join(p.describe() for p in problems) + "</b>")
        self.hazard.setVisible(bool(problems))
        self.reload_button.setVisible(bool(problems))
        self.reload_button.setEnabled(bool(problems) and not busy)
        slot = self._module_slot()
        here = (profile.deck.module_for(slot)
                if profile is not None and slot is not None else None)
        self.add_module_button.setText(f"Place on slot {slot}"
                                       if slot is not None else "Place on slot")
        self.add_module_button.setEnabled(
            profile is not None and not busy and slot is not None
            and self.module_type.count() > 0)
        self.remove_module_button.setText(f"Remove from slot {slot}"
                                          if slot is not None
                                          else "Remove from slot")
        self.remove_module_button.setEnabled(
            profile is not None and not busy and here is not None)
        self.new_type_button.setEnabled(profile is not None)
        self.delete_type_button.setEnabled(profile is not None
                                           and self.module_type.count() > 0)

        definition = self.chosen_definition()
        entry = held.get(self._slot) if self._slot is not None else None

        if not connected:
            self.slot_heading.setText("Slot")
            self.slot_state.setText(
                "No robot session. Connect the robot on the Profile page; "
                "the deck shows what the robot session holds.")
        elif self._slot is None:
            self.slot_heading.setText("Slot")
            # The trash, once loaded for a drop, is run labware too; it is
            # not something the operator put on the deck.
            count = len([slot for slot in held if slot != TRASH_SLOT])
            self.slot_state.setText(
                f"Click a slot on the deck. The robot session holds "
                f"{count} labware{'s' if count != 1 else ''}.")
        else:
            self.slot_heading.setText(f"Slot {self._slot}")
            if entry is None:
                self.slot_state.setText("empty, as far as the robot session "
                                        "knows.")
            else:
                self.slot_state.setText(
                    f"{self._describe(entry)}\n{entry.namespace}/"
                    f"{entry.load_name}/{entry.version}  ·  id {entry.labware_id}")

        # -- tips
        rack = self._definition_of(entry) if entry is not None else None
        rack = rack if (rack is not None and rack.is_tiprack) else None
        self._fill_wells(rack)
        tip = session.tip
        if not connected:
            self.tip_state.setText("")
        elif tip.attached is None:
            self.tip_state.setText(
                "The robot's tip state could not be read. Nothing here will "
                "move until it is: connect again on the Profile page. A tip "
                "nobody knows about is how the robot crashes.")
        elif tip.attached:
            self.tip_state.setText(
                "The robot reports a tip on the pipette"
                + (f", from slot {tip.slot} {tip.well}" if tip.returnable
                   else ", from labware no longer in a slot")
                + ". Drop it before picking up another.")
        elif rack is not None:
            self.tip_state.setText(
                f"The robot reports no tip. Slot {self._slot} is a tip rack: "
                f"choose a well and pick up.")
        else:
            self.tip_state.setText(
                "The robot reports no tip. Select a slot holding a tip rack "
                "to pick one up.")
        known = connected and not busy and tip.attached is not None
        self.pick_button.setEnabled(known and not tip.attached
                                    and rack is not None
                                    and self.tip_well.count() > 0)
        self.drop_place_button.setEnabled(known and bool(tip.attached))
        self.drop_trash_button.setEnabled(known and bool(tip.attached))

        can_act = connected and self._slot is not None and not busy
        if definition is None:
            self.load_button.setText("Load")
        elif entry is None:
            self.load_button.setText(f"Load into slot {self._slot}"
                                     if self._slot else "Load")
        else:
            self.load_button.setText(f"Replace in slot {self._slot}")
        self.load_button.setEnabled(can_act and definition is not None)
        self.remove_button.setEnabled(can_act and entry is not None)
        self.definitions.setEnabled(not busy)

    def _definition_of(self, entry) -> LabwareDefinition | None:
        """The catalogue's definition for what the run reports, by load name;
        None for a load name the catalogue does not have."""
        for index in range(self.definitions.count()):
            definition = self.definitions.item(index).data(DEFINITION_ROLE)
            if definition is not None and definition.load_name == entry.load_name:
                return definition
        return None

    def _describe(self, entry) -> str:
        """The display name when the catalogue knows the load name, else the
        load name: the run reports only the latter."""
        definition = self._definition_of(entry)
        return definition.display_name if definition is not None else entry.load_name


def _row_first(well: str):
    """A1, A2 ... A12, B1: by row letters, then by column number. A name
    that is not letters and a number goes last, as it is."""
    match = re.fullmatch(r"([A-Za-z]+)(\d+)", well)
    if match is None:
        return (1, "", 0, well)
    row, column = match.groups()
    return (0, row.upper().rjust(4), int(column), well)
