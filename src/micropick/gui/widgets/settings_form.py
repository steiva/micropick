"""Every picking setting, from the model rather than from a list of them.

`PickingConfig` has forty-odd fields and they change; a hand-written form
would be a second list of them, and the field this one forgot would be the
one an operator needed at two in the morning. So the form is built by
walking `model_fields`: the names, the order, the types and the defaults
all come from the schema, and a field added there appears here with no edit.

What that costs is prose. The schema explains itself in comments beside the
fields, and a comment is not data — pydantic cannot hand it over. So a row
shows the field's name, its type and its default in the tooltip, and the
filter box is how forty of them stay usable. The alternative, copying the
comments into a dictionary here, is the drift this module exists to avoid.

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
                               QDoubleSpinBox, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QScrollArea, QSpinBox,
                               QVBoxLayout, QWidget)

from ...config.schema import PickingConfig
from ..theme import SPACING

__all__ = ["PickingSettingsDialog", "field_widget"]

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
        box = QSpinBox()
        box.setRange(*INT_RANGE)
        box.setValue(int(value))
    else:
        box = QDoubleSpinBox()
        box.setRange(*FLOAT_RANGE)
        box.setDecimals(FLOAT_DECIMALS)
        box.setValue(float(value))
    box.setMinimumWidth(NUMBER_WIDTH)
    box.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    return box


class _Row:
    """One field: its widget, and how to read a value back out of it."""

    def __init__(self, name: str, annotation, value):
        self.name = name
        self.widget: QWidget
        self._read = None
        self._write = None

        literal, choices = _is_literal(annotation)
        pair, kind = _is_pair(annotation)

        if annotation is bool:
            box = QCheckBox()
            box.setChecked(bool(value))
            self.widget, self._read = box, box.isChecked
            self._write = lambda v: box.setChecked(bool(v))
        elif literal:
            box = QComboBox()
            box.addItems([str(c) for c in choices])
            box.setCurrentText(str(value))
            self.widget, self._read = box, box.currentText
            self._write = lambda v: box.setCurrentText(str(v))
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
        elif annotation in (int, float):
            box = _number(annotation, value)
            self.widget, self._read = box, box.value
            self._write = box.setValue
        elif annotation is str:
            text = QLineEdit(str(value))
            self.widget, self._read = text, text.text
            self._write = lambda v: text.setText(str(v))
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

    def set_value(self, value) -> None:
        """Put a value into the widget that is already in the form.

        Not a new widget: `QFormLayout.setWidget` into a cell that already
        holds one leaves the old one parented and painted, which is a
        second copy of half the form floating over the first.
        """
        if self._write is not None:
            self._write(value)


def field_widget(name: str, annotation, value) -> _Row:
    """One row, for a test or a caller that wants a single field."""
    return _Row(name, annotation, value)


class PickingSettingsDialog(QDialog):
    """The whole of `PickingConfig`, editable, saved to the profile."""

    def __init__(self, config: PickingConfig, *, profile_name: str = "",
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Picking settings")
        self.resize(640, 760)
        self._config = config
        self._rows: dict[str, _Row] = {}

        self.filter = QLineEdit(self)
        self.filter.setPlaceholderText("filter by name…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._apply_filter)

        form_host = QWidget()
        self._form = QFormLayout(form_host)
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        for name, field in type(config).model_fields.items():
            row = _Row(name, field.annotation, getattr(config, name))
            label = QLabel(name)
            label.setToolTip(self._hint(name, field))
            row.widget.setToolTip(label.toolTip())
            self._form.addRow(label, row.widget)
            self._rows[name] = row

        area = QScrollArea(self)
        area.setWidgetResizable(True)
        area.setWidget(form_host)

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
        layout.addWidget(QLabel(
            "Everything the picking run reads. Saved into the profile's "
            "picking.json; the values a run uses are the ones stored there."))
        layout.addWidget(self.filter)
        layout.addWidget(area, 1)
        layout.addWidget(self.message)
        layout.addWidget(buttons)

    @staticmethod
    def _hint(name: str, field) -> str:
        annotation = getattr(field.annotation, "__name__", None) or str(field.annotation)
        hint = f"{name}: {annotation}\ndefault {field.default!r}"
        if name == "model_file":
            # A free-text box for a file name is correct and is not what
            # anyone should be typing into by choice; the Profile page
            # offers what is actually in ml_models/.
            hint += "\nchosen from the weights in ml_models/ on the Profile page"
        return hint

    def _apply_filter(self, text: str) -> None:
        wanted = text.strip().lower()
        for index in range(self._form.rowCount()):
            label = self._form.itemAt(index, QFormLayout.ItemRole.LabelRole)
            field = self._form.itemAt(index, QFormLayout.ItemRole.FieldRole)
            if label is None or field is None:
                continue
            name = label.widget().text()
            shown = not wanted or wanted in name.lower()
            label.widget().setVisible(shown)
            field.widget().setVisible(shown)

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
