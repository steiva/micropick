"""StepList's drags, in a real drag loop (LIQUID.md §5, §9 checks 2 and 4).

Not collected by itself: `test_step_list.py` runs this file in a process of
its own on the `minimal` platform. The suite's `offscreen` platform has a drag
that returns at once, and a process has one platform. Here a press and a move
on the handle start a `QDrag` whose loop is Qt's own, as on a Linux desktop;
Windows and macOS have their native loops, for the bench checks.

The harness is the page's part: it holds the plan, applies a move as one undo
entry and hands the steps back.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "minimal")

import pytest                                                      # noqa: E402
from PySide6.QtCore import QPoint, Qt, QTimer                     # noqa: E402
from PySide6.QtGui import QCursor, QKeySequence, QShortcut        # noqa: E402
from PySide6.QtTest import QTest                                   # noqa: E402
from PySide6.QtWidgets import (QApplication, QVBoxLayout,          # noqa: E402
                               QWidget)

from micropick.core.liquid_plan import (LiquidPlan, MoveLiquid,    # noqa: E402
                                        Place, WaitStep, move_step)
from micropick.gui.widgets.step_list import StepList               # noqa: E402

LEFT = Qt.MouseButton.LeftButton
COLORS = ("#111111", "#222222", "#333333", "#444444")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _step(n: int) -> MoveLiquid:
    return MoveLiquid(id=f"s{n}", color=COLORS[(n - 1) % 4], volume_ul=n * 10,
                      source=Place(slot="5", load_name="p", wells=[f"A{n}"]),
                      dest=Place(kind="trash") if n == 2 else Place())


class Harness(QWidget):
    def __init__(self, count: int = 3, height: int = 500):
        super().__init__()
        self.plan = LiquidPlan(steps=[_step(n + 1) for n in range(count)])
        self.undo: list[LiquidPlan] = []
        self.moves: list[tuple[str, int]] = []
        self.stops = 0
        self.list = StepList(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.list)
        self.list.set_steps(self.plan.steps)
        self.list.moveRequested.connect(self.apply)
        QShortcut(QKeySequence("Ctrl+Z"), self, self.revert)
        # What the main window binds, for a run's Stop.
        QShortcut(QKeySequence("Esc"), self, self.stopped)
        self.resize(420, height)
        self.show()
        QApplication.processEvents()

    def stopped(self) -> None:
        self.stops += 1

    def apply(self, step_id: str, new_index: int) -> None:
        self.moves.append((step_id, new_index))
        self.undo.append(self.plan)
        index = self.ids.index(step_id)
        self.plan = move_step(self.plan, index, new_index)
        self.list.set_steps(self.plan.steps)

    def revert(self) -> None:
        if self.undo:
            self.plan = self.undo.pop()
            self.list.set_steps(self.plan.steps)

    @property
    def ids(self) -> list[str]:
        return [s.id for s in self.plan.steps]


@pytest.fixture
def harness(app):
    made = []

    def make(*args, **kw):
        made.append(Harness(*args, **kw))
        return made[-1]

    yield make
    for widget in made:
        widget.close()
        widget.deleteLater()


def _in_window(harness, point: QPoint) -> QPoint:
    return harness.mapFromGlobal(point)


def _drag(harness, step_id: str, *during) -> None:
    """Press on the step's handle and move past the drag distance; each of
    `during`, called from inside the drag's loop, goes on with it, and the
    last ends it. Through the window, as a mouse is: `QDrag` starts only
    with a button down in `QGuiApplication.mouseButtons()`."""
    window = harness.windowHandle()
    handle = harness.list.card(step_id).handle
    start = handle.mapToGlobal(handle.rect().center())
    QCursor.setPos(start)
    QTest.mousePress(window, LEFT, pos=_in_window(harness, start))
    pending = list(during)

    def next_part():
        if pending:
            pending.pop(0)()
            QTimer.singleShot(0, next_part)

    QTimer.singleShot(0, next_part)
    far = start + QPoint(0, QApplication.startDragDistance() + 2)
    # Returns when the drag's loop ends.
    QTest.mouseMove(window, _in_window(harness, far))


def _to(harness, point: QPoint):
    def move():
        QCursor.setPos(point)
        QTest.mouseMove(harness.windowHandle(), _in_window(harness, point))
    return move


def _release(harness, point: QPoint):
    def release():
        QTest.mouseRelease(harness.windowHandle(), LEFT,
                           pos=_in_window(harness, point))
    return release


def _activate(harness) -> None:
    """On `minimal`, with no window manager, Qt's drag icon keeps the focus
    after any drag, and a window's shortcut needs its window active."""
    harness.activateWindow()
    harness.windowHandle().requestActivate()
    QApplication.processEvents()


def _above(harness, step_id: str) -> QPoint:
    card = harness.list.card(step_id)
    return card.mapToGlobal(QPoint(card.width() // 2, 2))


def _numbers(harness) -> list[str]:
    return [c.number.text() for c in harness.list.cards()]


def _shown_ids(harness) -> list[str]:
    return [c.step_id for c in harness.list.cards()]


def test_dragging_card_3_above_card_1_asks_for_the_move(harness):
    h = harness()
    point = _above(h, "s1")
    _drag(h, "s3", _to(h, point), _release(h, point))
    assert h.moves == [("s3", 0)]
    assert h.ids == ["s3", "s1", "s2"]
    assert _shown_ids(h) == ["s3", "s1", "s2"]
    assert _numbers(h) == ["1", "2", "3"]


def test_the_list_never_reorders_itself(harness):
    h = harness()
    h.list.moveRequested.disconnect(h.apply)
    point = _above(h, "s1")
    _drag(h, "s3", _to(h, point), _release(h, point))
    assert _shown_ids(h) == ["s1", "s2", "s3"]


def test_a_drag_down_lands_where_the_line_was(harness):
    h = harness()
    card = h.list.card("s3")
    point = card.mapToGlobal(QPoint(10, card.height() - 2))
    _drag(h, "s1", _to(h, point), _release(h, point))
    assert h.moves == [("s1", 2)]


def test_a_drop_where_it_was_asks_for_nothing(harness):
    h = harness()
    point = _above(h, "s2")
    _drag(h, "s2", _to(h, point), _release(h, point))
    assert h.moves == []


def test_the_card_lifts_with_a_placeholder_and_a_line(harness):
    h = harness()
    seen = {}
    point = _above(h, "s1")

    def look():
        seen["placeholder"] = h.list.placeholder.isVisible()
        seen["geometry"] = h.list.placeholder.geometry()
        seen["line"] = h.list.line.isVisible()
        seen["line height"] = h.list.line.height()

    _drag(h, "s3", _to(h, point), look, _release(h, point))
    assert seen["placeholder"] and seen["line"]
    assert seen["line height"] == 2
    assert not h.list.placeholder.isVisible() and not h.list.line.isVisible()


def test_the_placeholder_has_the_lifted_cards_place(harness):
    h = harness()
    seen = {}
    card = h.list.card("s2")
    place = card.geometry()
    point = _above(h, "s1")

    def look():
        seen["geometry"] = h.list.placeholder.geometry()

    _drag(h, "s2", _to(h, point), look, _release(h, point))
    assert seen["geometry"] == place


def test_esc_cancels_the_drag_and_is_not_the_windows_stop(harness):
    h = harness()
    point = _above(h, "s1")

    def esc():
        QTest.keyClick(h.windowHandle(), Qt.Key.Key_Escape)

    esc()
    assert h.stops == 1                  # the key does reach the shortcut
    _drag(h, "s3", _to(h, point), esc)
    assert h.moves == []
    assert h.stops == 1
    assert not h.list.placeholder.isVisible()
    _activate(h)
    esc()
    assert h.stops == 2


def test_a_release_outside_the_list_cancels(harness):
    h = harness()
    outside = h.mapToGlobal(QPoint(h.width() + 300, 10))
    _drag(h, "s3", _to(h, _above(h, "s1")), _to(h, outside),
          _release(h, outside))
    assert h.moves == []
    assert not h.list.line.isVisible()


def test_a_drag_near_the_edge_scrolls_the_list(harness):
    h = harness(count=12, height=300)
    bar = h.list.area.verticalScrollBar()
    assert bar.maximum() > 0
    viewport = h.list.area.viewport()
    bottom = viewport.mapToGlobal(QPoint(50, viewport.height() - 4))
    values = []

    def wait_a_little():
        QTest.qWait(200)
        values.append(bar.value())

    _drag(h, "s1", _to(h, bottom), wait_a_little, _release(h, bottom))
    assert values[0] > 0
    assert h.moves and h.moves[0][1] > 2


def test_an_open_card_stays_open_and_colors_stay_with_their_steps(harness):
    h = harness()
    card = h.list.card("s3")
    QTest.mouseClick(card.header, LEFT, pos=QPoint(120, 8))
    point = _above(h, "s1")
    _drag(h, "s3", _to(h, point), _release(h, point))
    assert h.list.card("s3") is card
    assert card.is_open
    assert [c.color for c in h.list.cards()] == [COLORS[2], COLORS[0],
                                                 COLORS[1]]


def test_during_a_run_the_handles_are_hidden_and_nothing_moves(harness):
    h = harness()
    h.list.set_locked(True)
    card = h.list.card("s3")
    assert not any(c.handle.isVisible() for c in h.list.cards())
    QTest.keyClick(card, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    for name in ("Move up", "Move down", "Duplicate", "Delete"):
        assert not card.action(name).isEnabled()
    # A press and a move where the handle was start nothing.
    where = card.mapToGlobal(QPoint(14, 16))
    window = h.windowHandle()
    QTest.mousePress(window, LEFT, pos=_in_window(h, where))
    QTest.mouseMove(window, _in_window(h, where + QPoint(0, -80)))
    QTest.mouseRelease(window, LEFT, pos=_in_window(h, where + QPoint(0, -80)))
    assert h.moves == []
    h.list.set_locked(False)
    assert all(c.handle.isVisible() for c in h.list.cards())
    assert card.action("Move up").isEnabled()


