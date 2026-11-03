"""What an open plan step asks in its card (LIQUID.md §3.4 - §3.6).

The fields of a section that is not a place: Amount and Extras of a Move
liquid, How of a Mix, a Blow out or a Move the tip, the time of a Wait and the
message of a Wait for me. There is nothing to confirm: a change goes out as it
is typed, as `edited(key, values)`, the step's fields and their new values,
and the page makes it part of the plan. `key` names the field, so a number
typed digit by digit is one undo (`LiquidPlanPage._commit`). `show_section`
fills the fields from the step, saying nothing.

Take fields are shown only for wells as the source and Put fields only for
wells as the destination: a point has its own height, and so has the trash.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QStackedWidget, QVBoxLayout, QWidget)

from ...core.liquid_plan import Height
from ..theme import COLORS, SPACING
from ..theme.factory import double_spin_box, muted_label, select, spin_box
from .controls import Switch

__all__ = ["HeightFields", "StepForm", "uses_height"]

REFS = (("bottom", "Bottom"), ("middle", "Middle"), ("top", "Top"))
MAX_UL = 100000.0
MAX_FLOW = 1000.0
MAX_OFFSET_MM = 100.0
MAX_ASIDE_MM = 50.0
MAX_MINUTES = 9999
MAX_WAIT_S = 3600.0
MAX_CYCLES = 99


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


class HeightFields(QWidget):
    """A height: Bottom, Middle or Top, an offset, and Aside X and Y."""

    changed = Signal(object)                # the Height

    def __init__(self, title: str, color: str, parent: QWidget | None = None):
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        self.title = QLabel(title, self)
        self.title.setStyleSheet(f"color: {COLORS[color]};")
        grid.addWidget(self.title, 0, 0)
        self.ref = select(self)
        for ref, text in REFS:
            self.ref.addItem(text, ref)
        grid.addWidget(self.ref, 0, 1)
        self.offset = _number(self, -MAX_OFFSET_MM, MAX_OFFSET_MM, " mm", 2)
        grid.addWidget(self.offset, 0, 2)
        grid.addWidget(muted_label("Aside X, Y", self), 1, 0)
        self.aside_x = _number(self, -MAX_ASIDE_MM, MAX_ASIDE_MM, " mm", 2)
        self.aside_y = _number(self, -MAX_ASIDE_MM, MAX_ASIDE_MM, " mm", 2)
        grid.addWidget(self.aside_x, 1, 1)
        grid.addWidget(self.aside_y, 1, 2)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        self.ref.currentIndexChanged.connect(lambda _i: self._changed())
        for box in (self.offset, self.aside_x, self.aside_y):
            box.valueChanged.connect(lambda _v: self._changed())

    def _changed(self) -> None:
        self.changed.emit(Height(ref=self.ref.currentData(),
                                 offset_mm=self.offset.value(),
                                 aside_x_mm=self.aside_x.value(),
                                 aside_y_mm=self.aside_y.value()))

    def show_height(self, height: Height) -> None:
        self.ref.blockSignals(True)
        self.ref.setCurrentIndex(self.ref.findData(height.ref))
        self.ref.blockSignals(False)
        _put(self.offset, height.offset_mm)
        _put(self.aside_x, height.aside_x_mm)
        _put(self.aside_y, height.aside_y_mm)


class StepForm(QFrame):
    edited = Signal(str, dict)              # key, {field: value}

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("planForm")
        self._filling = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        self.stack = QStackedWidget(self)
        outer.addWidget(self.stack)
        self.pages = {"amount": self._amount(), "extras": self._extras(),
                      "how": self._how(), "wait": self._wait(),
                      "message": self._message()}
        for page in self.pages.values():
            self.stack.addWidget(page)

    def _say(self, field: str):
        """A slot sending the one field's new value."""
        def say(value) -> None:
            if not self._filling:
                self.edited.emit(field, {field: value})
        return say

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget(self)
        page.setObjectName("plain")
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        return page, column

    def _line(self, parent: QWidget, column: QVBoxLayout, label: str,
              field: QWidget) -> QWidget:
        """`label` and its field on a line of `column`; the line."""
        line = QWidget(parent)
        line.setObjectName("plain")
        row = QHBoxLayout(line)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel(label, line), 1)
        row.addWidget(field)
        column.addWidget(line)
        return line

    # -- the sections --------------------------------------------------------

    def _amount(self) -> QWidget:
        page, column = self._page()
        self.volume = _number(page, 0.1, MAX_UL, " µl")
        self.take_flow = _number(page, 0.1, MAX_FLOW, " µl/s")
        self.put_flow = _number(page, 0.1, MAX_FLOW, " µl/s")
        self._line(page, column, "Volume per well", self.volume)
        self._line(page, column, "Take speed", self.take_flow)
        self._line(page, column, "Put speed", self.put_flow)
        self.volume.valueChanged.connect(self._say("volume_ul"))
        self.take_flow.valueChanged.connect(self._say("take_flow_ul_s"))
        self.put_flow.valueChanged.connect(self._say("put_flow_ul_s"))
        self.take_at = HeightFields("Take it at", "tips", page)
        self.put_at = HeightFields("Put it at", "plate", page)
        self.take_at.changed.connect(self._say("take_at"))
        self.put_at.changed.connect(self._say("put_at"))
        column.addWidget(self.take_at)
        column.addWidget(self.put_at)
        return page

    def _switch(self, parent: QWidget, column: QVBoxLayout, text: str,
                help_line: str, field: str) -> tuple[Switch, QLabel, QWidget]:
        """A switch over its help line; the switch, the line, what holds
        them."""
        box = QWidget(parent)
        box.setObjectName("plain")
        lines = QVBoxLayout(box)
        lines.setContentsMargins(0, 0, 0, 0)
        lines.setSpacing(0)
        switch = Switch(text, parent=box)
        switch.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        switch.toggled.connect(self._say(field))
        lines.addWidget(switch)
        said = muted_label(help_line, box)
        lines.addWidget(said)
        column.addWidget(box)
        return switch, said, box

    def _extras(self) -> QWidget:
        page, column = self._page()
        self.mix_after, self.mix_help, self.mix_box = self._switch(
            page, column, "Mix after putting it in", "", "mix_after")
        self.mix_fields = QWidget(self.mix_box)
        self.mix_fields.setObjectName("plain")
        row = QHBoxLayout(self.mix_fields)
        row.setContentsMargins(0, 4, 0, 0)
        self.mix_cycles = spin_box(self.mix_fields)
        self.mix_cycles.setRange(1, MAX_CYCLES)
        self.mix_cycles.setSuffix(" times")
        self.mix_cycles.valueChanged.connect(self._say("mix_cycles"))
        self.mix_ul = _number(self.mix_fields, 0.1, MAX_UL, " µl")
        self.mix_ul.valueChanged.connect(self._say("mix_ul"))
        row.addWidget(self.mix_cycles)
        row.addWidget(self.mix_ul)
        self.mix_box.layout().addWidget(self.mix_fields)
        self.pause_after_first, _said, _box = self._switch(
            page, column, "Pause after the first well",
            "So you can check it before the rest.", "pause_after_first")
        self.empty_at_end, _said, _box = self._switch(
            page, column, "Empty the tip into the trash at the end",
            "Leftover liquid goes to the trash.", "empty_at_end")
        self.slow_lift, _said, self.lift_box = self._switch(
            page, column, "Lift out slowly",
            "Small steps up after taking, so tissue is not pulled along.",
            "slow_lift")
        self.wait_after = _number(page, 0, MAX_WAIT_S, " s")
        self.wait_after.valueChanged.connect(self._say("wait_after_s"))
        self._line(page, column, "Wait after each well", self.wait_after)
        return page

    def _how(self) -> QWidget:
        page, column = self._page()
        self.cycles = spin_box(page)
        self.cycles.setRange(1, MAX_CYCLES)
        self.cycles.setSuffix(" times")
        self.cycles.valueChanged.connect(self._say("cycles"))
        self.cycles_line = self._line(page, column, "Mix", self.cycles)
        self.how_ul = _number(page, 0.1, MAX_UL, " µl")
        self.how_ul.valueChanged.connect(self._say("volume_ul"))
        self.how_ul_line = self._line(page, column, "Volume", self.how_ul)
        self.how_flow = _number(page, 0.1, MAX_FLOW, " µl/s")
        self.how_flow.valueChanged.connect(self._say("flow_ul_s"))
        self.how_flow_line = self._line(page, column, "Speed", self.how_flow)
        self.at = HeightFields("Height", "plate", page)
        self.at.changed.connect(self._say("at"))
        column.addWidget(self.at)
        return page

    def _wait(self) -> QWidget:
        page, column = self._page()
        self.minutes = spin_box(page)
        self.minutes.setRange(0, MAX_MINUTES)
        self.minutes.setSuffix(" min")
        self.seconds = _number(page, 0, 59.9, " s")
        self.minutes.valueChanged.connect(lambda _v: self._wait_changed())
        self.seconds.valueChanged.connect(lambda _v: self._wait_changed())
        self._line(page, column, "Minutes", self.minutes)
        self._line(page, column, "Seconds", self.seconds)
        return page

    def _wait_changed(self) -> None:
        self.edited.emit("seconds", {
            "seconds": self.minutes.value() * 60 + self.seconds.value()})

    def _message(self) -> QWidget:
        page, column = self._page()
        column.addWidget(QLabel("The message you will see", page))
        self.message = QLineEdit(page)
        self.message.textEdited.connect(self._say("message"))
        column.addWidget(self.message)
        return page

    # -- from the step -------------------------------------------------------

    def show_section(self, step, section: str | None) -> None:
        """The fields of `section`; hidden for one asked on the left only."""
        page = self.pages.get(section)
        self.setVisible(page is not None)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        getattr(self, f"_show_{section}")(step)
        # Only the page shown counts for the card's height.
        self.stack.setFixedHeight(page.sizeHint().height())

    def _show_amount(self, step) -> None:
        _put(self.volume, step.volume_ul)
        _put(self.take_flow, step.take_flow_ul_s)
        _put(self.put_flow, step.put_flow_ul_s)
        self.take_at.setVisible(step.source.kind == "wells")
        self.put_at.setVisible(step.dest.kind == "wells")
        self.take_at.show_height(step.take_at)
        self.put_at.show_height(step.put_at)

    def _show_extras(self, step) -> None:
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

    def _show_how(self, step) -> None:
        mixing = step.kind == "mix"
        self.cycles_line.setVisible(mixing)
        self.how_ul_line.setVisible(mixing)
        self.how_flow_line.setVisible(step.kind != "move_tip")
        self.at.setVisible(uses_height(step))
        if mixing:
            _put(self.cycles, step.cycles)
            _put(self.how_ul, step.volume_ul)
        if step.kind != "move_tip":
            _put(self.how_flow, step.flow_ul_s)
        self.at.show_height(step.at)

    def _show_wait(self, step) -> None:
        minutes, seconds = divmod(step.seconds, 60)
        _put(self.minutes, int(minutes))
        _put(self.seconds, round(seconds, 1))

    def _show_message(self, step) -> None:
        if self.message.text() != step.message:
            self.message.setText(step.message)
