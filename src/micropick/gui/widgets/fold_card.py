"""Cards whose body folds, one open at a time (REDESIGN §3, Card / FoldCard).

A FoldCard is a header row - chevron, title, and on the right a summary
("250–500 µm", "12 of 24 wells") that stays readable while folded - over
a body. In a `FoldGroup` one card is open at a time: opening one folds
the others, and the page opens the one needed next (Sizes before a run,
Plate during it). That is how a side panel avoids scrolling (§1.6).

`fold=False` is the same card that does not fold: no chevron, the body
always shown - Picking's two cards, both seen at once (#52).
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QSizePolicy,
                               QToolButton, QVBoxLayout, QWidget)

from ..theme import COLORS, SPACING
from ..theme.icons import icon

__all__ = ["FoldCard", "FoldGroup"]


class FoldCard(QFrame):
    """`toggled(open)` when the header is clicked or `set_open` changes it."""

    toggled = Signal(bool)

    def __init__(self, title: str, *, summary: str = "", open: bool = False,
                 fold: bool = True, parent: QWidget | None = None):
        super().__init__(parent)
        self._fold = fold
        self.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACING * 2, 10, SPACING * 2, 10)
        outer.setSpacing(SPACING)

        header = QHBoxLayout()
        header.setSpacing(8)
        self.header = QToolButton(self)
        self.header.setObjectName("foldHeader")
        self.header.setText(title)
        self.header.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setIconSize(QSize(16, 16))
        self.header.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if fold:
            self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding,
                                  QSizePolicy.Policy.Preferred)
        self.header.clicked.connect(self._header_clicked)
        header.addWidget(self.header, 1)
        # Not folding, the title is a label: a tool button with no icon
        # centers its text.
        self.title = QLabel(title, self)
        self.title.setObjectName("heading3")
        self.title.setVisible(not fold)
        self.header.setVisible(fold)
        header.addWidget(self.title, 1)
        self.summary = QLabel(summary, self)
        self.summary.setObjectName("foldSummary")
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        header.addWidget(self.summary)
        outer.addLayout(header)

        self.body = QWidget(self)
        self.body.setObjectName("plain")
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 4, 0, 4)
        body.setSpacing(SPACING)
        outer.addWidget(self.body)
        self._open = not open
        self.set_open(open or not fold)

    def _header_clicked(self) -> None:
        if self._fold:
            self.set_open(not self._open)

    def is_open(self) -> bool:
        return self._open

    def set_open(self, open: bool) -> None:
        open = open or not self._fold
        if open == self._open:
            return
        self._open = open
        if self._fold:
            self.header.setIcon(icon("chevron-down" if open
                                     else "chevron-right",
                                     COLORS["text-2"], 16, stroke=2))
        self.body.setVisible(open)
        self.toggled.emit(open)

    def set_summary(self, text: str) -> None:
        self.summary.setText(text)

    def set_title(self, text: str) -> None:
        self.header.setText(text)
        self.title.setText(text)

    def mouseReleaseEvent(self, event) -> None:
        # The whole header row folds, summary included.
        if (self._fold and event.button() == Qt.MouseButton.LeftButton
                and event.position().y() < self.header.geometry().bottom()
                + 4):
            self.set_open(not self._open)
        super().mouseReleaseEvent(event)


class FoldGroup:
    """Keeps one of its cards open. Folding the open one leaves none."""

    def __init__(self, cards: list[FoldCard] | None = None):
        self.cards: list[FoldCard] = []
        for card in cards or []:
            self.add(card)

    def add(self, card: FoldCard) -> FoldCard:
        self.cards.append(card)
        card.toggled.connect(lambda open, c=card: self._toggled(c, open))
        return card

    def open(self, card: FoldCard) -> None:
        card.set_open(True)

    def _toggled(self, card: FoldCard, open: bool) -> None:
        if not open:
            return
        for other in self.cards:
            if other is not card:
                other.set_open(False)
