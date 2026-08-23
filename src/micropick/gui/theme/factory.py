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

from PySide6.QtWidgets import (QFrame, QLabel, QPushButton, QVBoxLayout,
                               QWidget)

from . import SPACING

__all__ = ["primary_button", "secondary_button", "card", "heading"]


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
