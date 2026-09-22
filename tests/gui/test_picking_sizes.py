"""The size histogram: the count inside the window is the number acted on.

Offscreen, with a stub detection. `cuboid_size_threshold` decides what a run
will pick, and this is the only place it is ever shown against the dish it
will be applied to, so what the words under the plot say has to be right.
"""

import os

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pyqtgraph as pg                                           # noqa: E402
from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.gui.app import Options                            # noqa: E402
from micropick.gui.detector import Detection                     # noqa: E402
from micropick.gui.pages.picking import PickingPage              # noqa: E402
from micropick.gui.session import Session                        # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def page(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MICROPICK_ROOT", str(tmp_path))
    session = Session(Options(mock=True))
    session.load_profile("mock")
    p = PickingPage(session)
    yield p
    p.deleteLater()


def detection(sizes, classified=True):
    df = pd.DataFrame({"diameter_microns": np.asarray(sizes, dtype=float)})
    empty = df.iloc[0:0]
    return Detection(np.empty((0, 4)), np.empty(0), df, empty, empty, empty,
                     {}, classified=classified, standin=False)


def bars(page):
    return [i for i in page.hist.getPlotItem().items
            if isinstance(i, pg.BarGraphItem)]


def regions(page):
    return [i for i in page.hist.getPlotItem().items
            if isinstance(i, pg.LinearRegionItem)]


def test_it_counts_what_the_window_would_accept(page):
    page.session.profile.picking.cuboid_size_threshold = (250, 500)
    page._detection = detection([100, 200, 300, 400, 450, 600, 900])
    page._draw_histogram()
    text = page.window_state.text()
    assert "3 of 7" in text                 # 300, 400, 450
    assert "2 smaller" in text              # 100, 200
    assert "2 bigger" in text               # 600, 900
    assert regions(page)[0].getRegion() == (250.0, 500.0)


def test_moving_the_window_moves_the_count(page):
    page._detection = detection([100, 200, 300, 400, 450, 600, 900])
    page.session.profile.picking.cuboid_size_threshold = (50, 1000)
    page._draw_histogram()
    assert "7 of 7" in page.window_state.text()
    page.session.profile.picking.cuboid_size_threshold = (1000, 2000)
    page._draw_histogram()
    assert "0 of 7" in page.window_state.text()
    assert "7 smaller" in page.window_state.text()


def test_one_cuboid_still_draws_a_bar(page):
    """A zero-width range gives numpy one repeated bin edge, and the bars
    come out zero wide: a plot that looks empty with a detection in it."""
    page._detection = detection([420.0])
    page._draw_histogram()
    assert bars(page), "no bar drawn for a single detection"
    assert all(bar.opts["width"] > 0 for bar in bars(page))
    assert "1 of 1" in page.window_state.text()


def test_inside_and_outside_are_two_series(page):
    page.session.profile.picking.cuboid_size_threshold = (250, 500)
    page._detection = detection([100, 300, 900])
    page._draw_histogram()
    # One brush for what the window takes and one for what it does not: a
    # bar is inside or it is not, and the eye should not have to compare a
    # shade against the band behind it.
    assert len(bars(page)) == 2


def test_without_a_pixel_map_it_says_so_rather_than_plotting_pixels(page):
    page._detection = detection([1, 2, 3], classified=False)
    page._detection.notes.append("no pixel map in the profile")
    page._draw_histogram()
    assert bars(page) == []
    assert "pixel map" in page.window_state.text()


def test_nothing_detected_is_not_an_error(page):
    page._detection = detection([])
    page._draw_histogram()
    assert bars(page) == []
    assert "no detections" in page.window_state.text()
