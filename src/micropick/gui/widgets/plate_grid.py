"""A plate's wells, chosen with the mouse (REDESIGN §3, PlateGrid).

Wells as rounded squares with the row letters and column numbers around
them. A click toggles a well; a click on a row letter or a column number
toggles the whole row or column (all of it chosen already: it is taken
out, else it is all put in); a drag chooses the wells in its box, by the
same rule. Undo, Redo, Select all and Clear are `PlateTools`, a row the
page puts above the grid. The gestures are not explained on screen (§3).

The same widget, `mini=True`, is the small map of a whole plate: no
headers, smaller gaps, filled and planned wells in their colors and the
current one ringed (Picking §6.5, Dispense §6.6). A click on a well there
emits `well_clicked` and chooses nothing.

Geometry comes from the plate's `ordering` - columns, each a list of
well names top to bottom - and the labels from `core.routine.grid_labels`:
no well name is parsed here.

Every plate is drawn in its real proportions (LIQUID R5): a box of the SBS
footprint (`plate_rect`), the same at the same widget size whatever the
plate, and the wells a square pitch filling the footprint's well area, so
a 6-well plate has six big wells and a 96-well plate 96 small ones.

What a well looks like is the page's to say, by name: `set_fills` (a
color per well), `set_counts` (a number inside, shown only when the page
gives one - §12.11: only when more than one cuboid goes per well) and
`set_current` (the white ring). The selection is drawn over those.
`set_dim_fills` is the wells other plan steps use, toned down towards an
empty well so they never read as chosen, and under all of the above.

`planning=True` is the Plan step's grid (#43): a **plan** - the wells
planned, each with its count, drawn in every planned well, "1" included -
and a **selection** apart from it, the wells a count is set for
(`set_count`). A click or a box chooses: wells not planned yet are
planned with `new_count`, and the selection becomes the wells chosen, so
a click on a planned well selects it to change its count; chosen again,
it stays. Ctrl or Shift adds them to the selection, or - all of them
selected already - takes them out of it, never out of the plan. Only
`remove_selected` (PlateTools' Remove, Delete or Backspace on the grid)
takes wells out of the plan. Undo and Redo take back the plan and the
selection together; typing a count over and over is one step.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from ...core.routine import grid_labels
from ..theme import COLORS, mono_font
from ..theme.factory import secondary_button
from ..theme.icons import icon

__all__ = ["PlateGrid", "PlateTools"]

HEADER = 22            # px for the row letters and column numbers
DRAG_SLOP = 4          # px a press travels before it is a box

# The SBS footprint, and the part of it the wells fill: a 6-well plate's span
# (117 × 78 mm); a 96-well plate's, 108 × 72 mm, comes out a little larger.
PLATE_MM = (127.76, 85.48)
WELLS_MM = (116.0, 78.0)
DIM = 0.35             # how much of its color a dim fill keeps
EMPTY_WELL = "#23272E"


def _toned_down(color: QColor) -> QColor:
    """`color` most of the way to an empty well."""
    empty = QColor(EMPTY_WELL)
    return QColor(*(round(a * DIM + b * (1 - DIM)) for a, b in (
        (color.red(), empty.red()), (color.green(), empty.green()),
        (color.blue(), empty.blue()))))


def dim_color(color: str) -> str:
    """`color` as `set_dim_fills` draws it, for a legend."""
    return _toned_down(QColor(color)).name()


class PlateGrid(QWidget):
    selection_changed = Signal(object)    # set of well names
    history_changed = Signal()            # can_undo / can_redo may differ
    well_clicked = Signal(str)            # mini: the well clicked
    hovered = Signal(object)              # a name, or None off the wells

    def __init__(self, *, mini: bool = False, planning: bool = False,
                 read_only: bool = False, parent: QWidget | None = None):
        """`read_only`: a click chooses nothing; `hovered` still says."""
        super().__init__(parent)
        self.mini = mini
        self.planning = planning
        self.read_only = read_only
        # The count a well newly planned takes.
        self.new_count = 1
        self.setMouseTracking(True)
        # A click gives the grid Delete and Backspace.
        if planning:
            self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._columns: list[list[str]] = []
        self._row_labels: list[str] = []
        self._column_labels: list[str] = []
        self._at: dict[str, tuple[int, int]] = {}
        self._selected: set[str] = set()
        self._plan: dict[str, int] = {}
        self._fills: dict[str, QColor] = {}
        self._dim_fills: dict[str, QColor] = {}
        self._counts: dict[str, int] = {}
        self._current: str | None = None
        self._hover: str | None = None
        self._press: QPointF | None = None
        self._band: QRectF | None = None
        # (plan, selection) as they were; the plan is {} unless planning.
        self._undo: list[tuple[dict[str, int], set[str]]] = []
        self._redo: list[tuple[dict[str, int], set[str]]] = []
        # The selection a count was last typed for: the next count typed
        # for it is the same undo step.
        self._counting: set[str] | None = None
        self.selection_color = QColor(COLORS["plate"])

    # -- the plate -------------------------------------------------------

    def set_ordering(self, ordering) -> None:
        """A new plate: columns of well names. Clears the selection and
        its history."""
        self._columns = [list(c) for c in ordering or []]
        self._at = {name: (c, r) for c, wells in enumerate(self._columns)
                    for r, name in enumerate(wells)}
        if self._columns:
            rows, columns = grid_labels(self._columns)
            self._row_labels = [str(r) for r in rows]
            self._column_labels = [str(c) for c in columns]
        else:
            self._row_labels, self._column_labels = [], []
        self._selected = set()
        self._plan = {}
        self._undo, self._redo = [], []
        self._counting = None
        self._current = None
        self.history_changed.emit()
        self.update()

    @property
    def wells(self) -> list[str]:
        return list(self._at)

    # -- what the page draws ----------------------------------------------

    def set_fills(self, fills: dict) -> None:
        self._fills = {n: QColor(c) for n, c in (fills or {}).items()}
        self.update()

    def set_dim_fills(self, fills: dict) -> None:
        """A color per well used elsewhere; see the module docstring."""
        self._dim_fills = {n: _toned_down(QColor(c))
                           for n, c in (fills or {}).items()}
        self.update()

    def set_counts(self, counts: dict) -> None:
        self._counts = dict(counts or {})
        self.update()

    def set_current(self, name: str | None) -> None:
        self._current = name
        self.update()

    # -- selection and its history ----------------------------------------

    @property
    def selection(self) -> set[str]:
        return set(self._selected)

    @property
    def plan(self) -> dict[str, int]:
        """Planning: the wells planned, each with its count."""
        return dict(self._plan)

    def set_selection(self, names, *, record: bool = True) -> None:
        """The wells chosen. Planning: they are chosen as a box chooses
        them, without taking any out."""
        wanted = {n for n in (names or ()) if n in self._at}
        if self.planning:
            plan = dict(self._plan)
            for name in wanted:
                plan.setdefault(name, self.new_count)
            self._change(plan, wanted, record=record)
        else:
            self._change({}, wanted, record=record)

    def set_plan(self, plan: dict, *, record: bool = True) -> None:
        """Planning: `plan` on the grid, nothing selected."""
        self._change({n: int(c) for n, c in (plan or {}).items()
                      if n in self._at}, set(), record=record)

    def set_count(self, count: int) -> None:
        """Planning: `count` for the wells selected, and for the next well
        planned."""
        self.new_count = count
        if not self._selected:
            return
        plan = dict(self._plan)
        for name in self._selected:
            plan[name] = count
        if plan == self._plan:
            return
        counting = set(self._selected)
        self._change(plan, counting, record=self._counting != counting)
        self._counting = counting

    def choose(self, names, *, add: bool = False) -> None:
        """A click or a box on `names` (see the module docstring); not
        planning, `toggle`."""
        if not self.planning:
            self.toggle(names)
            return
        names = {n for n in names if n in self._at}
        if not names:
            return
        if add and names <= self._selected:
            self._change(dict(self._plan), self._selected - names)
            return
        plan = dict(self._plan)
        for name in names:
            plan.setdefault(name, self.new_count)
        self._change(plan, (self._selected if add else set()) | names)

    def remove_selected(self) -> None:
        """Planning: the wells selected out of the plan."""
        if not self.planning or not self._selected:
            return
        self._change({w: c for w, c in self._plan.items()
                      if w not in self._selected}, set())

    def toggle(self, names) -> None:
        """All of `names` chosen: take them out. Else: put them all in."""
        names = {n for n in names if n in self._at}
        if not names:
            return
        if names <= self._selected:
            self.set_selection(self._selected - names)
        else:
            self.set_selection(self._selected | names)

    def select_all(self) -> None:
        self.set_selection(self._at)

    def clear(self) -> None:
        self._change({}, set())

    def _change(self, plan: dict[str, int], selection: set[str], *,
                record: bool = True) -> None:
        if plan == self._plan and selection == self._selected:
            return
        if record:
            self._undo.append((dict(self._plan), set(self._selected)))
            self._redo.clear()
        self._counting = None
        self._apply_history((plan, selection))

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> None:
        if self._undo:
            self._redo.append((dict(self._plan), set(self._selected)))
            self._counting = None
            self._apply_history(self._undo.pop())

    def redo(self) -> None:
        if self._redo:
            self._undo.append((dict(self._plan), set(self._selected)))
            self._counting = None
            self._apply_history(self._redo.pop())

    def _apply_history(self, state) -> None:
        plan, names = state
        self._plan, self._selected = dict(plan), set(names)
        self.update()
        self.selection_changed.emit(set(names))
        self.history_changed.emit()

    # -- geometry ----------------------------------------------------------

    def _shape(self) -> tuple[int, int]:
        return (max(1, len(self._columns)),
                max(1, max((len(c) for c in self._columns), default=1)))

    def plate_rect(self) -> QRectF:
        """The plate's outer box, the same for every plate at one size."""
        head = 0 if self.mini else HEADER
        room_w, room_h = self.width() - head, self.height() - head
        scale = min(room_w / PLATE_MM[0], room_h / PLATE_MM[1])
        width, height = PLATE_MM[0] * scale, PLATE_MM[1] * scale
        return QRectF(head + (room_w - width) / 2,
                      head + (room_h - height) / 2, width, height)

    def _layout(self) -> tuple[float, float, float]:
        """Cell size and the top left of well A1's cell."""
        cols, rows = self._shape()
        box = self.plate_rect()
        scale = box.width() / PLATE_MM[0]
        cell = max(2.0, min(WELLS_MM[0] / cols, WELLS_MM[1] / rows) * scale)
        return (cell, box.center().x() - cell * cols / 2,
                box.center().y() - cell * rows / 2)

    def well_rect(self, name: str) -> QRectF:
        cell, left, top = self._layout()
        c, r = self._at[name]
        gap = max(1.0, cell * (0.14 if not self.mini else 0.18))
        return QRectF(left + c * cell + gap / 2, top + r * cell + gap / 2,
                      cell - gap, cell - gap)

    def _hit(self, pos: QPointF):
        """("well", name), ("row", i), ("column", i) or None."""
        cell, left, top = self._layout()
        c = int((pos.x() - left) // cell)
        r = int((pos.y() - top) // cell)
        if 0 <= c < len(self._columns) and 0 <= r < len(self._columns[c]):
            return ("well", self._columns[c][r])
        if self.mini:
            return None
        if pos.y() < top and 0 <= c < len(self._columns):
            return ("column", c)
        if pos.x() < left and 0 <= r < len(self._row_labels):
            return ("row", r)
        return None

    def sizeHint(self) -> QSize:
        width = 120 if self.mini else 384
        head = 0 if self.mini else HEADER
        return QSize(head + width, self.heightForWidth(head + width))

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        head = 0 if self.mini else HEADER
        return round(head + (width - head) * PLATE_MM[1] / PLATE_MM[0])

    # -- mouse -------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self.read_only:
            self._press = event.position()
            self._band = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        if self._press is not None and not self.mini:
            if (self._band is not None
                    or (pos - self._press).manhattanLength() > DRAG_SLOP):
                self._band = QRectF(self._press, pos).normalized()
                self.update()
            return
        hit = self._hit(pos)
        name = hit[1] if hit and hit[0] == "well" else None
        if name != self._hover:
            self._hover = name
            self.setToolTip(name or "")
            self.hovered.emit(name)
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._press is None:
            return
        if self._band is not None:
            inside = [n for n in self._at
                      if self._band.contains(self.well_rect(n).center())]
            self._band = None
            self.choose(inside, add=self._adding(event))
        else:
            hit = self._hit(event.position())
            if hit is not None:
                kind, what = hit
                if self.mini:
                    if kind == "well":
                        self.well_clicked.emit(what)
                elif kind == "well":
                    self.choose([what], add=self._adding(event))
                elif kind == "column":
                    self.choose(self._columns[what], add=self._adding(event))
                else:
                    self.choose([c[what] for c in self._columns
                                 if what < len(c)], add=self._adding(event))
        self._press = None
        self.update()

    @staticmethod
    def _adding(event) -> bool:
        return bool(event.modifiers() & (Qt.KeyboardModifier.ControlModifier
                                         | Qt.KeyboardModifier.ShiftModifier))

    def keyPressEvent(self, event) -> None:
        if (self.planning and event.key() in (Qt.Key.Key_Delete,
                                              Qt.Key.Key_Backspace)):
            self.remove_selected()
            return
        super().keyPressEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hover is not None:
            self._hover = None
            self.hovered.emit(None)
            self.update()
        super().leaveEvent(event)

    # -- paint -------------------------------------------------------------

    def paintEvent(self, event) -> None:
        if not self._columns:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cell, left, top = self._layout()
        empty = QColor(EMPTY_WELL)
        ring = QColor(COLORS["text"])
        hover_c, hover_r = self._at.get(self._hover, (-1, -1))
        box = self.plate_rect()

        painter.setPen(QPen(QColor(COLORS["card-border"]), 1))
        painter.setBrush(QColor(COLORS["card"]))
        painter.drawRoundedRect(box, box.width() * 0.02, box.width() * 0.02)

        if not self.mini:
            painter.setFont(mono_font(10, 500))
            for c, text in enumerate(self._column_labels):
                hot = c == hover_c
                painter.setPen(QColor(COLORS["text"] if hot
                                      else COLORS["muted"]))
                painter.drawText(QRectF(left + c * cell, box.top() - HEADER,
                                        cell, HEADER),
                                 Qt.AlignmentFlag.AlignCenter, text)
            for r, text in enumerate(self._row_labels):
                hot = r == hover_r
                painter.setPen(QColor(COLORS["text"] if hot
                                      else COLORS["muted"]))
                painter.drawText(QRectF(box.left() - HEADER, top + r * cell,
                                        HEADER, cell),
                                 Qt.AlignmentFlag.AlignCenter, text)

        radius = max(1.5, cell * 0.12)
        number_font = mono_font(max(7, int(cell * 0.38)), 600)
        for name in self._at:
            rect = self.well_rect(name)
            fill = self._fills.get(name)
            if self.planning and name in self._plan:
                fill = self.selection_color
            selected = name in self._selected
            dim = self._dim_fills.get(name)
            color = (self.selection_color if selected and not self.mini
                     else fill if fill is not None
                     else dim if dim is not None else empty)
            if (name == self._hover and not selected and fill is None
                    and dim is None):
                color = QColor("#2C313A")
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(rect, radius, radius)
            if name == self._current:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(ring, max(1.5, cell * 0.08)))
                painter.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), radius,
                                        radius)
            if self.planning and selected:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(ring, max(2.0, cell * 0.1)))
                painter.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), radius,
                                        radius)
            count = (self._plan.get(name) if self.planning
                     else self._counts.get(name))
            if count and cell >= 14:
                painter.setFont(number_font)
                painter.setPen(QColor(COLORS["on-accent"] if selected or fill
                                      is not None else COLORS["text-2"]))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                                 str(count))

        if self._band is not None:
            painter.setPen(QPen(QColor(255, 255, 255, 180), 1,
                                Qt.PenStyle.DashLine))
            painter.setBrush(QColor(255, 255, 255, 24))
            painter.drawRect(self._band)
        painter.end()


class PlateTools(QWidget):
    """Undo, Redo, Select all, Clear - for one grid; Remove (the wells
    selected out of the plan) for a planning one."""

    def __init__(self, grid: PlateGrid, parent: QWidget | None = None):
        super().__init__(parent)
        self.grid = grid
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.undo = self._button("Undo", "undo", grid.undo)
        self.redo = self._button("Redo", "redo", grid.redo)
        self.select_all = self._button("Select all", None, grid.select_all)
        self.remove = self._button("Remove", None, grid.remove_selected)
        self.remove.setToolTip("Delete")
        self.remove.setVisible(grid.planning)
        self.clear = self._button("Clear", None, grid.clear)
        for button in (self.undo, self.redo, self.select_all, self.remove,
                       self.clear):
            row.addWidget(button)
        grid.history_changed.connect(self._refresh)
        grid.selection_changed.connect(self._refresh)
        self._refresh()

    def _button(self, text: str, name: str | None, slot) -> QPushButton:
        button = secondary_button(text, self)
        if name:
            button.setIcon(icon(name, COLORS["text-2"], 16))
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(slot)
        return button

    def _refresh(self, *_args) -> None:
        self.undo.setEnabled(self.grid.can_undo())
        self.redo.setEnabled(self.grid.can_redo())
        self.remove.setEnabled(bool(self.grid.selection))
        self.clear.setEnabled(bool(self.grid.selection or self.grid.plan))
