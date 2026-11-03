"""Liquid's Run step (LIQUID.md §6): what the robot is doing now.

The plan (`core.liquid_plan.LiquidPlan`) is made on Liquid's Plan step, which
hands it to the session as it is edited (`Session.set_liquid_plan`). Here it
runs, all its steps in a row, through `workflows.liquid_plan.run_plan`. How to
run it a step at a time, what the robot asks after a step and the strips are
#77's.

**Left:** the step in hand. A caption ("STEP 2 OF 3 · RUNNING"), its sentence
from → to, the wells done in this step and a bar. Under them Taking from and
Putting into, two cards of the same size, each a plate in its real proportions
(`PlateGrid`) or, for a point, a spot or the trash, its name. On a plate the
step's wells done are in its color, the one in hand ringed, those still to do
dim, and the wells of steps done before it in their colors. A step with one
place has one card, and a Wait none. `core.liquid_plan.pairs` says which wells
go together.

**Right:** the steps as cards to read, each with how far the run got, the
trash, and what the tip holds with **Tip is empty**, for a tip emptied by some
other way; the count goes to zero by itself when the robot reports no tip.

The footer is the run's status as on Picking (`run_status`, `footer_buttons`,
read by the shell): READY, RUNNING, PAUSED, WAITING FOR YOU, DONE, NOT READY
and one sentence, in the plan's words: the engine's status ("aspirating") is
never shown. Its buttons are **Back to plan** and **Start** (and **Continue**
after a stop), then **Pause** / **Continue** (one button, yellow while held)
and **Stop run** during a run, Back to plan gray beside its sentence: the plan
cannot change under the robot. A run that went to its end is what makes the
step done (`Session.confirm("liquid_run")`).

Running it
----------
Through a jog panel that is never shown (as Dish bottom's), like every robot
command on a page with a panel (`JogPanel.run_job`): the run holds the robot's
lease from Start to its end, pauses and waits included, so STOP and the pill
show and nothing else can move the robot. The pill says "Liquid · step 2 of 3"
(`run_label`). Pause and Stop are `threading.Event`s the run checks between
commands. Start begins with the blow-out over the trash, so nothing a tried
step or an earlier run left in the tip goes into the first well; Continue does
not. `PlanState` is kept on the page across a stop: Continue carries on from
the well after the last one done, Start always begins again. A plan used again
forgets that progress unless only its steps' numbers changed, so a flow rate
can be corrected between Stop and Continue.

The well center
---------------
A plate's measured well center (`config.schema.WellCenter`) is read through
`Session.liquid_plates`: a step on that plate is measured sideways from the real
center, and its depth makes the measured bottom.
"""

from __future__ import annotations

import logging
import threading
import time

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (QFrame, QGraphicsOpacityEffect, QGridLayout,
                               QHBoxLayout, QLabel, QMessageBox,
                               QStackedWidget, QVBoxLayout, QWidget)

from ... import paths
from ...core.liquid_plan import (Place, chip, compiles, pairs, places, spots,
                                 step_chips, step_title)
from ...workflows import liquid
from ...workflows.liquid_plan import PlanState, compile_plan, run_plan
from ..session import Session
from ..theme import COLORS, SPACING, mono_font
from ..theme.factory import (caption, danger_button, heading, muted_label,
                             primary_button, scroll_column, secondary_button)
from ..theme.icons import pixmap
from ..widgets.jog_panel import JogPanel
from ..widgets.plate_grid import PlateGrid, dim_color
from ..widgets.step_list import PlanStepCard
from ..workers import describe_error
from .liquid_plan import robot_problems, trash_place

__all__ = ["LiquidHandlingPage"]

TITLE = "Liquid handling"

log = logging.getLogger(__name__)

PANEL_WIDTH = 420
BAR_PX = 8
ICON_PX = 48
COUNTER_PX = 28
# A step done, on the list.
DONE_OPACITY = 0.7

# (key, what it does, handler). Esc is the window's Stop.
KEYS = (("P", "pause / continue the run", "_pause_key"),)

CONFIRM_START = (
    "Make sure every plate is in place with its lid off, and a tip is on the "
    "pipette.\n\nThe robot starts moving as soon as you press Start.")
LOCKED = "The plan can be changed when the robot is not moving."
NO_PLAN = "there is no plan: make it on the Plan step."
NOT_ON_DECK = "This plate is not on the deck."


def goes_to(programs, trash) -> tuple[set[str], set[str]]:
    """The slots and the points a run sends the tip to. The trash always: it
    begins there, whatever its steps are."""
    slots = {trash.slot} if trash.kind == "well" else set()
    names = {trash.point} if trash.kind == "point" else set()
    for program in programs:
        slots |= liquid.slots_used(program)
        names |= liquid.points_used(program)
    return slots, names


def reach_problems(session: Session, programs, trash,
                   positions: dict) -> list[str]:
    """Points out of the robot's reach, and deck problems in the slots the
    run goes to."""
    out = []
    slots, names = goes_to(programs, trash)
    for name in sorted(names):
        if name in positions:
            why = session.motion.refused(positions[name])
            if why:
                out.append(f"a point is outside the soft limits: {why}.")
    return _unique(out + [p.describe() for p in session.deck_problems()
                          if p.slot in slots])


def _unique(messages: list[str]) -> list[str]:
    return list(dict.fromkeys(messages))


def _wells(count: int) -> str:
    return f"{count} well{'' if count == 1 else 's'}"


def ends(step) -> list[tuple[str, Place, str]]:
    """What the step's cards show: (caption, place, role), the source's
    first. Empty for a step with no place."""
    if step.kind == "move_liquid":
        return [("Taking from", step.source, "source"),
                ("Putting into", step.dest, "dest")]
    if step.kind == "mix":
        return [("Mixing in", step.wells, "dest")]
    if step.kind == "blow_out":
        return [("Blowing out at", step.place, "dest")]
    if step.kind == "move_tip":
        return [("Moving the tip to", step.place, "dest")]
    return []


class _Bar(QWidget):
    """How far the step got."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.fraction = 0.0
        self.color = COLORS["accent"]
        self.setFixedHeight(BAR_PX)

    def show_bar(self, fraction: float, color: str) -> None:
        self.fraction, self.color = fraction, color
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        radius = self.height() / 2
        painter.setBrush(QColor(COLORS["card-border"]))
        painter.drawRoundedRect(self.rect(), radius, radius)
        painter.setBrush(QColor(self.color))
        painter.drawRoundedRect(QRectF(0, 0, self.width() * self.fraction,
                                       self.height()), radius, radius)
        painter.end()


class _End(QFrame):
    """A card of the step: a plate, or what is not one by name."""

    def __init__(self, hovered, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("runEnd")
        column = QVBoxLayout(self)
        column.setContentsMargins(18, 18, 18, 18)
        column.setSpacing(SPACING)
        self.caption = caption("", self)
        column.addWidget(self.caption)
        self.name = heading("", 3, self)
        column.addWidget(self.name)
        self.stack = QStackedWidget(self)
        self.plate = PlateGrid(read_only=True, parent=self)
        self.plate.hovered.connect(hovered)
        self.stack.addWidget(self.plate)
        self.named = QWidget(self)
        self.named.setObjectName("plain")
        lines = QVBoxLayout(self.named)
        lines.addStretch(1)
        self.icon = QLabel(self.named)
        lines.addWidget(self.icon, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.place_name = heading("", 2, self.named)
        lines.addWidget(self.place_name,
                        alignment=Qt.AlignmentFlag.AlignHCenter)
        self.place_facts = muted_label("", self.named)
        lines.addWidget(self.place_facts,
                        alignment=Qt.AlignmentFlag.AlignHCenter)
        lines.addStretch(1)
        self.stack.addWidget(self.named)
        column.addWidget(self.stack, 1)
        self.note = muted_label("", self)
        column.addWidget(self.note)

    def show_named(self, name: str, facts: str, icon_name: str | None) -> None:
        self.stack.setCurrentWidget(self.named)
        self.place_name.setText(name)
        self.place_facts.setText(facts)
        self.icon.setVisible(icon_name is not None)
        if icon_name is not None:
            self.icon.setPixmap(pixmap(icon_name, COLORS["text-2"], ICON_PX))

    def show_plate(self, slot: str, ordering) -> None:
        self.stack.setCurrentWidget(self.plate)
        if self.plate.property("slot") != slot:
            self.plate.setProperty("slot", slot)
            self.plate.set_ordering(ordering)


class LiquidHandlingPage(QWidget):
    # What a job on the jog panel's thread has to tell this page: (kind,
    # payload). A signal, so the widgets are touched by their own thread.
    job_said = Signal(object)

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        # The session's plan, None while it cannot run, and what it needs.
        self.plan = None
        self.programs: dict = {}
        self.plates: dict = {}
        self.positions: dict = {}
        self._shape: list = []
        self.state = PlanState()
        self._pause = threading.Event()
        self._stop = threading.Event()
        self._running = False
        self._can_carry_on = False
        self._checking: str | None = None        # what the plan holds it for
        self._index = 0                          # the step in hand
        # How the last run ended ("done", "stopped", "failed") and in words.
        self._ended: str | None = None
        self._outcome = ""
        self._need: str | None = None
        self._log_path = None
        self.cards: dict[str, PlanStepCard] = {}

        # Never shown: it runs the robot work and holds the lease, as Dish
        # bottom's does. A pad here would move a tip the run left in a well.
        self.jog = JogPanel(session, shortcut_host=self, parent=self)
        self.jog.hide()
        # Emitted whenever a job ends, done or failed: the one moment the
        # buttons that were grayed for it can come back.
        self.jog.position_changed.connect(lambda _lines: self._refresh())

        # The footer's buttons (`footer_buttons`), the shell lays them out.
        self.back_button = secondary_button("Back to plan", self)
        self.back_button.clicked.connect(
            lambda: self.session.page_requested.emit("liquid_plan"))
        self.back_note = muted_label(LOCKED, self)
        self.back_note.setWordWrap(False)
        self.start_button = primary_button("Start", self)
        self.start_button.setToolTip("Run the plan from its first step.")
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
        for button in (self.back_button, self.back_note, self.start_button,
                       self.continue_button, self.pause_button,
                       self.stop_button):
            button.hide()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2,
                                  SPACING * 2)
        layout.setSpacing(SPACING * 2)
        layout.addWidget(self._now(), 1)
        layout.addWidget(scroll_column(self._panel(), PANEL_WIDTH))

        self.job_said.connect(self._on_job_said)
        session.liquid_plan_changed.connect(lambda _p: self._take_plan())
        # The window's Stop, and Esc: see `shell.MainWindow._stop`.
        session.stop_requested.connect(self._stop_run)
        session.robot_state_changed.connect(lambda _s: self._take_plan())
        session.robot_busy_changed.connect(lambda _h: self._refresh())
        session.labware_changed.connect(lambda _s: self._take_plan())
        session.tip_changed.connect(self._tip_changed)
        session.in_tip_changed.connect(self._in_tip_said)
        self._install_shortcuts()
        self._take_plan()

    # -- construction --------------------------------------------------------

    def _now(self) -> QWidget:
        box = QWidget(self)
        box.setObjectName("plain")
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING * 2)
        top = QHBoxLayout()
        titles = QVBoxLayout()
        self.head_caption = caption("", box)
        self.head_title = heading("", 2, box)
        self.head_title.setWordWrap(True)
        titles.addWidget(self.head_caption)
        titles.addWidget(self.head_title)
        top.addLayout(titles, 1)
        counts = QVBoxLayout()
        self.counter = QLabel(box)
        self.counter.setFont(mono_font(COUNTER_PX, 600))
        self.counter_note = muted_label("", box)
        self.counter_note.setWordWrap(False)
        counts.addWidget(self.counter, alignment=Qt.AlignmentFlag.AlignRight)
        counts.addWidget(self.counter_note,
                         alignment=Qt.AlignmentFlag.AlignRight)
        top.addLayout(counts)
        column.addLayout(top)
        self.bar = _Bar(box)
        column.addWidget(self.bar)

        grid = QGridLayout()
        grid.setSpacing(SPACING)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(2, 1)
        self.taking = _End(self._hovered, box)
        self.putting = _End(self._hovered, box)
        self.arrow = heading("→", 2, box)
        grid.addWidget(self.taking, 0, 0)
        grid.addWidget(self.arrow, 0, 1)
        grid.addWidget(self.putting, 0, 2)
        column.addLayout(grid, 1)
        # The well under the pointer, by name: on a 384 the plate has no room
        # for it. A line always there, so the plates do not move.
        self.hover_line = muted_label(" ", box)
        column.addWidget(self.hover_line)
        return box

    def _panel(self) -> QWidget:
        panel = QWidget(self)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        top = QHBoxLayout()
        top.addWidget(caption("Steps", panel), 1)
        self.trash_line = muted_label("", panel)
        self.trash_line.setWordWrap(False)
        top.addWidget(self.trash_line)
        column.addLayout(top)
        self.step_column = QVBoxLayout()
        self.step_column.setSpacing(8)
        column.addLayout(self.step_column)
        self.tip_line = muted_label("", panel)
        column.addWidget(self.tip_line)
        self.empty_button = secondary_button("Tip is empty", panel)
        self.empty_button.setToolTip("Mark the tip as empty.")
        self.empty_button.clicked.connect(self._mark_empty)
        column.addWidget(self.empty_button)
        # What keeps Start back, every item; the footer names the first.
        self.run_state = muted_label("", panel)
        column.addWidget(self.run_state)
        column.addStretch(1)
        return panel

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

    # -- the plan ------------------------------------------------------------

    def _take_plan(self) -> None:
        """The session's plan, if it can run, once no run is going. Its
        progress is kept while its steps and places are the same: only a
        step's numbers changed."""
        if self._running:
            return
        session = self.session
        plan = session.liquid_plan
        ready = plan is not None and not session.liquid_plan_problems()
        self.plan = plan if ready else None
        programs, plates, positions = {}, {}, {}
        if ready:
            programs, plates = compile_plan(plan, session.liquid_plates())
            positions = {**session.liquid_positions(), **spots(plan)}
        shape = [(step.id, places(step)) for step in plan.steps] if ready else []
        if shape != self._shape:
            self.state = PlanState(in_tip=self.state.in_tip)
            self._can_carry_on = False
            self._ended, self._outcome = None, ""
            self._index = 0
        elif programs != self.programs:
            for carried in self.state.steps.values():
                carried.resume = {}
        self._shape = shape
        self.programs, self.plates, self.positions = programs, plates, positions
        self._show_steps()
        self._refresh()

    def _steps(self) -> list:
        """The steps on screen: of the plan that can run, else of the one on
        the Plan step, to read."""
        plan = self.plan or self.session.liquid_plan
        return list(plan.steps) if plan is not None else []

    def _step_pairs(self, step) -> list:
        return pairs(step, self.plates) if compiles(step) else []

    def _step_total(self, step) -> int:
        return len(self._step_pairs(step))

    def _step_done(self, step) -> int:
        """Of its wells."""
        if step.id in self.state.done:
            return self._step_total(step)
        carried = self.state.steps.get(step.id)
        return len(carried.done) if carried is not None else 0

    def _in_tip(self) -> float:
        """During a step its own count is the one that moves; `PlanState`
        has it when the step ends."""
        step = self._step()
        carried = self.state.steps.get(step.id) if step is not None else None
        if self._running and carried is not None and (
                step.id not in self.state.done):
            return carried.in_tip
        return self.state.in_tip

    def _total(self) -> int:
        return sum(self._step_total(step) for step in self._steps())

    def _done(self) -> int:
        return sum(self._step_done(step) for step in self._steps())

    # -- checks --------------------------------------------------------------

    def _run_problems(self) -> list[str]:
        out = robot_problems(self.session)
        if self.plan is None:
            return out + [NO_PLAN]
        return out + reach_problems(self.session, self.programs.values(),
                                    self.plan.trash, self.positions)

    # -- running -------------------------------------------------------------

    def _confirm_start(self) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Start")
        steps = self.plan.steps
        box.setText(f"Run {len(steps)} step{'s' if len(steps) != 1 else ''}"
                    f", {_wells(self._total())}?")
        lines = [f"Step {n}: " + " · ".join(
            [step_title(step), *([" → ".join(step_chips(step))]
                                 if step_chips(step) else [])]) + "."
                 for n, step in enumerate(steps, 1)]
        lines.append(f"Trash: {trash_place(self.plan.trash)}.")
        again = ("\n\nWells done earlier count as not done: the run starts "
                 "again from the first step." if self._done() else "")
        box.setInformativeText("\n".join(lines) + again + "\n\n" + CONFIRM_START)
        start = box.addButton("Start", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is start

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
        self.state = PlanState(in_tip=self.state.in_tip)
        self._index = 0
        self._log_path = (paths.logs_dir() / f"liquid_"
                          f"{time.strftime('%Y%m%d_%H%M%S')}.csv")
        self._launch()

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

    def _launch(self) -> None:
        motion, emit = self.session.motion, self.job_said.emit
        # A copy: the page may not be edited during the run, but a copy
        # makes that a rule of the data rather than of the buttons.
        plan = self.plan.model_copy(deep=True)
        plates, positions, state = self.plates, self.positions, self.state
        pause, stop, log_path = self._pause, self._stop, self._log_path
        pause.clear()
        stop.clear()

        def job(log_):
            try:
                run_plan(motion, plan, plates, positions, state, pause=pause,
                         stop=stop, log=log_,
                         on_step=lambda index, what: emit(
                             ("step", (index, what))),
                         on_progress=lambda *_a: emit(("progress", None)),
                         on_paused=lambda message: emit(("paused", message)),
                         log_path=log_path)
            except liquid.Stopped:
                emit(("ended", ("stopped", "")))
                return "run stopped."
            except Exception as exc:
                emit(("ended", ("failed", describe_error(exc))))
                raise
            emit(("ended", ("done", "")))
            return "run finished."

        # The robot for the whole run, pauses included (`Session.robot_lease`).
        used = {(slot, plates[slot].load_name)
                for slot in goes_to(self.programs.values(), plan.trash)[0]
                if slot in plates}
        if not self.jog.run_job(job, what="liquid run", run=True, plates=used):
            self._ended, self._outcome = None, self.session.robot_lease.refusal
            self._refresh()
            return
        self._running = True
        self._can_carry_on = False
        self._checking = None
        self._ended, self._outcome = None, ""
        log.info("liquid handling: running %d steps (%d wells already done)",
                 len(plan.steps), self._done())
        self._refresh()

    def _pause_run(self) -> None:
        if self._running and not self._pause.is_set():
            self._pause.set()
            log.info("liquid handling: paused")
            self._refresh()

    def _pause_key(self) -> None:
        """Pause, or Continue while paused or waiting; between runs nothing:
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
        if kind == "step":
            index, what = value
            if what == "started":
                self._index = index
        elif kind == "paused":
            self._checking = value
        elif kind == "ended":
            how, said = value
            self._running = False
            self._checking = None
            self._pause.clear()
            self.session.set_in_tip(self.state.in_tip)
            done, total = self._done(), self._total()
            self._can_carry_on = (how != "done"
                                  and len(self.state.done) < len(self._steps()))
            self._ended = how
            count = len(self._steps())
            self._outcome = {
                "done": f"All {count} step{'' if count == 1 else 's'} done.",
                "stopped": f"Stopped. {done} of {_wells(total)} done.",
            }.get(how, said)
            if how == "done":
                self.session.confirm("liquid_run")
            log.info("liquid handling: %s", how)
            # A plan used while it ran.
            self._take_plan()
        self._refresh()

    # -- keeping up with the session -----------------------------------------

    def _tip_changed(self, tip) -> None:
        """No tip holds nothing."""
        if tip.attached is False and not self._running:
            self.state.in_tip = 0.0
            self._refresh()

    def _in_tip_said(self, ul: float) -> None:
        """Try once, Manual pick: the same tip. During a run the run counts."""
        if not self._running:
            self.state.in_tip = ul
            self._refresh()

    def _mark_empty(self) -> None:
        if self._running or self.jog.busy:
            return
        log.info("liquid handling: tip marked empty (it was counted at %g µl)",
                 self.state.in_tip)
        self.state.in_tip = 0.0
        self.session.set_in_tip(0.0)
        self._refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        for shortcut in self._shortcuts:
            shortcut.setEnabled(True)
        # A well center measured since: a bottom compiles differently.
        self._take_plan()

    def hideEvent(self, event) -> None:
        """The run keeps going; only the keys are given up."""
        for shortcut in self._shortcuts:
            shortcut.setEnabled(False)
        super().hideEvent(event)

    # -- the footer and the pill ---------------------------------------------

    def set_need(self, need: str | None) -> None:
        """The step's first unmet need (the shell's banner), or None: while
        there is one, Start waits and the footer says NOT READY."""
        if need != self._need:
            self._need = need
            self._refresh()

    def _now_sentence(self) -> str:
        """What the robot is at, in the plan's words."""
        step = self._step()
        well = self._well_in_hand(step) if step is not None else None
        return f"Now at well {well}." if well else "Starting."

    def _well_in_hand(self, step) -> str | None:
        wells = self._step_pairs(step)
        done = self._step_done(step)
        if done >= len(wells):
            return None
        source, dest = wells[done]
        return dest or source

    def run_status(self) -> tuple[str, str, str]:
        """The footer's tag, sentence and tone (`Footer.STATUS_TONES`)."""
        if self._running:
            if self._stop.is_set():
                return ("Running", "Stopping after the current command.",
                        "busy")
            if self._checking is not None:
                return ("Waiting for you", self._checking, "warn")
            if self._pause.is_set():
                return "Paused", "Press Continue to go on.", "warn"
            return "Running", self._now_sentence(), "busy"
        if self._ended == "done":
            return "Done", self._outcome, "ok"
        problems = ([self._need] if self._need is not None else []) + [
            p[:1].upper() + p[1:] for p in self._run_problems()]
        if problems:
            return "Not ready", problems[0], "off"
        if self._outcome:
            return "Ready", self._outcome, "ready"
        return "Ready", f"{_wells(self._total())} to do.", "ready"

    def footer_buttons(self, _back: QWidget) -> list[QWidget]:
        """What the footer shows on the right: Back to plan and Start between
        runs (Continue between them after a stop); during one Back to plan
        gray with why, Pause or Continue, and Stop run."""
        if self._running:
            return [self.back_note, self.back_button, self.pause_button,
                    self.stop_button]
        if self._can_carry_on:
            return [self.back_button, self.continue_button, self.start_button]
        return [self.back_button, self.start_button]

    def run_activity(self) -> str | None:
        """For the top bar's pill: "waiting" at a check the plan pauses
        for, "paused", or None."""
        if not self._running:
            return None
        if self._checking is not None:
            return "waiting"
        return "paused" if self._pause.is_set() else None

    def run_label(self) -> str | None:
        """For the top bar's pill while the run goes on."""
        count = len(self._steps())
        return (f"Liquid · step {self._index + 1} of {count}" if count
                else None)

    # -- display -------------------------------------------------------------

    def _step(self):
        steps = self._steps()
        return steps[min(self._index, len(steps) - 1)] if steps else None

    def _head(self) -> tuple[str, str]:
        """The caption's words and color."""
        step = self._step()
        where = f"STEP {self._index + 1} OF {len(self._steps())} · "
        if self._running:
            if self._checking is not None:
                return where + "WAITING FOR YOU", COLORS["warn"]
            if self._pause.is_set():
                return where + "PAUSED", COLORS["warn"]
            return where + "RUNNING", step.color
        if self._ended == "done":
            return where + "DONE", COLORS["ok"]
        return "READY", COLORS["text-3"]

    def _show_steps(self) -> None:
        """The cards, made again only when the steps are others."""
        steps = self._steps()
        if [s.id for s in steps] != list(self.cards):
            for card in self.cards.values():
                self.step_column.removeWidget(card)
                card.deleteLater()
            self.cards = {}
            for step in steps:
                card = PlanStepCard(step.id, self)
                card.set_read_only()
                self.step_column.addWidget(card)
                self.cards[step.id] = card
        for index, step in enumerate(steps):
            self.cards[step.id].show_step(step, index, len(steps),
                                          unfinished=False, locked=True)

    def _step_status(self, index: int, step) -> tuple[str, str]:
        """What a step's card says of it, and in which color."""
        if step.id in self.state.done:
            return "✓ Done", COLORS["ok"]
        if index == self._index and self._running:
            held = self._checking is not None or self._pause.is_set()
            total = self._step_total(step)
            count = f" · {self._step_done(step)}/{total}" if total else ""
            return (("Paused", COLORS["warn"]) if held
                    else (f"Running{count}", step.color))
        if index == 0 and not self._running and not self.state.done:
            return "First", COLORS["text-2"]
        return "Next", COLORS["text-3"]

    def _show_status(self) -> None:
        for index, step in enumerate(self._steps()):
            card = self.cards[step.id]
            text, color = self._step_status(index, step)
            card.show_status(text, color, current=self._running
                             and index == self._index
                             and step.id not in self.state.done)
            done = step.id in self.state.done
            if done != (card.graphicsEffect() is not None):
                effect = None
                if done:
                    effect = QGraphicsOpacityEffect(card)
                    effect.setOpacity(DONE_OPACITY)
                card.setGraphicsEffect(effect)

    def _note(self, step) -> str:
        if step.kind != "move_liquid":
            return ""
        refills = (step.source.count == 1 and step.dest.count > 1
                   and not step.mix_after)
        return (f"{step.volume_ul:g} µl per well"
                + (" · refills when the tip runs low" if refills else ""))

    def _legend(self, step) -> str:
        return (f'<span style="color: {step.color}">●</span> done &nbsp; '
                f'<span style="color: {COLORS["text"]}">○</span> now &nbsp; '
                f'<span style="color: {dim_color(step.color)}">●</span> '
                "still to do")

    def _end_wells(self, step, role: str) -> list[str | None]:
        """A card's well of each of the step's pairs."""
        at = 0 if role == "source" else 1
        return [pair[at] for pair in self._step_pairs(step)]

    def _show_wells(self, end: _End, step, place: Place, role: str) -> None:
        """Done in the step's color, the one in hand ringed, still to do dim,
        and what the steps before it did in their colors."""
        wells = self._end_wells(step, role)
        done = self._step_done(step)
        fills = {well: earlier.color for earlier in self._steps()
                 if earlier.id in self.state.done and earlier.id != step.id
                 for _role, other in places(earlier)
                 if other.kind == "wells" and other.slot == place.slot
                 for well in other.wells}
        still = {well: step.color for well in wells[done:] if well}
        fills.update({well: step.color for well in wells[:done]
                      if well and well not in still})
        end.plate.set_fills({well: color for well, color in fills.items()
                             if well not in still})
        end.plate.set_dim_fills(still)
        in_hand = (wells[done] if self._running and done < len(wells)
                   else None)
        end.plate.set_current(in_hand)

    def _show_end(self, end: _End, step, title: str, place: Place,
                  role: str) -> None:
        end.caption.setText(title)
        color = "tips" if role == "source" and step.kind == "move_liquid" \
            else "plate"
        end.caption.setStyleSheet(f"color: {COLORS[color]};")
        plates = {slot: definition
                  for slot, _entry, definition in self.session.plates()}
        if place.kind == "wells" and place.slot in plates:
            definition = plates[place.slot]
            end.name.setText(f"Slot {place.slot} · {definition.display_name}")
            end.show_plate(place.slot, definition.ordering)
            self._show_wells(end, step, place, role)
        elif place.kind == "trash":
            trash = self.plan.trash if self.plan is not None else None
            end.name.setText("Trash")
            end.show_named("Into the trash",
                           trash_place(trash) if trash is not None else "",
                           "trash")
        elif place.kind == "wells":
            end.name.setText(f"Slot {place.slot}" if place.slot else "")
            end.show_named(chip(place), NOT_ON_DECK if place.slot else "", None)
        else:
            end.name.setText("Point" if not place.empty else "")
            pose = (place.spot.x, place.spot.y, place.spot.z) if place.spot \
                else self.positions.get(place.point)
            end.show_named(chip(place), "" if pose is None else
                           "X {:.1f}  Y {:.1f}  Z {:.1f}".format(*pose), None)
        end.note.setText(self._note(step) if role == "source"
                         else self._legend(step) if place.kind == "wells"
                         else "")

    def _show_now(self) -> None:
        step = self._step()
        text, color = self._head() if step is not None else ("READY",
                                                             COLORS["text-3"])
        self.head_caption.setText(text)
        self.head_caption.setStyleSheet(f"color: {color};")
        shown = ends(step) if step is not None else []
        self.head_title.setText(
            " → ".join(step_chips(step)) or step_title(step)
            if step is not None else "No plan yet")
        total = self._step_total(step) if step is not None else 0
        done = self._step_done(step) if step is not None else 0
        self.counter.setVisible(total > 0)
        self.counter_note.setVisible(total > 0)
        self.counter.setText(f"{done} / {total}")
        self.counter_note.setText("wells done in this step")
        held = self._running and (self._checking is not None
                                  or self._pause.is_set())
        self.bar.show_bar(done / total if total else 0.0, COLORS["warn"] if held
                          else step.color if step is not None
                          else COLORS["accent"])
        cards = [self.taking, self.putting][2 - len(shown):] if shown else []
        for end in (self.taking, self.putting):
            end.setVisible(end in cards)
        self.arrow.setVisible(len(shown) == 2)
        for end, (title, place, role) in zip(cards, shown):
            self._show_end(end, step, title, place, role)

    def _hovered(self, name: str | None) -> None:
        """The well and how far the run got with it."""
        step = self._step()
        if name is None or step is None:
            self.hover_line.setText(" ")
            return
        done = self._step_done(step)
        for at, pair in enumerate(self._step_pairs(step)):
            if name in pair:
                state = ("done" if at < done else "now" if at == done
                         and self._running else "to do")
                self.hover_line.setText(f"{name}: {state}")
                return
        self.hover_line.setText(f"{name}: not in this step")

    def _refresh(self) -> None:
        running = self._running
        # Robot work waits for the robot to be free, on any page
        # (`Session.robot_lease`); marking the tip empty does not.
        idle_here = not running and not self.jog.busy
        problems = self._run_problems()
        ready = (idle_here and not self.session.robot_lease.busy
                 and self._need is None and not problems)
        held = running and self._pause.is_set()
        self.start_button.setEnabled(ready)
        self.continue_button.setEnabled(ready and self._can_carry_on)
        self.back_button.setEnabled(not running)
        self.back_button.setToolTip(LOCKED if running else "")
        self.pause_button.setText("Continue" if held else "Pause")
        if self.pause_button.property("tone") != ("warn" if held else None):
            self.pause_button.setProperty("tone", "warn" if held else None)
            self.pause_button.style().unpolish(self.pause_button)
            self.pause_button.style().polish(self.pause_button)
        self.pause_button.setEnabled(running and not self._stop.is_set())
        self.stop_button.setEnabled(running)
        in_tip = self._in_tip()
        self.empty_button.setEnabled(idle_here and in_tip > 0)
        self.tip_line.setText(f"The tip holds {in_tip:g} µl." if in_tip > 0
                              else "The tip is empty.")
        trash = self.plan.trash if self.plan is not None else None
        self.trash_line.setText("" if trash is None
                                else f"Trash: {trash_place(trash)}")
        listed = problems if not running and len(problems) > 1 else []
        self.run_state.setText("\n".join("• " + p for p in listed))
        self.run_state.setVisible(bool(listed))
        self._show_status()
        self._show_now()
        # Last: the shell reads `run_status` and `footer_buttons` on it.
        self.session.run_activity_changed.emit()
