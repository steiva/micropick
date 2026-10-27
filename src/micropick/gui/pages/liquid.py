"""Liquid's Run step (REDESIGN §6.9): the liquid plan done by the robot.

A liquid plan (`core.liquid.Program`) is groups of wells of a plate on the
deck, each with the steps every one of its wells gets - Aspirate,
Dispense, Move to, Mix, Blow out, Wait, Pause - each saying where it
happens. It is made on Liquid's Plan step (`pages.liquid_plan`) and handed
over with Use this plan (`Session.set_liquid_plan`), with the groups to
run: all, one after another, or only one. The run takes them in that
order and, for each well of a group, does all its steps before the next
well (`workflows.liquid`).

On the left the plate, with what was done: the plan's plates in a
dropdown, the wells in their group's color, done ones marked, the current
one ringed. On the right, with what the run has done and what the tip
holds, the deck with where the group goes. There is no camera: what a
liquid run does is seen on the plate.

The footer is the run's status as on Picking (`run_status`,
`footer_buttons`, read by the shell): READY, RUNNING, PAUSED, WAITING FOR
YOU, DONE, NOT READY and one sentence, the run's own
(`LiquidState.status`) while it runs. Its buttons are Back and **Start**
between runs (and **Continue** after a stop), **Pause** / **Resume** (one
button, yellow while paused or waiting) and **Stop run** during one. A
run that went to its end is what makes the step done
(`Session.confirm("liquid_run")`).

The well center
---------------
A plate's measured well center (`config.schema.WellCenter`, measured on
Liquid's Well center step or Manual control, per slot and plate type) is
read here: every step on that plate is measured sideways from the real
center (`workflows.liquid`, `Plate.center`), and its depth makes the
measured bottom, rim less depth, a level a step can use.

What the tip holds
------------------
The run counts it (`LiquidState.in_tip`, shown beside the plate), and an
Aspirate with "Auto refill" on - the default - is a refill: skipped while
the tip holds enough for the dispenses after it, else a top-up to its
volume (`core.liquid`, "What the tip holds"). The plan says how much a tip
takes, and nothing is let overfill it. The count is the page's: it goes to
zero when the robot reports no tip, and "Tip is empty" sets it there after
the tip was emptied some other way.

Running it
----------
Through a jog panel that is never shown (as Dish bottom's), like every
robot command on a page with a panel (`JogPanel.run_job`): the run holds
the robot's lease from Start to its end, pauses and waits included, so
STOP and the pill show and nothing else can move the robot. Pause and
Stop are `threading.Event`s the run checks between commands. Start first
blows the tip out over the plan's trash (`core.liquid`, "The trash"), so
nothing a tried step or an earlier run left in it goes into the first
well; Continue does not. What the tip holds and how far the run got are
kept on the page (`LiquidState`) across a stop: Continue carries on from
the step after the last one done, Start always begins again from the
first well. A plan used again forgets that progress unless only its
steps' numbers changed, so a flow rate can be corrected between Stop and
Continue.
"""

from __future__ import annotations

import logging
import threading
import time

import qtawesome as qta
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMessageBox, QToolButton,
                               QVBoxLayout, QWidget)

from ... import paths
from ...core.liquid import ACTIONS, Group, Program, describe_location
from ...core.routine import Destination, RoutineError
from ...workflows import liquid
from ..session import Session
from ..theme import SPACING
from ..theme.factory import (card, combo_box, danger_button, heading,
                             primary_button, scroll_column, secondary_button)
from ..widgets.deck_view import DeckView
from ..widgets.jog_panel import JogPanel
from ..widgets.plate_view import PlateView
from ..widgets.plate_window import PlateWindow
from ..workers import describe_error
from .liquid_plan import (plates_for, positions_for, reach_problems,
                          robot_problems)

__all__ = ["LiquidHandlingPage", "deck_marks"]

TITLE = "Liquid handling"

log = logging.getLogger(__name__)

PANEL_WIDTH = 420

# (key, what it does, handler). Esc is the window's Stop.
KEYS = (("P", "pause / resume the run", "_pause_key"),)

CONFIRM_START = (
    "Make sure every plate is in place with its lid off, and a tip is on the "
    "pipette.\n\nThe robot starts moving as soon as you press Start.")

# The Deck card's map.
DECK_HEIGHT = 200
TRASH_COLOUR = "#b0b2ba"
_VERBS = {action: title.lower() for action, _cls, title in ACTIONS}


def deck_marks(group, trash) -> tuple[dict, list[str]]:
    """(marks for `DeckView.set_marks`, lines for what is not in a slot):
    the group's plate filled in its colour with its wells and the steps done
    in them; every other slot a step goes to dashed, with the step's number,
    what it does and the well; the trash. A saved point is not in a slot,
    so it is a line."""
    marks: dict[str, tuple[str, str, bool]] = {}
    elsewhere: list[str] = []
    if group is not None:
        at: dict[str, list[str]] = {}
        for n, step in enumerate(group.steps, 1):
            location = getattr(step, "location", None)
            if location is None:
                continue
            verb = _VERBS[step.action]
            if location.kind == "this_well":
                at.setdefault(str(group.slot), []).append(f"{n} {verb}")
            elif location.kind == "well" and location.slot:
                at.setdefault(str(location.slot), []).append(
                    f"{n} {verb} {location.well or '?'}")
            elif location.kind == "point":
                elsewhere.append(f"Step {n} {verb}: point "
                                 f"{location.point or '?'}.")
        own = at.pop(str(group.slot), [])
        marks[str(group.slot)] = (
            group.color, f"{group.name}: {len(group.wells)} wells"
            + (f"\n{', '.join(own)}" if own else ""), True)
        for slot, lines in at.items():
            marks[slot] = (group.color, ", ".join(lines), False)
    if trash is not None and trash.kind == "well" and trash.slot:
        slot = str(trash.slot)
        line = f"trash {trash.well or '?'}"
        if slot in marks:
            colour, text, solid = marks[slot]
            marks[slot] = (colour, f"{text}\n{line}", solid)
        else:
            marks[slot] = (TRASH_COLOUR, line, False)
    elif trash is not None and trash.kind == "point":
        elsewhere.append(f"Trash: point {trash.point or '?'}.")
    return marks, elsewhere


def _label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class LiquidHandlingPage(QWidget):
    # What a job on the jog panel's thread has to tell this page: (kind,
    # payload). A signal, so the widgets are touched by their own thread.
    job_said = Signal(object)

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        # The plan Use this plan handed over, and the groups it runs.
        self.program = Program()
        self.groups: list[int] | None = None
        self.state = liquid.LiquidState()
        self._pause = threading.Event()
        self._stop = threading.Event()
        self._running = False
        self._can_carry_on = False
        self._checking: str | None = None
        # The run's sentence (`LiquidState.status`) while it runs; how the
        # last one ended ("done", "stopped", "failed") and in words.
        self._status = ""
        self._ended: str | None = None
        self._outcome = ""
        self._need: str | None = None
        self._current: tuple[int, str] | None = None
        self._log_path = None
        self._loading = False
        # session.plates(), read when the deck changes rather than on every
        # refresh: each entry is a definition file resolved from disk.
        self._deck = session.plates()

        # Never shown: it runs the robot work and holds the lease, as Dish
        # bottom's does. A pad here would move a tip the run left in a well.
        self.jog = JogPanel(session, shortcut_host=self, parent=self)
        self.jog.hide()
        # Emitted whenever a job ends, done or failed: the one moment the
        # buttons that were grayed for it can come back.
        self.jog.position_changed.connect(lambda _lines: self._refresh())

        # The footer's buttons (`footer_buttons`), the shell lays them out.
        self.start_button = primary_button("Start", self)
        self.start_button.setToolTip("Run the plan from the first well.")
        self.start_button.clicked.connect(self._start)
        self.continue_button = secondary_button("Continue", self)
        self.continue_button.setToolTip(
            "Carry on from where the run stopped.")
        self.continue_button.clicked.connect(self._continue)
        self.pause_button = secondary_button("Pause", self)
        self.pause_button.setToolTip("Key: P")
        self.pause_button.clicked.connect(self._pause_key)
        self.stop_button = danger_button("Stop run", self)
        self.stop_button.setToolTip("Stop after the current command and "
                                    "raise the tip. Key: Esc")
        self.stop_button.clicked.connect(self._stop_run)
        for button in (self.start_button, self.continue_button,
                       self.pause_button, self.stop_button):
            button.hide()

        panel = QWidget(self)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        column.addWidget(self._progress_card())
        column.addWidget(self._deck_card())
        column.addStretch(1)
        self.body = QHBoxLayout()
        self.body.setSpacing(SPACING)
        self.plate_box = self._plate_card()
        self.body.addWidget(self.plate_box, 1)
        self.body.addWidget(scroll_column(panel, PANEL_WIDTH))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2,
                                  SPACING * 2)
        layout.setSpacing(SPACING)
        layout.addLayout(self.body, 1)

        self.job_said.connect(self._on_job_said)
        session.liquid_plan_changed.connect(lambda _p: self._take_plan())
        # The window's Stop, and Esc: see `shell.MainWindow._stop`.
        session.stop_requested.connect(self._stop_run)
        session.robot_state_changed.connect(lambda _s: self._robot_changed())
        session.robot_busy_changed.connect(self._on_robot_busy)
        session.labware_changed.connect(lambda _s: self._deck_changed())
        session.tip_changed.connect(self._tip_changed)
        self._install_shortcuts()
        self._reload_plates()
        self._take_plan()

    # -- construction --------------------------------------------------------

    def _plate_card(self) -> QWidget:
        box = card(self)
        row = QHBoxLayout()
        # Names the slot: the same plate may stand in two.
        self.wells_heading = heading("Wells", 2)
        row.addWidget(self.wells_heading, 1)
        self.plate_choice = combo_box(self)
        self.plate_choice.setToolTip("The plates the plan uses.")
        self.plate_choice.currentIndexChanged.connect(
            lambda _i: self._show_plate())
        row.addWidget(self.plate_choice)
        self.enlarge_button = QToolButton(self)
        self.enlarge_button.setIcon(qta.icon("mdi6.arrow-expand"))
        self.enlarge_button.setToolTip("Show the plate in its own window.")
        self.enlarge_button.clicked.connect(self._show_plate_window)
        row.addWidget(self.enlarge_button)
        box.layout().addLayout(row)

        self.plate = PlateView(self)
        self.plate.hovered.connect(self._hovered)
        box.layout().addWidget(self.plate, 1)
        # The well under the pointer, by name: on a 384 the map has no room
        # for it. A line always there, so the map does not move.
        self.hover_line = QLabel(" ", self)
        box.layout().addWidget(self.hover_line)
        self.plate_window = PlateWindow(self.plate, self)

        self.plate_state = _label()
        box.layout().addWidget(self.plate_state)
        return box

    def _progress_card(self) -> QWidget:
        box = card(self)
        box.layout().addWidget(heading("Progress", 2))
        self.progress_line = heading("", 3)
        box.layout().addWidget(self.progress_line)
        self.tip_line = _label()
        box.layout().addWidget(self.tip_line)
        self.empty_button = secondary_button("Tip is empty", self)
        self.empty_button.setToolTip("Mark the tip as empty.")
        self.empty_button.clicked.connect(self._mark_empty)
        box.layout().addWidget(self.empty_button)
        # What keeps Start back, every item; the footer names the first.
        self.run_state = _label()
        box.layout().addWidget(self.run_state)
        return box

    def _deck_card(self) -> QWidget:
        """Where the chosen group's wells are and where its steps go, on the
        deck: two slots holding the same plate are otherwise one misread
        away from the wrong one."""
        box = card(self)
        box.layout().addWidget(heading("Deck", 2))
        box.layout().addWidget(_label(
            "The group running, or next: its wells (filled) and where its "
            "steps go (dashed)."))
        self.deck = DeckView(self)
        self.deck.setFixedHeight(DECK_HEIGHT)
        box.layout().addWidget(self.deck)
        self.deck_state = _label()
        box.layout().addWidget(self.deck_state)
        return box

    # -- the plan ------------------------------------------------------------

    def _indices(self) -> list[int]:
        """The groups that run, in order."""
        return (list(range(len(self.program.groups))) if self.groups is None
                else [gi for gi in self.groups
                      if gi < len(self.program.groups)])

    def _group(self) -> Group | None:
        """The group running, else the first to run on the plate shown,
        else the first to run."""
        groups = self.program.groups
        if self._current is not None and self._current[0] < len(groups):
            return groups[self._current[0]]
        shown = self._shown_plate()
        indices = self._indices()
        for gi in indices:
            if (groups[gi].slot, groups[gi].load_name) == shown:
                return groups[gi]
        return groups[indices[0]] if indices else None

    def _take_plan(self) -> None:
        """The session's plan (Use this plan), once no run is going. Its
        progress is kept while the groups run and their wells are the
        same: only a step's numbers changed."""
        if self._running:
            return
        plan = self.session.liquid_plan
        program = plan.model_copy(deep=True) if plan is not None else Program()
        groups = self.session.liquid_groups
        wells = [(g.slot, g.load_name, g.wells, g.order)
                 for g in program.groups]
        if (groups != self.groups or wells != [
                (g.slot, g.load_name, g.wells, g.order)
                for g in self.program.groups]):
            self.state.reset()
            self._can_carry_on = False
            self._ended, self._outcome = None, ""
        elif program.groups != self.program.groups:
            self.state.resume = {}
        self.program, self.groups = program, groups
        self._reload_plates()
        group = self._group()
        if group is not None:
            for index in range(self.plate_choice.count()):
                slot, definition = self.plate_choice.itemData(index)
                if (slot, definition.load_name) == (group.slot,
                                                    group.load_name):
                    self.plate_choice.setCurrentIndex(index)
                    break
        self._paint_plate()
        self._refresh()

    def _show_deck(self) -> None:
        names = {slot: definition.display_name
                 for slot, _entry, definition in self._deck}
        self.deck.set_labware(names)
        marks, elsewhere = deck_marks(self._group(), self.program.trash)
        self.deck.set_marks(marks)
        self.deck_state.setText("\n".join(elsewhere))
        self.deck_state.setVisible(bool(elsewhere))

    def _install_shortcuts(self) -> None:
        """P, window-wide while this page is showing - enabled only on
        screen, so no hidden page claims the key. Esc is the window's."""
        self._shortcuts = []
        for key, _what, handler in KEYS:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(getattr(self, handler))
            shortcut.setEnabled(False)
            self._shortcuts.append(shortcut)

    # -- what is in hand -----------------------------------------------------

    def _shown_plate(self):
        """(slot, load name) of the plate on the map, or None."""
        data = self.plate_choice.currentData()
        return None if data is None else (data[0], data[1].load_name)

    # -- the plate map ---------------------------------------------------------

    def _reload_plates(self) -> None:
        """The plates the plan's groups are on, or every plate on the deck
        while there is no plan."""
        shown = self._shown_plate()
        self._deck = self.session.plates()
        used = {(g.slot, g.load_name) for g in self.program.groups}
        self._loading = True
        self.plate_choice.clear()
        for slot, _entry, definition in self._deck:
            if used and (slot, definition.load_name) not in used:
                continue
            self.plate_choice.addItem(f"slot {slot} — {definition.display_name}",
                                      (slot, definition))
        for index in range(self.plate_choice.count()):
            slot, definition = self.plate_choice.itemData(index)
            if shown == (slot, definition.load_name):
                self.plate_choice.setCurrentIndex(index)
        self.plate_choice.setVisible(self.plate_choice.count() > 1)
        self._loading = False
        self._show_plate()

    def _show_plate(self) -> None:
        if self._loading:
            return
        data = self.plate_choice.currentData()
        destination = None
        if data is not None:
            slot, definition = data
            try:
                destination = Destination.from_definition(definition, int(slot))
            except (RoutineError, ValueError) as exc:
                log.error("liquid handling: cannot draw slot %s: %s", slot, exc)
        self.plate.set_destination(destination)
        self.wells_heading.setText(
            f"Slot {data[0]} · {data[1].display_name}" if data is not None
            else "Wells")
        self.plate_window.setWindowTitle(
            f"plate: {self.plate_choice.currentText() or 'none'}")
        self._paint_plate()
        self._refresh()

    def _show_plate_window(self) -> None:
        self.plate_window.setWindowTitle(
            f"plate: {self.plate_choice.currentText() or 'none'}")
        self.plate_window.show_window()

    def _hovered(self, name: str | None) -> None:
        text = self._hover_text(name)
        self.hover_line.setText(text or " ")
        self.plate_window.say(text)

    def _hover_text(self, name: str | None) -> str:
        """The well, its group and how far the run got with it."""
        if name is None:
            return ""
        shown = self._shown_plate()
        for gi in self._indices():
            group = self.program.groups[gi]
            if (group.slot, group.load_name) != shown or name not in group.wells:
                continue
            state = ("done" if (gi, name) in self.state.done else
                     "in progress" if self._current == (gi, name) else
                     "to do")
            return f"{name} — {group.name}, {state}"
        return f"{name} — no group"

    def _paint_plate(self) -> None:
        shown = self._shown_plate()
        colours, done = {}, set()
        for gi in self._indices():
            group = self.program.groups[gi]
            if (group.slot, group.load_name) != shown:
                continue
            for well in group.wells:
                colours[well] = group.color
                if (gi, well) in self.state.done:
                    done.add(well)
        self.plate.set_colours(colours)
        self.plate.set_done(done)
        current = self._current
        self.plate.set_current(
            current[1] if current is not None
            and current[0] < len(self.program.groups)
            and (self.program.groups[current[0]].slot,
                 self.program.groups[current[0]].load_name) == shown
            else None)

    def _robot_changed(self) -> None:
        self._deck_changed()

    def _deck_changed(self) -> None:
        self._reload_plates()

    # -- checks -------------------------------------------------------------------

    def _run_problems(self) -> list[str]:
        out = robot_problems(self.session)
        if not self.program.groups:
            return out + ["there is no plan: make it on the Plan step."]
        # Played through from what the tip holds now, for a run from the
        # first well. Carrying on is checked step by step as it runs.
        out += liquid.problems(self.program, plates_for(self.session),
                               positions_for(self.session),
                               volumes=not self._can_carry_on,
                               in_tip=self.state.in_tip, groups=self.groups)
        out += reach_problems(self.session,
                              liquid.chosen(self.program, self.groups))
        return out

    # -- running ---------------------------------------------------------------------

    def _total(self) -> int:
        try:
            return len(liquid.plan(self.program, plates_for(self.session),
                                   self.groups))
        except ValueError:
            return 0

    def _confirm_start(self) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Start")
        groups = liquid.chosen(self.program, self.groups).groups
        box.setText(f"Run {len(groups)} group{'s' if len(groups) != 1 else ''}"
                    f", {self._total()} wells?")
        lines = [self._group_route(g) for g in groups]
        trash = self.program.trash
        if trash is not None:
            lines.append("Trash: " + describe_location(trash) + ".")
        again = ("\n\nWells done earlier count as not done: the run starts "
                 "again from the first well." if self.state.done else "")
        box.setInformativeText("\n".join(lines) + again + "\n\n" + CONFIRM_START)
        start = box.addButton("Start", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is start

    def _group_route(self, group: Group) -> str:
        """A group in one line: its wells' slot and plate, and every other
        slot its steps go to - what Start asks to be checked on the deck."""
        names = {slot: definition.display_name
                 for slot, _entry, definition in self._deck}
        goes = sorted({str(loc.slot) for loc in
                       (getattr(step, "location", None) for step in group.steps)
                       if loc is not None and loc.kind == "well" and loc.slot
                       and str(loc.slot) != str(group.slot)})
        text = (f"{group.name}: {len(group.wells)} wells of slot {group.slot} "
                f"({names.get(group.slot, group.load_name)})")
        if goes:
            text += "; to " + ", ".join(
                f"slot {slot} ({names.get(slot, '?')})" for slot in goes)
        return text + "."

    def _startable(self) -> bool:
        return (not self._running and not self.jog.busy
                and not self.session.robot_lease.busy
                and self._need is None and not self._run_problems())

    def _start(self) -> None:
        if not self._startable():
            return
        if not self._confirm_start():
            return
        if not self._startable():                # changed while asking
            self._refresh()
            return
        self.state.reset()
        self._log_path = (paths.logs_dir() / f"liquid_"
                          f"{time.strftime('%Y%m%d_%H%M%S')}.csv")
        self._launch(clear_first=True)

    def _continue(self) -> None:
        if self._running:
            if self._pause.is_set():
                self._pause.clear()
                self._checking = None
                log.info("liquid handling: resumed")
                self._refresh()
            return
        if self._can_carry_on and self._startable():
            self._launch()

    def _launch(self, *, clear_first: bool = False) -> None:
        """`clear_first`: a run from the first well, which blows the tip out
        over the trash before anything else."""
        motion, emit = self.session.motion, self.job_said.emit
        # A copy: the page may not be edited during the run, but a copy
        # makes that a rule of the data rather than of the buttons.
        program = self.program.model_copy(deep=True)
        plates, state = plates_for(self.session), self.state
        positions, groups = positions_for(self.session), self.groups
        pause, stop, log_path = self._pause, self._stop, self._log_path
        pause.clear()
        stop.clear()

        def job(log_):
            try:
                liquid.run(motion, program, plates, positions, state,
                           pause=pause, stop=stop, log=log_,
                           on_well=lambda gi, well: emit(("well", (gi, well))),
                           on_progress=lambda *_a: emit(("progress", None)),
                           on_paused=lambda message: emit(("paused", message)),
                           on_status=lambda text: emit(("status", text)),
                           log_path=log_path, clear_first=clear_first,
                           groups=groups)
            except liquid.Stopped:
                emit(("ended", ("stopped", "")))
                return "run stopped."
            except Exception as exc:
                emit(("ended", ("failed", describe_error(exc))))
                raise
            emit(("ended", ("done", state.status)))
            return "run finished."

        # The robot for the whole run, pauses included (`Session.robot_lease`).
        used = {(slot, plates[slot].load_name)
                for slot in liquid.slots_used(liquid.chosen(program, groups))
                if slot in plates}
        if not self.jog.run_job(job, what="liquid run", run=True,
                                plates=used):
            self._ended, self._outcome = None, self.session.robot_lease.refusal
            self._refresh()
            return
        self._running = True
        self._can_carry_on = False
        self._checking = None
        self._ended, self._outcome = None, ""
        self._status = "Starting."
        log.info("liquid handling: running %d groups (%d wells already done)",
                 len(self._indices()), len(state.done))
        self._refresh()

    def _pause_run(self) -> None:
        if self._running and not self._pause.is_set():
            self._pause.set()
            log.info("liquid handling: paused")
            self._refresh()

    def _pause_key(self) -> None:
        """Pause, or Resume while paused or waiting; between runs nothing:
        Continue is a button of its own."""
        if not self._running:
            return
        if self._pause.is_set():
            self._continue()
        else:
            self._pause_run()

    def _stop_run(self) -> None:
        if self._running and not self._stop.is_set():
            self._stop.set()
            log.info("liquid handling: stop asked")
            self._refresh()

    def _on_job_said(self, payload) -> None:
        kind, value = payload
        if kind == "well":
            self._current = value
        elif kind == "status":
            self._status = value
        elif kind == "paused":
            self._checking = value
        elif kind == "ended":
            how, said = value
            self._running = False
            self._checking = None
            self._current = None
            self._pause.clear()
            done, total = len(self.state.done), self._total()
            self._can_carry_on = how != "done" and total > done
            self._ended = how
            self._outcome = (
                said if how != "stopped" else
                f"Stopped. {done} of {total} well{'' if total == 1 else 's'}"
                f" done.")
            if how == "done":
                self.session.confirm("liquid_run")
            log.info("liquid handling: %s", how)
            # A plan used while it ran.
            self._take_plan()
        self._paint_plate()
        self._refresh()

    # -- keeping up with the session -----------------------------------------------

    def _tip_changed(self, tip) -> None:
        """No tip holds nothing."""
        if tip.attached is False and not self._running:
            self.state.in_tip = 0.0
            self._refresh()

    def _mark_empty(self) -> None:
        if self._running or self.jog.busy:
            return
        log.info("liquid handling: tip marked empty (it was counted at %g µl)",
                 self.state.in_tip)
        self.state.in_tip = 0.0
        self._refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        for shortcut in self._shortcuts:
            shortcut.setEnabled(True)
        self._refresh()

    def hideEvent(self, event) -> None:
        """The run keeps going; only the keys are given up."""
        for shortcut in self._shortcuts:
            shortcut.setEnabled(False)
        super().hideEvent(event)

    # -- the footer -------------------------------------------------------------

    def set_need(self, need: str | None) -> None:
        """The step's first unmet need (the shell's banner), or None: while
        there is one, Start waits and the footer says NOT READY."""
        if need != self._need:
            self._need = need
            self._refresh()

    def run_status(self) -> tuple[str, str, str]:
        """The footer's tag, sentence and tone (`Footer.STATUS_TONES`)."""
        if self._running:
            if self._stop.is_set():
                return ("Running", "Stopping after the current command.",
                        "busy")
            if self._checking is not None:
                return ("Waiting for you", self._status
                        or "Waiting for you.", "warn")
            if self._pause.is_set():
                return "Paused", self._status, "warn"
            return "Running", self._status, "busy"
        if self._ended == "done":
            return "Done", self._outcome, "ok"
        problems = ([self._need] if self._need is not None else []) + [
            p[:1].upper() + p[1:] for p in self._run_problems()]
        if problems:
            return "Not ready", problems[0], "off"
        if self._outcome:
            return "Ready", self._outcome, "ready"
        total = self._total()
        return ("Ready", f"{total} well{'' if total == 1 else 's'} to do.",
                "ready")

    def footer_buttons(self, back: QWidget) -> list[QWidget]:
        """What the footer shows on the right: Back and Start between runs
        (Continue between them after a stop), Pause or Resume and Stop run
        during one."""
        if self._running:
            return [self.pause_button, self.stop_button]
        if self._can_carry_on:
            return [back, self.continue_button, self.start_button]
        return [back, self.start_button]

    # -- display ------------------------------------------------------------------

    def _on_robot_busy(self, _holder) -> None:
        self._refresh()

    def run_activity(self) -> str | None:
        """For the top bar's pill: "waiting" at a check the plan pauses
        for, "paused", or None."""
        if not self._running:
            return None
        if self._checking is not None:
            return "waiting"
        return "paused" if self._pause.is_set() else None

    def _refresh(self) -> None:
        running = self._running
        # Robot work waits for the robot to be free, on any page
        # (`Session.robot_lease`); marking the tip empty does not.
        idle_here = not running and not self.jog.busy
        self._show_deck()

        # The plate.
        group = self._group()
        if self.session.robot is None:
            self.plate_state.setText("Robot not connected.")
        elif group is not None and self._shown_plate() is None:
            self.plate_state.setText(
                f"{group.name}: slot {group.slot} ({group.load_name}) is not "
                f"loaded.")
        elif self.plate_choice.count() == 0:
            self.plate_state.setText("No labware loaded.")
        else:
            self.plate_state.setText("")
        self.plate_state.setVisible(bool(self.plate_state.text()))

        # The run.
        problems = self._run_problems()
        ready = (idle_here and not self.session.robot_lease.busy
                 and self._need is None and not problems)
        held = running and self._pause.is_set()
        self.start_button.setEnabled(ready)
        self.continue_button.setEnabled(ready and self._can_carry_on)
        self.pause_button.setText("Resume" if held else "Pause")
        if self.pause_button.property("tone") != ("warn" if held else None):
            self.pause_button.setProperty("tone", "warn" if held else None)
            self.pause_button.style().unpolish(self.pause_button)
            self.pause_button.style().polish(self.pause_button)
        self.pause_button.setEnabled(running and not self._stop.is_set())
        self.stop_button.setEnabled(running)
        self.empty_button.setEnabled(idle_here and self.state.in_tip > 0)
        total, done = self._total(), len(self.state.done)
        self.progress_line.setText(f"{done} of {total} wells done")
        self.tip_line.setText(f"The tip holds {self.state.in_tip:g} µl."
                              if self.state.in_tip > 0 else
                              "The tip is empty.")
        listed = problems if not running and len(problems) > 1 else []
        self.run_state.setText("\n".join("• " + p for p in listed))
        self.run_state.setVisible(bool(listed))
        # Last: the shell reads `run_status` and `footer_buttons` on it.
        self.session.run_activity_changed.emit()
