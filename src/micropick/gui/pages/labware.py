"""The Deck step (REDESIGN §6.1, #13): telling the robot session what is
on the deck.

The robot only knows the labware it was told about. A plate set down by hand is
invisible to every `move_to_well`, and a plate loaded into the run and then
lifted off by hand is still there as far as the run is concerned. This page is
where the telling happens.

**Left, the deck** (`widgets.deck_tiles`): twelve tiles in the deck's real
layout, each with SLOT N, the platform the profile puts there (a chip,
the border dashed) and its labware - icon, name, facts - or Empty; the
trash faint and not clickable. A filled tile dragged onto an empty one
moves its labware there (`Session.move_labware`). Over the deck, while
the robot holds labware it remembers from before, the info banner "The
robot remembers what was on it last time..." until Got it, once per robot
session; and while labware sits on a platform without the platform's
height, a warn banner with Load again (below).

**Right, the slot clicked.** An empty one asks "What goes to slot N?":
a search over every definition, the chips Frequent · Plates · Tips ·
Dishes, and a list of items with icon, name and facts (the well volume
always: two Corning 384 plates differ only by it) - a click loads it.
A filled one is "In slot N": the item, Remove and Put something else,
which offers the same list and replaces it. At the bottom, Change
platforms in Robot setup opens the platforms sheet: the platforms are
the profile's, placed once per bench, and no longer chosen here.

Frequent is the profile's recently used (kept in `deck.json`, because a
bench loads the same handful of plates every day and the stock list is
hundreds long), then the custom definitions in `labware/`. Search, while
something is typed, looks through all of them, custom first: a stock name
shadowed by a custom file is the custom one, as `resolve_definition`
has it.

Everything the page shows comes from the run state the robot reports, re-read
after every change, rather than from a record the page keeps of what it asked
for. That is the same rule `Routine.check_labware` follows: what the robot
says is on the deck is the only thing the robot will act on.

**Deck is done** once Next was pressed on it in this robot session and
the deck has no problem (`tasks`); any change made here - a load, a
remove, a move, the platforms - clears that until the next Next
(`Session._deck_changed`).

**Platforms are the one thing here that ends in a crash if forgotten.**
The picking platform and the calibration module raise the labware in their
slots by an amount the robot's deck model does not have; the offset is
attached at load to each labware loaded *after* it was registered. The run
reports the offset on every labware it holds, and a plate on a platform
without one is named on its tile and in the warn banner, whose Load again
loads it with the offset (`Session.reload_labware`). Deck is not done
while one is.

Loading, removing and moving are HTTP and go to a `Worker` under the
robot's lease like every job that changes the robot session. None moves
the gantry. A tip is put on at the Tip step.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget)

from ...config.labware import (LabwareDefinition, LabwareError,
                               local_definitions,
                               shared_definitions)
from ..labware_kinds import CHIPS, facts, in_chip, kind
from ...hardware.labware import LoadedLabware
from ..session import Session
from ..theme import COLORS, SIZES, SPACING
from ..theme.factory import (card, compact, heading, muted_label,
                             scroll_column, secondary_button)
from ..theme.icons import icon
from ..widgets.banner import Banner
from ..widgets.deck_tiles import (TRASH_SLOT, DeckTiles, kind_icon,
                                  set_kind_icon)
from ..widgets.mini_deck import DeckTile
from ..widgets.toast import show_toast
from ..workers import Worker

__all__ = ["LabwarePage"]

TITLE = "Deck"

log = logging.getLogger(__name__)

PANEL_WIDTH = SIZES["side-min"]

# A search lists no more than this: the stock catalogue is hundreds long,
# and a longer list is typed into, not read.
MAX_LISTED = 60

MEMORY = ("The robot remembers what was on it last time. Check that it "
          "matches the real deck.")
NO_HEIGHT = "Loaded without the platform's height."
NO_HEIGHT_IN = "Slot {}: loaded without the platform's height."
DRAG_HINT = "Drag an item to another slot to move it."
# Got it on the memory banner, for the robot session (`Session.confirm`).
MEMORY_SEEN = "deck-memory-seen"

# A definition rides on its list item under this role.
DEFINITION_ROLE = Qt.ItemDataRole.UserRole


class _Item(QWidget):
    """A catalogue line: icon, name and facts, and a +."""

    def __init__(self, definition: LabwareDefinition, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("plain")
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(12)
        row.addWidget(kind_icon(kind(definition), 18, self))
        text = QVBoxLayout()
        text.setSpacing(2)
        name = QLabel(definition.display_name, self)
        name.setObjectName("setupLabel")
        text.addWidget(name)
        text.addWidget(muted_label(facts(definition), self))
        row.addLayout(text, 1)
        plus = QLabel(self)
        plus.setPixmap(icon("plus", COLORS["muted"], 18).pixmap(18, 18))
        row.addWidget(plus)
        for label in self.findChildren(QLabel):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


class LabwarePage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._worker: Worker | None = None
        self.slot: int | None = None
        # "Put something else" pressed for the filled slot shown: the
        # catalogue is offered in its place, and a choice replaces it.
        self.replacing = False
        self.chip = CHIPS[0]
        # What a load in flight is loading, so a successful one goes into
        # Frequent and a failed one does not; and what its toast says.
        self._loading_name: str | None = None
        self._done_text = ""
        self._custom: dict[str, LabwareDefinition] = {}
        self._known: dict[str, LabwareDefinition] = {}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(self._deck_column(), 1)
        panel = QWidget(self)
        panel.setObjectName("plain")
        panel.setLayout(self._panel())
        side = scroll_column(panel, PANEL_WIDTH)
        side.setStyleSheet(f"#column {{ border-left: 1px solid "
                           f"{COLORS['line']}; }}")
        layout.addWidget(side)

        session.robot_state_changed.connect(self._on_robot_changed)
        session.robot_busy_changed.connect(lambda _h: self._refresh())
        session.labware_changed.connect(lambda _s: self._refresh())
        # Another profile has other platforms and another Frequent.
        session.profile_changed.connect(lambda _p: self._refresh())

        self._reload_definitions()
        self._refresh()

    # -- construction --------------------------------------------------------

    def _deck_column(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setContentsMargins(SPACING * 3, SPACING * 2, SPACING * 3,
                                  SPACING * 2)
        column.setSpacing(SPACING * 2)
        self.memory = Banner(MEMORY, "info", self)
        self.memory.add_action("Got it", quiet=True).clicked.connect(
            self._memory_seen_now)
        column.addWidget(self.memory)
        self.problems = Banner("", "warn", self)
        self.reload_button = self.problems.add_action("Load again")
        self.reload_button.clicked.connect(self._reload_problems)
        column.addWidget(self.problems)
        self.deck = DeckTiles(self)
        self.deck.slot_clicked.connect(self.choose)
        self.deck.moved.connect(self.move)
        column.addWidget(self.deck, 1)
        column.addWidget(muted_label(DRAG_HINT, self))
        # Refusals land here rather than in a dialog: a slot the robot will
        # not load into is ordinary, and its text says what to do.
        self.message = QLabel(self)
        self.message.setWordWrap(True)
        self.message.setStyleSheet(f"color: {COLORS['warn-text']};")
        self.message.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.message.hide()
        column.addWidget(self.message)
        return column

    def _panel(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setContentsMargins(SPACING * 3, SPACING * 3, SPACING * 3,
                                  SPACING * 3)
        column.setSpacing(SPACING * 2)
        self.title = heading("", 2, self)
        self.title.setWordWrap(True)
        column.addWidget(self.title)

        # A filled slot: the item, Remove, Put something else.
        self.held = QWidget(self)
        self.held.setObjectName("plain")
        held = QVBoxLayout(self.held)
        held.setContentsMargins(0, 0, 0, 0)
        held.setSpacing(SPACING * 2)
        held_card = card(self.held)
        line = QHBoxLayout()
        line.setSpacing(12)
        self.held_icon = kind_icon(None, 22, held_card)
        line.addWidget(self.held_icon)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.held_name = QLabel(held_card)
        self.held_name.setWordWrap(True)
        self.held_name.setObjectName("setupLabel")
        text.addWidget(self.held_name)
        self.held_facts = muted_label("", held_card)
        text.addWidget(self.held_facts)
        line.addLayout(text, 1)
        held_card.layout().addLayout(line)
        held.addWidget(held_card)
        buttons = QHBoxLayout()
        self.remove_button = secondary_button("Remove", self.held)
        self.remove_button.setIcon(icon("close", COLORS["text"], 16))
        self.remove_button.clicked.connect(self.remove)
        self.replace_button = secondary_button("Put something else",
                                               self.held)
        self.replace_button.clicked.connect(self.put_something_else)
        buttons.addWidget(self.remove_button)
        buttons.addWidget(self.replace_button)
        buttons.addStretch(1)
        held.addLayout(buttons)
        column.addWidget(self.held)

        # An empty slot, or one being replaced: what can go there.
        self.catalogue = QWidget(self)
        self.catalogue.setObjectName("plain")
        catalogue = QVBoxLayout(self.catalogue)
        catalogue.setContentsMargins(0, 0, 0, 0)
        catalogue.setSpacing(SPACING * 2)
        self.search = QLineEdit(self.catalogue)
        self.search.setPlaceholderText("Search, e.g. 384")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon("search", COLORS["muted"], 18),
                              QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(lambda _t: self._fill_list())
        catalogue.addWidget(self.search)
        chips = QHBoxLayout()
        chips.setSpacing(8)
        self.chips = QButtonGroup(self)
        self.chip_buttons = {}
        for name in CHIPS:
            button = compact(secondary_button(name, self.catalogue))
            button.setCheckable(True)
            button.setChecked(name == self.chip)
            button.clicked.connect(lambda _=False, n=name: self.choose_chip(n))
            self.chips.addButton(button)
            self.chip_buttons[name] = button
            chips.addWidget(button)
        chips.addStretch(1)
        catalogue.addLayout(chips)
        self.items = QListWidget(self.catalogue)
        self.items.setMinimumHeight(240)
        self.items.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.items.itemClicked.connect(self._item_clicked)
        catalogue.addWidget(self.items, 1)
        self.items_note = muted_label("", self.catalogue)
        catalogue.addWidget(self.items_note)
        column.addWidget(self.catalogue, 1)

        column.addStretch(1)
        platforms = secondary_button("Change platforms in Robot setup", self)
        platforms.clicked.connect(
            lambda: self.session.page_requested.emit("platforms"))
        column.addWidget(platforms)
        return column

    # -- the catalogue -------------------------------------------------------

    def _reload_definitions(self) -> None:
        """Read the catalogue, once: neither list changes while the
        application runs, short of editing labware/ underneath it."""
        try:
            self._custom = local_definitions()
        except LabwareError as exc:
            self._custom = {}
            log.error("custom labware: %s", exc)
        try:
            stock = shared_definitions()
        except LabwareError as exc:
            stock = {}
            log.error("stock labware: %s", exc)
        # Every definition by load name, custom first.
        self._known = {**self._custom,
                       **{n: d for n, d in stock.items()
                          if n not in self._custom}}

    def listed(self) -> list[LabwareDefinition]:
        """What the list offers now: the search's matches while something
        is typed, else the chip's."""
        known = self._known
        wanted = self.search.text().strip().lower()
        if wanted:
            found = [d for d in known.values() if wanted in " ".join(
                (d.display_name, d.load_name, d.category)).lower()]
        elif self.chip == "Frequent":
            profile = self.session.profile
            recent = list(profile.deck.recent_labware) if profile else []
            names = recent + [n for n in self._custom if n not in recent]
            return [known[n] for n in names if n in known]
        else:
            found = [d for d in known.values() if in_chip(d, self.chip)]
        return sorted(found, key=lambda d: (d.load_name not in self._custom,
                                            d.display_name.lower()))

    def choose_chip(self, name: str) -> None:
        self.chip = name
        self.chip_buttons[name].setChecked(True)
        self._fill_list()

    def _fill_list(self) -> None:
        listed = self.listed()
        self.items.clear()
        for definition in listed[:MAX_LISTED]:
            item = QListWidgetItem(self.items)
            item.setData(DEFINITION_ROLE, definition)
            item.setToolTip(definition.load_name)
            row = _Item(definition, self.items)
            item.setSizeHint(row.sizeHint())
            self.items.setItemWidget(item, row)
        more = len(listed) - MAX_LISTED
        self.items_note.setText(
            "Nothing found." if not listed else
            f"{more} more: type to narrow." if more > 0 else "")
        self.items_note.setVisible(bool(self.items_note.text()))

    def _item_clicked(self, item: QListWidgetItem) -> None:
        definition = item.data(DEFINITION_ROLE)
        if definition is not None:
            self.put(definition)

    # -- acting --------------------------------------------------------------

    def choose(self, slot: int) -> None:
        """A slot clicked on the deck; the trash is not one."""
        if slot == TRASH_SLOT:
            return
        self.slot = slot
        self.replacing = False
        self._say("")
        self._refresh()

    def put_something_else(self) -> None:
        self.replacing = True
        self._refresh()

    def put(self, definition: LabwareDefinition) -> None:
        """`definition` into the chosen slot, in place of what is there."""
        if self.slot is None or self.session.robot is None:
            return
        slot = self.slot
        if self._run(Worker(self.session.load_labware, definition, slot),
                     f"loading {definition.load_name} into slot {slot}",
                     f"{definition.display_name} is in slot {slot}."):
            self._loading_name = definition.load_name

    def remove(self) -> None:
        if self.slot is None or self.session.robot is None:
            return
        self._run(Worker(self.session.unload_labware, self.slot),
                  f"moving slot {self.slot} off deck",
                  f"Slot {self.slot} is empty.")

    def move(self, source: int, target: int) -> None:
        """A tile dragged onto an empty one."""
        if self.session.robot is None:
            return
        if self._run(Worker(self.session.move_labware, source, target),
                     f"moving slot {source} to slot {target}",
                     f"Moved to slot {target}."):
            self.slot = target

    def _reload_problems(self) -> None:
        """Load again every labware the run holds without its platform's
        height, one after the other in one job."""
        problems = self.session.deck_problems()
        if not problems or self.session.robot is None:
            return
        session = self.session

        def job():
            for problem in problems:
                session.reload_labware(problem.slot)

        self._run(Worker(job), "loading again with the module offset: slots "
                  + ", ".join(p.slot for p in problems))

    def _memory_seen_now(self) -> None:
        self.session.confirm(MEMORY_SEEN)
        self._refresh()

    # -- jobs ----------------------------------------------------------------

    def _busy(self) -> bool:
        return self._worker is not None and self._worker.running

    def _run(self, worker: Worker, what: str, done: str = "") -> bool:
        """Every job here sends the robot commands - a load, a tip - so it
        claims the robot's lease first (`Session.robot_lease`). False, and
        nothing started, while the robot is busy."""
        if self._busy():
            return False
        lease = self.session.robot_lease.claim(what)
        if lease is None:
            self._say(self.session.robot_lease.refusal)
            return False
        self._say("")
        worker.lease = lease
        self._worker = worker
        self._done_text = done
        worker.what = worker.what or what
        # Bound methods, not lambdas: Qt takes a connection's thread from the
        # receiver, and a lambda has none.
        worker.finished.connect(self._job_done)
        worker.failed.connect(self._job_failed)
        log.info("%s", what)
        worker.start()
        self._refresh()
        return True

    def _job_done(self, _result) -> None:
        if self.sender() is not self._worker:
            return
        self._worker = None
        if self._loading_name is not None:
            self.session.remember_labware(self._loading_name)
            self._loading_name = None
            self._fill_list()
        self.replacing = False
        if self._done_text:
            show_toast(self, self._done_text)
        self._refresh()

    def _job_failed(self, reason: str) -> None:
        if self.sender() is not self._worker:
            return
        self._worker = None
        self._loading_name = None
        self._say(reason)
        log.error("deck: %s", reason)
        self._refresh()

    # -- display -------------------------------------------------------------

    def _on_robot_changed(self, _state: str) -> None:
        if self.session.robot is None:
            self.slot = None
            self.replacing = False
        self._refresh()

    def _say(self, text: str) -> None:
        self.message.setText(text)
        self.message.setVisible(bool(text))

    def _held(self) -> dict[str, LoadedLabware]:
        """What the run holds, by slot name; nothing with no robot."""
        state = self.session.run_state
        if self.session.robot is None or state is None:
            return {}
        return state.labware

    def _tiles(self, held: dict, problems: set[str]) -> list[DeckTile]:
        profile = self.session.profile
        out = []
        for name, entry in held.items():
            if not name.isdigit() or int(name) == TRASH_SLOT:
                continue
            label, its_kind, its_facts = self._shown(entry)
            problem = name in problems
            out.append(DeckTile(int(name), name=label, kind=its_kind,
                                facts=its_facts,
                                note=NO_HEIGHT if problem else "",
                                note_tone="warn" if problem else ""))
        by_slot = {t.slot: t for t in out}
        modules = profile.deck.modules if profile is not None else []
        for module in modules:
            for slot in module.slots:
                by_slot[slot] = replace(by_slot.get(slot, DeckTile(slot)),
                                        platform=module.name or "Platform")
        return list(by_slot.values())

    def _refresh(self) -> None:
        session = self.session
        busy = self._busy()
        free = not busy and not session.robot_lease.busy
        connected = session.robot is not None
        held = self._held()
        problems = session.deck_problems()
        self.deck.set_tiles(self._tiles(held, {p.slot for p in problems}))
        self.deck.select(self.slot)

        remembered = any(n != str(TRASH_SLOT) for n in held)
        self.memory.setVisible(remembered
                               and not session.is_confirmed(MEMORY_SEEN))
        self.problems.set_text(NO_HEIGHT_IN.format(
            ", ".join(p.slot for p in problems)) if problems else "")
        self.problems.setToolTip("\n".join(p.describe() for p in problems))
        self.problems.setVisible(bool(problems))
        self.reload_button.setEnabled(free)

        entry = held.get(str(self.slot)) if self.slot is not None else None
        filled = entry is not None
        if self.slot is None:
            self.title.setText("Click a slot.")
        elif filled and not self.replacing:
            self.title.setText(f"In slot {self.slot}")
        else:
            self.title.setText(f"What goes to slot {self.slot}?")
        offering = self.slot is not None and (not filled or self.replacing)
        self.held.setVisible(filled and not self.replacing)
        self.catalogue.setVisible(offering)
        if filled:
            self._show_held(entry)
        if offering and self.items.count() == 0:
            self._fill_list()
        for widget in (self.remove_button, self.replace_button, self.items):
            widget.setEnabled(connected and free)
        lease = session.robot_lease
        self.items.setToolTip(f"Robot busy: {lease.holder}."
                              if lease.busy else "")

    def _show_held(self, entry) -> None:
        label, its_kind, its_facts = self._shown(entry)
        set_kind_icon(self.held_icon, its_kind, 22)
        self.held_name.setText(label)
        self.held_facts.setText(its_facts)
        self.held_facts.setVisible(bool(its_facts))

    def _shown(self, entry: LoadedLabware) -> tuple[str, str | None, str]:
        """Name, kind and facts of what the run holds; its load name alone
        when the catalogue does not know it."""
        definition = self._definition_of(entry)
        if definition is None:
            return entry.load_name, None, ""
        return definition.display_name, kind(definition), facts(definition)

    def _definition_of(self, entry) -> LabwareDefinition | None:
        """The catalogue's definition for what the run reports, by load name;
        None for a load name the catalogue does not have."""
        return self._known.get(entry.load_name)
