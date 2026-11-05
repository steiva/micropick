"""The points as one list of rows of our own (#112, MANUAL.md §2.5, §5).

The points are the session's (`Session.points`): the memo points, then the
saved points (#62), the profile's positions less Robot setup's. This shows
them and writes an edit back, and the "points" layer of the picture follows
the same list. A row is its star, its name, X, Y and Z. ☆ asks a name, the
point's own by default, and keeps it in the profile
(`Session.save_memo_point`, a point at the travel height with that height as
its Z); ★ asks, and makes it a memo point again
(`Session.unkeep_position`). `StarButton` and `PointButton` are Liquid's too,
whose star is never taken off.

A double click on a cell edits it, in a box over the cell: Enter or leaving
it keeps what was typed, Esc puts it back and is no Stop. A memo point's Z
box goes down to "travel", a point with no Z of its own (the tip stays at the
travel height over it), and a saved point's does not; only a saved point's
name is edited (`Session.rename_saved_point`). A name in use or Robot setup's
is refused, said through `said`. A Z the tip cannot go to - below the top of
a deck module there, say (`Session.memo_problem`) - is red, with the reason
under the pointer.

A click chooses a row, a click on the chosen one lets it go. With `actions`
the chosen row unfolds Camera to, Tip to, Z from tip and Delete under the
point: the page connects the first three, Delete is the list's (a memo point
at once, a saved one once asked).

Unfolding lifts every row under the one it folds away from, so the second
press of a double click would come down on Tip to, and a double click never
moves the robot. A click therefore chooses at once and unfolds only after the
double click's interval, and the actions take no click for one interval more,
for a double click slower than that.

Nothing here takes the keyboard: the arrows and PageUp/PageDown are the robot's
keys on this page, and a list with the keyboard would scroll on them instead.
The box of a cell does, while it is open, as any field does (`JogPanel`, "A
field has the keys"), and gives it back to no other field.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QFrame,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QScrollArea, QSizePolicy, QToolButton,
                               QVBoxLayout, QWidget)

from ..session import Session, SessionError
from ..theme import COLORS, mono_font
from ..theme.factory import (compact, danger_text_button, double_spin_box,
                             laid_out_at_once, secondary_button)
from .controls import CutLabel
from .overlays import ConfirmDialog

__all__ = ["BAD", "PointButton", "PointList", "PointRow", "StarButton",
           "TRAVEL_TEXT", "Z_TOP_MM"]

# What a point with no Z shows, and the bottom of Z's box that means it.
TRAVEL_TEXT = "travel"
# The boxes' ranges: the deck and a little round it, and Z to the top of
# any tip's travel; the robot's own limits are checked when it moves.
XY_RANGE = (-20.0, 420.0)
Z_TOP_MM = 200.0

# A Z the tip cannot go to (`Session.memo_problem`), with the reason as
# the cell's tooltip.
BAD = "#ff5c5c"

# The row, px (ManualPick.dc.html).
ROW_HEIGHT = 44
STAR_WIDTH = 34
CELL_WIDTHS = {"x": 62, "y": 62, "z": 66}
CELL_GAP = 6


class StarButton(QToolButton):
    """★ kept in the profile, ☆ not. `removable`: ★ can be clicked too."""

    def __init__(self, saved: bool, parent: QWidget, *,
                 removable: bool = False):
        super().__init__(parent)
        self.setText("★" if saved else "☆")
        self.setEnabled(removable or not saved)
        self.setToolTip("Remove from saved points." if saved and removable
                        else "" if saved else "Keep this point.")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet(f"QToolButton {{ border: none; background:"
                           f" transparent; color: {COLORS['warn']};"
                           f" font-size: 16px; }}")


class PointButton(QWidget):
    """A point as Liquid's picker has it: its star and its name, which uses
    it. A click on ☆ keeps it."""

    def __init__(self, name: str, saved: bool, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("plain")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.star = StarButton(saved, self)
        row.addWidget(self.star)
        self.use = secondary_button(name, self)
        self.use.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row.addWidget(self.use)


class _Name(CutLabel):
    """A name cut to the room there is, whole under the pointer."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        font = self.font()
        font.setPixelSize(14)
        font.setWeight(font.Weight.DemiBold)
        self.setFont(font)

    def set_name(self, name: str) -> None:
        self.set_full(name)


def _text(value) -> str:
    return TRAVEL_TEXT if value is None else f"{value:.2f}"


def _grid(row: QHBoxLayout, cells: dict) -> None:
    """A name that takes the room left, and X, Y and Z at their widths: the
    captions' and every row's, so the columns line up."""
    row.setSpacing(CELL_GAP)
    row.addWidget(cells["name"], 1)
    for key, width in CELL_WIDTHS.items():
        cells[key].setFixedWidth(width)
        cells[key].setAlignment(Qt.AlignmentFlag.AlignRight
                                | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(cells[key])


class PointRow(QFrame):
    """A point, and under it the list's actions while it is chosen."""

    pressed = Signal()
    # The cell double-clicked: "name", "x", "y", "z", or None between them.
    double_clicked = Signal(object)
    # A cell's box closed on a new value: the cell and the value.
    edited = Signal(str, object)

    def __init__(self, point, parent: QWidget, *, problem: str = ""):
        super().__init__(parent)
        self.setObjectName("pointRow")
        self.point = point
        self.editor = None              # the open box
        self._key = ""                  # the cell it is on
        self._before = None             # what it opened on

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.head = QWidget(self)
        self.head.setObjectName("plain")
        self.head.setFixedHeight(ROW_HEIGHT)
        line = QHBoxLayout(self.head)
        line.setContentsMargins(0, 0, 10, 0)
        line.setSpacing(0)
        self.star = StarButton(point.saved, self.head, removable=True)
        self.star.setFixedSize(STAR_WIDTH, ROW_HEIGHT)
        line.addWidget(self.star)
        self.cells = {"name": _Name(self.head)}
        for key in CELL_WIDTHS:
            self.cells[key] = QLabel(self.head)
            self.cells[key].setFont(mono_font(13))
        cells = QHBoxLayout()
        _grid(cells, self.cells)
        line.addLayout(cells, 1)
        column.addWidget(self.head)
        self.show_point(point, problem)
        self.set_chosen(False)

    @property
    def name(self) -> str:
        return self.point.name

    def show_point(self, point, problem: str) -> None:
        """The same point's values again; an open box stays as typed."""
        self.point = point
        self.cells["name"].set_name(point.name)
        for key in CELL_WIDTHS:
            self.cells[key].setText(_text(getattr(point, key)))
        # Said on the point's Z: the height is what is wrong when it is
        # below a module's top, and the only thing to change.
        z = self.cells["z"]
        ink = (BAD if problem else COLORS["muted"] if point.z is None
               else COLORS["text"])
        z.setStyleSheet(f"color: {ink};")
        z.setToolTip(problem)

    def set_chosen(self, chosen: bool) -> None:
        fill = COLORS["selected-bg"] if chosen else "transparent"
        line = COLORS["accent"] if chosen else "transparent"
        self.setStyleSheet(f"QFrame#pointRow {{ background: {fill};"
                           f" border: 1px solid {line};"
                           f" border-radius: 10px; }}")

    # -- the mouse -----------------------------------------------------------

    def _on_head(self, event) -> bool:
        return self.head.geometry().contains(event.position().toPoint())

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._on_head(event):
            self.pressed.emit()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if not self._on_head(event):
            return
        under = self.childAt(event.position().toPoint())
        self.double_clicked.emit(next(
            (key for key, cell in self.cells.items() if cell is under), None))

    # -- a cell's box --------------------------------------------------------

    def edit(self, key: str) -> None:
        if self.editor is not None:
            return
        if key == "name":
            box = QLineEdit(self.point.name, self.head)
            self._before = self.point.name
        else:
            box = self._number_box(key)
            self._before = box.value()
        box.setGeometry(self.cells[key].geometry().translated(
            self.cells[key].parentWidget().mapTo(self.head,
                                                 self.head.rect().topLeft())))
        box.installEventFilter(self)
        self.editor, self._key = box, key
        box.show()
        box.setFocus()
        box.selectAll()

    def _number_box(self, key: str):
        box = double_spin_box(self.head)
        box.setDecimals(2)
        box.setSingleStep(0.1)
        box.setFrame(False)
        box.setFont(mono_font(13))
        box.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        box.setAlignment(Qt.AlignmentFlag.AlignRight)
        if key == "z":
            box.setRange(0.0, Z_TOP_MM)
            if not self.point.saved:
                box.setSpecialValueText(TRAVEL_TEXT)
        else:
            box.setRange(*XY_RANGE)
        value = getattr(self.point, key)
        box.setValue(box.minimum() if value is None else float(value))
        return box

    def close_editor(self, keep: bool) -> None:
        """Close the box, the keyboard going to no other field; `keep`: say
        what was typed, if it is new."""
        box, self.editor = self.editor, None
        if box is None:
            return
        box.removeEventFilter(self)
        box.clearFocus()
        box.hide()
        box.deleteLater()
        if not keep:
            return
        if self._key == "name":
            value = box.text()
            if value.strip() != self._before:
                self.edited.emit("name", value)
            return
        box.interpretText()
        value = box.value()
        if value == self._before:
            return
        travel = (self._key == "z" and box.specialValueText()
                  and value <= box.minimum())
        self.edited.emit(self._key, None if travel else value)

    def eventFilter(self, watched, event) -> bool:
        if watched is not self.editor:
            return False
        kind = event.type()
        if kind == QEvent.Type.ShortcutOverride:
            # Esc is the window's Stop everywhere but in an open box.
            if event.key() == Qt.Key.Key_Escape:
                event.accept()
                return True
        elif kind == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.close_editor(False)
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.close_editor(True)
                return True
        elif kind == QEvent.Type.FocusOut:
            self.close_editor(True)
        return False


class _Actions(QWidget):
    """What is done to the chosen point. A click between its buttons is
    nobody's, not the row's."""

    def mousePressEvent(self, event) -> None:
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        event.accept()


class PointList(QWidget):
    # The point chosen in the list, by name; None when none is.
    chosen = Signal(object)
    # A point's Z was typed: the height (None: travel height).
    z_typed = Signal(object)
    # A refusal, as a sentence: a name in use, say.
    said = Signal(str)
    # An answer, as a sentence.
    told = Signal(str)

    def __init__(self, session: Session, parent=None, *,
                 actions: bool = False):
        super().__init__(parent)
        self.session = session
        self.setObjectName("plain")
        self.rows: list[PointRow] = []
        self.chosen_name: str | None = None
        self._letting_go = False
        # Until a double click is ruled out, and then until it is past.
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.timeout.connect(self._settled)
        self._inert = QTimer(self)
        self._inert.setSingleShot(True)
        self._inert.timeout.connect(lambda: self._take_clicks(True))

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        column.addLayout(self._captions())

        self.area = QScrollArea(self)
        self.area.setObjectName("plain")
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setWidgetResizable(True)
        self.area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.inner = QWidget(self.area)
        self.inner.setObjectName("plain")
        self._lines = QVBoxLayout(self.inner)
        self._lines.setContentsMargins(0, 0, 0, 0)
        self._lines.setSpacing(4)
        self._lines.addStretch(1)
        self.area.setWidget(self.inner)
        column.addWidget(self.area, 1)

        self.actions = self._make_actions() if actions else None
        session.memo_points_changed.connect(self.refresh)
        # The deck modules, and with them what is too low, are the profile's.
        session.profile_changed.connect(lambda _p: self.refresh())
        self.refresh()

    def _captions(self) -> QHBoxLayout:
        line = QHBoxLayout()
        line.setContentsMargins(STAR_WIDTH + 1, 0, 11, 0)
        cells = {}
        for key, text in (("name", "POINT"), ("x", "X"), ("y", "Y"),
                          ("z", "Z")):
            cells[key] = QLabel(text, self)
            cells[key].setObjectName("caption")
        _grid(line, cells)
        return line

    def _make_actions(self) -> QWidget:
        bar = _Actions(self)
        bar.setObjectName("plain")
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 0, 8, 8)
        row.setSpacing(CELL_GAP)
        self.camera_to = compact(secondary_button("Camera to", bar))
        self.tip_to = compact(secondary_button("Tip to", bar))
        self.z_from_tip = compact(secondary_button("Z from tip", bar))
        self.delete = compact(danger_text_button("Delete", bar))
        self.delete.setToolTip("Delete the chosen point.")
        self.delete.clicked.connect(self._delete_chosen)
        for button in (self.camera_to, self.tip_to, self.z_from_tip,
                       self.delete):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setStyleSheet("padding: 0 2px;")
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Ignored,
                                 QSizePolicy.Policy.Fixed)
            row.addWidget(button, 1)
        bar.hide()
        return bar

    # -- what is listed ------------------------------------------------------

    @property
    def names(self) -> list[str]:
        return [row.name for row in self.rows]

    def row(self, name: str | None) -> PointRow | None:
        return next((row for row in self.rows if row.name == name), None)

    def refresh(self) -> None:
        """The rows again from the session, the chosen one kept. Rewritten
        in place when the points are the same ones: an edit's own echo then
        does not pull the list out from under the box that made it."""
        points = self.session.points
        same = [(p.name, p.saved) for p in points] == [
            (row.name, row.point.saved) for row in self.rows]
        if same:
            for row, point in zip(self.rows, points):
                row.show_point(point, self.session.memo_problem(point))
            return
        chosen = self.chosen_name
        with laid_out_at_once(self):
            self._park_actions()
            for row in self.rows:
                row.close_editor(False)
                self._lines.removeWidget(row)
                row.hide()
                row.deleteLater()
            self.rows = [self._make_row(point) for point in points]
            for at, row in enumerate(self.rows):
                self._lines.insertWidget(at, row)
            if self.row(chosen) is None:
                self.chosen_name = None
            self._show_chosen()
            self._place_actions()
        if chosen is not None and self.chosen_name is None:
            self._settle.stop()
            self.chosen.emit(None)

    def _make_row(self, point) -> PointRow:
        row = PointRow(point, self.inner,
                       problem=self.session.memo_problem(point))
        row.pressed.connect(lambda: self._pressed(row.name))
        row.double_clicked.connect(lambda key: self._double_clicked(row, key))
        row.edited.connect(lambda key, value: self._edited(row.name, key,
                                                           value))
        row.star.clicked.connect(lambda: self._star(row.name))
        return row

    # -- choosing ------------------------------------------------------------

    def choose(self, name: str | None) -> None:
        """Choose a point, or none, its actions unfolded at once."""
        self._settle.stop()
        self._letting_go = False
        self._set_chosen(name if self.row(name) is not None else None)
        with laid_out_at_once(self):
            self._place_actions()
        row = self.row(self.chosen_name)
        if row is not None:
            QTimer.singleShot(0, self, lambda: self._bring_into_view(
                row.name))

    def _bring_into_view(self, name: str) -> None:
        row = self.row(name)
        if row is not None:
            self.area.ensureWidgetVisible(row, 0, 0)

    def _set_chosen(self, name: str | None) -> None:
        if name == self.chosen_name:
            return
        self.chosen_name = name
        self._show_chosen()
        self.chosen.emit(name)

    def _show_chosen(self) -> None:
        for row in self.rows:
            row.set_chosen(row.name == self.chosen_name)

    def _pressed(self, name: str) -> None:
        """Chosen at once; what would move the rows waits, see the module's
        docstring."""
        self._letting_go = name == self.chosen_name
        self._set_chosen(name)
        self._settle.start(QApplication.doubleClickInterval())

    def _settled(self) -> None:
        if self._letting_go:
            self._letting_go = False
            self._set_chosen(None)
        with laid_out_at_once(self):
            self._place_actions()
        if self.actions is not None and self.actions.isVisibleTo(self):
            self._take_clicks(False)
            self._inert.start(QApplication.doubleClickInterval())

    def _take_clicks(self, on: bool) -> None:
        self.actions.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, not on)

    def _park_actions(self) -> None:
        """Out of every row, which may be about to go."""
        if self.actions is not None:
            self.actions.hide()
            self.actions.setParent(self)

    def _place_actions(self) -> None:
        if self.actions is None:
            return
        row = self.row(self.chosen_name)
        if row is None:
            self._park_actions()
        elif self.actions.parent() is not row:
            self._park_actions()
            self.actions.setParent(row)
            row.layout().addWidget(self.actions)
            self.actions.show()

    # -- what a row asks -----------------------------------------------------

    def _double_clicked(self, row: PointRow, key) -> None:
        self._letting_go = False
        if key is None or (key == "name" and not row.point.saved):
            return
        row.edit(key)

    def _edited(self, name: str, key: str, value) -> None:
        if key == "name":
            self._rename(name, value)
            return
        try:
            self.session.change_point(name, **{key: value})
        except SessionError as exc:
            self.said.emit(str(exc))
            self.refresh()
            return
        if key == "z":
            self.z_typed.emit(value)

    def _rename(self, old: str, new: str) -> None:
        chosen = self.chosen_name == old
        try:
            self.session.rename_saved_point(old, new)
        except SessionError as exc:
            self.said.emit(str(exc))
            return
        if chosen:
            self.choose(new.strip())

    def _star(self, name: str) -> None:
        point = self.session.point(name)
        if point is None:
            return
        if point.saved:
            self._unkeep(name)
        else:
            self._keep(name)

    def _keep(self, name: str) -> None:
        new, ok = QInputDialog.getText(self, "Keep this point", "Name",
                                       text=name)
        if not ok or not new.strip():
            return
        chosen = self.chosen_name == name
        try:
            self.session.save_memo_point(name, new)
        except SessionError as exc:
            self.said.emit(str(exc))
            return
        if chosen:
            self.choose(new.strip())
        self.told.emit(f"{new.strip()} kept for later.")

    def _unkeep(self, name: str) -> None:
        if ConfirmDialog.confirm(self, f"Remove {name} from saved points?",
                                 safe="Keep", destructive="Remove"):
            self.session.unkeep_position(name)

    def _delete_chosen(self) -> None:
        point = self.session.point(self.chosen_name or "")
        if point is None:
            return
        if not point.saved:
            self.session.delete_memo_point(point.name)
        elif ConfirmDialog.confirm(self, f"Delete point {point.name}?",
                                   safe="Keep", destructive="Delete"):
            self.session.delete_saved_point(point.name)
