"""Telling the run what is on the deck.

The robot only knows the labware it was told about. A plate set down by hand is
invisible to every `move_to_well`, and a plate loaded into the run and then
lifted off by hand is still there as far as the run is concerned. This page is
where the telling happens: a deck with a clickable slot each, a list of every
definition the robot can be given, and two acts — load this into that slot, or
take what is in that slot off the deck with `move_labware('offDeck')`.

The Slot card comes first and has one load button, "Load into slot N", for
anything that goes into a slot: the labware definition or the deck module
chosen in the card under it. The two are one card because they answer one
question - what goes in the slot - with the deck modules under a rule and
folded away once the profile has them, since they are placed once per bench.
Clicking a line in either list is choosing it, and lets go of the other's
choice, so the button never loads something that is not in view. What the
slot holds is listed in the Slot card, a line each - labware, module - with
its own Remove.

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
in the hazard colour on the deck, named in the Slot card with a
button to load it again, and flagged in the status bar on every page. That
is the case that was one forgotten cell away in the notebook: a plate loaded
before the offsets, or a run carried on from a session that never set them.

A module is placed from a catalogue of module types - "Picking platform",
"Calibration module", and any the operator adds - each with the height it
usually raises the labware by. Choose the type, the height is filled in and
can still be changed, click the slot on the deck, Load. What is stored is a
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
                               QDialogButtonBox, QFormLayout, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QToolButton,
                               QVBoxLayout, QWidget)

from ...config.labware import (LabwareDefinition, LabwareError,
                               local_definitions, shared_definitions)
from ...config.schema import (DEFAULT_MODULE_HEIGHT_MM, DeckModule,
                              ModuleType)
from ..session import Session
from ..theme import SPACING
from ..theme.factory import (card, combo_box, double_spin_box, heading,
                             muted_label, primary_button, scroll_column,
                             secondary_button)
from ..widgets.deck_view import TRASH_SLOT, DeckView
from ..workers import Worker

__all__ = ["LabwarePage"]

TITLE = "Robot & Deck"

log = logging.getLogger(__name__)

PANEL_WIDTH = 420

# The definitions list: tall enough to browse in, short enough that the
# Slot and Tip cards under it are on screen.
DEFINITIONS_HEIGHT = 195

# The module types list: a few types are all a bench has.
MODULE_TYPES_HEIGHT = 84

# What Load into slot puts there.
LABWARE, MODULE = "labware", "module"

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
        # What Load into slot puts there: LABWARE (the chosen definition)
        # or MODULE (the chosen module type), whichever list was clicked
        # last; None before either.
        self._kind: str | None = None
        # What a load in flight is loading, so a successful one goes into
        # Recently used and a failed one does not.
        self._loading_name: str | None = None
        self._custom: dict = {}
        self._stock: dict = {}
        self._catalogue_notes: list[str] = []
        self._shown_recent: list[str] | None = None
        # The profile the Deck modules fold was last set for: it starts
        # folded for a profile that has modules, and is the operator's after.
        self._folded_for: str | None = None

        self.deck = DeckView(self)
        self.deck.slot_clicked.connect(self._slot_clicked)

        panel = QWidget(self)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        # The slot first: it is what is acted on, and its one button loads
        # whatever is chosen below it - labware or a deck module. Then the
        # tip, taken from what was loaded.
        column.addWidget(self._slot_card())
        column.addWidget(self._catalogue_card())
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
        self._fold_modules()
        self._refresh()

    # -- construction --------------------------------------------------------

    def _slot_card(self) -> QWidget:
        """The chosen slot: what is in it, each with its Remove, and the one
        button that loads the chosen labware or module into it."""
        box = card(self)
        self.slot_heading = heading("Slot", 2)
        box.layout().addWidget(self.slot_heading)

        # The hazard, when there is one: bold and amber, and a button that
        # does the one thing that fixes it. Deck-wide, so first.
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

        self.slot_state = QLabel()
        self.slot_state.setWordWrap(True)
        self.slot_state.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addWidget(self.slot_state)

        # What the slot holds, a line each with its own Remove.
        self.slot_labware = QLabel()
        self.slot_labware.setWordWrap(True)
        self.slot_labware.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.remove_button = secondary_button("Remove", self)
        self.remove_button.setToolTip("Take this labware off the deck in the "
                                      "robot session (off deck).")
        self.remove_button.clicked.connect(self._remove)
        row = QHBoxLayout()
        row.addWidget(self.slot_labware, 1)
        row.addWidget(self.remove_button, 0, Qt.AlignmentFlag.AlignTop)
        box.layout().addLayout(row)
        self.slot_module = QLabel()
        self.slot_module.setWordWrap(True)
        self.remove_module_button = secondary_button("Remove", self)
        self.remove_module_button.setToolTip("Take the deck module out of "
                                             "this slot in the profile.")
        self.remove_module_button.clicked.connect(self._remove_module)
        row = QHBoxLayout()
        row.addWidget(self.slot_module, 1)
        row.addWidget(self.remove_module_button, 0, Qt.AlignmentFlag.AlignTop)
        box.layout().addLayout(row)

        self.chosen_state = muted_label()
        box.layout().addWidget(self.chosen_state)
        row = QHBoxLayout()
        self.load_button = primary_button("Load into slot", self)
        self.load_button.clicked.connect(self._load)
        row.addWidget(self.load_button)
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

    def _catalogue_card(self) -> QWidget:
        """What can go into a slot: labware definitions, then - under a
        rule, folded away once the profile has its modules - deck modules."""
        box = card(self)
        box.layout().addWidget(heading("Labware definitions", 2))
        self.filter = QLineEdit(self)
        self.filter.setPlaceholderText("filter by name…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._apply_filter)
        box.layout().addWidget(self.filter)

        self.definitions = QListWidget(self)
        self.definitions.setFixedHeight(DEFINITIONS_HEIGHT)
        self.definitions.currentItemChanged.connect(
            lambda current, _prev: self._definition_chosen(current))
        self.definitions.itemDoubleClicked.connect(lambda _item: self._load())
        box.layout().addWidget(self.definitions)

        self.catalogue_note = QLabel()
        self.catalogue_note.setWordWrap(True)
        box.layout().addWidget(self.catalogue_note)

        rule = QFrame(self)
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setFrameShadow(QFrame.Shadow.Sunken)
        box.layout().addWidget(rule)

        self.modules_toggle = QToolButton(self)
        self.modules_toggle.setObjectName("sectionToggle")
        self.modules_toggle.setText("Deck modules")
        self.modules_toggle.setCheckable(True)
        self.modules_toggle.setChecked(True)
        self.modules_toggle.setAutoRaise(True)
        self.modules_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.modules_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.modules_toggle.toggled.connect(self._modules_toggled)
        box.layout().addWidget(self.modules_toggle)

        self.modules_body = QWidget(self)
        # A plain container inside a card: without this it would paint the
        # window's background over the card's (see micropick.qss).
        self.modules_body.setObjectName("plain")
        body = QVBoxLayout(self.modules_body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(SPACING)
        self.modules_state = QLabel()
        self.modules_state.setWordWrap(True)
        self.modules_state.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(self.modules_state)

        # The catalogue of module types, chosen like a definition: clicked,
        # its usual height filled in below and still editable.
        self.module_types = QListWidget(self)
        self.module_types.setFixedHeight(MODULE_TYPES_HEIGHT)
        self.module_types.currentItemChanged.connect(
            lambda current, _prev: self._type_chosen(current))
        self.module_types.itemDoubleClicked.connect(lambda _item: self._load())
        body.addWidget(self.module_types)

        row = QHBoxLayout()
        row.addWidget(QLabel("Height"))
        self.module_height = _height_box(self)
        self.module_height.valueChanged.connect(lambda _v: self._refresh())
        self.module_height.setToolTip("How much this module raises the "
                                      "labware in its slot.")
        row.addWidget(self.module_height)
        row.addStretch(1)
        body.addLayout(row)

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
        body.addLayout(row)

        body.addWidget(QLabel("Placed in this profile"))
        self.modules_list = QListWidget(self)
        self.modules_list.setMaximumHeight(72)
        self.modules_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        body.addWidget(self.modules_list)
        box.layout().addWidget(self.modules_body)
        return box

    def _modules_toggled(self, shown: bool) -> None:
        self.modules_toggle.setArrowType(Qt.ArrowType.DownArrow if shown
                                         else Qt.ArrowType.RightArrow)
        self.modules_body.setVisible(shown)

    def _fold_modules(self) -> None:
        """Folded for a profile that already has its modules, open for one
        that has none - once per profile, then the operator's."""
        profile = self.session.profile
        name = profile.name if profile is not None else None
        if name == self._folded_for:
            return
        self._folded_for = name
        has = profile is not None and bool(profile.deck.modules)
        self.modules_toggle.setChecked(not has)
        self._modules_toggled(not has)

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
        """The definition Load would load: the one chosen, while labware is
        what was chosen last."""
        if self._kind != LABWARE:
            return None
        item = self.definitions.currentItem()
        if item is None or item.isHidden():
            return None
        return item.data(DEFINITION_ROLE)

    def chosen_type(self) -> str | None:
        """The module type Load would place, while a module is what was
        chosen last."""
        if self._kind != MODULE:
            return None
        item = self.module_types.currentItem()
        return item.text() if item is not None else None

    def _definition_chosen(self, item) -> None:
        """A definition clicked: labware is what Load loads now, and the
        module list lets go of its choice so only one thing is chosen."""
        if item is not None and item.data(DEFINITION_ROLE) is not None:
            self._kind = LABWARE
            self.module_types.blockSignals(True)
            self.module_types.setCurrentItem(None)
            self.module_types.blockSignals(False)
        self._refresh()

    # -- acting --------------------------------------------------------------

    def _slot_clicked(self, name: str) -> None:
        self._slot = name
        self.deck.select(name)
        self._show_slot_module()
        self._refresh()

    def _load(self) -> None:
        """Whatever is chosen, into the chosen slot: a deck module into the
        profile, labware into the robot session."""
        if self._kind == MODULE:
            self._add_module()
            return
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
        name = self.chosen_type()
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
        """The profile's catalogue in the list, the chosen one kept."""
        profile = self.session.profile
        current = self.module_types.currentItem()
        chosen = current.text() if current is not None else None
        names = ([kind.name for kind in profile.deck.module_types]
                 if profile is not None else [])
        self.module_types.blockSignals(True)
        self.module_types.clear()
        self.module_types.addItems(names)
        if chosen in names:
            self.module_types.setCurrentRow(names.index(chosen))
        elif self._kind == MODULE:
            # The chosen type was deleted: nothing is chosen now.
            self._kind = None
        self.module_types.blockSignals(False)

    def _select_type(self, name: str) -> None:
        """Choose the type `name` in the list, as a click would."""
        items = self.module_types.findItems(name, Qt.MatchFlag.MatchExactly)
        if items:
            self.module_types.setCurrentItem(items[0])

    def _type_chosen(self, item) -> None:
        """A type clicked: a module is what Load places now, at the type's
        usual height - or at the height of the one already in the chosen
        slot, if it is of this type, so Load is how a height is changed."""
        if item is None:
            return
        self._kind = MODULE
        self.definitions.blockSignals(True)
        self.definitions.setCurrentItem(None)
        self.definitions.blockSignals(False)
        profile, slot = self.session.profile, self._module_slot()
        here = (profile.deck.module_for(slot)
                if profile is not None and slot is not None else None)
        kind = (profile.deck.module_type(item.text())
                if profile is not None else None)
        if here is not None and here.name == item.text():
            self.module_height.setValue(here.height_mm)
        elif kind is not None:
            self.module_height.setValue(kind.height_mm)
        self._refresh()

    def _show_slot_module(self) -> None:
        """While a module is what is chosen, a slot that holds one shows it
        in the editor - its type and its height - so Load with a changed
        height is how its height is changed."""
        if self._kind != MODULE:
            return
        profile, slot = self.session.profile, self._module_slot()
        module = (profile.deck.module_for(slot)
                  if profile is not None and slot is not None else None)
        if module is None:
            return
        self._select_type(module.name)
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
        self._select_type(name)

    def _delete_type(self) -> None:
        profile = self.session.profile
        current = self.module_types.currentItem()
        if profile is None or current is None:
            return
        name = current.text()
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
        worker.what = worker.what or what
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
        self._fold_modules()
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
                "calibration module - choose it here, click its slot on the "
                "deck and Load it, or the robot will plan its well moves to "
                "the slot floor and hit it.")
        else:
            self.modules_state.setText(
                "Every labware loaded into a module's slot carries the "
                "module's offset. Choose a type and Load it into a slot to "
                "place one or change its height.")
        if problems:
            self.hazard.setText(
                "<b style='color:#f0a030'>"
                + "<br>".join(p.describe() for p in problems) + "</b>")
        self.hazard.setVisible(bool(problems))
        self.reload_button.setVisible(bool(problems))
        self.reload_button.setEnabled(bool(problems) and not busy)
        self.new_type_button.setEnabled(profile is not None)
        self.delete_type_button.setEnabled(
            profile is not None and self.module_types.currentItem() is not None)

        # -- the slot
        definition = self.chosen_definition()
        type_name = self.chosen_type()
        entry = held.get(self._slot) if self._slot is not None else None
        module_slot = self._module_slot()
        here = (profile.deck.module_for(module_slot)
                if profile is not None and module_slot is not None else None)

        if self._slot is None:
            self.slot_heading.setText("Slot")
            if connected:
                # The trash, once loaded for a drop, is run labware too; it
                # is not something the operator put on the deck.
                count = len([slot for slot in held if slot != TRASH_SLOT])
                self.slot_state.setText(
                    f"Click a slot on the deck. The robot session holds "
                    f"{count} labware{'s' if count != 1 else ''}.")
            else:
                self.slot_state.setText(
                    "Click a slot on the deck. Labware needs the robot: "
                    "connect it on the Profile page.")
        else:
            self.slot_heading.setText(f"Slot {self._slot}")
            self.slot_state.setText(
                "" if connected else
                "No robot session: labware cannot be loaded or seen. "
                "Connect the robot on the Profile page.")
        self.slot_state.setVisible(bool(self.slot_state.text()))

        show = self._slot is not None
        if connected and show:
            self.slot_labware.setText(
                f"Labware: {self._describe(entry)}\n{entry.namespace}/"
                f"{entry.load_name}/{entry.version}" if entry is not None
                else "Labware: none, as far as the robot session knows.")
        self.slot_labware.setVisible(connected and show)
        self.remove_button.setVisible(connected and show and entry is not None)
        self.remove_button.setEnabled(not busy)
        if show and module_slot is not None:
            self.slot_module.setText(
                f"Deck module: {here.name or 'module'}, raises the labware "
                f"+{here.height_mm:g} mm" if here is not None
                else "Deck module: none.")
        self.slot_module.setVisible(show and module_slot is not None)
        self.remove_module_button.setVisible(show and here is not None)
        self.remove_module_button.setEnabled(not busy)

        # The one button: whatever is chosen, into this slot.
        if definition is not None:
            self.chosen_state.setText(f"Chosen: {definition.display_name} "
                                      f"(labware)")
            replacing = entry is not None
            ready = connected and show
        elif type_name is not None:
            self.chosen_state.setText(
                f"Chosen: {type_name}, +{self.module_height.value():g} mm "
                f"(deck module)")
            replacing = here is not None
            ready = profile is not None and module_slot is not None
        else:
            self.chosen_state.setText(
                "Choose labware or a deck module below, then a slot on the "
                "deck.")
            replacing, ready = False, False
        where = f" slot {self._slot}" if show else " slot"
        self.load_button.setText(("Replace in" if replacing else "Load into")
                                 + where)
        self.load_button.setEnabled(ready and not busy)
        self.definitions.setEnabled(not busy)

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
