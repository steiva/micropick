"""Manual pick Run's side panel (#113): two columns of 400 beside the picture,
one of 420 under about 1600 px of window, Points then a card folded by default,
and whatever the width the picture keeps the rest."""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QWidget      # noqa: E402

from micropick.gui.widgets.camera_view import CameraView         # noqa: E402
from micropick.gui.widgets.feed_row import FeedRow               # noqa: E402
from micropick.gui.widgets.fold_card import FoldCard             # noqa: E402
from micropick.gui.widgets.work_columns import WorkColumns       # noqa: E402

GAP = 16


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def frame(app):
    first, second = QLabel("In place"), QLabel("Aspirate")
    points = FoldCard("Points")
    points.body.layout().addWidget(QLabel("the list"))
    side = WorkColumns([first, second], points)
    view = CameraView()
    row = FeedRow(view, side, spacing=GAP)
    row.first, row.second, row.points, row.columns = first, second, points, side
    yield row
    row.close()
    row.deleteLater()


def _at(app, row, width, height=850):
    row.resize(width, height)
    row.show()
    for _ in range(3):
        app.processEvents()


def test_a_wide_window_has_two_columns_of_400_points_open(app, frame):
    _at(app, frame, 1880)
    assert frame.columns.two_columns
    assert frame.first.width() == 400
    assert frame.points.width() == 400
    assert frame.points.mapTo(frame, frame.points.rect().topLeft()).x() \
        > frame.first.mapTo(frame, frame.first.rect().topRight()).x()
    assert frame.points.is_open()
    assert not frame.points.header.isVisible()     # it does not fold here


def test_points_stands_the_full_height_of_its_column(app, frame):
    _at(app, frame, 1880)
    assert frame.points.height() == frame.columns.height()


def test_the_picture_takes_the_rest_of_the_width(app, frame):
    _at(app, frame, 1880)
    assert frame.view.width() + GAP + frame.columns.width() == 1880
    assert frame.view.height() == 850


def test_a_narrow_window_has_one_column_of_420_points_folded_last(app, frame):
    _at(app, frame, 1500)
    assert not frame.columns.two_columns
    assert frame.first.width() == frame.points.width() == 420
    assert frame.points.y() > frame.second.y()
    assert frame.points.mapTo(frame, frame.points.rect().topLeft()).x() \
        == frame.first.mapTo(frame, frame.first.rect().topLeft()).x()
    assert frame.points.header.isVisible() and not frame.points.is_open()


def test_points_opens_by_its_header_in_one_column(app, frame):
    _at(app, frame, 1500)
    frame.points.header.click()
    assert frame.points.is_open()


def test_the_columns_change_at_about_1600_px_of_window(app, frame):
    # The page's margins are 20 a side: the row is the window less 40.
    _at(app, frame, 1560)
    assert frame.columns.two_columns
    _at(app, frame, 1559)
    assert not frame.columns.two_columns


def test_the_way_back_and_forth_leaves_each_arrangement_whole(app, frame):
    _at(app, frame, 1880)
    _at(app, frame, 1500)
    _at(app, frame, 1880)
    assert frame.points.is_open() and frame.points.width() == 400
    _at(app, frame, 1500)
    assert not frame.points.is_open() and frame.points.width() == 420


def test_no_card_is_under_380_at_the_narrowest_window(app, frame):
    _at(app, frame, 1240)
    assert frame.first.width() >= 380


def test_a_plain_panel_still_gets_what_the_picture_leaves(app):
    side = QWidget()
    side.setMinimumWidth(300)
    row = FeedRow(CameraView(), side)
    _at(app, row, 1200, 600)
    assert side.width() == 1200 - 8 - 800
    row.close()
    row.deleteLater()


def test_a_card_that_stops_folding_opens_and_shows_its_title(app):
    card = FoldCard("Points")
    assert not card.is_open()
    card.set_folds(False)
    assert card.is_open() and card.header.isHidden()
    assert not card.title.isHidden()
    card.header.click()
    assert card.is_open()
    card.set_folds(True)
    assert not card.is_open() and not card.header.isHidden()
    assert card.title.isHidden()
    card.deleteLater()
