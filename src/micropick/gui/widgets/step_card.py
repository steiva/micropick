"""One numbered step of a procedure page, as a card with a mark.

The calibration pages are a list of steps in the order they are done, each
a card whose heading carries its number and a mark - done, to do, or
waiting for a step above it (the marks of `widgets.checklist`) - and whose
body the page fills: what to do, in a sentence, and the controls that do
it. A step that waits has its heading dimmed, and the mark's tooltip says
what it waits for.
"""

from __future__ import annotations

import qtawesome as qta
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ..theme.factory import card, heading
from .checklist import BLOCKED, COLOURS, ICONS, TODO

__all__ = ["StepCard", "wrapped"]

MARK_PX = 18


def wrapped(text: str = "") -> QLabel:
    """A sentence that wraps."""
    label = QLabel(text)
    label.setWordWrap(True)
    return label


class StepCard:
    """The card is `box`; `add` and `add_layout` fill it."""

    def __init__(self, parent: QWidget, number: int, title: str):
        self.box = card(parent)
        row = QHBoxLayout()
        self.mark = QLabel(parent)
        row.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignVCenter)
        self.heading = heading(f"{number}. {title}", 2)
        self.heading.setWordWrap(True)
        row.addWidget(self.heading, 1)
        self.box.layout().addLayout(row)
        self.state = None
        self.set_state(TODO)

    def add(self, widget: QWidget) -> None:
        self.box.layout().addWidget(widget)

    def add_layout(self, layout) -> None:
        self.box.layout().addLayout(layout)

    def set_state(self, state: str, why: str = "") -> None:
        """OK, TODO or BLOCKED; `why` is the mark's tooltip."""
        self.mark.setToolTip(why)
        if state == self.state:
            return
        self.state = state
        self.mark.setPixmap(qta.icon(ICONS[state], color=COLOURS[state])
                            .pixmap(MARK_PX, MARK_PX))
        # A step that waits for one above it reads as waiting.
        self.heading.setEnabled(state != BLOCKED)
