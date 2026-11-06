"""A path: what is gone through once, in order, without numbers (TIP.md §2.2).

A vertical line of nodes, one per `PathItem`, each done, current or later.
Order is shown by position. Only the current item has a body - a sentence
and one button, which the page owns, so the page's logic stays in the page:
a done item is folded to its result, with a link on the right if it has one,
and a later item is its task, muted. "Not done yet" is the later state and
never a red mark.

The page says where the path is (`set_current`, `set_all_done`,
`set_all_later`) each time it refreshes: the widget remembers nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from ..theme import COLORS
from ..theme.factory import laid_out_at_once

__all__ = ["DONE", "CURRENT", "LATER", "PathItem", "PathSteps"]

DONE, CURRENT, LATER = "done", "current", "later"

NODE_PX = 22
LINE_PX = 2
GAP_PX = 14            # the node to the title
UNDER_CURRENT_PX = 22
UNDER_OTHERS_PX = 14


@dataclass
class PathItem:
    """`body_widget` is shown while the item is current, `link_text` is a
    link on the right while it is done."""
    key: str
    task_title: str
    done_title: str
    body_widget: QWidget | None = None
    link_text: str | None = None


class _Node(QWidget):
    """The mark, and the line down to the next one."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.state = LATER
        self.last = False
        self.setFixedWidth(NODE_PX)
        self.setSizePolicy(QSizePolicy.Policy.Fixed,
                           QSizePolicy.Policy.Expanding)

    def show_as(self, state: str, last: bool) -> None:
        self.state, self.last = state, last
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        done = self.state == DONE
        if not self.last:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(COLORS["path-line-done" if done
                                           else "path-line"]))
            painter.drawRoundedRect(
                QRectF((NODE_PX - LINE_PX) / 2, NODE_PX + 5, LINE_PX,
                       max(0, self.height() - NODE_PX - 9)), 1, 1)
        inset = {DONE: 0.75, CURRENT: 1.0, LATER: 1.0}[self.state]
        rect = QRectF(1, 1, NODE_PX - 2, NODE_PX - 2).adjusted(
            inset - 1, inset - 1, 1 - inset, 1 - inset)
        ring, width, fill = {
            DONE: ("ok-border", 1.5, "ok-bg"),
            CURRENT: ("accent", 2.0, "selected-bg"),
            LATER: ("path-ring", 2.0, None)}[self.state]
        painter.setPen(QPen(QColor(COLORS[ring]), width))
        if fill is None:
            painter.setBrush(Qt.BrushStyle.NoBrush)
        else:
            painter.setBrush(QColor(COLORS[fill]))
        painter.drawEllipse(rect)
        middle = QPointF(NODE_PX / 2, NODE_PX / 2)
        if self.state == CURRENT:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(COLORS["accent"]))
            painter.drawEllipse(middle, 4, 4)
        elif done:
            tick = QPainterPath(middle + QPointF(-4, 0))
            tick.lineTo(middle + QPointF(-1, 3))
            tick.lineTo(middle + QPointF(4, -3))
            pen = QPen(QColor(COLORS["ok"]), 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(tick)
        painter.end()


class _Row:
    """One item's widgets."""

    def __init__(self, item: PathItem, parent: QWidget, clicked):
        self.item = item
        self.state = LATER
        self.hidden = False
        self.box = QWidget(parent)
        self.box.setObjectName("plain")
        row = QHBoxLayout(self.box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(GAP_PX)
        self.node = _Node(self.box)
        row.addWidget(self.node)

        self.content = QVBoxLayout()
        self.content.setContentsMargins(0, 0, 0, UNDER_OTHERS_PX)
        self.content.setSpacing(8)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel(item.task_title, self.box)
        self.title.setObjectName("pathTitle")
        self.title.setWordWrap(True)
        head.addWidget(self.title, 1, Qt.AlignmentFlag.AlignTop)
        self.link = None
        if item.link_text:
            self.link = QPushButton(item.link_text, self.box)
            self.link.setObjectName("pathLink")
            self.link.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.link.setCursor(Qt.CursorShape.PointingHandCursor)
            self.link.clicked.connect(clicked)
            self.link.hide()
            head.addWidget(self.link, 0, Qt.AlignmentFlag.AlignTop)
        self.content.addLayout(head)
        if item.body_widget is not None:
            item.body_widget.setParent(self.box)
            item.body_widget.hide()
            self.content.addWidget(item.body_widget)
        row.addLayout(self.content, 1)

    def show_as(self, state: str, last: bool) -> None:
        self.state = state
        item = self.item
        self.node.show_as(state, last)
        self.title.setText(item.done_title if state == DONE
                           else item.task_title)
        if self.title.property("state") != state:
            self.title.setProperty("state", state)
            self.title.style().unpolish(self.title)
            self.title.style().polish(self.title)
        if self.link is not None:
            self.link.setVisible(state == DONE)
        if item.body_widget is not None:
            item.body_widget.setVisible(state == CURRENT)
        self.content.setContentsMargins(
            0, 0, 0, 0 if last else UNDER_CURRENT_PX if state == CURRENT
            else UNDER_OTHERS_PX)


class PathSteps(QWidget):
    link_clicked = Signal(str)            # the item's key

    def __init__(self, items: list[PathItem],
                 parent: QWidget | None = None):
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self._rows: dict[str, _Row] = {}
        for item in items:
            row = _Row(item, self, lambda _c=False, key=item.key:
                       self.link_clicked.emit(key))
            self._rows[item.key] = row
            column.addWidget(row.box)
        self.set_all_later()

    def state(self, key: str) -> str:
        return self._rows[key].state

    def title(self, key: str) -> str:
        return self._rows[key].title.text()

    def link(self, key: str) -> QPushButton | None:
        """The item's link, for the page to enable."""
        return self._rows[key].link

    @property
    def current(self) -> str | None:
        return next((key for key, row in self._rows.items()
                     if row.state == CURRENT), None)

    def set_hidden(self, key: str, hidden: bool) -> None:
        """An item that does not apply now: not on the path, the line ending
        at the one before it."""
        row = self._rows[key]
        if row.hidden == hidden:
            return
        row.hidden = hidden
        row.box.setVisible(not hidden)
        self._show({k: r.state for k, r in self._rows.items()})

    def set_current(self, key: str) -> None:
        """`key` is current, the items before it done, those after it later."""
        keys = list(self._rows)
        at = keys.index(key)
        self._show({k: DONE if i < at else CURRENT if i == at else LATER
                    for i, k in enumerate(keys)})

    def set_all_done(self) -> None:
        self._show(dict.fromkeys(self._rows, DONE))

    def set_all_later(self) -> None:
        self._show(dict.fromkeys(self._rows, LATER))

    def _show(self, states: dict[str, str]) -> None:
        shown = [key for key, row in self._rows.items() if not row.hidden]
        with laid_out_at_once(self):
            for key, row in self._rows.items():
                row.show_as(states[key], bool(shown) and key == shown[-1])
