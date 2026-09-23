"""The widgets the pages are built from.

Thin wrappers, and the point is where they are rather than what they do: a page
that calls `primary_button("Home")` instead of `QPushButton("Home")` can be
restyled by editing this module and `micropick.qss`, while a page that
constructs its own widgets and sets its own object names cannot.

Each wrapper does two things — it names the widget for `micropick.qss`, which
sets geometry, and it puts the widget into the state qdarktheme already
colours. The second half is what keeps this application following the operating
system's light/dark setting without owning a single colour of its own.

Nothing here knows about the session, the robot or a camera. These are widgets.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFrame, QLabel,
                               QPushButton, QScrollArea, QSpinBox, QToolButton,
                               QVBoxLayout, QWidget)

from . import SPACING

__all__ = ["primary_button", "secondary_button", "card", "heading",
           "combo_box", "spin_box", "double_spin_box", "scroll_column",
           "Section"]


def primary_button(text: str, parent: QWidget | None = None) -> QPushButton:
    """The one action a panel exists for. At most one per panel.

    `setDefault` is what makes it the accented one: qdarktheme fills
    `QPushButton:default` with the theme's primary colour and derives the
    hover, pressed and disabled variants from it. That is also Qt's own
    convention — the accented button is the one Enter activates — so the
    appearance and the keyboard behaviour agree rather than being set twice.
    """
    button = QPushButton(text, parent)
    button.setObjectName("primary")
    button.setDefault(True)
    return button


def secondary_button(text: str, parent: QWidget | None = None) -> QPushButton:
    """Everything else. Outlined, from qdarktheme's plain QPushButton rule."""
    button = QPushButton(text, parent)
    button.setObjectName("secondary")
    return button


def card(parent: QWidget | None = None) -> QFrame:
    """A grouped surface with its own vertical layout, already spaced.

    The shape is `Panel` because that is the selector qdarktheme gives a raised
    surface colour to; `NoFrame`, the default, is explicitly stripped of its
    border there and would come out invisible.

    The layout comes with the card rather than being left to the caller: a card
    whose contents sit against its border is the mistake this exists to
    prevent, and it is made once, here.
    """
    frame = QFrame(parent)
    frame.setObjectName("card")
    frame.setFrameShape(QFrame.Shape.Panel)
    # A card is mostly wrapped paragraphs, and its height therefore depends
    # on its width. Qt does not infer that from the layout: a widget's size
    # policy has to say so, or the layout above it never asks and the card
    # is given one line's worth with the rest clipped away. This is the
    # other half of theme.factory._ScrollColumn, and the two together are
    # what makes a wrapped sentence in a side panel visible.
    policy = frame.sizePolicy()
    policy.setHeightForWidth(True)
    frame.setSizePolicy(policy)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(SPACING * 2, SPACING * 2, SPACING * 2, SPACING * 2)
    layout.setSpacing(SPACING)
    return frame


def heading(text: str, level: int = 1, parent: QWidget | None = None) -> QLabel:
    """Levels 1 to 3. Anything else is a caller's typo, not a new size."""
    if level not in (1, 2, 3):
        raise ValueError(f"heading level must be 1, 2 or 3, got {level!r}")
    label = QLabel(text, parent)
    label.setObjectName(f"heading{level}")
    return label


class _WheelSafeComboBox(QComboBox):
    """A combo box the wheel does not turn.

    Inside a scrolling column the wheel means "scroll", and a combo box that
    also took it would change the jog step - or the camera - under a hand
    that was scrolling past. The dropdown and the keyboard still work.
    """

    def wheelEvent(self, event) -> None:
        event.ignore()


def combo_box(parent: QWidget | None = None) -> QComboBox:
    """A combo box for a panel that may scroll. See _WheelSafeComboBox."""
    return _WheelSafeComboBox(parent)


class _WheelSafeSpinBox(QSpinBox):
    """A spin box the wheel does not turn, for the combo box's reason - and
    here the number is often a Z the tip is about to be sent to."""

    def wheelEvent(self, event) -> None:
        event.ignore()


class _WheelSafeDoubleSpinBox(QDoubleSpinBox):
    """See _WheelSafeSpinBox."""

    def wheelEvent(self, event) -> None:
        event.ignore()


def spin_box(parent: QWidget | None = None) -> QSpinBox:
    """An integer field the mouse wheel leaves alone."""
    return _WheelSafeSpinBox(parent)


def double_spin_box(parent: QWidget | None = None) -> QDoubleSpinBox:
    """A decimal field the mouse wheel leaves alone."""
    return _WheelSafeDoubleSpinBox(parent)


class _ScrollColumn(QScrollArea):
    """A scroll area whose widget is as tall as its text actually needs.

    `widgetResizable` sizes the inner widget to the viewport unless its
    minimum height says otherwise, and that minimum comes from its layout,
    which asks each child for `minimumSizeHint`. A word-wrapped QLabel
    answers that with the height of about one line: wrapping means it can
    be any height, so its *minimum* is small. The layout then hands it one
    line's worth and the rest of the sentence is clipped - silently, with
    no scrollbar, because as far as the scroll area is concerned everything
    fits. Several of these panels are nothing but wrapped paragraphs, and
    the paragraph that went missing here was the one explaining why the
    marker was not detected.

    The column has a fixed width, so the height its layout needs is a
    number rather than a range: `heightForWidth`. This watches the inner
    widget for layout changes - which is what setting a label's text
    causes - and keeps its minimum height at that number.

    The measurement is deferred by a zero-timer rather than taken in the
    event handler. A label's new text reaches its own geometry before it
    reaches the layouts above it, and asked too early the column answers
    with the height the old text needed. Deferring costs one turn of the
    event loop and is what makes the number the one on screen. It cannot
    loop: the minimum is only written when it changes, and writing it
    produces a resize that measures the same number again.
    """

    def __init__(self, inner: QWidget, width: int):
        super().__init__()
        self._inner = inner
        self._inner_width = width
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(0)
        self._fit_timer.timeout.connect(self._fit_height)
        inner.installEventFilter(self)

    def set_outer_width(self, width: int) -> None:
        """Change the column's width, scrollbar included.

        Still one number at a time, which is all `heightForWidth` needs: a
        page that gives its panel whatever the picture leaves over calls
        this on resize, and the labels are measured again at the new width.
        """
        inner = max(1, int(width) - self.verticalScrollBar().sizeHint().width())
        if inner == self._inner_width:
            return
        self._inner_width = inner
        self._inner.setMinimumWidth(inner)
        self._inner.setMaximumWidth(inner)
        self.setFixedWidth(int(width))
        self._fit_timer.start()

    def eventFilter(self, obj, event) -> bool:
        if obj is self._inner and event.type() in (
                QEvent.Type.LayoutRequest, QEvent.Type.Resize,
                QEvent.Type.Show):
            self._fit_timer.start()
        return super().eventFilter(obj, event)

    def _fit_height(self) -> None:
        layout = self._inner.layout()
        if layout is None:
            return
        self._fit_labels()
        # invalidate, not merely activate: a layout caches the height it
        # computed for a width, and the labels just changed theirs.
        layout.invalidate()
        layout.activate()
        wanted = layout.heightForWidth(self._inner_width)
        if wanted > 0 and wanted != self._inner.minimumHeight():
            self._inner.setMinimumHeight(wanted)

    def _fit_labels(self) -> None:
        """Give every wrapped label a minimum height that is its own text.

        The column's total height being right is not enough: a `QVBoxLayout`
        distributing more than it needs hands each item its `sizeHint`, and
        a word-wrapped label's size hint is the height of a line or two at
        some width of the layout's choosing, not the height the text takes
        at the width it actually got. The card keeps the smaller number and
        the sentence is cut off inside it - with no scrollbar, because the
        column above fits.

        A minimum is the one thing a box layout will not take away, and it
        is set from the width the label has right now, so a narrower window
        recomputes it rather than staying tall.
        """
        for label in self._inner.findChildren(QLabel):
            if not label.wordWrap() or label.width() <= 0 or label.isHidden():
                continue
            wanted = label.heightForWidth(label.width())
            if wanted > 0 and wanted != label.minimumHeight():
                label.setMinimumHeight(wanted)


def scroll_column(inner: QWidget, width: int) -> QScrollArea:
    """A side panel that scrolls vertically when the window is too short.

    The panels beside a camera view stack several cards, and their minimum
    height added up to more than a 1080-line screen has under a taskbar;
    Qt then refused the window's geometry and the bottom of the panel was
    off screen with no way to reach it. Wrapped like this the column has no
    minimum height of its own, and the window can be as short as the screen.

    Takes no focus, and its viewport neither, so the keys over it still
    belong to the jog panel's shortcuts: PageUp and PageDown are the Z axis
    on these pages, and a scroll area with focus would page instead. The
    width is the panel's plus a scrollbar, so the cards do not change size
    when the bar comes and goes.

    Word-wrapped labels inside it are the reason for `_ScrollColumn`; see
    there.
    """
    area = _ScrollColumn(inner, width)
    area.setObjectName("column")
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    area.viewport().setFocusPolicy(Qt.FocusPolicy.NoFocus)
    inner.setMinimumWidth(width)
    inner.setMaximumWidth(width)
    area.setFixedWidth(width + area.verticalScrollBar().sizeHint().width())
    area.setWidget(inner)
    area._fit_height()
    return area


class Section(QFrame):
    """A card whose contents fold away behind its title.

    For the panels that carry more controls than any one job needs. The jog
    panel is the case: centring a marker wants the D-pad and nothing else,
    the tip calibration wants neither it nor the saved positions most of the
    time, and the check page wants the positions above all. Rather than
    three panels, or a panel that guesses, each section folds and each page
    says which ones it wants folded to begin with.

    Collapsed is a real collapse: the body is hidden, so it contributes no
    height and no minimum, which is what keeps a folded panel short on a
    1080-line screen. The arrow is the whole affordance, so the title is a
    flat button across the card rather than a label with a control beside
    it - there is nothing else in the row to click by mistake.

    Contents go into `body`, which has a vertical layout already spaced
    like a card's.
    """

    def __init__(self, title: str, *, collapsed: bool = False,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setFrameShape(QFrame.Shape.Panel)
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

        self.toggle = QToolButton(self)
        self.toggle.setObjectName("sectionToggle")
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(not collapsed)
        self.toggle.setAutoRaise(True)
        self.toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        # No keyboard focus: over these panels the arrows are the gantry's,
        # and a focused toggle would take space and Enter as well.
        self.toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.toggle.setSizePolicy(self.toggle.sizePolicy().horizontalPolicy(),
                                  self.toggle.sizePolicy().verticalPolicy())
        self.toggle.toggled.connect(self._toggled)

        self.body = QWidget(self)
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(SPACING)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACING * 2, SPACING, SPACING * 2, SPACING * 2)
        outer.setSpacing(SPACING)
        outer.addWidget(self.toggle)
        outer.addWidget(self.body)

        self._toggled(self.toggle.isChecked())

    def _toggled(self, shown: bool) -> None:
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if shown
                                 else Qt.ArrowType.RightArrow)
        self.body.setVisible(shown)

    @property
    def collapsed(self) -> bool:
        return not self.toggle.isChecked()

    def set_collapsed(self, collapsed: bool) -> None:
        self.toggle.setChecked(not collapsed)
