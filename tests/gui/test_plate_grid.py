"""PlateGrid draws a plate in its real proportions (LIQUID R5)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest                                                    # noqa: E402
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt            # noqa: E402
from PySide6.QtGui import QColor, QMouseEvent                     # noqa: E402
from PySide6.QtTest import QTest                                  # noqa: E402
from PySide6.QtWidgets import QApplication                        # noqa: E402

from micropick.gui.widgets.plate_grid import PlateGrid            # noqa: E402

SBS_RATIO = 127.76 / 85.48


def _ordering(rows: int, columns: int) -> list[list[str]]:
    letters = "ABCDEFGHIJKLMNOP"[:rows]
    return [[f"{r}{c}" for r in letters] for c in range(1, columns + 1)]


SIX = _ordering(2, 3)
TWENTY_FOUR = _ordering(4, 6)
NINETY_SIX = _ordering(8, 12)
PLATES = [SIX, TWENTY_FOUR, NINETY_SIX, _ordering(16, 24)]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _grid(ordering, *, size=(500, 360), **kw) -> PlateGrid:
    grid = PlateGrid(**kw)
    grid.set_ordering(ordering)
    grid.resize(*size)
    return grid


def _center(grid, name) -> QPoint:
    return grid.well_rect(name).center().toPoint()


def _click(grid, point, modifier=Qt.KeyboardModifier.NoModifier) -> None:
    QTest.mouseClick(grid, Qt.MouseButton.LeftButton, modifier, point)


def _pixel(grid, name) -> QColor:
    return grid.grab().toImage().pixelColor(_center(grid, name))


@pytest.mark.parametrize("mini", [False, True])
def test_every_plate_has_the_same_outer_box(app, mini):
    boxes = {tuple(_grid(o, mini=mini).plate_rect().getRect()) for o in PLATES}
    assert len(boxes) == 1


@pytest.mark.parametrize("size", [(500, 360), (800, 300), (200, 500)])
def test_the_box_has_the_footprints_proportions_at_any_size(app, size):
    box = _grid(SIX, size=size).plate_rect()
    assert box.width() / box.height() == pytest.approx(SBS_RATIO)
    assert box.width() <= size[0] and box.height() <= size[1]


def test_six_wells_are_bigger_than_ninety_six(app):
    six = _grid(SIX).well_rect("A1").width()
    ninety_six = _grid(NINETY_SIX).well_rect("A1").width()
    assert six > 3 * ninety_six


@pytest.mark.parametrize("ordering", PLATES)
def test_every_well_lies_inside_the_box(app, ordering):
    grid = _grid(ordering)
    box = grid.plate_rect()
    assert all(box.contains(grid.well_rect(w)) for w in grid.wells)


def test_a_plates_size_does_not_depend_on_its_wells(app):
    hints = {(PlateGrid().sizeHint().width(),)}
    for ordering in PLATES:
        grid = PlateGrid()
        grid.set_ordering(ordering)
        hints.add((grid.sizeHint().width(),))
        assert grid.heightForWidth(400) == PlateGrid().heightForWidth(400)
    assert len(hints) == 1


@pytest.mark.parametrize("ordering", PLATES)
def test_a_click_hits_the_well_under_it(app, ordering):
    grid = _grid(ordering)
    for name in (ordering[0][0], ordering[-1][-1], ordering[1][1]):
        _click(grid, _center(grid, name))
        assert name in grid.selection


def test_a_click_on_the_plate_beside_the_wells_chooses_nothing(app):
    grid = _grid(SIX)
    box = grid.plate_rect()
    _click(grid, QPoint(int(box.right() - 2), int(box.bottom() - 2)))
    assert grid.selection == set()


@pytest.mark.parametrize("ordering", [SIX, NINETY_SIX])
def test_the_headers_take_their_column_and_row(app, ordering):
    grid = _grid(ordering)
    box = grid.plate_rect()
    column = ordering[1]
    _click(grid, QPoint(_center(grid, column[0]).x(), int(box.top() - 8)))
    assert grid.selection == set(column)
    grid.clear()
    row = [c[1] for c in ordering]
    _click(grid, QPoint(int(box.left() - 8), _center(grid, row[0]).y()))
    assert grid.selection == set(row)


def test_a_box_chooses_the_wells_inside_it(app):
    grid = _grid(NINETY_SIX)
    start, end = _center(grid, "B2"), _center(grid, "C4")
    QTest.mousePress(grid, Qt.MouseButton.LeftButton, pos=start - QPoint(3, 3))
    grid.mouseMoveEvent(QMouseEvent(
        QEvent.Type.MouseMove, QPointF(end + QPoint(3, 3)),
        QPointF(grid.mapToGlobal(end)), Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    QTest.mouseRelease(grid, Qt.MouseButton.LeftButton, pos=end + QPoint(3, 3))
    assert grid.selection == {f"{r}{c}" for r in "BC" for c in (2, 3, 4)}


def test_a_mini_click_reports_the_well_at_any_plate(app):
    grid = _grid(SIX, size=(150, 100), mini=True)
    clicked = []
    grid.well_clicked.connect(clicked.append)
    _click(grid, _center(grid, "B3"))
    assert clicked == ["B3"]


def test_planning_still_plans_a_clicked_well(app):
    grid = _grid(SIX, planning=True)
    _click(grid, _center(grid, "A2"))
    assert grid.plan == {"A2": 1} and grid.selection == {"A2"}


# -- wells of other steps ------------------------------------------------------


def test_a_dim_fill_is_the_color_toned_down(app):
    grid = _grid(SIX)
    bare = _pixel(grid, "A1")
    grid.set_dim_fills({"A1": "#e8a33d"})
    dim = _pixel(grid, "A1")
    assert dim != bare and dim != QColor("#e8a33d")
    assert dim.red() > dim.blue()                       # still orange
    assert _pixel(grid, "A2") == bare


def test_a_dim_fill_never_reads_as_selected(app):
    grid = _grid(SIX)
    grid.selection_color = QColor("#e8a33d")
    grid.set_dim_fills({"A1": "#e8a33d"})
    assert _pixel(grid, "A1") != grid.selection_color
    assert grid.selection == set()


def test_a_selection_is_drawn_over_a_dim_fill(app):
    grid = _grid(SIX)
    grid.set_dim_fills({"A1": "#e8a33d"})
    grid.toggle(["A1"])
    assert _pixel(grid, "A1") == grid.selection_color


def test_a_page_fill_is_drawn_over_a_dim_one(app):
    grid = _grid(SIX, mini=True)
    grid.set_dim_fills({"A1": "#e8a33d"})
    grid.set_fills({"A1": "#5cc18a"})
    assert _pixel(grid, "A1") == QColor("#5cc18a")


def test_dim_fills_can_be_taken_away(app):
    grid = _grid(SIX)
    bare = _pixel(grid, "A1")
    grid.set_dim_fills({"A1": "#e8a33d"})
    grid.set_dim_fills({})
    assert _pixel(grid, "A1") == bare
