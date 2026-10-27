"""Robot setup's platforms sheet (REDESIGN §5, #12): what stands in each
slot of the deck.

A sheet over whatever is shown, opened from the Platforms row: the deck
as a big 3×4 grid of slots (10 11 12 at the back, 1 2 3 at the front)
on the left, and for the slot clicked "What stands in this slot?" on
the right - one of the profile's module types (Picking platform,
Calibration module) or Nothing. Slot 12 is the fixed trash and takes
none.

The choices are a draft until Save changes (#30): it saves the deck
modules (`Session.set_deck_modules`, which registers their offsets with
the robot) and the types (`Session.set_module_types`), and says what was
saved ("Saved: slot 9 · Calibration module"). It is gray while the draft
is what is saved, and while anything has the robot: saving registers the
offsets in the robot session. Done and × only close; closed with unsaved
changes, the sheet asks Save / Discard first. There is no Cancel. A slot
keeps the height of the module it had while its type is not changed; a
slot given a type takes that type's usual height; Height changes the
slot's own. New type adds one with its usual height, Delete type takes
the chosen one out of the list - what stands in a slot keeps its name and
height.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLineEdit,
                               QPushButton, QVBoxLayout, QWidget)

from ...config.schema import (DEFAULT_MODULE_HEIGHT_MM, DeckModule,
                              ModuleType)
from ..session import Session
from ..tasks import SETUP_TITLES
from ..theme import COLORS
from ..theme.factory import (caption, compact, double_spin_box, heading,
                             muted_label, primary_button, secondary_button,
                             select)
from ..widgets.mini_deck import DECK_ROWS
from ..widgets.overlays import ConfirmDialog, SheetWindow
from ..workers import describe_error

__all__ = ["NOTHING", "PlatformsSheet"]

log = logging.getLogger(__name__)

TITLE = SETUP_TITLES["platforms"]

PANEL_WIDTH = 320
TILE_MIN = (150, 96)
TRASH_SLOT = 12
NOTHING = "Nothing"
HINT = "Platforms only differ in height."
HEIGHT_RANGE = (0.0, 200.0)


def _height_box(parent: QWidget):
    box = double_spin_box(parent)
    box.setRange(*HEIGHT_RANGE)
    box.setDecimals(2)
    box.setSingleStep(0.1)
    box.setSuffix(" mm")
    return box


class PlatformsSheet(SheetWindow):
    """`open_sheet()`; `said(text)` after a save. `slot` is the slot
    chosen, `tiles` the deck's buttons by slot, `question` the Save /
    Discard asked on closing with changes (None while not asked)."""

    said = Signal(str)

    def __init__(self, session: Session, host: QWidget):
        super().__init__(host, TITLE)
        self.session = session
        self.slot: int | None = None
        # slot -> the module standing there, as it will be saved.
        self._draft: dict[int, DeckModule] = {}
        # The profile's platform types, as they will be saved.
        self._types: list[ModuleType] = []
        self.question: ConfirmDialog | None = None

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
        # The height of what stands in the slot: the type's usual one when
        # it is chosen, then this slot's own.
        self.height_row = QWidget(self.body_widget)
        self.height_row.setObjectName("plain")
        line = QHBoxLayout(self.height_row)
        line.setContentsMargins(0, 0, 0, 0)
        line.addWidget(muted_label("Height", self.height_row))
        self.height = _height_box(self.height_row)
        self.height.valueChanged.connect(self._height_changed)
        line.addWidget(self.height)
        line.addStretch(1)
        # The type chosen, taken out of the list; what stands stays.
        self.delete_button = compact(secondary_button("Delete type",
                                                      self.height_row))
        self.delete_button.setToolTip("Placed platforms are kept.")
        self.delete_button.clicked.connect(self.delete_type)
        line.addWidget(self.delete_button)
        panel.addWidget(self.height_row)
        hint = muted_label(HINT, self.body_widget)
        hint.setWordWrap(True)
        panel.addWidget(hint)
        panel.addSpacing(16)
        panel.addLayout(self._types_box())
        panel.addStretch(1)
        self.saved_note = muted_label("", self.body_widget)
        self.saved_note.setWordWrap(True)
        panel.addWidget(self.saved_note)
        self.save_button = primary_button("Save changes", self.body_widget)
        self.save_button.clicked.connect(self.save)
        panel.addWidget(self.save_button)
        holder = QWidget(self.body_widget)
        holder.setLayout(panel)
        holder.setFixedWidth(PANEL_WIDTH)
        row.addWidget(holder)
        self.body.addLayout(row, 1)

        # Done only closes; Save changes is the panel's one action.
        self.cancel_button.hide()
        self.done_button.setObjectName("secondary")
        self.done_button.setDefault(False)
        self.close_button.setToolTip("Close")
        session.robot_busy_changed.connect(lambda _h: self._show_save())

    def _types_box(self) -> QVBoxLayout:
        """A new type: its name and usual height, a draft until Done."""
        box = QVBoxLayout()
        box.setSpacing(8)
        box.addWidget(caption("New type", self.body_widget))
        self.new_name = QLineEdit(self.body_widget)
        self.new_name.setPlaceholderText("Name, e.g. Tall platform")
        self.new_name.returnPressed.connect(self.add_type)
        box.addWidget(self.new_name)
        line = QHBoxLayout()
        self.new_height = _height_box(self.body_widget)
        self.new_height.setValue(DEFAULT_MODULE_HEIGHT_MM)
        line.addWidget(self.new_height)
        add = compact(secondary_button("Add type", self.body_widget))
        add.clicked.connect(self.add_type)
        line.addWidget(add)
        line.addStretch(1)
        box.addLayout(line)
        self.type_note = muted_label("", self.body_widget)
        box.addWidget(self.type_note)
        return box

    # -- opening -------------------------------------------------------

    def open_sheet(self) -> None:
        """The deck as the profile has it; the first slot with a platform
        chosen, else the first slot."""
        profile = self.session.profile
        modules = profile.deck.modules if profile is not None else []
        self._draft = {slot: module.model_copy(update={"slots": [slot]})
                       for module in modules for slot in module.slots}
        self._types = (list(profile.deck.module_types)
                       if profile is not None else [])
        self.new_name.clear()
        self.type_note.clear()
        self._fill_kinds()
        self.saved_note.clear()
        self._choose(min(self._draft, default=1))
        self._show_save()
        self.open()

    def _fill_kinds(self) -> None:
        """The types, then Nothing. A placed platform whose type was
        deleted keeps its name, so it is offered too."""
        names = [t.name for t in self._types]
        names += sorted({m.name for m in self._draft.values()}
                        - set(names) - {""})
        self.kind.blockSignals(True)
        self.kind.clear()
        self.kind.addItems(names + [NOTHING])
        self.kind.blockSignals(False)

    def _choose(self, slot: int) -> None:
        self.slot = slot
        self.slot_caption.setText(f"Slot {slot}")
        module = self._draft.get(slot)
        self.kind.blockSignals(True)
        self.kind.setCurrentText(module.name if module is not None
                                 else NOTHING)
        self.kind.blockSignals(False)
        self._show_height()
        self._show_tiles()

    def _kind_chosen(self, name: str) -> None:
        slot = self.slot
        if slot is None or not name:
            return
        if name == NOTHING:
            self._draft.pop(slot, None)
        elif (slot not in self._draft
              or self._draft[slot].name != name):
            kind = next((t for t in self._types if t.name == name), None)
            height = kind.height_mm if kind is not None else 0.0
            self._draft[slot] = DeckModule(slots=[slot],
                                           offset=[0.0, 0.0, height],
                                           name=name)
        self._show_height()
        self._show_tiles()
        self._show_save()

    def add_type(self) -> None:
        name = self.new_name.text().strip()
        if not name:
            return
        if any(t.name.lower() == name.lower() for t in self._types):
            self.type_note.setText("There is a type of that name.")
            return
        self._types.append(ModuleType(
            name=name, height_mm=round(self.new_height.value(), 3)))
        self.new_name.clear()
        self.type_note.clear()
        self._fill_kinds()
        self._choose(self.slot)
        self._show_save()

    def delete_type(self) -> None:
        """The type chosen for the slot; what stands anywhere stays."""
        name = self.kind.currentText()
        self._types = [t for t in self._types if t.name != name]
        self._fill_kinds()
        self._choose(self.slot)
        self._show_save()

    def _height_changed(self, value: float) -> None:
        module = self._draft.get(self.slot)
        if module is not None:
            self._draft[self.slot] = module.model_copy(
                update={"offset": [0.0, 0.0, round(value, 3)]})
        self._show_save()

    def _show_height(self) -> None:
        module = self._draft.get(self.slot)
        self.height_row.setVisible(module is not None)
        if module is not None:
            self.height.blockSignals(True)
            self.height.setValue(module.height_mm)
            self.height.blockSignals(False)

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

    def _show_save(self) -> None:
        """Save changes: gray while the draft is what is saved, or while
        anything has the robot (saving registers the offsets)."""
        lease = self.session.robot_lease
        changed = bool(self._changes())
        self.save_button.setEnabled(changed and not lease.busy)
        self.save_button.setToolTip(
            f"Robot busy: {lease.holder}." if lease.busy else "")
        if changed:
            self.saved_note.clear()
        if self.question is not None:
            self.question.safe.setEnabled(not lease.busy)

    # -- saving --------------------------------------------------------

    def _changes(self) -> tuple:
        """(slots whose module differs from the profile's, whether the
        types do); empty while the draft is what is saved."""
        profile = self.session.profile
        if profile is None:
            return ()
        before = {slot: (m.name, tuple(m.offset))
                  for m in profile.deck.modules for slot in m.slots}
        after = {slot: (m.name, tuple(m.offset))
                 for slot, m in self._draft.items()}
        slots = sorted(slot for slot in before.keys() | after.keys()
                       if before.get(slot) != after.get(slot))
        types = self._types != list(profile.deck.module_types)
        return (slots, types) if slots or types else ()

    def save(self) -> bool:
        """Save the draft and say what was saved; False when it failed."""
        changes = self._changes()
        if not changes:
            return True
        slots, types = changes
        modules = [self._draft[slot] for slot in sorted(self._draft)]
        try:
            if types:
                self.session.set_module_types(self._types)
            if slots:
                self.session.set_deck_modules(modules)
        except Exception as exc:                     # noqa: BLE001
            log.error("platforms not saved: %s", exc)
            self.said.emit(describe_error(exc))
            return False
        saved = [f"slot {slot} · "
                 + (self._draft[slot].name if slot in self._draft
                    else NOTHING) for slot in slots]
        if types:
            saved.append("platform types")
        text = "Saved: " + ", ".join(saved)
        self._show_save()
        self.saved_note.setText(text)
        self.said.emit(text + ".")
        return True

    # -- closing -------------------------------------------------------

    def _done(self) -> None:
        self._close(self.done)

    def _cancel(self) -> None:
        self._close(self.canceled)

    def _close(self, signal) -> None:
        """Done and × alike: closed at once while nothing is unsaved,
        else once Save / Discard is answered."""
        if not self._changes():
            self._shut(signal)
            return
        self._ask(signal)

    def _ask(self, signal) -> None:
        """Save / Discard over the window; `signal` (None: the sheet is
        already gone) emitted once it is answered and closed."""
        if self.question is not None:
            return
        self.question = ConfirmDialog(
            self.window(), "Save the changes?", text="Platforms not saved.",
            safe="Save", destructive="Discard")
        self.question.answered.connect(
            lambda discard: self._answered(discard, signal))
        self._show_save()
        self.question.open()

    def _answered(self, discard: bool, signal) -> None:
        question, self.question = self.question, None
        if question is not None:
            question.deleteLater()
        if signal is None:
            # Gone elsewhere: a failed save is said, the sheet stays shut.
            if not discard:
                self.save()
            return
        if discard or self.save():
            self._shut(signal)

    def _shut(self, signal) -> None:
        self.hide()
        signal.emit()

    def discard(self) -> None:
        """Closed by going elsewhere (a crumb, Home, another task): the
        sheet goes, and with unsaved changes Save / Discard is asked over
        what is shown next, as Done and × ask."""
        if not self.isHidden():
            self.hide()
        if self._changes():
            self._ask(None)
            if self.question is not None:
                self.question.raise_()
