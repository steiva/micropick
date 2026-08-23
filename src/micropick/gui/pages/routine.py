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
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout,
                               QInputDialog, QLabel, QPlainTextEdit, QSpinBox,
                               QVBoxLayout, QWidget)

from ... import paths
from ...config.labware import LabwareError, local_definitions
from ...core.routine import STRATEGIES, Destination, Routine, RoutineError
from ...hardware.labware import loaded_labware
from ..session import Session
from ..theme import SPACING
from ..theme.factory import card, heading, primary_button, secondary_button
from ..widgets.plate_view import PlateView
from ..workers import Worker

__all__ = ["RoutinePage"]

TITLE = "Routine"

log = logging.getLogger(__name__)

PANEL_WIDTH = 420


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

        self.plate = PlateView(self)
        self.plate.well_clicked.connect(self._well_clicked)

        panel = QWidget(self)
        panel.setFixedWidth(PANEL_WIDTH)
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
        body.addWidget(panel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2, SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addWidget(heading(TITLE, 1))
        layout.addLayout(body, 1)

        session.robot_state_changed.connect(lambda _s: self._refresh())
        self._reload_definitions()
        self._refresh()

    # -- construction --------------------------------------------------------

    def _plate_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Plate", 2))
        self.definition = QComboBox(self)
        box.layout().addWidget(self.definition)

        row = QHBoxLayout()
        row.addWidget(QLabel("Slot"))
        self.slot = QSpinBox(self)
        self.slot.setRange(1, 11)          # 12 is the fixed trash
        self.slot.setValue(5)
        row.addWidget(self.slot)
        self.use_button = secondary_button("Use this plate", self)
        self.use_button.clicked.connect(self._use_plate)
        row.addWidget(self.use_button)
        row.addStretch(1)
        box.layout().addLayout(row)

        self.plate_state = QLabel("no plate chosen")
        self.plate_state.setWordWrap(True)
        box.layout().addWidget(self.plate_state)
        return box

    def _plan_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Plan", 2))
        row = QHBoxLayout()
        row.addWidget(QLabel("Per well"))
        self.per_well = QSpinBox(self)
        self.per_well.setRange(1, 999)
        self.per_well.setValue(1)
        row.addWidget(self.per_well)
        row.addWidget(QLabel("Order"))
        self.strategy = QComboBox(self)
        self.strategy.addItems(STRATEGIES)
        self.strategy.setCurrentText("by_column")
        row.addWidget(self.strategy, 1)
        box.layout().addLayout(row)

        buttons = QHBoxLayout()
        self.clear_button = secondary_button("Clear", self)
        self.clear_button.clicked.connect(self._clear_plan)
        self.all_button = secondary_button("Select all", self)
        self.all_button.clicked.connect(self._select_all)
        buttons.addWidget(self.all_button)
        buttons.addWidget(self.clear_button)
        buttons.addStretch(1)
        box.layout().addLayout(buttons)

        self.plan_state = QLabel("click wells to plan them")
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

    def _reload_definitions(self) -> None:
        self.definition.clear()
        try:
            known = local_definitions()
        except LabwareError as exc:
            self.plate_state.setText(str(exc))
            return
        self.definition.addItems(sorted(known))

    def _use_plate(self) -> None:
        name = self.definition.currentText()
        if not name:
            return
        try:
            destination = Destination.from_labware(name, self.slot.value())
        except (RoutineError, LabwareError) as exc:
            self.plate_state.setText(str(exc))
            log.error("%s", exc)
            return
        self.destination = destination
        self.routine = None
        self._plan = {}
        self.plate.set_destination(destination)
        self.plate.set_plan({})
        self.plate.set_progress({})
        self.plate_state.setText(
            f"{destination.load_name} v{destination.version} "
            f"({destination.namespace}), {len(destination)} wells, slot "
            f"{destination.slot}")
        self.summary.clear()
        log.info("destination: %r", destination)
        self._refresh()

    # -- plan ----------------------------------------------------------------

    def _well_clicked(self, name: str) -> None:
        if self.routine is not None:
            # A routine's plan is fixed once it exists: editing it underneath
            # recorded progress would make the counts describe two plans.
            self.plan_state.setText(
                "this routine's plan is fixed. Create a new one to change it.")
            return
        if name in self._plan:
            del self._plan[name]
        else:
            self._plan[name] = self.per_well.value()
        self.plate.set_plan(self._plan)
        self._refresh()

    def _select_all(self) -> None:
        if self.destination is None or self.routine is not None:
            return
        self._plan = {w: self.per_well.value() for w in self.destination.targets}
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
        self.all_button.setEnabled(editable)
        self.clear_button.setEnabled(editable and bool(self._plan))
        self.per_well.setEnabled(editable)
        self.strategy.setEnabled(editable)
        self.create_button.setEnabled(editable and bool(self._plan))
        self.open_button.setEnabled(not busy)

        wells = len(self._plan)
        objects = sum(self._plan.values())
        self.plan_state.setText(
            f"{wells} wells planned, {objects} objects"
            if wells else "click wells to plan them")

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
