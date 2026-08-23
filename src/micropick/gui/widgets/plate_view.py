"""The plate, drawn from its own definition.

Geometry comes from `Destination.ordering` and nothing else: it is a list of
columns, each a list of well names top to bottom, so the well at row `r` of
column `c` is `ordering[c][r]` and the grid needs no arithmetic on names.

**No name is parsed here.** `int(well[1:])` is how the old code found a column,
and it broke the moment a plate had a two-letter row — which a 1536 does, all
the way to AF. The label on a well is the well's own name, and the position is
where the definition put it. Nothing else can drift.

Colour says two things at once, because the operator is asking two questions of
the same picture: an outline means this well is in the plan, and the fill is how
much of its plan has arrived. A well not in the plan is drawn faintly and is
still clickable, since choosing the plan is what the clicking is for.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (QGraphicsEllipseItem, QGraphicsScene,
                               QGraphicsSimpleTextItem, QGraphicsView)

__all__ = ["PlateView"]

# One accent, as on the calibration plots. A plate map is its own surface, like
# the camera viewport, so the ink is fixed rather than taken from the palette.
ACCENT = QColor(94, 158, 235)
PLANNED_EDGE = QColor(94, 158, 235)
IDLE_EDGE = QColor(120, 122, 130)
LABEL = QColor(210, 212, 218)
BACKDROP = QColor(24, 24, 26)

WELL_PITCH = 20.0
WELL_RADIUS = 8.0

# A name inside a well needs room. Below this the wells are dots and the name
# lives in the tooltip, which is the only place it fits on a 1536.
LABEL_MIN_WELLS = 400


class _Well(QGraphicsEllipseItem):
    def __init__(self, name: str, rect: QRectF):
        super().__init__(rect)
        self.name = name
        self.setAcceptHoverEvents(True)
        self.setToolTip(name)


class PlateView(QGraphicsView):
    """A clickable well grid. Owns no routine and changes no state."""

    well_clicked = Signal(str)

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
        self._plan: dict[str, int] = {}
        self._delivered: dict[str, int] = {}
        self._destination = None

    # -- building ------------------------------------------------------------

    def set_destination(self, destination) -> None:
        scene = self.scene()
        scene.clear()
        self._wells.clear()
        self._destination = destination
        if destination is None or not destination.is_plate:
            self.setSceneRect(QRectF())
            return

        ordering = destination.ordering
        show_names = len(destination.targets) <= LABEL_MIN_WELLS
        for column, wells in enumerate(ordering):
            for row, name in enumerate(wells):
                rect = QRectF(column * WELL_PITCH - WELL_RADIUS,
                              row * WELL_PITCH - WELL_RADIUS,
                              2 * WELL_RADIUS, 2 * WELL_RADIUS)
                well = _Well(name, rect)
                scene.addItem(well)
                self._wells[name] = well
                if show_names:
                    label = QGraphicsSimpleTextItem(name, well)
                    label.setBrush(QBrush(LABEL))
                    font = label.font()
                    font.setPointSizeF(5.0)
                    label.setFont(font)
                    bounds = label.boundingRect()
                    label.setPos(rect.center().x() - bounds.width() / 2,
                                 rect.center().y() - bounds.height() / 2)
                    # The label must not swallow the click meant for the well.
                    label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

        scene.setSceneRect(scene.itemsBoundingRect().adjusted(-8, -8, 8, 8))
        self._repaint_wells()
        self._fit()

    # -- state ---------------------------------------------------------------

    def set_plan(self, plan: dict[str, int]) -> None:
        self._plan = dict(plan or {})
        self._repaint_wells()

    def set_progress(self, delivered: dict[str, int]) -> None:
        self._delivered = dict(delivered or {})
        self._repaint_wells()

    def _repaint_wells(self) -> None:
        for name, well in self._wells.items():
            planned = self._plan.get(name, 0)
            done = self._delivered.get(name, 0)
            if planned <= 0:
                well.setPen(QPen(IDLE_EDGE, 1))
                well.setBrush(Qt.BrushStyle.NoBrush)
                well.setToolTip(name)
                continue
            well.setPen(QPen(PLANNED_EDGE, 1.5))
            fill = QColor(ACCENT)
            # Alpha as the fraction delivered, so an empty planned well is an
            # outline and a finished one is solid, with everything between
            # readable at a glance across the whole plate.
            fill.setAlpha(int(255 * min(1.0, done / planned)) if planned else 0)
            well.setBrush(QBrush(fill))
            well.setToolTip(f"{name}: {done} of {planned}")

    # -- interaction ---------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        item = self.itemAt(event.position().toPoint())
        while item is not None and not isinstance(item, _Well):
            item = item.parentItem()
        if item is not None:
            self.well_clicked.emit(item.name)
        super().mousePressEvent(event)

    # -- geometry ------------------------------------------------------------

    def _fit(self) -> None:
        if self.scene().sceneRect().isValid():
            self.fitInView(self.scene().sceneRect(),
                           Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()
