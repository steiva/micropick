"""Editing one step of a liquid handling program.

`StepEditor` shows the fields of whichever step it is given - a volume and a
flow rate for an aspirate, a count for a mix, seconds for a wait - and under
them a `LocationEditor` for where the step happens. Every field is built
once and shown or hidden by the step's kind, so switching between steps is
a matter of filling boxes, not of building a form each time.

Neither widget owns a program. They are given a step, and they say
`changed(step)` with a new one whenever a box is edited; the page puts it
into the program. What the boxes offer - the labware on the deck, the
profile's saved points - is handed in with `set_choices`, and a location
that names labware or a point not on offer now is still shown, marked as
missing, rather than quietly changed to the first thing in the list.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QVBoxLayout, QWidget)

from ...core.liquid import KINDS, LEVELS, Location
from ..theme.factory import combo_box, double_spin_box, spin_box

__all__ = ["LocationEditor", "StepEditor", "PlateChoice"]

VOLUME_RANGE = (0.1, 1000.0)
FLOW_RANGE = (0.1, 1000.0)
OFFSET_RANGE = (-50.0, 50.0)
SECONDS_RANGE = (0.0, 3600.0)
CYCLES_RANGE = (1, 50)

# (slot, load name, text for the combo, wells row by row), one per labware
# a location can name.
PlateChoice = tuple


def _key(slot, load_name) -> str:
    """A labware's item data: a string, since a tuple does not survive the
    round trip through QVariant well enough for `findData` to match it."""
    return f"{slot}|{load_name}"


def _number(parent, span, suffix: str, decimals: int = 1, step: float = 1.0):
    box = double_spin_box(parent)
    box.setRange(*span)
    box.setDecimals(decimals)
    box.setSingleStep(step)
    box.setSuffix(suffix)
    box.setMinimumWidth(100)
    return box


class LocationEditor(QWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._plates: list[PlateChoice] = []
        self._points: list[str] = []
        self._loading = False

        self.kind = combo_box(self)
        for kind, text in KINDS.items():
            self.kind.addItem(text, kind)
        self.kind.setToolTip(
            "each well of the group: the step is done in every well of the "
            "group, one after another.\n"
            "one fixed well: one well of any labware on the deck, the same "
            "for every well of the group - a reservoir, a waste.\n"
            "a saved point: a position saved in the jog panel.\n"
            "same place as the step before: no move; wherever the step "
            "before left the tip.")
        self.labware = combo_box(self)
        self.well = combo_box(self)
        self.well.setEditable(True)
        self.well.setInsertPolicy(self.well.InsertPolicy.NoInsert)
        self.point = combo_box(self)
        self.point.setToolTip("A position saved in the jog panel's Positions. "
                              "The tip goes there, plus the offset.")
        self.level = combo_box(self)
        self.level.addItems(LEVELS)
        self.level.setToolTip("Where in the well the offset is measured "
                              "from: its rim, its middle or its bottom.")
        # The axis inside the box and the unit on the row's label: three
        # boxes with a letter beside each and "mm" in each do not fit a
        # column of the panel.
        self.offsets = [_number(self, OFFSET_RANGE, "", 2, 0.1)
                        for _ in range(3)]
        for axis, box in zip("XYZ", self.offsets):
            box.setPrefix(f"{axis} ")
            box.setMinimumWidth(0)
            box.setToolTip(f"{axis} from the level (or the point), in mm. "
                           + ("+ is up." if axis == "Z" else ""))
        # Across the whole form, under a label of its own: beside a label
        # three boxes are too narrow to show their numbers.
        self.offset_label = QLabel("Offset from there, mm", self)
        offset_row = QWidget(self)
        row = QHBoxLayout(offset_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        for box in self.offsets:
            row.addWidget(box, 1)
        self.offset_row = offset_row

        self.form = QFormLayout(self)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.form.addRow("Where", self.kind)
        self.form.addRow("Labware", self.labware)
        self.form.addRow("Well", self.well)
        self.form.addRow("Point", self.point)
        self.form.addRow("Level", self.level)
        self.form.addRow(self.offset_label)
        self.form.addRow(self.offset_row)

        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.labware.currentIndexChanged.connect(self._labware_changed)
        for widget in (self.well, self.point, self.level):
            widget.currentIndexChanged.connect(self._edited)
        self.well.lineEdit().editingFinished.connect(self._edited)
        for box in self.offsets:
            box.valueChanged.connect(self._edited)
        self._show_rows()

    # -- what is on offer ----------------------------------------------------

    def set_choices(self, plates: list[PlateChoice], points: list[str]) -> None:
        location = self.location()
        self._plates = list(plates)
        self._points = sorted(points)
        self.set_location(location)

    # -- in and out ----------------------------------------------------------

    def set_location(self, location: Location) -> None:
        self._loading = True
        self.kind.setCurrentIndex(max(0, self.kind.findData(location.kind)))
        self.labware.clear()
        for slot, load_name, text, _wells in self._plates:
            self.labware.addItem(text, _key(slot, load_name))
        wanted = _key(location.slot, location.load_name)
        if location.slot and self.labware.findData(wanted) < 0:
            self.labware.addItem(f"slot {location.slot} — "
                                 f"{location.load_name} (not on the deck)",
                                 wanted)
        if location.slot:
            self.labware.setCurrentIndex(self.labware.findData(wanted))
        self._fill_wells(location.well)
        self.point.clear()
        self.point.addItems(self._points)
        if location.point and location.point not in self._points:
            self.point.addItem(f"{location.point} (missing)", location.point)
        index = self.point.findText(location.point or "")
        if index < 0 and location.point:
            index = self.point.findData(location.point)
        if index >= 0:
            self.point.setCurrentIndex(index)
        self.level.setCurrentText(location.level)
        for box, value in zip(self.offsets, location.offset):
            box.setValue(float(value))
        self._loading = False
        self._show_rows()

    def location(self) -> Location:
        kind = self.kind.currentData() or "this_well"
        slot = load_name = well = point = None
        if kind == "well":
            data = self.labware.currentData()
            if data:
                slot, load_name = data.split("|", 1)
            well = self.well.currentText().strip().upper() or None
        elif kind == "point":
            point = self.point.currentData() or self.point.currentText() or None
        return Location(kind=kind, slot=slot, load_name=load_name, well=well,
                        point=point, level=self.level.currentText() or "top",
                        offset=[round(b.value(), 3) for b in self.offsets])

    # -- reacting ----------------------------------------------------------

    def _fill_wells(self, wanted: str | None) -> None:
        data = self.labware.currentData()
        wells = next((w for s, n, _t, w in self._plates
                      if data == _key(s, n)), [])
        self.well.clear()
        self.well.addItems(wells)
        if wanted:
            if wanted not in wells:
                self.well.addItem(wanted)
            self.well.setCurrentText(wanted)

    def _kind_changed(self, _index: int) -> None:
        self._show_rows()
        self._edited()

    def _labware_changed(self, _index: int) -> None:
        if self._loading:
            return
        self._loading = True
        self._fill_wells(self.well.currentText())
        self._loading = False
        self._edited()

    def _edited(self, *_args) -> None:
        if not self._loading:
            self.changed.emit()

    def _show_rows(self) -> None:
        kind = self.kind.currentData()
        self.form.setRowVisible(self.labware, kind == "well")
        self.form.setRowVisible(self.well, kind == "well")
        self.form.setRowVisible(self.point, kind == "point")
        self.form.setRowVisible(self.level, kind in ("this_well", "well"))
        self.form.setRowVisible(self.offset_label, kind != "here")
        self.form.setRowVisible(self.offset_row, kind != "here")


class StepEditor(QWidget):
    """The fields of one step. See the module docstring."""

    changed = Signal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._step = None
        self._loading = False

        self.volume = _number(self, VOLUME_RANGE, " µl")
        self.all_in_tip = QCheckBox("everything in the tip", self)
        self.all_in_tip.setToolTip("Dispense whatever the tip holds at that "
                                   "moment.")
        self.refill = QCheckBox("Auto refill", self)
        self.refill.setToolTip(
            "A refill: skipped while the tip holds enough for the dispenses "
            "after it; otherwise the tip is topped up to this volume. For "
            "filling from a reservoir. Off: the volume is aspirated every "
            "time, as for taking liquid out of a well.")
        # The box and its switch one above the other: side by side they
        # are squeezed in a column of the panel.
        volume_row = QWidget(self)
        rows = QVBoxLayout(volume_row)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(4)
        rows.addWidget(self.volume)
        rows.addWidget(self.all_in_tip)
        rows.addWidget(self.refill)
        self.volume_row = volume_row
        self.flow = _number(self, FLOW_RANGE, " µl/s")
        self.flow.setToolTip("How fast the plunger moves. Slow near cuboids.")
        self.cycles = spin_box(self)
        self.cycles.setRange(*CYCLES_RANGE)
        self.cycles.setToolTip("How many times to draw and push back the "
                               "volume.")
        self.seconds = _number(self, SECONDS_RANGE, " s")
        self.message = QLineEdit(self)
        self.message.setToolTip("Shown when the run pauses here.")
        self.where = LocationEditor(self)

        self.form = QFormLayout()
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.form.addRow("Volume", self.volume_row)
        self.form.addRow("Flow rate", self.flow)
        self.form.addRow("Cycles", self.cycles)
        self.form.addRow("Seconds", self.seconds)
        self.form.addRow("Message", self.message)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.addLayout(self.form)
        column.addWidget(self.where)

        for box in (self.volume, self.flow, self.seconds):
            box.valueChanged.connect(self._edited)
        self.cycles.valueChanged.connect(self._edited)
        self.all_in_tip.toggled.connect(self._all_toggled)
        self.refill.toggled.connect(self._edited)
        self.message.editingFinished.connect(self._edited)
        self.where.changed.connect(self._edited)
        self.set_step(None)

    def set_choices(self, plates: list[PlateChoice], points: list[str]) -> None:
        self.where.set_choices(plates, points)

    def set_step(self, step) -> None:
        self._step = step
        self._loading = True
        action = step.action if step is not None else None
        has = lambda name: step is not None and hasattr(step, name)  # noqa: E731
        if has("volume_ul"):
            self.all_in_tip.setVisible(action == "dispense")
            self.all_in_tip.setChecked(step.volume_ul is None)
            self.volume.setValue(step.volume_ul if step.volume_ul is not None
                                 else self.volume.value())
            self.volume.setEnabled(step.volume_ul is not None)
        self.refill.setVisible(action == "aspirate")
        if action == "aspirate":
            self.refill.setChecked(step.refill)
        if has("flow_rate"):
            self.flow.setValue(step.flow_rate)
        if has("cycles"):
            self.cycles.setValue(step.cycles)
        if has("seconds"):
            self.seconds.setValue(step.seconds)
        if has("message"):
            self.message.setText(step.message)
        if has("location"):
            self.where.set_location(step.location)
        self.form.setRowVisible(self.volume_row, has("volume_ul"))
        self.form.setRowVisible(self.flow, has("flow_rate"))
        self.form.setRowVisible(self.cycles, has("cycles"))
        self.form.setRowVisible(self.seconds, has("seconds"))
        self.form.setRowVisible(self.message, has("message"))
        self.where.setVisible(has("location"))
        self._loading = False

    def step(self):
        """The step as the boxes now say it."""
        step = self._step
        if step is None:
            return None
        update = {}
        if hasattr(step, "volume_ul"):
            update["volume_ul"] = (None if step.action == "dispense"
                                   and self.all_in_tip.isChecked()
                                   else round(self.volume.value(), 3))
        if hasattr(step, "refill"):
            update["refill"] = self.refill.isChecked()
        if hasattr(step, "flow_rate"):
            update["flow_rate"] = round(self.flow.value(), 3)
        if hasattr(step, "cycles"):
            update["cycles"] = int(self.cycles.value())
        if hasattr(step, "seconds"):
            update["seconds"] = round(self.seconds.value(), 3)
        if hasattr(step, "message"):
            update["message"] = self.message.text()
        if hasattr(step, "location"):
            update["location"] = self.where.location()
        # Validated, not model_copy(update=...), which would skip it.
        return type(step).model_validate({**step.model_dump(), **{
            k: (v.model_dump() if hasattr(v, "model_dump") else v)
            for k, v in update.items()}})

    def _all_toggled(self, on: bool) -> None:
        self.volume.setEnabled(not on)
        self._edited()

    def _edited(self, *_args) -> None:
        if self._loading or self._step is None:
            return
        self._step = self.step()
        self.changed.emit(self._step)
