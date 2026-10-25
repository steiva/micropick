"""The cuboids of one analysed frame, in figures: a window of its own.

Opened and closed by the chart button on the size histogram over the
picture (`CameraView.enable_stats_button`). It holds what the picking
filters judge each detection by, every measure against the window the
picking settings give it, so a population cut by one filter shows as bars
outside that one band:

- the counts: boxes from the detector, contours, pickable, isolated,
  bubbles, and the rejections by reason (`cuboids.label_rejections`);
- a histogram per measure - diameter, circularity, aspect ratio, solidity,
  radial CV, nearest neighbour, detector confidence, distance from the
  dish center - bars inside the window green;
- a table: the window, median, 10-90 % range, range, and how many fall
  inside.

The diameter is `SizeHistogram`, the same bins the picture shows. The
measures that need millimetres (diameter, nearest neighbour) are missing
without a pixel map, and say so.
"""

from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (QAbstractItemView, QGridLayout, QHeaderView,
                               QLabel, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..theme import SPACING
from .size_histogram import (INSIDE, INSIDE_ALPHA, OUTSIDE_ALPHA, WINDOW_EDGE,
                             SizeHistogram)

__all__ = ["CuboidStats", "MEASURES"]

BINS = 24

# (column, name, unit, the window from the picking settings as (low, high),
# None for an open side). The diameter is drawn by SizeHistogram.
MEASURES = (
    ("diameter_microns", "Diameter", "µm",
     lambda c: tuple(c.cuboid_size_threshold)),
    ("circularity", "Circularity", "", lambda c: tuple(c.circularity_window)),
    ("aspect_ratio", "Aspect ratio", "", lambda c: tuple(c.aspect_ratio_window)),
    ("solidity", "Solidity", "", lambda c: (c.min_solidity, None)),
    ("radial_cv", "Radial CV", "", lambda c: (None, c.max_radial_cv)),
    ("min_dist_mm", "Nearest neighbor", "mm",
     lambda c: (c.minimum_distance, None)),
    ("conf", "Detector confidence", "", lambda c: (c.yolo_conf, None)),
    ("distance_to_center", "Distance from dish center", "px",
     lambda c: (None, c.circle_radius)),
)

REASONS = ("shape", "bubble", "floater", "crowded")


def _window_text(low, high, unit: str) -> str:
    unit = f" {unit}" if unit else ""
    if low is not None and high is not None:
        return f"{low:g}–{high:g}{unit}"
    if low is not None:
        return f"≥ {low:g}{unit}"
    if high is not None:
        return f"≤ {high:g}{unit}"
    return "—"


def _inside(values: np.ndarray, low, high) -> np.ndarray:
    lo = -math.inf if low is None else float(low)
    hi = math.inf if high is None else float(high)
    return (values >= lo) & (values <= hi)


def _plot(title: str, unit: str) -> pg.PlotWidget:
    """A histogram readout: no mouse, no menu, axes set from the data."""
    plot = pg.PlotWidget(background=None)
    ink = plot.palette().color(QPalette.ColorRole.Text)
    for axis in ("left", "bottom"):
        plot.getAxis(axis).setPen(ink)
        plot.getAxis(axis).setTextPen(ink)
    view = plot.getPlotItem().getViewBox()
    view.setMouseEnabled(x=False, y=False)
    view.setMenuEnabled(False)
    view.wheelEvent = lambda event, axis=None: event.ignore()
    plot.getPlotItem().hideButtons()
    plot.setTitle(title, color=ink, size="10pt")
    plot.setLabel("bottom", unit)
    plot.showGrid(x=True, y=True, alpha=0.2)
    plot.setMinimumHeight(190)
    return plot


class CuboidStats(QWidget):
    """A tool window over the main one. `show_detection` fills it; `closed`
    says it was closed by its own title bar."""

    closed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Tool)
        self.setWindowTitle("Cuboid statistics")
        self.resize(1280, 820)

        self.counts = QLabel("Analyze the dish to fill this window.")
        self.counts.setWordWrap(True)
        self.counts.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)

        self.sizes = SizeHistogram("Not analyzed yet.", self)
        self.sizes.plot.setTitle(
            "Diameter", color=self.palette().color(QPalette.ColorRole.Text),
            size="10pt")
        self.sizes.plot.setMaximumHeight(16777215)
        self.plots: dict[str, pg.PlotWidget] = {}
        grid = QGridLayout()
        grid.setSpacing(SPACING)
        for i, (column, name, unit, _window) in enumerate(MEASURES):
            widget = (self.sizes if column == "diameter_microns"
                      else _plot(name, unit))
            if column != "diameter_microns":
                self.plots[column] = widget
            grid.addWidget(widget, i // 4, i % 4)

        self.table = QTableWidget(len(MEASURES), 6, self)
        self.table.setHorizontalHeaderLabels(
            ["Measure", "Window", "Median", "10–90 %", "Range", "In window"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.table.setFixedHeight(
            self.table.horizontalHeader().sizeHint().height()
            + len(MEASURES) * self.table.verticalHeader().defaultSectionSize()
            + 4)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2,
                                  SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addWidget(self.counts)
        layout.addLayout(grid, 1)
        layout.addWidget(self.table)

    # -- filling -------------------------------------------------------------

    def show_detection(self, detection, cfg) -> None:
        """Everything for `detection` against `cfg` (PickingConfig); None
        for either clears it."""
        window = tuple(cfg.cuboid_size_threshold) if cfg is not None else None
        self.sizes.show_detection(detection, window)
        if detection is None or cfg is None:
            self.counts.setText("Analyze the dish to fill this window.")
            for plot in self.plots.values():
                plot.clear()
            self.table.clearContents()
            return
        self.counts.setText(self._counts(detection))
        df = detection.df
        for row, (column, name, unit, window_of) in enumerate(MEASURES):
            low, high = window_of(cfg)
            values = (df[column].to_numpy(dtype=float) if column in df
                      else np.empty(0))
            values = values[np.isfinite(values)]
            if column != "diameter_microns":
                self._draw(self.plots[column], values, low, high)
            self._row(row, name, unit, values, low, high,
                      missing=column not in df)

    def _counts(self, detection) -> str:
        df = detection.df
        parts = [f"{len(detection.boxes)} boxes from the detector",
                 f"{len(df)} with a contour"]
        if detection.classified:
            parts += [f"{len(detection.pickable)} pickable",
                      f"{len(detection.isolated)} isolated",
                      f"{len(detection.bubbles)} bubbles"]
        text = " · ".join(parts)
        counts = detection.counts or {}
        rejected = [f"{reason} {counts[reason]}" for reason in REASONS
                    if reason in counts]
        if rejected:
            text += "\nRejected: " + ", ".join(rejected)
        if detection.notes:
            text += "\n" + " ".join(detection.notes)
        return text

    def _draw(self, plot: pg.PlotWidget, values: np.ndarray, low,
              high) -> None:
        plot.clear()
        if len(values) == 0:
            return
        span = float(values.max() - values.min())
        if span <= 0:
            center = float(values[0])
            pad = max(abs(center) * 0.1, 1e-3)
            edges = np.linspace(center - pad, center + pad, BINS + 1)
        else:
            edges = np.histogram_bin_edges(values, bins=BINS)
        counts, edges = np.histogram(values, bins=edges)
        centers = (edges[:-1] + edges[1:]) / 2
        width = float(edges[1] - edges[0])
        inside = _inside(centers, low, high)
        ink = plot.palette().color(QPalette.ColorRole.Text)
        outside = (ink.red(), ink.green(), ink.blue())
        for mask, colour, alpha in ((~inside, outside, OUTSIDE_ALPHA),
                                    (inside, INSIDE, INSIDE_ALPHA)):
            if mask.any():
                plot.addItem(pg.BarGraphItem(
                    x=centers[mask], height=counts[mask], width=width * 0.92,
                    brush=pg.mkBrush(*colour, alpha),
                    pen=pg.mkPen(*colour, min(255, alpha + 60))))
        left = min(float(edges[0]), low if low is not None else math.inf)
        right = max(float(edges[-1]), high if high is not None else -math.inf)
        margin = (right - left) * 0.05 or 1.0
        x0, x1 = left - margin, right + margin
        band = (x0 if low is None else low, x1 if high is None else high)
        region = pg.LinearRegionItem(values=band, movable=False,
                                     brush=pg.mkBrush(*WINDOW_EDGE, 28),
                                     pen=pg.mkPen(*WINDOW_EDGE, width=2))
        region.setZValue(-10)
        plot.addItem(region)
        plot.setXRange(x0, x1, padding=0)
        plot.setYRange(0, max(1, int(counts.max())) * 1.1, padding=0)

    def _row(self, row: int, name: str, unit: str, values: np.ndarray, low,
             high, *, missing: bool) -> None:
        def fmt(v: float) -> str:
            return f"{v:.0f}" if abs(v) >= 100 else f"{v:.3g}"

        cells = [name, _window_text(low, high, unit)]
        if missing:
            cells += ["needs the pixel map", "", "", ""]
        elif len(values) == 0:
            cells += ["—", "—", "—", "0"]
        else:
            p10, p50, p90 = np.percentile(values, (10, 50, 90))
            n_in = int(_inside(values, low, high).sum())
            cells += [fmt(p50), f"{fmt(p10)}–{fmt(p90)}",
                      f"{fmt(values.min())}–{fmt(values.max())}",
                      f"{n_in} of {len(values)} "
                      f"({100 * n_in / len(values):.0f} %)"]
        for col, text in enumerate(cells):
            item = QTableWidgetItem(text)
            if col >= 2:
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                      | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, col, item)

    def closeEvent(self, event) -> None:
        self.closed.emit()
        super().closeEvent(event)
