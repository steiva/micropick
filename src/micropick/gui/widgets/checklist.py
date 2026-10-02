"""What has to be true before a run, one line each, with a way to fix it.

A run that cannot start used to say why in a paragraph of bullets: correct,
and a list to read rather than a list to work down. Here each condition is
a line with a mark - done, to do, or a note worth reading - and the ones
not done carry a button to the page where they are done. Only "to do"
stops a run; a note (an old calibration, a dish bottom never measured
here) is said and left to the operator.

The page builds the list (`Check`), this only shows it; the button asks
for the page by name, and the window shows it (`Session.page_requested`).
"""

from __future__ import annotations

from dataclasses import dataclass

import qtawesome as qta
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QGridLayout, QLabel, QPushButton, QWidget)

__all__ = ["Check", "Checklist", "OK", "TODO", "NOTE"]

OK, TODO, NOTE = "ok", "todo", "note"

# Fixed colours, as the status bar's: a mark has to read in either theme.
COLOURS = {OK: "#3fb950", TODO: "#e5534b", NOTE: "#f0a030"}
ICONS = {OK: "mdi6.check-circle", TODO: "mdi6.close-circle",
         NOTE: "mdi6.alert-circle"}
ICON_PX = 16


@dataclass(frozen=True)
class Check:
    """One condition. `page` is where it is done (a name in `shell.PAGES`),
    `place` the words on its button; None for what is done on this page."""

    text: str
    state: str = OK
    page: str | None = None
    place: str = ""

    @property
    def blocking(self) -> bool:
        return self.state == TODO


class Checklist(QWidget):
    go = Signal(str)                   # a page name, from a row's button

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
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
        for row, check in enumerate(checks):
            mark = QLabel(self)
            mark.setPixmap(qta.icon(ICONS[check.state],
                                    color=COLOURS[check.state])
                           .pixmap(ICON_PX, ICON_PX))
            mark.setAlignment(Qt.AlignmentFlag.AlignTop)
            self._grid.addWidget(mark, row, 0, Qt.AlignmentFlag.AlignTop)
            text = QLabel(check.text, self)
            text.setWordWrap(True)
            text.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            self._grid.addWidget(text, row, 1)
            if check.state != OK and check.page:
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
                                     Qt.AlignmentFlag.AlignTop)

    @property
    def checks(self) -> list[Check]:
        return list(self._shown)
