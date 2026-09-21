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

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFrame, QLabel, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from . import SPACING

__all__ = ["primary_button", "secondary_button", "card", "heading",
           "combo_box", "scroll_column"]


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
    """
    area = QScrollArea()
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
    return area
