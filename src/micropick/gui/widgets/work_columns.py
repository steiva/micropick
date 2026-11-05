"""Manual pick Run's side panel: the work, and Points beside it or under it.

Two columns of `COLUMN` px, the work's cards and Points at their full height,
from a window of about 1600 px; under that one column of `ONE_COLUMN` px, with
Points a card after the work's, folded by default (MANUAL.md §1). The widths are
fixed: the picture takes the rest (`FeedRow`), so what a card shows moves no
column, and no card is ever under 380 px.

Each column is a `scroll_column`, its scroll bar's room kept beside the cards,
so a bar coming moves nothing.

`FeedRow` asks `width_in` for the panel's width at the row's, and gives it with
`set_outer_width`, which is what changes the arrangement. The page hears of it
by `two_columns_changed`: Points' list fills a column of its own, and is as tall
as it was built under the work. `last` stays at the end of the work's column,
under Points there: a jog panel, whose pad is over the picture.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ..theme.factory import scroll_column
from .fold_card import FoldCard

__all__ = ["COLUMN", "COLUMN_GAP", "ONE_COLUMN", "TWO_COLUMNS_FROM",
           "WorkColumns"]

COLUMN = 400
ONE_COLUMN = 420
COLUMN_GAP = 16
CARD_GAP = 12
# The row's width: a window of 1600 less the page's margins.
TWO_COLUMNS_FROM = 1560


def _column(parent: QWidget) -> tuple[QWidget, QVBoxLayout]:
    inner = QWidget(parent)
    layout = QVBoxLayout(inner)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(CARD_GAP)
    return inner, layout


class WorkColumns(QWidget):
    two_columns_changed = Signal(bool)

    def __init__(self, cards, points: FoldCard,
                 parent: QWidget | None = None, *,
                 last: QWidget | None = None):
        super().__init__(parent)
        self._points = points
        self._two: bool | None = None

        work, self._work = _column(self)
        for card in cards:
            self._work.addWidget(card)
        self._points_at = self._work.count()
        if last is not None:
            self._work.addWidget(last)
        self._work.addStretch(1)
        beside, self._beside = _column(self)

        self._work_area = scroll_column(work, ONE_COLUMN)
        self._beside_area = scroll_column(beside, COLUMN)
        self._bar = self._work_area.verticalScrollBar().sizeHint().width()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(COLUMN_GAP)
        row.addWidget(self._work_area)
        row.addWidget(self._beside_area)
        self._arrange(False)

    @property
    def two_columns(self) -> bool:
        return bool(self._two)

    def width_in(self, row_width: int) -> int:
        """The panel's width, scroll bars' room included, in a row this wide."""
        if row_width >= TWO_COLUMNS_FROM:
            return 2 * (COLUMN + self._bar) + COLUMN_GAP
        return ONE_COLUMN + self._bar

    def set_outer_width(self, width: int) -> None:
        self._arrange(width > ONE_COLUMN + self._bar)
        self.setFixedWidth(width)

    def _arrange(self, two: bool) -> None:
        if two == self._two:
            return
        first = self._two is None
        self._two = two
        if two:
            self._work.removeWidget(self._points)
            self._beside.addWidget(self._points)
        else:
            self._beside.removeWidget(self._points)
            self._work.insertWidget(self._points_at, self._points)
        self._points.show()
        self._points.set_folds(not two)
        self._beside_area.setVisible(two)
        self._work_area.set_outer_width(
            (COLUMN if two else ONE_COLUMN) + self._bar)
        if not first:
            self.two_columns_changed.emit(two)

    def sizeHint(self) -> QSize:
        return QSize(ONE_COLUMN + self._bar, 700)
