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


def test_the_window_is_dragged_on_the_histogram_and_saved(page, tmp_path):
    profile = page.session.profile
    profile.picking.cuboid_size_threshold = (250, 500)
    page._detection = detection([100, 200, 300, 400, 450, 600, 900])
    page._draw_histogram()
    assert page.sizes.isVisibleTo(page)
    region = regions(page)[0]
    assert region.movable
    region.setRegion((150, 700))                 # the band, dragged
    assert "5 of 7" in page.window_state.text()  # followed while moving
    region.sigRegionChangeFinished.emit(region)  # and let go
    assert tuple(profile.picking.cuboid_size_threshold) == (150, 700)
    saved = (profile.path / "picking.json").read_text()
    assert "150" in saved and "700" in saved
    assert "5 of 7" in page.window_state.text()


def test_sizes_and_plate_are_both_shown_and_do_not_fold(page):
    """Both cards at once (#52): a click on a header folds nothing."""
    page.show()
    assert page.sizes.is_open() and page.plate_card.is_open()
    page.sizes.header.click()
    page.plate_card.header.click()
    assert page.sizes.is_open() and page.plate_card.is_open()
    assert page.histogram.isVisible() and page.plate.isVisible()
    page.hide()


def test_all_measures_opens_the_figures(page):
    page._detection = detection([300, 400])
    page._draw_histogram()
    page._refresh()
    page.stats_button.click()
    assert page.stats.isVisible()
    page.stats_button.click()
    assert not page.stats.isVisible()
    page._detection = None
    page._refresh()
    assert not page.stats_button.isEnabled()


def test_the_plate_shows_the_plan_before_a_run(page):
    from micropick.config.labware import resolve_definition
    from micropick.core.routine import Destination, Routine
    from micropick.gui.theme import COLORS
    definition = resolve_definition("corning_96_wellplate_360ul_flat")
    page.session.set_routine(Routine(
        Destination.from_definition(definition, 5), {"A1": 2, "B1": 2},
        name="test plate"))
    assert page.plate_card.header.text() == "Plate · slot 5"
    from PySide6.QtGui import QColor
    assert page.plate._fills == {"A1": QColor(COLORS["planned"]),
                                 "B1": QColor(COLORS["planned"])}
    assert page.legend["Filled"].isHidden()
    assert page.plate_card.summary.text() == "0 of 2 wells"


def _settings_ready(app, page):
    """Shown, the page loads its detector; More settings waits for it."""
    import time
    end = time.monotonic() + 10
    while not page.settings_button.isEnabled() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    assert page.settings_button.isEnabled()


def test_more_settings_is_a_sheet_and_asks_to_analyze_again(app, page,
                                                            monkeypatch):
    """Over the page, not a window of its own (#52); a change leaves the
    cuboids found under the old settings, so the page says so."""
    page.resize(1200, 800)
    page.show()
    page._detection = detection([300, 400])
    page._refresh()
    assert page.again_line.isHidden()
    _settings_ready(app, page)
    page.settings_button.click()
    sheet = page.settings_sheet
    assert sheet.parentWidget() is page and sheet.isVisible()
    monkeypatch.setattr(sheet, "_confirm", lambda _changes: True)
    sheet._rows["vol"].set_value(12.5)
    sheet.done_button.click()
    assert not sheet.isVisible()
    profile = page.session.profile
    assert profile.picking.vol == 12.5
    assert "12.5" in (profile.path / "picking.json").read_text()
    assert page.again_line.text() == "Analyze again."
    assert not page.again_line.isHidden()
    page.hide()


def test_cancel_saves_nothing(app, page):
    page.show()
    before = page.session.profile.picking.vol
    _settings_ready(app, page)
    page.settings_button.click()
    page.settings_sheet._rows["vol"].set_value(before + 1)
    page.settings_sheet.cancel_button.click()
    assert page.session.profile.picking.vol == before
    page.hide()


def test_there_is_no_pickup_clips_switch(page):
    page.show()
    assert not page.clips_box.isVisible()
    page.hide()


def test_the_pad_is_folded_on_picking(page):
    assert page.jog.pad.folded


def test_camera_to_dish_center_is_opaque(page):
    """Readable over any picture (#50)."""
    assert "background" in page.center_button.styleSheet()


class _Detector:
    model = object()

    def __init__(self, fail=False):
        self.fail = fail

    def detect(self, frame, cfg, pixel_map):
        if self.fail:
            raise RuntimeError("the model fell over")
        return detection([300.0])


class _Camera:
    def __init__(self, robot):
        self.robot, self.lit = robot, []

    def read_after(self, _t):
        self.lit.append(self.robot.lights)
        return np.zeros((30, 40, 3), np.uint8)


def _analyse(app, page, monkeypatch, fail=False):
    import time
    session = page.session
    camera = _Camera(session.robot)
    monkeypatch.setattr(page, "detector", _Detector(fail))
    monkeypatch.setattr(page, "_camera", lambda: camera)
    page._analyse()
    end = time.monotonic() + 5
    while page._busy() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    return camera


@pytest.mark.parametrize("fail", [False, True])
def test_analyze_darkens_the_bench_and_lights_it_again(app, page,
                                                       monkeypatch, fail):
    """The rail lights put highlights on the dish (#51): off for the frame,
    back on after, a failed analysis included."""
    page.session.connect_robot()
    assert page.session.robot.lights is True
    camera = _analyse(app, page, monkeypatch, fail)
    assert camera.lit == [False]
    assert page.session.robot.lights is True
    assert page.session.lights is True
    page.session.shutdown()


def test_lights_left_off_stay_off(app, page, monkeypatch):
    page.session.connect_robot()
    page.session.set_lights(False)
    camera = _analyse(app, page, monkeypatch)
    assert camera.lit == [False]
    assert page.session.robot.lights is False
    page.session.shutdown()


def test_the_analyze_button_says_the_lights_go_off(page):
    assert page.lights_line.text() == "Lights go off while analyzing."
    assert page.lights_line.isVisibleTo(page)
