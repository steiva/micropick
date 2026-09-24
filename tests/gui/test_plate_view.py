"""The plate map: selecting with the mouse, and what the drawing says.

Offscreen, with a real `Destination` built from a stock definition, so the
grid is a real plate's and the labels come from the same parser the planning
table uses.
"""

import os

import pytest

pytest.importorskip("PySide6", reason="the gui extra is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt                           # noqa: E402
from PySide6.QtGui import QMouseEvent                            # noqa: E402
from PySide6.QtWidgets import QApplication                       # noqa: E402

from micropick.core.routine import Destination                   # noqa: E402
from micropick.gui.widgets.plate_view import (LABEL_MIN_WELLS,   # noqa: E402
                                              PlateView, _Header)

CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
NONE = Qt.KeyboardModifier.NoModifier


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def view(app):
    v = PlateView()
    v.resize(600, 420)
    v.show()
    v.set_destination(Destination.from_labware(
        "corning_96_wellplate_360ul_flat", 5))
    app.processEvents()
    yield v
    v.close()


def centre(view, well):
    """The viewport point of a well's centre. Mouse events go to the
    viewport, which is where Qt delivers them in a QGraphicsView."""
    item = view._wells[well]
    return QPointF(view.mapFromScene(item.sceneBoundingRect().center()))


def click(app, view, point, mods=NONE, kind=None):
    for typ in (kind,) if kind else (QMouseEvent.Type.MouseButtonPress,
                                     QMouseEvent.Type.MouseButtonRelease):
        app.sendEvent(view.viewport(), QMouseEvent(
            typ, point, view.viewport().mapToGlobal(point),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, mods))
    app.processEvents()


def drag(app, view, start, end, mods=NONE):
    app.sendEvent(view.viewport(), QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, start,
        view.viewport().mapToGlobal(start), Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton, mods))
    for step in (0.5, 1.0):
        here = QPointF(start.x() + (end.x() - start.x()) * step,
                       start.y() + (end.y() - start.y()) * step)
        app.sendEvent(view.viewport(), QMouseEvent(
            QMouseEvent.Type.MouseMove, here,
            view.viewport().mapToGlobal(here), Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton, mods))
    app.sendEvent(view.viewport(), QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease, end,
        view.viewport().mapToGlobal(end), Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton, mods))
    app.processEvents()


def header(view, text):
    for item in view.scene().items():
        if isinstance(item, _Header):
            children = item.childItems()
            if children and children[0].text() == text:
                return QPointF(view.mapFromScene(item.sceneBoundingRect().center()))
    raise KeyError(text)


def test_a_click_replaces_the_selection(app, view):
    click(app, view, centre(view, "A1"))
    assert view.selection == {"A1"}
    click(app, view, centre(view, "B2"))
    assert view.selection == {"B2"}


def test_ctrl_click_adds_and_takes_away(app, view):
    click(app, view, centre(view, "A1"))
    click(app, view, centre(view, "A2"), CTRL)
    click(app, view, centre(view, "A3"), CTRL)
    assert view.selection == {"A1", "A2", "A3"}
    click(app, view, centre(view, "A2"), CTRL)
    assert view.selection == {"A1", "A3"}


def test_a_header_takes_a_whole_line(app, view):
    click(app, view, header(view, "3"))
    assert view.selection == {f"{row}3" for row in "ABCDEFGH"}
    click(app, view, header(view, "B"))
    assert view.selection == {f"B{n}" for n in range(1, 13)}
    # And the modifiers mean the same thing on a header as on a well.
    click(app, view, header(view, "3"), CTRL)
    assert len(view.selection) == 12 + 8 - 1
    click(app, view, header(view, "B"), SHIFT)
    assert view.selection == {f"{row}3" for row in "ACDEFGH"}


def test_a_box_takes_what_is_inside_it(app, view):
    # From outside the grid's top-left to past C3, so no well sits on the
    # boundary: a marquee takes the wells whose centres are inside it.
    start = centre(view, "A1")
    end = centre(view, "C3")
    start = QPointF(start.x() - 8, start.y() - 8)
    end = QPointF(end.x() + 8, end.y() + 8)
    drag(app, view, start, end)
    assert view.selection == {f"{row}{n}" for row in "ABC" for n in (1, 2, 3)}
    # Shift takes away, which is the "или наоборот" of a marquee.
    inner_end = centre(view, "B2")
    drag(app, view, start, QPointF(inner_end.x() + 8, inner_end.y() + 8), SHIFT)
    assert view.selection == {"A3", "B3", "C1", "C2", "C3"}


def test_a_click_on_nothing_clears(app, view):
    view.select_all()
    assert len(view.selection) == 96
    click(app, view, QPointF(3.0, 3.0))
    assert view.selection == set()


def test_select_all_and_none(app, view):
    view.select_all()
    assert len(view.selection) == 96
    view.clear_selection()
    assert view.selection == set()


def test_the_count_is_drawn_in_the_well_and_the_name_when_it_is_not_planned(app, view):
    assert view._labels["A1"].text() == "A1"
    view.set_plan({"A1": 7})
    assert view._labels["A1"].text() == "7"
    view.set_plan({})
    assert view._labels["A1"].text() == "A1"


def test_a_double_click_names_one_well(app, view):
    seen = []
    view.well_activated.connect(seen.append)
    click(app, view, centre(view, "D4"),
          kind=QMouseEvent.Type.MouseButtonDblClick)
    assert seen == ["D4"]


def test_a_1536_has_no_room_for_labels(app):
    v = PlateView()
    v.resize(600, 420)
    v.show()
    v.set_destination(Destination.from_labware("greiner_1536_wellplate_12.6ul", 5))
    assert len(v._wells) > LABEL_MIN_WELLS
    assert v._labels == {}
    # The headers are still there: a row of a 1536 is exactly what a mouse
    # cannot select any other way.
    v.set_selection(())
    app.processEvents()
    v.close()


def test_changing_the_plate_forgets_the_selection(app, view):
    view.select_all()
    view.set_destination(Destination.from_labware(
        "corning_384_wellplate_112ul_flat", 2))
    assert view.selection == set()
    assert len(view._wells) == 384
