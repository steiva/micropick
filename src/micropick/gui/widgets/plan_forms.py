"""What an open plan step asks that is not a place (LIQUID.md §3.4 - §3.6).

Amount and Extras of a Move liquid, How of a Mix, a Blow out or a Move the tip,
the time of a Wait and the message of a Wait for me have the left area to
themselves (`AmountForm`, `ExtrasForm`, `HowForm`, `WaitForm`, `MessageForm`).
There is nothing to confirm: a change goes out as it is typed, as
`edited(key, values)`, the step's fields and their new values, and the page
makes it part of the plan. `key` names the field, so a number typed digit by
digit is one undo (`LiquidPlanPage._commit`). Filling the fields from the step
says nothing.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QVBoxLayout,
                               QWidget)

from ...core.liquid_plan import Height, duration, height_words
from ..theme import COLORS, SPACING
from ..theme.factory import (caption, card, double_spin_box, heading,
                             muted_label, spin_box)
from .banner import Banner
from .controls import Segmented, Switch
from .well_heights import DEPTH_MM, WellDrawing

__all__ = ["AmountForm", "ExtrasForm", "HeightCard", "HowForm", "MessageForm",
           "WAITING_WHY", "WaitForm", "uses_height"]

REFS = (("bottom", "Bottom"), ("middle", "Middle"), ("top", "Top"))
MAX_UL = 100000.0
MAX_FLOW = 1000.0
MAX_OFFSET_MM = 100.0
MAX_ASIDE_MM = 50.0
MAX_MINUTES = 9999
MAX_WAIT_S = 3600.0
MAX_CYCLES = 99
DRAWING_PX = (140, 150)
DRAWING_WELL = 0.34
SHORT_FIELD_PX = 110
WAIT_HELP = "The robot stays still this long."
WAIT_STEP_HELP = ("The robot does nothing for this long. Pause and Stop still "
                  "work.")
WAITING_WHY = "You asked for this in the plan."
NO_HEIGHT = "A point and the trash are used at their own height."


def uses_height(step) -> bool:
    """Whether How asks a height: only in a well."""
    if step.kind == "mix":
        return True
    return step.kind in ("blow_out", "move_tip") and step.place.kind == "wells"


def _number(parent: QWidget, low: float, high: float, suffix: str,
            decimals: int = 1):
    box = double_spin_box(parent)
    box.setRange(low, high)
    box.setDecimals(decimals)
    box.setSuffix(suffix)
    return box


def _put(box, value) -> None:
    """A field's value from the step, not as an edit, and not over what is
    being typed there."""
    if box.value() != value:
        box.blockSignals(True)
        box.setValue(value)
        box.blockSignals(False)


def _labeled(parent: QWidget, label: str, field: QWidget) -> QWidget:
    """`field` under its `label`."""
    box = QWidget(parent)
    box.setObjectName("plain")
    column = QVBoxLayout(box)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(4)
    column.addWidget(muted_label(label, box))
    column.addWidget(field)
    return box


class HeightCard(QWidget):
    """A height with the well drawn beside its fields, the sentence and where
    the bottom comes from under them."""

    changed = Signal(object)                # the Height

    def __init__(self, title: str, color: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("plain")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box = card(self)
        outer.addWidget(box)
        column = box.layout()
        self.title = caption(title, box)
        self.title.setStyleSheet(f"color: {COLORS[color]};")
        column.addWidget(self.title)
        self.ref = Segmented([text for _ref, text in REFS], parent=box)
        column.addWidget(_labeled(box, "Measured from", self.ref))
        row = QHBoxLayout()
        row.setSpacing(SPACING * 2)
        self.drawing = WellDrawing(box, DRAWING_WELL)
        self.drawing.setFixedSize(*DRAWING_PX)
        self.drawing.color = QColor(COLORS[color])
        row.addWidget(self.drawing)
        fields = QVBoxLayout()
        fields.setSpacing(SPACING)
        self.offset = _number(box, -MAX_OFFSET_MM, MAX_OFFSET_MM, " mm", 2)
        fields.addWidget(_labeled(box, "Offset", self.offset))
        self.aside_x = _number(box, -MAX_ASIDE_MM, MAX_ASIDE_MM, " mm", 2)
        self.aside_y = _number(box, -MAX_ASIDE_MM, MAX_ASIDE_MM, " mm", 2)
        aside = QHBoxLayout()
        aside.addWidget(_labeled(box, "Aside X", self.aside_x))
        aside.addWidget(_labeled(box, "Aside Y", self.aside_y))
        fields.addLayout(aside)
        fields.addStretch(1)
        row.addLayout(fields, 1)
        column.addLayout(row)
        self.sentence = QLabel(box)
        self.sentence.setObjectName("heading3")
        self.sentence.setWordWrap(True)
        column.addWidget(self.sentence)
        self.note = muted_label("", box)
        column.addWidget(self.note)
        self.ref.changed.connect(lambda _i: self._changed())
        for field in (self.offset, self.aside_x, self.aside_y):
            field.valueChanged.connect(lambda _v: self._changed())

    def _changed(self) -> None:
        self.changed.emit(Height(ref=REFS[self.ref.current()][0],
                                 offset_mm=self.offset.value(),
                                 aside_x_mm=self.aside_x.value(),
                                 aside_y_mm=self.aside_y.value()))

    def show_height(self, height: Height, note: str,
                    depth_mm: float | None) -> None:
        self.ref.set_current([ref for ref, _text in REFS].index(height.ref))
        _put(self.offset, height.offset_mm)
        _put(self.aside_x, height.aside_x_mm)
        _put(self.aside_y, height.aside_y_mm)
        self.sentence.setText(height_words(height))
        self.note.setText(note)
        self.drawing.height_ = height
        self.drawing.depth_mm = depth_mm or DEPTH_MM
        self.drawing.update()


class _Fields(QWidget):
    """Fields saying `edited` as they change."""

    edited = Signal(str, dict)              # key, {field: value}

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("plain")
        self._filling = False

    def _say(self, field: str):
        """A slot sending the one field's new value."""
        def say(value) -> None:
            if not self._filling:
                self.edited.emit(field, {field: value})
        return say


class AmountForm(_Fields):
    """How much and how fast over Take it at and Put it at, the heights only
    for wells: a point has its own height, and so has the trash."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        box = card(self)
        row = QHBoxLayout()
        row.setSpacing(SPACING * 3)
        box.layout().addLayout(row)
        self.volume = _number(box, 0.1, MAX_UL, " µl")
        self.take_flow = _number(box, 0.1, MAX_FLOW, " µl/s")
        self.put_flow = _number(box, 0.1, MAX_FLOW, " µl/s")
        for label, field, name in (
                ("Volume per well", self.volume, "volume_ul"),
                ("Take speed", self.take_flow, "take_flow_ul_s"),
                ("Put speed", self.put_flow, "put_flow_ul_s")):
            row.addWidget(_labeled(box, label, field), 1)
            field.valueChanged.connect(self._say(name))
        column.addWidget(box)
        heights = QHBoxLayout()
        heights.setSpacing(SPACING * 2)
        self.take_at = HeightCard("TAKE IT AT", "tips", self)
        self.put_at = HeightCard("PUT IT AT", "plate", self)
        self.take_at.changed.connect(self._say("take_at"))
        self.put_at.changed.connect(self._say("put_at"))
        heights.addWidget(self.take_at, 1)
        heights.addWidget(self.put_at, 1)
        column.addLayout(heights)
        self.no_height = muted_label(NO_HEIGHT, self)
        column.addWidget(self.no_height)

    def show_step(self, step, take: tuple, put: tuple) -> None:
        """`take`, `put`: for each end the note and the well's depth."""
        _put(self.volume, step.volume_ul)
        _put(self.take_flow, step.take_flow_ul_s)
        _put(self.put_flow, step.put_flow_ul_s)
        taken = step.source.kind == "wells"
        put_in = step.dest.kind == "wells"
        self.take_at.setVisible(taken)
        self.put_at.setVisible(put_in)
        self.no_height.setVisible(not (taken and put_in))
        self.take_at.show_height(step.take_at, *take)
        self.put_at.show_height(step.put_at, *put)


class ExtrasForm(_Fields):
    """A card for each switch, its help line under it."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        self.mix_after, self.mix_help, self.mix_box = self._switch(
            column, "Mix after putting it in", "", "mix_after")
        self.mix_fields = QWidget(self.mix_box)
        self.mix_fields.setObjectName("plain")
        row = QHBoxLayout(self.mix_fields)
        row.setContentsMargins(0, 0, 0, 0)
        self.mix_cycles = spin_box(self.mix_fields)
        self.mix_cycles.setRange(1, MAX_CYCLES)
        self.mix_cycles.setSuffix(" times")
        self.mix_cycles.valueChanged.connect(self._say("mix_cycles"))
        self.mix_ul = _number(self.mix_fields, 0.1, MAX_UL, " µl")
        self.mix_ul.valueChanged.connect(self._say("mix_ul"))
        for field in (self.mix_cycles, self.mix_ul):
            field.setFixedWidth(SHORT_FIELD_PX)
            row.addWidget(field)
        self.mix_box.layout().itemAt(0).layout().addWidget(self.mix_fields)
        self.pause_after_first, _said, _box = self._switch(
            column, "Pause after the first well",
            "So you can check it before the rest.", "pause_after_first")
        self.empty_at_end, _said, _box = self._switch(
            column, "Empty the tip into the trash at the end",
            "Leftover liquid goes to the trash.", "empty_at_end")
        self.slow_lift, _said, self.lift_box = self._switch(
            column, "Retract slowly",
            "Up slowly after taking, so tissue is not pulled along.",
            "slow_lift")
        box = card(self)
        row = QHBoxLayout()
        lines = QVBoxLayout()
        lines.setSpacing(2)
        lines.addWidget(QLabel("Wait after each well", box))
        lines.addWidget(muted_label(WAIT_HELP, box))
        row.addLayout(lines, 1)
        self.wait_after = _number(box, 0, MAX_WAIT_S, " s")
        self.wait_after.setFixedWidth(SHORT_FIELD_PX)
        self.wait_after.valueChanged.connect(self._say("wait_after_s"))
        row.addWidget(self.wait_after)
        box.layout().addLayout(row)
        column.addWidget(box)

    def _switch(self, column: QVBoxLayout, text: str, help_line: str,
                field: str) -> tuple[Switch, QLabel, QWidget]:
        """A card of a switch over its help line; the switch, the line, the
        card."""
        box = card(self)
        box.layout().setSpacing(2)
        line = QHBoxLayout()
        switch = Switch(text, parent=box)
        switch.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        switch.toggled.connect(self._say(field))
        line.addWidget(switch, 1)
        box.layout().addLayout(line)
        said = muted_label(help_line, box)
        box.layout().addWidget(said)
        column.addWidget(box)
        return switch, said, box

    def show_step(self, step) -> None:
        self.mix_box.setVisible(step.dest.kind == "wells")
        self.lift_box.setVisible(step.source.kind == "wells")
        # Not by blocking its signals: a switch's knob follows `toggled`.
        self._filling = True
        self.mix_after.setChecked(step.mix_after)
        self.pause_after_first.setChecked(step.pause_after_first)
        self.empty_at_end.setChecked(step.empty_at_end)
        self.slow_lift.setChecked(step.slow_lift)
        self._filling = False
        self.mix_help.setText(f"Pipettes up and down {step.mix_cycles} times "
                              "in each well.")
        self.mix_fields.setVisible(step.mix_after)
        _put(self.mix_cycles, step.mix_cycles)
        _put(self.mix_ul, step.mix_ul or step.volume_ul)
        _put(self.wait_after, step.wait_after_s)


class HowForm(_Fields):
    """The numbers of a step over its height, what the step does not use
    hidden: a point has its own height, and so has the trash."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        self.numbers = card(self)
        row = QHBoxLayout()
        row.setSpacing(SPACING * 3)
        self.numbers.layout().addLayout(row)
        self.cycles = spin_box(self.numbers)
        self.cycles.setRange(1, MAX_CYCLES)
        self.cycles.setSuffix(" times")
        self.volume = _number(self.numbers, 0.1, MAX_UL, " µl")
        self.flow = _number(self.numbers, 0.1, MAX_FLOW, " µl/s")
        self.mix_fields = []
        for label, field, name in (("Times", self.cycles, "cycles"),
                                   ("Volume", self.volume, "volume_ul"),
                                   ("Speed", self.flow, "flow_ul_s")):
            labeled = _labeled(self.numbers, label, field)
            row.addWidget(labeled, 1)
            field.valueChanged.connect(self._say(name))
            if field is not self.flow:
                self.mix_fields.append(labeled)
        column.addWidget(self.numbers)
        self.at = HeightCard("HEIGHT", "plate", self)
        self.at.changed.connect(self._say("at"))
        column.addWidget(self.at)
        self.no_height = muted_label(NO_HEIGHT, self)
        column.addWidget(self.no_height)

    def show_step(self, step, note: str, depth_mm: float | None) -> None:
        """`note`, `depth_mm`: of the height, for a well."""
        mixing = step.kind == "mix"
        for field in self.mix_fields:
            field.setVisible(mixing)
        self.numbers.setVisible(step.kind != "move_tip")
        height = uses_height(step)
        self.at.setVisible(height)
        self.no_height.setVisible(not height)
        if mixing:
            _put(self.cycles, step.cycles)
            _put(self.volume, step.volume_ul)
        if step.kind != "move_tip":
            _put(self.flow, step.flow_ul_s)
        self.at.show_height(step.at, note, depth_mm)


class WaitForm(_Fields):
    """The time, large, over its Minutes and Seconds and the help line."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        self.time = heading("", 1, self)
        column.addWidget(self.time, alignment=Qt.AlignmentFlag.AlignHCenter)
        box = card(self)
        row = QHBoxLayout()
        row.setSpacing(SPACING * 3)
        box.layout().addLayout(row)
        self.minutes = spin_box(box)
        self.minutes.setRange(0, MAX_MINUTES)
        self.minutes.setSuffix(" min")
        self.seconds = _number(box, 0, 59.9, " s")
        for label, field in (("Minutes", self.minutes),
                             ("Seconds", self.seconds)):
            row.addWidget(_labeled(box, label, field), 1)
            field.valueChanged.connect(lambda _v: self._changed())
        column.addWidget(box)
        # Its own alignment, not the layout's: a wrapping label given less
        # than the column is cut off.
        said = muted_label(WAIT_STEP_HELP, self)
        said.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(said)

    def _changed(self) -> None:
        self.edited.emit("seconds", {
            "seconds": self.minutes.value() * 60 + self.seconds.value()})

    def show_step(self, step) -> None:
        self.time.setText(duration(step.seconds))
        minutes, seconds = divmod(step.seconds, 60)
        _put(self.minutes, int(minutes))
        _put(self.seconds, round(seconds, 1))


class MessageForm(_Fields):
    """The message over how the Run step will show it."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        box = card(self)
        self.message = QLineEdit(box)
        self.message.textEdited.connect(self._say("message"))
        box.layout().addWidget(_labeled(box, "The message you will see",
                                        self.message))
        column.addWidget(box)
        column.addWidget(muted_label("The Run step will show:", self))
        self.strip = Banner("", tone="warn", parent=self)
        column.addWidget(self.strip)
        column.addWidget(muted_label(WAITING_WHY, self))

    def show_step(self, step) -> None:
        if self.message.text() != step.message:
            self.message.setText(step.message)
        self.strip.set_text(step.message)
