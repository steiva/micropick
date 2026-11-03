"""The points' list on Manual control: one row a point, X, Y and Z.

The points are the session's (`Session.points`): the memo points, then the
saved points (#62), the profile's positions less Robot setup's - or only
one kind (`kind`: the Memo points and the Saved points cards, #66). This
shows them and writes an edit back, and the "points" layer of the picture
follows the same list. A cell is edited by a double click, in a number box
that takes Enter; a memo point's Z box goes down to "travel height", which
is a point with no Z of its own (the tip stays at the travel height over
it), and a saved point's does not. A Z the tip cannot go to - below the
top of a deck module there, say (`Session.memo_problem`) - is red, with the
reason under the pointer.

A memo point's row has Save: it asks for a name and moves the point into
the profile (`Session.save_memo_point`, a point at the travel height with
that height as its Z); a saved
point's has "Saved" there, and its name is renamed by a double click
(`Session.rename_saved_point`). A name in use or Robot setup's is refused,
said through `said`. The × at the end of a row deletes that point: a memo
point at once (`Session.delete_memo_point`), a saved one once asked
(`Session.delete_saved_point`).

The table takes no focus: the arrows and PageUp/PageDown are the robot's
keys on this page, and a table with the keyboard would scroll on them
instead. The editor of a cell does take it, while it is open, as any field
does (`JogPanel`, "A field has the keys").
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView,
                               QInputDialog, QStyledItemDelegate,
                               QTableWidget, QTableWidgetItem, QToolButton)

from ..session import Session, SessionError
from ..theme.factory import double_spin_box, muted_label
from .overlays import ConfirmDialog

__all__ = ["MemoTable", "TRAVEL_TEXT", "Z_TOP_MM"]

COLUMNS = ("Point", "X", "Y", "Z", "", "")
_KEYS = {1: "x", 2: "y", 3: "z"}
Z_COLUMN = 3
SAVE_COLUMN = 4
DELETE_COLUMN = 5

# What a point with no Z shows, and the bottom of Z's box that means it.
TRAVEL_TEXT = "travel height"
# The boxes' ranges: the deck and a little round it, and Z to the top of
# any tip's travel; the robot's own limits are checked when it moves.
XY_RANGE = (-20.0, 420.0)
Z_TOP_MM = 200.0

_VALUE = Qt.ItemDataRole.UserRole
# On a row's name: True for a saved point.
_SAVED = Qt.ItemDataRole.UserRole + 1

# A Z the tip cannot go to (`Session.memo_problem`), with the reason as
# the cell's tooltip.
BAD = "#ff5c5c"


def _text(value) -> str:
    return TRAVEL_TEXT if value is None else f"{value:.2f}"


class _NumberDelegate(QStyledItemDelegate):
    """A number box for X, Y and Z; a memo point's Z's lowest value is
    "travel height". A saved point's name is a text field."""

    def createEditor(self, parent, option, index):
        if index.column() == 0:
            return super().createEditor(parent, option, index)
        box = double_spin_box(parent)
        box.setDecimals(2)
        box.setSingleStep(0.1)
        box.setFrame(False)
        if index.column() == Z_COLUMN:
            box.setRange(0.0, Z_TOP_MM)
            if not index.siblingAtColumn(0).data(_SAVED):
                box.setSpecialValueText(TRAVEL_TEXT)
        else:
            box.setRange(*XY_RANGE)
        return box

    def setEditorData(self, editor, index) -> None:
        if index.column() == 0:
            super().setEditorData(editor, index)
            return
        value = index.data(_VALUE)
        editor.setValue(editor.minimum() if value is None else float(value))

    def setModelData(self, editor, model, index) -> None:
        if index.column() == 0:
            super().setModelData(editor, model, index)
            return
        editor.interpretText()
        value = editor.value()
        if (index.column() == Z_COLUMN and editor.specialValueText()
                and value <= editor.minimum()):
            value = None
        model.setData(index, value, _VALUE)


class MemoTable(QTableWidget):
    # The point chosen in the list, by name; None when none is.
    chosen = Signal(object)
    # A point's Z was typed: the height (None: travel height).
    z_typed = Signal(object)
    # A refusal, as a sentence: a name in use, say.
    said = Signal(str)

    def __init__(self, session: Session, parent=None, *,
                 kind: str | None = None):
        """`kind`: "memo" or "saved" for one kind of point; None for both."""
        super().__init__(0, len(COLUMNS), parent)
        self.session = session
        self.kind = kind
        self._filling = False
        self.setHorizontalHeaderLabels(COLUMNS)
        self.verticalHeader().hide()
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for column in (SAVE_COLUMN, DELETE_COLUMN):
            header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents)
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

    def _row(self, name: str) -> int | None:
        return next((row for row in range(self.rowCount())
                     if self.item(row, 0).text() == name), None)

    def delete_button(self, name: str) -> QToolButton | None:
        """The × of the point named `name`."""
        row = self._row(name)
        return None if row is None else self.cellWidget(row, DELETE_COLUMN)

    def save_button(self, name: str) -> QToolButton | None:
        """The Save of the memo point named `name`; None for a saved one."""
        row = self._row(name)
        widget = None if row is None else self.cellWidget(row, SAVE_COLUMN)
        return widget if isinstance(widget, QToolButton) else None

    def is_saved(self, name: str) -> bool:
        row = self._row(name)
        return row is not None and bool(self.item(row, 0).data(_SAVED))

    def _button(self, text: str, tip: str, then) -> QToolButton:
        button = QToolButton(self)
        button.setText(text)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setToolTip(tip)
        button.clicked.connect(then)
        return button

    def _delete(self, name: str, saved: bool) -> None:
        if not saved:
            self.session.delete_memo_point(name)
        elif ConfirmDialog.confirm(self, f"Delete point {name}?",
                                   safe="Keep", destructive="Delete"):
            self.session.delete_saved_point(name)

    def _save(self, name: str) -> None:
        new, ok = QInputDialog.getText(self, "Save point",
                                       f"Name for {name}:")
        if not ok or not new.strip():
            return
        try:
            self.session.save_memo_point(name, new)
        except SessionError as exc:
            self.said.emit(str(exc))

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
        points = [p for p in self.session.points
                  if self.kind is None or p.saved == (self.kind == "saved")]
        names = [p.name for p in points]
        same = [(p.name, p.saved) for p in points] == [
            (self.item(r, 0).text(), bool(self.item(r, 0).data(_SAVED)))
            for r in range(self.rowCount())]
        chosen = self.chosen_name
        self._filling = True
        self.blockSignals(True)
        try:
            if not same:
                self.setRowCount(len(points))
            for row, point in enumerate(points):
                if not same:
                    self._fill_name(row, point)
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

    def _fill_name(self, row: int, point) -> None:
        """The name and the buttons of a row: Save or "Saved", and ×."""
        name = QTableWidgetItem(point.name)
        name.setData(_VALUE, point.name)
        name.setData(_SAVED, point.saved)
        if not point.saved:
            name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.setItem(row, 0, name)
        if point.saved:
            self.setCellWidget(row, SAVE_COLUMN, muted_label("Saved", self))
        else:
            self.setCellWidget(row, SAVE_COLUMN, self._button(
                "Save", f"Keep {point.name} as a saved point.",
                lambda: self._save(point.name)))
        self.setCellWidget(row, DELETE_COLUMN, self._button(
            "×", f"Delete {point.name}.",
            lambda: self._delete(point.name, point.saved)))

    def _edited(self, item: QTableWidgetItem) -> None:
        if self._filling:
            return
        if item.column() == 0:
            self._renamed(item)
            return
        if item.column() not in _KEYS:
            return
        name = self.item(item.row(), 0).text()
        key, value = _KEYS[item.column()], item.data(_VALUE)
        try:
            self.session.change_point(name, **{key: value})
        except SessionError as exc:
            self.said.emit(str(exc))
            self.refresh()
            return
        if key == "z":
            self.z_typed.emit(value)

    def _renamed(self, item: QTableWidgetItem) -> None:
        old, new = item.data(_VALUE), item.text()
        try:
            self.session.rename_saved_point(old, new)
        except SessionError as exc:
            self.said.emit(str(exc))
            self._filling = True
            item.setText(old)
            self._filling = False
