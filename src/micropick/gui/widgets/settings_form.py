"""Every picking setting, from the model rather than from a list of them.

`PickingConfig` has forty-odd fields and they change; a hand-written form
would be a second list of them, and the field this one forgot would be the
one an operator needed at two in the morning. So the form is built by
walking `model_fields`: the names, the order, the types and the defaults
all come from the schema, and a field added there appears here with no edit.

The words are `picking_fields`: a label, a unit, a tooltip and a group per
field, and for the few behind most failures the symptom that points at them,
shown in amber. Those are the operator's, which the schema's comments are
not. The walk still decides what is shown: a field the words do not cover
yet appears under Advanced by its code name, so the cost of forgetting one
is prose, never a setting. Main and Run are open; the technical groups fold
under Advanced, and the filter opens it when a match is inside.

Editing is refused by the model, not by the widgets
---------------------------------------------------
Nothing here validates. The values are collected and handed to
`PickingConfig(**values)`, and pydantic's error — an out-of-order window, a
bad literal — is shown as it comes. The widgets only have to be able to
express what the model accepts; being unable to express something invalid
is a bonus, never the check.
"""

from __future__ import annotations

import logging
import typing

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QScrollArea, QVBoxLayout, QWidget)

from ...config.schema import PickingConfig
from ..theme import SPACING
from ..theme.factory import Section, double_spin_box, heading, spin_box
from .picking_fields import (ADVANCED_GROUPS, FIELDS, FLOATER_MODE, MAIN,
                             MISS_POLICY, RUN)

__all__ = ["PickingSettingsDialog", "field_widget", "TROUBLE"]

# The settings behind most failed pickups and deposits. The status bar's
# amber, for the same reason: it has to read as "look here" in either theme.
TROUBLE = "#f0a030"
# Fields the schema has and `picking_fields` does not describe yet.
OTHER = "Other"
LABEL_WIDTH = 230
LABELS = {"miss_policy": MISS_POLICY, "floater_mode": FLOATER_MODE}

log = logging.getLogger(__name__)

# Wide enough for a number and its units without the spin arrows eating it.
NUMBER_WIDTH = 120
# Spin boxes need bounds. These are not validation - the model does that -
# only what the widget can express; deliberately far wider than anything
# sensible so the model is what refuses a bad value.
INT_RANGE = (-1_000_000, 1_000_000)
FLOAT_RANGE = (-1e6, 1e6)
FLOAT_DECIMALS = 3


def _is_literal(annotation) -> tuple[bool, tuple]:
    if typing.get_origin(annotation) is typing.Literal:
        return True, typing.get_args(annotation)
    return False, ()


def _is_pair(annotation) -> tuple[bool, type | None]:
    """A tuple of two of the same number, as the windows are."""
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is tuple and len(args) == 2 and args[0] is args[1]:
        if args[0] in (int, float):
            return True, args[0]
    return False, None


def _is_window(name: str) -> bool:
    """Whether a pair of numbers is a range rather than a point. By name,
    which is the only thing the schema offers: the type of a window and the
    type of a centre are the same tuple."""
    return name.endswith(("_window", "_threshold"))


def _number(kind: type, value) -> QWidget:
    if kind is int:
        box = spin_box()
        box.setRange(*INT_RANGE)
        box.setValue(int(value))
    else:
        box = double_spin_box()
        box.setRange(*FLOAT_RANGE)
        box.setDecimals(FLOAT_DECIMALS)
        box.setValue(float(value))
    box.setMinimumWidth(NUMBER_WIDTH)
    box.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    return box


class _Row:
    """One field: its widget, and how to read a value back out of it.

    `labels` names a literal's choices for the screen, the value itself
    staying what is stored; `unit` goes after a number.
    """

    def __init__(self, name: str, annotation, value, *,
                 labels: dict | None = None, unit: str = ""):
        self.name = name
        self.widget: QWidget
        self._read = None
        self._write = None
        self._changed = []

        literal, choices = _is_literal(annotation)
        pair, kind = _is_pair(annotation)

        if annotation is bool:
            box = QCheckBox()
            box.setChecked(bool(value))
            self.widget, self._read = box, box.isChecked
            self._write = lambda v: box.setChecked(bool(v))
            self._changed = [box.toggled]
        elif literal:
            box = QComboBox()
            for choice in choices:
                box.addItem(str((labels or {}).get(choice, choice)), choice)
            box.setCurrentIndex(max(0, box.findData(value)))
            self.widget, self._read = box, box.currentData
            self._write = lambda v: box.setCurrentIndex(max(0, box.findData(v)))
            self._changed = [box.currentIndexChanged]
        elif pair:
            low, high = (_number(kind, value[0]), _number(kind, value[1]))
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(SPACING)
            row.addWidget(low)
            # A window reads as "250 to 500" and a point does not: comparing
            # the two numbers of circle_center is not a thing anyone does.
            row.addWidget(QLabel("to" if _is_window(name) else ","))
            row.addWidget(high)
            row.addStretch(1)
            self.widget = holder
            self._read = lambda: (low.value(), high.value())
            self._write = lambda v: (low.setValue(v[0]), high.setValue(v[1]))
            self._changed = [low.valueChanged, high.valueChanged]
        elif annotation in (int, float):
            box = _number(annotation, value)
            if unit:
                box.setSuffix(f" {unit}")
            self.widget, self._read = box, box.value
            self._write = box.setValue
            self._changed = [box.valueChanged]
        elif annotation is str:
            text = QLineEdit(str(value))
            self.widget, self._read = text, text.text
            self._write = lambda v: text.setText(str(v))
            self._changed = [text.editingFinished]
        else:
            # Anything the form cannot express is shown and left alone, so a
            # field added to the schema is visible here even before this
            # module learns its type.
            text = QLineEdit(str(value))
            text.setReadOnly(True)
            text.setToolTip("this setting is not editable here; edit "
                            "picking.json in the profile")
            self.widget, self._read = text, None

    @property
    def editable(self) -> bool:
        return self._read is not None

    def value(self):
        return self._read()

    def on_change(self, callback) -> None:
        """Call `callback()` whenever the operator changes the value."""
        for signal in self._changed:
            signal.connect(lambda *_: callback())

    def set_value(self, value) -> None:
        """Put a value into the widget that is already in the form.

        Not a new widget: `QFormLayout.setWidget` into a cell that already
        holds one leaves the old one parented and painted, which is a
        second copy of half the form floating over the first.
        """
        if self._write is not None:
            self._write(value)


def field_widget(name: str, annotation, value, *, labels: dict | None = None,
                 unit: str = "") -> _Row:
    """One row, for a test or a caller that wants a single field."""
    return _Row(name, annotation, value, labels=labels, unit=unit)


class PickingSettingsDialog(QDialog):
    """The whole of `PickingConfig`, editable, saved to the profile: the
    settings a run is tuned with first, the technical ones folded under
    Advanced (`picking_fields`)."""

    def __init__(self, config: PickingConfig, *, profile_name: str = "",
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Picking settings")
        self.resize(760, 820)
        self._config = config
        self._rows: dict[str, _Row] = {}
        self._labels: dict[str, QLabel] = {}

        self.filter = QLineEdit(self)
        self.filter.setPlaceholderText("filter…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._apply_filter)

        forms = self._forms(config)
        host = QWidget()
        column = QVBoxLayout(host)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        for title in (MAIN, RUN):
            section = Section(title, parent=host)
            section.body.layout().addLayout(forms.pop(title))
            column.addWidget(section)
        self.advanced = Section("Advanced", collapsed=True, parent=host)
        for title in (*ADVANCED_GROUPS, OTHER):
            form = forms.pop(title, None)
            if form is None:
                continue
            self.advanced.body.layout().addWidget(heading(title, 3))
            self.advanced.body.layout().addLayout(form)
        column.addWidget(self.advanced)
        column.addStretch(1)

        area = QScrollArea(self)
        area.setWidgetResizable(True)
        # Down only: the labels wrap to the width there is.
        area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(host)

        legend = QLabel(
            f"Picking settings, saved into the profile's picking.json. "
            f"<span style='color:{TROUBLE}'><b>Amber</b></span> marks the ones "
            f"behind most failed pickups and deposits; hover over any setting "
            f"for what it does.")
        legend.setWordWrap(True)

        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.hide()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults, self)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        buttons.button(
            QDialogButtonBox.StandardButton.RestoreDefaults
        ).clicked.connect(self._restore_defaults)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(
            f"Save to {profile_name}" if profile_name else "Save")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2,
                                  SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addWidget(legend)
        layout.addWidget(self.filter)
        layout.addWidget(area, 1)
        layout.addWidget(self.message)
        layout.addWidget(buttons)

    def _forms(self, config: PickingConfig) -> dict[str, QFormLayout]:
        """A form per group, every field of the schema in one of them: the
        described ones in `FIELDS`' order, the rest under OTHER."""
        fields = type(config).model_fields
        order = [n for n in FIELDS if n in fields] + [
            n for n in fields if n not in FIELDS]
        forms: dict[str, QFormLayout] = {}
        for name in order:
            spec = FIELDS.get(name)
            group = spec.group if spec else OTHER
            form = forms.get(group)
            if form is None:
                form = forms[group] = QFormLayout()
                form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
                form.setFieldGrowthPolicy(
                    QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
            field = fields[name]
            row = _Row(name, field.annotation, getattr(config, name),
                       labels=LABELS.get(name), unit=spec.unit if spec else "")
            label = QLabel(spec.label if spec else name)
            # Wrapped and capped, so a long name takes two lines rather
            # than pushing the number off the dialog.
            label.setWordWrap(True)
            label.setFixedWidth(LABEL_WIDTH)
            if spec and spec.trouble:
                label.setText(f"<span style='color:{TROUBLE}'><b>"
                              f"{spec.label}</b></span>")
            tip = self._hint(name, field, spec)
            label.setToolTip(tip)
            row.widget.setToolTip(tip)
            form.addRow(label, row.widget)
            self._rows[name] = row
            self._labels[name] = label
        return forms

    @staticmethod
    def _hint(name: str, field, spec) -> str:
        """Rich text: what the setting does, the symptom that points at it,
        and its code name and default for whoever reads picking.json."""
        default = field.default
        if isinstance(default, tuple):
            default = " to ".join(f"{v:g}" if isinstance(v, float) else str(v)
                                  for v in default)
        if spec is None:
            return (f"<b>{name}</b><br>Not described yet: see the comment "
                    f"beside it in config/schema.py.<br><i>default {default}"
                    f"</i>")
        parts = [f"<b>{spec.label}</b>", spec.tip]
        if spec.trouble:
            parts.append(f"<span style='color:{TROUBLE}'><b>If something "
                         f"fails:</b></span> {spec.trouble}")
        parts.append(f"<i>{name} in picking.json, default {default}"
                     f"{' ' + spec.unit if spec.unit else ''}</i>")
        # Wrapped, not one line across the screen: a <p> with a width.
        return "<p style='white-space:normal; width:360px'>" + \
            "<br><br>".join(parts) + "</p>"

    def _apply_filter(self, text: str) -> None:
        """Rows whose screen name or code name hold the text. Advanced opens
        while a filter is typed, so a match folded inside it is seen."""
        wanted = text.strip().lower()
        for name, row in self._rows.items():
            spec = FIELDS.get(name)
            words = f"{name} {spec.label if spec else ''}".lower()
            shown = not wanted or wanted in words
            self._labels[name].setVisible(shown)
            row.widget.setVisible(shown)
        if wanted:
            self.advanced.set_collapsed(False)

    def _restore_defaults(self) -> None:
        """The model's own defaults, not this dialog's idea of them."""
        fresh = PickingConfig()
        for name, row in self._rows.items():
            row.set_value(getattr(fresh, name))

    def values(self) -> dict:
        return {name: row.value() for name, row in self._rows.items()
                if row.editable}

    def config(self) -> PickingConfig:
        """The edited config, or a pydantic error to show the operator."""
        values = dict(self._config.model_dump())
        values.update(self.values())
        return PickingConfig(**values)

    def _accept(self) -> None:
        try:
            self.result_config = self.config()
        except Exception as exc:                     # noqa: BLE001
            # The model's own words: an out-of-order window says which one.
            self.message.setText(str(exc))
            self.message.show()
            return
        self.accept()
