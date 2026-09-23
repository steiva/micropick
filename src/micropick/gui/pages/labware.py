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
exists; the stock list is long, so a filter box sits above it.

Everything the page shows comes from the run state the robot reports, re-read
after every change, rather than from a record the page keeps of what it asked
for. That is the same rule `Routine.check_labware` follows: what the robot
says is on the deck is the only thing the robot will act on, and a page that
remembered its own version would be the one place those could disagree.

Tips are here too, because a tip comes from a rack in a slot and that is what
this page knows. Once the selected slot holds a tip rack, the Tip card offers
a well to take from and the three ways to let go of a tip: in place, into the
fixed trash, or back into the well it came from. The trash is not in a run
created over HTTP on current robot software, so the session loads it into
slot 12 the first time it is needed (`Session.ensure_trash`).

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

Loading and unloading are HTTP and go to a `Worker` like every blocking call.
Neither moves the gantry; picking up a tip and dropping it in the trash or
the rack do.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDoubleSpinBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QVBoxLayout, QWidget)

from ...config.labware import (LabwareDefinition, LabwareError,
                               local_definitions, shared_definitions)
from ...config.schema import DeckModule
from ..session import Session
from ..theme import SPACING
from ..theme.factory import (card, combo_box, heading, primary_button,
                             scroll_column, secondary_button)
from ..widgets.deck_view import TRASH_SLOT, DeckView
from ..workers import Worker

__all__ = ["LabwarePage"]

TITLE = "Labware"

log = logging.getLogger(__name__)

PANEL_WIDTH = 420

# A definition rides on its list item under this role, so the list is the
# only place the catalogue is kept.
DEFINITION_ROLE = Qt.ItemDataRole.UserRole


class LabwarePage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._worker: Worker | None = None
        self._slot: str | None = None

        self.deck = DeckView(self)
        self.deck.slot_clicked.connect(self._slot_clicked)

        panel = QWidget(self)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        column.addWidget(self._modules_card())
        column.addWidget(self._slot_card())
        column.addWidget(self._tip_card())
        column.addWidget(self._definitions_card(), 1)

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

        self._reload_definitions()
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

        row = QHBoxLayout()
        self.module_slots = QLineEdit(self)
        self.module_slots.setPlaceholderText("slots, e.g. 5, 8, 9")
        self.module_slots.setMaximumWidth(120)
        self.module_height = QDoubleSpinBox(self)
        self.module_height.setRange(-200.0, 200.0)
        self.module_height.setDecimals(2)
        self.module_height.setSingleStep(0.1)
        self.module_height.setSuffix(" mm")
        self.module_height.setToolTip("how much the module raises the labware")
        self.module_name = QLineEdit(self)
        self.module_name.setPlaceholderText("name (optional)")
        row.addWidget(self.module_slots)
        row.addWidget(self.module_height)
        row.addWidget(self.module_name, 1)
        box.layout().addLayout(row)
        buttons = QHBoxLayout()
        self.add_module_button = secondary_button("Add module", self)
        self.add_module_button.clicked.connect(self._add_module)
        self.remove_module_button = secondary_button("Remove selected", self)
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
        self.refresh_button = secondary_button("Re-read", self)
        self.refresh_button.clicked.connect(self._reread)
        row.addWidget(self.load_button)
        row.addWidget(self.remove_button)
        row.addWidget(self.refresh_button)
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
        row.addWidget(self.tip_well)
        self.pick_button = primary_button("Pick up tip", self)
        self.pick_button.clicked.connect(self._pick_up)
        row.addWidget(self.pick_button)
        row.addStretch(1)
        box.layout().addLayout(row)

        # Two rows, not three in one: at this panel's width three buttons
        # get about 120 px each and "Drop in trash" needs 150, so the words
        # were cut in half. The return button carries the well's name when
        # there is one, which is longer still, so it has a row to itself.
        drops = QHBoxLayout()
        self.drop_place_button = secondary_button("Drop in place", self)
        self.drop_place_button.clicked.connect(self._drop_in_place)
        self.drop_trash_button = secondary_button("Drop in trash", self)
        self.drop_trash_button.clicked.connect(self._drop_in_trash)
        drops.addWidget(self.drop_place_button)
        drops.addWidget(self.drop_trash_button)
        box.layout().addLayout(drops)

        self.return_button = secondary_button("Return to rack", self)
        self.return_button.clicked.connect(self._return)
        box.layout().addWidget(self.return_button)
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
        # Tall enough to browse in: the column scrolls, so the list need not
        # shrink to make room for the cards above it.
        self.definitions.setMinimumHeight(260)
        self.definitions.currentItemChanged.connect(
            lambda _cur, _prev: self._refresh())
        self.definitions.itemDoubleClicked.connect(lambda _item: self._load())
        box.layout().addWidget(self.definitions, 1)

        self.catalogue_note = QLabel()
        self.catalogue_note.setWordWrap(True)
        box.layout().addWidget(self.catalogue_note)
        return box

    # -- the catalogue -------------------------------------------------------

    def _reload_definitions(self) -> None:
        """Custom first, then stock. Read once: neither list changes while
        the application runs, short of editing labware/ underneath it."""
        self.definitions.clear()
        notes = []
        try:
            custom = local_definitions()
        except LabwareError as exc:
            custom = {}
            notes.append(str(exc))
        try:
            stock = shared_definitions()
        except LabwareError as exc:
            stock = {}
            notes.append(str(exc))

        self._add_group("Custom — labware/", custom.values())
        # A stock name shadowed by a custom file is the custom one, as
        # resolve_definition would have it; listing both would offer a choice
        # the loader does not make.
        self._add_group("Stock — Opentrons",
                        (d for name, d in stock.items() if name not in custom))
        notes.append(f"{len(custom)} custom, "
                     f"{len(set(stock) - set(custom))} stock definitions")
        self.catalogue_note.setText("\n".join(notes))

    def _add_group(self, title: str, definitions) -> None:
        header = QListWidgetItem(title)
        header.setFlags(Qt.ItemFlag.NoItemFlags)
        font = header.font()
        font.setBold(True)
        header.setFont(font)
        self.definitions.addItem(header)
        for definition in sorted(definitions, key=lambda d: d.display_name.lower()):
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
        well = self.tip_well.currentText()
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

    def _return(self) -> None:
        tip = self.session.tip
        if self._busy() or self.session.robot is None or not tip.returnable:
            return
        self._say("")
        self._run(Worker(self.session.return_tip),
                  f"returning the tip to slot {tip.slot} {tip.well}")

    # -- deck modules --------------------------------------------------------

    def _add_module(self) -> None:
        profile = self.session.profile
        if profile is None:
            return
        text = self.module_slots.text().replace(";", ",").replace(" ", ",")
        try:
            slots = sorted({int(part) for part in text.split(",") if part})
            if not slots:
                raise ValueError("no slots")
            module = DeckModule(slots=slots,
                                offset=[0.0, 0.0, self.module_height.value()],
                                name=self.module_name.text().strip())
            self.session.set_deck_modules(list(profile.deck.modules) + [module])
        except Exception as exc:                     # noqa: BLE001
            self._say(f"not added: {exc}")
            return
        self.module_slots.clear()
        self.module_name.clear()

    def _remove_module(self) -> None:
        profile = self.session.profile
        row = self.modules_list.currentRow()
        if profile is None or row < 0:
            return
        modules = list(profile.deck.modules)
        del modules[row]
        self.session.set_deck_modules(modules)

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

    def _reread(self) -> None:
        if self._busy() or self.session.robot is None:
            return
        session = self.session

        def job():
            session.refresh_run_state()
            session.read_tip()

        self._run(Worker(job),
                  "re-reading the run's labware")

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
        self._refresh()

    def _job_failed(self, reason: str) -> None:
        if self.sender() is not self._worker:
            return
        self._worker = None
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
        """The rack's wells, in the definition's order; kept if unchanged so
        the chooser does not jump back to A1 on every refresh."""
        wells = list(definition.wells) if definition is not None else []
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
        self.deck.set_modules({str(slot): f"module +{m.height_mm:g} mm"
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
                "calibration module - add it here, or the robot will plan "
                "its well moves to the slot floor and hit it.")
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
        self.add_module_button.setEnabled(profile is not None and not busy)
        self.remove_module_button.setEnabled(
            profile is not None and not busy and bool(modules))

        definition = self.chosen_definition()
        entry = held.get(self._slot) if self._slot is not None else None

        if not connected:
            self.slot_heading.setText("Slot")
            self.slot_state.setText(
                "No run. Connect the robot on the Profile page; the deck "
                "shows what the run holds.")
        elif self._slot is None:
            self.slot_heading.setText("Slot")
            # The trash, once loaded for a drop, is run labware too; it is
            # not something the operator put on the deck.
            count = len([slot for slot in held if slot != TRASH_SLOT])
            self.slot_state.setText(
                f"Click a slot on the deck. The run holds "
                f"{count} labware{'s' if count != 1 else ''}.")
        else:
            self.slot_heading.setText(f"Slot {self._slot}")
            if entry is None:
                self.slot_state.setText("empty, as far as the run knows.")
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
                "move until Re-read answers it: a tip nobody knows about is "
                "how the robot crashes.")
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
        self.return_button.setEnabled(known and tip.returnable)
        self.return_button.setText(f"Return to slot {tip.slot} {tip.well}"
                                   if tip.returnable else "Return to rack")

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
        self.refresh_button.setEnabled(connected and not busy)
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
