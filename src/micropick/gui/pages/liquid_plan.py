"""Liquid's Plan step (LIQUID.md §3): a plan of steps, by mouse.

A plan (`core.liquid_plan.LiquidPlan`) is an ordered list of steps, each read
as from → to. It is handed to the session as it is edited
(`Session.set_liquid_plan`): there is nothing to confirm. The step is done
while the plan has no problems (`Session.liquid_plan_problems`), and until
then Next is gray with one sentence (`next_reason`, read by the shell).

**Right:** Start from, the Trash row, the steps (`widgets.step_list`) and Add
a step, and beside the Trash row what a tip holds. One card is open at a time;
its rows choose the section. Take from comes before Put into; Amount and Extras
are fields in the card (`widgets.plan_forms`), as are the other kinds' How. A
step's sections are `sections`.

**Left:** what the open section asks: the plate from the `MiniDeckDropdown`,
its wells clicked, the wells of other steps dim, and under it the rule of how
sources pair with destinations (`core.liquid_plan.pairing`), warn-colored with
what would work. A plate not measured (nor skipped on the Well center step)
shows a warning with **Measure now** and **Use anyway**: it is not blocked,
the robot then uses the plate's drawing. For Amount and How, the heights drawn
(`widgets.well_heights`) following the fields; for Extras the step's plate, to
look at; for a Wait the time, for a Wait for me how its message will read. With
no card open: the whole plan's wells in each step's color and a legend; with
no steps, the first step's button.

The page holds the plan, every edit a new copy on its undo list: wells, a
move, the trash, a step added or deleted are one undo each (Ctrl+Z, and Undo
over the plate), and so is a field typed in digit by digit. The widgets never
reorder or change a plan themselves.

A point and By hand are on the live upper camera, with the X Y Z over it. Under
the picture are the points as buttons: the saved ones (`Session.saved_points`,
so never Robot setup's) and the memo points. A right click adds a memo point.
Using a memo point stores a **spot** in the step, its coordinates kept in the
plan, so it does not depend on what is in memory; one with no Z is stored at
the travel height, as saving it does. The star keeps a point under a name: it
becomes a saved point, for the steps using it too. Use this spot stores where
the tip is. Controls opens the jog pad, the face of a panel that is never shown
(`JogPanel.run_job` is the page's way to the robot); its keys go with it (#57),
so they are on only while it is on screen. With no picture the points and the
pad still work, and a banner goes to the camera's sheet.

A place that is not wells has no height of its own in the step: a point is used
at its Z, so the offset a new step has for a well is taken off it.

Still to come, with its ticket under #67: the trash chooser, Try once and
templates (#75).

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
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QInputDialog, QLabel, QMenu,
                               QPushButton, QScrollArea, QStackedWidget,
                               QToolButton, QVBoxLayout, QWidget, QWidgetAction)

from ... import paths
from ...core.liquid import Location, Program, ProgramError
from ...core.liquid_plan import (BY_HAND, BlowOutStep, Height, LiquidPlan,
                                 MixStep, MoveLiquid, MoveTip, Place, Spot,
                                 WaitForMe, WaitStep, by_row, chip, duration,
                                 height_words, move_step, pairing, places,
                                 step_chips, step_title)
from .. import camera_moves as moves
from ..auto_camera import CameraOpener
from ..labware_kinds import facts, kind, well_depth
from ..position_marks import PositionMarks
from ..session import MEASURED, NOT_MEASURED, Session, SessionError
from ..theme import COLORS, SPACING
from ..theme.factory import (caption, compact, danger_text_button,
                             double_spin_box, heading, muted_label, primary_button,
                             secondary_button, set_primary)
from ..theme.icons import icon
from ..widgets.banner import Banner
from ..widgets.camera_view import CameraView
from ..widgets.controls import Segmented
from ..widgets.jog_panel import JogPanel
from ..widgets.mini_deck import DeckTile, MiniDeckDropdown
from ..widgets.plan_forms import StepForm, uses_height
from ..widgets.plate_grid import PlateGrid
from ..widgets.step_list import StepList
from ..widgets.toast import show_toast
from ..widgets.well_heights import WellSection

__all__ = ["LiquidPlanPage", "CURRENT_FILE"]

TITLE = "Plan"

log = logging.getLogger(__name__)

PANEL_WIDTH = 460
# The plan is saved this long after the last edit.
SAVE_MS = 400
CURRENT_FILE = "current.json"

MAX_TIP_UL = 5000
POINT_ROW_PX = 52

NO_PLATE = "There is no plate on the deck. Add it on the Deck step."
NOT_MEASURED_TEXT = ("The well center of this plate was not measured. The "
                     "robot will use the plate's drawing.")
LOCKED = "The plan cannot change during a run."
NO_HEIGHT = "A point and the trash are used at their own height."
WAIT_HELP = "The robot does nothing for this long. Pause and Stop still work."
WAITING_WHY = "You asked for this in the plan."
NO_WELLS = "This step uses no wells."
NO_CAMERA = ("There is no camera picture. The points and Controls still work.")
POINTS_HELP = ("Right-click the picture to add a point. The star keeps a "
               "point for later experiments.")
BY_HAND_HELP = ("Bring the tip to the spot with Controls, then use it. The "
                "robot comes back here every time.")
NO_POSE = "The robot's position is not known yet."
MOUSE_HELP = ["Right click   add a point"]
NO_MAP = "Calibrate the camera first."
BOTTOM_MEASURED = "Bottom measured in Well center"
BOTTOM_DRAWN = "Bottom from the plate drawing"
TOP_NOTE = "Top of the well"
MIDDLE_NOTE = "Middle of the well"

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

# The section of the Trash row, for the plan and not a step.
TRASH = "trash"
# What a section's row says it is, and in which color.
LABELS = {"source": ("Take from", "tips"), "dest": ("Put into", "plate"),
          "wells": ("Wells", "plate"), "place": ("Where", "plate"),
          "amount": ("Amount", "text-2"), "extras": ("Extras", "text-2"),
          "how": ("How", "text-2"), "wait": ("How long", "text-2"),
          "message": ("Message", "text-2")}
# section -> (caption, title), of a section answered with fields.
ANSWERS = {
    "amount": ("AMOUNT AND HEIGHT", "How much, and how deep the tip goes"),
    "extras": ("EXTRAS", "Anything else in this step?"),
    "how": ("HOW", "How is it done?"),
    "wait": ("WAIT", "How long does the robot wait?"),
    "message": ("WAIT FOR ME", "What should the robot say?"),
}
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
BLOW_OUT_QUESTION = ("WHERE", "Where is the tip blown out?", "Click a well.",
                     "plate")
# The kinds a place can be: (place kind, on screen), by what asks it. One
# choice is no choice, and a section without an entry has only wells.
CHOICES = {
    "source": (("wells", "A well"), ("point", "A point"), ("spot", "By hand")),
    "dest": (("wells", "Wells"), ("point", "A point"), ("trash", "Trash")),
    "blow_out": (("wells", "A well"), ("point", "A point"),
                 ("trash", "Trash")),
    "move_tip": (("wells", "A well"), ("point", "A point"),
                 ("spot", "By hand")),
}
# The field of the height that goes with a place of a step.
HEIGHTS = {"source": "take_at", "dest": "put_at", "place": "at"}
# Sections that are one well: a click replaces it.
ONE_WELL = ("place", TRASH)

# The heights a new step starts from (LiquidPlan.dc.html).
TAKE_AT = Height(ref="bottom", offset_mm=2.0)
PUT_AT = Height(ref="top", offset_mm=-1.0)


def chosen_kind(place: Place) -> str:
    """Which of `CHOICES` the place is: a memo point is stored as a spot and
    is still A point."""
    if place.kind == "spot" and place.spot is not None and (
            place.spot.name != BY_HAND):
        return "point"
    return place.kind


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


def sections(step) -> tuple:
    """What an open step asks, in order."""
    if step.kind == "move_liquid":
        return ("source", "dest", "amount", "extras")
    if step.kind == "mix":
        return ("wells", "how")
    if step.kind == "blow_out":
        return ("place", "how")
    if step.kind == "move_tip":
        return ("place", "how") if uses_height(step) else ("place",)
    return ("wait",) if step.kind == "wait" else ("message",)


def amount(step: MoveLiquid) -> str:
    take, put = step.take_flow_ul_s, step.put_flow_ul_s
    speed = f"{take:g} µl/s" if take == put else f"{take:g} and {put:g} µl/s"
    return f"{step.volume_ul:g} µl per well · {speed}"


def extras(step: MoveLiquid) -> str:
    """The extras that are on, in a few words."""
    said = [text for on, text in (
        (step.mix_after and step.dest.kind == "wells", "mix after"),
        (step.pause_after_first, "pause after the first well"),
        (step.slow_lift and step.source.kind == "wells", "lift out slowly"),
        (step.wait_after_s > 0, f"wait {step.wait_after_s:g} s"),
        (not step.empty_at_end, "keep what is left")) if on]
    return ", ".join(said) or "none"


def how(step) -> str:
    if step.kind == "mix":
        return (f"{step.cycles}× · {step.volume_ul:g} µl · "
                f"{step.flow_ul_s:g} µl/s")
    if step.kind == "blow_out":
        return f"{step.flow_ul_s:g} µl/s"
    return height_words(step.at)


def section_value(step, section: str) -> str:
    """What a section's row says was answered."""
    if section == "amount":
        return amount(step)
    if section == "extras":
        return extras(step)
    if section == "how":
        return how(step)
    if section == "wait":
        return duration(step.seconds)
    if section == "message":
        return step.message or "choose"
    return chip(getattr(step, section))


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
        self.rows = [_Row(self) for _section in range(4)]
        for row in self.rows:
            column.addWidget(row)
        self.form = StepForm(self)
        column.addWidget(self.form)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 6, 0, 0)
        self.delete_button = danger_text_button("Delete", self)
        self.next_button = primary_button("Done", self)
        buttons.addWidget(self.delete_button)
        buttons.addStretch(1)
        buttons.addWidget(self.next_button)
        column.addLayout(buttons)


class _PointButton(QWidget):
    """A point: its star and its name, which uses it. ★ is kept in the
    profile; a click on ☆ keeps it."""

    def __init__(self, name: str, saved: bool, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("plain")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.star = QToolButton(self)
        self.star.setText("★" if saved else "☆")
        self.star.setEnabled(not saved)
        self.star.setToolTip("" if saved else "Keep this point.")
        self.star.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row.addWidget(self.star)
        self.use = secondary_button(name, self)
        self.use.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row.addWidget(self.use)


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
        self._typing: str | None = None          # the field of the last undo
        self.point_buttons: dict[str, _PointButton] = {}
        self._points_shown: list | None = None
        self._looking = False                    # the camera section is up
        self._camera_failed = False
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
        self.answer = self._answer()
        self.overview = self._overview()
        for widget in (self.empty_box, self.question, self.answer,
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
        session.memo_points_changed.connect(self._show_points)
        session.camera_opened.connect(lambda _l: self._show_camera())
        session.camera_closed.connect(lambda _l: self._show_camera())
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
        self.kinds: dict[str, Segmented] = {}
        for key, choices in CHOICES.items():
            self.kinds[key] = Segmented([text for _kind, text in choices],
                                        parent=box)
            self.kinds[key].changed.connect(
                lambda index, key=key: self._kind_chosen(key, index))
            top.addWidget(self.kinds[key])
        self.dest_kind = self.kinds["dest"]
        column.addLayout(top)
        self.question_body = QStackedWidget(box)
        self.on_plate = self._on_plate()
        self.into_trash = self._into_trash()
        self.on_camera = self._on_camera()
        for widget in (self.on_plate, self.into_trash, self.on_camera):
            self.question_body.addWidget(widget)
        column.addWidget(self.question_body, 1)
        return box

    def _on_plate(self) -> QWidget:
        box = QWidget(self)
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
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

    def _on_camera(self) -> QWidget:
        box = QWidget(self)
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        self.no_camera = Banner(NO_CAMERA, tone="warn", parent=box)
        self.no_camera_button = self.no_camera.add_action("Choose the camera")
        self.no_camera_button.clicked.connect(
            lambda: self.session.page_requested.emit("upper_camera"))
        column.addWidget(self.no_camera)

        self.opener = CameraOpener(self.session, self)
        self.opener.failed.connect(self._camera_refused)
        self.view = CameraView(box)
        self.view.marks_default = True           # the points are drawn there
        self.view.set_mouse_help(MOUSE_HELP)
        self.view.right_clicked.connect(self._mark)
        # Never shown: the pad over the picture is its face, and its keys go
        # with the pad.
        self.jog = JogPanel(self.session, shortcut_host=self, parent=self)
        self.jog.hide()
        self.jog.show_position_on(self.view)
        self.jog.pose_changed.connect(lambda _p: self._show_use_spot())
        self.jog.position_changed.connect(lambda _l: self._show_use_spot())
        self.marks = PositionMarks.on(self.session, self.view)
        self.controls_button = compact(secondary_button("Controls", self.view))
        self.controls_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Opaque: an outlined button over a bright dish cannot be read.
        self.controls_button.setStyleSheet(
            f"QPushButton {{ background: {COLORS['raised']}; }}")
        self.controls_button.clicked.connect(self._toggle_controls)
        self.view.add_top_widget(self.controls_button)
        column.addWidget(self.view, 1)

        self.point_row = QHBoxLayout()
        self.point_row.setContentsMargins(0, 0, 0, 0)
        self.point_row.setSpacing(SPACING)
        self.point_row.addStretch(1)
        holder = QWidget(box)
        holder.setObjectName("plain")
        holder.setLayout(self.point_row)
        self.point_area = QScrollArea(box)
        self.point_area.setWidget(holder)
        self.point_area.setWidgetResizable(True)
        self.point_area.setFrameShape(QFrame.Shape.NoFrame)
        self.point_area.setFixedHeight(POINT_ROW_PX)
        self.point_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        column.addWidget(self.point_area)

        line = QHBoxLayout()
        self.camera_help = muted_label("", box)
        line.addWidget(self.camera_help, 1)
        self.use_spot_button = primary_button("Use this spot", box)
        self.use_spot_button.clicked.connect(self._use_spot)
        line.addWidget(self.use_spot_button)
        column.addLayout(line)
        self.camera_rule = QLabel(box)
        self.camera_rule.setObjectName("planRule")
        self.camera_rule.setWordWrap(True)
        column.addWidget(self.camera_rule)
        return box

    def _into_trash(self) -> QWidget:
        box = QWidget(self)
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        column.addWidget(heading("Into the trash", 3, box))
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

    def _answer(self) -> QWidget:
        """The left side of a section answered in the card."""
        box = QWidget(self)
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        self.answer_caption = caption("", box)
        self.answer_title = heading("", 2, box)
        column.addWidget(self.answer_caption)
        column.addWidget(self.answer_title)
        self.answer_body = QStackedWidget(box)
        self.heights_box = self._heights()
        self.step_plate_box = self._step_plate()
        self.wait_box = self._wait_shown()
        self.message_box = self._message_shown()
        for widget in (self.heights_box, self.step_plate_box, self.wait_box,
                       self.message_box):
            self.answer_body.addWidget(widget)
        column.addWidget(self.answer_body, 1)
        return box

    def _heights(self) -> QWidget:
        box = QWidget(self)
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.setSpacing(SPACING * 3)
        self.take_section = WellSection(box)
        self.put_section = WellSection(box)
        row.addWidget(self.take_section, 1)
        row.addWidget(self.put_section, 1)
        column.addLayout(row, 1)
        self.no_height = muted_label(NO_HEIGHT, box)
        column.addWidget(self.no_height)
        return box

    def _step_plate(self) -> QWidget:
        box = QWidget(self)
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        self.step_plate_name = muted_label("", box)
        column.addWidget(self.step_plate_name)
        self.step_plate = PlateGrid(parent=box)
        self.step_plate.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        column.addWidget(self.step_plate, 1)
        return box

    def _wait_shown(self) -> QWidget:
        box = QWidget(self)
        column = QVBoxLayout(box)
        column.addStretch(1)
        self.wait_time = heading("", 1, box)
        column.addWidget(self.wait_time, alignment=Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(muted_label(WAIT_HELP, box),
                         alignment=Qt.AlignmentFlag.AlignHCenter)
        column.addStretch(1)
        return box

    def _message_shown(self) -> QWidget:
        box = QWidget(self)
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        column.addWidget(muted_label("The Run step will show:", box))
        self.message_strip = Banner("", tone="warn", parent=box)
        column.addWidget(self.message_strip)
        column.addWidget(muted_label(WAITING_WHY, box))
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
        self.trash_text.setWordWrap(True)
        beside = QHBoxLayout()
        beside.addWidget(self.trash_row, 1)
        beside.addWidget(QLabel("A tip holds", panel))
        self.tip_ul = double_spin_box(panel)
        self.tip_ul.setRange(1, MAX_TIP_UL)
        self.tip_ul.setDecimals(0)
        self.tip_ul.setSuffix(" µl")
        self.tip_ul.valueChanged.connect(self._tip_typed)
        beside.addWidget(self.tip_ul)
        column.addLayout(beside)

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
        self.card.form.edited.connect(self._edited)
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
        self._looking = False
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

    def _commit(self, plan: LiquidPlan, typing: str | None = None) -> None:
        """`plan` is the plan now: one undo, with what was before it. Typing
        on in the same field (`typing`) is still that undo."""
        if typing is None or typing != self._typing:
            self._undo.append(self.plan)
        self._typing = typing
        self._redo.clear()
        self._adopt(plan)

    def _adopt(self, plan: LiquidPlan) -> None:
        self.plan = plan
        self.session.set_liquid_plan(plan)
        self._save_timer.start()
        self._show()

    def undo(self) -> None:
        if self._undo and not self._locked:
            self._typing = None
            self._redo.append(self.plan)
            self._adopt(self._undo.pop())

    def redo(self) -> None:
        if self._redo and not self._locked:
            self._typing = None
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

    def _edited(self, key: str, values: dict) -> None:
        """A field of the open card."""
        plan = self._edit()
        step = plan.steps[self._index(self.open_id)]
        for field, value in values.items():
            setattr(step, field, value)
        self._commit(plan, typing=f"{step.id}:{key}")

    def _tip_typed(self, value: float) -> None:
        if value != self.plan.tip_ul:
            plan = self._edit()
            plan.tip_ul = value
            self._commit(plan, typing="tip_ul")

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
        return sections(step) if step is not None else ()

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
        asked = self._sections()
        self.section = asked[0] if asked else None
        self._show_card()
        self._show_left()

    def _select(self, section: str | None) -> None:
        self.section = section
        self._typing = None
        self._show_card()
        self._show_left()

    def _row_clicked(self, n: int) -> None:
        self._select(self._sections()[n])

    def _next_section(self) -> None:
        asked = self._sections()
        at = asked.index(self.section) if self.section in asked else -1
        if at + 1 < len(asked):
            self._select(asked[at + 1])
            return
        number = self._index(self.open_id) + 1
        self.step_list.set_open("")
        show_toast(self, f"Step {number} ready.")

    def _show_card(self) -> None:
        step = self._open_step()
        if step is None:
            return
        asked = self._sections()
        if self.section not in (*asked, TRASH):  # a place changed what it asks
            self.section = asked[-1]
        for n, row in enumerate(self.card.rows):
            row.setVisible(n < len(asked))
            if n < len(asked):
                label, color = LABELS[asked[n]]
                row.show_row(label, section_value(step, asked[n]), color,
                             asked[n] == self.section)
        self.card.form.show_section(step, self.section)
        at = asked.index(self.section) if self.section in asked else -1
        self.next_button.setText(f"Next: {LABELS[asked[at + 1]][0]}"
                                 if at + 1 < len(asked) else "Done")

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
        if step is None or self.section in (None, TRASH, *ANSWERS):
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

    def _kind_chosen(self, key: str, index: int) -> None:
        """A click on the kind already chosen is one too: it keeps what was
        chosen."""
        place_kind = CHOICES[key][index][0]
        if chosen_kind(self._shown_place()) != place_kind:
            self._set_place(Place(kind=place_kind))

    def _set_place(self, place: Place) -> None:
        """`place` for the section shown. See the module's docstring on what
        a place that is not wells does to the height."""
        plan = self._edit()
        step = plan.steps[self._index(self.open_id)]
        was_wells = getattr(step, self.section).kind == "wells"
        setattr(step, self.section, place)
        field = HEIGHTS.get(self.section)
        if place.kind != "wells":
            setattr(step, field, Height())
        elif not was_wells:
            setattr(step, field, {"source": TAKE_AT, "dest": PUT_AT}.get(
                self.section, Height()))
        if self.section == "dest" and place.kind != "wells":
            step.mix_after = False
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

    # -- points and By hand --------------------------------------------------

    def _toggle_controls(self) -> None:
        pad = self.jog.pad
        pad.setVisible(not pad.isVisible())
        if pad.isVisible():
            pad.raise_()

    def _mark(self, u: float, v: float) -> None:
        """A right click on the picture: a memo point where the tip would go
        down at this pixel, at the travel height. Moves nothing."""
        xy = moves.tip_at_pixel(self.session, self.jog, u, v, no_map=NO_MAP)
        if xy is not None:
            point = self.session.add_memo_point(xy[0], xy[1], None)
            self.jog.tell(f"{point.name} at ({point.x:.2f}, {point.y:.2f}).")

    def _use_point(self, name: str) -> None:
        """A saved point by its name; a memo point as a spot of the step."""
        point = self.session.point(name)
        if point is None:
            return
        self._set_place(
            Place(kind="point", point=name) if point.saved else
            Place(kind="spot", spot=Spot(name=name, x=point.x, y=point.y,
                                         z=self.session.point_z(point))))

    def _keep_point(self, name: str) -> None:
        """The star: asks its name, the memo point's by default. The steps
        using it use the kept point from then on."""
        new_name, chosen = QInputDialog.getText(
            self, "Keep this point", "Name", text=name)
        if not chosen:
            return
        point = self.session.memo_point(name)
        try:
            self.session.save_memo_point(name, new_name)
        except SessionError as exc:
            show_toast(self, str(exc))
            return
        plan = self._edit()
        kept = Place(kind="point", point=new_name.strip())
        used = False
        for step in plan.steps:
            for role, place in places(step):
                if place.kind == "spot" and (place.spot.name, place.spot.x,
                                             place.spot.y) == (
                        name, point.x, point.y):
                    setattr(step, role, kept)
                    used = True
        if used:
            self._commit(plan)

    def _use_spot(self) -> None:
        pose = self.jog.pose
        if pose is None:
            return
        self._set_place(Place(kind="spot", spot=Spot(
            name=BY_HAND, x=pose[0], y=pose[1], z=pose[2])))
        show_toast(self, "Spot saved for this step.")

    def _camera_refused(self, _label: str, reason: str) -> None:
        log.info("liquid plan: no picture: %s", reason)
        self._camera_failed = True
        self._show_camera()

    def _show_camera(self) -> None:
        label = self.session.upper_camera_label
        self.view.set_camera(self.session.camera(label) if label else None)
        self.no_camera.setVisible(self.view.camera is None and (
            label is None or self._camera_failed))

    def _show_use_spot(self) -> None:
        known = self.jog.pose is not None
        self.use_spot_button.setEnabled(known and not self._locked)
        self.use_spot_button.setToolTip("" if known else NO_POSE)

    def _show_points(self) -> None:
        """A button per point, made again only when the points change."""
        points = [(p.name, p.saved) for p in self.session.points]
        if points != self._points_shown:
            self._points_shown = points
            for button in self.point_buttons.values():
                self.point_row.removeWidget(button)
                button.deleteLater()
            self.point_buttons = {}
            holder = self.point_area.widget()
            saved_first = sorted(points, key=lambda point: not point[1])
            for at, (name, saved) in enumerate(saved_first):
                button = _PointButton(name, saved, holder)
                button.use.clicked.connect(
                    lambda _c=False, n=name: self._use_point(n))
                button.star.clicked.connect(
                    lambda _c=False, n=name: self._keep_point(n))
                self.point_row.insertWidget(at, button)
                self.point_buttons[name] = button
        place = self._shown_place()
        chosen = None
        if place is not None and place.kind == "point":
            chosen = place.point
        elif place is not None and place.kind == "spot" and place.spot:
            chosen = place.spot.name
        for name, button in self.point_buttons.items():
            set_primary(button.use, name == chosen)

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
        for widget in (self.new_plan_button, self.trash_row, self.tip_ul,
                       self.add_button, self.card, self.first_step_button,
                       self.point_area):
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
        if self.tip_ul.value() != self.plan.tip_ul:
            self.tip_ul.blockSignals(True)
            self.tip_ul.setValue(self.plan.tip_ul)
            self.tip_ul.blockSignals(False)
        missing = trash is None
        if self.trash_row.property("missing") != missing:
            self.trash_row.setProperty("missing", missing)
            self.trash_row.style().unpolish(self.trash_row)
            self.trash_row.style().polish(self.trash_row)

    def _show_left(self) -> None:
        step = self._open_step()
        if self.section in ANSWERS and step is not None:
            self._show_answer(step)
            self.left.setCurrentWidget(self.answer)
        elif self.section == TRASH or self._shown_place() is not None:
            self._show_question(step)
            self.left.setCurrentWidget(self.question)
        elif self.plan.steps:
            self._show_overview()
            self.left.setCurrentWidget(self.overview)
        else:
            self.left.setCurrentWidget(self.empty_box)
        self._camera_up()

    def _camera_up(self) -> None:
        """The picture and the pose as the camera section comes up."""
        looking = (self.isVisible() and self.left.currentWidget() is
                   self.question and self.question_body.currentWidget()
                   is self.on_camera)
        if looking and not self._looking:
            self._camera_failed = False
            self.opener.ensure(self.session.upper_camera_label)
            self._show_camera()
            if self.jog.controller is not None:
                self.jog.controller.limits = self.session.motion.envelope
            self.jog.refresh_position()
        self._looking = looking

    def _choice(self, step) -> str | None:
        """What in `CHOICES` asks the section shown."""
        if self.section == "place":
            return step.kind if step.kind in CHOICES else None
        return self.section if self.section in CHOICES else None

    def _show_question(self, step) -> None:
        section = self.section
        asked = (BLOW_OUT_QUESTION if section == "place"
                 and step.kind == "blow_out" else QUESTIONS[section])
        title_caption, title, _hint, color = asked
        number = (f"STEP {self._index(step.id) + 1} · "
                  if section != TRASH else "")
        self.question_caption.setText(number + title_caption)
        self.question_caption.setStyleSheet(f"color: {COLORS[color]};")
        self.question_title.setText(title)
        place = self._shown_place()
        place_kind = "wells" if place is None else chosen_kind(place)
        choice = self._choice(step) if step is not None else None
        for key, control in self.kinds.items():
            control.setVisible(key == choice)
            if key == choice:
                control.set_current([k for k, _text in CHOICES[key]]
                                    .index(place_kind))
        if place_kind == "trash":
            self._show_into_trash(step)
            self.question_body.setCurrentWidget(self.into_trash)
        elif place_kind in ("point", "spot"):
            self._show_on_camera(step, place_kind)
            self.question_body.setCurrentWidget(self.on_camera)
        else:
            self._show_plate(step, asked)
            self.question_body.setCurrentWidget(self.on_plate)

    def _show_on_camera(self, step, place_kind: str) -> None:
        by_hand = place_kind == "spot"
        self.point_area.setVisible(not by_hand)
        self.camera_help.setText(BY_HAND_HELP if by_hand else POINTS_HELP)
        self.use_spot_button.setVisible(by_hand)
        self._show_points()
        self._show_use_spot()
        self._show_camera()
        rule = pairing(step) if step.kind == "move_liquid" else None
        warn = rule is not None and not rule.allowed
        self.camera_rule.setText(("ⓘ " if warn else "")
                                 + (rule.sentence if rule is not None else ""))
        self.camera_rule.setVisible(bool(self.camera_rule.text()))
        if self.camera_rule.property("warn") != warn:
            self.camera_rule.setProperty("warn", warn)
            self.camera_rule.style().unpolish(self.camera_rule)
            self.camera_rule.style().polish(self.camera_rule)

    def _show_plate(self, step, asked: tuple) -> None:
        section = self.section
        _caption, _title, hint, color = asked
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
        self.trash_where.setText(
            f"{trash_place(trash)}. It is set once for the whole plan; every "
            "step can use it." if trash is not None
            else "The trash is not set yet.")
        self.trash_rule.setText(pairing(step).sentence
                                if step.kind == "move_liquid" else "")
        self.change_trash_button.setText(
            "Change where the trash is" if trash is not None
            else "Set the trash")

    def _show_answer(self, step) -> None:
        section = self.section
        title_caption, title = ANSWERS[section]
        self.answer_caption.setText(
            f"STEP {self._index(step.id) + 1} · {title_caption}")
        self.answer_title.setText(title)
        if section in ("amount", "how"):
            self._show_heights(step)
            self.answer_body.setCurrentWidget(self.heights_box)
        elif section == "extras":
            self._show_step_plate(step)
            self.answer_body.setCurrentWidget(self.step_plate_box)
        elif section == "wait":
            self.wait_time.setText(duration(step.seconds))
            self.answer_body.setCurrentWidget(self.wait_box)
        else:
            self.message_strip.set_text(step.message)
            self.answer_body.setCurrentWidget(self.message_box)

    def _note(self, height: Height, slot: str | None) -> str:
        """Where the height comes from, as the plate is now."""
        if height.ref != "bottom":
            return TOP_NOTE if height.ref == "top" else MIDDLE_NOTE
        plate = self.session.liquid_plates().get(slot)
        return (BOTTOM_MEASURED if plate is not None and plate.has_bottom
                else BOTTOM_DRAWN)

    def _depth(self, slot: str | None) -> float | None:
        entry = self._plates().get(slot)
        return well_depth(entry[1]) if entry is not None else None

    def _show_heights(self, step) -> None:
        if step.kind == "move_liquid":
            shown = [(step.source, "Take it at", "tips", step.take_at),
                     (step.dest, "Put it at", "plate", step.put_at)]
        else:
            place = step.wells if step.kind == "mix" else step.place
            shown = [(place, "Height", "plate", step.at), None]
        sections_ = (self.take_section, self.put_section)
        used = False
        for section, end in zip(sections_, shown):
            wells = end is not None and end[0].kind == "wells"
            section.setVisible(wells)
            if wells:
                used = True
                place, title, color, height = end
                section.show_height(title, color, height,
                                    self._note(height, place.slot),
                                    self._depth(place.slot))
        self.no_height.setVisible(not used)

    def _show_step_plate(self, step) -> None:
        """The wells it puts into, else those it takes from."""
        place = next((p for p in (step.dest, step.source)
                      if p.kind == "wells" and not p.empty), None)
        self.step_plate.setVisible(place is not None)
        self.step_plate_name.setText(chip(place) if place is not None
                                     else NO_WELLS)
        if place is None:
            return
        entry = self._plates().get(place.slot)
        if self.step_plate.property("slot") != place.slot:
            self.step_plate.setProperty("slot", place.slot)
            self.step_plate.set_ordering(entry[1].ordering if entry else [])
        self.step_plate.set_fills({well: step.color for well in place.wells})
        self.step_plate.set_dim_fills(self._fills(place.slot))

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
