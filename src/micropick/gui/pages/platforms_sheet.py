"""Robot setup's platforms sheet (REDESIGN §5, #12): what stands in each
slot of the deck.

A sheet over whatever is shown, opened from the Platforms row: the deck
as a big 3×4 grid of slots (10 11 12 at the back, 1 2 3 at the front)
on the left, and for the slot clicked "What stands in this slot?" on
the right - one of the profile's module types (Picking platform,
Calibration module) or Nothing. Slot 12 is the fixed trash and takes
none.

The choices are a draft until Done: Done saves the deck modules
(`Session.set_deck_modules`, which registers their offsets with the
robot), Cancel and × save nothing. A slot keeps the height of the module
it had while its type is not changed; a slot given a type takes that
type's usual height. Done is gray while anything has the robot: saving
registers the offsets in the robot session.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QPushButton,
                               QVBoxLayout, QWidget)

from ...config.schema import DeckModule
from ..session import Session
from ..tasks import SETUP_TITLES
from ..theme import COLORS
from ..theme.factory import caption, heading, muted_label, select
from ..widgets.mini_deck import DECK_ROWS
from ..widgets.overlays import SheetWindow
from ..workers import describe_error

__all__ = ["NOTHING", "PlatformsSheet"]

log = logging.getLogger(__name__)

TITLE = SETUP_TITLES["platforms"]

PANEL_WIDTH = 320
TILE_MIN = (150, 96)
TRASH_SLOT = 12
NOTHING = "Nothing"
HINT = "Platforms only differ in height."


class PlatformsSheet(SheetWindow):
    """`open_sheet()`; `said(text)` after Done. `slot` is the slot chosen,
    `tiles` the deck's buttons by slot."""

    said = Signal(str)

    def __init__(self, session: Session, host: QWidget):
        super().__init__(host, TITLE)
        self.session = session
        self.slot: int | None = None
        # slot -> the module standing there, as it will be saved.
        self._draft: dict[int, DeckModule] = {}

        row = QHBoxLayout()
        row.setSpacing(24)
        grid = QGridLayout()
        grid.setSpacing(10)
        self.tiles: dict[int, QPushButton] = {}
        for r, slots in enumerate(DECK_ROWS):
            for c, slot in enumerate(slots):
                tile = QPushButton(self.body_widget)
                tile.setMinimumSize(*TILE_MIN)
                tile.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                tile.setEnabled(slot != TRASH_SLOT)
                tile.clicked.connect(lambda _=False, s=slot: self._choose(s))
                grid.addWidget(tile, r, c)
                self.tiles[slot] = tile
        deck = QWidget(self.body_widget)
        deck.setLayout(grid)
        row.addWidget(deck, 1)

        panel = QVBoxLayout()
        panel.setSpacing(8)
        self.slot_caption = caption("", self.body_widget)
        panel.addWidget(self.slot_caption)
        panel.addWidget(heading("What stands in this slot?", 3,
                                self.body_widget))
        self.kind = select(self.body_widget)
        self.kind.currentTextChanged.connect(self._kind_chosen)
        panel.addWidget(self.kind)
        hint = muted_label(HINT, self.body_widget)
        hint.setWordWrap(True)
        panel.addWidget(hint)
        panel.addStretch(1)
        holder = QWidget(self.body_widget)
        holder.setLayout(panel)
        holder.setFixedWidth(PANEL_WIDTH)
        row.addWidget(holder)
        self.body.addLayout(row, 1)

        self.done.connect(self._save)
        session.robot_busy_changed.connect(lambda _h: self._show_done())

    # -- opening -------------------------------------------------------

    def open_sheet(self) -> None:
        """The deck as the profile has it; the first slot with a platform
        chosen, else the first slot."""
        profile = self.session.profile
        modules = profile.deck.modules if profile is not None else []
        self._draft = {slot: module.model_copy(update={"slots": [slot]})
                       for module in modules for slot in module.slots}
        kinds = ([t.name for t in profile.deck.module_types]
                 if profile is not None else [])
        self.kind.blockSignals(True)
        self.kind.clear()
        self.kind.addItems(kinds + [NOTHING])
        self.kind.blockSignals(False)
        self._choose(min(self._draft, default=1))
        self._show_done()
        self.open()

    def _choose(self, slot: int) -> None:
        self.slot = slot
        self.slot_caption.setText(f"Slot {slot}")
        module = self._draft.get(slot)
        self.kind.blockSignals(True)
        self.kind.setCurrentText(module.name if module is not None
                                 else NOTHING)
        self.kind.blockSignals(False)
        self._show_tiles()

    def _kind_chosen(self, name: str) -> None:
        profile, slot = self.session.profile, self.slot
        if profile is None or slot is None or not name:
            return
        if name == NOTHING:
            self._draft.pop(slot, None)
        elif (slot not in self._draft
              or self._draft[slot].name != name):
            kind = profile.deck.module_type(name)
            height = kind.height_mm if kind is not None else 0.0
            self._draft[slot] = DeckModule(slots=[slot],
                                           offset=[0.0, 0.0, height],
                                           name=name)
        self._show_tiles()

    # -- display -------------------------------------------------------

    def _show_tiles(self) -> None:
        for slot, tile in self.tiles.items():
            module = self._draft.get(slot)
            name = ("Trash" if slot == TRASH_SLOT else
                    module.name if module is not None else NOTHING)
            tile.setText(f"SLOT {slot}\n{name}")
            chosen = slot == self.slot
            border = COLORS["accent"] if chosen else COLORS["line"]
            style = "dashed" if module is not None else "solid"
            tile.setStyleSheet(
                f"QPushButton {{ border: {2 if chosen else 1}px {style} "
                f"{border}; border-radius: 12px; font-weight: 600; }}")

    def _show_done(self) -> None:
        lease = self.session.robot_lease
        self.done_button.setEnabled(not lease.busy)
        self.done_button.setToolTip(
            f"Robot busy: {lease.holder}." if lease.busy else "")

    # -- closing -------------------------------------------------------

    def _save(self) -> None:
        profile = self.session.profile
        if profile is None:
            return
        modules = [self._draft[slot] for slot in sorted(self._draft)]
        before = {slot: (m.name, tuple(m.offset))
                  for m in profile.deck.modules for slot in m.slots}
        after = {m.slots[0]: (m.name, tuple(m.offset)) for m in modules}
        if before == after:
            return
        try:
            self.session.set_deck_modules(modules)
        except Exception as exc:                     # noqa: BLE001
            log.error("platforms not saved: %s", exc)
            self.said.emit(describe_error(exc))
            return
        self.said.emit("Platforms saved.")

    def discard(self) -> None:
        """Closed by going elsewhere: as ×, nothing is saved."""
        if not self.isHidden():
            self.hide()
