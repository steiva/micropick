"""The points as one list of rows of our own (#112, MANUAL.md §2.5, §5).

The points are the session's (`Session.points`): the memo points, then the
saved points (#62), the profile's positions less Robot setup's. This shows
them and writes an edit back, and the "points" layer of the picture follows
the same list, a thin line between the two kinds. A row is its star, its name,
X, Y, Z and its ×. ☆ asks a name, the point's own by default, and keeps it in
the profile
(`Session.save_memo_point`, a point at the travel height with that height as
its Z); ★ asks, and makes it a memo point again
(`Session.unkeep_position`); Liquid's Plan has the same list with
`star_removable=False`, its ★ never taken off.

A double click on a cell edits it, in a box over the cell: Enter or leaving
it keeps what was typed, Esc puts it back and is no Stop. A memo point's Z
box goes down to "travel", a point with no Z of its own (the tip stays at the
travel height over it), and a saved point's does not; only a saved point's
name is edited (`Session.rename_saved_point`). A name in use or Robot setup's
is refused, said through `said`. A Z the tip cannot go to - below the top of
a deck module there, say (`Session.memo_problem`) - is red, with the reason
under the pointer.

The × deletes the point: a memo point at once, a saved one once asked.

A click chooses a row, a click on the chosen one lets it go. With `actions`
the chosen row unfolds Camera to, Tip to and Z from tip under the point, for
the page to connect; with `use` too, Use point under them (Liquid's Plan: the
point for the step's place). Choosing a row changes no step: only Use point
does. `set_used` marks the row the step goes to, in the step's color.
`star_removable=False` is Liquid's ★, never taken off there.

A click unfolds at once. Folding the row above lifts the one clicked, and the
pointer is then on its actions: the second press of a double click would come
down on Tip to, and a double click never moves the robot. After a lift the
actions therefore take no click for two double click intervals, one for a
double click slower than the interval, and a double click there is the row's,
its cell read from the x alone. A click on the chosen row lets it go only after
the interval, since a double click keeps it.

Nothing here takes the keyboard: the arrows and PageUp/PageDown are the robot's
keys on this page, and a list with the keyboard would scroll on them instead.
The box of a cell does, while it is open, as any field does (`JogPanel`, "A
field has the keys"), and gives it back to no other field.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QFrame,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QScrollArea, QSizePolicy, QToolButton,
                               QVBoxLayout, QWidget)

from ..session import Session, SessionError
from ..theme import COLORS, mono_font
from ..theme.factory import (close_button, double_spin_box, laid_out_at_once,
                             primary_button, secondary_button)
from ..theme.icons import icon, size
from .controls import CutLabel
from .overlays import ConfirmDialog

__all__ = ["BAD", "PointList", "PointRow", "StarButton",
           "STAR_PX", "TRAVEL_TEXT", "Z_TOP_MM", "star_icon"]

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
STAR_PX = 16
CELL_WIDTHS = {"x": 56, "y": 56, "z": 56}
CELL_GAP = 6
DELETE_WIDTH = 24
ROW_END = 4
# A number's box has a padding the cell has not: it reaches over the gap.
BOX_REACH = 12


def star_icon(saved: bool) -> QIcon:
    """A filled star in the warning's ink, an outline in gray: the ★ and ☆ of
    a font hardly differ at this size. The same when disabled: Liquid's ★
    takes no click and still says the point is kept."""
    ink = COLORS["warn"] if saved else COLORS["text-2"]
    return icon("star", ink, STAR_PX, fill=saved, disabled=ink)


class StarButton(QToolButton):
    """★ kept in the profile, ☆ not, as its text; `star_icon` is what is seen.
    `removable`: ★ can be clicked too."""

    def __init__(self, saved: bool, parent: QWidget, *,
                 removable: bool = False):
        super().__init__(parent)
        self.setText("★" if saved else "☆")
        self.setIcon(star_icon(saved))
        self.setIconSize(size(STAR_PX))
        self.setEnabled(removable or not saved)
        self.setToolTip("Remove from saved points." if saved and removable
                        else "" if saved else "Keep this point.")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet("QToolButton { border: none;"
                           " background: transparent; }")


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

    def __init__(self, point, parent: QWidget, *, problem: str = "",
                 star_removable: bool = True):
        super().__init__(parent)
        self.setObjectName("pointRow")
        self.point = point
        self._chosen = False
        self._used = ""                 # the step's color while it uses it
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
        line.setContentsMargins(0, 0, ROW_END, 0)
        line.setSpacing(0)
        self.star = StarButton(point.saved, self.head,
                               removable=star_removable)
        self.star.setFixedSize(STAR_WIDTH, ROW_HEIGHT)
        line.addWidget(self.star)
        self.cells = {"name": _Name(self.head)}
        for key in CELL_WIDTHS:
            self.cells[key] = QLabel(self.head)
            self.cells[key].setFont(mono_font(13))
        cells = QHBoxLayout()
        _grid(cells, self.cells)
        line.addLayout(cells, 1)
        line.addSpacing(CELL_GAP)
        self.delete = close_button("Delete this point.", self.head)
        self.delete.setFixedSize(DELETE_WIDTH, DELETE_WIDTH)
        line.addWidget(self.delete)
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
        self._chosen = chosen
        self._style()

    def set_used(self, color: str) -> None:
        """The step's color while the step goes to this point, else ""."""
        self._used = color
        self._style()

    def _style(self) -> None:
        fill = COLORS["selected-bg"] if self._chosen else "transparent"
        line = COLORS["accent"] if self._chosen else "transparent"
        left = (f" border-left: 4px solid {self._used};" if self._used
                else "")
        self.setStyleSheet(f"QFrame#pointRow {{ background: {fill};"
                           f" border: 1px solid {line};{left}"
                           f" border-radius: 10px; }}")

    # -- the mouse -----------------------------------------------------------

    def _on_head(self, event) -> bool:
        return self.head.geometry().contains(event.position().toPoint())

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._on_head(event):
            self.pressed.emit()

    def mouseDoubleClickEvent(self, event) -> None:
        """The x alone: this press may be on the actions of a row that lifted
        after the first, see the module's docstring."""
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x = event.position().toPoint().x()
        self.double_clicked.emit(next(
            (key for key, cell in self.cells.items()
             if cell.geometry().left() <= x <= cell.geometry().right()), None))

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
        reach = 0 if key == "name" else BOX_REACH
        box.setGeometry(self.cells[key].geometry().adjusted(-reach, 0, 0, 0))
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
    # ☆ kept a memo point: its name, the name kept under, the point as it was.
    kept = Signal(str, str, object)

    def __init__(self, session: Session, parent=None, *,
                 actions: bool = False, use: bool = False,
                 star_removable: bool = True):
        super().__init__(parent)
        self.session = session
        self.setObjectName("plain")
        self.rows: list[PointRow] = []
        self.chosen_name: str | None = None
        self._use = use
        self._star_removable = star_removable
        self._used: tuple[str | None, str] = (None, "")
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
        # Between the memo points and the saved points.
        self.divider = QFrame(self.inner)
        self.divider.setFixedHeight(1)
        self.divider.setStyleSheet(f"background: {COLORS['line']};")
        self.divider.hide()
        self.area.setWidget(self.inner)
        column.addWidget(self.area, 1)

        self.actions = self._make_actions() if actions else None
        session.memo_points_changed.connect(self.refresh)
        # The deck modules, and with them what is too low, are the profile's.
        session.profile_changed.connect(lambda _p: self.refresh())
        self.refresh()

    def _captions(self) -> QHBoxLayout:
        line = QHBoxLayout()
        line.setContentsMargins(STAR_WIDTH + 1, 0,
                                ROW_END + 1 + DELETE_WIDTH + CELL_GAP, 0)
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
        column = QVBoxLayout(bar)
        column.setContentsMargins(8, 0, 8, 8)
        column.setSpacing(CELL_GAP)
        row = QHBoxLayout()
        row.setSpacing(CELL_GAP)
        column.addLayout(row)
        self.camera_to = secondary_button("Camera to", bar)
        self.tip_to = secondary_button("Tip to", bar)
        self.z_from_tip = secondary_button("Z from tip", bar)
        for button in (self.camera_to, self.tip_to, self.z_from_tip):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setStyleSheet("padding: 0 2px;")
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Ignored,
                                 QSizePolicy.Policy.Fixed)
            row.addWidget(button, 1)
        self.use_button = None
        if self._use:
            self.use_button = primary_button("Use point", bar)
            self.use_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            column.addWidget(self.use_button)
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
            self._show_used()
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
            self._place_divider()
            self._show_used()
            if self.row(chosen) is None:
                self.chosen_name = None
            self._show_chosen()
            self._place_actions()
        if chosen is not None and self.chosen_name is None:
            self._settle.stop()
            self.chosen.emit(None)

    def _place_divider(self) -> None:
        memo = sum(not row.point.saved for row in self.rows)
        self._lines.removeWidget(self.divider)
        self._lines.insertWidget(memo, self.divider)
        self.divider.setVisible(0 < memo < len(self.rows))

    def _make_row(self, point) -> PointRow:
        row = PointRow(point, self.inner,
                       problem=self.session.memo_problem(point),
                       star_removable=self._star_removable)
        row.pressed.connect(lambda: self._pressed(row.name))
        row.double_clicked.connect(lambda key: self._double_clicked(row, key))
        row.edited.connect(lambda key, value: self._edited(row.name, key,
                                                           value))
        row.star.clicked.connect(lambda: self._star(row.name))
        row.delete.clicked.connect(lambda: self._delete(row.name))
        return row

    def set_used(self, name: str | None, color: str = "") -> None:
        """Mark the point a step goes to, in `color`; None: no point."""
        self._used = (name, color)
        self._show_used()

    def _show_used(self) -> None:
        name, color = self._used
        for row in self.rows:
            row.set_used(color if row.name == name else "")

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
        """Chosen and unfolded at once, but for the chosen row; see the
        module's docstring."""
        interval = QApplication.doubleClickInterval()
        if name == self.chosen_name:
            self._letting_go = True
            self._settle.start(interval)
            return
        self._letting_go = False
        self._settle.stop()
        names = self.names
        lifted = (self.chosen_name in names
                  and names.index(self.chosen_name) < names.index(name))
        self._set_chosen(name)
        with laid_out_at_once(self):
            self._place_actions()
        if self.actions is None:
            return
        self._take_clicks(not lifted)
        if lifted:
            self._inert.start(2 * interval)
        else:
            self._inert.stop()

    def _settled(self) -> None:
        """No double click came: the chosen row clicked is let go."""
        if self._letting_go:
            self._letting_go = False
            self._set_chosen(None)
            with laid_out_at_once(self):
                self._place_actions()

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
        point = self.session.point(name)
        try:
            self.session.save_memo_point(name, new)
        except SessionError as exc:
            self.said.emit(str(exc))
            return
        if chosen:
            self.choose(new.strip())
        self.kept.emit(name, new.strip(), point)
        self.told.emit(f"{new.strip()} kept for later.")

    def _unkeep(self, name: str) -> None:
        if ConfirmDialog.confirm(self, f"Remove {name} from saved points?",
                                 safe="Keep", destructive="Remove"):
            self.session.unkeep_position(name)

    def _delete(self, name: str) -> None:
        point = self.session.point(name)
        if point is None:
            return
        if not point.saved:
            self.session.delete_memo_point(point.name)
        elif ConfirmDialog.confirm(self, f"Delete point {point.name}?",
                                   safe="Keep", destructive="Delete"):
            self.session.delete_saved_point(point.name)
