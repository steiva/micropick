"""The memo points' list on Manual control: one row a point, X, Y and Z.

The points are the session's (`Session.memo_points`); this shows them and
writes an edit back, and the "points" layer of the picture follows the same
list. A cell is edited by a double click, in a number box that takes Enter;
Z's box goes down to "travel height", which is a point with no Z of its own
(the tip stays at the travel height over it). A Z the tip cannot go to -
below the top of a deck module there, say (`Session.memo_problem`) - is
red, with the reason under the pointer.

The table takes no focus: the arrows and PageUp/PageDown are the robot's
keys on this page, and a table with the keyboard would scroll on them
instead. The editor of a cell does take it, while it is open, as any field
does (`JogPanel`, "A field has the keys").
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView,
                               QStyledItemDelegate, QTableWidget,
                               QTableWidgetItem)

from ..session import Session
from ..theme.factory import double_spin_box

__all__ = ["MemoTable", "TRAVEL_TEXT", "Z_TOP_MM"]

COLUMNS = ("Point", "X", "Y", "Z")
_KEYS = {1: "x", 2: "y", 3: "z"}
Z_COLUMN = 3

# What a point with no Z shows, and the bottom of Z's box that means it.
TRAVEL_TEXT = "travel height"
# The boxes' ranges: the deck and a little round it, and Z to the top of
# any tip's travel; the robot's own limits are checked when it moves.
XY_RANGE = (-20.0, 420.0)
Z_TOP_MM = 200.0

_VALUE = Qt.ItemDataRole.UserRole

# A Z the tip cannot go to (`Session.memo_problem`), with the reason as
# the cell's tooltip.
BAD = "#ff5c5c"


def _text(value) -> str:
    return TRAVEL_TEXT if value is None else f"{value:.2f}"


class _NumberDelegate(QStyledItemDelegate):
    """A number box for X, Y and Z; Z's lowest value is "travel height"."""

    def createEditor(self, parent, option, index):
        box = double_spin_box(parent)
        box.setDecimals(2)
        box.setSingleStep(0.1)
        box.setFrame(False)
        if index.column() == Z_COLUMN:
            box.setRange(0.0, Z_TOP_MM)
            box.setSpecialValueText(TRAVEL_TEXT)
        else:
            box.setRange(*XY_RANGE)
        return box

    def setEditorData(self, editor, index) -> None:
        value = index.data(_VALUE)
        editor.setValue(editor.minimum() if value is None else float(value))

    def setModelData(self, editor, model, index) -> None:
        editor.interpretText()
        value = editor.value()
        if index.column() == Z_COLUMN and value <= editor.minimum():
            value = None
        model.setData(index, value, _VALUE)


class MemoTable(QTableWidget):
    # The point chosen in the list, by name; None when none is.
    chosen = Signal(object)
    # A point's Z was typed: the height (None: travel height).
    z_typed = Signal(object)

    def __init__(self, session: Session, parent=None):
        super().__init__(0, len(COLUMNS), parent)
        self.session = session
        self._filling = False
        self.setHorizontalHeaderLabels(COLUMNS)
        self.verticalHeader().hide()
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.setItemDelegate(_NumberDelegate(self))
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMinimumHeight(140)
        self.itemChanged.connect(self._edited)
        self.itemSelectionChanged.connect(
            lambda: self.chosen.emit(self.chosen_name))
        session.memo_points_changed.connect(self.refresh)
        # The deck modules, and with them what is too low, are the profile's.
        session.profile_changed.connect(lambda _p: self.refresh())
        self.refresh()

    @property
    def chosen_name(self) -> str | None:
        rows = self.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.item(rows[0].row(), 0)
        return None if item is None else item.text()

    def choose(self, name: str | None) -> None:
        for row in range(self.rowCount()):
            if self.item(row, 0).text() == name:
                self.selectRow(row)
                return
        self.clearSelection()

    def refresh(self) -> None:
        """The rows again from the session, the chosen one kept. Rewritten
        in place when the points are the same ones: an edit's own echo then
        does not pull the table out from under the box that made it."""
        points = self.session.memo_points
        names = [p.name for p in points]
        same = names == [self.item(r, 0).text() for r in range(self.rowCount())]
        chosen = self.chosen_name
        self._filling = True
        self.blockSignals(True)
        try:
            if not same:
                self.setRowCount(len(points))
            for row, point in enumerate(points):
                if not same:
                    name = QTableWidgetItem(point.name)
                    name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.setItem(row, 0, name)
                for column, key in _KEYS.items():
                    value = getattr(point, key)
                    item = self.item(row, column) if same else None
                    if item is None:
                        item = QTableWidgetItem()
                        self.setItem(row, column, item)
                    item.setData(_VALUE, value)
                    item.setText(_text(value))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
                # Said on the point's Z: the height is what is wrong when
                # it is below a module's top, and the only thing to change.
                problem = self.session.memo_problem(point)
                z_item = self.item(row, Z_COLUMN)
                z_item.setForeground(QBrush(QColor(BAD)) if problem
                                     else QBrush())
                z_item.setToolTip(problem)
        finally:
            self.blockSignals(False)
            self._filling = False
        if not same:
            self.choose(chosen if chosen in names else None)
            self.chosen.emit(self.chosen_name)

    def _edited(self, item: QTableWidgetItem) -> None:
        if self._filling or item.column() not in _KEYS:
            return
        name = self.item(item.row(), 0).text()
        key, value = _KEYS[item.column()], item.data(_VALUE)
        self.session.change_memo_point(name, **{key: value})
        if key == "z":
            self.z_typed.emit(value)
