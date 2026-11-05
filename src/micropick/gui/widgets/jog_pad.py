"""The face of the jog panel: **Controls** (MANUAL.md §2.3, #110).

XY arrows and Z Up / Down, labeled "XY" and "Z"; over them the step size as
a carousel - three sizes in view, ‹ › to slide - and a **Keys** toggle that
puts each button's key on it, under its icon, so the shortcuts are learned
from the buttons (§1.9). Beside Keys, **Undo step**: it undoes the last
step, so it sits with the steps.

Only the face. It emits `jog(axis, sign)`, `undo()` and `step_changed(mm)`; what a
step does - the robot's lease, the jog envelope, the travel height - stays
`JogPanel`'s (`run_job`), which drives this pad the way it drove its own
buttons. The keycaps name the keys as bound (`gui.keys`), so a key rebound
in Settings shows here; under them, with Keys on, the panel's other keys
and the page's (`set_key_lines`), which used to be a box on the picture.

The pad floats over the bottom right of a picture, on a dark translucent
plate, as the prototype draws it (`JogPanel.show_position_on`), and is as
wide wherever it is. Its title is the fold toggle, with the arrow of every
title that folds (`fold_arrow`) before it, apart from Undo step and Keys at
the row's other end: an arrow at the end of the row read as a third button.
It folds the pad into that corner, the header alone left over the picture
(#50); the keys still move the robot - they are the panel's, not the face's.
Keys stays in that header, so its list shows folded too. The fold is the
screen's: a page is built once and its pad stays with it, so it holds for
as long as the program runs and no other page's follows (§10.20); the Tip
step's two pads, on one picture, share theirs. Picking folds its own from
the start.

The pad's own containers are `plain` (transparent): qdarktheme fills a bare
QWidget with the window's color, an opaque block on the translucent plate.

The chosen step is colored by how far one press moves the gantry
(`step_color`): a 10 mm step near the dish is the one to notice. So is the
chip that shows the step while the pad is folded (#99): the panel has it
in the picture's status plate (`CameraView.set_step_line`), a page
with no picture puts up a `StepChip`. Neither is a control: the step is
changed on the pad and by its keys.
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QSizePolicy, QToolButton,
                               QVBoxLayout, QWidget)

from ...workflows.jog import DEFAULT_STEPS
from .. import keys as key_actions
from ..theme import COLORS, mono_font, overlay_box
from ..theme.factory import fold_arrow
from ..theme.icons import icon

__all__ = ["STEPS", "JogPad", "KeycapButton", "StepCarousel", "StepChip",
           "key_row", "keycap", "step_chip_text", "step_label", "step_color",
           "step_ink"]

# The carousel's sizes, mm (§8.11): the jog controller's own.
STEPS = tuple(DEFAULT_STEPS)
VISIBLE = 3

# The panel and its buttons, px (ManualPick.dc.html).
PANEL_WIDTH = 340
XY_BUTTON = (48, 44)
Z_BUTTON = (64, 44)
STEP_ARROW = (34, 36)
HEADER_BUTTON_HEIGHT = 36

_BUTTONS = (  # action id, grid cell or None for Z, icon or text
    ("y+", (0, 1), "chevron-up"),
    ("x-", (1, 0), "chevron-left"),
    ("x+", (1, 2), "chevron-right"),
    ("y-", (2, 1), "chevron-down"),
)


def _jog_style() -> str:
    """A button of the panel's: the arrows, Up and Down, ‹ and ›."""
    return (f"QPushButton {{ background: {COLORS['raised']};"
            f" border: 1px solid {COLORS['control-border']};"
            f" border-radius: 9px; color: {COLORS['text']};"
            f" font-weight: 600; font-size: 14px; }}"
            f"QPushButton:hover {{ background: #262B33; }}"
            f"QPushButton:pressed {{ background: {COLORS['selected-bg']}; }}"
            f"QPushButton:disabled {{ color: {COLORS['faint']}; }}")


def _header_style() -> str:
    """Undo step and Keys; Keys is filled while it is on."""
    return (f"QPushButton {{ background: transparent;"
            f" border: 1px solid {COLORS['control-border']};"
            f" border-radius: 9px; color: {COLORS['text-2']};"
            f" font-weight: 600; font-size: 13px; padding: 0 12px; }}"
            f"QPushButton:hover {{ background: {COLORS['hover']};"
            f" color: {COLORS['text']}; }}"
            f"QPushButton:checked {{ background: {COLORS['text']};"
            f" border-color: {COLORS['text']}; color: {COLORS['bg']}; }}"
            f"QPushButton:disabled {{ color: {COLORS['faint']}; }}")


def step_label(mm: float) -> str:
    return f"{mm:g} mm"


def step_chip_text(mm: float) -> str:
    return f"STEP · {step_label(mm)}"


def step_color(mm: float) -> str:
    """The chosen step's fill: the accent up to 2 mm, warn to 5, the
    destructive red above (`danger` is the Stop button's alone)."""
    if mm <= 2:
        return COLORS["accent"]
    return COLORS["warn"] if mm <= 5 else COLORS["danger-text"]


def step_ink(mm: float) -> str:
    """The chosen step as text on the overlay: `step_color` warns the same
    way, and its accent is lighter there."""
    return COLORS["accent-hover"] if mm <= 2 else step_color(mm)


class KeycapButton(QPushButton):
    """A button that can carry a keycap in its corner, its key as bound:
    the pad's, and a page's own robot buttons (Dish bottom's Up and Down,
    #58). `under`: the key under the icon instead, as Controls has it."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.keycap = ""
        self.under = False

    def set_keycap(self, text: str) -> None:
        self.keycap = text
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self.keycap:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.under:
            painter.setFont(mono_font(10, 600))
            painter.setPen(QColor(COLORS["accent-hover"]))
            painter.drawText(self.rect().adjusted(0, 0, 0, -2),
                             Qt.AlignmentFlag.AlignHCenter
                             | Qt.AlignmentFlag.AlignBottom, self.keycap)
            painter.end()
            return
        font = mono_font(9, 500)
        painter.setFont(font)
        w = painter.fontMetrics().horizontalAdvance(self.keycap) + 8
        rect = QRectF(self.width() - w - 3, self.height() - 15, w, 12)
        painter.setPen(QPen(QColor("#3A4049"), 1))
        painter.setBrush(QColor(COLORS["bg"]))
        painter.drawRoundedRect(rect, 3, 3)
        painter.setPen(QColor(COLORS["text-3"]))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.keycap)
        painter.end()


class _JogButton(KeycapButton):
    """A pad button."""

    def __init__(self, action: str, parent: QWidget):
        super().__init__(parent=parent)
        self.action = action
        self.hint = ""                  # the tooltip while it is enabled
        self.under = True
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoRepeat(False)
        self.setFixedSize(*(Z_BUTTON if action[0] == "z" else XY_BUTTON))
        self.setStyleSheet(_jog_style())


class StepCarousel(QWidget):
    """The step size: three sizes in view, ‹ › to slide. The pad's, and
    the Dish bottom step's between its Up and Down. `panel`: as Controls
    draws it, filling its row, in mm."""

    step_changed = Signal(float)

    def __init__(self, *, steps=STEPS, step: float = 1.0, panel: bool = False,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.panel = panel
        self.steps = tuple(sorted(steps))
        self.step = min(self.steps, key=lambda s: abs(s - step))
        self._first = 0

        carousel = QHBoxLayout(self)
        carousel.setContentsMargins(0, 0, 0, 0)
        carousel.setSpacing(4)
        label = QLabel("Step", self)
        label.setObjectName("faint")
        carousel.addWidget(label)
        self.smaller = self._arrow("chevron-left", "Smaller step")
        self.smaller.clicked.connect(lambda: self.step_by(-1))
        carousel.addWidget(self.smaller)
        self.options: list[QPushButton] = []
        for _ in range(VISIBLE):
            option = QPushButton(self)
            option.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            if panel:
                option.setFont(mono_font(14, 600))
                option.setFixedHeight(STEP_ARROW[1])
                option.setSizePolicy(QSizePolicy.Policy.Expanding,
                                     QSizePolicy.Policy.Fixed)
            else:
                option.setFixedSize(64, 28)
            option.clicked.connect(lambda _=False, b=option:
                                   self.set_step(b.property("mm"), emit=True))
            carousel.addWidget(option)
            self.options.append(option)
        self.bigger = self._arrow("chevron-right", "Bigger step")
        self.bigger.clicked.connect(lambda: self.step_by(+1))
        carousel.addWidget(self.bigger)
        if panel:
            unit = QLabel("mm", self)
            unit.setObjectName("faint")
            carousel.addWidget(unit)
        self.set_step(self.step)

    def _arrow(self, name: str, tip: str) -> KeycapButton:
        button = KeycapButton(parent=self)
        button.setObjectName("quiet")
        button.setProperty("compact", True)
        button.setIcon(icon(name, COLORS["text-2"], 14, stroke=2.4))
        button.setFixedWidth(STEP_ARROW[0])
        if self.panel:
            button.under = True
            button.setFixedHeight(STEP_ARROW[1])
            button.setStyleSheet(_jog_style())
        button.setToolTip(tip)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return button

    def set_step(self, mm: float, *, emit: bool = False) -> None:
        """Choose a size and bring it into view."""
        self.step = min(self.steps, key=lambda s: abs(s - mm))
        i = self.steps.index(self.step)
        if not self._first <= i < self._first + VISIBLE:
            self._first = max(0, min(i - 1, len(self.steps) - VISIBLE))
        self._paint_options()
        if emit:
            self.step_changed.emit(self.step)

    def show_keycaps(self, bindings: dict | None) -> None:
        """The step keys as bound on ‹ ›, or none (None)."""
        on = bindings is not None
        self.smaller.set_keycap(keycap(bindings, "step_down") if on else "")
        self.bigger.set_keycap(keycap(bindings, "step_up") if on else "")

    def step_by(self, delta: int) -> None:
        """One size smaller or bigger: the step keys, and ‹ ›, which slide
        the view with the chosen size."""
        i = self.steps.index(self.step) + delta
        if 0 <= i < len(self.steps):
            self.set_step(self.steps[i], emit=True)

    def _paint_options(self) -> None:
        shown = self.steps[self._first:self._first + VISIBLE]
        for button, mm in zip(self.options, shown):
            chosen = mm == self.step
            button.setText(f"{mm:g}")
            button.setProperty("mm", mm)
            button.setToolTip(step_label(mm))
            bg = step_color(mm) if chosen else "transparent"
            ink = COLORS["on-accent"] if chosen else COLORS["text-2"]
            radius = 9 if self.panel else 7
            size = 14 if self.panel else 13
            button.setStyleSheet(
                f"QPushButton {{ background: {bg}; color: {ink}; border: none;"
                f" border-radius: {radius}px; font-weight: 600;"
                f" font-size: {size}px; }}"
                f"QPushButton:hover {{ background: "
                f"{bg if chosen else '#22262D'}; }}")
        self.smaller.setEnabled(self.step != self.steps[0])
        self.bigger.setEnabled(self.step != self.steps[-1])


class JogPad(QFrame):
    jog = Signal(str, int)          # axis "x" / "y" / "z", sign +1 / -1
    undo = Signal()                 # Undo step
    step_changed = Signal(float)
    fold_changed = Signal(bool)

    def __init__(self, *, steps=STEPS, step: float = 1.0,
                 bindings: dict | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("jogPad")
        self.setStyleSheet("QFrame#jogPad { background: rgba(16, 18, 22, 0.92);"
                           " border: 1px solid rgba(255, 255, 255, 0.08);"
                           " border-radius: 14px; }")
        self.setFixedWidth(PANEL_WIDTH)
        self._bindings = bindings or {}

        column = QVBoxLayout(self)
        column.setContentsMargins(12, 10, 12, 12)
        column.setSpacing(12)

        head = QHBoxLayout()
        self.fold_button = QToolButton(self)
        self.fold_button.setObjectName("padTitle")
        self.fold_button.setText("Controls")
        self.fold_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.fold_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.fold_button.clicked.connect(
            lambda: self.set_folded(not self.folded))
        head.addWidget(self.fold_button)
        head.addStretch(1)
        self.undo_button = QPushButton("Undo step", self)
        self.undo_button.setStyleSheet(_header_style())
        self.undo_button.setFixedHeight(HEADER_BUTTON_HEIGHT)
        self.undo_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.undo_button.setToolTip("Undo the last jog step.")
        self.undo_button.clicked.connect(self.undo)
        head.addWidget(self.undo_button)
        self.keys = QPushButton("Keys", self)
        self.keys.setStyleSheet(_header_style())
        self.keys.setFixedHeight(HEADER_BUTTON_HEIGHT)
        self.keys.setCheckable(True)
        self.keys.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.keys.toggled.connect(self._show_keys)
        head.addWidget(self.keys)
        column.addLayout(head)

        self.carousel = StepCarousel(steps=steps, step=step, panel=True,
                                     parent=self)
        self.carousel.step_changed.connect(self.step_changed)
        self.smaller = self.carousel.smaller
        self.bigger = self.carousel.bigger
        self.options = self.carousel.options
        column.addWidget(self.carousel)

        self.pads = QWidget(self)
        self.pads.setObjectName("plain")
        pads = QHBoxLayout(self.pads)
        pads.setContentsMargins(6, 0, 6, 0)
        pads.setSpacing(0)
        grid = QGridLayout()
        grid.setSpacing(4)
        self.buttons: dict[str, _JogButton] = {}
        for action, (r, c), name in _BUTTONS:
            button = _JogButton(action, self)
            button.setIcon(icon(name, COLORS["text"], 18, stroke=2))
            button.clicked.connect(lambda _=False, a=action:
                                   self.jog.emit(a[0], 1 if a[1] == "+" else -1))
            grid.addWidget(button, r, c)
            self.buttons[action] = button
        xy = QLabel("XY", self)
        xy.setObjectName("faint")
        xy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(xy, 1, 1)
        pads.addLayout(grid)
        pads.addStretch(1)
        z = QVBoxLayout()
        z.setSpacing(4)
        for action, text in (("z+", "Up"), (None, "Z"), ("z-", "Down")):
            if action is None:
                label = QLabel(text, self)
                label.setObjectName("faint")
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                label.setFixedHeight(Z_BUTTON[1])
                z.addWidget(label)
                self._z_label = label
                continue
            button = _JogButton(action, self)
            button.setText(text)
            button.clicked.connect(lambda _=False, a=action:
                                   self.jog.emit("z", 1 if a[1] == "+" else -1))
            z.addWidget(button)
            self.buttons[action] = button
        pads.addLayout(z)
        column.addWidget(self.pads)

        self.key_list = QLabel(self)
        self.key_list.setObjectName("faint")
        self.key_list.setTextFormat(Qt.TextFormat.RichText)
        self.key_list.setWordWrap(True)
        self.key_list.hide()
        column.addWidget(self.key_list)
        # Whatever the toggle: the keys are a field's for now.
        self.note = QLabel(self)
        self.note.setObjectName("faint")
        self.note.setWordWrap(True)
        self.note.hide()
        column.addWidget(self.note)
        self.key_lines: list[str] = []
        self.folded = False
        self.set_bindings(self._bindings)
        self._show_fold()

    def sizeHint(self) -> QSize:
        """As tall as the panel's width makes it: a key line wraps. The
        picture's corner places the pad by this."""
        layout = self.layout()
        if layout.hasHeightForWidth():
            return QSize(PANEL_WIDTH, layout.totalHeightForWidth(PANEL_WIDTH))
        return QSize(PANEL_WIDTH, super().sizeHint().height())

    # -- folding ---------------------------------------------------------

    def set_folded(self, folded: bool) -> None:
        """Fold the pad to its header, or open it again."""
        changed = bool(folded) != self.folded
        self.folded = bool(folded)
        self._show_fold()
        if changed:
            self.fold_changed.emit(self.folded)

    def _show_fold(self) -> None:
        open_ = not self.folded
        for widget in (self.carousel, self.pads):
            widget.setVisible(open_)
        self._show_lines()
        fold_arrow(self.fold_button, open_)
        self.fold_button.setToolTip("Fold Controls" if open_
                                    else "Open Controls")
        self.adjustSize()

    def _show_lines(self) -> None:
        """Under the panel: the key list while Keys is on, folded or not,
        and the note with it; open, the note whatever Keys says."""
        on = self.keys.isChecked()
        self.key_list.setVisible(on)
        self.note.setVisible(bool(self.note.text())
                             and (on or not self.folded))

    # -- the step --------------------------------------------------------

    @property
    def steps(self) -> tuple:
        return self.carousel.steps

    @property
    def step(self) -> float:
        return self.carousel.step

    def set_step(self, mm: float, *, emit: bool = False) -> None:
        """Choose a size and bring it into view."""
        self.carousel.set_step(mm, emit=emit)

    def step_by(self, delta: int) -> None:
        self.carousel.step_by(delta)

    # -- keys ------------------------------------------------------------

    def set_bindings(self, bindings: dict) -> None:
        self._bindings = bindings or {}
        self._write_keys()

    def set_key_lines(self, lines) -> None:
        """More keys under the step keys, each "keys   what it does"."""
        self.key_lines = [str(line) for line in lines]
        self._write_keys()

    def set_note(self, text: str) -> None:
        """A line under the pad whatever Keys says, or none."""
        self.note.setText(text)
        self._show_lines()

    def _write_keys(self) -> None:
        ink = COLORS["text-2"]
        smaller = key_actions.key_text(self._bindings, "step_down")
        bigger = key_actions.key_text(self._bindings, "step_up")
        rows = [(f'Step size: <span style="color:{ink}">{escape(smaller)}'
                 f'</span> smaller, <span style="color:{ink}">'
                 f'{escape(bigger)}</span> bigger')]
        rows += [key_row(line) for line in self.key_lines]
        self.key_list.setText("<br>".join(rows))
        self._show_keys(self.keys.isChecked())

    def _show_keys(self, on: bool) -> None:
        for action, button in self.buttons.items():
            button.set_keycap(keycap(self._bindings, action) if on else "")
        self.carousel.show_keycaps(self._bindings if on else None)
        self._show_lines()

    def set_xy_only(self, on: bool) -> None:
        for action in ("z+", "z-"):
            self.buttons[action].setVisible(not on)
        self._z_label.setVisible(not on)

    def set_hints(self, hints: dict) -> None:
        """Tooltips by action, shown while the buttons are enabled."""
        for action, button in self.buttons.items():
            button.hint = hints.get(action, "")
            if button.isEnabled():
                button.setToolTip(button.hint)

    def set_enabled_reason(self, enabled: bool, reason: str = "") -> None:
        """Gray every move while the robot is not free, saying why."""
        for button in self.buttons.values():
            button.setEnabled(enabled)
            button.setToolTip(button.hint if enabled else reason)
        self.undo_button.setEnabled(enabled)
        self.undo_button.setToolTip("Undo the last jog step." if enabled
                                    else reason)


class StepChip(QLabel):
    """The pad's step in its color, for a page with no picture to paint it
    on (Well center)."""

    def __init__(self, pad: JogPad, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFont(mono_font(13, 500))
        pad.step_changed.connect(self._show_step)
        self._show_step(pad.step)

    def _show_step(self, mm: float) -> None:
        self.setText(step_chip_text(mm))
        self.setStyleSheet(f"{overlay_box()} color: {step_ink(mm)}; "
                           f"padding: 6px 12px;")


def key_row(line: str) -> str:
    """A key line, "keys   what it does", as the pad lists it: the keys
    brighter, in rich text."""
    keys, sep, what = line.partition("   ")
    if not sep:
        return escape(line)
    return (f'<span style="color:{COLORS["text-2"]}">{escape(keys.strip())}'
            f'</span> {escape(what.strip())}')


def keycap(bindings: dict | None, action: str) -> str:
    """The key, short enough for a corner of a button. From the portable
    name, which is the same on every system (macOS names PgUp "⇞")."""
    text = key_actions.sequences(bindings or {}, action)[0].toString(
        QKeySequence.SequenceFormat.PortableText)
    return {"Left": "←", "Right": "→", "Up": "↑", "Down": "↓",
            "PgUp": "PgUp", "PgDown": "PgDn"}.get(text, text)

