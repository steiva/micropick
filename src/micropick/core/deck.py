"""The OT-2 deck's geometry, in the robot's deck frame and in millimetres.

The numbers are the deck definition's: slot pitch and the slot opening.
`gui.widgets.deck_view` draws the deck with them, and the jog envelope
(`workflows.motion`) raises its floor over a deck module's slots with them,
so they live here, with no Qt, rather than in the widget.
"""

from __future__ import annotations

__all__ = ["SLOT_PITCH_X", "SLOT_PITCH_Y", "SLOT_WIDTH", "SLOT_HEIGHT",
           "slot_centre"]

SLOT_PITCH_X = 132.5
SLOT_PITCH_Y = 90.5
SLOT_WIDTH = 127.76
SLOT_HEIGHT = 85.48


def slot_centre(slot) -> tuple[float, float]:
    """The centre of a slot in the robot's deck frame, in millimetres: slot
    1's corner at the origin, x to the right, y to the back."""
    n = int(slot)
    column = (n - 1) % 3
    row = (n - 1) // 3
    return (column * SLOT_PITCH_X + SLOT_WIDTH / 2,
            row * SLOT_PITCH_Y + SLOT_HEIGHT / 2)
