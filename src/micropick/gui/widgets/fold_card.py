"""Cards whose body folds, one open at a time (REDESIGN §3, Card / FoldCard).

A FoldCard is a header row - chevron, title, and on the right a summary
("250–500 µm", "12 of 24 wells") that stays readable while folded - over
a body. In a `FoldGroup` one card is open at a time: opening one folds
the others, and the page opens the one needed next (Sizes before a run,
Plate during it). That is how a side panel avoids scrolling (§1.6).

`fold=False` is the same card that does not fold: no chevron, the body
always shown - Picking's two cards, both seen at once (#52).

An `ActionCard` has what it is for in the header: a number, the title, under it
a summary of the whole action, cut to its one line, and on the right the button
(MANUAL.md §4.1). The button works with the card folded and the body holds only
settings: Manual pick's Aspirate and Dispense. Open, its border takes its color.

A card folding gives up the keyboard. Left to Qt, a field folded away passes it
to the next one the window finds, which the operator never chose, and the
robot's keys go with it (`jog_panel.TYPING_HELP`).
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                               QPushButton, QSizePolicy, QToolButton,
                               QVBoxLayout, QWidget)

from ..theme import COLORS, SPACING
from ..theme.factory import laid_out_at_once, primary_button
from ..theme.icons import icon

__all__ = ["ActionCard", "FoldCard", "FoldGroup"]

BADGE_PX = 22


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
        self.header_row = header
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
        self._fill_header(header, title, summary)
        outer.addLayout(header)

        self.body = QWidget(self)
        self.body.setObjectName("plain")
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 4, 0, 4)
        body.setSpacing(SPACING)
        outer.addWidget(self.body)
        self._open = not open
        self.set_open(open or not fold)

    def _fill_header(self, header: QHBoxLayout, title: str,
                     summary: str) -> None:
        header.addWidget(self.header, 1)
        # Not folding, the title is a label: a tool button with no icon
        # centers its text.
        self.title = QLabel(title, self)
        self.title.setObjectName("heading3")
        # A card stretched taller than its body (one beside a taller one)
        # keeps its title on top: the label takes none of the height.
        self.title.setSizePolicy(QSizePolicy.Policy.Preferred,
                                 QSizePolicy.Policy.Fixed)
        self.title.setVisible(not self._fold)
        self.header.setVisible(self._fold)
        header.addWidget(self.title, 1)
        self.summary = QLabel(summary, self)
        self.summary.setObjectName("foldSummary")
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        header.addWidget(self.summary)

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
        focus = QApplication.focusWidget()
        if not open and self.body.isAncestorOf(focus):
            focus.clearFocus()
        with laid_out_at_once(self):
            self.body.setVisible(open)
            self.toggled.emit(open)

    def set_folds(self, fold: bool) -> None:
        """Whether the card folds from now, folded if it does: Manual pick's
        Points, a card under the work and a column of its own beside it."""
        self._fold = fold
        self.header.setVisible(fold)
        self.title.setVisible(not fold)
        if fold:
            self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        # The opposite of what it becomes, so that set_open draws it.
        self._open = fold
        self.set_open(False)

    def set_summary(self, text: str) -> None:
        self.summary.setText(text)

    def set_title(self, text: str) -> None:
        self.header.setText(text)
        self.title.setText(text)

    def mouseReleaseEvent(self, event) -> None:
        # The whole header row folds, summary included, but for a button: a
        # gray one passes its press on to here.
        at = event.position().toPoint()
        if (self._fold and event.button() == Qt.MouseButton.LeftButton
                and at.y() < self.header_row.geometry().bottom() + 4
                and not isinstance(self.childAt(at), QPushButton)):
            self.set_open(not self._open)
        super().mouseReleaseEvent(event)


class _Summary(QLabel):
    """One line cut to the room it is given, the whole of it in the tooltip:
    what it says never widens the card."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("foldSummary")
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Preferred)
        self.full = ""

    def set_full(self, text: str) -> None:
        self.full = text
        self.setToolTip(text)
        self._cut()

    def _cut(self) -> None:
        self.setText(self.fontMetrics().elidedText(
            self.full, Qt.TextElideMode.ElideRight, self.width()))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._cut()


def _repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class ActionCard(FoldCard):
    """`button` is the card's action, primary at first; `number` and `color`
    (a labware kind's: "tips", "plate") its badge and its open border."""

    def __init__(self, title: str, number: int, color: str, *,
                 open: bool = False, parent: QWidget | None = None):
        super().__init__(title, open=open, parent=parent)
        self.badge.setText(str(number))
        for widget in (self, self.badge):
            widget.setProperty("tint", color)
        self.toggled.connect(self._show_open)
        self._show_open(self.is_open())
        self.set_summary("")

    def _fill_header(self, header: QHBoxLayout, title: str,
                     summary: str) -> None:
        # Only the chevron: the title is over the summary.
        self.header.setText("")
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.header.setSizePolicy(QSizePolicy.Policy.Fixed,
                                  QSizePolicy.Policy.Preferred)
        header.addWidget(self.header)
        self.badge = QLabel(self)
        self.badge.setObjectName("cardBadge")
        self.badge.setFixedSize(BADGE_PX, BADGE_PX)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.badge)
        self.title = QLabel(title, self)
        self.title.setObjectName("actionTitle")
        self.summary = _Summary(self)
        words = QVBoxLayout()
        words.setSpacing(0)
        words.addWidget(self.title)
        words.addWidget(self.summary)
        header.addLayout(words, 1)
        self.button = primary_button(title, self)
        self.button.setProperty("cardAction", True)
        # No focus to lose: grayed for its job, it would pass the keyboard,
        # and the robot's keys, to the next field.
        self.button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        header.addWidget(self.button)

    def _show_open(self, open: bool) -> None:
        self.setProperty("open", open)
        _repolish(self)

    def set_summary(self, text: str, *, warn: bool = False) -> None:
        self.summary.set_full(text)
        if self.summary.property("warn") is not warn:
            self.summary.setProperty("warn", warn)
            _repolish(self.summary)


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
