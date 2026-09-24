"""A side panel's cards in two columns when there is room, one when not.

`FeedRow` gives the panel whatever the picture leaves over, up to twice the
width it was designed for, and one column of cards stretched across that is
a column of short lines with a long way down. Two columns of at least
`COLUMN_MIN` fit from about 620 px; below that the cards stack as before.

The order is kept - reading down the left column and then down the right -
and the split is the point that makes the two columns most nearly the same
height at the width they will have. It is chosen when the arrangement
changes, not on every resize, so the cards do not hop between columns while
a window is being dragged.

This is the inner widget of `theme.factory.scroll_column`, which measures
its height for its width; a pair of box layouts answers that like one does.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ..theme import SPACING

__all__ = ["CardColumns", "COLUMN_MIN"]

COLUMN_MIN = 300


class CardColumns(QWidget):
    def __init__(self, cards, parent: QWidget | None = None, *,
                 column_min: int = COLUMN_MIN):
        super().__init__(parent)
        self._cards = list(cards)
        self._column_min = column_min
        self._two: bool | None = None

        self._outer = QHBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._columns = []
        for _ in range(2):
            column = QVBoxLayout()
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(SPACING)
            self._outer.addLayout(column, 1)
            self._columns.append(column)
        for card in self._cards:
            card.setParent(self)
        self._arrange(False)

    @property
    def two_columns(self) -> bool:
        return bool(self._two)

    def _fits_two(self, width: int) -> bool:
        return width >= 2 * self._column_min + SPACING

    def _split(self, column_width: int) -> int:
        """How many cards go on the left: the most even split, order kept."""
        heights = []
        for card in self._cards:
            height = (card.heightForWidth(column_width)
                      if card.hasHeightForWidth() else -1)
            heights.append(height if height > 0 else card.sizeHint().height())
        total = sum(heights)
        best, best_k, left = None, 1, 0
        for k in range(1, len(heights)):
            left += heights[k - 1]
            worst = max(left, total - left)
            if best is None or worst < best:
                best, best_k = worst, k
        return best_k

    def _arrange(self, two: bool) -> None:
        self._two = two
        for column in self._columns:
            while column.count():
                column.takeAt(0)
        if two and len(self._cards) > 1:
            k = self._split((self.width() - SPACING) // 2)
            groups = (self._cards[:k], self._cards[k:])
        else:
            groups = (self._cards, [])
        # An empty right column is an empty layout, which the outer layout
        # skips, spacing and all.
        self._outer.setSpacing(SPACING if groups[1] else 0)
        for column, cards in zip(self._columns, groups):
            for card in cards:
                column.addWidget(card)
            if cards:
                column.addStretch(1)
        self._outer.invalidate()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        two = self._fits_two(self.width())
        if two != self._two:
            self._arrange(two)
