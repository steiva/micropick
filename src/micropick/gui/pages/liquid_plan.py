"""Liquid's Plan step (LIQUID.md §3): a plan of steps, by mouse.

A plan (`core.liquid_plan.LiquidPlan`) is an ordered list of steps, each read
as from → to. It is handed to the session as it is edited
(`Session.set_liquid_plan`): there is nothing to confirm. The step is done
while the plan has no problems (`Session.liquid_plan_problems`), and until
then Next is gray with one sentence (`next_reason`, read by the shell).

**Right:** Start from, the Trash row, the steps (`widgets.step_list`) and Add
a step. One card is open at a time; its rows choose the section, what the left
area asks. Take from comes before Put into.

**Left:** what the open section asks: the plate from the `MiniDeckDropdown`,
its wells clicked, the wells of other steps dim, and under it the rule of how
sources pair with destinations (`core.liquid_plan.pairing`), warn-colored with
what would work. A plate not measured (nor skipped on the Well center step)
shows a warning with **Measure now** and **Use anyway**: it is not blocked,
the robot then uses the plate's drawing. With no card open: the whole plan's
wells in each step's color and a legend; with no steps, the first step's
button.

The page holds the plan, every edit a new copy on its undo list: wells, a
move, the trash, a step added or deleted are one undo each (Ctrl+Z, and Undo
over the plate). The widgets never reorder or change a plan themselves.

Still to come, each with its ticket under #67: Amount and Extras and the
forms of the other kinds (#73; a new step takes the prototype's heights),
points and By hand (#74), the trash chooser, Try once and templates (#75).

The plan is saved as it is edited, into `outputs/liquid/current.json`; the
next start keeps only its trash and what a tip holds: steps are chosen for
the plates on the deck that day.
"""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMenu, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget,
                               QWidgetAction)

from ... import paths
from ...core.liquid import Location, Program, ProgramError
from ...core.liquid_plan import (BlowOutStep, Height, LiquidPlan, MixStep,
                                 MoveLiquid, MoveTip, Place, WaitForMe,
                                 WaitStep, by_row, chip, move_step, pairing,
                                 places, step_chips, step_title)
from ..labware_kinds import facts, kind
from ..session import MEASURED, NOT_MEASURED, Session
from ..theme import COLORS, SPACING
from ..theme.factory import (caption, danger_text_button, heading, muted_label,
                             primary_button, secondary_button)
from ..theme.icons import icon
from ..widgets.banner import Banner
from ..widgets.controls import Segmented
from ..widgets.mini_deck import DeckTile, MiniDeckDropdown
from ..widgets.plate_grid import PlateGrid
from ..widgets.step_list import StepList
from ..widgets.toast import show_toast

__all__ = ["LiquidPlanPage", "CURRENT_FILE"]

TITLE = "Plan"

log = logging.getLogger(__name__)

PANEL_WIDTH = 460
# The plan is saved this long after the last edit.
SAVE_MS = 400
CURRENT_FILE = "current.json"

NO_PLATE = "There is no plate on the deck. Add it on the Deck step."
NOT_MEASURED_TEXT = ("The well center of this plate was not measured. The "
                     "robot will use the plate's drawing.")
LOCKED = "The plan cannot change during a run."

# The Add a step menu: (kind, on screen, its help line).
KINDS = (
    ("move_liquid", "Move liquid",
     "From a well or a point into wells, or into the trash."),
    ("mix", "Mix", "Pipette up and down in wells, so what was added mixes in."),
    ("blow_out", "Blow out", "Blows what is left out of the tip."),
    ("move_tip", "Move the tip", "Moves the tip to a place, with no liquid."),
    ("wait", "Wait", "The robot does nothing for a set time, e.g. 5 min."),
    ("wait_for_me", "Wait for me", "The robot stops until you press Continue."),
)

# What a kind asks on the left: (the step's field, its row).
SECTIONS = {
    "move_liquid": (("source", "Take from"), ("dest", "Put into")),
    "mix": (("wells", "Wells"),),
    "move_tip": (("place", "Where"),),
}
# The section of the Trash row, for the plan and not a step.
TRASH = "trash"
# section -> (caption, title, the hint under the plate, its color).
QUESTIONS = {
    "source": ("TAKE FROM", "Where is the liquid taken from?",
               "Click a well. Click several wells to take from each of them.",
               "tips"),
    "dest": ("PUT INTO", "Where does it go?", "Click the wells it goes into.",
             "plate"),
    "wells": ("WELLS", "Which wells are mixed?", "Click the wells to mix.",
              "plate"),
    "place": ("WHERE", "Where does the tip go?", "Click a well.", "plate"),
    TRASH: ("THE TRASH", "Where does leftover liquid go?",
            "Click the well that is the trash.", "plate"),
}
# Sections that are one well: a click replaces it.
ONE_WELL = ("place", TRASH)

# The prototype's heights (LiquidPlan.dc.html), until the Amount fields.
TAKE_AT = Height(ref="bottom", offset_mm=2.0)
PUT_AT = Height(ref="top", offset_mm=-1.0)


def plan_file() -> Path:
    directory = paths.outputs_dir() / "liquid"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / CURRENT_FILE


def new_step(step_kind: str, color: str):
    """A step of that kind, as Add a step makes it."""
    return {
        "move_liquid": lambda: MoveLiquid(color=color, take_at=TAKE_AT,
                                          put_at=PUT_AT),
        "mix": lambda: MixStep(color=color, at=TAKE_AT),
        "blow_out": lambda: BlowOutStep(color=color),
        "move_tip": lambda: MoveTip(color=color),
        "wait": lambda: WaitStep(color=color),
        "wait_for_me": lambda: WaitForMe(color=color),
    }[step_kind]()


def amount(step: MoveLiquid) -> str:
    return f"{step.volume_ul:g} µl per well · {step.take_flow_ul_s:g} µl/s"


def trash_place(trash: Location) -> str:
    """The plan's trash in a few words."""
    if trash.kind == "point":
        return f"Point · {trash.point}"
    return f"Slot {trash.slot} · {trash.well}"


class _Row(QPushButton):
    """A row of the open card: what it asks and what was answered."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("planRow")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        self.label = QLabel(self)
        self.value = QLabel(self)
        self.value.setObjectName("planRowValue")
        for label in (self.label, self.value):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(self.label, 1)
        row.addWidget(self.value)

    def show_row(self, label: str, value: str, color: str,
                 selected: bool) -> None:
        self.label.setText(label)
        self.label.setStyleSheet(f"color: {COLORS[color]};")
        self.value.setText(value)
        for widget, name, on in ((self.value, "missing", value == "choose"),
                                 (self, "selected", selected)):
            if widget.property(name) != on:
                widget.setProperty(name, on)
                widget.style().unpolish(widget)
                widget.style().polish(widget)


class _OpenCard(QWidget):
    """What an open card shows; one, moved into the card that is open."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("plain")
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 4, 0, 0)
        column.setSpacing(4)
        self.rows = [_Row(self), _Row(self)]
        for row in self.rows:
            column.addWidget(row)
        self.amount = muted_label("", self)
        self.amount.setContentsMargins(8, 0, 8, 0)
        column.addWidget(self.amount)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 6, 0, 0)
        self.delete_button = danger_text_button("Delete", self)
        self.next_button = primary_button("Done", self)
        buttons.addWidget(self.delete_button)
        buttons.addStretch(1)
        buttons.addWidget(self.next_button)
        column.addLayout(buttons)


class _KindItem(QWidget):
    """A line of the Add a step menu: a kind over its help line."""

    def __init__(self, action: QWidgetAction, name: str, help_line: str,
                 menu: QMenu):
        super().__init__(menu)
        self.action = action
        self.menu = menu
        self.setObjectName("kindItem")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        column = QVBoxLayout(self)
        column.setContentsMargins(10, 6, 10, 6)
        column.setSpacing(0)
        column.addWidget(QLabel(name, self))
        column.addWidget(muted_label(help_line, self))

    def mouseReleaseEvent(self, _event) -> None:
        self.menu.close()
        self.action.trigger()


class LiquidPlanPage(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.plan = self._load_current()
        self._undo: list[LiquidPlan] = []
        self._redo: list[LiquidPlan] = []
        self.open_id = ""                        # the card open
        self.section: str | None = None          # what the left area asks
        self._before_trash: str | None = None
        self.slot: str | None = None             # the plate a new step opens on
        self._trash_slot: str | None = None      # the plate looked at for it
        self._overview_slot: str | None = None
        self._accepted: set[tuple[str, str]] = set()   # Use anyway
        self._locked = False
        self._shown_steps: tuple | None = None

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_MS)
        self._save_timer.timeout.connect(self._save_current)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2,
                                  SPACING * 2)
        layout.setSpacing(SPACING * 2)
        self.left = QStackedWidget(self)
        self.empty_box = self._empty()
        self.question = self._question()
        self.into_trash = self._into_trash()
        self.overview = self._overview()
        for widget in (self.empty_box, self.question, self.into_trash,
                       self.overview):
            self.left.addWidget(widget)
        layout.addWidget(self.left, 1)
        layout.addWidget(self._panel())

        session.robot_state_changed.connect(self._deck_changed)
        session.labware_changed.connect(self._deck_changed)
        session.profile_changed.connect(self._deck_changed)
        # A plate skipped on the Well center step.
        session.confirmations_changed.connect(self._deck_changed)
        session.robot_busy_changed.connect(self._robot_busy)
        self._install_shortcuts()
        session.set_liquid_plan(self.plan)
        self._deck_changed()

    # -- construction --------------------------------------------------------

    def _empty(self) -> QWidget:
        box = QWidget(self)
        column = QVBoxLayout(box)
        column.addStretch(1)
        column.addWidget(heading("Add the first step", 2, box),
                         alignment=Qt.AlignmentFlag.AlignHCenter)
        self.first_step_button = primary_button("+ Move liquid", box)
        self.first_step_button.setMinimumHeight(48)
        self.first_step_button.clicked.connect(
            lambda: self.add_step("move_liquid"))
        column.addWidget(self.first_step_button,
                         alignment=Qt.AlignmentFlag.AlignHCenter)
        column.addStretch(1)
        return box

    def _tool(self, text: str, name: str | None, slot) -> QPushButton:
        button = secondary_button(text, self)
        if name:
            button.setIcon(icon(name, COLORS["text-2"], 16))
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(slot)
        return button

    def _question(self) -> QWidget:
        box = QWidget(self)
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        top = QHBoxLayout()
        titles = QVBoxLayout()
        self.question_caption = caption("", box)
        self.question_title = heading("", 2, box)
        titles.addWidget(self.question_caption)
        titles.addWidget(self.question_title)
        top.addLayout(titles, 1)
        self.dest_kind = Segmented(["Wells", "Trash"], parent=box)
        self.dest_kind.changed.connect(self._dest_kind_chosen)
        top.addWidget(self.dest_kind)
        column.addLayout(top)

        tools = QHBoxLayout()
        self.dropdown = MiniDeckDropdown(box, placeholder="Choose the plate")
        self.dropdown.slot_chosen.connect(self._plate_chosen)
        tools.addWidget(self.dropdown)
        self.facts = muted_label("", box)
        self.facts.setWordWrap(False)
        tools.addWidget(self.facts)
        tools.addStretch(1)
        self.undo_button = self._tool("Undo", "undo", self.undo)
        self.redo_button = self._tool("Redo", "redo", self.redo)
        self.select_all_button = self._tool("Select all", None,
                                            self._select_all)
        self.clear_button = self._tool("Clear", None, lambda: self.plate.clear())
        for button in (self.undo_button, self.redo_button,
                       self.select_all_button, self.clear_button):
            tools.addWidget(button)
        column.addLayout(tools)

        self.no_plate = Banner(NO_PLATE, tone="warn", parent=box)
        self.no_plate.add_action("Back to Deck").clicked.connect(
            lambda: self.session.page_requested.emit("labware"))
        column.addWidget(self.no_plate)
        self.not_measured = Banner(NOT_MEASURED_TEXT, tone="warn", parent=box)
        self.measure_button = self.not_measured.add_action("Measure now")
        self.measure_button.clicked.connect(
            lambda: self.session.page_requested.emit("well_center"))
        self.use_anyway_button = self.not_measured.add_action("Use anyway")
        self.use_anyway_button.clicked.connect(self._use_anyway)
        column.addWidget(self.not_measured)

        self.plate = PlateGrid(parent=box)
        self.plate.selection_changed.connect(self._wells_chosen)
        column.addWidget(self.plate, 1)
        self.rule = QLabel(box)
        self.rule.setObjectName("planRule")
        self.rule.setWordWrap(True)
        column.addWidget(self.rule)
        return box

    def _into_trash(self) -> QWidget:
        box = QWidget(self)
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        column.addWidget(caption("PUT INTO", box))
        top = QHBoxLayout()
        top.addWidget(heading("Into the trash", 2, box), 1)
        self.trash_kind = Segmented(["Wells", "Trash"], current=1, parent=box)
        self.trash_kind.changed.connect(self._dest_kind_chosen)
        top.addWidget(self.trash_kind)
        column.addLayout(top)
        self.trash_where = muted_label("", box)
        column.addWidget(self.trash_where)
        self.trash_rule = muted_label("", box)
        column.addWidget(self.trash_rule)
        self.change_trash_button = secondary_button("", box)
        self.change_trash_button.clicked.connect(self._choose_trash)
        column.addWidget(self.change_trash_button,
                         alignment=Qt.AlignmentFlag.AlignLeft)
        column.addStretch(1)
        return box

    def _overview(self) -> QWidget:
        box = QWidget(self)
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        column.addWidget(caption("THE WHOLE PLAN", box))
        self.overview_title = heading("", 2, box)
        column.addWidget(self.overview_title)
        self.overview_choice = MiniDeckDropdown(box)
        self.overview_choice.slot_chosen.connect(self._overview_chosen)
        column.addWidget(self.overview_choice,
                         alignment=Qt.AlignmentFlag.AlignLeft)
        self.overview_plate = PlateGrid(parent=box)
        self.overview_plate.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        column.addWidget(self.overview_plate, 1)
        self.legend = QVBoxLayout()
        self.legend.setSpacing(2)
        column.addLayout(self.legend)
        return box

    def _panel(self) -> QWidget:
        panel = QWidget(self)
        panel.setFixedWidth(PANEL_WIDTH)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        self.locked_note = muted_label(LOCKED, panel)
        column.addWidget(self.locked_note)
        column.addWidget(caption("Start from", panel))
        self.new_plan_button = QPushButton("New plan\nStart with no steps",
                                           panel)
        self.new_plan_button.setObjectName("tile")
        self.new_plan_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.new_plan_button.setMinimumHeight(64)
        self.new_plan_button.clicked.connect(self.new_plan)
        column.addWidget(self.new_plan_button)

        self.trash_row = QPushButton(panel)
        self.trash_row.setObjectName("trashRow")
        self.trash_row.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.trash_row.clicked.connect(self._choose_trash)
        row = QHBoxLayout(self.trash_row)
        row.setContentsMargins(12, 6, 12, 6)
        self.trash_text = QLabel(self.trash_row)
        self.trash_action = QLabel(self.trash_row)
        self.trash_action.setObjectName("trashAction")
        for label in (self.trash_text, self.trash_action):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(self.trash_text, 1)
        row.addWidget(self.trash_action)
        column.addWidget(self.trash_row)

        self.step_list = StepList(panel)
        self.step_list.moveRequested.connect(self._moved)
        self.step_list.duplicateRequested.connect(self._duplicate)
        self.step_list.deleteRequested.connect(self.delete_step)
        self.step_list.openChanged.connect(self._opened)
        column.addWidget(self.step_list, 1)
        self.spacer = QWidget(panel)
        column.addWidget(self.spacer, 1)

        self.add_button = QPushButton("+ Add a step", panel)
        self.add_button.setObjectName("addStep")
        self.add_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.add_button.clicked.connect(self._open_add_menu)
        column.addWidget(self.add_button)
        self.add_menu = QMenu(self)
        for step_kind, name, help_line in KINDS:
            action = QWidgetAction(self.add_menu)
            action.setText(name)
            action.setDefaultWidget(_KindItem(action, name, help_line,
                                              self.add_menu))
            action.triggered.connect(
                lambda _c=False, k=step_kind: self.add_step(k))
            self.add_menu.addAction(action)

        self.card = _OpenCard(self)
        self.card.hide()
        self.next_button = self.card.next_button
        self.next_button.clicked.connect(self._next_section)
        self.card.delete_button.clicked.connect(
            lambda: self.delete_step(self.open_id))
        for n, row_ in enumerate(self.card.rows):
            row_.clicked.connect(lambda _c=False, n=n: self._row_clicked(n))
        return panel

    def _open_add_menu(self) -> None:
        self.add_menu.setMinimumWidth(self.add_button.width())
        height = self.add_menu.sizeHint().height()
        self.add_menu.popup(self.add_button.mapToGlobal(QPoint(0, -height)))

    def _install_shortcuts(self) -> None:
        """Only while the page is shown."""
        self._shortcuts = []
        for sequence, handler in ((QKeySequence.StandardKey.Undo, self.undo),
                                  (QKeySequence.StandardKey.Redo, self.redo),
                                  (QKeySequence.StandardKey.SelectAll,
                                   self._select_all)):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(handler)
            shortcut.setEnabled(False)
            self._shortcuts.append(shortcut)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        for shortcut in self._shortcuts:
            shortcut.setEnabled(True)
        self._show()

    def hideEvent(self, event) -> None:
        for shortcut in self._shortcuts:
            shortcut.setEnabled(False)
        super().hideEvent(event)

    # -- the plan file -------------------------------------------------------

    def _load_current(self) -> LiquidPlan:
        """A plan with the last one's trash and tip. The file may be a
        program, as the page saved it before plans."""
        path = plan_file()
        for load in (LiquidPlan.load, Program.load):
            try:
                last = load(path)
            except ProgramError:
                continue
            except OSError:
                break
            return LiquidPlan(trash=last.trash, tip_ul=last.tip_ul)
        return LiquidPlan()

    def _save_current(self) -> None:
        try:
            self.plan.save(plan_file())
        except OSError as exc:
            log.error("liquid plan: could not save it: %s", exc)

    # -- editing -------------------------------------------------------------

    def _edit(self) -> LiquidPlan:
        """A copy of the plan to change and give to `_commit`."""
        return self.plan.model_copy(deep=True)

    def _commit(self, plan: LiquidPlan) -> None:
        """`plan` is the plan now: one undo."""
        self._undo.append(self.plan)
        self._redo.clear()
        self._adopt(plan)

    def _adopt(self, plan: LiquidPlan) -> None:
        self.plan = plan
        self.session.set_liquid_plan(plan)
        self._save_timer.start()
        self._show()

    def undo(self) -> None:
        if self._undo and not self._locked:
            self._redo.append(self.plan)
            self._adopt(self._undo.pop())

    def redo(self) -> None:
        if self._redo and not self._locked:
            self._undo.append(self.plan)
            self._adopt(self._redo.pop())

    def new_plan(self) -> None:
        """No steps; the trash and what a tip holds stay."""
        self.section = None
        self._commit(LiquidPlan(trash=self.plan.trash,
                                tip_ul=self.plan.tip_ul))

    def add_step(self, step_kind: str) -> None:
        plan = self._edit()
        step = new_step(step_kind, plan.next_color())
        plan.steps.append(step)
        self._commit(plan)
        self.step_list.set_open(step.id)

    def delete_step(self, step_id: str) -> None:
        plan = self._edit()
        plan.steps = [s for s in plan.steps if s.id != step_id]
        self._commit(plan)

    def _duplicate(self, step_id: str) -> None:
        plan = self._edit()
        index = self._index(step_id)
        plan.steps.insert(index + 1, plan.steps[index].model_copy(
            deep=True, update={"id": str(uuid4()),
                               "color": plan.next_color()}))
        self._commit(plan)

    def _moved(self, step_id: str, to: int) -> None:
        self._commit(move_step(self.plan, self._index(step_id), to))
        show_toast(self, f"Step moved to position {to + 1}")

    def _index(self, step_id: str) -> int:
        return [s.id for s in self.plan.steps].index(step_id)

    def _open_step(self):
        return next((s for s in self.plan.steps if s.id == self.open_id), None)

    # -- the open card -------------------------------------------------------

    def _sections(self) -> tuple:
        step = self._open_step()
        return SECTIONS.get(step.kind, ()) if step is not None else ()

    def _opened(self, step_id: str) -> None:
        """The one card's rows go into the card that is open."""
        self.open_id = step_id
        if step_id:
            body = self.step_list.card(step_id).body
            layout = body.layout() or QVBoxLayout(body)
            layout.setContentsMargins(0, 0, 0, 0)
            self.card.setParent(body)
            layout.addWidget(self.card)
            self.card.show()
        else:
            self.card.hide()
            self.card.setParent(self)
        sections = self._sections()
        self.section = sections[0][0] if sections else None
        self._show_card()
        self._show_left()

    def _select(self, section: str | None) -> None:
        self.section = section
        self._show_card()
        self._show_left()

    def _row_clicked(self, n: int) -> None:
        self._select(self._sections()[n][0])

    def _next_section(self) -> None:
        sections = [role for role, _label in self._sections()]
        at = sections.index(self.section) if self.section in sections else -1
        if at + 1 < len(sections):
            self._select(sections[at + 1])
            return
        number = self._index(self.open_id) + 1
        self.step_list.set_open("")
        show_toast(self, f"Step {number} ready.")

    def _show_card(self) -> None:
        step = self._open_step()
        if step is None:
            return
        sections = self._sections()
        for n, row in enumerate(self.card.rows):
            row.setVisible(n < len(sections))
            if n < len(sections):
                role, label = sections[n]
                row.show_row(label, chip(getattr(step, role)),
                             QUESTIONS[role][3], role == self.section)
        self.card.amount.setVisible(step.kind == "move_liquid")
        if step.kind == "move_liquid":
            self.card.amount.setText(amount(step))
        roles = [role for role, _label in sections]
        at = roles.index(self.section) if self.section in roles else -1
        self.next_button.setText(f"Next: {sections[at + 1][1]}"
                                 if at + 1 < len(sections) else "Done")

    # -- the wells -----------------------------------------------------------

    def _plates(self) -> dict[str, tuple]:
        return {slot: (entry, definition)
                for slot, entry, definition in self.session.plates()}

    def _deck_changed(self, _arg=None) -> None:
        plates = self.session.plates()
        self.dropdown.set_tiles([self._tile(*plate) for plate in plates
                                 if plate[0].isdigit()])
        if self.slot not in {slot for slot, _e, _d in plates}:
            self.slot = plates[0][0] if plates else None
        self._show()

    def _tile(self, slot, entry, definition) -> DeckTile:
        measured = (self.session.well_center_status(slot, entry.load_name)
                    == MEASURED)
        return DeckTile(int(slot), name=definition.display_name,
                        kind=kind(definition), facts=facts(definition),
                        **({"note": "Measured", "note_tone": "ok"} if measured
                           else {"note": "Not measured", "note_tone": "warn"}))

    def _shown_place(self) -> Place | None:
        """The place the section asks about; None for the trash."""
        step = self._open_step()
        if step is None or self.section in (None, TRASH):
            return None
        return getattr(step, self.section)

    def _shown_slot(self) -> str | None:
        """The plate on the grid: the place's, else the one last chosen."""
        held = self._plates()
        place, trash = self._shown_place(), self.plan.trash
        if self.section == TRASH:
            slot = self._trash_slot or (trash.slot if trash else None)
        else:
            slot = place.slot if place is not None else None
        return slot if slot in held else self.slot

    def _chosen_wells(self) -> list[str]:
        if self.section != TRASH:
            place = self._shown_place()
            return list(place.wells) if place is not None else []
        trash = self.plan.trash
        return [trash.well] if trash is not None and trash.well else []

    def _plate_chosen(self, slot: int) -> None:
        """The trash's plate is not where the next step opens: a waste dish
        is rarely what a step works on."""
        if self.section == TRASH:
            self._trash_slot = str(slot)
            self._show_left()
            return
        self.slot = str(slot)
        place = self._shown_place()
        if place is not None and place.wells and place.slot != self.slot:
            self._set_wells([], self.slot)
            return
        self._show_left()

    def _wells_chosen(self, selection: set[str]) -> None:
        chosen = self._chosen_wells()
        if self.section is None or set(chosen) == selection:
            return
        if self.section in ONE_WELL:
            selection = (selection - set(chosen)) or selection
        self._set_wells(selection)

    def _set_wells(self, wells, slot: str | None = None) -> None:
        slot = slot or self._shown_slot()
        held = self._plates().get(slot)
        if held is None:
            return
        load_name = held[0].load_name
        ordered = by_row(Place(slot=slot, load_name=load_name,
                               wells=sorted(wells)),
                         self.session.liquid_plates())
        if self.section in ONE_WELL:
            ordered = ordered[:1]
        plan = self._edit()
        if self.section == TRASH:
            plan.trash = (Location(kind="well", slot=slot, load_name=load_name,
                                   well=ordered[0]) if ordered else None)
            if ordered:
                self.section = self._before_trash
        else:
            step = plan.steps[self._index(self.open_id)]
            setattr(step, self.section, Place(slot=slot, load_name=load_name,
                                              wells=ordered))
        self._commit(plan)

    def _select_all(self) -> None:
        if self.left.currentWidget() is self.question and (
                self.section not in ONE_WELL) and not self._locked:
            self.plate.select_all()

    def _dest_kind_chosen(self, index: int) -> None:
        """A click on the kind already chosen is one too: it keeps the
        wells."""
        plan = self._edit()
        step = plan.steps[self._index(self.open_id)]
        if (step.dest.kind == "trash") == bool(index):
            return
        step.dest = Place(kind="trash" if index else "wells")
        self._commit(plan)

    def _use_anyway(self) -> None:
        slot = self._shown_slot()
        if slot is not None:
            self._accepted.add((slot, self._plates()[slot][0].load_name))
            log.info("liquid plan: slot %s used with its drawing", slot)
        self._show_left()

    def _choose_trash(self) -> None:
        if self.section == TRASH:
            self._select(self._before_trash)
            return
        self._before_trash = self.section
        self._trash_slot = None
        self._select(TRASH)

    def _fills(self, slot: str | None, skip: tuple = ()) -> dict[str, str]:
        """Well -> the step's color, of the plan's wells on that plate but
        for the place `skip`, (step id, role)."""
        return {well: step.color for step in self.plan.steps
                for role, place in places(step)
                if place.kind == "wells" and place.slot == slot
                and (step.id, role) != skip for well in place.wells}

    # -- what the shell asks -------------------------------------------------

    def next_reason(self) -> str:
        """Why Next is gray, in one sentence."""
        problems = self.session.liquid_plan_problems()
        if problems.get("plan"):
            return problems["plan"][0]
        for number, step in enumerate(self.plan.steps, 1):
            if step.id in problems:
                return f"Finish step {number} to run this plan."
        return ""

    # -- display -------------------------------------------------------------

    def _robot_busy(self, _holder) -> None:
        self._show()

    def _show(self) -> None:
        problems = self.session.liquid_plan_problems()
        locked = self.session.robot_lease.holding_run
        if locked != self._locked:
            self._locked = locked
            self.step_list.set_locked(locked)
        shown = (list(self.plan.steps), {k for k in problems if k != "plan"})
        if shown != self._shown_steps:           # not on every robot tick
            self._shown_steps = shown
            self.step_list.set_steps(*shown)
        has_steps = bool(self.plan.steps)
        self.step_list.setVisible(has_steps)
        self.spacer.setVisible(not has_steps)
        self.locked_note.setVisible(locked)
        for widget in (self.new_plan_button, self.trash_row, self.add_button,
                       self.card, self.first_step_button):
            widget.setEnabled(not locked)
        self._show_trash_row()
        self._show_card()
        self._show_left()

    def _show_trash_row(self) -> None:
        trash = self.plan.trash
        self.trash_text.setText(
            f"Trash · {trash_place(trash)} · used by every step"
            if trash is not None else "Not set yet. Needed before running.")
        self.trash_action.setText("Change" if trash is not None else "Set")
        missing = trash is None
        if self.trash_row.property("missing") != missing:
            self.trash_row.setProperty("missing", missing)
            self.trash_row.style().unpolish(self.trash_row)
            self.trash_row.style().polish(self.trash_row)

    def _show_left(self) -> None:
        step = self._open_step()
        place = self._shown_place()
        if self.section == TRASH or (place is not None
                                     and place.kind == "wells"):
            self._show_question(step)
            self.left.setCurrentWidget(self.question)
        elif place is not None and place.kind == "trash":
            self._show_into_trash(step)
            self.left.setCurrentWidget(self.into_trash)
        elif self.plan.steps:
            self._show_overview()
            self.left.setCurrentWidget(self.overview)
        else:
            self.left.setCurrentWidget(self.empty_box)

    def _show_question(self, step) -> None:
        section = self.section
        title_caption, title, hint, color = QUESTIONS[section]
        number = (f"STEP {self._index(step.id) + 1} · "
                  if section != TRASH else "")
        self.question_caption.setText(number + title_caption)
        self.question_caption.setStyleSheet(f"color: {COLORS[color]};")
        self.question_title.setText(title)
        self.dest_kind.setVisible(section == "dest")
        self.dest_kind.set_current(0)

        held = self._plates()
        slot = self._shown_slot()
        entry = held.get(slot)
        self.dropdown.set_slot(int(slot) if slot and slot.isdigit() else None)
        self.no_plate.setVisible(not held)
        self.plate.setEnabled(entry is not None and not self._locked)
        self.plate.blockSignals(True)
        if self.plate.property("slot") != slot:
            self.plate.setProperty("slot", slot)
            self.plate.set_ordering(entry[1].ordering if entry else [])
        self.plate.selection_color = QColor(COLORS[color])
        self.plate.set_selection(self._chosen_wells(), record=False)
        self.plate.blockSignals(False)
        self.plate.set_dim_fills(self._fills(slot, (self.open_id, section)))
        self.plate.update()

        self.not_measured.setVisible(self._warns(slot, entry, section))
        self.facts.setText(self._facts(slot, entry))
        self.undo_button.setEnabled(bool(self._undo) and not self._locked)
        self.redo_button.setEnabled(bool(self._redo) and not self._locked)
        can_edit = entry is not None and not self._locked
        self.select_all_button.setEnabled(can_edit
                                          and section not in ONE_WELL)
        self.clear_button.setEnabled(can_edit and bool(self.plate.selection))

        rule = pairing(step) if section in ("source", "dest") else None
        warn = rule is not None and not rule.allowed
        sentence = rule.sentence if rule is not None else ""
        self.rule.setText(("ⓘ " if warn else "") + (sentence or hint))
        if self.rule.property("warn") != warn:
            self.rule.setProperty("warn", warn)
            self.rule.style().unpolish(self.rule)
            self.rule.style().polish(self.rule)

    def _warns(self, slot, entry, section: str) -> bool:
        """A plate a step uses, not measured and not taken as it is."""
        if entry is None or section == TRASH:
            return False
        load_name = entry[0].load_name
        return ((slot, load_name) not in self._accepted
                and self.session.well_center_status(slot, load_name)
                == NOT_MEASURED)

    def _facts(self, slot, entry) -> str:
        """What the tile beside it does not say."""
        if entry is None:
            return ""
        measured = (self.session.well_center_status(slot, entry[0].load_name)
                    == MEASURED)
        return "measured" if measured else "from drawing"

    def _show_into_trash(self, step) -> None:
        trash = self.plan.trash
        self.trash_kind.set_current(1)
        self.trash_where.setText(
            f"{trash_place(trash)}. It is set once for the whole plan; every "
            "step can use it." if trash is not None
            else "The trash is not set yet.")
        self.trash_rule.setText(pairing(step).sentence)
        self.change_trash_button.setText(
            "Change where the trash is" if trash is not None
            else "Set the trash")

    def _show_overview(self) -> None:
        steps = self.plan.steps
        self.overview_title.setText(
            f"{len(steps)} step{'' if len(steps) == 1 else 's'}, in this order")
        held = self._plates()
        used = [slot for slot in held if self._fills(slot)]
        if self._overview_slot not in used:
            self._overview_slot = used[0] if used else None
        slot = self._overview_slot
        self.overview_choice.set_tiles([self._tile(s, *held[s]) for s in used
                                        if s.isdigit()])
        self.overview_choice.set_slot(int(slot) if slot else None)
        self.overview_choice.setVisible(len(used) > 1)
        self.overview_plate.setVisible(slot is not None)
        if slot is not None:
            if self.overview_plate.property("slot") != slot:
                self.overview_plate.setProperty("slot", slot)
                self.overview_plate.set_ordering(held[slot][1].ordering)
            self.overview_plate.set_fills(self._fills(slot))
        while self.legend.count():
            line = self.legend.takeAt(0).widget()
            line.hide()
            line.deleteLater()
        for number, step in enumerate(steps, 1):
            self.legend.addWidget(QLabel(
                f'<span style="color: {step.color}">●</span> Step {number}: '
                + (" → ".join(step_chips(step)) or step_title(step)),
                self.overview))

    def _overview_chosen(self, slot: int) -> None:
        self._overview_slot = str(slot)
        self._show_overview()
