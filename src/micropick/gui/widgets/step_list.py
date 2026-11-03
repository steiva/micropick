"""The plan's steps as cards, reordered by dragging (LIQUID.md §3.1, §5).

A `StepList` shows `core.liquid_plan` steps, one `PlanStepCard` each (not the
calibration pages' `StepCard`), in a scroll area. It never changes the order
itself: a drag, Alt+↑ or Alt+↓ and the ⋯ menu emit `moveRequested(step_id,
new_index)`, the page applies that to the plan as one undo entry and calls
`set_steps`, which reuses the cards by id and only lays them out again. So an
open card stays open and the model is the one source of the order. `new_index`
is the step's place once it is taken out, as `move_step` has it.

Not a `QListWidget` with item widgets: on a move Qt loses the item's widget,
and with it the card's state, and it cannot draw the placeholder.

The drag
--------
It starts on the ⠿ handle, past the drag distance, as a `QDrag` carrying the
step's id. The list takes the drop; nothing inside it accepts one, so the
drag's events come to the list whatever card the pointer is over. The
placeholder covers the lifted card, which stays where it is: the layout does
not change during a drag, and neither does the place of any gap. A gap is
counted from the cards' middles. The line and the placeholder are children of
the column, so they scroll with it.

Esc belongs to the drag while it lasts: `QDrag`'s loop takes the keys, and
accepts the shortcut override, before the window's Esc shortcut sees them
(tested on Qt's own loop; Windows' and macOS's native ones are a bench check).
So Esc cancels a drag and is not a Stop, and a release outside the list cancels
as well. The list is locked during a run, so no drag ever meets a run to stop.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QMimeData, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QDrag, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                               QMenu, QScrollArea, QToolButton, QVBoxLayout,
                               QWidget)

from ...core.liquid_plan import step_chips, step_title
from ..theme import SPACING
from ..theme.factory import caption, muted_label

__all__ = ["MIME", "PlanStepCard", "StepList"]

MIME = "application/x-micropick-plan-step"

# How near an edge a drag scrolls the list, and how fast.
EDGE_PX = 24
SCROLL_STEP_PX = 12
SCROLL_EVERY_MS = 30

LIFTED_OPACITY = 0.85
NUMBER_PX = 22


def _faded(pixmap: QPixmap) -> QPixmap:
    out = QPixmap(pixmap.size())
    out.setDevicePixelRatio(pixmap.devicePixelRatio())
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setOpacity(LIFTED_OPACITY)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return out


def _restyle(widget: QWidget) -> None:
    """A style sheet rule on a property takes effect only polished again."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class _Handle(QLabel):
    """⠿: past the drag distance a press on it asks for the drag. A press
    that does not move does nothing, and never reaches the header."""

    dragAsked = Signal(QPoint)

    def __init__(self, parent: QWidget):
        super().__init__("⠿", parent)
        self.setObjectName("stepHandle")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.pressed: QPoint | None = None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.pressed = event.position().toPoint()
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        event.accept()
        if self.pressed is None:
            return
        moved = event.position().toPoint() - self.pressed
        if moved.manhattanLength() < QApplication.startDragDistance():
            return
        pressed, self.pressed = self.pressed, None
        self.dragAsked.emit(pressed)

    def mouseReleaseEvent(self, event) -> None:
        self.pressed = None
        event.accept()


class _Header(QWidget):
    """What a click opens and closes the card on."""

    clicked = Signal()

    def mousePressEvent(self, event) -> None:
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if (event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.position().toPoint())):
            self.clicked.emit()


class PlanStepCard(QFrame):
    """One step: `⠿ (2) Move 50 µl   ⋯` over its chips, and `body`, shown
    while open, for what the page puts there."""

    dragAsked = Signal(object, QPoint)      # the card, the press in it
    moveAsked = Signal(str, int)            # the step, -1 or +1
    duplicateAsked = Signal(str)
    deleteAsked = Signal(str)
    clicked = Signal(str)

    def __init__(self, step_id: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.step_id = step_id
        self.color = ""
        self.is_open = False
        self._unfinished = False
        self.setObjectName("planStepCard")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACING * 2, 10, SPACING * 2, 10)
        outer.setSpacing(SPACING)

        self.header = _Header(self)
        self.header.setObjectName("plain")
        self.header.clicked.connect(lambda: self.clicked.emit(self.step_id))
        lines = QVBoxLayout(self.header)
        lines.setContentsMargins(0, 0, 0, 0)
        lines.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.handle = _Handle(self.header)
        self.handle.dragAsked.connect(self._drag_asked)
        top.addWidget(self.handle)
        self.number = QLabel(self.header)
        self.number.setObjectName("stepNumber")
        self.number.setFixedSize(NUMBER_PX, NUMBER_PX)
        self.number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(self.number)
        self.title = QLabel(self.header)
        self.title.setObjectName("heading3")
        top.addWidget(self.title, 1)
        self.warning = QLabel("Not finished", self.header)
        self.warning.setObjectName("stepWarning")
        top.addWidget(self.warning)
        self.more = QToolButton(self.header)
        self.more.setObjectName("stepMore")
        self.more.setText("⋯")
        self.more.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more.setMenu(self._menu())
        top.addWidget(self.more)
        lines.addLayout(top)
        self.chip_row = QHBoxLayout()
        self.chip_row.setContentsMargins(NUMBER_PX + 16, 0, 0, 0)
        self.chip_row.setSpacing(6)
        lines.addLayout(self.chip_row)
        self.chips: list[QLabel] = []
        outer.addWidget(self.header)

        self.body = QWidget(self)
        self.body.setObjectName("plain")
        self.body.setVisible(False)
        outer.addWidget(self.body)

    def _menu(self) -> QMenu:
        menu = QMenu(self)
        self.actions_by_name: dict[str, QAction] = {}
        for name, done in (("Move up", lambda: self.moveAsked.emit(self.step_id, -1)),
                           ("Move down", lambda: self.moveAsked.emit(self.step_id, 1)),
                           ("Duplicate", lambda: self.duplicateAsked.emit(self.step_id)),
                           ("Delete", lambda: self.deleteAsked.emit(self.step_id))):
            action = menu.addAction(name)
            action.triggered.connect(done)
            self.actions_by_name[name] = action
        return menu

    def action(self, name: str) -> QAction:
        return self.actions_by_name[name]

    def _drag_asked(self, pressed: QPoint) -> None:
        self.dragAsked.emit(self, self.handle.mapTo(self, pressed))

    def show_step(self, step, index: int, count: int, *, unfinished: bool,
                  locked: bool) -> None:
        """The card as step number `index + 1` of `count`."""
        self.color = step.color
        self.number.setText(str(index + 1))
        self.number.setStyleSheet(f"background: {step.color}; "
                                  f"border-radius: {NUMBER_PX // 2}px;")
        self.title.setText(step_title(step))
        self._show_chips(step_chips(step))
        self._unfinished = unfinished
        self.handle.setVisible(not locked)
        self.action("Move up").setEnabled(not locked and index > 0)
        self.action("Move down").setEnabled(not locked and index < count - 1)
        self.action("Duplicate").setEnabled(not locked)
        self.action("Delete").setEnabled(not locked)
        self._show_warning()

    def _show_chips(self, texts: list[str]) -> None:
        while self.chip_row.count():
            item = self.chip_row.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.chips = []
        for n, text in enumerate(texts):
            if n:
                self.chip_row.addWidget(muted_label("→", self.header))
            chip = QLabel(text, self.header)
            # One place alone is where the step works: on the plate's side.
            chip.setObjectName("chipFrom" if len(texts) > 1 and n == 0
                               else "chipTo")
            chip.setProperty("missing", text == "choose")
            self.chip_row.addWidget(chip)
            self.chips.append(chip)
        self.chip_row.addStretch(1)

    def _show_warning(self) -> None:
        self.warning.setVisible(self._unfinished and not self.is_open)

    def set_open(self, shown: bool) -> None:
        self.is_open = shown
        self.body.setVisible(shown)
        self.setProperty("open", shown)
        _restyle(self)
        self._show_warning()

    def keyPressEvent(self, event) -> None:
        by = {Qt.Key.Key_Up: -1, Qt.Key.Key_Down: 1}.get(event.key())
        if by is None or not event.modifiers() & Qt.KeyboardModifier.AltModifier:
            super().keyPressEvent(event)
            return
        self.moveAsked.emit(self.step_id, by)


class StepList(QWidget):
    """See the module's docstring. `openChanged(step_id)` as the open card
    changes, "" for none: one card is open at a time."""

    moveRequested = Signal(str, int)
    duplicateRequested = Signal(str)
    deleteRequested = Signal(str)
    openChanged = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._cards: dict[str, PlanStepCard] = {}
        self._order: list[PlanStepCard] = []
        self._steps: list = []
        self._not_finished: set[str] = set()
        self._open = ""
        self._locked = False
        self._pointer_y = 0.0
        self._scroll_by = 0
        self.setAcceptDrops(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING)
        top = QHBoxLayout()
        top.addWidget(caption("STEPS", self))
        top.addStretch(1)
        self.hint = muted_label("drag ⠿ to reorder", self)
        top.addWidget(self.hint)
        layout.addLayout(top)

        self.area = QScrollArea(self)
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.inner = QWidget(self.area)
        self.inner.setObjectName("plain")
        self.column = QVBoxLayout(self.inner)
        self.column.setContentsMargins(0, 0, 0, 0)
        self.column.setSpacing(SPACING)
        self.column.addStretch(1)
        self.area.setWidget(self.inner)
        layout.addWidget(self.area, 1)

        self.placeholder = QFrame(self.inner)
        self.placeholder.setObjectName("stepPlaceholder")
        self.placeholder.hide()
        self.line = QFrame(self.inner)
        self.line.setObjectName("stepInsertion")
        self.line.hide()
        self._scroller = QTimer(self)
        self._scroller.setInterval(SCROLL_EVERY_MS)
        self._scroller.timeout.connect(self._scroll)

    def card(self, step_id: str) -> PlanStepCard:
        return self._cards[step_id]

    def cards(self) -> list[PlanStepCard]:
        """The cards in the order shown."""
        return list(self._order)

    def set_steps(self, steps: list, not_finished=()) -> None:
        """Show `steps` in their order, `not_finished` the ids of those with
        a problem. A card whose step is still there is kept."""
        ids = {step.id for step in steps}
        for step_id in [i for i in self._cards if i not in ids]:
            card = self._cards.pop(step_id)
            self.column.removeWidget(card)
            card.deleteLater()
        if self._open and self._open not in ids:
            self._open = ""
            self.openChanged.emit("")
        self._order = [self._cards.get(s.id) or self._new_card(s.id)
                       for s in steps]
        for index, (step, card) in enumerate(zip(steps, self._order)):
            self.column.insertWidget(index, card)
            card.show_step(step, index, len(steps),
                           unfinished=step.id in not_finished,
                           locked=self._locked)
        focused = next((c for c in self._order if c.hasFocus()), None)
        if focused is not None:
            self.column.activate()
            self.area.ensureWidgetVisible(focused)
        self._steps = list(steps)
        self._not_finished = set(not_finished)

    def _new_card(self, step_id: str) -> PlanStepCard:
        card = PlanStepCard(step_id, self.inner)
        card.dragAsked.connect(self._drag)
        card.moveAsked.connect(self._move_by)
        card.duplicateAsked.connect(self.duplicateRequested)
        card.deleteAsked.connect(self.deleteRequested)
        card.clicked.connect(self._toggle)
        self._cards[step_id] = card
        return card

    def set_locked(self, locked: bool) -> None:
        """During a run: no handles, and nothing moves or goes."""
        self._locked = locked
        self.hint.setVisible(not locked)
        self.set_steps(self._steps, self._not_finished)

    def set_open(self, step_id: str) -> None:
        """Open that card and close the one open; "": close it."""
        self._open = step_id
        for card in self._order:
            card.set_open(card.step_id == step_id)
        self.openChanged.emit(step_id)

    def _toggle(self, step_id: str) -> None:
        self.set_open("" if step_id == self._open else step_id)

    def _index(self, step_id: str) -> int:
        return self._order.index(self._cards[step_id])

    def _move_by(self, step_id: str, by: int) -> None:
        to = self._index(step_id) + by
        if not self._locked and 0 <= to < len(self._order):
            self.moveRequested.emit(step_id, to)

    # -- the drag --

    def _drag(self, card: PlanStepCard, hot_spot: QPoint) -> None:
        if self._locked:
            return
        data = QMimeData()
        data.setData(MIME, QByteArray(card.step_id.encode()))
        drag = QDrag(card.handle)
        drag.setMimeData(data)
        drag.setPixmap(_faded(card.grab()))
        drag.setHotSpot(hot_spot)
        self._lift(card)
        QApplication.setOverrideCursor(Qt.CursorShape.ClosedHandCursor)
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            QApplication.restoreOverrideCursor()
            self._land()

    def _lift(self, card: PlanStepCard) -> None:
        self.placeholder.setGeometry(card.geometry())
        self.placeholder.show()
        self.placeholder.raise_()

    def _land(self) -> None:
        self.placeholder.hide()
        self._leave()

    def _leave(self) -> None:
        self.line.hide()
        self._scroller.stop()

    def _step_of(self, event) -> str | None:
        """The step a drag carries, if this list may take it."""
        if self._locked or not event.mimeData().hasFormat(MIME):
            return None
        step_id = bytes(event.mimeData().data(MIME)).decode()
        return step_id if step_id in self._cards else None

    def _gap(self, y: float) -> int:
        """How many cards are above the pointer, at `y` in this widget."""
        inner_y = self.inner.mapFrom(self, QPoint(0, int(y))).y()
        return sum(1 for card in self._order
                   if card.geometry().center().y() < inner_y)

    def _show_line(self) -> None:
        gap = self._gap(self._pointer_y)
        half = self.column.spacing() // 2
        if gap < len(self._order):
            y = self._order[gap].geometry().top() - half
        else:
            y = self._order[-1].geometry().bottom() + half
        self.line.setGeometry(0, max(0, y - 1), self.inner.width(), 2)
        self.line.show()
        self.line.raise_()

    def _scroll(self) -> None:
        bar = self.area.verticalScrollBar()
        bar.setValue(bar.value() + self._scroll_by)
        self._show_line()

    def _follow(self, y: float) -> None:
        """The pointer at `y`: the line, and scrolling near an edge."""
        self._pointer_y = y
        self._show_line()
        viewport = self.area.viewport()
        in_view = viewport.mapFrom(self, QPoint(0, int(y))).y()
        self._scroll_by = (-SCROLL_STEP_PX if in_view < EDGE_PX
                           else SCROLL_STEP_PX
                           if in_view > viewport.height() - EDGE_PX else 0)
        if self._scroll_by:
            self._scroller.start()
        else:
            self._scroller.stop()

    def dragEnterEvent(self, event) -> None:
        if self._step_of(event) is None:
            event.ignore()
            return
        event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if self._step_of(event) is None:
            event.ignore()
            return
        self._follow(event.position().y())
        event.acceptProposedAction()

    def dragLeaveEvent(self, _event) -> None:
        self._leave()

    def dropEvent(self, event) -> None:
        step_id = self._step_of(event)
        if step_id is None:
            event.ignore()
            return
        event.acceptProposedAction()
        gap = self._gap(event.position().y())
        index = self._index(step_id)
        to = gap if gap <= index else gap - 1
        self._land()
        if to != index:
            self.moveRequested.emit(step_id, to)
