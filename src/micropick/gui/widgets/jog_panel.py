"""Driving the robot by hand.

A third input backend for `JogController`, after the cv2 window and the global
hotkeys, and it shares the controller rather than the loop: the step sizes, the
soft limits, the saved positions and the undo history all live there, and this
widget only decides what a click and a key press mean.

A widget rather than a page, because four pages need it. Manual control is
one; the first step of either calibration is another, where a target has to
be centered before anything can be measured; and the check page needs it to
come back to the pose the camera was looking from. Those are the same act,
and a second D-pad would be a second place for the step list and the soft
limits to drift.

The face is the JogPad
----------------------
The arrows, Z Up / Down and the step carousel are `widgets.jog_pad.JogPad`
(REDESIGN §3), the same everywhere. It only says what was pressed; this
panel does what it did for its own buttons - the lease, the envelope,
`run_job`. On a page with a picture (`show_position_on`) the pad floats
over the picture's bottom-right corner, shown while this panel and its
Move section are; without one it sits in the Move section. Grayed while
the robot is not free, the reason in each button's tooltip. Its steps are
the controller's (`jog.DEFAULT_STEPS`).

Two sections of it fold
-----------------------
Not every page that needs the panel needs all of it. Centering a marker is
the pad and nothing else; the tip calibration usually drives to a stored
position and never jogs at all; the check page wants the stored positions
above everything. So Move and Positions are `theme.factory.Section`s and
each page says which of them start folded, rather than four panels or one
panel that guesses. A folded section is hidden, not merely empty, so it
costs no height on a short screen.

Positions are the profile's
---------------------------
There was a second, parallel store: `JogController.saved`, in memory, with
generated names, emptied on every disconnect - and beside it a "Remember in
profile" button writing to `profile.positions`, which is what everything
else reads. Two lists, one visible and one useful. The visible one is gone:
this list is `profile.positions`, with its coordinates beside each name,
and saving, renaming and deleting all write to the profile. `tip_calib`
taught by the pipette calibration, or by the notebook, appears here and can
be driven to, which is the point of having named a pose at all. What is
saved here is a pipette point; the camera poses (`observe`, `tip_calib`:
`position_marks.CAMERA_POSITIONS`) are only taught on their own pages, so
saving or renaming to one of those names is refused.

`shortcut_host` is the widget the key bindings are registered on, normally the
page. They are `WindowShortcut`: while the panel is on screen the keys reach
the robot from anywhere in the window, whatever has focus — the camera view,
a page tab, a combo box that was just clicked. The cv2 window
behaves the same way, and it is what an operator with one hand on the keyboard
and eyes on the picture expects; a shortcut that works only while the right
widget has focus is one that silently stops working after a click somewhere
else. They are disabled while the panel is hidden, because two pages each
holding one would otherwise both claim the arrow keys and leave Qt to guess
between them.

The layout is jog's, the keys are Settings'
-------------------------------------------
What the keys do comes from `jog.LAYOUT`; which key does it comes from
`gui.keys`, the defaults being jog's own (the arrows, PgUp and PgDn for Z)
and any of them rebound in Settings for a keyboard without them. The
shortcuts and the keys on the pad are both built from the same
two, so the list cannot name a key that is not bound; a saved change binds
the new keys at once (`_rebind`).

Every entry of `LAYOUT` is checked at import to have an action in
`gui.keys`: one without would produce a line of help for a key that does
nothing.

One key of the layout has no counterpart here, and it is listed rather than
dropped: `enter` finishes the cv2 window's loop, and this page has no loop to
finish. It is named in `UNBOUND`, with its reason, and filtered out of the
generated help so the help does not promise it.

Every move goes to a worker
---------------------------
`move_relative` blocks on HTTP. In the GUI thread that is a freeze on every
step, so each one runs in a `Worker` and the controls are disabled until it
returns. `JogController` already refuses a second move while one is in flight —
that is what stops a held-down arrow queueing moves that keep running after the
key is released — and this neither repeats that guard nor works around it.

Where the gantry is goes on the picture
---------------------------------------
The panel has no readout of its own. It says where the gantry is, and what a
refused or clamped step was, through `position_changed`, and the page hands
that to its `CameraView`, which draws it in a box in the top-left corner:
the robot's X, Y and Z, and what a step ran into - the step itself is on
the pad. The operator jogging is watching the picture; a readout at the
other side of the window was one more place to look.

Arrows as on the picture, when the picture is turned
---------------------------------------------------
The arrows mean the robot's axes: right is +X, up is +Y. Through the upper
camera that is also right and up on the picture. Through the lower camera,
which is turned and mirrored, it is not, and the tip goes the other way from
the arrow pressed. `view_axes` is for that: a 2x2 matrix from a step on the
picture, in pixel directions (right is +u, down is +v), to the robot's X and
Y - `TipTarget.axes`, for the lower camera. With it, each arrow is turned
into a picture direction and then into the robot axis nearest to it, so the
tip moves the way the arrow points; each pad button's tooltip names the
robot axis it moves, and the help says "as on the picture". A step is still one axis at a time.

XY only
-------
Nudging a tip onto a crosshair seen from below is a move in X and Y; a Z
step there pushes the tip into the disc or out of focus. `xy_only` takes Z
off the panel: the Z buttons are hidden, PgUp and PgDn are not bound (the
shortcuts stay disabled, so the keys go to whatever else wants them), and
the help does not list them.

The keys are on the pad: its Keys toggle, which H also turns on and off,
puts each button's key on it as a keycap and lists the step keys, the
panel's other keys and the page's own (`add_help`) under it. What the
mouse does is the picture's own row (`CameraView.set_mouse_help`).

A field has the keys
--------------------
The keys are window shortcuts, and a text or number field with the keyboard
takes the ones it can use first: the arrows move its cursor or step its
number, a letter is typed. Taking them from it would move the robot while
someone corrects a digit, so the field keeps them, and the pad says so
under its buttons (`TYPING_HELP`). Enter in the field, or a click on
the picture or on the page beside the fields, takes the keyboard off it and
the keys move the robot again.

And the saved positions, on a "points" box on the picture: the pose read
with every status line is sent on as `pose_changed`, and the
`gui.position_marks.PositionMarks` that `show_position_on` attaches draws
the profile's positions from it. While a job that moves is in flight the
pose is None, and the marks are off the picture.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QFontDatabase, QShortcut
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox,
                               QPlainTextEdit, QTextEdit, QVBoxLayout,
                               QWidget)

from ...workflows.jog import DEFAULT_STEPS, LAYOUT, JogController
from .. import keys
from ..position_marks import CAMERA_POSITIONS, PositionMarks
from ..session import Session
from ..theme import SPACING
from ..theme.factory import (Section, card, heading, primary_button,
                             secondary_button)
from .jog_pad import JogPad

# What the picture says while there is no robot to ask.
NO_ROBOT = "robot not connected"
# Why the pad is gray then.
NO_ROBOT_REASON = "Robot not connected."

# Under the pad while a field has the keyboard; see "A field has the keys".
TYPING_HELP = ("Keys now control the input field, not the robot.",
               "Press Enter or click the picture to move the robot again.")
_ENTER = (Qt.Key.Key_Return, Qt.Key.Key_Enter)


def _input_field(widget) -> QWidget | None:
    """The text or number field `widget` is, or None."""
    if isinstance(widget, QAbstractSpinBox):
        return widget
    if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
        return None if widget.isReadOnly() else widget
    return None
from ..workers import Worker

__all__ = ["JogPanel", "KEY_TO_QT", "UNBOUND", "SECTIONS", "HOME_TITLE",
           "HOME_DETAIL", "home_robot", "view_move", "check_view_axes"]

log = logging.getLogger(__name__)

# The position's name rides on its row, so the text can carry coordinates
# without the name having to be parsed back out of it.
POSITION_ROLE = Qt.ItemDataRole.UserRole


def _position_row(name: str, where) -> str:
    return f"{name:<14} {where[0]:8.2f} {where[1]:8.2f} {where[2]:7.2f}"


# Over the list, in the same columns as `_position_row`.
POSITION_HEADER = f"{'':<14} {'X':>8} {'Y':>8} {'Z':>7}"


def camera_name_refusal(name: str) -> str | None:
    """Why `name` cannot be given here, or None. A camera pose is taught on
    the page that uses it, with the camera on the spot; saved from this
    panel it would be taken for one and drawn and driven to as one."""
    tab = CAMERA_POSITIONS.get(name)
    if tab is None:
        return None
    return (f"{name!r} is where the camera looks from; set it on the {tab} "
            f"tab with Set position")


PANEL_WIDTH = 400

# Home, asked the same way from the panel's Machine card and from the status
# bar's button.
HOME_TITLE = "Home robot position?"
HOME_DETAIL = ("Home all axes. The undo history is cleared.")


def home_robot(robot) -> str:
    """Home all axes. Blocking: run it on a worker or through `run_job`."""
    robot.home_robot(verbose=False)
    log.info("homed")
    return "homed"

# The pose at the head of `JogController.status()`, "(x, y, z) ...", and
# its "x OUT by 0.30" for each axis out of the envelope.
POSE = re.compile(r"^\(\s*(-?[\d.]+),\s*(-?[\d.]+),\s*(-?[\d.]+)\)")
OUT = re.compile(r"\b[xyz] OUT by [\d.]+")

# The sections a page may ask to start folded, by name.
SECTIONS = ("move", "positions")

def action_id(key) -> str:
    """The `gui.keys` action of a `jog.LAYOUT` entry: "x+" for a move, the
    command's name otherwise."""
    if key.move is not None:
        axis, direction = key.move
        return f"{axis}{'+' if direction > 0 else '-'}"
    return key.command

# Layout entries this backend deliberately does not bind, and why. Listed
# rather than omitted: an entry that is simply absent is indistinguishable from
# one that was forgotten.
UNBOUND: dict[str, str] = {
    "finish": "the cv2 window's loop ends on Enter; this page has no loop",
}

# The commands this backend implements. Checked against the layout below.
HANDLED = {"step_up", "step_down", "save", "undo", "help"}


def _check_layout() -> None:
    """Fail at import rather than at a key press that does nothing."""
    known = {a.id for a in keys.ACTIONS}
    missing = [key.name for key in LAYOUT if key.command not in UNBOUND
               and action_id(key) not in known]
    if missing:
        raise RuntimeError(
            f"jog.LAYOUT has keys with no action in gui.keys: "
            f"{', '.join(missing)}. Add them there, or the help will list a "
            f"key that does nothing.")
    unknown = [key.command for key in LAYOUT
               if key.command is not None
               and key.command not in HANDLED and key.command not in UNBOUND]
    if unknown:
        raise RuntimeError(
            f"jog.LAYOUT has commands this page neither handles nor declares "
            f"unbound: {', '.join(unknown)}. Implement them in _command, or "
            f"name them in UNBOUND with a reason.")


_check_layout()


def _bound_layout() -> tuple:
    """The layout minus what this backend does not bind, for the help."""
    return tuple(key for key in LAYOUT if key.command not in UNBOUND)


# The picture direction of each arrow, in pixel terms: right is +u, up is -v.
# The arrows are declared in LAYOUT as robot moves (right = +x, up = +y),
# which is what they mean with no view_axes.
_ARROW_PIXELS = {("x", +1): (1.0, 0.0), ("x", -1): (-1.0, 0.0),
                 ("y", +1): (0.0, -1.0), ("y", -1): (0.0, 1.0)}



def check_view_axes(axes):
    """`axes` as a 2x2 tuple of floats, or None; refused if not 2x2 or
    singular, since every arrow has to land on some robot axis."""
    if axes is None:
        return None
    rows = tuple(tuple(float(v) for v in row) for row in axes)
    if len(rows) != 2 or any(len(row) != 2 for row in rows):
        raise ValueError(f"view_axes must be 2x2, got {axes!r}")
    (a, b), (c, d) = rows
    if abs(a * d - b * c) < 1e-6:
        raise ValueError(f"view_axes {axes!r} is singular")
    return rows


def view_move(axes, axis: str, direction: int) -> tuple[str, int]:
    """The robot step for an arrow, through `view_axes`: the arrow's picture
    direction mapped onto the robot's XY, then rounded to the axis it is
    nearest. An arrow for Z, or no `axes`, is the move as given."""
    if axes is None or axis not in ("x", "y"):
        return axis, direction
    du, dv = _ARROW_PIXELS[(axis, int(direction))]
    (a, b), (c, d) = axes
    rx, ry = a * du + b * dv, c * du + d * dv
    if abs(rx) >= abs(ry):
        return "x", (1 if rx > 0 else -1)
    return "y", (1 if ry > 0 else -1)


class JogPanel(QWidget):
    # Where the gantry is, and what the last step ran into, as lines for
    # `CameraView.set_position`. Emitted on every change; `position_lines`
    # holds the latest for a view connected later.
    position_changed = Signal(list)
    # A job that may have moved the gantry has finished. A page with
    # detections drawn on the picture drops them: they were measured from
    # where the camera was.
    moved = Signal()
    # The gantry's (x, y, z) from the latest status line, or None while a
    # job that moves is running or before the first read.
    pose_changed = Signal(object)

    def __init__(self, session: Session, *,
                 shortcut_host: QWidget | None = None,
                 machine_controls: bool = True,
                 collapsed: tuple[str, ...] = (),
                 view_axes=None,
                 xy_only: bool = False,
                 parent: QWidget | None = None):
        """`collapsed` names the sections that start folded; see SECTIONS.

        A name that is not a section is a caller's typo rather than a new
        section, so it is refused here instead of quietly doing nothing.

        `view_axes`, a 2x2 matrix from picture pixels to robot XY, makes the
        arrows move the tip as on a turned or mirrored picture; see "Arrows
        as on the picture". `set_view_axes` changes it later.

        `xy_only` hides Z and unbinds PgUp and PgDn; see "XY only".
        """
        super().__init__(parent)
        unknown = [name for name in collapsed if name not in SECTIONS]
        if unknown:
            raise ValueError(f"no such jog panel section(s): "
                             f"{', '.join(unknown)}; have {', '.join(SECTIONS)}")
        self.session = session
        self.controller: JogController | None = None
        self._worker: Worker | None = None
        self._then = None                  # run_job's, for the job in flight
        # A lease lent by the work this panel jogs for (`lend`), or None.
        self._lent = None
        self._shortcuts: list[QShortcut] = []
        self._all_shortcuts: list[QShortcut] = []
        self._movers: list[QWidget] = []
        self._collapsed = tuple(collapsed)
        self._status = NO_ROBOT
        self._message = ""
        self._job_moves = True
        self._view_axes = check_view_axes(view_axes)
        self._xy_only = bool(xy_only)
        self._z_shortcuts: list[QShortcut] = []
        # The page's own lines, kept apart so the jog keys' part can be
        # generated again when an option changes what they say.
        self._extra_help: list[str] = []
        self._help = self._key_help()
        self._typing = False                # a field on the page has the keys
        self._pad_view = None               # the picture the pad floats over
        self.pose: tuple[float, float, float] | None = None

        # Not a scroll area itself. The page that hosts it may put it in one
        # (theme.factory.scroll_column), which takes no focus, so PageUp and
        # PageDown - the Z axis - still reach the window-level shortcuts.
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING)
        column.addWidget(self._move_card())
        column.addWidget(self._positions_card())
        if machine_controls:
            # Off during a calibration: homing halfway through centering the
            # marker throws away the pose the whole sweep is planned around.
            column.addWidget(self._machine_card())
        else:
            self.home_button = self.retract_button = None
        column.addStretch(1)

        self._host = shortcut_host or self
        self._install_shortcuts(self._host)
        # A click beside the fields; see "A field has the keys".
        self._host.installEventFilter(self)
        QApplication.instance().focusChanged.connect(self._focus_moved)

        session.robot_state_changed.connect(self._on_robot_changed)
        session.settings_changed.connect(lambda _s: self._rebind())
        session.profile_changed.connect(self._on_profile_changed)
        # A bound method, not a lambda: the tip changes on a worker thread,
        # and only a slot with a receiver is queued to this one.
        session.tip_changed.connect(self._on_tip_changed)
        session.robot_busy_changed.connect(self._on_robot_busy)

        self._on_robot_changed(session.robot_state)
        self._emit_help()

    # -- construction --------------------------------------------------------

    def _move_card(self) -> QWidget:
        box = Section("Move", collapsed="move" in self._collapsed, parent=self)
        self.move_section = box

        self.pad = JogPad(steps=DEFAULT_STEPS,
                          bindings=self.session.settings.keys, parent=box.body)
        self.pad.jog.connect(self._move)
        self.pad.step_changed.connect(self._pad_step)
        self.pad.set_xy_only(self._xy_only)
        box.body.layout().addWidget(self.pad)
        box.toggle.toggled.connect(lambda _on: self._sync_pad())

        # Undo belongs with the steps it undoes. It sat under Positions,
        # where "Undo" beside a list of saved poses read as if it would undo
        # the saving; here, next to the pad, and named for what it does.
        self.undo_button = secondary_button("Undo step", self)
        self.undo_button.setToolTip(
            "Undo the last jog step.")
        self.undo_button.clicked.connect(lambda: self._command("undo"))
        self._movers.append(self.undo_button)
        box.body.layout().addWidget(self.undo_button)
        self._label_pad()
        return box

    def _label_pad(self) -> None:
        """Each arrow's robot axis in its tooltip when the arrows mean the
        picture's directions; none when they are the robot's own."""
        hints = {}
        if self._view_axes is not None:
            for action in ("x+", "x-", "y+", "y-"):
                axis, direction = view_move(self._view_axes, action[0],
                                            1 if action[1] == "+" else -1)
                hints[action] = (f"Robot {'+' if direction > 0 else '-'}"
                                 f"{axis.upper()}.")
        self.pad.set_hints(hints)

    def _positions_card(self) -> QWidget:
        """The profile's named poses: what is stored, where, and how to go.

        Double click drives to one, because that is what a list of places
        invites; the button is there for anyone who does not try it.
        """
        box = Section("Positions", collapsed="positions" in self._collapsed,
                      parent=self)
        self.positions_section = box

        fixed = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        header = QLabel(POSITION_HEADER, self)
        header.setFont(fixed)
        # The list's frame and its items' padding, so the letters sit over
        # the numbers' last digits.
        header.setContentsMargins(4, 0, 0, 0)
        box.body.layout().addWidget(header)

        self.saved = QListWidget(self)
        # PageUp and PageDown belong to the Z axis on this page, and a list
        # with focus would scroll on them instead.
        self.saved.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.saved.setMinimumHeight(110)
        self.saved.setFont(fixed)
        self.saved.itemDoubleClicked.connect(lambda _item: self._goto())
        self.saved.currentItemChanged.connect(lambda *_: self._refresh_buttons())
        box.body.layout().addWidget(self.saved)

        row = QHBoxLayout()
        self.goto_button = secondary_button("Go to", self)
        self.goto_button.clicked.connect(self._goto)
        self.rename_button = secondary_button("Rename…", self)
        self.rename_button.clicked.connect(self._rename)
        self.delete_button = secondary_button("Delete", self)
        self.delete_button.clicked.connect(self._delete)
        for button in (self.goto_button, self.rename_button,
                       self.delete_button):
            row.addWidget(button)
        self._movers.append(self.goto_button)
        box.body.layout().addLayout(row)

        self.remember_button = primary_button("Save this position…", self)
        self.remember_button.setToolTip(
            "Save the current position to the profile.")
        self.remember_button.clicked.connect(self._remember)
        self._movers.append(self.remember_button)
        box.body.layout().addWidget(self.remember_button)

        self.positions_note = QLabel()
        self.positions_note.setWordWrap(True)
        box.body.layout().addWidget(self.positions_note)
        return box

    def _machine_card(self) -> QWidget:
        """Home and retract, kept apart from the jogging controls.

        Both move the gantry a long way from wherever it is, and neither is
        undoable, so they do not sit beside a button that moves 0.05 mm."""
        box = card(self)
        box.layout().addWidget(heading("Machine", 3))
        row = QHBoxLayout()
        self.home_button = secondary_button("Home robot position", self)
        self.home_button.clicked.connect(self._home)
        self.retract_button = secondary_button("Retract Z", self)
        self.retract_button.clicked.connect(self._retract)
        row.addWidget(self.home_button)
        row.addWidget(self.retract_button)
        row.addStretch(1)
        self._movers += [self.home_button, self.retract_button]
        box.layout().addLayout(row)
        return box

    # -- keys ----------------------------------------------------------------

    def _install_shortcuts(self, host: QWidget) -> None:
        """One shortcut per Qt spelling of every bound key in the layout.

        WindowShortcut: these reach the robot from anywhere in this window
        while the host is visible, and from no other window. A global hotkey
        would drive it from whatever application the operator happened to be
        typing in, which is the reason `jog_in_window` exists in preference
        to `jog_with_hotkeys`. Widgets that need a key for themselves still
        get it: Qt asks the focus widget first, and a line edit or a spin box
        keeps the characters it edits with. A plain button or a list does not
        ask, so the arrow keys belong to the gantry even with focus there.
        """
        bindings = self.session.settings.keys
        for key in LAYOUT:
            if key.command in UNBOUND:
                continue
            for sequence in keys.sequences(bindings, action_id(key)):
                shortcut = QShortcut(sequence, host)
                shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
                if key.move is not None:
                    axis, direction = key.move
                    shortcut.activated.connect(
                        lambda a=axis, d=direction: self._move(a, d))
                else:
                    shortcut.activated.connect(
                        lambda c=key.command: self._command(c))
                self._all_shortcuts.append(shortcut)
                if key.command == "help":
                    # Left out of the disable set: reading the key list is
                    # exactly what is wanted when nothing else works yet.
                    continue
                self._shortcuts.append(shortcut)
                if key.move is not None and key.move[0] == "z":
                    self._z_shortcuts.append(shortcut)

    def _rebind(self) -> None:
        """The keys again, after Settings changed them."""
        for shortcut in self._all_shortcuts:
            shortcut.setEnabled(False)
            shortcut.setParent(None)
            shortcut.deleteLater()
        self._all_shortcuts, self._shortcuts, self._z_shortcuts = [], [], []
        self._install_shortcuts(self._host)
        self._set_enabled(self._worker is None)
        self._help = self._key_help()
        self._emit_help()

    # -- acting --------------------------------------------------------------

    def _move(self, axis: str, direction: int) -> None:
        if self.controller is None or self._busy():
            return
        if self._xy_only and axis == "z":
            return

        controller = self.controller
        axis, direction = view_move(self._view_axes, axis, direction)

        def job():
            # status() re-reads the pose, so a step costs one extra GET. At a
            # key press per step that is nothing.
            return controller.move(axis, direction), controller.status()

        self._run(Worker(job))

    def _command(self, command: str) -> None:
        if command == "help":
            # Before the robot check: reading the keys is exactly what is
            # wanted when nothing else works yet.
            self.pad.keys.toggle()
            return
        if self.controller is None:
            return
        if self._busy():
            return
        controller = self.controller

        if command == "step_up":
            self.pad.step_by(+1)              # the pad tells the controller
        elif command == "step_down":
            self.pad.step_by(-1)
        elif command == "save":
            # The same act as the button: one store, and it asks for a name.
            # It used to put the pose in the controller's own dict under a
            # generated name, which is the list nothing else could see.
            self._remember()
        elif command == "undo":
            self._run(Worker(lambda: (controller.undo(), controller.status())))
        else:                                  # _check_layout rules this out
            raise RuntimeError(f"unhandled jog command {command!r}")

    def _chosen_position(self) -> str | None:
        item = self.saved.currentItem()
        return None if item is None else item.data(POSITION_ROLE)

    def _select_position(self, name: str) -> None:
        """Leave the row for `name` chosen. After a rename or a save the
        position the operator was working with is still the one in hand;
        losing the selection there means the next click is on whatever the
        sorting put in its place."""
        for index in range(self.saved.count()):
            if self.saved.item(index).data(POSITION_ROLE) == name:
                self.saved.setCurrentRow(index)
                return

    def _goto(self) -> None:
        name = self._chosen_position()
        if name is None or self.controller is None or self._busy():
            return
        where = (self.session.profile.positions or {}).get(name)
        if where is None:
            return
        why = self.session.motion.refused(where)
        if why:
            self._say(f"{name!r} is outside the soft limits: {why}", firm=True)
            return
        controller, motion = self.controller, self.session.motion

        # Motion, not the controller's move_to: a pose taught at the top of
        # Z is out of bounds for the robot, and one moveToCoordinates from
        # wherever the tip is drags it across the deck. See
        # `workflows.motion`.
        def job():
            motion.tip_to(where[:2], where[2])
            return f"at {name!r}", controller.status()

        self._run(Worker(job))

    def _rename(self) -> None:
        name = self._chosen_position()
        if name is None or self.session.profile is None or self._busy():
            return
        new, ok = QInputDialog.getText(self, "Rename position",
                                       f"New name for {name!r}:", text=name)
        new = new.strip()
        if not ok or not new or new == name:
            return
        why = camera_name_refusal(new)
        if why is not None:
            self._say(why, firm=True)
            return
        try:
            self.session.rename_position(name, new)
        except Exception as exc:                 # noqa: BLE001
            self._say(str(exc), firm=True)
            return
        self._select_position(new)

    def _delete(self) -> None:
        name = self._chosen_position()
        if name is None or self.session.profile is None or self._busy():
            return
        where = self.session.profile.positions.get(name, ())
        answer = QMessageBox.question(
            self, "Delete position",
            f"Delete {name!r} "
            f"({', '.join(f'{v:.2f}' for v in where)}) from profile "
            f"{self.session.profile.name!r}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.session.forget(name)
        except Exception as exc:                 # noqa: BLE001
            self._say(str(exc), firm=True)

    def _remember(self) -> None:
        if self.controller is None or self._busy():
            return
        if self.session.profile is None:
            self._say("no profile loaded", firm=True)
            return
        name, ok = QInputDialog.getText(
            self, "Save this position",
            f"Name this deck position in profile {self.session.profile.name!r}:")
        name = name.strip()
        if not ok or not name:
            return
        why = camera_name_refusal(name)
        if why is not None:
            self._say(why, firm=True)
            return
        controller, session = self.controller, self.session

        def job():
            session.remember(name, controller.position)
            return None, controller.status()

        self._run(Worker(job), moves=False, robot_work=False)
        # The pose just saved is the one in hand; Go to should need no
        # further aiming.
        self._select_position(name)

    def _home(self) -> None:
        if not self._confirm(HOME_TITLE, HOME_DETAIL):
            return
        robot, controller = self.session.robot, self.controller

        def job():
            # None, not the HTTP response: _job_done reads the first item as
            # a MoveResult, and a home has no clamp or refusal to report.
            home_robot(robot)
            return None, controller.status()

        self._run(Worker(job))

    def _retract(self) -> None:
        if not self._confirm(
                "Retract the Z axis?",
                "The left Z axis travels to the top of its range."):
            return
        robot, controller = self.session.robot, self.controller

        def job():
            robot.retract_axis("leftZ", verbose=False)
            return None, controller.status()

        self._run(Worker(job))

    def _confirm(self, title: str, detail: str) -> bool:
        if self.controller is None or self._busy():
            return False
        answer = QMessageBox.question(
            self, title, detail,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        # `==`, not `is`: the answer can come back as a plain int (the profile
        # page has the same note), and `is` never matches it.
        return answer == QMessageBox.StandardButton.Yes

    # -- for the pages that host one -----------------------------------------

    @property
    def busy(self) -> bool:
        """A step is in flight. A page that reads the pose after the operator
        is done should wait for this to clear first."""
        return self._busy()

    def run_job(self, fn, *, moves: bool = True, what: str | None = None,
                then=None, run: bool = False, plates=()) -> bool:
        """Run `fn(log)` on this panel's worker, after any step in flight.

        The one way to the robot on a page with a panel: a click-move or an
        aspirate from the page and a jog key can then never overlap, and the
        controls grey out for all of them. It claims the robot's lease
        (`Session.robot_lease`), so it cannot overlap work on another page
        either. `fn` may return a MoveResult, None, or a sentence to show
        under the position. Returns False, and runs nothing, while the panel
        is busy, the robot is busy elsewhere, or there is no robot.

        `then(result)`, if given, is called on the GUI thread with what `fn`
        returned, once it has; what it returns is shown in place of that.
        For a job whose answer the page acts on - asking before it saves.
        `run`: a run that holds the robot for minutes (`Lease.run`), going
        into `plates` - (slot, load name) - whose well centers then stay.
        """
        if self.controller is None or self._busy():
            return False
        controller = self.controller

        def job(log):
            return fn(log), controller.status()

        # `moves=False` for a job that leaves the gantry where it is, such
        # as an aspirate: what was detected on the picture still stands.
        if not self._run(Worker(job, what=what), moves=moves, run=run,
                         plates=plates):
            return False
        self._then = then
        return True

    def refresh_position(self) -> None:
        """Read the pose again, if nothing is running: after the robot was
        moved by something that did not go through this panel."""
        if self.controller is None or self._busy():
            return
        controller = self.controller
        self._run(Worker(lambda: (None, controller.status())), moves=False,
                  robot_work=False)

    def lend(self, lease) -> None:
        """Work under someone else's lease until `end_loan`: the pipette
        calibration's touch-up, jogged here while the calibration that holds
        the robot waits. The steps take no lease of their own."""
        self._lent = lease
        self._set_enabled(self._worker is None)

    def end_loan(self) -> None:
        self._lent = None
        self._set_enabled(self._worker is None)

    def tell(self, text: str) -> None:
        """A line under the position on the picture, as it is given."""
        self._message = text
        self._emit_position()

    def set_step(self, value: float) -> None:
        """Choose the step from outside, e.g. 0.05 mm for a touch-up: the
        nearest of the pad's sizes."""
        self.pad.set_step(float(value), emit=True)

    # -- the worker ----------------------------------------------------------

    def _busy(self) -> bool:
        return self._worker is not None and self._worker.running

    def _run(self, worker: Worker, *, moves: bool = True,
             robot_work: bool = True, run: bool = False,
             plates=()) -> bool:
        """Start `worker` as this panel's job. Robot work - anything that
        moves the robot - claims the robot's lease first, or runs under a
        lent one; refused, nothing starts and False is returned. A read
        (`robot_work=False`) takes none."""
        if robot_work and self._lent is None:
            what = worker.what or ("moving the robot" if moves
                                   else "talking to the robot")
            lease = self.session.robot_lease.claim(what, run=run,
                                                   plates=plates)
            if lease is None:
                self.tell(self.session.robot_lease.refusal)
                return False
            worker.lease = lease
        self._worker = worker
        worker.what = worker.what or ("moving the robot" if moves
                                      else "talking to the robot")
        self._job_moves = moves
        # A read says nothing of its own: what the last job said - why a
        # move failed - stays under the pose it reads.
        self._job_reads = not robot_work
        # Bound methods, not lambdas: Qt takes a connection's thread from the
        # receiver, and a lambda has none, so it would touch these widgets from
        # the worker's thread.
        worker.finished.connect(self._job_done)
        worker.failed.connect(self._job_failed)
        worker.message.connect(self._job_said)
        worker.start()
        self._set_enabled(False)
        if moves:
            self._set_pose(None)
        return True

    def _job_said(self, text: str) -> None:
        log.info("%s", text)

    def _claim(self) -> bool:
        """True if the signal came from the worker still in flight.

        These slots are queued, so a worker can finish, be replaced, and only
        then have its completion delivered. Clearing `_worker` unconditionally
        would mark the newer one idle and let a second move be requested while
        the first is still running.
        """
        if self.sender() is not self._worker:
            return False
        self._worker = None
        return True

    def _job_done(self, payload) -> None:
        if not self._claim():
            return
        result, status = payload
        self._set_status(status)
        self._refresh_saved()
        then, self._then = self._then, None
        if then is not None:
            result = then(result)
        if isinstance(result, str):
            self.tell(result)
        elif result is None:
            if not self._job_reads:
                self._say("")
        elif not result.ok:
            self._say(result.reason or "refused", firm=True)
        elif result.clamped or result.reason:
            self._say(result.reason, firm=False)
        else:
            self._say("")
        self._set_enabled(True)
        if self._job_moves:
            self.moved.emit()

    def _job_failed(self, reason: str) -> None:
        if not self._claim():
            return
        self._then = None
        self._say(reason, firm=True)
        self._set_enabled(True)
        # A failed move may still have gone part of the way.
        if self._job_moves:
            self.moved.emit()
            # Read where it ended, so the pose - and the marks - come back.
            if self.controller is not None:
                controller = self.controller
                self._run(Worker(lambda: (None, controller.status())),
                          moves=False, robot_work=False)

    # -- display -------------------------------------------------------------

    @property
    def position_lines(self) -> list[str]:
        return [self._status] + ([self._message] if self._message else [])

    def show_position_on(self, view) -> None:
        """Keep `view` (a CameraView) showing where the gantry is, with the
        pad over its bottom-right corner, from now."""
        self.position_changed.connect(view.set_position)
        view.set_position(self.position_lines)
        self._pad_view = view
        view.add_corner_widget(self.pad)
        self._sync_pad()
        view.installEventFilter(self)       # a click on it gives the keys back
        PositionMarks.on(self.session, view).follow(self)

    def _sync_pad(self) -> None:
        """Over the picture, the pad is shown while this panel and its Move
        section are; in the section it simply goes with it."""
        if self._pad_view is None:
            return
        self.pad.setVisible(self.isVisible()
                            and not self.move_section.collapsed)
        if self.pad.isVisible():
            self.pad.raise_()

    @property
    def help_lines(self) -> list[str]:
        """The keys, "keys   what they do", or what a field with the
        keyboard means for them."""
        if self._typing:
            return list(TYPING_HELP)
        return list(self._help)

    # -- a field with the keys ------------------------------------------------

    def _focus_moved(self, _old, new) -> None:
        field = _input_field(new)
        typing = field is not None and self._host.isAncestorOf(field)
        if typing:
            field.installEventFilter(self)      # for its Enter
        if typing != self._typing:
            self._typing = typing
            self._emit_help()

    def eventFilter(self, watched, event) -> bool:
        """Enter in a field, a click on the picture or the page: the
        keyboard off the field. Nothing is taken from anyone: the field
        still gets its Enter, the picture its click."""
        if not self._typing:
            return False
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress:
            # Not a click in a field: that one places its cursor.
            if _input_field(watched) is None:
                self._release_keys()
        elif (kind == QEvent.Type.KeyPress and event.key() in _ENTER
              and isinstance(watched, (QLineEdit, QAbstractSpinBox))):
            # After the field has had it, so the number is taken first.
            QTimer.singleShot(0, self._release_keys)
        return False

    def _release_keys(self) -> None:
        field = _input_field(QApplication.focusWidget())
        if field is not None and self._host.isAncestorOf(field):
            field.clearFocus()

    def add_help(self, lines) -> None:
        """A page's own keys and mouse actions, after the jog keys."""
        self.set_page_help(self._extra_help + [str(line) for line in lines])

    def set_page_help(self, lines) -> None:
        """The page's own lines, replacing what it added before: for keys
        rebound in Settings."""
        self._extra_help = [str(line) for line in lines]
        self._help = self._key_help()
        self._emit_help()

    def _key_help(self) -> list[str]:
        """The jog keys as the options make them, then the page's lines."""
        layout = _bound_layout()
        if self._xy_only:
            layout = tuple(key for key in layout
                           if key.move is None or key.move[0] != "z")
        if self._view_axes is not None:
            layout = tuple(replace(key, help="move as on the picture")
                           if key.move is not None and key.move[0] in "xy"
                           else key for key in layout)
        return self._key_lines(layout) + list(self._extra_help)

    def _pad_help(self) -> list[str]:
        """The keys the pad has no keycap or step line for, then the
        page's lines."""
        layout = tuple(key for key in _bound_layout() if key.move is None
                       and key.command not in ("step_up", "step_down"))
        return self._key_lines(layout) + list(self._extra_help)

    def _key_lines(self, layout) -> list[str]:
        """One line per kind of key, the keys named as bound now:
        "Left/Right/Up/Down   move x and y"."""
        bindings = self.session.settings.keys
        groups: list[tuple[str, list[str]]] = []
        for key in layout:
            name = keys.key_text(bindings, action_id(key))
            if groups and groups[-1][0] == key.help:
                groups[-1][1].append(name)
            else:
                groups.append((key.help, [name]))
        return [f"{'/'.join(names)}   {what}" for what, names in groups]

    def set_xy_only(self, on: bool) -> None:
        """Change `xy_only`; see "XY only"."""
        self._xy_only = bool(on)
        self.pad.set_xy_only(self._xy_only)
        self._set_enabled(getattr(self, "_enabled", False))
        self._help = self._key_help()
        self._emit_help()

    def set_view_axes(self, axes) -> None:
        """Change `view_axes`; None goes back to the robot's own axes."""
        self._view_axes = check_view_axes(axes)
        self._label_pad()
        self._help = self._key_help()
        self._emit_help()

    def _emit_help(self) -> None:
        """The pad's key list - the keys it has no button for - and its
        note while a field has the keys."""
        self.pad.set_bindings(self.session.settings.keys)
        self.pad.set_key_lines(self._pad_help())
        self.pad.set_note("\n".join(TYPING_HELP) if self._typing else "")

    def _emit_position(self) -> None:
        self.position_changed.emit(self.position_lines)

    def _set_status(self, status: str) -> None:
        """The robot's X, Y and Z from `controller.status()`, and how far
        out of the envelope it is if it is; its step is the pad's."""
        found = POSE.match(status.strip())
        if found is None:
            self._status = status.strip()
            self._emit_position()
            return
        pose = tuple(float(v) for v in found.groups())
        self._status = "  ".join(
            [f"{axis} {value:.2f}" for axis, value in zip("XYZ", pose)]
            + OUT.findall(status))
        self._emit_position()
        self._set_pose(pose)

    def _set_pose(self, pose) -> None:
        if pose != self.pose:
            self.pose = pose
            self.pose_changed.emit(pose)

    def _say(self, text: str, firm: bool = False) -> None:
        """Words rather than colour. Running into a soft limit is ordinary, and
        the difference between refused and clamped is what the sentence says.
        Shown on the picture under the position rather than in a dialog: a
        modal box for it would be in the way several times a minute."""
        self._message = (("REFUSED: " if firm else "Clamped: ") + text
                         if text else "")
        self._emit_position()

    def _pad_step(self, mm: float) -> None:
        if self.controller is not None:
            self.controller.step = mm

    def _refresh_saved(self) -> None:
        """The profile's positions, name and coordinates, selection kept."""
        profile = self.session.profile
        positions = dict(profile.positions) if profile is not None else {}
        rows = [(name, positions[name]) for name in sorted(positions)]
        wanted = [_position_row(name, where) for name, where in rows]
        if [self.saved.item(i).text() for i in range(self.saved.count())] == wanted:
            return
        chosen = self._chosen_position()
        self.saved.clear()
        for (name, where), text in zip(rows, wanted):
            item = QListWidgetItem(text)
            item.setData(POSITION_ROLE, name)
            self.saved.addItem(item)
            if name == chosen:
                self.saved.setCurrentItem(item)
        self.positions_note.setText(
            "" if rows else
            "No saved positions.")

    def _on_profile_changed(self, _profile) -> None:
        if self.controller is not None:
            self.controller.limits = self.session.motion.envelope
        self._refresh_saved()
        self._refresh_buttons()

    def _on_tip_changed(self, _tip) -> None:
        """Another tip, or none: the Z top follows it (`Motion.envelope`),
        and so does the Z the robot reports for the same gantry height, so
        the pose shown is read again - on the page on screen; another page
        reads it when it is shown."""
        if self.controller is not None:
            self.controller.limits = self.session.motion.envelope
        if self.isVisible():
            self.refresh_position()

    def _on_robot_changed(self, _state: str) -> None:
        robot = self.session.robot
        if robot is None:
            self.controller = None
            self._set_pose(None)
            self._status = NO_ROBOT
            self._say("")
            self._set_enabled(False)
            # The list outlives the connection: it is the profile's, and
            # tidying it up is exactly what happens with the robot off.
            self._refresh_saved()
            return

        self.controller = JogController(robot,
                                        limits=self.session.motion.envelope,
                                        steps=list(DEFAULT_STEPS),
                                        step=self.pad.step)
        self._set_enabled(True)
        # Now, not when the first pose read comes back: the list is the
        # profile's and needs no robot, and an empty one for the second it
        # takes to answer is a list that looks lost.
        self._refresh_saved()
        # The first pose read is HTTP like any other.
        controller = self.controller
        self._run(Worker(lambda: (None, controller.status())),
                  robot_work=False)

    def _robot_elsewhere(self) -> str | None:
        """What has the robot, if it is not this panel: then nothing here may
        move it (`Session.robot_lease`)."""
        if self._lent is not None:
            return None
        holder = self.session.robot_lease.holder
        if holder is None:
            return None
        lease = getattr(self._worker, "lease", None)
        if lease is not None and not lease.released:
            return None
        return holder

    def _on_robot_busy(self, _holder) -> None:
        """Something claimed or gave back the robot, here or on another
        page: the controls follow."""
        if self._worker is None:
            self._set_enabled(True)

    def _set_enabled(self, enabled: bool) -> None:
        elsewhere = self._robot_elsewhere()
        self.setToolTip("" if elsewhere is None
                        else f"Robot busy: {elsewhere}.")
        usable = (enabled and self.controller is not None
                  and elsewhere is None)
        for widget in self._movers:
            widget.setEnabled(usable)
        self.pad.set_enabled_reason(
            usable, NO_ROBOT_REASON if self.controller is None
            else "" if elsewhere is None else f"Robot busy: {elsewhere}.")
        # Only while on screen: a robot connect enables every panel, and a
        # window-wide shortcut on a hidden page would compete with the one on
        # the page that is showing. hideEvent keeps this true afterwards.
        for shortcut in self._shortcuts:
            shortcut.setEnabled(usable and self.isVisible()
                                and not (self._xy_only
                                         and shortcut in self._z_shortcuts))
        self._enabled = usable
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        """The three that need a position chosen, and not only a robot."""
        chosen = self._chosen_position() is not None
        usable = getattr(self, "_enabled", False)
        self.goto_button.setEnabled(usable and chosen)
        # Renaming and deleting touch the profile, not the robot: they work
        # with nothing connected, which is when tidying up happens.
        editable = self.session.profile is not None and not self._busy()
        self.rename_button.setEnabled(editable and chosen)
        self.delete_button.setEnabled(editable and chosen)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_pad()
        self._set_enabled(self._worker is None)
        # The pose as it is now: the robot may have moved, or the tip may
        # have changed - and with it the Z reported - while this page was
        # not on screen.
        if self.controller is not None:
            self.controller.limits = self.session.motion.envelope
            self.refresh_position()

    def hideEvent(self, event) -> None:
        """Give up the arrow keys.

        Two pages hold a panel each, and both sets of shortcuts exist for the
        life of the window. Left enabled they would both claim the same keys
        and Qt would pick one of them without saying which.
        """
        for shortcut in self._shortcuts:
            shortcut.setEnabled(False)
        super().hideEvent(event)
        self._sync_pad()
