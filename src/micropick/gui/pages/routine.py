"""Setting up a run: which plate, which wells, how many, and whose progress.

A page of its own rather than a corner of the picking page, because this is
what happens before a run and `Routine` is its own concept — the plan, the
progress and the ordering, with an identity that outlives a kernel restart
(DESIGN section 7).

Two things it refuses to smooth over.

**Resuming is a deliberate act.** `Routine.load` marks a file that carries
progress as needing confirmation, and the reason is that the one case software
cannot detect is the dangerous one: the same plate format, physically swapped
for a fresh plate, is indistinguishable from carrying on with the old one. So
`needs_confirmation` is shown as a block with the summary above it — the name,
the run, how much is already delivered, the next well — and the run cannot
start until the operator says yes to that particular text.

**`check_labware` is a check, not a guarantee, and it says so.** It verifies
the definition in the slot against the run state; it cannot verify that the
physical plate is the one this progress belongs to. All three of its outcomes
are surfaced as they come.

**The plate is one the robot already has.** It used to be any definition in
`labware/`, with a slot typed in beside it — so a routine could name a plate
that was not on the deck, in a slot that held a tip rack, and nothing found
out until the first well move. The list is now what the run reports:
everything loaded through the Labware page that has wells to deliver into,
named by slot. The deck beside it is the same `DeckView` that page draws,
because "which one is the destination" is a question about the deck and is
answered by pointing at it.

**Selecting wells and planning them are two acts.** A click used to toggle a
well into the plan with whatever the spin box held, which made "these twelve,
three each" impossible to say: every count was a fresh round of clicking.
Now the mouse selects — click, Ctrl to add, Shift to remove, a dragged box,
or a row letter or column number for the whole line — and the count is
applied to whatever is selected. The two are separate everywhere: the
selection is a white ring, the plan is the number inside the well.

The version note. `check_labware` prints a line to stdout when the slot reports
a different definition revision, and from a GUI that goes nowhere anyone can
see. Since `ot2_api.load_labware` hard-codes version 1 in the load command, that
fires for most stock definitions, so this page shows both versions itself rather
than relying on a print it cannot capture.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QInputDialog, QLabel,
                               QPlainTextEdit, QVBoxLayout, QWidget)

from ... import paths
from ...config.labware import LabwareError, resolve_definition
from ...core.routine import STRATEGIES, Destination, Routine, RoutineError
from ...hardware.labware import loaded_labware
from ..session import Session
from ..theme import SPACING
from ..theme.factory import (card, combo_box, heading, primary_button,
                             scroll_column, secondary_button, spin_box)
from ..widgets.deck_view import DeckView
from ..widgets.plate_view import PlateView
from ..workers import Worker

__all__ = ["RoutinePage"]

TITLE = "Routine"

log = logging.getLogger(__name__)

PANEL_WIDTH = 420

# Labware that holds no destination. A tip rack has wells and an ordering
# like a plate, and delivering a cuboid into one is a mistake nothing
# downstream would catch.
NOT_A_DESTINATION = ("tipRack", "trash", "adapter")

# How tall the deck picture is in the panel. Enough to read a slot number
# and tell a full slot from an empty one; the Labware page is where the
# deck is worked on.
MINI_DECK_HEIGHT = 190


def routines_dir() -> Path:
    directory = paths.outputs_dir() / "routines"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


class RoutinePage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.routine: Routine | None = None
        self.destination: Destination | None = None
        self._plan: dict[str, int] = {}
        self._worker: Worker | None = None

        self._selection: set[str] = set()

        self.plate = PlateView(self)
        self.plate.selection_changed.connect(self._selection_changed)
        self.plate.well_activated.connect(self._well_activated)

        panel = QWidget(self)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        column.addWidget(self._plate_card())
        column.addWidget(self._plan_card())
        column.addWidget(self._routine_card())
        column.addWidget(self._labware_card())
        column.addStretch(1)

        body = QHBoxLayout()
        body.setSpacing(SPACING)
        body.addWidget(self.plate, 1)
        body.addWidget(scroll_column(panel, PANEL_WIDTH))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2, SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addLayout(body, 1)

        session.robot_state_changed.connect(lambda _s: self._reload_plates())
        session.labware_changed.connect(lambda _s: self._reload_plates())
        self._reload_plates()
        self._refresh()

    # -- construction --------------------------------------------------------

    def _plate_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Plate", 2))

        # The deck as the Labware page draws it, small: picking the
        # destination is pointing at the slot it is in.
        self.deck = DeckView(self)
        self.deck.setFixedHeight(MINI_DECK_HEIGHT)
        self.deck.slot_clicked.connect(self._slot_clicked)
        box.layout().addWidget(self.deck)

        self.definition = combo_box(self)
        self.definition.currentIndexChanged.connect(self._chose_plate)
        box.layout().addWidget(self.definition)

        self.use_button = secondary_button("Use this plate", self)
        self.use_button.clicked.connect(self._use_plate)
        box.layout().addWidget(self.use_button)

        self.plate_state = QLabel("no plate chosen")
        self.plate_state.setWordWrap(True)
        box.layout().addWidget(self.plate_state)
        return box

    def _plan_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Plan", 2))

        note = QLabel(
            "Click a well to select it, Ctrl to add, Shift to remove; drag a "
            "box over several; click a row letter or a column number for the "
            "whole line. Then set how many objects each selected well should "
            "get — 0 takes them out of the plan again.")
        note.setWordWrap(True)
        box.layout().addWidget(note)

        selecting = QHBoxLayout()
        self.all_button = secondary_button("Select all", self)
        self.all_button.clicked.connect(self.plate.select_all)
        self.none_button = secondary_button("Select none", self)
        self.none_button.clicked.connect(self.plate.clear_selection)
        selecting.addWidget(self.all_button)
        selecting.addWidget(self.none_button)
        box.layout().addLayout(selecting)

        row = QHBoxLayout()
        row.addWidget(QLabel("Per well"))
        self.per_well = spin_box(self)
        self.per_well.setRange(0, 999)
        self.per_well.setValue(1)
        self.per_well.setMinimumWidth(70)
        # Typing a number applies it: with wells selected, the number is the
        # whole of what is being said, and a separate Apply after it is a
        # second click for nothing. The button is there for the operator who
        # sets the number first and selects afterwards.
        self.per_well.valueChanged.connect(self._per_well_changed)
        row.addWidget(self.per_well)
        self.apply_button = primary_button("Set in selected", self)
        self.apply_button.clicked.connect(self._apply_count)
        row.addWidget(self.apply_button, 1)
        box.layout().addLayout(row)

        buttons = QHBoxLayout()
        self.clear_button = secondary_button("Clear the plan", self)
        self.clear_button.clicked.connect(self._clear_plan)
        row_order = QLabel("Order")
        self.strategy = combo_box(self)
        self.strategy.addItems(STRATEGIES)
        self.strategy.setCurrentText("by_column")
        buttons.addWidget(self.clear_button)
        buttons.addWidget(row_order)
        buttons.addWidget(self.strategy, 1)
        box.layout().addLayout(buttons)

        self.plan_state = QLabel("select wells on the plate")
        self.plan_state.setWordWrap(True)
        box.layout().addWidget(self.plan_state)
        return box

    def _routine_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Routine", 2))
        row = QHBoxLayout()
        self.create_button = primary_button("Create…", self)
        self.create_button.clicked.connect(self._create)
        self.open_button = secondary_button("Open…", self)
        self.open_button.clicked.connect(self._open)
        row.addWidget(self.create_button)
        row.addWidget(self.open_button)
        row.addStretch(1)
        box.layout().addLayout(row)

        self.summary = QPlainTextEdit(self)
        self.summary.setReadOnly(True)
        self.summary.setMaximumHeight(110)
        self.summary.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        box.layout().addWidget(self.summary)

        # Not a checkbox and not tucked into a dialog's default button: the
        # operator has to have read the summary above before this means
        # anything, and its own text says what is being confirmed.
        self.confirm_note = QLabel()
        self.confirm_note.setWordWrap(True)
        box.layout().addWidget(self.confirm_note)
        self.confirm_button = primary_button("Confirm resume", self)
        self.confirm_button.clicked.connect(self._confirm)
        box.layout().addWidget(self.confirm_button)
        return box

    def _labware_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Labware in the slot", 2))
        self.check_button = secondary_button("Check against the robot", self)
        self.check_button.clicked.connect(self._check)
        box.layout().addWidget(self.check_button)
        self.labware_state = QLabel(
            "verifies the definition in the slot, not that this is the same "
            "physical plate — no software check can tell those apart.")
        self.labware_state.setWordWrap(True)
        self.labware_state.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.layout().addWidget(self.labware_state)
        return box

    # -- plate ---------------------------------------------------------------

    def _loaded_plates(self) -> list[tuple[str, str, str]]:
        """(slot, load_name, display name) for every labware in the run that
        something could be delivered into, nearest slot first."""
        state = self.session.run_state
        if self.session.robot is None or state is None:
            return []
        out = []
        for slot, entry in sorted(state.labware.items(), key=lambda kv: int(kv[0])
                                  if kv[0].isdigit() else 99):
            try:
                definition = resolve_definition(entry.load_name)
            except LabwareError:
                continue
            if definition.category in NOT_A_DESTINATION or not definition.ordering:
                continue
            out.append((slot, entry.load_name, definition.display_name))
        return out

    def _reload_plates(self) -> None:
        """The run's labware, in the combo and on the deck."""
        plates = self._loaded_plates()
        state = self.session.run_state
        held = (state.labware if state is not None
                and self.session.robot is not None else {})
        self.deck.set_labware({slot: entry.load_name
                               for slot, entry in held.items()})
        # The profile's modules, with the meaning they have on the Labware
        # page: a raised floor. Not "this slot is a candidate" - one mark,
        # one meaning, or the deck picture starts needing a legend.
        profile = self.session.profile
        modules = list(profile.deck.modules) if profile is not None else []
        self.deck.set_modules({str(slot): f"module +{m.height_mm:g} mm"
                               for m in modules for slot in m.slots})

        chosen = self.definition.currentData()
        self.definition.blockSignals(True)
        self.definition.clear()
        for slot, load_name, display in plates:
            self.definition.addItem(f"slot {slot} — {display}", (slot, load_name))
        index = next((i for i in range(self.definition.count())
                      if self.definition.itemData(i) == chosen), -1)
        if index >= 0:
            self.definition.setCurrentIndex(index)
        self.definition.blockSignals(False)
        if self.destination is not None:
            self.deck.select(str(self.destination.slot))
        self._refresh()

    def _slot_clicked(self, slot: str) -> None:
        """Choosing on the deck picks the same plate the combo would."""
        for index in range(self.definition.count()):
            if self.definition.itemData(index)[0] == slot:
                self.definition.setCurrentIndex(index)
                # Directly, not through the signal: setting the index it
                # already has emits nothing, and clicking the slot of the
                # plate already chosen must still mark it on the deck.
                self._chose_plate(index)
                return
        self.plate_state.setText(
            f"slot {slot} holds nothing a routine can deliver into. Load a "
            f"plate there on the Labware page.")

    def _chose_plate(self, _index: int) -> None:
        data = self.definition.currentData()
        if data is not None:
            self.deck.select(data[0])
        self._refresh()

    def _use_plate(self) -> None:
        data = self.definition.currentData()
        if data is None:
            return
        slot, name = data
        try:
            destination = Destination.from_labware(name, int(slot))
        except (RoutineError, LabwareError) as exc:
            self.plate_state.setText(str(exc))
            log.error("%s", exc)
            return
        self.destination = destination
        self.routine = None
        self.session.set_routine(None)
        self._plan = {}
        self._selection = set()
        self.plate.set_destination(destination)
        self.plate.set_plan({})
        self.plate.set_progress({})
        self.deck.select(str(destination.slot))
        self.plate_state.setText(
            f"{destination.load_name} v{destination.version} "
            f"({destination.namespace}), {len(destination)} wells, slot "
            f"{destination.slot}")
        self.summary.clear()
        log.info("destination: %r", destination)
        self._refresh()

    # -- plan ----------------------------------------------------------------

    def _selection_changed(self, names) -> None:
        """The selection changed; the number in the box did not.

        It was made to follow the selection - select a well, read its count
        - and that turned out to be the wrong way round. The count is
        already written inside the well, so the box has nothing to report;
        what it is for is the value being applied, and a box that reset
        itself to the well just clicked made "three in each of these"
        impossible to say twice in a row.
        """
        self._selection = set(names)
        self._refresh()

    def _well_activated(self, name: str) -> None:
        """Double click: this well, the number in the box, now."""
        self.plate.set_selection({name})
        self._apply_count()

    def _per_well_changed(self, _value: int) -> None:
        if self._selection:
            self._apply_count()

    def _apply_count(self) -> None:
        if self.routine is not None:
            # A routine's plan is fixed once it exists: editing it underneath
            # recorded progress would make the counts describe two plans.
            self.plan_state.setText(
                "this routine's plan is fixed. Create a new one to change it.")
            return
        if not self._selection:
            self.plan_state.setText("nothing is selected. Click a well, or a "
                                    "row letter, or drag a box.")
            return
        count = self.per_well.value()
        for name in self._selection:
            if count > 0:
                self._plan[name] = count
            else:
                self._plan.pop(name, None)
        self.plate.set_plan(self._plan)
        self._refresh()

    def _clear_plan(self) -> None:
        if self.routine is not None:
            return
        self._plan = {}
        self.plate.set_plan(self._plan)
        self._refresh()

    # -- routine -------------------------------------------------------------

    def _create(self) -> None:
        if self.destination is None or not self._plan:
            return
        # A plate routine needs the operator's own label, and Routine refuses
        # without one: it is what tells a resumed run from a fresh plate of the
        # same kind.
        name, ok = QInputDialog.getText(
            self, "Name this routine",
            "Your label for this physical plate:")
        name = name.strip()
        if not ok or not name:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save the routine", str(routines_dir() / f"{name}.json"),
            "Routine files (*.json)")
        if not path:
            return
        try:
            routine = Routine(self.destination, dict(self._plan), name=name,
                              strategy=self.strategy.currentText(), path=path)
            routine.save()
        except RoutineError as exc:
            self.summary.setPlainText(str(exc))
            log.error("%s", exc)
            return
        self._adopt(routine)

    def _open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open a routine", str(routines_dir()),
            "Routine files (*.json)")
        if not path:
            return
        try:
            routine = Routine.load(path)
        except (RoutineError, LabwareError) as exc:
            self.summary.setPlainText(str(exc))
            log.error("%s", exc)
            return
        self.destination = routine.destination
        self._plan = dict(routine.plan)
        self.plate.set_destination(routine.destination)
        self._adopt(routine)

    def _adopt(self, routine: Routine) -> None:
        self.routine = routine
        # The Picking page needs it and the two pages never meet; the
        # session is where "what this session is attached to" lives.
        self.session.set_routine(routine)
        self.plate.set_plan(dict(routine.plan))
        self.plate.set_progress(self._delivered())
        self.summary.setPlainText(routine.summary())
        self.plate_state.setText(
            f"{routine.destination.load_name} v{routine.destination.version}, "
            f"slot {routine.destination.slot}  ·  {routine.path}")
        log.info("routine %r: %s", routine.name,
                 routine.summary().replace("\n", " | "))
        self._refresh()

    def _delivered(self) -> dict[str, int]:
        if self.routine is None:
            return {}
        table = self.routine.progress_table()
        ordering = self.routine.destination.ordering
        # progress_table is shaped like the plate, so the cell at (row, column)
        # is the well the ordering puts there. No name is taken apart.
        out: dict[str, int] = {}
        for column, wells in enumerate(ordering):
            for row, well in enumerate(wells):
                if well in self.routine.plan:
                    out[well] = int(table.iat[row, column])
        return out

    def _confirm(self) -> None:
        if self.routine is None or not self.routine.needs_confirmation:
            return
        self.routine.confirm_resume()
        log.info("resume confirmed for routine %r (run %s)",
                 self.routine.name, self.routine.run_id)
        self._refresh()

    # -- labware -------------------------------------------------------------

    def _check(self) -> None:
        if self.routine is None or self.session.robot is None:
            return
        if self._worker is not None and self._worker.running:
            return
        robot, routine = self.session.robot, self.routine

        def job():
            """Read the run state and judge it. Both halves are HTTP-blocking
            on a real robot, so both are here rather than split across
            threads."""
            loaded = loaded_labware(robot)
            slot = str(routine.destination.slot)
            entry = loaded.get(slot)
            routine.check_labware(loaded)        # raises on empty or foreign
            return entry

        self.labware_state.setText("reading the run state…")
        worker = Worker(job)
        self._worker = worker
        worker.finished.connect(self._checked)
        worker.failed.connect(self._check_failed)
        worker.message.connect(self._said)
        worker.start()
        self._refresh()

    def _checked(self, entry) -> None:
        self._worker = None
        want = self.routine.destination
        # The version note check_labware would have printed, shown instead.
        reported = getattr(entry, "version", None)
        note = ""
        if reported is not None and want.version is not None \
                and int(reported) != int(want.version):
            note = (f"\nThe slot reports v{reported} and the routine recorded "
                    f"v{want.version}. Ignored: ot2_api.load_labware sends "
                    f"version 1 whatever the definition says, and a version is "
                    f"a revision of the description rather than the identity "
                    f"of the plate.")
        self.labware_state.setText(
            f"slot {want.slot} holds {getattr(entry, 'load_name', '?')} — "
            f"matches this routine.{note}\nThis verifies the definition, not "
            f"that it is the same physical plate.")
        log.info("labware check passed for slot %s", want.slot)
        self._refresh()

    def _check_failed(self, reason: str) -> None:
        self._worker = None
        self.labware_state.setText(reason)
        log.error("labware check: %s", reason)
        self._refresh()

    def _said(self, text: str) -> None:
        log.info("%s", text)

    # -- display -------------------------------------------------------------

    def _refresh(self) -> None:
        busy = self._worker is not None and self._worker.running
        has_plate = self.destination is not None
        editable = has_plate and self.routine is None

        self.use_button.setEnabled(not busy and self.definition.count() > 0)
        self.all_button.setEnabled(has_plate)
        self.none_button.setEnabled(has_plate and bool(self._selection))
        self.apply_button.setEnabled(editable and bool(self._selection))
        self.clear_button.setEnabled(editable and bool(self._plan))
        self.per_well.setEnabled(editable)
        self.strategy.setEnabled(editable)
        self.create_button.setEnabled(editable and bool(self._plan))
        self.open_button.setEnabled(not busy)

        if self.definition.count() == 0:
            self.plate_state.setText(
                "The run holds nothing to deliver into. Load a plate on the "
                "Labware page; tip racks and the trash are not offered here."
                if self.session.robot is not None else
                "No run. Connect the robot on the Profile page; the plate is "
                "chosen from what the run actually holds.")

        wells = len(self._plan)
        objects = sum(self._plan.values())
        chosen = len(self._selection)
        parts = []
        if chosen:
            parts.append(f"{chosen} selected")
        if chosen:
            # What is in the selection now, so the number to type is an
            # informed one without the box having to show it.
            counts = sorted({self._plan.get(n, 0) for n in self._selection})
            parts[-1] += (f", holding {counts[0]}" if len(counts) == 1 else
                          f", holding {counts[0]} to {counts[-1]}")
        parts.append(f"{wells} wells planned, {objects} objects"
                     if wells else "nothing planned yet")
        self.plan_state.setText("  ·  ".join(parts))

        needs = self.routine is not None and self.routine.needs_confirmation
        self.confirm_button.setVisible(needs)
        self.confirm_note.setVisible(needs)
        if needs:
            self.confirm_note.setText(
                "This routine was restored with progress already on it. If the "
                "plate in the slot is a fresh one of the same kind, do not "
                "confirm — build a new routine, or its first well will be the "
                "middle of this one. Nothing can tell those two apart but you.")
        self.check_button.setEnabled(
            self.routine is not None and self.session.robot is not None
            and not busy)
