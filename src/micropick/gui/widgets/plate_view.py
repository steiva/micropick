"""The plate, drawn from its own definition, planned with the mouse.

Geometry comes from `Destination.ordering` and nothing else: it is a list of
columns, each a list of well names top to bottom, so the well at row `r` of
column `c` is `ordering[c][r]` and the grid needs no arithmetic on names.

**No name is parsed here.** `int(well[1:])` is how the old code found a
column, and it broke the moment a plate had a two-letter row — which a 1536
does, all the way to AF. The row and column labels come from
`core.routine.grid_labels`, the same parser the planning table uses, so the
header an operator clicks and the well the plan records cannot disagree.

Selecting and planning are two things
-------------------------------------
They were one: a click toggled a well into the plan with whatever count the
spin box held, so changing a count meant clicking every well twice and
"select these twelve and put three in each" was not expressible at all.

Now the mouse only ever changes the **selection** — the wells being talked
about — and the count is applied to the selection by the page. A click
replaces it, Ctrl adds, Shift removes; a drag does the same over a box; and
a click on a row letter or a column number takes the whole row or column,
with the same three modifiers. The plan is what has a count, and it is
drawn in the well.

What the drawing says
---------------------
Three things at once, because the operator is asking three questions of one
picture. A faint outline is a well that is not planned; an accent outline is
one that is, with the number of objects it wants written inside it; and the
fill is how much of that has arrived. Over all of them, a white ring is what
is selected — white being a decision rather than a class of thing, as in
`viz.overlays`. At 1536 wells there is no room for a number, so above
`LABEL_MIN_WELLS` the wells are dots and the tooltip carries the detail.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QGraphicsEllipseItem, QGraphicsRectItem,
                               QGraphicsScene, QGraphicsSimpleTextItem,
                               QGraphicsView)

from ...core.routine import grid_labels

__all__ = ["PlateView"]

# One accent, as on the calibration plots. A plate map is its own surface, like
# the camera viewport, so the ink is fixed rather than taken from the palette.
ACCENT = QColor(94, 158, 235)
PLANNED_EDGE = QColor(94, 158, 235)
IDLE_EDGE = QColor(120, 122, 130)
SELECTED = QColor(255, 255, 255)
LABEL = QColor(210, 212, 218)
HEADER = QColor(170, 172, 180)
HEADER_HOT = QColor(255, 255, 255)
BACKDROP = QColor(24, 24, 26)
BAND_EDGE = QColor(255, 255, 255, 180)
BAND_FILL = QColor(255, 255, 255, 30)

WELL_PITCH = 20.0
WELL_RADIUS = 8.0
# Where the headers sit, in the same scene units as the wells.
HEADER_GAP = 18.0

# A name inside a well needs room. Below this the wells are dots and the name
# lives in the tooltip, which is the only place it fits on a 1536.
LABEL_MIN_WELLS = 400

# A press that travels less than this is a click, not a box. In scene units,
# so it is the same gesture whatever the zoom.
DRAG_SLOP = 6.0


class _Well(QGraphicsEllipseItem):
    def __init__(self, name: str, rect: QRectF):
        super().__init__(rect)
        self.name = name
        self.setAcceptHoverEvents(True)
        self.setToolTip(name)


class _Header(QGraphicsRectItem):
    """A row letter or a column number, and the wells it stands for."""

    def __init__(self, rect: QRectF, wells: list[str]):
        super().__init__(rect)
        self.wells = wells
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setBrush(Qt.BrushStyle.NoBrush)
        self.setAcceptHoverEvents(True)


class PlateView(QGraphicsView):
    """A plate that can be selected on. Owns no routine and changes no plan."""

    # The set of well names now selected. One signal for every way of
    # selecting, so the page has one thing to listen to.
    selection_changed = Signal(object)
    # A double click, for "this one well, now".
    well_activated = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setBackgroundBrush(QBrush(BACKDROP))
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        # The plate is the thing being looked at; nothing here scrolls.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._wells: dict[str, _Well] = {}
        self._labels: dict[str, QGraphicsSimpleTextItem] = {}
        self._plan: dict[str, int] = {}
        self._delivered: dict[str, int] = {}
        self._selected: set[str] = set()
        self._destination = None
        self._show_names = False

        self._press_scene: QPointF | None = None
        self._press_mods = Qt.KeyboardModifier.NoModifier
        self._band: QGraphicsRectItem | None = None

    # -- building ------------------------------------------------------------

    def set_destination(self, destination) -> None:
        scene = self.scene()
        scene.clear()
        self._wells.clear()
        self._labels.clear()
        self._selected.clear()
        self._band = None
        self._destination = destination
        if destination is None or not destination.is_plate:
            self.setSceneRect(QRectF())
            self.selection_changed.emit(set())
            return

        ordering = destination.ordering
        rows, columns = grid_labels(ordering)
        self._show_names = len(destination.targets) <= LABEL_MIN_WELLS

        font = QFont()
        font.setPointSizeF(5.0)
        header_font = QFont()
        header_font.setPointSizeF(6.0)
        header_font.setBold(True)

        for column, wells in enumerate(ordering):
            for row, name in enumerate(wells):
                rect = QRectF(column * WELL_PITCH - WELL_RADIUS,
                              row * WELL_PITCH - WELL_RADIUS,
                              2 * WELL_RADIUS, 2 * WELL_RADIUS)
                well = _Well(name, rect)
                scene.addItem(well)
                self._wells[name] = well
                if self._show_names:
                    label = QGraphicsSimpleTextItem("", well)
                    label.setFont(font)
                    # The label must not swallow the click meant for the well.
                    label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                    self._labels[name] = label

        # Headers last, so they sit over nothing and hit-test first.
        for column, wells in enumerate(ordering):
            x = column * WELL_PITCH
            self._add_header(str(columns[column]), header_font,
                             QRectF(x - WELL_PITCH / 2, -HEADER_GAP - WELL_PITCH / 2,
                                    WELL_PITCH, WELL_PITCH),
                             list(wells))
        for row in range(len(ordering[0]) if ordering else 0):
            y = row * WELL_PITCH
            column_wells = [ordering[c][row] for c in range(len(ordering))
                            if row < len(ordering[c])]
            self._add_header(str(rows[row]), header_font,
                             QRectF(-HEADER_GAP - WELL_PITCH / 2, y - WELL_PITCH / 2,
                                    WELL_PITCH, WELL_PITCH),
                             column_wells)

        scene.setSceneRect(scene.itemsBoundingRect().adjusted(-8, -8, 8, 8))
        self._repaint_wells()
        self._fit()
        self.selection_changed.emit(set())

    def _add_header(self, text: str, font: QFont, rect: QRectF,
                    wells: list[str]) -> None:
        header = _Header(rect, wells)
        header.setToolTip(f"{text}: click to select, Ctrl to add, "
                          f"Shift to remove")
        self.scene().addItem(header)
        label = QGraphicsSimpleTextItem(text, header)
        label.setFont(font)
        label.setBrush(QBrush(HEADER))
        bounds = label.boundingRect()
        label.setPos(rect.center().x() - bounds.width() / 2,
                     rect.center().y() - bounds.height() / 2)
        label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    # -- state ---------------------------------------------------------------

    def set_plan(self, plan: dict[str, int]) -> None:
        self._plan = dict(plan or {})
        self._repaint_wells()

    def set_progress(self, delivered: dict[str, int]) -> None:
        self._delivered = dict(delivered or {})
        self._repaint_wells()

    @property
    def selection(self) -> set[str]:
        return set(self._selected)

    def set_selection(self, names) -> None:
        """Replace the selection. Emits, so the page can follow."""
        wanted = {n for n in (names or ()) if n in self._wells}
        if wanted == self._selected:
            return
        self._selected = wanted
        self._repaint_wells()
        self.selection_changed.emit(set(self._selected))

    def select_all(self) -> None:
        self.set_selection(self._wells)

    def clear_selection(self) -> None:
        self.set_selection(())

    def _apply(self, names, mods) -> None:
        """Replace, add or remove, by modifier. One rule for clicks, boxes
        and headers, so the three cannot drift apart."""
        names = {n for n in names if n in self._wells}
        if mods & Qt.KeyboardModifier.ControlModifier:
            self.set_selection(self._selected | names)
        elif mods & Qt.KeyboardModifier.ShiftModifier:
            self.set_selection(self._selected - names)
        else:
            self.set_selection(names)

    def _repaint_wells(self) -> None:
        for name, well in self._wells.items():
            planned = self._plan.get(name, 0)
            done = self._delivered.get(name, 0)
            selected = name in self._selected

            if planned > 0:
                fill = QColor(ACCENT)
                # Alpha as the fraction delivered, so an empty planned well is
                # an outline and a finished one is solid, readable at a glance
                # across the whole plate.
                fill.setAlpha(int(255 * min(1.0, done / planned)))
                well.setBrush(QBrush(fill))
                edge = PLANNED_EDGE
                well.setToolTip(f"{name}: {done} of {planned}")
            else:
                well.setBrush(Qt.BrushStyle.NoBrush)
                edge = IDLE_EDGE
                well.setToolTip(name)
            if selected:
                well.setPen(QPen(SELECTED, 2.5))
            else:
                well.setPen(QPen(edge, 1.5 if planned else 1))

            label = self._labels.get(name)
            if label is not None:
                # The count is what the plan is about; the name is what a
                # well is called, and only matters while nothing is planned.
                label.setText(str(planned) if planned else name)
                label.setBrush(QBrush(SELECTED if selected else LABEL))
                bounds = label.boundingRect()
                rect = well.rect()
                label.setPos(rect.center().x() - bounds.width() / 2,
                             rect.center().y() - bounds.height() / 2)

    # -- interaction ---------------------------------------------------------

    def _item_at(self, position, kind):
        item = self.itemAt(position)
        while item is not None and not isinstance(item, kind):
            item = item.parentItem()
        return item

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._press_scene = self.mapToScene(event.position().toPoint())
        self._press_mods = event.modifiers()
        header = self._item_at(event.position().toPoint(), _Header)
        if header is not None:
            self._apply(header.wells, event.modifiers())
            self._press_scene = None
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._press_scene is None:
            super().mouseMoveEvent(event)
            return
        here = self.mapToScene(event.position().toPoint())
        rect = QRectF(self._press_scene, here).normalized()
        if (rect.width() < DRAG_SLOP and rect.height() < DRAG_SLOP
                and self._band is None):
            return
        if self._band is None:
            self._band = QGraphicsRectItem()
            self._band.setPen(QPen(BAND_EDGE, 1, Qt.PenStyle.DashLine))
            self._band.setBrush(QBrush(BAND_FILL))
            self._band.setZValue(10)
            self.scene().addItem(self._band)
        self._band.setRect(rect)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._press_scene is None:
            super().mouseReleaseEvent(event)
            return
        if self._band is not None:
            rect = self._band.rect()
            self.scene().removeItem(self._band)
            self._band = None
            inside = [name for name, well in self._wells.items()
                      if rect.contains(well.sceneBoundingRect().center())]
            self._apply(inside, self._press_mods)
        else:
            well = self._item_at(event.position().toPoint(), _Well)
            if well is not None:
                if self._press_mods & Qt.KeyboardModifier.ControlModifier:
                    # Ctrl on a single well toggles: adding one at a time is
                    # what Ctrl means everywhere, and removing it again is
                    # the same gesture rather than a different modifier.
                    names = (self._selected - {well.name}
                             if well.name in self._selected
                             else self._selected | {well.name})
                    self.set_selection(names)
                else:
                    self._apply([well.name], self._press_mods)
            elif not (self._press_mods & (Qt.KeyboardModifier.ControlModifier
                                          | Qt.KeyboardModifier.ShiftModifier)):
                self.clear_selection()
        self._press_scene = None
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        well = self._item_at(event.position().toPoint(), _Well)
        if well is not None:
            self.well_activated.emit(well.name)
        super().mouseDoubleClickEvent(event)

    # -- geometry ------------------------------------------------------------

    def _fit(self) -> None:
        if self.scene().sceneRect().isValid():
            self.fitInView(self.scene().sceneRect(),
                           Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()
