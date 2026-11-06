"""The camera screens' layout rule (TIP.md §2.1, #120)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint                                # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from micropick.gui.theme.factory import scroll_column
from micropick.gui.widgets.camera_view import CameraView
from micropick.gui.widgets.feed_row import FeedRow
from micropick.gui.widgets.fold_card import FoldCard
from micropick.gui.widgets.work_columns import COLUMN, WorkColumns

BODY = {1920: 860, 1600: 700}       # a row's height in such a window


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def row(app):
    inner = QWidget()
    view = CameraView()
    made = FeedRow(view, scroll_column(inner, 440))
    made.inner = inner
    yield made
    made.close()
    made.deleteLater()


def _at(app, row, width, height):
    row.resize(width, height)
    row.show()
    for _ in range(3):
        app.processEvents()


@pytest.mark.parametrize("width, height", [
    (1880, 860), (1560, 700), (1200, 860), (1000, 860), (1880, 500)])
def test_the_box_stays_4_3_at_any_size(app, row, width, height):
    _at(app, row, width, height)
    assert row.view.width() / row.view.height() == pytest.approx(4 / 3,
                                                                 rel=0.01)


@pytest.mark.parametrize("window", [1920, 1600])
def test_the_column_is_440_and_centered_in_what_the_picture_leaves(
        app, row, window):
    _at(app, row, window - 40, BODY[window])
    assert row.view.height() == BODY[window]
    assert row.inner.width() == 440
    left = row.inner.mapTo(row, QPoint(0, 0)).x()
    before, after = left - row.view.width() - 8, row.width() - left - 440
    assert abs(before - after) <= 1


def test_with_no_room_the_picture_gives_way_never_the_column(app, row):
    _at(app, row, 1000, 860)
    assert row.inner.width() == 440
    assert row.side.geometry().right() < row.width()
    assert row.view.width() < 860 * 4 / 3


def test_a_panel_wider_inside_than_designed_still_stands_at_its_width(app):
    """A scroll column's size hint is what is in it, not its fixed width."""
    from PySide6.QtWidgets import QVBoxLayout
    inner = QWidget()
    QVBoxLayout(inner).addWidget(QLabel("a long line " * 20))
    row = FeedRow(CameraView(), scroll_column(inner, 440))
    _at(app, row, 1880, 860)
    assert inner.width() == 440
    row.close()
    row.deleteLater()


def test_two_columns_keep_their_width_and_the_picture_takes_the_rest(app):
    points = FoldCard("Points")
    side = WorkColumns([QLabel("Aspirate")], points)
    row = FeedRow(CameraView(), side, spacing=16)
    _at(app, row, 1880, 860)
    assert side.two_columns
    # About 1048 x 786: two scroll bars' room comes off it.
    assert row.view.width() == pytest.approx(1048, abs=40)
    assert row.view.height() == pytest.approx(786, abs=30)
    assert points.width() == COLUMN
    row.close()
    row.deleteLater()


def test_with_a_short_window_two_columns_are_centered_too(app):
    side = WorkColumns([QLabel("Aspirate")], FoldCard("Points"))
    row = FeedRow(CameraView(), side, spacing=16)
    _at(app, row, 1880, 500)
    free = row.width() - row.view.width() - 16 - side.width()
    assert free > 100
    assert side.x() - row.view.width() - 16 == pytest.approx(free / 2, abs=10)
    row.close()
    row.deleteLater()


def test_a_panel_that_grows_keeps_the_old_rule(app):
    side = QWidget()
    side.setMinimumWidth(400)
    view = CameraView()
    row = FeedRow(view, side, side_grows=True)
    _at(app, row, 1880, 860)
    assert view.height() == 860
    assert side.width() == 1880 - 8 - round(860 * 4 / 3)
    assert side.x() == view.width() + 8
    _at(app, row, 1880, 300)
    assert side.width() == 800                  # twice what it was built for
    assert view.height() == 300
    row.close()
    row.deleteLater()
