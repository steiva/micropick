"""The face of the jog panel (REDESIGN §3, JogPad).

XY arrows and Z Up / Down, all one size, labeled "XY" and "Z"; over them
the step size as a carousel - three sizes in view, ‹ › to slide - and a
**Keys** toggle that puts each button's key on it as a keycap, so the
shortcuts are learned from the buttons (§1.9). Beside Keys, **Undo step**:
it undoes the last step, so it sits with the steps.

Only the face. It emits `jog(axis, sign)`, `undo()` and `step_changed(mm)`; what a
step does - the robot's lease, the jog envelope, the travel height - stays
`JogPanel`'s (`run_job`), which drives this pad the way it drove its own
buttons. The keycaps name the keys as bound (`gui.keys`), so a key rebound
in Settings shows here; under them, with Keys on, the panel's other keys
and the page's (`set_key_lines`), which used to be a box on the picture.

The pad floats over the bottom right of a picture, on a dark translucent
plate, as the prototype draws it (`JogPanel.show_position_on`). The fold
button at the end of its title row folds it into that corner, the title row
alone left over the picture (#50); the keys still move the robot - they are
the panel's, not the face's. Picking folds it from the start.

The chosen step is colored by how far one press moves the gantry
(`step_color`): a 10 mm step near the dish is the one to notice.
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QToolButton, QVBoxLayout, QWidget)

from ...workflows.jog import DEFAULT_STEPS
from .. import keys as key_actions
from ..theme import COLORS, SIZES, mono_font
from ..theme.icons import icon

__all__ = ["STEPS", "JogPad", "StepCarousel", "step_label", "step_color"]

# The carousel's sizes, mm (§8.11): the jog controller's own.
STEPS = tuple(DEFAULT_STEPS)
VISIBLE = 3

_BUTTONS = (  # action id, grid cell or None for Z, icon or text
    ("y+", (0, 1), "chevron-up"),
    ("x-", (1, 0), "chevron-left"),
    ("x+", (1, 2), "chevron-right"),
    ("y-", (2, 1), "chevron-down"),
)


def step_label(mm: float) -> str:
    return f"{mm:g} mm"


def step_color(mm: float) -> str:
    """The chosen step's fill: the accent up to 2 mm, warn to 5, the
    destructive red above (`danger` is the Stop button's alone)."""
    if mm <= 2:
        return COLORS["accent"]
    return COLORS["warn"] if mm <= 5 else COLORS["danger-text"]


class _JogButton(QPushButton):
    """A pad button that can carry a keycap in its corner."""

    def __init__(self, action: str, parent: QWidget):
        super().__init__(parent)
        self.action = action
        self.keycap = ""
        self.hint = ""                  # the tooltip while it is enabled
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoRepeat(False)
        side = SIZES["jog-button"] + 4
        self.setFixedSize(side if action[0] != "z" else side + 12, side)
        self.setStyleSheet(
            f"QPushButton {{ background: #22262D; border: none;"
            f" border-radius: 8px; color: {COLORS['text']};"
            f" font-weight: 600; font-size: 13px; }}"
            f"QPushButton:hover {{ background: #2C313A; }}"
            f"QPushButton:pressed {{ background: {COLORS['selected-bg']}; }}"
            f"QPushButton:disabled {{ color: {COLORS['faint']}; }}")

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self.keycap:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
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


class StepCarousel(QWidget):
    """The step size: three sizes in view, ‹ › to slide. The pad's, and
    the Dish bottom step's between its Up and Down."""

    step_changed = Signal(float)

    def __init__(self, *, steps=STEPS, step: float = 1.0,
                 parent: QWidget | None = None):
        super().__init__(parent)
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
            option.setFixedSize(64, 28)
            option.clicked.connect(lambda _=False, b=option:
                                   self.set_step(b.property("mm"), emit=True))
            carousel.addWidget(option)
            self.options.append(option)
        self.bigger = self._arrow("chevron-right", "Bigger step")
        self.bigger.clicked.connect(lambda: self.step_by(+1))
        carousel.addWidget(self.bigger)
        self.set_step(self.step)

    def _arrow(self, name: str, tip: str) -> QPushButton:
        button = QPushButton(self)
        button.setObjectName("quiet")
        button.setProperty("compact", True)
        button.setIcon(icon(name, COLORS["text-2"], 14, stroke=2.4))
        button.setFixedWidth(28)
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
            button.setStyleSheet(
                f"QPushButton {{ background: {bg}; color: {ink}; border: none;"
                f" border-radius: 7px; font-weight: 600; font-size: 13px; }}"
                f"QPushButton:hover {{ background: "
                f"{bg if chosen else '#22262D'}; }}")
        self.smaller.setEnabled(self.step != self.steps[0])
        self.bigger.setEnabled(self.step != self.steps[-1])


class JogPad(QFrame):
    jog = Signal(str, int)          # axis "x" / "y" / "z", sign +1 / -1
    undo = Signal()                 # Undo step
    step_changed = Signal(float)

    def __init__(self, *, steps=STEPS, step: float = 1.0,
                 bindings: dict | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("jogPad")
        self.setStyleSheet("QFrame#jogPad { background: rgba(10, 12, 14, 0.86);"
                           " border-radius: 12px; }")
        self._bindings = bindings or {}

        column = QVBoxLayout(self)
        column.setContentsMargins(12, 12, 12, 12)
        column.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("MOVE THE ROBOT", self)
        title.setObjectName("caption")
        head.addWidget(title, 1)
        self.undo_button = QPushButton("Undo step", self)
        self.undo_button.setObjectName("secondary")
        self.undo_button.setProperty("compact", True)
        self.undo_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.undo_button.setToolTip("Undo the last jog step.")
        self.undo_button.clicked.connect(self.undo)
        head.addWidget(self.undo_button)
        self.keys = QPushButton("Keys", self)
        self.keys.setObjectName("secondary")
        self.keys.setProperty("compact", True)
        self.keys.setCheckable(True)
        self.keys.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.keys.toggled.connect(self._show_keys)
        head.addWidget(self.keys)
        self.fold_button = QToolButton(self)
        self.fold_button.setObjectName("barButton")
        self.fold_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.fold_button.clicked.connect(
            lambda: self.set_folded(not self.folded))
        head.addWidget(self.fold_button)
        column.addLayout(head)

        self.carousel = StepCarousel(steps=steps, step=step, parent=self)
        self.carousel.step_changed.connect(self.step_changed)
        self.smaller = self.carousel.smaller
        self.bigger = self.carousel.bigger
        self.options = self.carousel.options
        column.addWidget(self.carousel)

        self.pads = QWidget(self)
        pads = QHBoxLayout(self.pads)
        pads.setContentsMargins(0, 0, 0, 0)
        pads.setSpacing(14)
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
        z = QVBoxLayout()
        z.setSpacing(4)
        for action, text in (("z+", "Up"), (None, "Z"), ("z-", "Down")):
            if action is None:
                label = QLabel(text, self)
                label.setObjectName("faint")
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                label.setFixedHeight(SIZES["jog-button"] + 4)
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
        self.key_list.hide()
        column.addWidget(self.key_list)
        # Whatever the toggle: the keys are a field's for now.
        self.note = QLabel(self)
        self.note.setObjectName("faint")
        self.note.hide()
        column.addWidget(self.note)
        self.key_lines: list[str] = []
        self.folded = False
        self.set_bindings(self._bindings)
        self._show_fold()

    # -- folding ---------------------------------------------------------

    def set_folded(self, folded: bool) -> None:
        """Fold the pad to its title row, or open it again."""
        self.folded = bool(folded)
        self._show_fold()

    def _show_fold(self) -> None:
        open_ = not self.folded
        for widget in (self.undo_button, self.keys, self.carousel,
                       self.pads):
            widget.setVisible(open_)
        self.key_list.setVisible(open_ and self.keys.isChecked())
        self.note.setVisible(open_ and bool(self.note.text()))
        self.fold_button.setIcon(icon("chevron-down" if open_
                                      else "chevron-up", COLORS["text-2"],
                                      16, stroke=2))
        self.fold_button.setToolTip("Fold the pad" if open_
                                    else "Open the pad")
        self.adjustSize()

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
        self.note.setVisible(bool(text) and not self.folded)

    def _write_keys(self) -> None:
        ink = COLORS["text-2"]
        smaller = key_actions.key_text(self._bindings, "step_down")
        bigger = key_actions.key_text(self._bindings, "step_up")
        rows = [(f'Step size: <span style="color:{ink}">{escape(smaller)}'
                 f'</span> smaller, <span style="color:{ink}">'
                 f'{escape(bigger)}</span> bigger')]
        for line in self.key_lines:
            keys, sep, what = line.partition("   ")
            rows.append(f'<span style="color:{ink}">{escape(keys.strip())}'
                        f'</span> {escape(what.strip())}' if sep
                        else escape(line))
        self.key_list.setText("<br>".join(rows))
        self._show_keys(self.keys.isChecked())

    def _show_keys(self, on: bool) -> None:
        for action, button in self.buttons.items():
            button.keycap = _keycap(self._bindings, action) if on else ""
            button.update()
        self.key_list.setVisible(on and not self.folded)

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


def _keycap(bindings: dict, action: str) -> str:
    """The key, short enough for a corner of a button. From the portable
    name, which is the same on every system (macOS names PgUp "⇞")."""
    text = key_actions.sequences(bindings, action)[0].toString(
        QKeySequence.SequenceFormat.PortableText)
    return {"Left": "←", "Right": "→", "Up": "↑", "Down": "↓",
            "PgUp": "PgUp", "PgDown": "PgDn"}.get(text, text)

