"""The points as chips: Aspirate's and Dispense's At a point (MANUAL.md §4.2).

The same points as `widgets.point_list`, from the session, for choosing one and
nothing else: a star and a name, the chosen chip outlined, again letting it go.
They are made, edited and moved to in the Points card. A point the tip cannot
go to is flagged here too (`Session.memo_problem`).

As tall as it was given whatever it lists, more rows scrolling, so that a point
marked with this card open moves nothing under it.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtWidgets import (QFrame, QLayout, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from ..session import Session
from ..theme.factory import laid_out_at_once

__all__ = ["PointChips"]

GAP = 8
# A chip's text, which a long name is cut to.
TEXT_PX = 240


class _Flow(QLayout):
    """Left to right, a new row where the width ends."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setContentsMargins(0, 0, 0, 0)
        self._items = []

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return (self._items.pop(index) if 0 <= index < len(self._items)
                else None)

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._place(QRect(0, 0, width, 0), move=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._place(rect, move=True)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        return QSize(0, 0)

    def _place(self, rect: QRect, *, move: bool) -> int:
        x, y, row = rect.x(), rect.y(), 0
        for item in self._items:
            size = item.sizeHint()
            if x > rect.x() and x + size.width() > rect.right() + 1:
                x, y, row = rect.x(), y + row + GAP, 0
            if move:
                item.setGeometry(QRect(QPoint(x, y), size))
            x += size.width() + GAP
            row = max(row, size.height())
        return y + row - rect.y()


class PointChips(QWidget):
    # The point chosen, by name; None when none is.
    chosen = Signal(object)

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.setObjectName("plain")
        self.chosen_name: str | None = None
        self._chips: dict[str, QPushButton] = {}
        self.area = QScrollArea(self)
        self.area.setObjectName("plain")
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setWidgetResizable(True)
        self.area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.inner = QWidget(self.area)
        self.inner.setObjectName("plain")
        self._flow = _Flow(self.inner)
        self.area.setWidget(self.inner)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.area)
        session.memo_points_changed.connect(self.refresh)
        # The deck modules, and with them what is too low, are the profile's.
        session.profile_changed.connect(lambda _p: self.refresh())
        self.refresh()

    def names(self) -> list[str]:
        return list(self._chips)

    def chip(self, name: str | None) -> QPushButton | None:
        return self._chips.get(name)

    def refresh(self) -> None:
        """The chips again from the session, the chosen one kept."""
        with laid_out_at_once(self):
            for chip in self._chips.values():
                self._flow.removeWidget(chip)
                chip.hide()
                chip.deleteLater()
            self._chips = {point.name: self._make_chip(point)
                           for point in self.session.points}
        if self.chosen_name not in self._chips:
            self._set_chosen(None)
        self._show_chosen()

    def _make_chip(self, point) -> QPushButton:
        chip = QPushButton(self.inner)
        chip.setObjectName("pointChip")
        chip.setCheckable(True)
        chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        name = chip.fontMetrics().elidedText(
            point.name, Qt.TextElideMode.ElideRight, TEXT_PX)
        chip.setText(f"{'★' if point.saved else '☆'} {name}")
        problem = self.session.memo_problem(point)
        chip.setProperty("problem", bool(problem))
        chip.setToolTip(f"{point.name}: {problem}." if problem else point.name)
        chip.clicked.connect(lambda _on=False, n=point.name: self._clicked(n))
        self._flow.addWidget(chip)
        chip.show()
        return chip

    def choose(self, name: str | None) -> None:
        """Choose a point, or none."""
        self._set_chosen(name if name in self._chips else None)
        self._show_chosen()

    def _clicked(self, name: str) -> None:
        self.choose(None if name == self.chosen_name else name)

    def _set_chosen(self, name: str | None) -> None:
        if name != self.chosen_name:
            self.chosen_name = name
            self.chosen.emit(name)

    def _show_chosen(self) -> None:
        for name, chip in self._chips.items():
            chip.setChecked(name == self.chosen_name)
