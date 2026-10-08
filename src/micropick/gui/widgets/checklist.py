"""What has to be true before a run, one line each, with a way to fix it.

A run that cannot start used to say why in a paragraph of bullets: correct,
and a list to read rather than a list to work down. Here each step is a
line with a mark - done, to do, a note worth reading, or blocked - and a
button to the page where it is done, whatever its mark.

The lines do not change. Each says what to do, in the same words whether it
is done or not; only its mark moves. A list whose sentences rewrite
themselves as the work goes on reads as a different list every time it is
looked at. What is particular to the moment - a date, why a step is not
done - is the line's tooltip (`Check.detail`).

A step that needs an earlier one first is *blocked*: its text is dimmed and
the eye goes past it to the first step that can be done.
Blocked and to do stop a run; a note (an old calibration, a dish bottom
never measured here) is said and left to the operator, and an optional step
never stops one.

The page builds the list (`Check`), this only shows it; the button asks
for the page by name, and the window shows it (`Session.page_requested`).
"""

from __future__ import annotations

from dataclasses import dataclass

import qtawesome as qta
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QGridLayout, QLabel, QPushButton, QWidget)

__all__ = ["Check", "Checklist", "OK", "TODO", "NOTE", "BLOCKED"]

OK, TODO, NOTE, BLOCKED = "ok", "todo", "note", "blocked"

# Fixed colours, as the status bar's: a mark has to read in either theme.
COLOURS = {OK: "#3fb950", TODO: "#e5534b", NOTE: "#f0a030",
           BLOCKED: "#8b949e"}
ICONS = {OK: "mdi6.check-circle", TODO: "mdi6.close-circle",
         NOTE: "mdi6.alert-circle", BLOCKED: "mdi6.lock-outline"}
ICON_PX = 16


@dataclass(frozen=True)
class Check:
    """One step. `page` is where it is done (a name in `shell.PAGES`),
    `place` the words on its button; None for what is done on this page.
    `detail` is the tooltip: what is particular to now. An `optional` step
    never stops a run, whatever its mark."""

    text: str
    state: str = OK
    page: str | None = None
    place: str = ""
    detail: str = ""
    optional: bool = False

    @property
    def blocking(self) -> bool:
        return self.state in (TODO, BLOCKED) and not self.optional


class Checklist(QWidget):
    go = Signal(str)                   # a page name, from a row's button

    def __init__(self, parent: QWidget | None = None, *,
                 numbered: bool = False):
        super().__init__(parent)
        self._numbered = numbered
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(4)
        self._grid.setColumnStretch(1, 1)
        self._shown: list[Check] = []

    def show_checks(self, checks: list[Check]) -> None:
        """Rebuilt only when the list changed: it is refreshed on every
        event of the page, and the rows are widgets."""
        checks = list(checks)
        if checks == self._shown:
            return
        self._shown = checks
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget() is not None:
                # Hidden at once: deletion waits for the event loop, and
                # until then the old row is painted under the new one.
                item.widget().hide()
                item.widget().deleteLater()
        # Every row as tall as one with a button, so the list keeps an even
        # step as buttons come and go.
        probe = QPushButton("x")              # never shown
        probe.setFlat(True)
        row_height = probe.sizeHint().height()
        probe.deleteLater()
        for row, check in enumerate(checks):
            self._grid.setRowMinimumHeight(row, row_height)
            mark = QLabel(self)
            mark.setPixmap(qta.icon(ICONS[check.state],
                                    color=COLOURS[check.state])
                           .pixmap(ICON_PX, ICON_PX))
            mark.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            self._grid.addWidget(mark, row, 0, Qt.AlignmentFlag.AlignVCenter)
            words = (f"{row + 1}. {check.text}" if self._numbered
                     else check.text)
            if check.optional:
                words += " (optional)"
            text = QLabel(words, self)
            text.setWordWrap(True)
            text.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            # Disabled is the dimmer ink in either theme; see
            # `theme.factory.muted_label`.
            text.setEnabled(check.state != BLOCKED)
            # On the mark too, which is never disabled: a disabled widget's
            # tooltip is not to be relied on.
            for widget in (mark, text):
                widget.setToolTip(check.detail)
            self._grid.addWidget(text, row, 1)
            # Every step that is done on another page links to it, done or
            # not: a done step is often the one to look at again.
            if check.page:
                # "&&": a single & is a button's mnemonic, and "Robot &
                # Deck" would read "Robot _Deck".
                button = QPushButton(f"{check.place.replace('&', '&&')} ›",
                                     self)
                button.setFlat(True)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.setToolTip(f"Go to the {check.place} page")
                button.clicked.connect(
                    lambda _c=False, page=check.page: self.go.emit(page))
                self._grid.addWidget(button, row, 2,
                                     Qt.AlignmentFlag.AlignVCenter)

    @property
    def checks(self) -> list[Check]:
        return list(self._shown)
