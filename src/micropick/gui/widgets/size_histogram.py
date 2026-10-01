"""The cuboids' sizes against the size window, as a histogram and a sentence.

`cuboid_size_threshold` decides what the run will pick, and it is two
numbers in a configuration file. On a dish of real cuboids it is also the
difference between a run that fills a plate and one that finds nothing. The
histogram is every detection's diameter with that window drawn over it, and
the count inside it stated in words: an operator who sees the population
sitting to the left of the window knows what to change and by how much.

A widget because two pages draw it - Picking, after Analyse the dish, and
Manual control, after Detect cuboids - and one drawing is the only way the
two cannot disagree about what is inside the window.

The numbers are `size_bins`, apart from the drawing: the same bins are
drawn here and, small and translucent, over the picture by `CameraView`
(`set_histogram`), so the plot and the picture cannot count differently.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pyqtgraph as pg
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

__all__ = ["SizeHistogram", "SizeBins", "size_bins", "BAR", "INSIDE",
           "WINDOW_EDGE", "HIST_BINS"]

# Histogram colours. A plot is its own surface, like the camera viewport,
# so these are fixed; the ink follows the palette's text colour, which is
# the one role qdarktheme really varies between light and dark.
BAR = (94, 158, 235)
INSIDE = (120, 220, 130)
WINDOW_EDGE = (240, 160, 48)

HIST_BINS = 28


@dataclass(frozen=True)
class SizeBins:
    """A detection's diameters, binned, against the size window (µm)."""

    centres: np.ndarray
    counts: np.ndarray
    width: float
    edges: np.ndarray
    low: float
    high: float
    total: int
    inside: int
    smaller: int
    bigger: int

    @property
    def inside_bins(self) -> np.ndarray:
        """Which bins are inside the window: a bar is in it or it is not."""
        return (self.centres >= self.low) & (self.centres <= self.high)

    @property
    def x_range(self) -> tuple[float, float]:
        """Both the population and the window in view, with a margin."""
        left = min(float(self.edges[0]), self.low)
        right = max(float(self.edges[-1]), self.high)
        margin = max(1.0, (right - left) * 0.05)
        return left - margin, right + margin

    def summary(self) -> str:
        return (f"{self.inside} of {self.total} cuboids are inside "
                f"{self.low:g}–{self.high:g} µm — {self.smaller} smaller, "
                f"{self.bigger} bigger.")


def size_bins(detection, window) -> SizeBins | str:
    """The bins for `detection` against `window` (low, high µm), or a
    sentence saying why there are none."""
    if detection is None or window is None:
        return "nothing measured yet"
    if not detection.classified or "diameter_microns" not in detection.df:
        # Without a pixel map there are no millimetres, so there are no
        # microns either, and a histogram of pixels would be a different
        # quantity wearing the same axis label.
        return "sizes need the pixel map: " + " ".join(detection.notes)
    sizes = detection.df.diameter_microns.to_numpy(dtype=float)
    sizes = sizes[np.isfinite(sizes)]
    low, high = (float(v) for v in window)
    if len(sizes) == 0:
        return "no detections to measure"
    # A dish with one cuboid, or with several of exactly one size, gives
    # numpy a zero-width range and every bin edge the same number: the
    # bars come out zero wide and the plot looks empty. Give it a span to
    # divide, centred on the value.
    span = float(sizes.max() - sizes.min())
    if span <= 0:
        centre = float(sizes[0])
        pad = max(1.0, abs(centre) * 0.1)
        edges = np.linspace(centre - pad, centre + pad, HIST_BINS + 1)
    else:
        edges = np.histogram_bin_edges(sizes, bins=HIST_BINS)
    counts, edges = np.histogram(sizes, bins=edges)
    width = float(edges[1] - edges[0]) if len(edges) > 1 else 1.0
    return SizeBins(centres=(edges[:-1] + edges[1:]) / 2, counts=counts,
                    width=width, edges=edges, low=low, high=high,
                    total=len(sizes),
                    inside=int(((sizes >= low) & (sizes <= high)).sum()),
                    smaller=int((sizes < low).sum()),
                    bigger=int((sizes > high).sum()))


class SizeHistogram(QWidget):
    """The plot and the sentence under it. `show_detection` draws one
    detection against a window; `intro` is what the sentence says before
    there is anything to draw."""

    def __init__(self, intro: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.plot = pg.PlotWidget(background=None)
        ink = self.palette().color(QPalette.ColorRole.Text)
        for axis in ("left", "bottom"):
            self.plot.getAxis(axis).setPen(ink)
            self.plot.getAxis(axis).setTextPen(ink)
        # The plot is a readout, not something to explore. Left to itself
        # pyqtgraph keeps whatever range the last wheel turn or auto-range
        # left behind, and a plot in a scrolling column collects wheel
        # turns meant for the column - which is how it ends up too tall or
        # too narrow with an "A" button in the corner as the only way back.
        # Both axes are set from the data after every draw instead.
        view = self.plot.getPlotItem().getViewBox()
        view.setMouseEnabled(x=False, y=False)
        view.setMenuEnabled(False)
        view.wheelEvent = lambda event, axis=None: event.ignore()
        self.plot.getPlotItem().hideButtons()
        self.plot.setLabel("bottom", "diameter, µm")
        self.plot.setLabel("left", "cuboids")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        # Bounded above too: a plot widget expands, and in a tall column it
        # took every spare pixel and pushed the next card out of sight.
        self.plot.setMinimumHeight(190)
        self.plot.setMaximumHeight(260)

        self.state = QLabel(intro)
        self.state.setWordWrap(True)

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.plot)
        column.addWidget(self.state)

    def show_detection(self, detection, window, *,
                       after: str = "") -> SizeBins | None:
        """Draw `detection`'s diameters against `window` (low, high µm).
        `after` is added to the sentence when there are sizes to count.
        None for `detection` clears it. Returns the bins drawn, for the
        picture's overlay, or None."""
        # clear() drops the items; the ranges below are what stop the view
        # box from keeping the last dish's.
        self.plot.clear()
        bins = size_bins(detection, window)
        if isinstance(bins, str):
            self.state.setText(bins)
            return None
        # Two series rather than one recoloured: a bar is inside the window
        # or it is not, and the eye should not have to compare a shade with
        # the band behind it.
        inside_bin = bins.inside_bins
        for mask, colour in ((~inside_bin, BAR), (inside_bin, INSIDE)):
            if not mask.any():
                continue
            self.plot.addItem(pg.BarGraphItem(
                x=bins.centres[mask], height=bins.counts[mask],
                width=bins.width * 0.92,
                brush=pg.mkBrush(*colour, 200), pen=pg.mkPen(*colour)))

        region = pg.LinearRegionItem(values=(bins.low, bins.high),
                                     movable=False,
                                     brush=pg.mkBrush(*WINDOW_EDGE, 28),
                                     pen=pg.mkPen(*WINDOW_EDGE, width=2))
        region.setZValue(-10)
        self.plot.addItem(region)
        # Both the population and the window in view, so "the cuboids are to
        # the left of the window" is a thing that can be seen rather than
        # inferred from two numbers off the edge of the plot.
        self.plot.setXRange(*bins.x_range, padding=0)
        # And the height, for the same reason: a bar chart whose y range is
        # remembered from another dish is a bar chart of the wrong height.
        self.plot.setYRange(0, max(1, int(bins.counts.max())) * 1.1,
                            padding=0)
        self.state.setText(bins.summary() + (f" {after}" if after else ""))
        return bins
